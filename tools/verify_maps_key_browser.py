#!/usr/bin/env python3
"""One-shot browser verification of a Google Maps browser key.

Serves one local attacker-origin page, initializes one map, captures Chromium's
DOM/stderr/screenshot, then shuts the local server down cleanly. Intended for
explicitly authorized, low-rate validation only.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import html, re, subprocess, threading, time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workspace/google_maps_key_check"
source = (OUT / "home1.html").read_text(errors="ignore")
m = re.search(r"AIza[0-9A-Za-z_-]{30,}", source)
if not m:
    raise SystemExit("No API key found in captured source")
key = m.group(0)

PAGE = f'''<!doctype html><html><head><meta charset="utf-8">
<title>Authorized Maps key restriction check</title>
<style>html,body,#map{{height:100%;margin:0}}#status{{position:fixed;z-index:9;background:#fff;padding:8px}}</style>
<script>
window.__events=[];
function rec(s){{ window.__events.push(String(s)); document.getElementById('status').textContent=window.__events.join(' | '); }}
window.onerror=(m,s,l,c,e)=>rec('onerror:'+m);
window.onunhandledrejection=e=>rec('rejection:'+e.reason);
function initMap(){{
  rec('callback');
  try {{
    const map=new google.maps.Map(document.getElementById('map'), {{center:{{lat:38.9,lng:-77.0}},zoom:4}});
    google.maps.event.addListenerOnce(map,'idle',()=>rec('map-idle'));
    setTimeout(()=>rec('map-object:'+!!map),2500);
  }} catch(e) {{ rec('exception:'+e); }}
}}
</script>
<script async defer src="https://maps.googleapis.com/maps/api/js?key={html.escape(key)}&callback=initMap&v=weekly"></script>
</head><body><div id="status">loading</div><div id="map"></div></body></html>'''

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b=PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self,*args): pass

srv=HTTPServer(('127.0.0.1',0),H)
th=threading.Thread(target=srv.serve_forever,daemon=True); th.start()
url=f'http://127.0.0.1:{srv.server_port}/attacker-origin.html'
cmd=['/usr/bin/chromium','--headless=new','--no-sandbox','--disable-gpu',
     '--disable-dev-shm-usage','--window-size=1280,800','--virtual-time-budget=12000',
     '--enable-logging=stderr','--log-level=0',
     f'--screenshot={OUT / "attacker-origin.png"}','--dump-dom',url]
try:
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=35)
finally:
    srv.shutdown(); srv.server_close()
(OUT/'attacker-origin.dom.html').write_text(r.stdout)
(OUT/'attacker-origin.chromium.stderr').write_text(r.stderr)
print('url_origin=http://127.0.0.1:<ephemeral>')
print('chromium_exit=',r.returncode)
for pat in ['RefererNotAllowedMapError','InvalidKeyMapError','ApiNotActivatedMapError','BillingNotEnabledMapError','map-idle','map-object:true','gm-err-container']:
    count=(r.stdout+'\n'+r.stderr).count(pat)
    print(f'{pat}={count}')
print('dom_bytes=',len(r.stdout),'stderr_bytes=',len(r.stderr))
