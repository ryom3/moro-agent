#!/usr/bin/env python3
"""
fetch_bbd.py — Bug Bounty Disclosures カタログを取得して KB ingest 用 md に変換
------------------------------------------------------------------------------
ソース: https://bug-bounty-disclosures.vercel.app/ (11,304 records)
   - HackerOne 9,991 / Bugcrowd 804 / Code4rena 410 / Immunefi 92 ...

Phase 1 (catalog):    賞金あり or High/Critical (~4,000件) のメタデータ md
Phase 2 (--deep):     HackerOne 公開 JSON から vulnerability_information
                      (攻撃手順本文) を取得して md に埋め込む

使い方:
  python3 scripts/fetch_bbd.py                     # Phase 1: カタログ → md
  python3 scripts/fetch_bbd.py --deep              # Phase 2: H1 本文取得 (時間かかる)
  python3 scripts/fetch_bbd.py --deep --limit 100  # 最初の100件だけ試す
  python3 scripts/fetch_bbd.py --deep --resume     # 既存 md に本文を追記 (再開)

その後:
  python3.13 kb/kb.py ingest --source kb/bbd-disclosures --tag bbd
  (KB daemon 再起動も忘れずに)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_URL = "https://bug-bounty-disclosures.vercel.app/data/catalog.js"
OUTDIR = os.path.join(FRAMEWORK_DIR, "kb", "bbd-disclosures")

UA = {"User-Agent": "moro-agent/0.1 (security research; contact: local)"}
REQUEST_INTERVAL = 1.0  # 秒 — 1 req/sec を維持


def fetch_url(url: str, timeout: int = 30) -> str | None:
    """URL を取得してテキストを返す。失敗時は None。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def fetch_catalog() -> list[dict]:
    print(f"[fetch] {CATALOG_URL}")
    raw = fetch_url(CATALOG_URL, timeout=60)
    if not raw:
        print("[error] カタログ取得失敗", file=sys.stderr)
        sys.exit(1)
    m = re.search(r"window\.DISCLOSURE_REPORTS=(\[.*\])", raw, re.S)
    data = json.loads(m.group(1))
    print(f"[fetch] {len(data):,} records")
    return data


def record_to_md(r: dict, vuln_info: str = "") -> str:
    bounty = r.get("bounty") or 0
    text = (f"# {r.get('title', 'Untitled')}\n\n"
            f"- Platform: {r.get('platform', '')}\n"
            f"- Severity: {r.get('severity', '')}\n"
            f"- Class: {r.get('vulnerabilityClass', '')}\n"
            f"- Weakness: {r.get('weakness') or 'N/A'}\n"
            f"- Bounty: ${bounty:,}\n"
            f"- Researcher: {r.get('researcher', '')}\n"
            f"- Date: {r.get('disclosedAt', '')}\n"
            f"- URL: {r.get('url', '')}\n"
            f"- CVEs: {', '.join(r.get('cves', [])) or 'N/A'}\n\n"
            f"Kind: {r.get('kind', '')} — {r.get('program', '')}\n")
    if vuln_info:
        text += f"\n## Vulnerability Details (from report)\n\n{vuln_info}\n"
    return text


def phase1_catalog(data: list[dict]) -> list[dict]:
    """カタログから対象を抽出して md を書き出す。対象リストを返す。"""
    good = [r for r in data
            if (r.get("bounty") and r.get("bounty", 0) > 0)
            or r.get("severity") in ("High", "Critical")]
    print(f"[filter] bounty>0 or High/Critical: {len(good):,} records")

    os.makedirs(OUTDIR, exist_ok=True)
    for r in good:
        rid = str(r.get("id", "unknown")).replace("/", "_")
        with open(os.path.join(OUTDIR, f"{rid}.md"), "w") as f:
            f.write(record_to_md(r))
    print(f"[done] {len(good):,} files → {OUTDIR}/")
    return good


def extract_h1_report_id(url: str) -> str | None:
    """HackerOne URL からレポート ID を抽出。"""
    m = re.search(r"hackerone\.com/reports/(\d+)", url or "")
    return m.group(1) if m else None


def fetch_h1_vuln_info(report_id: str) -> str | None:
    """HackerOne 公開 JSON から vulnerability_information を取得。"""
    url = f"https://hackerone.com/reports/{report_id}.json"
    raw = fetch_url(url)
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    info = d.get("vulnerability_information", "")
    if not info or len(info.strip()) < 50:
        # 本文が無い (非公開) 場合は summaries を fallback
        for s in d.get("summaries", []):
            c = s.get("content", "")
            if c and len(c.strip()) > 50:
                return c.strip()
        return None
    return info.strip()


def phase2_deep(records: list[dict], limit: int | None, resume: bool):
    """HackerOne の公開 JSON から攻撃手順本文を取得して md に追記。"""
    h1_records = [(extract_h1_report_id(r.get("url", "")), r)
                  for r in records
                  if r.get("platform") == "HackerOne"]
    h1_records = [(rid, r) for rid, r in h1_records if rid]
    print(f"[deep] HackerOne 対象: {len(h1_records):,} records")

    if limit:
        h1_records = h1_records[:limit]
        print(f"[deep] limit={limit} に制限")

    fetched = skipped = failed = 0
    for i, (rid, r) in enumerate(h1_records):
        md_path = os.path.join(OUTDIR, f"{r['id']}.md")
        existing = ""
        if os.path.exists(md_path):
            existing = open(md_path).read()
            # resume モードで既に本文がある場合はスキップ
            if resume and "## Vulnerability Details" in existing:
                skipped += 1
                continue

        # メタデータ部分のみ残して本文を置き換え
        meta_part = existing.split("\n## Vulnerability Details")[0]

        print(f"  [{i+1}/{len(h1_records)}] h1-{rid}: ", end="", flush=True)
        info = fetch_h1_vuln_info(rid)
        if info:
            with open(md_path, "w") as f:
                f.write(record_to_md(r, vuln_info=info))
            fetched += 1
            print(f"✓ {len(info)} chars")
        else:
            # 本文が取れない場合はメタデータだけ書き直して終わり
            if meta_part:
                with open(md_path, "w") as f:
                    f.write(meta_part)
            failed += 1
            print("— (非公開/本文なし)")

        time.sleep(REQUEST_INTERVAL)

    print(f"\n[deep] 完了: fetched={fetched}, skipped={skipped}, failed={failed}")
    print(f"[next] python3.13 kb/kb.py ingest --source {OUTDIR} --tag bbd")
    print("       KB daemon 再起動も忘れずに")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deep", action="store_true",
                    help="HackerOne 公開 JSON から攻撃手順本文を取得")
    ap.add_argument("--limit", type=int, default=None,
                    help="deep モードで最初の N 件のみ処理")
    ap.add_argument("--resume", action="store_true",
                    help="deep モードで既に本文があるものをスキップ")
    args = ap.parse_args()

    data = fetch_catalog()
    records = phase1_catalog(data)

    if args.deep:
        phase2_deep(records, args.limit, args.resume)
    else:
        print(f"\n[next] python3.13 kb/kb.py ingest --source {OUTDIR} --tag bbd")
        print("       KB daemon 再起動も忘れずに")
        print("       攻撃手順本文も取り込むなら: python3 scripts/fetch_bbd.py --deep")


if __name__ == "__main__":
    sys.exit(main())
