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
R2_RECON_PY = os.path.join(FRAMEWORK_DIR, "tools", "re", "r2_recon.py")

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


# ---------------------------------------------------------------------------
# RE ツール (radare2 ベース。Linux/Windows 共に r2 があれば動く)
# ---------------------------------------------------------------------------
@mcp.tool()
def re_checksec(binary: str) -> str:
    """バイナリの保護機構 (NX/PIE/Canary/RELRO) を判定する。"""
    return _run([PYTHON, R2_RECON_PY, "info", binary])


@mcp.tool()
def re_funcs(binary: str) -> str:
    """バイナリの関数一覧を取得する (radare2 afl)。"""
    return _run([PYTHON, R2_RECON_PY, "funcs", binary], timeout=180)


@mcp.tool()
def re_strings(binary: str) -> str:
    """バイナリ内の文字列を列挙する (radare2 izz)。"""
    return _run([PYTHON, R2_RECON_PY, "strings", binary], timeout=180)


@mcp.tool()
def re_imports(binary: str) -> str:
    """バイナリのインポート (PLT/GOT 解決) を取得する。"""
    return _run([PYTHON, R2_RECON_PY, "imports", binary], timeout=180)


@mcp.tool()
def re_disasm(binary: str, func: str) -> str:
    """指定関数を逆アセンブルする (radare2 pdf)。"""
    return _run([PYTHON, R2_RECON_PY, "disasm", binary, func], timeout=180)


@mcp.tool()
def re_xrefs(binary: str, addr: str) -> str:
    """指定アドレスへの参照元/参照先 (xref) を取得する。"""
    return _run([PYTHON, R2_RECON_PY, "xrefs", binary, addr], timeout=180)


# ---------------------------------------------------------------------------
# 検証ツール (llm-as-a-verifier) — 回顧から抽出した criteria で審査
# ---------------------------------------------------------------------------
# バックエンド: logprobs を返す OpenAI 互換 API が必要。
#   OPENAI_BASE_URL + OPENAI_API_KEY (または DEEPSEEK_API_KEY) で解決。
#   opencode-go を使う場合:
#     OPENAI_BASE_URL=https://opencode.ai/zen/go/v1
#     OPENAI_API_KEY=$OPENCODE_GO_API_KEY
# ---------------------------------------------------------------------------
try:
    import llm_verifier
    _VERIFIER_AVAILABLE = True
except ImportError:
    _VERIFIER_AVAILABLE = False

# 回顧 (HTB Hard 失敗) から抽出した審査基準
_NEGATIVE_CRITERIA = {
    "監視点到達": ("実験が対象の実際の監視点（正しいトピック/エンドポイント/入力経路）に"
                   "届いたかを疑っているか。新規テスト用でなく対象が実際に読む既存の監視点に"
                   "plant したか。"),
    "挑発能力": ("検出装置が対象行動を誘発できるか。素朴なHTTPリスナーではNTLM認証等は"
                "誘発できない。responder/ntlmrelayx等の認証挑発能力を問うているか。"),
    "陰性の再解釈": ("陰性結果を「対象行動が存在しない」でなく「検出装置が誘発・観測できなかった」"
                    "として再解釈しているか。"),
}

_JUDGMENT_CRITERIA = {
    "全攻撃原理の列挙": ("隠れたfetcher/callback発見時、狭い問いでなく「取得してくる主体への"
                        "全攻撃原理（NTLM挑発/リレー/クロスプロトコル）」を列挙しているか。"),
    "推測より窃取・リレー": ("credを推測（スプレー）でなく窃取（実値回収）や"
                            "リレー（ntlmrelayx/certipy）で得る判断を優先しているか。"),
    "可逆改変の活用": ("元データ保存済み+復元手順がある場合、1レコード単位の上書きを"
                      "宣言制で許可する柔軟性があるか。過剰な自粛で勝ち筋を封じていないか。"),
}


def _verify_select(context: str, candidates: list[str],
                   criteria: dict[str, str]) -> str:
    """llm_verifier.select を呼び、結果を整形して返す。"""
    if not _VERIFIER_AVAILABLE:
        return ("[error] llm-verifier がインストールされていません。"
                "pip install llm-verifier を実行してください。")
    result = llm_verifier.select(
        problem=context,
        candidates=candidates,
        criteria=criteria,
    )
    lines = [f"最良: candidate #{result.index + 1}",
             f"スコア: {[round(s, 3) for s in result.scores]}", ""]
    for i, (cand, score) in enumerate(zip(candidates, result.scores)):
        marker = " ★" if i == result.index else ""
        lines.append(f"--- Candidate #{i+1} (score={score:.3f}){marker} ---")
        lines.append(cand)
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def verify_negative(context: str, candidate_a: str, candidate_b: str = "") -> str:
    """陰性結果（「否定された」報告）を審査する (偽陰性の検出)。

    高価値仮説が否定された時、実験設計自体の欠陥を見抜いているかを
    回顧から抽出した3基準（監視点到達/挑発能力/陰性の再解釈）で採点する。

    Args:
        context: 何がどう否定されたかの説明（実験内容と結果）
        candidate_a: 判断案A（例: 陰性をそのまま受理する提案）
        candidate_b: 判断案B（例: 実験設計を疑って再実験する提案）
    """
    candidates = [candidate_a] + ([candidate_b] if candidate_b else [])
    return _verify_select(context, candidates, _NEGATIVE_CRITERIA)


@mcp.tool()
def verify_judgment(context: str, candidate_a: str, candidate_b: str = "",
                    candidate_c: str = "") -> str:
    """監督の重要判断を審査する (戦略的意思決定の品質評価)。

    タスク割当/損切り/dead-end受理/relay判断等の意思決定を、
    3基準（全攻撃原理の列挙/推測より窃取・リレー/可逆改変の活用）で採点する。

    Args:
        context: 判断の背景（現在の状況、何を判断しようとしているか）
        candidate_a: 判断案A
        candidate_b: 判断案B
        candidate_c: 判断案C
    """
    candidates = ([candidate_a] + ([candidate_b] if candidate_b else [])
                  + ([candidate_c] if candidate_c else []))
    return _verify_select(context, candidates, _JUDGMENT_CRITERIA)


if __name__ == "__main__":
    mcp.run(transport="stdio")
