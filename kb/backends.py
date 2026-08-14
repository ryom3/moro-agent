"""Model backends: dense embedder + cross-encoder reranker.

Two implementations of each:
  * BGE ("bge")  -> BGE-M3 dense via FlagEmbedding; bge-reranker-v2-m3 via
                    transformers directly (no FlagEmbedding, to avoid its
                    version-fragile reranker path). Weights download on first use.
  * Mock ("mock")-> deterministic, no network. Used for offline smoke tests of
                    the whole pipeline (chunking -> index -> fuse -> rerank -> CLI).
                    The mock embedder is a hashing/bag-of-tokens projection so it
                    carries *some* lexical signal (not pure noise); the mock
                    reranker scores by token overlap. Good enough to verify wiring
                    and eval plumbing; NOT a substitute for real retrieval quality.

Select with the KB_BACKEND env var (default "bge").
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

import numpy as np

from tokenizer import HybridTokenizer


def _stable_hash(s: str) -> int:
    return int.from_bytes(hashlib.md5(s.encode()).digest()[:8], "little")


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


# --- Embedders ---------------------------------------------------------------

class BGEM3Embedder:
    def __init__(self, model_name: str, device: Optional[str] = None,
                 use_fp16: bool = True, batch_size: int = 12, max_length: int = 1024):
        from FlagEmbedding import BGEM3FlagModel  # lazy import
        kwargs = {"use_fp16": use_fp16}
        if device:
            kwargs["devices"] = device
        self.model = BGEM3FlagModel(model_name, **kwargs)
        self.dim = 1024
        self.batch_size = batch_size
        self.max_length = max_length

    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        out = self.model.encode(texts, batch_size=self.batch_size,
                                max_length=self.max_length)["dense_vecs"]
        return _l2_normalize(np.asarray(out, dtype="float32"))


class MockEmbedder:
    """Feature-hashing bag-of-tokens embedder. Deterministic, offline."""

    def __init__(self, dim: int = 256, tokenizer: Optional[HybridTokenizer] = None):
        self.dim = dim
        self.tok = tokenizer or HybridTokenizer()

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype="float32")
        for t in self.tok.tokenize(text):
            h = _stable_hash(t)
            v[h % self.dim] += 1.0
            v[(h >> 16) % self.dim] += 0.5  # a second hash reduces collisions
        return v

    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        m = np.stack([self._vec(t) for t in texts])
        return _l2_normalize(m)


# --- Rerankers ---------------------------------------------------------------

class BGEReranker:
    """Cross-encoder reranker via transformers directly (no FlagEmbedding).

    FlagEmbedding's FlagReranker breaks across transformers versions (it calls the
    removed tokenizer method `prepare_for_model`, and its loader passes a `dtype`
    kwarg newer transformers reject). Driving the model through the plain
    transformers API sidesteps both: we tokenize (query, passage) pairs ourselves
    and read the single classification logit. score = sigmoid(logit) in [0,1],
    matching FlagReranker(normalize=True).
    """

    def __init__(self, model_name: str, use_fp16: bool = True,
                 device: Optional[str] = None, max_length: int = 512,
                 batch_size: int = 16):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        if use_fp16 and self.device == "cuda":
            self.model = self.model.half()
        self.model = self.model.to(self.device).eval()
        self.max_length = max_length
        self.batch_size = batch_size

    def score(self, query: str, passages: List[str]) -> List[float]:
        if not passages:
            return []
        torch = self._torch
        out: List[float] = []
        for i in range(0, len(passages), self.batch_size):
            batch = passages[i:i + self.batch_size]
            enc = self.tokenizer(
                [query] * len(batch), batch,
                padding=True, truncation=True,
                max_length=self.max_length, return_tensors="pt",
            ).to(self.device)
            with torch.no_grad():
                logits = self.model(**enc).logits.view(-1).float()
                probs = torch.sigmoid(logits)
            out.extend(probs.cpu().tolist())
        return out


class MockReranker:
    """Token-overlap (weighted Jaccard) reranker. Deterministic, offline."""

    def __init__(self, tokenizer: Optional[HybridTokenizer] = None):
        self.tok = tokenizer or HybridTokenizer()

    def score(self, query: str, passages: List[str]) -> List[float]:
        q = set(self.tok.tokenize(query))
        if not q:
            return [0.0] * len(passages)
        out = []
        for p in passages:
            pt = set(self.tok.tokenize(p))
            inter = len(q & pt)
            out.append(inter / (len(q) + 1e-9))
        return out


# --- Factory -----------------------------------------------------------------

def get_embedder(cfg):
    if cfg.backend == "mock":
        return MockEmbedder(dim=min(cfg.embed_dim, 256))
    return BGEM3Embedder(cfg.embed_model, device=cfg.device, use_fp16=cfg.use_fp16,
                         batch_size=cfg.embed_batch_size, max_length=cfg.embed_max_length)


def get_reranker(cfg):
    if cfg.backend == "mock":
        return MockReranker()
    return BGEReranker(cfg.rerank_model, use_fp16=cfg.use_fp16, device=cfg.device)