#!/usr/bin/env python3
"""session.py — エンゲージメントのセッション保存/復元

reset (state/ と logs/ を初期化して archive/ に丸ごと退避) の代わりに、
「次回そこから続けられる最小限」だけを保存する。

思想:
  - 復元に本当に必要なのは state/ (履歴)・strategy.md (思考)・tasks/ (指示)・
    報告書などのテキスト成果物だけ。合計で数 MB。
  - APK/ISO/システムイメージ/展開済みディレクトリ等の巨大バイナリは
    再取得できるので保存しない。代わりに「何がどこにあったか」を
    manifest に記録し、復元時に現存を照合する。

使い方:
  python3 scripts/session.py save    [--name NAME] [--note "..."] [--with-evidence]
  python3 scripts/session.py list
  python3 scripts/session.py show    NAME
  python3 scripts/session.py restore NAME [--force] [--with-logs]
  python3 scripts/session.py clean   --session NAME [--dry-run]
"""
import os, sys, json, shutil, argparse, hashlib, stat, gzip
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS = os.path.join(ROOT, "sessions")   # workspace 外 (reset で消えない)

# 保存するテキスト成果物の拡張子
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".asc", ".sh", ".py", ".yml", ".yaml"}
# 1 ファイルの上限 (これを超えるテキストは manifest 記録のみ)
MAX_FILE = 3 * 1024 * 1024
# 巨大成果物として manifest に載せる閾値
BIG = 20 * 1024 * 1024
# 中身を保存しないディレクトリ名 (再取得可能な展開物)
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "ext", "dl", "bins", "src",
             "asar_mail", "asar_pass", "asar_meet", "jadx-mail", "bundle",
             "src-pass", "src-auth", "src-vpn", "sn-server", "src_web", "tmp"}


def _is_special(path):
    try:
        m = os.lstat(path).st_mode
    except OSError:
        return True
    if stat.S_ISSOCK(m) or stat.S_ISFIFO(m) or stat.S_ISBLK(m) or stat.S_ISCHR(m):
        return True
    if stat.S_ISLNK(m):
        try:
            os.stat(path)
        except OSError:
            return True
    return False


def _sha256(path, limit=64 * 1024 * 1024):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b); n += len(b)
            if n >= limit:
                return h.hexdigest() + "+partial"
    return h.hexdigest()


