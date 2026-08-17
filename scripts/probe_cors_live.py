#!/usr/bin/env python3
"""
probe_cors_live.py - ROE-compliant live CORS probe.

Sends a SMALL, fixed set of read-only requests (GET/OPTIONS) with different
Origin/credential conditions, parses the response headers, feeds them into
cors_analyzer, and prints per-vector verdicts.

Safety / rules-of-engagement:
  * Read-only methods only (GET, OPTIONS).
  * Global rate limit >= 1s between requests (configurable, default 1.5s).
  * No credentials are ever sent to third-party targets (we only MODEL the
    credentialed case; we never replay real cookies).
  * Bounded number of requests (max 4).
"""
from __future__ import annotations
import argparse, shutil, subprocess, sys, time

CURL = shutil.which("curl") or "/usr/bin/curl"
from cors_analyzer import Probe, analyze, render, summarize

EVIL = "https://attacker.example"


def curl_headers(url, method="GET", origin=None, timeout=15):
    cmd = [CURL, "-sS", "-o", "/dev/null", "-D", "-",
           "-X", method, "--max-time", str(timeout), url]
    if origin:
        cmd += ["-H", "Origin: " + origin]
    if method == "OPTIONS":
        cmd += ["-H", "Access-Control-Request-Method: GET"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    headers, multi, status = {}, {}, 0
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.upper().startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
        elif ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().lower(); v = v.strip()
            headers[k] = v                       # last-wins single view
            multi.setdefault(k, []).append(v)    # all occurrences
    return status, headers, multi


def make_probe(label, url, method, origin, sent_credentials):
    status, h, multi = curl_headers(url, method=method, origin=origin)
    return Probe(
        label=label,
        request_origin=origin,
        sent_credentials=sent_credentials,
        status=status,
        acao=h.get("access-control-allow-origin"),
        acao_values=multi.get("access-control-allow-origin"),
        acac=h.get("access-control-allow-credentials"),
        vary=h.get("vary"),
        acam=h.get("access-control-allow-methods"),
        acah=h.get("access-control-allow-headers"),
        body_is_sensitive=None,  # never auto-assume sensitivity
    )


def run(url, delay):
    conditions = [
        ("baseline-no-origin",   "GET",     None),
        ("evil-origin-get",      "GET",     EVIL),
        ("null-origin-get",      "GET",     "null"),
        ("evil-origin-preflight","OPTIONS", EVIL),
    ]
    probes = []
    for i, (label, method, origin) in enumerate(conditions):
        if i > 0:
            time.sleep(delay)  # ROE rate limit
        p = make_probe(label, url, method, origin,
                       sent_credentials=(label != "baseline-no-origin"))
        probes.append(p)
        print("  probe %-22s method=%-7s origin=%-24s -> status=%s acao=%r acac=%r vary=%r"
              % (label, method, origin, p.status, p.acao, p.acac, p.vary), file=sys.stderr)
    return probes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    print("[*] Probing %s (read-only, %.1fs spacing)\n" % (args.url, args.delay), file=sys.stderr)
    probes = run(args.url, args.delay)
    verdicts = analyze(probes)
    print("\n" + render(verdicts))
    print("\nSUMMARY:", summarize(verdicts))


if __name__ == "__main__":
    main()
