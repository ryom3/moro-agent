#!/usr/bin/env python3
"""Compatibility shim -> `kb ingest`. Prefer: python3 kb.py ingest ..."""
import sys
from cli import main

if __name__ == "__main__":
    main(["ingest", *sys.argv[1:]])
