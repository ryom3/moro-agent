"""Unified `kb` command.

    kb ingest --source ~/notion-export/ --tag notion --reset
    kb ingest --source ~/pdfs/ --tag oscp --ocr        # PDF layout + OCR
    kb query "SUID privilege escalation" --json
    kb query "SQLi bypass WAF" --tag cheatsheet --top 5
    kb serve                                            # resident model daemon
    kb eval --eval-set my_eval.yaml
    kb sync --ingest                                    # Notion API -> KB
    kb status                                           # what's indexed / daemon up?

Legacy entry points `ingest.py` / `query.py` are thin shims that call into here,
so existing agent commands and notion_sync keep working.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

from config import KBConfig, load_config

KB_DIR = Path(__file__).parent


def _cfg(args) -> KBConfig:
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "data_dir", None):
        cfg.data_dir = args.data_dir
    return cfg


# ------------------------------------------------------------------ ingest ----

def cmd_ingest(args) -> None:
    from chunking import chunk_markdown
    from loaders import load
    from index import reset as reset_indices, merge_chunks, upsert_dense, rebuild_sparse
    from backends import get_embedder

    cfg = _cfg(args)
    if args.reset:
        reset_indices(cfg)
        print("index reset.")

    print(f"[ingest] backend={cfg.backend}; loading embedder...")
    embedder = get_embedder(cfg)

    SKIP = (".git", "__pycache__", "node_modules", ".trash")
    EXTS = (".md", ".pdf", ".txt", ".csv")

    t0 = time.time()
    new_chunks: List[dict] = []
    for src in args.source:
        root = Path(src).expanduser()
        if not root.is_dir():
            print(f"[ingest] skip (not a dir): {root}")
            continue
        print(f"[ingest] {root}" + (f" (tag={args.tag})" if args.tag else ""))
        files = [p for p in sorted(root.rglob("*"))
                 if p.suffix.lower() in EXTS and not any(s in str(p) for s in SKIP)]
        for i, f in enumerate(files, 1):
            try:
                text, is_md = load(f, ocr=args.ocr, pdf_engine=args.pdf_engine,
                                   ocr_lang=args.ocr_lang)
            except Exception as e:
                print(f"  [{i}/{len(files)}] {f.name} -> ERROR: {e}")
                continue
            if len(text.strip()) < 100:
                continue
            cs = chunk_markdown(
                text, source=str(f),
                target_chars=cfg.target_chars, max_chars=cfg.max_chars,
                min_chars=cfg.min_chars,
                add_breadcrumb=cfg.add_breadcrumb if is_md else False,
                tag=args.tag)
            new_chunks.extend(c.to_dict() for c in cs)
            print(f"  [{i}/{len(files)}] {f.name} -> {len(cs)} chunks")

    if not new_chunks:
        sys.exit("no chunks produced — check the source paths")

    print(f"[ingest] {len(new_chunks)} new chunks; updating indices...")
    full = merge_chunks(cfg, new_chunks)
    upsert_dense(cfg, new_chunks, embedder)   # dense: upsert this run
    rebuild_sparse(cfg, full)                 # sparse: rebuild over full corpus
    print(f"[ingest] done in {time.time() - t0:.1f}s. total chunks: {len(full)}")


# ------------------------------------------------------------------- query ----

def _via_daemon(cfg, query, top_k, use_hybrid, use_rerank, tag):
    import requests
    base = f"http://{cfg.host}:{cfg.runtime_port}"
    try:
        h = requests.get(f"{base}/health", timeout=0.5)
        if h.status_code != 200 or h.json().get("status") != "ok":
            return None
    except Exception:
        return None
    payload = {"query": query}
    if top_k is not None:
        payload["top_k"] = top_k
    if use_hybrid is not None:
        payload["use_hybrid"] = use_hybrid
    if use_rerank is not None:
        payload["use_rerank"] = use_rerank
    if tag:
        payload["tag"] = tag
    r = requests.post(f"{base}/search", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["results"]


def _in_process(cfg, query, top_k, use_hybrid, use_rerank, tag):
    from backends import get_embedder, get_reranker
    from index import Indices
    from search import search, SearchParams
    print("[query] daemon not running; loading models in-process "
          "(slow — run `kb serve` for a hot path)", file=sys.stderr)
    emb = get_embedder(cfg)
    rr = get_reranker(cfg)
    idx = Indices(cfg)
    params = SearchParams.from_config(cfg)
    if top_k is not None:
        params.top_k = top_k
    if use_hybrid is not None:
        params.use_hybrid = use_hybrid
    if use_rerank is not None:
        params.use_rerank = use_rerank
    return search(query, idx, emb, rr, params, tag=tag)


def _print_human(query, results):
    print(f"\n query: {query}\n" + "─" * 68)
    if not results:
        print(" (no results)")
        return
    for i, r in enumerate(results, 1):
        code = " [code]" if r.get("has_code") else ""
        tg = f" #{r['tag']}" if r.get("tag") else ""
        print(f"[{i}] {r['score']:.3f} ({r.get('score_kind','')}){code}{tg}  "
              f"{r.get('breadcrumb') or r.get('page_title','')}")
        print(f"    source: {r.get('source')}")
        snippet = (r.get("text") or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + " …"
        print(f"    {snippet}\n")


def cmd_query(args) -> None:
    cfg = _cfg(args)
    use_hybrid = False if args.no_hybrid else None
    use_rerank = False if args.no_rerank else None
    tag = args.tag or ""
    results = None
    if not args.no_daemon:
        results = _via_daemon(cfg, args.query, args.top_k, use_hybrid, use_rerank, tag)
    if results is None:
        if not cfg.resolve("chunks_file").exists() or not cfg.resolve("bm25_file").exists():
            print("KB is empty or not built yet. Run:  kb ingest --source <dir> --tag <tag>",
                  file=sys.stderr)
            return
        results = _in_process(cfg, args.query, args.top_k, use_hybrid, use_rerank, tag)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human(args.query, results)


# ------------------------------------------------------------------- serve ----

def cmd_serve(args) -> None:
    from server import build_app
    import uvicorn
    cfg = _cfg(args)
    host = args.host or cfg.host
    port = args.port or cfg.runtime_port
    print(f"[serve] http://{host}:{port}  (backend={cfg.backend})")
    uvicorn.run(build_app(cfg), host=host, port=port, log_level="warning")


# -------------------------------------------------------------------- eval ----

def cmd_eval(args) -> None:
    import yaml
    from eval import run_eval, print_eval_table
    cfg = _cfg(args)
    eval_items = yaml.safe_load(Path(args.eval_set).read_text())
    print(f"[eval] {len(eval_items)} queries; backend={cfg.backend}")
    rows = run_eval(cfg, eval_items, args.k)
    print_eval_table(rows, args.k)


# -------------------------------------------------------------------- sync ----

def cmd_sync(args) -> None:
    # delegate to notion_sync.py, forwarding relevant flags
    cmd = [sys.executable, str(KB_DIR / "notion_sync.py")]
    if args.list:
        cmd.append("--list")
    if args.select:
        cmd.append("--select")
    if args.diff:
        cmd.append("--diff")
    if args.ingest:
        cmd.append("--ingest")
    subprocess.run(cmd, check=False)


# ------------------------------------------------------------------ status ----

def cmd_status(args) -> None:
    import requests
    from index import read_chunks
    cfg = _cfg(args)
    chunks_path = cfg.resolve("chunks_file")
    print(f"data_dir : {cfg.data_dir}")
    print(f"backend  : {cfg.backend}")
    if chunks_path.exists():
        chunks = read_chunks(chunks_path)
        by_tag = Counter(c.get("tag", "") or "(none)" for c in chunks)
        with_code = sum(1 for c in chunks if c.get("has_code"))
        srcs = len({c.get("source") for c in chunks})
        print(f"chunks   : {len(chunks)}  (from {srcs} files, {with_code} contain code)")
        for tag, n in by_tag.most_common():
            print(f"   #{tag}: {n}")
    else:
        print("chunks   : none (run `kb ingest` first)")
    for name in ("chroma_dir", "bm25_file"):
        p = cfg.resolve(name)
        print(f"{name:9}: {'ok' if p.exists() else 'missing'} ({p})")
    base = f"http://{cfg.host}:{cfg.runtime_port}"
    try:
        h = requests.get(f"{base}/health", timeout=0.5).json()
        print(f"daemon   : up ({base}, {h.get('n_chunks')} chunks)")
    except Exception:
        print(f"daemon   : down ({base}) — run `kb serve`")


# -------------------------------------------------------------------- main ----

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kb", description="Local security knowledge base.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # common flags, accepted AFTER the subcommand (so shims can forward them)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None)
    common.add_argument("--data-dir", default=None)

    p = sub.add_parser("ingest", parents=[common], help="index documents (md/pdf/txt/csv)")
    p.add_argument("--source", action="append", required=True, help="source dir (repeatable)")
    p.add_argument("--tag", default="", help="provenance tag stamped on every chunk")
    p.add_argument("--reset", action="store_true", help="wipe index before ingesting")
    p.add_argument("--ocr", action="store_true", help="OCR pages with little text (needs tesseract)")
    p.add_argument("--ocr-lang", default="eng+jpn", help="tesseract langs, e.g. eng or eng+jpn")
    p.add_argument("--pdf-engine", default="native", choices=["native", "pymupdf4llm"],
                   help="PDF extraction engine")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("query", parents=[common], help="search the KB")
    p.add_argument("query")
    p.add_argument("--top-k", "--top", dest="top_k", type=int, default=None)
    p.add_argument("--tag", default="", help="filter to this provenance tag")
    p.add_argument("--no-hybrid", action="store_true", help="dense only")
    p.add_argument("--no-rerank", action="store_true", help="skip cross-encoder")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-daemon", action="store_true", help="force in-process")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("serve", parents=[common], help="run the resident model daemon")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("eval", parents=[common], help="measure retrieval quality across configs")
    p.add_argument("--eval-set", required=True)
    p.add_argument("--k", nargs="+", type=int, default=[1, 3, 5, 10])
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("sync", parents=[common], help="sync Notion via API (notion_sync.py)")
    p.add_argument("--list", action="store_true")
    p.add_argument("--select", action="store_true")
    p.add_argument("--diff", action="store_true")
    p.add_argument("--ingest", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("status", parents=[common], help="show what's indexed and whether the daemon is up")
    p.set_defaults(func=cmd_status)

    return ap


def main(argv: Optional[List[str]] = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
