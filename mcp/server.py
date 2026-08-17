#!/usr/bin/env python3
"""
MCP サーバ — ペンテストフレームワークのツール公開層
----------------------------------------------------
state.py と kb.py の既存 CLI を、単一の真実源のまま MCP ツールとして公開する。

Claude Code / Codex / DSH (dsh-mcp-client) 等、どのエージェントランタイムからも
同じツール群を同じ形で呼べるようにする (共通契約)。ロジックの複製はせず、
サブプロセスで既存 CLI を呼ぶ薄いラッパー。

起動 (stdio):
  python3 mcp/server.py

Claude Code 登録例 (claude mcp add):
  claude mcp add pentest -- python3 /path/to/mcp/server.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PY = os.path.join(FRAMEWORK_DIR, "scripts", "state.py")
KB_PY = os.path.join(FRAMEWORK_DIR, "kb", "kb.py")

# CLI 呼び出しに使う Python。MCP venv (mcp SDK のみ) ではなく、
# chromadb/torch 等の KB 依存が入った通常の python3 をデフォルトにする。
# 必要なら PENTEST_PYTHON=/path/to/python で上書き。
PYTHON = os.environ.get("PENTEST_PYTHON", "python3")

mcp = FastMCP("moro-agent")


def _run(cmd, timeout=60):
    """サブプロセス実行。文字列出力を返す (失敗時は stderr 込みで例外)。"""
    env = dict(os.environ)
    env.setdefault("AGENT_ID", "mcp-client")
    try:
        p = subprocess.run(cmd, cwd=FRAMEWORK_DIR, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"[error] command timed out after {timeout}s"
    if p.returncode != 0:
        return f"[error] exit {p.returncode}: {p.stderr.strip() or p.stdout.strip()}"
    return p.stdout.strip() or "(no output)"


def _state(*args):
    return _run([PYTHON, STATE_PY, *args])


@mcp.tool()
def state_show(host: str = "") -> str:
    """現在の状態 (ホスト・クレデンシャル・findings) を表示。host 指定で詳細。"""
    cmd = ["show"]
    if host:
        cmd += ["--host", host]
    return _state(*cmd)


@mcp.tool()
def state_host(ip: str, services: str = "", state: str = "", note: str = "") -> str:
    """ホストを登録/更新する。services はカンマ区切り。"""
    cmd = ["host", "--ip", ip]
    if services:
        cmd += ["--services", services]
    if state:
        cmd += ["--state", state]
    if note:
        cmd += ["--note", note]
    return _state(*cmd)


@mcp.tool()
def state_tried(host: str, method: str) -> str:
    """試行済みの攻撃手法を記録する (重複排除に使用)。"""
    return _state("tried", "--host", host, "--method", method)


@mcp.tool()
def state_cred(user: str, secret: str, source: str,
               secret_type: str = "password", domain: str = "") -> str:
    """クレデンシャルを記録する。secret_type: password/hash/key/token/cookie。"""
    cmd = ["cred", "--user", user, "--secret", secret, "--source", source,
           "--secret-type", secret_type]
    if domain:
        cmd += ["--domain", domain]
    return _state(*cmd)


@mcp.tool()
def state_finding(host: str, heading: str, narrative: str, step: int = 1,
                  commands: str = "", output: str = "", screenshot: str = "") -> str:
    """脆弱性の finding を記録する。commands は ||| 区切り。"""
    cmd = ["finding", "--host", host, "--heading", heading,
           "--narrative", narrative, "--step", str(step)]
    if commands:
        cmd += ["--commands", commands]
    if output:
        cmd += ["--output", output]
    if screenshot:
        cmd += ["--screenshot", screenshot]
    return _state(*cmd)


@mcp.tool()
def state_log(host: str, action: str, result: str, detail: str = "") -> str:
    """アクションログを追記する。result: success/fail/partial/blocked。"""
    cmd = ["log", "--host", host, "--action", action, "--result", result]
    if detail:
        cmd += ["--detail", detail]
    return _state(*cmd)


@mcp.tool()
def state_alert(host: str, alert_type: str, detail: str) -> str:
    """重要イベントを通知する。type: root/cred/pivot/flag/sensitive/dead-end/done。"""
    return _state("alert", "--host", host, "--type", alert_type, "--detail", detail)


@mcp.tool()
def state_spray(cred_id: int, dry_run: bool = False) -> str:
    """指定クレデンシャルの全ホストへの spray コマンドを出力する。"""
    cmd = ["spray", "--cred-id", str(cred_id)]
    if dry_run:
        cmd += ["--dry-run"]
    return _state(*cmd)


@mcp.tool()
def state_relay(summary: str, dead_ends: str = "", next_steps: str = "") -> str:
    """セッションのリレー (引き継ぎ) を構造化して保存する。||| 区切り。"""
    cmd = ["relay", "--summary", summary]
    if dead_ends:
        cmd += ["--dead-ends", dead_ends]
    if next_steps:
        cmd += ["--next-steps", next_steps]
    return _state(*cmd)


@mcp.tool()
def state_resume(agent: str = "") -> str:
    """前セッションの relay を読み込む。"""
    cmd = ["resume"]
    if agent:
        cmd += ["--agent", agent]
    return _state(*cmd)


@mcp.tool()
def state_event(event_type: str, host: str = "", detail: str = "") -> str:
    """任意の構造化イベントを events.jsonl に記録する (agent_start/agent_done 等)。"""
    cmd = ["event", "--type", event_type]
    if host:
        cmd += ["--host", host]
    if detail:
        cmd += ["--detail", detail]
    return _state(*cmd)


@mcp.tool()
def kb_query(query: str, tag: str = "", top: int = 5) -> str:
    """ローカル知識ベース (KB) を検索する。手法・ライトアップ検索に使用。"""
    cmd = [PYTHON, KB_PY, "query", query, "--json", "--top", str(top)]
    if tag:
        cmd += ["--tag", tag]
    out = _run(cmd, timeout=120)
    try:
        data = json.loads(out)
        hits = []
        for r in data[:top]:
            hits.append({
                "source": r.get("source", ""),
                "tag": r.get("tag", ""),
                "score": r.get("score"),
                "text": (r.get("text", "") or "")[:400],
            })
        return json.dumps(hits, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return out


if __name__ == "__main__":
    mcp.run(transport="stdio")
