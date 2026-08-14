#!/usr/bin/env bash
# ./start.sh
# ./start.sh --model claude-opus-4-8
# ./start.sh --resume 06be35e0-9a81-4519-b004-800eb42ff6ff

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

[ -f "$DIR/config" ] && source "$DIR/config"
TMUX_SESSION="${TMUX_SESSION:-pentest}"

CLAUDE_CMD="claude --dangerously-skip-permissions $*"

if [ -n "${TMUX:-}" ]; then
    cd "$DIR" && exec $CLAUDE_CMD
else
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "既存セッション '$TMUX_SESSION' に接続します"
        tmux attach -t "$TMUX_SESSION"
    else
        tmux new-session -d -s "$TMUX_SESSION" -c "$DIR" "$CLAUDE_CMD"
        tmux set-option -t "$TMUX_SESSION" mouse on
        tmux set-option -t "$TMUX_SESSION" history-limit 50000
        tmux attach -t "$TMUX_SESSION"
    fi
fi
