#!/usr/bin/env bash
# MCP セットアップ (冪等) — venv 作成 + Claude Code へ登録
#
# start.sh から呼ばれ、ペンテストフレームワークの MCP サーバ (mcp/server.py) を
# Claude Code に登録する。MCP は Claude Code のグローバル設定 (~/.claude) に
# 書かれるため、一度登録すれば監督AI も run.sh 経由のサブエージェントも自動で
# kb_query / state_* ツールを利用できる。
#
# 冪等: 既に .venv があれば再作成しない。既に登録済みなら再登録しない。
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MCP_NAME="${MCP_NAME:-pentest}"
VENV="$DIR/mcp/.venv"
SERVER="$DIR/mcp/server.py"

# 1) MCP SDK を隔離した venv を用意 (既にあればスキップ)
if [ ! -x "$VENV/bin/python" ]; then
    echo "[mcp] .venv を作成中 (初回のみ) ..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$DIR/mcp/requirements.txt"
fi

# 2) Claude Code へ登録 (未登録なら)。kb_query が KB 依存 (chromadb/torch) を
#    使えるよう、システム python3 を PENTEST_PYTHON として渡す。
if ! claude mcp get "$MCP_NAME" >/dev/null 2>&1; then
    echo "[mcp] Claude Code へ '$MCP_NAME' を登録中 ..."
    claude mcp add "$MCP_NAME" -e "PENTEST_PYTHON=python3" -- "$VENV/bin/python" "$SERVER"
    echo "[mcp] 登録完了: $MCP_NAME"
else
    echo "[mcp] '$MCP_NAME' は登録済み (スキップ)"
fi
