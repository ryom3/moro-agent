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

# --- 引数パース ---
KNOWN_MODELS=$(python3 -c "import json; print(' '.join(json.load(open('$DIR/models.json')).keys()))")

if echo " $KNOWN_MODELS " | grep -q " ${1:-} "; then
    MODEL="$1"; shift; AGENT_ID="agent-$$"
else
    AGENT_ID="${1:?Usage: run.sh [AGENT_ID] MODEL [PROMPT]}"; shift
    MODEL="${1:?Usage: run.sh [AGENT_ID] MODEL [PROMPT]}"; shift
fi
PROMPT="${*:-}"

# ウィンドウ名を一意にする (重複回避)
WIN_NAME="${AGENT_ID}"
if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -q "^${WIN_NAME}$"; then
    WIN_NAME="${AGENT_ID}-$(date +%s)"
fi

# --- models.json → 起動コマンド生成 ---
export FRAMEWORK_DIR="$DIR"
LAUNCH_CMD=$(python3 -c "
import json, os, sys

with open('$DIR/models.json') as f:
    cfg = json.load(f).get('$MODEL')
if not cfg:
    print('Unknown model: $MODEL', file=sys.stderr)
    sys.exit(1)

runtime = cfg['runtime']
parts = ['cd $DIR &&', 'AGENT_ID=$AGENT_ID']

if runtime == 'claude-code':
    for k, v in cfg.get('env', {}).items():
        if v.startswith(chr(36)):
            v = os.environ.get(v[1:], '')
        parts.append(f'{k}={v}')
    model_name = cfg.get('model_override', '$MODEL')
    effort = os.environ.get('EFFORT', 'high')
    # OOM 対策 (2026-08-16): バンドル解析で node heap が 5-6GB に肥大化し oom-killer が
    # 無差別 kill (ユーザーの VS Code も被害)。エージェント毎にヒープ上限を課す。
    parts.append('NODE_OPTIONS=--max-old-space-size=2560')
    parts.append(f'claude --dangerously-skip-permissions --model {model_name} --effort {effort}')

elif runtime == 'codex':
    codex_cfg = cfg.get('codex', {})
    provider = codex_cfg.get('provider_name', 'custom')
    base_url = codex_cfg.get('base_url', '')
    env_key = codex_cfg.get('api_key_env', '')
    wire_api = codex_cfg.get('wire_api', 'responses')
    model_slug = codex_cfg.get('model_slug', '$MODEL')

    fw_dir = os.environ.get('FRAMEWORK_DIR', os.getcwd())
    config_dir = os.path.join(fw_dir, '.codex-profiles')
    config_path = os.path.join(config_dir, f'{provider}.config.toml')
    catalog_path = os.path.join(config_dir, f'{provider}.json')

    # config.toml 生成 (カタログは .codex-profiles/ に既にある)
    os.makedirs(config_dir, exist_ok=True)
    lines = [
        f'model = {chr(34)}{model_slug}{chr(34)}',
        f'model_provider = {chr(34)}{provider}{chr(34)}',
        f'model_catalog_json = {chr(34)}{catalog_path}{chr(34)}',
        f'model_reasoning_effort = {chr(34)}high{chr(34)}',
        f'sandbox_permissions = [{chr(34)}network{chr(34)}]',
        f'[model_providers.{provider}]',
        f'name = {chr(34)}{provider}{chr(34)}',
        f'base_url = {chr(34)}{base_url}{chr(34)}',
        f'env_key = {chr(34)}{env_key}{chr(34)}',
        f'wire_api = {chr(34)}{wire_api}{chr(34)}',
        'stream_idle_timeout_ms = 7200000',
        'stream_max_retries = 5',
        'request_max_retries = 4',
    ]
    with open(config_path, 'w') as f:
        f.write(chr(10).join(lines) + chr(10))

    # API キー
    api_key_val = os.environ.get(env_key, '')
    if api_key_val:
        parts.append(f'{env_key}={api_key_val}')
    parts.append(f'CODEX_HOME={config_dir} codex -p {provider} -a never -s danger-full-access')

elif runtime == 'aider':
    for k, v in cfg.get('env', {}).items():
        if v.startswith('\$'):
            v = os.environ.get(v[1:], '')
        parts.append(f'{k}={v}')
    parts.append(f'aider --yes-always --model $MODEL')

print(' '.join(parts))
")

# --- tmux 内チェック ---
if [ -z "${TMUX:-}" ]; then
    echo "tmux の中で実行してください (./start.sh で起動)"
    exit 1
fi

# --- ログ ---
mkdir -p "$DIR/logs"
LOGFILE="$DIR/logs/${AGENT_ID}_$(date +%Y%m%d_%H%M%S).log"
LOGGED_CMD="script -q -f $LOGFILE -c '$LAUNCH_CMD; echo \"[AGENT EXITED] Press enter to close\"; read'"

# --- ウィンドウ作成 (常に tab — フルスクリーンで UI が崩れない) ---
tmux new-window -n "$WIN_NAME" "$LOGGED_CMD"

# --- プロンプト送信 ---
if [ -n "$PROMPT" ]; then
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
