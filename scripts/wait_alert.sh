#!/usr/bin/env bash
# wait_alert.sh — alert / イベントをイベント駆動で待つ (監督の確認レイテンシ削減)
#
# state/alerts.json と state/events.jsonl の変化 (mtime) を 1 秒間隔で監視し、
# 変化があったら即座に終了 (exit 0) する。タイムアウトしても終了する (exit 1)。
#
# 監督AIは「sleep 300 して確認」の代わりにこれを使う:
#   ./scripts/wait_alert.sh 300 && python3 scripts/state.py show && cat state/alerts.json
#
# - サブエージェントの alert / relay / state 更新 → 約1秒で検知して即確認できる
#   (従来の「5分待ち」のレイテンシを排除)
# - inotifywait があれば通知駆動に fallback は mtime ポーリング (1s)
#
# 使い方: wait_alert.sh [timeout_sec]   (デフォルト 300)

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT="${1:-300}"
A="$DIR/state/alerts.json"
E="$DIR/state/events.jsonl"

# inotifywait があれば使う (イベント駆動・CPU ゼロ)
if command -v inotifywait >/dev/null 2>&1; then
    exec inotifywait -qq -t "$TIMEOUT" -e modify,create,move \
        "$A" "$E" 2>/dev/null
fi

# fallback: mtime ポーリング (1 秒間隔)
mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }
A0=$(mtime "$A")
E0=$(mtime "$E")
end=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$end" ]; do
    sleep 1
    A1=$(mtime "$A")
    E1=$(mtime "$E")
    if [ "$A1" != "$A0" ] || [ "$E1" != "$E0" ]; then
        exit 0    # 変化を検知 → 即座に制御を返す
    fi
done
exit 1            # タイムアウト (変化なし)
