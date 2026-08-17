#!/usr/bin/env python3
"""
r2_recon.py — radare2 ベースのバイナリ静止解析ラッパー
----------------------------------------------------
RE サブエージェントが、対象バイナリの基本情報・関数・文字列・インポート・
逆アセンブルを、安定した（ANSI 除去済みの）プレーンテキストで取得する。

Ghidra が導入されていない環境でも、r2 で即座に動く。MCP ツール化や
CLAUDE-re.md のサブエージェント指示から呼ばれることを想定。

使い方:
  python3 tools/re/r2_recon.py info    <binary>     # file / 保護機構 (NX/PIE/Canary/RELRO)
  python3 tools/re/r2_recon.py funcs   <binary>     # 関数一覧
  python3 tools/re/r2_recon.py strings <binary>     # 文字列
  python3 tools/re/r2_recon.py imports <binary>     # インポート (PLT/GOT 解決)
  python3 tools/re/r2_recon.py disasm  <binary> <func>   # 逆アセンブル
  python3 tools/re/r2_recon.py xrefs   <binary> <addr>   # 参照元/参照先
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def r2(binary: str, cmds: str) -> str:
    """r2 をヘッドレスで実行し、ANSI エスケープを除去した出力を返す。"""
    out = subprocess.run(
        ["r2", "-e", "scr.color=0", "-e", "scr.utf8=false", "-q", "-c", cmds, binary],
        capture_output=True, text=True, timeout=120)
    # ANSI エスケープシーケンス除去 (念のため)
    import re
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", out.stdout + out.stderr)
    return clean.strip()


def cmd_info(binary: str) -> str:
    """file 情報 + 保護機構 (NX/PIE/Canary/RELRO) を判定。"""
    lines = []
    f = subprocess.run(["file", binary], capture_output=True, text=True).stdout.strip()
    lines.append(f"FILE: {f}")

    # PIE 判定は file 出力 ("pie executable" / "shared object" なら PIE)
    pie = "PIE" if ("pie executable" in f or "shared object" in f) else "no PIE"

    # readelf で NX / RELRO / Canary を判定
    readelf_gnu = subprocess.run(
        ["readelf", "-lW", binary], capture_output=True, text=True).stdout
    readelf_dyn = subprocess.run(
        ["readelf", "-dW", binary], capture_output=True, text=True).stdout
    readelf_sym = subprocess.run(
        ["readelf", "-sW", binary], capture_output=True, text=True).stdout

    # NX: GNU_STACK セグメントの FLAGS に E (execute) が含まれるか
    nx = "NX enabled"
    for line in readelf_gnu.splitlines():
        if "GNU_STACK" in line:
            if "E" in line.split("GNU_STACK")[-1][:8]:
                nx = "NX disabled (stack executable)"
            break

    relro = "Full RELRO" if "BIND_NOW" in readelf_dyn else ("Partial RELRO" if "GNU_RELRO" in readelf_dyn else "no RELRO")
    canary = "Canary" if "__stack_chk_fail" in readelf_sym else "no Canary"

    lines.append(f"PROTECTIONS: {pie} / {nx} / {canary} / {relro}")
    return "\n".join(lines)


def cmd_funcs(binary: str) -> str:
    return r2(binary, "aaa; afl")


def cmd_strings(binary: str) -> str:
    return r2(binary, "izz")


def cmd_imports(binary: str) -> str:
    return r2(binary, "ii")


def cmd_disasm(binary: str, func: str) -> str:
    return r2(binary, f"aaa; s {func}; pdf")


def cmd_xrefs(binary: str, addr: str) -> str:
    return r2(binary, f"aaa; axt @ {addr}")


def main() -> int:
    ap = argparse.ArgumentParser(description="radare2 ベースの静止解析ラッパー")
    ap.add_argument("cmd", choices=["info", "funcs", "strings", "imports", "disasm", "xrefs"])
    ap.add_argument("binary")
    ap.add_argument("arg", nargs="?", default="")
    args = ap.parse_args()

    dispatch = {
        "info": lambda: cmd_info(args.binary),
        "funcs": lambda: cmd_funcs(args.binary),
        "strings": lambda: cmd_strings(args.binary),
        "imports": lambda: cmd_imports(args.binary),
        "disasm": lambda: cmd_disasm(args.binary, args.arg),
        "xrefs": lambda: cmd_xrefs(args.binary, args.arg),
    }
    fn = dispatch[args.cmd]
    if args.cmd in ("disasm", "xrefs") and not args.arg:
        print(f"error: {args.cmd} には関数名/アドレスが必要", file=sys.stderr)
        return 2
    print(fn())
    return 0


if __name__ == "__main__":
    sys.exit(main())