def cmd_save(args):
    name = args.name or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(SESSIONS, name)
    if os.path.exists(dest) and not args.force:
        print(f"既に存在します: {dest}\n上書きするなら --force"); return 1
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)

    manifest = {"name": name, "saved_at": datetime.now().astimezone().isoformat(),
                "note": args.note or "", "saved": [], "not_saved": []}
    saved_bytes = 0

    # 1) state/ を丸ごと
    st_src = os.path.join(ROOT, "state")
    if os.path.isdir(st_src):
        st_dst = os.path.join(dest, "state"); os.makedirs(st_dst, exist_ok=True)
        for f in sorted(os.listdir(st_src)):
            s = os.path.join(st_src, f)
            if os.path.isfile(s) and not _is_special(s):
                shutil.copy2(s, os.path.join(st_dst, f))
                saved_bytes += os.path.getsize(s)
                manifest["saved"].append(f"state/{f}")

    # 2) workspace の中のテキスト成果物
    ws = os.path.join(ROOT, "workspace")
    for dirpath, dirnames, filenames in os.walk(ws):
        rel_dir = os.path.relpath(dirpath, ROOT)
        if os.path.basename(dirpath) == "sessions":
            dirnames[:] = []; continue
        keep_ev = args.with_evidence and os.path.basename(dirpath) == "evidence"
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS or (args.with_evidence and d == "evidence")]
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, ROOT)
            if _is_special(src):
                manifest["not_saved"].append({"path": rel, "why": "special file"}); continue
            try:
                size = os.path.getsize(src)
            except OSError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            take = (ext in TEXT_EXT and size <= MAX_FILE) or keep_ev
            if not take:
                if size >= BIG:
                    manifest["not_saved"].append(
                        {"path": rel, "bytes": size, "why": "large/binary (re-acquirable)"})
                continue
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            saved_bytes += size
            manifest["saved"].append(rel)

    # 3) logs/ を gzip で取り込む (lane ログは「何をしたか」の一次証跡。
    #    平文で 90MB 超でも gzip で 1/15 程度になる)
    manifest["logs"] = []
    lg_src = os.path.join(ROOT, "logs")
    if os.path.isdir(lg_src):
        lg_dst = os.path.join(dest, "logs"); os.makedirs(lg_dst, exist_ok=True)
        for f in sorted(os.listdir(lg_src)):
            sp = os.path.join(lg_src, f)
            if not os.path.isfile(sp) or _is_special(sp):
                continue
            dp = os.path.join(lg_dst, f + ".gz")
            with open(sp, "rb") as fi, gzip.open(dp, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            saved_bytes += os.path.getsize(dp)
            manifest["logs"].append(f"logs/{f}")

    # 3) 巨大成果物の所在を記録 (中身は保存しない)
    loot = os.path.join(ws, "loot")
    if os.path.isdir(loot):
        big = []
        for d in sorted(os.listdir(loot)):
            p = os.path.join(loot, d)
            if not os.path.isdir(p):
                continue
            tot = 0
            for dp, _, fns in os.walk(p):
                for fn in fns:
                    q = os.path.join(dp, fn)
                    if not _is_special(q):
                        try: tot += os.path.getsize(q)
                        except OSError: pass
            big.append({"dir": f"workspace/loot/{d}", "bytes": tot})
        manifest["loot_sizes"] = sorted(big, key=lambda x: -x["bytes"])


    # 4) 提出物を SUBMIT/ に集約する。
    #    「後で戻して提出する」ときに restore を経ずに開けるようにするため、
    #    セッション直下に提出用ファイルだけを平置きして索引を付ける。
    sub_dir = os.path.join(dest, "SUBMIT")
    submits = []
    for rel in manifest["saved"]:
        base = os.path.basename(rel)
        if base.startswith(("SUBMIT_", "FORM_")) or base.startswith("JA_"):
            submits.append(rel)
    if submits:
        os.makedirs(sub_dir, exist_ok=True)
        for rel in sorted(submits):
            src_f = os.path.join(dest, rel)
            if os.path.isfile(src_f):
                shutil.copy2(src_f, os.path.join(sub_dir, os.path.basename(rel)))
        lines = ["# 提出物 (このディレクトリだけで提出できる)", "",
                 f"セッション: {name}", f"保存: {manifest['saved_at']}", ""]
        if note_txt := (args.note or ""):
            lines += [f"メモ: {note_txt}", ""]
        lines += ["| ファイル | 元の場所 |", "|---|---|"]
        for rel in sorted(submits):
            lines.append(f"| {os.path.basename(rel)} | {rel} |")
        lines += ["", "※ .txt = メール本文用 (プレーンテキスト)",
                  "※ .md  = 構造付きの控え",
                  "※ JA_* = 日本語の確認用 (提出しない)", ""]
        with open(os.path.join(sub_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        manifest["submit_files"] = [os.path.basename(r) for r in sorted(submits)]

    manifest["saved_bytes"] = saved_bytes
    with open(os.path.join(dest, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"セッション保存: sessions/{name}")
    print(f"  保存ファイル {len(manifest['saved'])} 件 / {saved_bytes/1048576:.1f} MB")
    print(f"  lane ログ {len(manifest.get('logs',[]))} 件 (gzip)")
    if manifest.get("submit_files"):
        print(f"  提出物 {len(manifest['submit_files'])} 件 → sessions/{name}/SUBMIT/")
    print(f"  未保存 (再取得可能) {len(manifest['not_saved'])} 件")
    if args.note:
        print(f"  note: {args.note}")
    return 0


def cmd_list(args):
    if not os.path.isdir(SESSIONS):
        print("セッションはまだありません"); return 0
    rows = []
    for n in sorted(os.listdir(SESSIONS)):
        mf = os.path.join(SESSIONS, n, "MANIFEST.json")
        if not os.path.isfile(mf):
            continue
        m = json.load(open(mf, encoding="utf-8"))
        rows.append((n, m.get("saved_at", "")[:19], len(m.get("saved", [])),
                     m.get("saved_bytes", 0) / 1048576, m.get("note", "")))
    if not rows:
        print("セッションはまだありません"); return 0
    print(f"{'NAME':<22} {'SAVED':<20} {'FILES':>6} {'MB':>7}  NOTE")
    for n, t, c, mb, note in rows:
        print(f"{n:<22} {t:<20} {c:>6} {mb:>7.1f}  {note[:50]}")
    return 0


def cmd_show(args):
    mf = os.path.join(SESSIONS, args.name, "MANIFEST.json")
    if not os.path.isfile(mf):
        print(f"見つかりません: {args.name}"); return 1
    m = json.load(open(mf, encoding="utf-8"))
    print(f"name     : {m['name']}")
    print(f"saved_at : {m['saved_at']}")
    print(f"note     : {m.get('note','')}")
    print(f"saved    : {len(m['saved'])} files / {m['saved_bytes']/1048576:.1f} MB")
    print(f"logs     : {len(m.get('logs',[]))} lane logs (gzip)")
    print("\n-- 主な成果物 --")
    for p in m["saved"]:
        if "SUBMIT_" in p or "FORM_" in p or p.endswith("strategy.md") or "/JA_" in p:
            print(f"  {p}")
    print("\n-- 保存していない大きい成果物 (再取得が必要なら) --")
    for x in m["not_saved"][:15]:
        if x.get("bytes"):
            print(f"  {x['bytes']/1048576:>8.1f} MB  {x['path']}")
    return 0


def cmd_restore(args):
    src = os.path.join(SESSIONS, args.name)
    mf = os.path.join(src, "MANIFEST.json")
    if not os.path.isfile(mf):
        print(f"見つかりません: {args.name}"); return 1
    m = json.load(open(mf, encoding="utf-8"))

    if not args.force:
        print(f"復元します: {args.name} ({m['saved_at'][:19]})")
        print(f"  {len(m['saved'])} ファイルを現在のツリーに書き戻します")
        print("  現在の state/ と重複ファイルは sessions/_before_restore/ に退避されます")
        if input("実行する? (y/N): ").lower() != "y":
            print("キャンセル"); return 0

    # 現状を退避
    bk = os.path.join(SESSIONS, "_before_restore",
                      datetime.now().strftime("%Y%m%d_%H%M%S"))
    restored = 0
    for rel in m["saved"]:
        s = os.path.join(src, rel)
        d = os.path.join(ROOT, rel)
        if not os.path.isfile(s):
            continue
        if os.path.isfile(d):
            b = os.path.join(bk, rel)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(d, b)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        restored += 1

    if getattr(args, "with_logs", False) and m.get("logs"):
        lg = os.path.join(ROOT, "logs"); os.makedirs(lg, exist_ok=True)
        for rel in m["logs"]:
            gz = os.path.join(src, rel + ".gz")
            if os.path.isfile(gz):
                with gzip.open(gz, "rb") as fi, open(os.path.join(ROOT, rel), "wb") as fo:
                    shutil.copyfileobj(fi, fo)
        print(f"lane ログを展開しました: {len(m['logs'])} 件")

    print(f"復元しました: {restored} ファイル")
    print(f"復元前の状態: sessions/_before_restore/{os.path.basename(bk)}/")
    missing = []
    for x in m.get("not_saved", []):
        p = os.path.join(ROOT, x["path"])
        if x.get("bytes") and not os.path.exists(p):
            missing.append(x)
    if missing:
        print(f"\n再取得が必要な大きい成果物 {len(missing)} 件 (先頭 10):")
        for x in missing[:10]:
            print(f"  {x['bytes']/1048576:>8.1f} MB  {x['path']}")
    print("\n次にやること: python3 scripts/state.py resume  で relay を読む")
    return 0



def cmd_clean(args):
    """セッション保存済みであることを確認した上で workspace/ を空にする。

    reset は workspace を消さない (巨大バイナリを毎回コピーしないため)。
    その結果、前エンゲージメントの成果物が次セッションのノイズになる。
    本コマンドはセッションに取り込み済みであることを照合してから消す。
    """
    src = os.path.join(SESSIONS, args.session)
    mf = os.path.join(src, "MANIFEST.json")
    if not os.path.isfile(mf):
        print(f"セッションが見つかりません: {args.session}")
        print("先に  python3 scripts/session.py save --name NAME  を実行してください")
        return 1
    m = json.load(open(mf, encoding="utf-8"))
    saved = set(m.get("saved", []))

    ws = os.path.join(ROOT, "workspace")
    if not os.path.isdir(ws):
        print("workspace/ がありません"); return 0

    uncaptured, total, nfiles = [], 0, 0
    for dirpath, dirnames, filenames in os.walk(ws):
        # save と同じ除外規則を適用する。適用しないと、クローンした公開リポジトリや
        # 展開済みディレクトリのテキストを「取りこぼし」と誤検知する。
        pruned = [d for d in dirnames if d in SKIP_DIRS]
        for d in pruned:
            p_ = os.path.join(dirpath, d)
            for dp2, _, fns2 in os.walk(p_):
                for fn2 in fns2:
                    q = os.path.join(dp2, fn2)
                    if not _is_special(q):
                        try:
                            total += os.path.getsize(q); nfiles += 1
                        except OSError:
                            pass
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT)
            if _is_special(fp):
                continue
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            total += sz; nfiles += 1
            ext = os.path.splitext(fn)[1].lower()
            # テキスト成果物なのにセッションに無いものは取りこぼし
            if ext in TEXT_EXT and sz <= MAX_FILE and rel not in saved:
                uncaptured.append((rel, sz))

    print(f"削除対象: workspace/ の {nfiles} ファイル / {total/1073741824:.2f} GB")
    print(f"セッション {args.session} に保存済み: {len(saved)} ファイル"
          f" ({m.get('saved_bytes',0)/1048576:.1f} MB) + lane ログ {len(m.get('logs',[]))} 件")

    if uncaptured:
        print(f"\n[!] セッションに取り込まれていないテキストが {len(uncaptured)} 件あります:")
        for rel, sz in uncaptured[:20]:
            print(f"      {sz/1024:>8.1f} KB  {rel}")
        if len(uncaptured) > 20:
            print(f"      ... 他 {len(uncaptured)-20} 件")
        if not args.force:
            print("\n中止しました。取りこぼしを保存してからやり直してください:")
            print(f"  python3 scripts/session.py save --name {args.session} --force")
            print("それでも消すなら --force")
            return 1

    if args.dry_run:
        print("\n--dry-run のため削除していません")
        return 0

    if not args.force and not getattr(args, "yes", False):
        print("\nworkspace/ の中身をすべて削除します"
              " (セッションから restore で戻せます)")
        if input("実行する? (y/N): ").lower() != "y":
            print("キャンセル"); return 0

    removed = 0
    for name in os.listdir(ws):
        p_ = os.path.join(ws, name)
        try:
            if os.path.isdir(p_) and not os.path.islink(p_):
                shutil.rmtree(p_)
            else:
                os.remove(p_)
            removed += 1
        except OSError as e:
            print(f"  [警告] 削除できません: {name} ({e})")
    print(f"\nworkspace/ をクリアしました ({removed} エントリ)")
    print(f"戻すには: python3 scripts/session.py restore {args.session}")
    print("※ APK/ISO 等の巨大バイナリはセッションに含まれないため再取得が必要です"
          " (所在は MANIFEST.json の not_saved)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="エンゲージメントのセッション保存/復元")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("save");    p.add_argument("--name"); p.add_argument("--note")
    p.add_argument("--with-evidence", action="store_true"); p.add_argument("--force", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("show");    p.add_argument("name")
    p = sub.add_parser("clean")
    p.add_argument("--session", required=True, help="照合するセッション名")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="確認を省く (取りこぼし検査は行う)")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("restore"); p.add_argument("name"); p.add_argument("--force", action="store_true")
    p.add_argument("--with-logs", action="store_true", help="lane ログも logs/ に展開する")
    a = ap.parse_args()
    return {"save": cmd_save, "list": cmd_list, "show": cmd_show,
            "restore": cmd_restore, "clean": cmd_clean}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main() or 0)
