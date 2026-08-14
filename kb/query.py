#!/usr/bin/env python3
"""Compatibility shim -> `kb query`. Prefer: python3 kb.py query ..."""
import sys
from cli import main

if __name__ == "__main__":
    main(["query", *sys.argv[1:]])
