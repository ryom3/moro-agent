"""Structure-based markdown chunking.

Why not embedding-similarity ("semantic") chunking: for heading-structured notes
(Notion export), splitting on the heading hierarchy and never breaking code blocks
is more stable and gives every chunk a breadcrumb (Page > H2 > H3) for free. That
breadcrumb doubles as metadata and as context for the dense encoder.

Rules:
  * Split on markdown headings; maintain a heading stack -> heading_path.
  * Never split inside a fenced code block (``` or ~~~). A code block larger than
    max_chars stays as its own (oversized) chunk rather than being cut.
  * Pack paragraph/code blocks greedily up to target_chars, allow up to max_chars.
  * Merge sections shorter than min_chars into the previous chunk when they share
    a parent, to avoid a swarm of tiny fragments.
  * Prepend the breadcrumb to `embed_text` (what we embed + tokenize for BM25);
    keep `text` clean (what we show the user).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterator, List, Optional, Tuple

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(\s*)(```+|~~~+)(.*)$")
_HEX_TAIL = re.compile(r"\s+[0-9a-f]{16,}$")  # Notion appends a hex id to filenames


@dataclass
class Chunk:
    id: str
    source: str
    page_title: str
    heading_path: List[str]
    breadcrumb: str
    text: str
    embed_text: str
    has_code: bool
    chunk_index: int
    char_len: int
    tag: str = ""  # provenance: "notion", "pdf", ... (for metadata filtering)

    def to_dict(self) -> Dict:
        return asdict(self)


# --- markdown block iteration ------------------------------------------------

def _iter_blocks(lines: List[str]) -> Iterator[Tuple[str, str]]:
    """Yield (kind, text) blocks where kind in {"text", "code"}.

    Consecutive non-blank text lines form one text block; a fenced region forms
    one atomic code block (fences included).
    """
    i, n = 0, len(lines)
    buf: List[str] = []

    while i < n:
        m = _FENCE.match(lines[i])
        if m:
            # collect until matching closing fence
            fence = m.group(2)[0]  # ` or ~
            code = [lines[i]]
            i += 1
            while i < n:
                code.append(lines[i])
                if _FENCE.match(lines[i]) and lines[i].strip().startswith(fence * 3):
                    i += 1
                    break
                i += 1
            # flush any pending text first
            if buf:
                txt = "\n".join(buf).strip("\n")
                if txt.strip():
                    yield ("text", txt)
                buf = []
            yield ("code", "\n".join(code))
            continue
        buf.append(lines[i])
        i += 1

    if buf:
        txt = "\n".join(buf).strip("\n")
        if txt.strip():
            yield ("text", txt)


# --- section parsing ---------------------------------------------------------

@dataclass
class _Section:
    heading_path: List[str]
    blocks: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def char_len(self) -> int:
        return sum(len(t) for _, t in self.blocks)

    @property
    def has_code(self) -> bool:
        return any(k == "code" for k, _ in self.blocks)


def _parse_sections(text: str, page_title: str) -> List[_Section]:
    lines = text.splitlines()
    stack: List[Tuple[int, str]] = []  # (level, title)
    sections: List[_Section] = []
    cur_lines: List[str] = []

    def current_path() -> List[str]:
        path = [page_title] if page_title else []
        for _, t in stack:
            if path and t == path[-1]:  # H1 duplicates the derived page title
                continue
            path.append(t)
        return path

    def close_section():
        if cur_lines:
            blocks = list(_iter_blocks(cur_lines))
            if blocks:
                sections.append(_Section(heading_path=current_path(), blocks=blocks))

    inside_fence = False
    fence_char = ""
    for ln in lines:
        fm = _FENCE.match(ln)
        if fm:
            fc = fm.group(2)[0]
            if not inside_fence:
                inside_fence, fence_char = True, fc
            elif fc == fence_char:
                inside_fence = False
            cur_lines.append(ln)
            continue
        if inside_fence:
            cur_lines.append(ln)
            continue

        hm = _HEADING.match(ln)
        if hm:
            close_section()
            cur_lines = []
            level = len(hm.group(1))
            title = hm.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            cur_lines.append(ln)
    close_section()
    return sections


# --- packing sections into chunks -------------------------------------------

def _pack_blocks(blocks: List[Tuple[str, str]], target: int, hard: int
                 ) -> List[Tuple[str, bool]]:
    """Greedily pack blocks into chunk texts. Returns (chunk_text, has_code)."""
    chunks: List[Tuple[str, bool]] = []
    cur: List[str] = []
    cur_len = 0
    cur_code = False

    def emit():
        nonlocal cur, cur_len, cur_code
        if cur:
            chunks.append(("\n\n".join(cur).strip(), cur_code))
        cur, cur_len, cur_code = [], 0, False

    for kind, txt in blocks:
        tlen = len(txt)
        # An oversized code block becomes its own atomic chunk.
        if kind == "code" and tlen > hard:
            emit()
            chunks.append((txt, True))
            continue
        if cur_len and cur_len + tlen > hard:
            emit()
        cur.append(txt)
        cur_len += tlen + 2
        cur_code = cur_code or (kind == "code")
        if cur_len >= target:
            emit()
    emit()
    return chunks


def _stable_id(source: str, path: List[str], idx: int, text: str) -> str:
    h = hashlib.sha1()
    h.update(source.encode())
    h.update("\u241f".join(path).encode())
    h.update(str(idx).encode())
    h.update(text.encode())
    return h.hexdigest()[:16]


def chunk_markdown(text: str, source: str, page_title: Optional[str] = None,
                   *, target_chars: int = 1200, max_chars: int = 2000,
                   min_chars: int = 200, add_breadcrumb: bool = True,
                   tag: str = "") -> List[Chunk]:
    if page_title is None:
        page_title = _derive_title(text, source)

    sections = _parse_sections(text, page_title)

    # merge tiny sections into the previous one when they share a parent
    merged: List[_Section] = []
    for sec in sections:
        if (merged and sec.char_len < min_chars
                and merged[-1].heading_path[:-1] == sec.heading_path[:-1]):
            merged[-1].blocks.extend(sec.blocks)
        else:
            merged.append(sec)

    chunks: List[Chunk] = []
    idx = 0
    for sec in merged:
        breadcrumb = " > ".join(sec.heading_path)
        for chunk_text, has_code in _pack_blocks(sec.blocks, target_chars, max_chars):
            if not chunk_text.strip():
                continue
            embed_text = (f"{breadcrumb}\n\n{chunk_text}"
                          if add_breadcrumb and breadcrumb else chunk_text)
            cid = _stable_id(source, sec.heading_path, idx, chunk_text)
            chunks.append(Chunk(
                id=cid, source=source, page_title=page_title,
                heading_path=sec.heading_path, breadcrumb=breadcrumb,
                text=chunk_text, embed_text=embed_text, has_code=has_code,
                chunk_index=idx, char_len=len(chunk_text), tag=tag,
            ))
            idx += 1
    return chunks


def _derive_title(text: str, source: str) -> str:
    for ln in text.splitlines():
        m = _HEADING.match(ln)
        if m and len(m.group(1)) == 1:
            return m.group(2).strip()
    # fall back to filename without extension and Notion hex id
    import os
    base = os.path.splitext(os.path.basename(source))[0]
    return _HEX_TAIL.sub("", base).strip()
