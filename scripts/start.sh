#!/usr/bin/env bash
# ./start.sh
# ./start.sh --model glm-5.2
# ./start.sh --resume <session-id>
# ./start.sh --bb                          # バグバウンティモード (CLAUDE-bb.md を使用)
# ./start.sh --bb --model glm-5.2

set -eo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$DIR/config" ] && source "$DIR/config"
[ -f "$DIR/.env" ] && { set -a; source "$DIR/.env"; set +a; }
TMUX_SESSION="${TMUX_SESSION:-pentest}"

# MCP セットアップ (冪等): venv 作成 + Claude Code へ kb_query/state_* ツールを登録。
# 一度登録すれば監督AI も run.sh 経由のサブエージェントも自動でツール利用可能。
[ -x "$DIR/scripts/mcp_setup.sh" ] && bash "$DIR/scripts/mcp_setup.sh"

# バグバウンティモード: 監督AIの指示を CLAUDE-bb.md に切り替える。
# CLAUDE.md は常にペンテスト版 (正) を保ち、--bb では「安全なスワップ + 終了時復元」
# を行う。tmux kill-session / クラッシュ等で復元できなかった場合も、次回起動時に
# 自己復旧 (CLAUDE.md.pentest が残っていれば復元) する。
BB_MARKER="$DIR/.bb-active"
if echo "$*" | grep -q -- '--bb'; then
    if [ -f "$DIR/CLAUDE-bb.md" ] && ! cmp -s "$DIR/CLAUDE.md" "$DIR/CLAUDE-bb.md"; then
        cp "$DIR/CLAUDE.md" "$DIR/CLAUDE.md.pentest"   # 退避: 正のペンテスト版
        cp "$DIR/CLAUDE-bb.md" "$DIR/CLAUDE.md"        # bb版を有効化
        touch "$BB_MARKER"
        restore_bb() { mv "$DIR/CLAUDE.md.pentest" "$DIR/CLAUDE.md" 2>/dev/null; rm -f "$BB_MARKER"; }
        trap restore_bb EXIT INT TERM
    fi
    # --bb を引数から除去
    set -- $(echo "$*" | sed 's/--bb//')
fi

# 自己復旧: 非 --bb 起動時に前回の --bb の残骸 (ペンテスト退避) が残っていれば復元
if [ ! -f "$BB_MARKER" ] && [ -f "$DIR/CLAUDE.md.pentest" ]; then
    mv "$DIR/CLAUDE.md.pentest" "$DIR/CLAUDE.md"
    echo "[start] 前回の --bb スワップ残骸を自己復旧しました (CLAUDE.md を復元)"
fi

# モデル名を引数から取得
MODEL_NAME=""
for i in $(seq 1 $#); do
    arg="${!i}"
    if [ "$arg" = "--model" ]; then
        next=$((i+1))
        MODEL_NAME="${!next:-}"
        break
    fi
done

# モデルに応じた環境変数を設定 (現在シェルへ export。argv に載せない = 漏洩対策)
if [ -n "$MODEL_NAME" ] && [ -f "$DIR/models.json" ]; then
    [ -f "$DIR/scripts/env_export.py" ] && eval "$(python3 "$DIR/scripts/env_export.py" "$MODEL_NAME" 2>/dev/null)"
fi

CLAUDE_CMD="claude --dangerously-skip-permissions $*"

if [ -n "${TMUX:-}" ]; then
    cd "$DIR" && exec $CLAUDE_CMD
else
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "既存セッション '$TMUX_SESSION' に接続します"
        tmux attach -t "$TMUX_SESSION"
    else
        tmux new-session -d -s "$TMUX_SESSION" -c "$DIR" "set -a; source $DIR/.env 2>/dev/null; set +a; eval \"\$(python3 $DIR/scripts/env_export.py $MODEL_NAME 2>/dev/null)\"; $CLAUDE_CMD"
        tmux set-option -t "$TMUX_SESSION" mouse on
        tmux set-option -t "$TMUX_SESSION" history-limit 50000
        tmux attach -t "$TMUX_SESSION"
    fi
fi
