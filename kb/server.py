"""Resident model daemon.

Loading BGE-M3 + the reranker takes several seconds, and BM25 would otherwise be
re-tokenized per call. The daemon loads everything once and serves searches over
localhost so the CLI stays a thin, instant client. Start it with:

    python3 server.py                 # uses ./kb_data + KB_BACKEND
    KB_BACKEND=mock python3 server.py # offline smoke test

Endpoints:
    GET  /health           -> {"status","backend","n_chunks"}
    POST /search {query, top_k?, use_hybrid?, use_rerank?} -> {"results": [...]}
"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from config import KBConfig, load_config
from backends import get_embedder, get_reranker
from index import Indices
from search import search, SearchParams


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    use_hybrid: Optional[bool] = None
    use_rerank: Optional[bool] = None
    tag: Optional[str] = None


def build_app(cfg: KBConfig) -> FastAPI:
    state = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        print(f"[daemon] backend={cfg.backend} loading models + indices...", flush=True)
        state["embedder"] = get_embedder(cfg)
        state["reranker"] = get_reranker(cfg)
        state["indices"] = Indices(cfg)
        print(f"[daemon] ready. {len(state['indices'].chunks)} chunks.", flush=True)
        yield
        state.clear()

    app = FastAPI(title="KB search daemon", lifespan=lifespan)

    @app.get("/health")
    def health():
        idx = state.get("indices")
        return {"status": "ok" if idx else "loading",
                "backend": cfg.backend,
                "n_chunks": len(idx.chunks) if idx else 0}

    @app.post("/search")
    def do_search(req: SearchRequest):
        params = SearchParams.from_config(cfg)
        if req.top_k is not None:
            params.top_k = req.top_k
        if req.use_hybrid is not None:
            params.use_hybrid = req.use_hybrid
        if req.use_rerank is not None:
            params.use_rerank = req.use_rerank
        results = search(req.query, state["indices"], state["embedder"],
                         state["reranker"], params, tag=req.tag or "")
        return {"results": results}

    return app


def main():
    # kept runnable for convenience; `kb serve` is the documented entry.
    import sys
    from cli import main as cli_main
    cli_main(["serve", *sys.argv[1:]])


if __name__ == "__main__":
    main()
