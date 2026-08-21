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
# バックエンド: logprobs を返す OpenAI 互換 API。
# モデルは env で変更可:
#   VERIFIER_MODEL=qwen3.7-max           (default, top_logprobs≤5)
#   VERIFIER_MODEL=deepseek-v4-flash     (opencode-go region opt-in 要)
#   OPENAI_BASE_URL + OPENAI_API_KEY (または DEEPSEEK_API_KEY)
# ---------------------------------------------------------------------------
try:
    import llm_verifier
    _VERIFIER_AVAILABLE = True
except ImportError:
    _VERIFIER_AVAILABLE = False

VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "qwen3.7-max")
# qwen3.7-max の top_logprobs 上限は 5。モデル毎に env で調整可。
VERIFIER_TOP_LOGPROBS = int(os.environ.get("VERIFIER_TOP_LOGPROBS", "5"))

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


# ---------------------------------------------------------------------------
# 独自 verify 実装 (qwen 等 open モデル対応)
# llm-verifier の prefill trick は vLLM/SGLang 専用 extra_body を使うため
# opencode-go 等のホスト API では動かない。代わりに:
#   1. few-shot system prompt で 1文字スコアを強制
#   2. スコア文字のトークン分布 (">T", ">A" 等の融合トークン含む) から期待値を計算
# ---------------------------------------------------------------------------
_SCALE = [chr(65 + i) for i in range(20)]  # A=0(best) ... T=19(worst)


def _extract_letter_dist(entries: list, tag_key: str) -> dict[str, float]:
    """score_X タグ直後のトークン分布から A-T の文字確率を抽出。

    qwen のトークン化: '<' 'score' '_A' '>A' '</' 'score' '_A' '>'
    → '_A' の次の '>A' (融合トークン) がスコア文字。
    閉じタグ側の '_A' の次は '>' (文字なし) なので、最初に見つかった方を使う。
    """
    import math
    tag_token = f"_{tag_key}"
    result = {}
    for i, e in enumerate(entries):
        if e.token == tag_token and i + 1 < len(entries):
            next_e = entries[i + 1]
            next_tok = next_e.token
            # '>A' 形式 (融合) または単体文字
            letter = next_tok.strip().lstrip(">").strip()
            if letter in _SCALE and len(next_tok) <= 3:
                tops = next_e.top_logprobs or []
                for t in tops:
                    t_clean = t.token.strip().lstrip(">").strip()
                    if t_clean in _SCALE and len(t.token) <= 3:
                        result[t_clean] = max(result.get(t_clean, 0.0),
                                              math.exp(t.logprob))
                if result:
                    return result
    return result


def _verify_select(context: str, candidates: list[str],
                   criteria: dict[str, str]) -> str:
    """独自ペアワイズ検証: logprobs 期待値で最良候補を選ぶ。"""
    if len(candidates) < 2:
        return ("[error] verify には最低2つの candidate が必要です。")

    from openai import OpenAI as _OpenAI
    import math

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    api_key = (os.environ.get("OPENAI_API_KEY")
               or os.environ.get("DEEPSEEK_API_KEY", "EMPTY"))
    client = _OpenAI(base_url=base_url, api_key=api_key)

    # 候補ごとに、他とのペアワイズ比較でスコアを蓄積
    scores = [0.0] * len(candidates)
    rounds = 0

    for crit_name, crit_desc in criteria.items():
        for a_idx in range(len(candidates)):
            for b_idx in range(a_idx + 1, len(candidates)):
                prompt = (f"Evaluate two approaches on ONE criterion.\n\n"
                          f"**Task:** {context}\n\n"
                          f"**Approach A:** {candidates[a_idx]}\n\n"
                          f"**Approach B:** {candidates[b_idx]}\n\n"
                          f"**Criterion — {crit_name}:** {crit_desc}\n\n"
                          f"Brief analysis, then output EXACTLY:\n"
                          f"<score_A>LETTER</score_A>\n"
                          f"<score_B>LETTER</score_B>\n"
                          f"LETTER is a SINGLE character A-T (A=best, T=worst).")

                try:
                    r = client.chat.completions.create(
                        model=VERIFIER_MODEL,
                        messages=[
                            {"role": "system", "content":
                             "Strict evaluator. Score tags contain EXACTLY one letter A-T.\n"
                             "Example:\n<score_A>K</score_A>\n<score_B>F</score_B>"},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=500, logprobs=True,
                        top_logprobs=VERIFIER_TOP_LOGPROBS,
                        temperature=1.0,
                    )
                    entries = (r.choices[0].logprobs or {}).content or []
                except Exception as e:
                    import sys as _sys
                    print(f"[verify] API call failed: {type(e).__name__}: {e}",
                          file=_sys.stderr)
                    continue

                for tag_key, idx in [("A", a_idx), ("B", b_idx)]:
                    dist = _extract_letter_dist(entries, tag_key)
                    if dist:
                        total = sum(dist.values())
                        exp_val = sum(_SCALE.index(l) * p for l, p in dist.items()) / total
                        norm = 1.0 - exp_val / 19.0  # A=1.0 T=0.0
                        scores[idx] += norm
                rounds += 1

    # 平均 (各候補は criteria数 × ペア数 回評価される)
    n_pairs = len(candidates) * (len(candidates) - 1) // 2
    n_evals_per_cand = len(criteria) * (len(candidates) - 1)
    if n_evals_per_cand > 0:
        avg = [s / n_evals_per_cand for s in scores]
    else:
        avg = [0.5] * len(candidates)

    best_idx = avg.index(max(avg))
    lines = [f"verifier: {VERIFIER_MODEL}",
             f"criteria: {list(criteria.keys())}",
             f"最良: candidate #{best_idx + 1}",
             f"スコア: {[round(s, 3) for s in avg]}", ""]
    for i, (cand, score) in enumerate(zip(candidates, avg)):
        marker = " ★" if i == best_idx else ""
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
