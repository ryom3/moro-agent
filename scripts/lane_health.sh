#!/usr/bin/env bash
# レーンの停滞/context 枯渇を検知して 1 行で出す (監督用)
cd /home/kali/Desktop/pentest-framework
now=$(date +%s)
for p in $(pgrep -f "claude --dangerously"); do
  id=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep "^AGENT_ID=" | cut -d= -f2)
  [ -z "$id" ] && continue
  L=$(ls -t logs/${id}_*.log 2>/dev/null | head -1); [ -z "$L" ] && continue
  m=$(stat -c %Y "$L" 2>/dev/null); age=$(( (now - m) / 60 ))
  ctx=$(tail -c 4000 "$L" 2>/dev/null | tr -d '\000' | grep -o "[0-9]\+%" | tail -1 | tr -d '%')
  rel=$([ -f state/relay_$id.json ] && echo "relay有" || echo "-")
  if [ "$age" -ge 20 ] || { [ -n "$ctx" ] && [ "$ctx" -ge 90 ] 2>/dev/null; }; then
    echo "[HEALTH] $id 停滞${age}分 context=${ctx:-?}% $rel — 要対応"
  fi
done
free -m | awk '/^Mem:/{if ($7 < 1500) print "[HEALTH] メモリ残 "$7"MB — レーンを減らせ"}'
