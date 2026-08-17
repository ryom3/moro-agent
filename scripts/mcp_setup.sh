#!/usr/bin/env bash
# MCP セットアップ (冪等) — venv 作成 + Claude Code / Codex へ登録
#
# start.sh から呼ばれ、ペンテストフレームワークの MCP サーバ (mcp/server.py) を
# Claude Code と Codex (OpenAI) の両方に登録する。登録後、監督AI も run.sh 経由の
# サブエージェント (claude / codex) も自動で kb_query / state_* ツールを利用できる。
#
# 冪等: .venv は既にあれば再作成しない。各エージェントは登録済みなら再登録しない。

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MCP_NAME="${MCP_NAME:-pentest}"
VENV="$DIR/mcp/.venv"
SERVER="$DIR/mcp/server.py"
PY="$VENV/bin/python"

# 1) MCP SDK を隔離した venv を用意 (既にあればスキップ)
if [ ! -x "$PY" ]; then
    echo "[mcp] .venv を作成中 (初回のみ) ..."
    python3 -m venv "$VENV"
    "$PY" -m pip install --quiet --disable-pip-version-check -r "$DIR/mcp/requirements.txt"
fi

# kb_query は KB 依存 (chromadb/torch) を使うため、システム python3 を渡す
PENTEST_PYTHON_ENV="PENTEST_PYTHON=python3"

# 2) Claude Code へ登録 (未登録なら)
if ! claude mcp get "$MCP_NAME" >/dev/null 2>&1; then
    echo "[mcp] Claude Code へ '$MCP_NAME' を登録中 ..."
    claude mcp add "$MCP_NAME" -e "$PENTEST_PYTHON_ENV" -- "$PY" "$SERVER"
    echo "[mcp] Claude Code: 登録完了"
else
    echo "[mcp] Claude Code: '$MCP_NAME' 登録済み (スキップ)"
fi

# 3) Codex へ登録 (未登録なら)。Codex は exit code でなく出力で判定する。
CODEX_HOME_DIR="$DIR/.codex-profiles"
mkdir -p "$CODEX_HOME_DIR"
if CODEX_HOME="$CODEX_HOME_DIR" codex mcp get "$MCP_NAME" 2>&1 | grep -q "No MCP server named"; then
    echo "[mcp] Codex へ '$MCP_NAME' を登録中 ..."
    CODEX_HOME="$CODEX_HOME_DIR" codex mcp add "$MCP_NAME" --env "$PENTEST_PYTHON_ENV" -- "$PY" "$SERVER"
    echo "[mcp] Codex: 登録完了"
else
    echo "[mcp] Codex: '$MCP_NAME' 登録済み (スキップ)"
fi
