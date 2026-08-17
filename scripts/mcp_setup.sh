#!/usr/bin/env bash
# MCP セットアップ (冪等) — MCP 設定をフレームワーク内に一元化
#
# start.sh から呼ばれ、ペンテストフレームワークの MCP サーバ (mcp/server.py) を
# Claude Code と Codex (OpenAI) の両方に登録する。
#
# 一元化ポリシー:
#   - Claude Code → リポジトリ内 `.mcp.json` (project スコープ) を直接生成
#   - Codex        → `.codex-profiles/config.toml` (CODEX_HOME 内)
#   サーバ定義はフレームワーク内の 1 箇所に閉じる。~/.claude.json に定義を散らばせない
#   (旧 local 登録は自動削除)。絶対パスは現在地に合わせて都度生成するため移動にも追従。
#
# 冪等: .venv は再作成しない。サーバ定義が現状と一致していれば再生成しない。

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

# 2) Claude Code → .mcp.json (リポジトリ内・project スコープ) を生成
#    絶対パスは現在のフレームワーク位置に合わせる。パスが変わっていれば再生成。
MCP_JSON="$DIR/.mcp.json"
if [ ! -f "$MCP_JSON" ] || ! grep -qF "$SERVER" "$MCP_JSON"; then
    echo "[mcp] Claude Code: .mcp.json を生成/更新 ..."
    PY="$PY" SERVER="$SERVER" python3 - <<'PYS' > "$MCP_JSON"
import json, os
print(json.dumps({"mcpServers": {"pentest": {
    "type": "stdio", "command": os.environ["PY"],
    "args": [os.environ["SERVER"]],
    "env": {"PENTEST_PYTHON": "python3"}}}}, indent=2))
PYS
else
    echo "[mcp] Claude Code: .mcp.json 更新不要 (スキップ)"
fi

# 旧 local スコープ登録があれば削除 (~/.claude.json への散らばりを解消)
if claude mcp get "$MCP_NAME" 2>/dev/null | grep -q "Local config"; then
    echo "[mcp] Claude Code: 旧 local 登録を削除 ..."
    claude mcp remove "$MCP_NAME" -s local 2>/dev/null || true
fi

# 3) Codex → .codex-profiles (CODEX_HOME 内に閉じる)
CODEX_HOME_DIR="$DIR/.codex-profiles"
mkdir -p "$CODEX_HOME_DIR"
if CODEX_HOME="$CODEX_HOME_DIR" codex mcp get "$MCP_NAME" 2>&1 | grep -q "No MCP server named"; then
    echo "[mcp] Codex へ '$MCP_NAME' を登録中 ..."
    CODEX_HOME="$CODEX_HOME_DIR" codex mcp add "$MCP_NAME" --env "PENTEST_PYTHON=python3" -- "$PY" "$SERVER"
    echo "[mcp] Codex: 登録完了"
else
    echo "[mcp] Codex: '$MCP_NAME' 登録済み (スキップ)"
fi
