"""Evaluation harness — the piece that tells you which change actually helped.

Define ~20-30 queries with the gold chunks you know should surface, then this
runs the pipeline under several configs and prints Recall@k / MRR / nDCG so you
can see, on *your* corpus, the marginal value of hybrid fusion and reranking.

Eval set format (YAML), see eval_set.example.yaml:

    - query: "SUID privilege escalation"
      gold:                       # a hit = a retrieved chunk matching ANY predicate
        - source_contains: "PrivEsc"
        - breadcrumb_contains: "SUID"
        - text_contains: "find / -perm -4000"

Predicate keys: source_contains, page_title_contains, breadcrumb_contains,
text_contains, id (exact). Matching is case-insensitive except `id`.

Usage:
    KB_BACKEND=mock python3 kb/eval.py --eval-set eval_set.example.yaml
    python3 kb/eval.py --eval-set my_eval.yaml --k 1 3 5 10
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List

import yaml

from config import KBConfig, load_config
from backends import get_embedder, get_reranker
from index import Indices
from search import search, SearchParams


def _match(chunk: dict, pred: Dict) -> bool:
    for key, val in pred.items():
        if key == "id":
            if chunk.get("id") != val:
                return False
            continue
        field = {
            "source_contains": "source",
            "page_title_contains": "page_title",
            "breadcrumb_contains": "breadcrumb",
            "text_contains": "text",
        }.get(key)
        if field is None:
            continue
        hay = str(chunk.get(field, "")).lower()
        if str(val).lower() not in hay:
            return False
    return True


def is_hit(chunk: dict, gold: List[Dict]) -> bool:
    return any(_match(chunk, pred) for pred in gold)


def eval_config(name: str, params: SearchParams, eval_items, indices,
                embedder, reranker, ks: List[int]) -> Dict:
    max_k = max(ks)
    params = SearchParams(**{**params.__dict__, "top_k": max_k})
    recall = {k: 0 for k in ks}
    rr_sum = 0.0
    ndcg_sum = 0.0
    n = len(eval_items)

    for item in eval_items:
        gold = item["gold"]
        results = search(item["query"], indices, embedder, reranker, params)
        hits = [is_hit(indices.chunk(r["id"]) or r, gold) for r in results]

        # first relevant rank -> MRR
        first = next((i for i, h in enumerate(hits) if h), None)
        if first is not None:
            rr_sum += 1.0 / (first + 1)
        # recall@k (binary: at least one relevant in top k)
        for k in ks:
            if any(hits[:k]):
                recall[k] += 1
        # nDCG@max_k (binary gains). IDCG = ideal ordering of the hits we found,
        # so nDCG in [0,1] measures how well relevant chunks are pushed to the top.
        dcg = sum(1.0 / math.log2(i + 2) for i, h in enumerate(hits) if h)
        n_hits = sum(hits)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(n_hits))
        ndcg_sum += (dcg / idcg) if idcg else 0.0

    return {
        "config": name,
        "recall": {k: recall[k] / n for k in ks},
        "mrr": rr_sum / n,
        "ndcg": ndcg_sum / n,
    }


def run_eval(cfg, eval_items, ks, embedder=None, reranker=None, indices=None):
    from backends import get_embedder, get_reranker
    from index import Indices
    embedder = embedder or get_embedder(cfg)
    reranker = reranker or get_reranker(cfg)
    indices = indices or Indices(cfg)
    base = SearchParams.from_config(cfg)
    configs = [
        ("dense_only",     SearchParams(**{**base.__dict__, "use_hybrid": False, "use_rerank": False})),
        ("hybrid",         SearchParams(**{**base.__dict__, "use_hybrid": True,  "use_rerank": False})),
        ("hybrid+rerank",  SearchParams(**{**base.__dict__, "use_hybrid": True,  "use_rerank": True})),
    ]
    return [eval_config(name, p, eval_items, indices, embedder, reranker, ks)
            for name, p in configs]


def print_eval_table(rows, ks):
    header = f"{'config':<16}" + "".join(f"R@{k:<5}" for k in ks) + f"{'MRR':<8}{'nDCG':<8}"
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        line = f"{r['config']:<16}"
        line += "".join(f"{r['recall'][k]:<7.3f}" for k in ks)
        line += f"{r['mrr']:<8.3f}{r['ndcg']:<8.3f}"
        print(line)
    print("\nHigher is better. Compare rows to see the marginal gain of hybrid and rerank.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--k", nargs="+", type=int, default=[1, 3, 5, 10])
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.data_dir:
        cfg.data_dir = args.data_dir

    eval_items = yaml.safe_load(Path(args.eval_set).read_text())
    print(f"[eval] {len(eval_items)} queries; backend={cfg.backend}")
    rows = run_eval(cfg, eval_items, args.k)
    print_eval_table(rows, args.k)


if __name__ == "__main__":
    main()
