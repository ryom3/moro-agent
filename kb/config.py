"""Central configuration for the KB pipeline.

All paths are relative to `data_dir` unless absolute. Override any field via a
YAML file passed to `load_config(path)`, or via environment variables for the
few that matter at runtime (KB_BACKEND, KB_PORT).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@dataclass
class KBConfig:
    # --- storage layout -----------------------------------------------------
    data_dir: str = "./kb_data"          # everything the pipeline writes lives here
    chunks_file: str = "chunks.jsonl"    # one chunk per line
    chroma_dir: str = "db"               # persistent Chroma dir (dense index)
    bm25_file: str = "bm25.pkl"          # pickled sparse index
    collection: str = "pentest-kb"       # Chroma collection name

    # --- chunking (structure-based) -----------------------------------------
    target_chars: int = 1200             # soft target size of a chunk
    max_chars: int = 2000                # hard cap (a single code block may exceed this and stay atomic)
    min_chars: int = 200                 # sections shorter than this get merged with a sibling
    add_breadcrumb: bool = True          # prepend "Page > H2 > H3" to embedded/indexed text

    # --- models -------------------------------------------------------------
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    embed_dim: int = 1024                # BGE-M3 dense dim (mock backend overrides)
    use_fp16: bool = True
    device: Optional[str] = None         # None -> auto (cuda if available else cpu)
    embed_batch_size: int = 12
    embed_max_length: int = 1024

    # --- retrieval pipeline -------------------------------------------------
    dense_k: int = 50                    # candidates from dense search
    sparse_k: int = 50                   # candidates from BM25
    rrf_k: int = 60                      # RRF damping constant
    fuse_k: int = 50                     # candidates kept after fusion (fed to reranker)
    top_k: int = 3                       # final results returned
    use_hybrid: bool = True              # False -> dense only
    use_rerank: bool = True              # False -> return fused order

    # --- daemon -------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8917

    # ------------------------------------------------------------------------
    def resolve(self, name: str) -> Path:
        """Resolve a filename against data_dir (absolute paths pass through)."""
        p = Path(getattr(self, name))
        if p.is_absolute():
            return p
        return Path(self.data_dir) / p

    @property
    def backend(self) -> str:
        """'bge' (real models) or 'mock' (deterministic, no downloads)."""
        return os.environ.get("KB_BACKEND", "bge").lower()

    @property
    def runtime_port(self) -> int:
        return int(os.environ.get("KB_PORT", self.port))

    def to_yaml(self, path: str) -> None:
        if yaml is None:
            raise RuntimeError("pyyaml not installed")
        Path(path).write_text(yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True))


def load_config(path: Optional[str] = None) -> KBConfig:
    cfg = KBConfig()
    if path:
        if yaml is None:
            raise RuntimeError("pyyaml not installed but a config path was given")
        data = yaml.safe_load(Path(path).read_text()) or {}
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg
