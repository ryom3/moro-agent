#!/usr/bin/env bash
# check_updates.sh — 起動時更新チェック (start.sh から呼ばれる)
#
# フレームワーク依存の更新 (skills submodule / フレームワーク本体) を確認し、
# 更新があれば「更新するか」を対話確認して更新する。
#
# - skills submodule: 更新があれば pull + sync_skills.py で symlink 再生成
# - フレームワーク本体: origin/main との差分を通知のみ (自動 pull はしない。
#   ローカル変更・稼働中エンゲージメントへの影響を避けるため、手動更新を促す)
#
# ネットワーク到達不可・更新なしの場合は無言で続行する (起動を妨げない)。

set -uo pipefail  # 失敗しても起動を止めない

DIR="$(cd "$(dirname "$0")/.." && pwd)"
SUBMODULE="$DIR/third_party/Anthropic-Cybersecurity-Skills"
UPDATED_ANY=0

# --- 1. skills submodule の更新確認 ---
# 注意: submodule の .git は「ファイル」(gitdir ポインタ) のため -e で判定する
if [ -e "$SUBMODULE/.git" ]; then
    echo "[update] skills リポジトリの更新を確認中..."
    if ( cd "$SUBMODULE" && timeout 20 git fetch --depth 1 origin main 2>/dev/null ); then
        LOCAL=$(git -C "$SUBMODULE" rev-parse HEAD 2>/dev/null)
        REMOTE=$(git -C "$SUBMODULE" rev-parse FETCH_HEAD 2>/dev/null)
        if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
            echo "  → 更新あり: $(git -C "$SUBMODULE" log -1 --format='%h %s' FETCH_HEAD 2>/dev/null | cut -c1-70)"
            UPDATED_ANY=1
            SKILL_UPDATED=1
        else
            echo "  → skills は最新です"
        fi
    else
        echo "  → skills の更新確認をスキップ (ネットワーク到達不可)"
    fi
else
    echo "  → skills submodule 未取得 (git submodule update --init --recursive で取得)"
fi

# --- 2. フレームワーク本体 (origin/main) の更新確認 ---
if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[update] フレームワーク本体 (origin/main) の更新を確認中..."
    if ( cd "$DIR" && timeout 20 git fetch origin main 2>/dev/null ); then
        BEHIND=$(git -C "$DIR" rev-list --count HEAD..origin/main 2>/dev/null)
        if [ -n "$BEHIND" ] && [ "$BEHIND" -gt 0 ]; then
            echo "  → フレームワーク本体に $BEHIND 件の更新があります"
            UPDATED_ANY=1
            FW_UPDATED=1
        else
            echo "  → フレームワーク本体は最新です"
        fi
    else
        echo "  → フレームワーク本体の更新確認をスキップ (ネットワーク到達不可)"
    fi
fi

# --- 3. 更新があれば確認して適用 ---
if [ "${UPDATED_ANY:-0}" -eq 1 ]; then
    # start.sh が --no-update-check で呼んだ場合は確認せずスキップ
    if [ "${SKIP_UPDATE_CONFIRM:-0}" = "1" ]; then
        echo "[update] 確認をスキップします (--no-update-check)"
        exit 0
    fi
    printf "[update] 更新を適用しますか? (y/N): "
    read -r ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        # skills submodule の更新を適用
        if [ "${SKILL_UPDATED:-0}" = "1" ]; then
            echo "[update] skills を更新中..."
            ( cd "$SUBMODULE" && git merge --ff-only FETCH_HEAD 2>/dev/null )
            echo "[update] symlink を再生成中..."
            python3 "$DIR/scripts/sync_skills.py" 2>&1 | tail -1
        fi
        # フレームワーク本体は通知のみ (自動 pull はしない)
        if [ "${FW_UPDATED:-0}" = "1" ]; then
            echo "[update] フレームワーク本体の更新は手動で行ってください:"
            echo "   git pull origin main   # (ローカル変更・稼働中エージェントに注意)"
        fi
    else
        echo "[update] 更新をスキップします"
    fi
fi
