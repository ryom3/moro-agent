"""The retrieval pipeline.

  query
   -> dense (BGE-M3)  top dense_k  ─┐
   -> sparse (BM25)   top sparse_k ─┴─> Reciprocal Rank Fusion -> top fuse_k
   -> cross-encoder rerank (bge-reranker-v2-m3) -> top_k

Every stage is toggleable via SearchParams so eval.py can measure the marginal
contribution of hybrid fusion and of reranking on *your* corpus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from config import KBConfig


@dataclass
class SearchParams:
    dense_k: int = 50
    sparse_k: int = 50
    rrf_k: int = 60
    fuse_k: int = 50
    top_k: int = 3
    use_hybrid: bool = True   # False -> dense candidates only
    use_rerank: bool = True   # False -> return fused order

    @classmethod
    def from_config(cls, cfg: KBConfig) -> "SearchParams":
        return cls(dense_k=cfg.dense_k, sparse_k=cfg.sparse_k, rrf_k=cfg.rrf_k,
                   fuse_k=cfg.fuse_k, top_k=cfg.top_k,
                   use_hybrid=cfg.use_hybrid, use_rerank=cfg.use_rerank)


def reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = 60
                           ) -> List[Tuple[str, float]]:
    """RRF over any number of ranked id-lists. score = sum 1/(k + rank)."""
    scores: Dict[str, float] = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def search(query: str, indices, embedder, reranker, params: SearchParams,
           tag: str = "") -> List[dict]:
    # 1) dense
    q_emb = embedder.encode([query], is_query=True)[0]
    dense_hits = indices.dense_search(q_emb, params.dense_k, tag=tag)
    dense_ids = [cid for cid, _ in dense_hits]
    dense_sim = {cid: s for cid, s in dense_hits}

    # 2) sparse (optional)
    if params.use_hybrid:
        sparse_hits = indices.sparse_search(query, params.sparse_k, tag=tag)
        sparse_ids = [cid for cid, _ in sparse_hits]
        sparse_score = {cid: s for cid, s in sparse_hits}
        fused = reciprocal_rank_fusion([dense_ids, sparse_ids], k=params.rrf_k)
    else:
        sparse_ids, sparse_score = [], {}
        fused = [(cid, dense_sim[cid]) for cid in dense_ids]

    fused = fused[: params.fuse_k]

    # 3) rerank (optional)
    if params.use_rerank and fused:
        passages = [indices.text_of(cid) for cid, _ in fused]
        rr = reranker.score(query, passages)
        order = sorted(range(len(fused)), key=lambda i: rr[i], reverse=True)
        ranked = [(fused[i][0], rr[i]) for i in order]
        score_kind = "rerank"
    else:
        ranked = fused
        score_kind = "rrf" if params.use_hybrid else "cosine"

    # 4) assemble results
    out: List[dict] = []
    for cid, score in ranked[: params.top_k]:
        c = indices.chunk(cid) or {}
        out.append({
            "id": cid,
            "score": float(score),
            "score_kind": score_kind,
            "source": c.get("source"),
            "page_title": c.get("page_title"),
            "breadcrumb": c.get("breadcrumb"),
            "tag": c.get("tag", ""),
            "has_code": c.get("has_code"),
            "text": c.get("text", ""),
            "dense_sim": dense_sim.get(cid),
            "sparse_score": sparse_score.get(cid),
        })
    return out
