#!/usr/bin/env python3
"""Run the Azimuth in-process test suite.

Usage:
    python run-tests.py               # run all tests
    python run-tests.py sword         # only tests whose name contains "sword"
    python run-tests.py --keep-db     # keep the temp test databases for inspection
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.framework import run_tests

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep-db" in sys.argv
    sys.exit(run_tests(pattern=args[0] if args else None, keep_db=keep))
