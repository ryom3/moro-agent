"""Hybrid tokenizer for BM25.

Design goals (the whole reason we add BM25 alongside dense search):
  1. Preserve technical tokens verbatim so `SUID`, `/etc/passwd`, `LD_PRELOAD`,
     `CVE-2021-4034` are matchable exactly. A naive whitespace/regex tokenizer
     mangles these.
  2. Also emit sub-tokens (split on . / - _) so `/etc/passwd` matches a query for
     `passwd`, and `CVE-2021-4034` matches `cve`. Maximizes recall.
  3. Segment Japanese properly (日英混在) via fugashi/MeCab; fall back to CJK
     character bigrams if fugashi is unavailable.

Query text and document text MUST go through the same tokenizer, so the same
instance is used on both sides.
"""
from __future__ import annotations

import re
from typing import List

# A technical token: starts alnum/underscore, may contain . / - _ afterwards.
_TECH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]*")
# Split a technical token into sub-tokens.
_SUBSPLIT = re.compile(r"[./\-_]+")
# Runs of CJK characters (for the bigram fallback).
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]+")
# A token worth indexing has at least one alphanumeric or CJK char.
_HAS_CONTENT = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")

# Small, conservative Japanese stoplist (particles / aux). BM25 idf already
# down-weights ubiquitous tokens, but dropping these shrinks the index a bit.
_JA_STOP = {
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し", "れ", "さ",
    "ある", "いる", "も", "する", "から", "な", "こと", "として", "い", "や",
    "など", "なる", "へ", "か", "だ", "これ", "によって", "により", "その",
    "それ", "この", "ため", "もの", "ます", "です", "この",
}


class HybridTokenizer:
    def __init__(self, use_fugashi: bool = True, drop_ja_stop: bool = True,
                 min_token_len: int = 1):
        self.drop_ja_stop = drop_ja_stop
        self.min_token_len = min_token_len
        self._tagger = None
        if use_fugashi:
            try:
                import fugashi  # type: ignore
                self._tagger = fugashi.Tagger()
            except Exception:
                self._tagger = None  # graceful fallback to bigrams

    # -- public --------------------------------------------------------------
    def tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        tokens: List[str] = []
        masked_chars = list(text)

        # 1) technical tokens (matched on original, emitted lowercased) + subtokens
        for m in _TECH.finditer(text):
            tok = m.group(0).lower()
            tokens.append(tok)
            subs = [s for s in _SUBSPLIT.split(tok) if s]
            if len(subs) > 1:
                tokens.extend(subs)
            # blank the span so the JP segmenter never sees ASCII/technical text
            for i in range(m.start(), m.end()):
                masked_chars[i] = " "

        masked = "".join(masked_chars)

        # 2) Japanese / remaining text
        tokens.extend(self._segment_ja(masked))

        # 3) cleanup
        out = []
        for t in tokens:
            t = t.strip()
            if not t or len(t) < self.min_token_len:
                continue
            if not _HAS_CONTENT.search(t):  # pure punctuation
                continue
            if self.drop_ja_stop and t in _JA_STOP:
                continue
            out.append(t)
        return out

    __call__ = tokenize

    # -- internals -----------------------------------------------------------
    def _segment_ja(self, text: str) -> List[str]:
        if self._tagger is not None:
            toks = []
            for w in self._tagger(text):
                s = w.surface.strip()
                if s and not s.isspace():
                    toks.append(s)
            return toks
        # fallback: character bigrams over CJK runs
        toks: List[str] = []
        for run in _CJK_RUN.findall(text):
            if len(run) == 1:
                toks.append(run)
            else:
                toks.extend(run[i:i + 2] for i in range(len(run) - 1))
        return toks

    def has_fugashi(self) -> bool:
        return self._tagger is not None
