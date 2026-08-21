#!/usr/bin/env bash
# CyberGym エージェントコンテナのエントリポイント
# 引数はそのまま cybergym_run.py へ渡す。
set -euo pipefail

# run.sh が tmux セッション "pentest" を要求するため、
# コンテナ内で tmux サーバをデーモン的に立ち上げておく
if ! tmux has-session -t "${TMUX_SESSION:-pentest}" 2>/dev/null; then
    tmux new-session -d -s "${TMUX_SESSION:-pentest}" "sleep infinity"
    echo "[entrypoint] tmux セッション ${TMUX_SESSION:-pentest} を起動"
fi

exec python3 /opt/moro-agent/scripts/cybergym_run.py \
    --framework-dir /opt/moro-agent \
    "$@"
