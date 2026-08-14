#!/usr/bin/env bash
# Offline end-to-end smoke test using the mock backend (no model downloads).
# Exercises: ingest (md + pdf via layout loader) -> serve -> query (+ tag filter)
#            -> status -> eval, all through the unified `kb` CLI.
set -euo pipefail
cd "$(dirname "$0")"

DATA=/tmp/kb_smoke
SAMPLE=/tmp/kb_smoke_notion
PORT=8931
export KB_BACKEND=mock

rm -rf "$DATA" "$SAMPLE"; mkdir -p "$SAMPLE"
cat > "$SAMPLE/Linux PrivEsc 1a2b3c4d5e6f7081.md" <<'MD'
# Linux Privilege Escalation
列挙が9割。
## SUID
SUID バイナリは所有者権限で実行される。
```bash
find / -perm -4000 -type f 2>/dev/null
```
### BoardLight
HTB BoardLight は SUID 経由で root を取得。
## Kernel Exploits
CVE-2021-4034 (pwnkit) は pkexec の SUID を悪用する。
MD

echo "== ingest source 1 (md, tag=notion, --reset) =="
python3 kb.py ingest --reset --source "$SAMPLE" --tag notion --data-dir "$DATA"

# second source: a layout-aware PDF built on the fly, tagged oscp
python3 - <<'PY'
import pymupdf, os
d = pymupdf.open(); p = d.new_page(width=600, height=400)
p.insert_textbox(pymupdf.Rect(40,25,560,60), "SUID Enumeration", fontsize=20, fontname="helv")
p.insert_textbox(pymupdf.Rect(40,90,285,300),
    "SUID binaries run as the owner. Enumerate first, then check GTFOBins.",
    fontsize=11, fontname="helv")
p.insert_textbox(pymupdf.Rect(40,170,285,200), "find / -perm -4000 -type f", fontsize=10, fontname="cour")
p.insert_textbox(pymupdf.Rect(315,90,560,300),
    "Kernel exploits target old versions. CVE-2021-4034 pwnkit abuses pkexec.",
    fontsize=11, fontname="helv")
os.makedirs("/tmp/kb_smoke_pdf", exist_ok=True)
d.save("/tmp/kb_smoke_pdf/suid.pdf"); d.close()
PY
echo "== ingest source 2 (pdf, tag=oscp) =="
python3 kb.py ingest --source /tmp/kb_smoke_pdf --tag oscp --data-dir "$DATA"

echo "== serve =="
python3 kb.py serve --port $PORT --data-dir "$DATA" >/tmp/kb_smoke_daemon.log 2>&1 &
DPID=$!
trap 'kill $DPID 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"status":"ok"' && break
  sleep 0.5
done

echo "== status =="
KB_PORT=$PORT python3 kb.py status --data-dir "$DATA"

echo "== query =="
KB_PORT=$PORT python3 kb.py query "SUID privilege escalation" --data-dir "$DATA"
echo "== query (tag filter: oscp only) =="
KB_PORT=$PORT python3 kb.py query "SUID" --tag oscp --top 2 --data-dir "$DATA"

echo "== eval =="
python3 kb.py eval --eval-set eval_set.example.yaml --data-dir "$DATA" --k 1 3 5

echo "== legacy shim (query.py still works) =="
KB_PORT=$PORT python3 query.py "pwnkit" --json --top 1 --data-dir "$DATA" | head -6

echo "== OK =="
