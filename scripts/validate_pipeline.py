#!/usr/bin/env python3
"""Self-contained end-to-end validation: starts the mock in a thread, runs the
live probe pipeline against every CORS variant, and asserts the expected verdict
for the primary vector of each path. No shell backgrounding required.
"""
import threading, time, sys
from http.server import HTTPServer
sys.path.insert(0, ".")
from mock_cors_server import H
import probe_cors_live as probe
from cors_analyzer import analyze

PORT = 8099

def start_server():
    srv = HTTPServer(("127.0.0.1", PORT), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv

# path -> (vector prefix we care about, expected verdict)
EXPECT = {
    "reflect-creds":  ("V1", "EXPLOITABLE"),
    "wildcard":       ("V3", "NEEDS_AUTHZ_DATA"),
    "wildcard-creds": ("V2", "NOT_EXPLOITABLE"),
    "reflect-vary":   ("V5", "NOT_EXPLOITABLE"),
    "null":           ("V4", "EXPLOITABLE"),
    "safe":           ("V1", "NOT_EXPLOITABLE"),
}

def verdict_for(verdicts, prefix):
    for v in verdicts:
        if v.vector.startswith(prefix):
            return v.verdict
    return "MISSING"

def main():
    srv = start_server()
    time.sleep(0.5)
    failures = 0
    try:
        for path, (prefix, expected) in EXPECT.items():
            url = "http://127.0.0.1:%d/%s" % (PORT, path)
            probes = probe.run(url, 0.02)   # fast: localhost
            verdicts = analyze(probes)
            got = verdict_for(verdicts, prefix)
            ok = (got == expected)
            failures += (0 if ok else 1)
            print("[%s] /%-14s %s => got %-16s expected %-16s"
                  % ("PASS" if ok else "FAIL", path, prefix, got, expected))
    finally:
        srv.shutdown()
    print("\n%s (%d failures)" % ("ALL PIPELINE TESTS PASSED" if failures == 0 else "PIPELINE TESTS FAILED", failures))
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()
