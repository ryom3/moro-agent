#!/usr/bin/env bash
# ./start.sh
# ./start.sh --model glm-5.2
# ./start.sh --resume <session-id>

set -eo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$DIR/config" ] && source "$DIR/config"
[ -f "$DIR/.env" ] && { set -a; source "$DIR/.env"; set +a; }
TMUX_SESSION="${TMUX_SESSION:-pentest}"

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

# モデルに応じた環境変数を設定
ENV_PREFIX=""
if [ -n "$MODEL_NAME" ] && [ -f "$DIR/models.json" ]; then
    # models.json から env ブロックを取得して bash で展開
    ENV_PAIRS=$(python3 << PYEOF
import json, os
with open("$DIR/models.json") as f:
    cfg = json.load(f).get("$MODEL_NAME", {})
for k, v in cfg.get("env", {}).items():
    if v.startswith("\$"):
        v = os.environ.get(v[1:], "")
    print(f"{k}={v}")
PYEOF
    )
    while IFS= read -r line; do
        [ -n "$line" ] && ENV_PREFIX="$ENV_PREFIX $line"
    done <<< "$ENV_PAIRS"
fi

CLAUDE_CMD="${ENV_PREFIX} claude --dangerously-skip-permissions $*"

if [ -n "${TMUX:-}" ]; then
    cd "$DIR" && eval exec $CLAUDE_CMD
else
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "既存セッション '$TMUX_SESSION' に接続します"
        tmux attach -t "$TMUX_SESSION"
    else
        tmux new-session -d -s "$TMUX_SESSION" -c "$DIR" "source $DIR/.env 2>/dev/null; eval $CLAUDE_CMD"
        tmux set-option -t "$TMUX_SESSION" mouse on
        tmux set-option -t "$TMUX_SESSION" history-limit 50000
        tmux attach -t "$TMUX_SESSION"
    fi
fi
