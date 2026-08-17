#!/usr/bin/env python3
"""
sync_skills.py — Anthropic-Cybersecurity-Skills を攻撃系に絞って公開
--------------------------------------------------------------------
git submodule (third_party/Anthropic-Cybersecurity-Skills) 内の 817 スキルから、
moro-agent の用途 (バグバウンティ/VDP + RE) に合う「攻撃系」サブドメインを選び、
`.claude/skills/` へ symlink で公開する。

Claude Code は `.claude/skills/` の SKILL.md を description で自動ディスカバリする。
description のみ常時コンテキストに載り、本文は発動時にのみ読まれる。

- 攻撃系 257 個を公開 (description 約 7,093 token ≒ 200k の 3.5%)
- 絞り込み条件は ATTACK_SUBDOMAINS を編集すれば変更可
- 冪等 (既存 symlink は削除し、対象だけ作り直す)

使い方:
  python3 scripts/sync_skills.py           # 攻撃系を .claude/skills/ へ symlink
  python3 scripts/sync_skills.py --all     # 全 817 を公開
  python3 scripts/sync_skills.py --dry-run # 何を公開するか確認のみ
  python3 scripts/sync_skills.py --clear   # .claude/skills/ の symlink を全削除
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBMODULE = os.path.join(FRAMEWORK_DIR, "third_party", "Anthropic-Cybersecurity-Skills")
SKILLS_SRC = os.path.join(SUBMODULE, "skills")
CLAUDE_SKILLS = os.path.join(FRAMEWORK_DIR, ".claude", "skills")

# moro-agent 用途 (バグバウンティ/VDP + RE) に合う攻撃系サブドメイン
ATTACK_SUBDOMAINS = {
    "red-teaming",
    "red-team",
    "offensive-security",
    "penetration-testing",
    "web-application-security",
    "api-security",
    "application-security",
    "malware-analysis",          # RE 系 (Ghidra / ELF / バッファ等)
    "network-security",
    "identity-access-management",
    "identity-and-access-management",
    "hardware-firmware-security",
}


def iter_skills() -> list[tuple[str, str]]:
    """(name, subdomain) の一覧を SUBMODULE から列挙。"""
    out = []
    for path in glob.glob(os.path.join(SKILLS_SRC, "*", "SKILL.md")):
        name = os.path.basename(os.path.dirname(path))
        txt = open(path, encoding="utf-8").read()
        m = re.search(r"^subdomain:\s*(.+)$", txt, re.M)
        sub = m.group(1).strip() if m else ""
        # subdomain はカンマ区切り複数の場合がある
        subs = [s.strip() for s in sub.replace(",", "/").split("/") if s.strip()]
        out.append((name, subs))
    return out


def select(all_skills: bool) -> list[str]:
    selected = []
    for name, subs in iter_skills():
        if all_skills or any(s in ATTACK_SUBDOMAINS for s in subs):
            selected.append(name)
    return sorted(selected)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全 817 スキルを公開")
    ap.add_argument("--dry-run", action="store_true", help="公開対象の表示のみ")
    ap.add_argument("--clear", action="store_true", help="既存 symlink を全削除")
    args = ap.parse_args()

    if not os.path.isdir(SKILLS_SRC):
        print(f"[error] submodule がありません: {SUBMODULE}", file=sys.stderr)
        print("  git submodule update --init --recursive で取得せよ", file=sys.stderr)
        return 1

    os.makedirs(CLAUDE_SKILLS, exist_ok=True)

    # 既存 symlink を全削除 (冪等にするため)
    removed = 0
    if os.path.isdir(CLAUDE_SKILLS):
        for e in os.listdir(CLAUDE_SKILLS):
            p = os.path.join(CLAUDE_SKILLS, e)
            if os.path.islink(p):
                os.unlink(p)
                removed += 1

    if args.clear:
        print(f"cleared {removed} symlinks from .claude/skills/")
        return 0

    names = select(all_skills=args.all)
    created = 0
    for name in names:
        src = os.path.join(SKILLS_SRC, name)
        dst = os.path.join(CLAUDE_SKILLS, name)
        if args.dry_run:
            print(f"  [dry-run] {name}")
            continue
        os.symlink(src, dst)
        created += 1

    if not args.dry_run:
        print(f"removed {removed} old symlinks; created {created} symlinks "
              f"({'ALL' if args.all else 'attack-only'})")
    else:
        print(f"(removed {removed} old; would create {len(names)} symlinks "
              f"({'ALL' if args.all else 'attack-only'}))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
