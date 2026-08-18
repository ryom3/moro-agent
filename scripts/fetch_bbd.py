#!/usr/bin/env python3
"""
fetch_bbd.py — Bug Bounty Disclosures カタログを取得して KB ingest 用 md に変換
------------------------------------------------------------------------------
ソース: https://bug-bounty-disclosures.vercel.app/ (11,304 records)
   - HackerOne 9,991 / Bugcrowd 804 / Code4rena 410 / Immunefi 92 ...
   - 賞金合計 $3.67M、High/Critical 486件+賞金あり

フィルタ: 賞金あり or High/Critical (約4,000件) を md に変換 → kb/bbd-disclosures/

使い方:
  python3 scripts/fetch_bbd.py                # 取得+変換
  python3.13 kb/kb.py ingest --source kb/bbd-disclosures --tag bbd  # KBに取り込み
"""
import json, os, re, sys, urllib.request

CATALOG_URL = "https://bug-bounty-disclosures.vercel.app/data/catalog.js"
OUTDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "kb", "bbd-disclosures")

def main():
    print(f"[fetch] {CATALOG_URL}")
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "moro-agent"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
    m = re.search(r"window\.DISCLOSURE_REPORTS=(\[.*\])", raw, re.S)
    data = json.loads(m.group(1))
    print(f"[fetch] {len(data):,} records")

    good = [r for r in data
            if (r.get("bounty") and r.get("bounty", 0) > 0)
            or r.get("severity") in ("High", "Critical")]
    print(f"[filter] bounty>0 or High/Critical: {len(good):,} records")

    os.makedirs(OUTDIR, exist_ok=True)
    for r in good:
        rid = str(r.get("id", "unknown")).replace("/", "_")
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
        with open(os.path.join(OUTDIR, f"{rid}.md"), "w") as f:
            f.write(text)

    print(f"[done] {len(good):,} files → {OUTDIR}/")
    print(f"[next] python3.13 kb/kb.py ingest --source {OUTDIR} --tag bbd")
    print("       (ingest 後は kb daemon 再起動が必要)")

if __name__ == "__main__":
    sys.exit(main())
