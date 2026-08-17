#!/usr/bin/env python3
"""
トークン使用量の集計と可視化

使い方:
  python3 tools/tokens.py                  # 集計表示
  python3 tools/tokens.py --html report    # HTML レポート生成
"""
import os, re, json, argparse
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"

def fmt_time(seconds):
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h{m:02d}m{s:02d}s"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m{s:02d}s"


def parse_log(filepath):
    """script ログから Claude Code のトークン使用量を抽出"""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        # ANSI エスケープコードを除去
        clean = re.sub(rb'\x1b\[[0-9;]*[a-zA-Z]', b'', raw)
        content = clean.decode('utf-8', errors='replace')
    except:
        return None

    # トークンパターン:
    #   ↓ 4.6k tokens / ↓4.6k tokens / ↓ 1225 tokens / ↓1225 tokens
    total_tokens = 0

    # k 付き: ↓ 4.6k tokens
    for m in re.finditer(r'[↓↑]\s*([\d.]+)\s*k\s*tokens', content):
        total_tokens += int(float(m.group(1)) * 1000)

    # k なし: ↓ 1225 tokens (k 付きと重複しないよう負の先読み)
    for m in re.finditer(r'[↓↑]\s*(\d+)\s*tokens(?!\s*k)', content):
        val = int(m.group(1))
        if val > 10:  # 小さすぎる値はノイズ
            total_tokens += val

    # 経過時間: ファイル名の日時 (run.sh が付与) → mtime の差
    total_seconds = 0
    try:
        ts_match = re.search(r'(\d{8}_\d{6})', filepath.name)
        if ts_match:
            start = datetime.strptime(ts_match.group(1), '%Y%m%d_%H%M%S')
            end = datetime.fromtimestamp(filepath.stat().st_mtime)
            total_seconds = max(0, int((end - start).total_seconds()))
    except:
        pass

    interactions = len(re.findall(r'[↓↑]\s*[\d.]+\s*k?\s*tokens', content))

    # エージェント名をファイル名から
    name = filepath.stem
    agent_id = name.rsplit('_', 2)[0] if name.count('_') >= 2 else name

    return {
        "agent_id": agent_id,
        "file": filepath.name,
        "tokens": total_tokens,
        "seconds": total_seconds,
        "interactions": interactions,
    }

def gather():
    if not LOGS_DIR.exists():
        return []
    results = []
    for f in sorted(LOGS_DIR.glob("*.log")):
        parsed = parse_log(f)
        if parsed and parsed["tokens"] > 0:
            results.append(parsed)
    return results

def show(results):
    if not results:
        print("ログなし、またはトークン情報が見つかりません")
        return

    print(f"\n{'='*65}")
    print(f"{'Agent':<25} {'Tokens':>10} {'Time':>10} {'Calls':>8}")
    print(f"{'-'*65}")

    by_agent = {}
    for r in results:
        aid = r["agent_id"]
        by_agent.setdefault(aid, {"tokens": 0, "seconds": 0, "calls": 0})
        by_agent[aid]["tokens"] += r["tokens"]
        by_agent[aid]["seconds"] += r["seconds"]
        by_agent[aid]["calls"] += r["interactions"]

    total_tokens = sum(d["tokens"] for d in by_agent.values())
    total_seconds = sum(d["seconds"] for d in by_agent.values())

    for aid, d in sorted(by_agent.items()):
        print(f"{aid:<25} {d['tokens']:>10,} {fmt_time(d['seconds']):>12} {d['calls']:>8}")

    print(f"{'-'*65}")
    print(f"{'TOTAL':<25} {total_tokens:>10,} {fmt_time(total_seconds):>12} {sum(d['calls'] for d in by_agent.values()):>8}")
    print(f"{'='*65}")

    # 全体の経過時間 (最初の開始 → 最後の終了)
    import re as _re
    starts, ends = [], []
    for r in results:
        ts = _re.search(r'(\d{8}_\d{6})', r["file"])
        if ts:
            starts.append(datetime.strptime(ts.group(1), '%Y%m%d_%H%M%S'))
        logpath = LOGS_DIR / r["file"]
        if logpath.exists():
            ends.append(datetime.fromtimestamp(logpath.stat().st_mtime))
    if starts and ends:
        overall = int((max(ends) - min(starts)).total_seconds())
        print(f"\nElapsed (wall clock): {fmt_time(overall)}")

    max_tokens = max(d["tokens"] for d in by_agent.values()) if by_agent else 1
    print(f"\nTokens per agent:")
    for aid, d in sorted(by_agent.items(), key=lambda x: x[1]["tokens"], reverse=True):
        bar_len = int(40 * d["tokens"] / max_tokens)
        bar = "█" * bar_len
        print(f"  {aid:<20} {bar} {d['tokens']:,}")

def generate_html(results, output):
    by_agent = {}
    for r in results:
        aid = r["agent_id"]
        by_agent.setdefault(aid, {"tokens": 0, "seconds": 0, "calls": 0})
        by_agent[aid]["tokens"] += r["tokens"]
        by_agent[aid]["seconds"] += r["seconds"]
        by_agent[aid]["calls"] += r["interactions"]

    agents = sorted(by_agent.keys())
    tokens = [by_agent[a]["tokens"] for a in agents]
    total = sum(tokens)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Token Usage</title>
<style>
  body {{ font-family: sans-serif; max-width: 800px; margin: 40px auto; background: #1a1a2e; color: #e0e0e0; }}
  h1 {{ color: #00d4ff; }}
  .bar-container {{ margin: 8px 0; }}
  .bar-label {{ display: inline-block; width: 200px; }}
  .bar {{ display: inline-block; height: 24px; background: linear-gradient(90deg, #00d4ff, #7b2ff7); border-radius: 4px; }}
  .bar-value {{ display: inline-block; margin-left: 8px; }}
  .total {{ font-size: 2em; color: #00d4ff; margin: 20px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
  th {{ color: #00d4ff; }}
</style></head><body>
<h1>Token Usage Report</h1>
<p>{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<div class="total">Total: {total:,} tokens</div>
<table>
<tr><th>Agent</th><th>Tokens</th><th>Time</th><th>Interactions</th></tr>
"""
    for a in agents:
        d = by_agent[a]
        html += f"<tr><td>{a}</td><td>{d['tokens']:,}</td><td>{fmt_time(d['seconds'])}</td><td>{d['calls']}</td></tr>\n"

    html += "</table>\n"
    max_t = max(tokens) if tokens else 1
    for a in agents:
        t = by_agent[a]["tokens"]
        w = int(400 * t / max_t) if max_t > 0 else 0
        html += f'<div class="bar-container"><span class="bar-label">{a}</span><span class="bar" style="width:{w}px"></span><span class="bar-value">{t:,}</span></div>\n'
    html += "</body></html>"

    outpath = f"{output}.html"
    with open(outpath, "w") as f:
        f.write(html)
    print(f"Generated: {outpath}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=str, help="HTML レポート出力")
    args = parser.parse_args()
    results = gather()
    if args.html:
        generate_html(results, args.html)
    else:
        show(results)

if __name__ == "__main__":
    main()
