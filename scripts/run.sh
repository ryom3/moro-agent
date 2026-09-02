#!/usr/bin/env bash
# ./scripts/run.sh claude-sonnet-4-6 "偵察して"
# ./scripts/run.sh recon-1 claude-haiku-4-5 "偵察して"
# ./scripts/run.sh --monitor

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$DIR/config" ] && source "$DIR/config"
LAYOUT="${LAYOUT:-tab}"
TMUX_SESSION="${TMUX_SESSION:-pentest}"
MONITOR_INTERVAL="${MONITOR_INTERVAL:-5}"

[ -f "$DIR/.env" ] && { set -a; source "$DIR/.env"; set +a; }

# --- モニタリング ---
if [ "${1:-}" = "--monitor" ]; then
    if [ -z "${TMUX:-}" ]; then echo "tmux の中で実行してください"; exit 1; fi
    tmux new-window -n monitor "cd $DIR && watch -n$MONITOR_INTERVAL 'python3 scripts/state.py show 2>/dev/null; echo; echo \"=== Recent ===\"; tail -15 state/log.jsonl 2>/dev/null'"
    exit 0
fi

# --- 観察モード (実行と観察の分離) ---
# エージェントを実行する tmux ペインに入らず、ログ/イベントを tail -f するだけの「窓」。
# TUI エージェントの実行コンテナとは独立に観察できる (巻き戻し・複数人での同時閲覧も可)。
if [ "${1:-}" = "--tail" ]; then
    if [ -z "${TMUX:-}" ]; then echo "tmux の中で実行してください"; exit 1; fi
    if [ -n "${2:-}" ]; then
        LOG=$(ls -t "$DIR/logs/${2}_"*.log 2>/dev/null | head -1)
    else
        LOG=$(ls -t "$DIR/logs/"*.log 2>/dev/null | head -1)
    fi
    AGENT="${2:-$(basename "${LOG:-none}" | cut -d_ -f1)}"
    if [ -z "$LOG" ]; then echo "ログが見つかりません"; exit 1; fi
    tmux new-window -n "view-${AGENT}" "cd $DIR && tail -n +1 -f '$LOG'"
    echo "[view] ${AGENT} → tab:view-${AGENT} (log: $LOG)"
    exit 0
fi

if [ "${1:-}" = "--events" ]; then
    if [ -z "${TMUX:-}" ]; then echo "tmux の中で実行してください"; exit 1; fi
    tmux new-window -n "events" "cd $DIR && tail -n +1 -f state/events.jsonl 2>/dev/null"
    echo "[view] structured events stream (state/events.jsonl)"
    exit 0
fi

# --- 引数パース ---
KNOWN_MODELS=$(python3 "$DIR/scripts/runner.py" models)

if echo " $KNOWN_MODELS " | grep -q " ${1:-} "; then
    MODEL="$1"; shift; AGENT_ID="agent-$$"
else
    AGENT_ID="${1:?Usage: run.sh [AGENT_ID] MODEL [PROMPT]}"; shift
    MODEL="${1:?Usage: run.sh [AGENT_ID] MODEL [PROMPT]}"; shift
fi
PROMPT="${*:-}"

# --- models.json の env ブロックを環境へ export (argv に載せない = 漏洩対策) ---
# 子プロセス (codex/claude/aider) は環境経由でシークレットを継承する。
# `set -a; source .env` により .env の値は既に export 済みなので、
# ここでは $VAR 形式のリマップ (例: ANTHROPIC_AUTH_TOKEN=$GLM_API_KEY) のみ解決する。
[ -f "$DIR/scripts/env_export.py" ] && eval "$(python3 "$DIR/scripts/env_export.py" "$MODEL" 2>/dev/null)"

# ウィンドウ名を一意にする (重複回避)
WIN_NAME="${AGENT_ID}"
if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -q "^${WIN_NAME}$"; then
    WIN_NAME="${AGENT_ID}-$(date +%s)"
fi

# --- models.json → 起動コマンド生成 (ランタイムレジストリへ委譲) ---
export FRAMEWORK_DIR="$DIR"
PROMPT_MODE=$(python3 "$DIR/scripts/runner.py" prompt-mode "$MODEL")
LAUNCH_CMD=$(python3 "$DIR/scripts/runner.py" command "$MODEL" "$AGENT_ID" "$PROMPT")

