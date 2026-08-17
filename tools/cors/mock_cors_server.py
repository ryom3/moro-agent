#!/usr/bin/env python3
"""Local mock emitting different CORS behaviors per path, to validate the probe.

Paths:
  /wildcard         -> ACAO: * (no ACAC)                [V3 NEEDS_AUTHZ_DATA]
  /reflect-creds    -> reflects Origin + ACAC:true      [V1 EXPLOITABLE, V5 poison]
  /wildcard-creds   -> ACAO:* + ACAC:true (spec deadend)[V2 NOT_EXPLOITABLE]
  /reflect-vary     -> reflects Origin + Vary: Origin    [V5 safe]
  /null             -> allows Origin: null + ACAC:true   [V4 EXPLOITABLE]
  /safe             -> no CORS headers                    [all safe]
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _emit(self):
        origin = self.headers.get("Origin")
        path = self.path
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if path.startswith("/wildcard-creds"):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Credentials", "true")
        elif path.startswith("/wildcard"):
            self.send_header("Access-Control-Allow-Origin", "*")
        elif path.startswith("/reflect-vary"):
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif path.startswith("/reflect-creds"):
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE")
        elif path.startswith("/null"):
            if origin == "null":
                self.send_header("Access-Control-Allow-Origin", "null")
                self.send_header("Access-Control-Allow-Credentials", "true")
        # /safe: nothing
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    do_GET = _emit
    do_OPTIONS = _emit

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    HTTPServer(("127.0.0.1", port), H).serve_forever()
