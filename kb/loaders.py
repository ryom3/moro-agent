"""Document loaders (Phase 2).

The hard part of this KB is the PDF course material (OffSec etc.): 2-column
layouts, code/terminal blocks, and screenshots. Naive extraction (pdfplumber
`extract_text`) reads across columns, so a left-column line and a right-column
line at the same height get merged into one garbled line — which then poisons
every downstream chunk.

`load_pdf` fixes this with PyMuPDF block geometry:
  * Column detection: text blocks are assigned to a left/right column by their
    x-center; full-width blocks (titles) stay in the main flow. Reading order
    becomes "all of left column, then all of right column" instead of interleaving.
  * Headings: font sizes larger than the body size become markdown headings, so
    the structure-based chunker gets real breadcrumbs.
  * Code preservation: lines set in a monospaced font are wrapped in ``` fences,
    so `find / -perm -4000`, `LD_PRELOAD=...` etc. survive intact and are indexed
    verbatim by the BM25 tokenizer.
  * OCR (optional, --ocr): pages with almost no extractable text are rasterized
    and run through Tesseract (needs the tesseract binary + pytesseract), to pull
    text out of screenshots. Skipped gracefully if unavailable.

Output is markdown, so PDFs flow through the SAME chunker as Notion notes.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import List, Tuple

_MONO_RE = re.compile(r"mono|courier|consol|menlo|inconsolata|hack|fira\s*code|source\s*code", re.I)


# --- public dispatch ---------------------------------------------------------

def load(path: Path, *, ocr: bool = False, pdf_engine: str = "native",
         ocr_lang: str = "eng+jpn") -> Tuple[str, bool]:
    """Return (text, is_markdown). is_markdown=True means the structure-based
    chunker should honor headings/breadcrumbs/code fences."""
    ext = path.suffix.lower()
    if ext == ".md":
        return path.read_text(encoding="utf-8", errors="replace"), True
    if ext in (".txt", ".csv"):
        return path.read_text(encoding="utf-8", errors="replace"), False
    if ext == ".pdf":
        return load_pdf(path, ocr=ocr, engine=pdf_engine, ocr_lang=ocr_lang), True
    return "", False


# --- PDF ---------------------------------------------------------------------

def load_pdf(path: Path, *, ocr: bool = False, engine: str = "native",
             ocr_min_chars: int = 40, ocr_lang: str = "eng+jpn") -> str:
    import pymupdf  # PyMuPDF

    if engine == "pymupdf4llm":
        import pymupdf4llm
        return pymupdf4llm.to_markdown(str(path))

    doc = pymupdf.open(str(path))
    pages_md: List[str] = []
    for page in doc:
        md, nchars = _page_to_markdown(page)
        if ocr and nchars < ocr_min_chars:
            ocr_text = _ocr_page(page, lang=ocr_lang)
            if ocr_text.strip():
                md = (md + "\n\n" + ocr_text).strip()
        if md.strip():
            pages_md.append(md)
    doc.close()
    return "\n\n".join(pages_md)


def _is_mono(span: dict) -> bool:
    # PyMuPDF flags bit 3 (value 8) marks a monospaced font; also match by name.
    if span.get("flags", 0) & 8:
        return True
    return bool(_MONO_RE.search(span.get("font", "")))


def _page_to_markdown(page) -> Tuple[str, int]:
    d = page.get_text("dict")
    blocks = [b for b in d.get("blocks", []) if b.get("type", 0) == 0 and b.get("lines")]
    if not blocks:
        return "", 0

    W = page.rect.width
    mid = W / 2.0

    def column(b) -> int:
        x0, _, x1, _ = b["bbox"]
        if (x1 - x0) > 0.7 * W:      # full-width block (title/banner) -> main flow
            return 0
        return 0 if (x0 + x1) / 2.0 < mid else 1

    left = [b for b in blocks if column(b) == 0]
    right = [b for b in blocks if column(b) == 1]

    def y_overlap(a, b) -> float:
        return min(a[3], b[3]) - max(a[1], b[1])

    # Side-by-side columns are the real signal: a left block and a right block
    # occupying the same vertical band. That's exactly what naive extraction
    # interleaves into garbled lines.
    two_col = any(y_overlap(l["bbox"], r["bbox"]) > 5 for l in left for r in right)
    if two_col:
        ordered = sorted(left, key=lambda b: b["bbox"][1]) + sorted(right, key=lambda b: b["bbox"][1])
    else:
        ordered = sorted(blocks, key=lambda b: b["bbox"][1])

    # body font size = most common rounded span size
    sizes = [round(s["size"]) for b in blocks for ln in b["lines"]
             for s in ln["spans"] if s["text"].strip()]
    body = Counter(sizes).most_common(1)[0][0] if sizes else 10
    heading_sizes = sorted({s for s in sizes if s > body + 1}, reverse=True)[:3]
    size_to_level = {s: i + 1 for i, s in enumerate(heading_sizes)}

    # classify each line within each block; a "BREAK" sentinel marks block
    # boundaries so prose is never joined across blocks (or the column seam).
    classified: List[Tuple[bool, "int|str|None", str]] = []
    nchars = 0
    for b in ordered:
        for ln in b["lines"]:
            spans = ln["spans"]
            txt = "".join(s["text"] for s in spans)
            if not txt.strip():
                continue
            nchars += len(txt.strip())
            real = [s for s in spans if s["text"].strip()]
            mono = sum(1 for s in real if _is_mono(s))
            is_code = bool(real) and mono / len(real) >= 0.6
            maxsize = max((round(s["size"]) for s in real), default=body)
            lvl = None if is_code else size_to_level.get(maxsize)
            classified.append((is_code, lvl, txt.rstrip()))
        classified.append((False, "BREAK", ""))

    # assemble markdown: fence consecutive code lines, join consecutive prose lines
    out: List[str] = []
    i, n = 0, len(classified)
    while i < n:
        is_code, lvl, txt = classified[i]
        if lvl == "BREAK":
            i += 1
            continue
        if is_code:
            block = [txt]
            i += 1
            while i < n and classified[i][0]:
                block.append(classified[i][2])
                i += 1
            out.append("```\n" + "\n".join(block) + "\n```")
        elif lvl:
            out.append("#" * lvl + " " + txt.strip())
            i += 1
        else:
            para = [txt.strip()]
            i += 1
            while i < n and not classified[i][0] and classified[i][1] is None:
                para.append(classified[i][2].strip())
                i += 1
            out.append(" ".join(para))
    return "\n\n".join(out), nchars


def _ocr_page(page, lang: str = "eng+jpn", dpi: int = 200) -> str:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""  # OCR deps not installed; skip silently
    try:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    except Exception:
        return ""
    for attempt in (lang, "eng"):   # fall back to eng if the language pack is missing
        try:
            return pytesseract.image_to_string(img, lang=attempt)
        except Exception:
            continue
    return ""