# --- tmux 内チェック ---
if [ -z "${TMUX:-}" ]; then
    echo "tmux の中で実行してください (./start.sh で起動)"
    exit 1
fi

# --- モデル env を tmux サーバへ明示的に反映する (引用符を使わない確実な方法) ---
# tmux new-window は update-environment (DISPLAY/SSH_* のみ) しか素通ししないため、
# export 済みのモデル関連変数を tmux set-environment で tmux サーバ環境に流し、
# 直後の new-window の子シェルへ確実に渡す。コマンド文字列に env を埋め込まない。
# 重要: 現在のシェルで「空」の変数は tmux 環境から「削除」する (-g u)。
# 残しておくと以前の GLM 起動の ANTHROPIC_BASE_URL が pollution として
# 次の opus 等の公式直結エージェントへ誤ルートさせる (実障害があった)。
for _v in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY \
          CLAUDE_CODE_MAX_CONTEXT_TOKENS CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT \
          API_TIMEOUT_MS SAKANA_API_KEY GLM_API_KEY GLM_BASE_URL; do
    if [ -n "${!_v:-}" ]; then
        tmux set-environment -g "$_v" "${!_v}" 2>/dev/null || true
    else
        tmux set-environment -gu "$_v" 2>/dev/null || true
    fi
done

# --- ログ ---
mkdir -p "$DIR/logs"
LOGFILE="$DIR/logs/${AGENT_ID}_$(date +%Y%m%d_%H%M%S).log"
# 後片付け (DSH の一時 DSH_HOME 削除) は実行時評価が必要なため別変数に組み、
# LAUNCH_CMD と連結して script -c に渡す。$(...) はここでは展開しない。
# (旧方式の settings.yaml 復元は廃止 — DSH_HOME 分離で本体は無傷のため削除のみ)
RESTORE_CMD="if [ -f '$DIR/.dsh-model-swap' ]; then rm -rf \"\$(cat '$DIR/.dsh-model-swap')\" && rm -f '$DIR/.dsh-model-swap'; fi"
LOGGED_CMD="script -q -f \"$LOGFILE\" -c \"$LAUNCH_CMD; echo '[AGENT EXITED] Press enter to close'; python3 '$DIR/scripts/state.py' event --type agent_done --field runtime_done=1 2>/dev/null; $RESTORE_CMD; read\""

# ライフサイクルイベントを events.jsonl に記録 (共通契約)
python3 "$DIR/scripts/state.py" event --type agent_start --host "" --detail "$MODEL" \
        --field model="$MODEL" --field runtime="$(python3 "$DIR/scripts/runner.py" resolve "$MODEL")" 2>/dev/null

# --- ウィンドウ作成 (常に tab — フルスクリーンで UI が崩れない) ---
tmux new-window -n "$WIN_NAME" "$LOGGED_CMD"

# --- プロンプト送信 (tui のみ。argv/stdin は既に起動コマンド/標準入力に含む) ---
if [ -n "$PROMPT" ] && [ "$PROMPT_MODE" = "tui" ]; then
    # Claude Code の起動を待つ
    for i in $(seq 1 20); do
        sleep 1
        if tmux capture-pane -t "$WIN_NAME" -p 2>/dev/null | grep -q -E 'bypass permissions|❯|>'; then
            sleep 2  # UI が完全に描画されるまで追加待ち
            break
        fi
    done
    # send-keys だと長いプロンプトや特殊文字で失敗するので
    # ファイル経由で paste する
    PROMPT_FILE=$(mktemp)
    echo "$PROMPT" > "$PROMPT_FILE"
    tmux load-buffer "$PROMPT_FILE"
    tmux paste-buffer -t "$WIN_NAME"
    sleep 1
    tmux send-keys -t "$WIN_NAME" Enter
    rm -f "$PROMPT_FILE"
fi

echo "[$AGENT_ID] $MODEL → tab:$WIN_NAME (log: $LOGFILE)"
echo "Ctrl+b n/p でタブ切替"
