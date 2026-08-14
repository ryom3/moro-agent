"""Index construction and loading.

Dense  : ChromaDB persistent collection, embeddings supplied explicitly
         (embedding_function=None) so nothing is ever downloaded by Chroma.
Sparse : rank_bm25 BM25Okapi over the custom-tokenized corpus, pickled together
         with the id order and tokenizer settings so it loads instantly at query
         time (no re-tokenizing 14k chunks on every CLI call).
Chunks : chunks.jsonl -> in-memory id->chunk dict (14k is trivial to hold in RAM).
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from config import KBConfig
from tokenizer import HybridTokenizer


# --- chunk store -------------------------------------------------------------

def write_chunks(chunks: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def read_chunks(path: Path) -> List[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# --- build -------------------------------------------------------------------

# --- build (incremental) -----------------------------------------------------
# Dense (Chroma) is UPSERTED so multiple `ingest --source ...` runs accumulate.
# Sparse (BM25) is REBUILT over the full corpus every run, because BM25 needs the
# whole vocabulary/document set — a per-batch BM25 would drop earlier sources from
# keyword search. `reset` wipes everything for a clean rebuild.

import os as _os


def _metadata_of(c: dict) -> dict:
    # keys kept compatible with the previous implementation (source, path, chunk, tag)
    src = c.get("source", "")
    return {
        "source": _os.path.basename(src) or src,
        "path": src,
        "chunk": int(c.get("chunk_index", 0)),
        "tag": c.get("tag", ""),
        "page_title": c.get("page_title", ""),
        "breadcrumb": c.get("breadcrumb", ""),
        "has_code": bool(c.get("has_code", False)),
    }


def reset(cfg: KBConfig) -> None:
    """Delete the Chroma collection, chunks.jsonl and the BM25 pickle."""
    import chromadb
    chroma_dir = cfg.resolve("chroma_dir")
    if chroma_dir.exists():
        client = chromadb.PersistentClient(path=str(chroma_dir))
        try:
            client.delete_collection(cfg.collection)
        except Exception:
            pass
    for name in ("chunks_file", "bm25_file"):
        p = cfg.resolve(name)
        if p.exists():
            p.unlink()


def merge_chunks(cfg: KBConfig, new_chunks: List[dict]) -> List[dict]:
    """Merge new chunks into chunks.jsonl (dedupe/overwrite by id). Return full set."""
    path = cfg.resolve("chunks_file")
    existing: Dict[str, dict] = {}
    if path.exists():
        for c in read_chunks(path):
            existing[c["id"]] = c
    for c in new_chunks:
        existing[c["id"]] = c
    full = list(existing.values())
    write_chunks(full, path)
    return full


def upsert_dense(cfg: KBConfig, chunks: List[dict], embedder) -> None:
    """Embed the given chunks and upsert them into the Chroma collection."""
    import chromadb
    chroma_dir = cfg.resolve("chroma_dir")
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    col = client.get_or_create_collection(
        cfg.collection, embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    embed_texts = [c["embed_text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    t0 = time.time()
    B = 256
    for start in range(0, len(chunks), B):
        sl = slice(start, start + B)
        embs = embedder.encode(embed_texts[sl], is_query=False)
        col.upsert(ids=ids[sl], embeddings=embs.tolist(),
                   documents=[c["text"] for c in chunks[sl]],
                   metadatas=[_metadata_of(c) for c in chunks[sl]])
        print(f"  dense: {min(start + B, len(chunks))}/{len(chunks)} "
              f"({time.time() - t0:.1f}s)", flush=True)


def rebuild_sparse(cfg: KBConfig, full_chunks: List[dict]) -> None:
    """Rebuild BM25 over the ENTIRE corpus and pickle it."""
    from rank_bm25 import BM25Okapi
    tok = HybridTokenizer()
    ids = [c["id"] for c in full_chunks]
    print(f"  sparse: tokenizing {len(ids)} chunks...", flush=True)
    corpus_tokens = [tok.tokenize(c["embed_text"]) for c in full_chunks]
    bm25 = BM25Okapi(corpus_tokens)
    payload = {
        "bm25": bm25,
        "ids": ids,
        "tokenizer": {"drop_ja_stop": tok.drop_ja_stop,
                      "min_token_len": tok.min_token_len,
                      "has_fugashi": tok.has_fugashi()},
        "built_at": time.time(),
        "n": len(ids),
    }
    with cfg.resolve("bm25_file").open("wb") as f:
        pickle.dump(payload, f)
    print(f"  sparse: BM25 over {len(ids)} chunks pickled.", flush=True)


def build_indices(cfg: KBConfig, chunks: List[dict], embedder, *, reset_first: bool = True) -> None:
    """One-shot full build (used by tests / single-source ingest)."""
    if reset_first:
        reset(cfg)
    full = merge_chunks(cfg, chunks)
    upsert_dense(cfg, chunks, embedder)
    rebuild_sparse(cfg, full)


# --- load --------------------------------------------------------------------

class Indices:
    """Loaded indices + chunk map, ready for querying."""

    def __init__(self, cfg: KBConfig):
        import chromadb
        self.cfg = cfg
        client = chromadb.PersistentClient(path=str(cfg.resolve("chroma_dir")))
        self.col = client.get_collection(cfg.collection, embedding_function=None)

        with cfg.resolve("bm25_file").open("rb") as f:
            payload = pickle.load(f)
        self.bm25 = payload["bm25"]
        self.bm25_ids = payload["ids"]
        self.tok = HybridTokenizer(
            drop_ja_stop=payload["tokenizer"].get("drop_ja_stop", True),
            min_token_len=payload["tokenizer"].get("min_token_len", 1),
        )

        self.chunks: Dict[str, dict] = {
            c["id"]: c for c in read_chunks(cfg.resolve("chunks_file"))
        }

    def text_of(self, cid: str) -> str:
        c = self.chunks.get(cid)
        # rerank on the same text we embedded (breadcrumb included) for consistency
        return c["embed_text"] if c else ""

    def chunk(self, cid: str) -> Optional[dict]:
        return self.chunks.get(cid)

    # -- dense --
    def dense_search(self, query_emb: np.ndarray, k: int, tag: str = "") -> List[tuple]:
        where = {"tag": tag} if tag else None
        res = self.col.query(query_embeddings=[query_emb.tolist()], n_results=k,
                             where=where, include=["distances"])
        ids = res["ids"][0]
        dists = res["distances"][0]
        # cosine distance -> similarity
        return [(cid, 1.0 - d) for cid, d in zip(ids, dists)]

    # -- sparse --
    def sparse_search(self, query: str, k: int, tag: str = "") -> List[tuple]:
        q_tokens = self.tok.tokenize(query)
        if not q_tokens:
            return []
        scores = self.bm25.get_scores(q_tokens)
        order = np.argsort(scores)[::-1]  # full sort so tag-filtering can't starve k
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            cid = self.bm25_ids[i]
            if tag:
                c = self.chunks.get(cid)
                if not c or c.get("tag", "") != tag:
                    continue
            out.append((cid, float(scores[i])))
            if len(out) >= k:
                break
        return out
