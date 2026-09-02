#!/usr/bin/env python3
"""
dsh_tail.py — DSH エージェントのセッションをライブ表示する観察窓
------------------------------------------------------------------
DSH headless は実行中にセッションログ (session.jsonl.zstd) へリアルタイムに
書き込む。これを 2 秒間隔で展開し、新規イベントを人間可読なストリームに
整形して表示する (疑似ライブ — Claude Code の TUI に相当する観察窓)。

使い方:
  scripts/dsh_tail.py <tmp_dsh_home>   # 一時 DSH_HOME を監視
  scripts/dsh_tail.py --full <path>   # 既存セッション全量を表示 (1回)

出力例:
  ┌ step/start ─────────────────────────
  │ 思考: ... (reasoning-chunks を1行に)
  │ ツール: bash "curl -X POST ..."     (tool/call)
  │ 結果: ✓ exit 0 (150 bytes)          (tool/result)
  │ 回答: ... (text-chunks を1行に)
  └ step/end ──────────────────────────
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

POLL_INTERVAL = 2.0
MAX_STREAM_LEN = 4000  # 1回の表示で出す最大行数 (フレーム溢れ防止)


def find_session_files(tmp_home: str) -> list[str]:
    """一時 DSH_HOME 内の session.jsonl.zstd を最新順で返す。

    実際の構造: sessions/<workspace>/<session-id>/session.jsonl.zstd (2階層)
    """
    pat = os.path.join(tmp_home, "sessions", "*", "*", "*.jsonl.zstd")
    files = sorted(glob.glob(pat), key=os.path.getmtime)
    return files


def decompress(path: str) -> str:
    """zstd ファイルを展開 (失敗時は空文字)。"""
    try:
        r = subprocess.run(["zstd", "-dc", path], capture_output=True, text=True,
                           timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def format_event(d: dict) -> str | None:
    """セッションイベントを人間可読な1行に。表示不要は None。"""
    t = d.get("type", "?")
    data = d.get("data") or {}

    if t == "reasoning-chunks":
        texts = data.get("texts") or []
        s = "".join(texts) if isinstance(texts, list) else str(texts)
        return f"💭 {s.strip()[:250]}" if s.strip() else None
    if t == "text-chunks":
        texts = data.get("texts") or []
        s = "".join(texts) if isinstance(texts, list) else str(texts)
        return f"📝 {s.strip()[:250]}" if s.strip() else None
    if t == "tool-call-chunks":
        name = data.get("name", "?")
        args = data.get("args") or []
        arg_str = "".join(args) if isinstance(args, list) else str(args)
        if len(arg_str) > 200:
            arg_str = arg_str[:200] + "..."
        return f"🔧 {name} {arg_str}" if arg_str.strip() else f"🔧 {name}"
    if t == "tool/call":
        name = data.get("name", "?")
        args = data.get("arguments", "")
        arg_str = str(args)
        if len(arg_str) > 200:
            arg_str = arg_str[:200] + "..."
        return f"🔧 {name} {arg_str}" if arg_str.strip() else f"🔧 {name}"
    if t == "tool/result":
        state = data.get("state")
        text = ""
        is_error = None
        msg = data.get("message") or {}
        content = msg.get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool-result":
                inner = block.get("content") or []
                for ic in inner:
                    if isinstance(ic, dict) and ic.get("type") == "text":
                        text += ic.get("text", "")
                    if isinstance(ic, dict) and "isError" in ic:
                        is_error = ic.get("isError")
        if not text:
            # fallback: 直接テキストフィールドを探す
            for key in ("stdout", "stderr", "error"):
                v = data.get(key)
                if v:
                    text = str(v)
                    break
        if len(text) > 150:
            text = text[:150] + "..."
        mark = "✗" if is_error else "✓"
        return f"{mark} ツール結果: {text.strip()}" if text.strip() else f"{mark} ツール結果"
    if t == "step/start":
        agent = data.get("agent", "")
        return f"── ▶ step/start{(' (' + agent + ')') if agent else ''} ──────────────"
    if t == "step/end":
        dur = data.get("durationMs")
        dur_s = f" {dur/1000:.1f}s" if isinstance(dur, (int, float)) else ""
        return f"── ◀ step/end{dur_s} ──────────────────"
    if t == "assistant/message":
        content = data.get("content", "")
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            s = " ".join(texts)
        else:
            s = str(content)
        return f"🧠 {s.strip()[:250]}" if s.strip() else None
    if t == "user/message":
        content = data.get("content", "")
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            s = " ".join(texts)
        else:
            s = str(content)
        return f"👤 {s.strip()[:250]}" if s.strip() else None
    if t == "session/title":
        return f"📋 タイトル: {data.get('title', '?')}"

    return None  # 他のイベント (permission/sandbox等) は表示しない


def live(tmp_home: str):
    last_lines = 0
    last_mtime = 0.0
    print(f"[dsh_tail] 監視中: {tmp_home} (Ctrl+C で終了)")
    try:
        while True:
            files = find_session_files(tmp_home)
            if not files:
                time.sleep(0.5)
                continue
            newest = files[-1]
            mt = os.path.getmtime(newest)
            text = decompress(newest)
            lines = text.splitlines()
            if len(lines) > last_lines:
                # 差分だけ表示
                for line in lines[last_lines:]:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    out = format_event(d)
                    if out:
                        print(out, flush=True)
                last_lines = len(lines)
            elif mt != last_mtime:
                # ファイルが差し替わった (新しいセッション) 場合は最初から
                last_lines = 0
                text = decompress(newest)
                lines = text.splitlines()
                for line in lines:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    out = format_event(d)
                    if out:
                        print(out, flush=True)
                last_lines = len(lines)
            last_mtime = mt
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n[dsh_tail] 終了")


def full(path: str):
    text = decompress(path)
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        out = format_event(d)
        if out:
            print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="一時 DSH_HOME か session.jsonl.zstd のパス")
    ap.add_argument("--full", action="store_true", help="全量を1回表示して終了")
    args = ap.parse_args()

    if args.full:
        if args.path.endswith(".zstd"):
            full(args.path)
        else:
            files = find_session_files(args.path)
            if not files:
                print("[dsh_tail] セッションファイルが見つかりません", file=sys.stderr)
                sys.exit(1)
            full(files[-1])
    else:
        live(args.path)


if __name__ == "__main__":
    main()