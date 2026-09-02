#!/usr/bin/env python3
"""
models.json の env ブロックを `export K=V` 行で標準出力する。

シークレット漏洩対策: 起動コマンドの argv に API キーを載せないため、
呼び出し側で `eval "$(python3 scripts/env_export.py MODEL)"` のようにして
環境変数へ export する (子プロセスは環境経由で継承。ps には出ない)。

使い方:
  eval "$(python3 scripts/env_export.py glm-5.3)"
"""
import json
import os
import shlex
import sys
from pathlib import Path

# CWD/環境非依存: フレームワークルートをスクリプト自身の位置から導出。
# (tmux / script 経由で FRAMEWORK_DIR が失われても正しく models.json を開ける)
FRAMEWORK_DIR = Path(__file__).resolve().parent.parent


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(FRAMEWORK_DIR / "models.json") as f:
        cfg = json.load(f).get(model, {})
    env = cfg.get("env", {})
    for k, v in env.items():
        # `$VAR` 形式は環境変数から解決。それ以外はリテラル値。
        if v.startswith("$"):
            val = os.environ.get(v[1:], "")
            if not val:
                # 空解決を静かに通さない (認証フォールバック事故の防止)
                print(f"# WARNING: {v[1:]} が環境に無く {k} が空になりました "
                      "(start.sh を tmux 外から実行した場合は .env の source に set -a が必要)",
                      file=sys.stderr)
        else:
            val = v
        print(f"export {k}={shlex.quote(val)}")

    # 環境汚染の除去: claude-code 系モデルが「env ブロックを持たない」場合、
    # それは Anthropic 公式 (サブスク) 直結を意味する。しかし tmux のグローバル
    # 環境に以前の GLM 起動の ANTHROPIC_BASE_URL/AUTH_TOKEN が残っていると、
    # opus 等のリクエストが Z.ai に誤ルートされる (実障害: opus が一切使えなかった)。
    # env ブロックが ANTHROPIC_* を設定しない場合 (モデル未指定の既定 claude も
    # 含む)、明示的に unset して汚染を断つ。
    anthropic_set = {k for k in env if k.startswith("ANTHROPIC")}
    if cfg.get("runtime") in ("claude-code", None) and not anthropic_set:
        ANTHROPIC_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                          "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")
        stale = [k for k in ANTHROPIC_VARS if os.environ.get(k)]
        if stale:
            print(f"# clearing stale {', '.join(stale)} (以前のGLM起動等の汚染)", file=sys.stderr)
            print("unset " + " ".join(stale))


if __name__ == "__main__":
    main()
