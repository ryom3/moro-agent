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


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else ""
    fw = os.environ.get("FRAMEWORK_DIR", ".")
    with open(os.path.join(fw, "models.json")) as f:
        cfg = json.load(f).get(model, {})
    for k, v in cfg.get("env", {}).items():
        # `$VAR` 形式は環境変数から解決。それ以外はリテラル値。
        val = os.environ.get(v[1:], "") if v.startswith("$") else v
        print(f"export {k}={shlex.quote(val)}")


if __name__ == "__main__":
    main()
