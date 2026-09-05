#!/usr/bin/env python3
"""Run the Azimuth in-process test suite.

Usage:
    python run-tests.py                          # run all tests (file backend)
    python run-tests.py sword                    # only tests whose name contains "sword"
    python run-tests.py --db sqlite              # run the whole suite on the sqlite backend
    python run-tests.py --keep-db                # keep the temp test databases for inspection
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.framework import run_tests

if __name__ == "__main__":
    # Parse flags properly: `--db` takes a value, which must NOT also be
    # treated as the positional name filter (it once ran 0/0 tests).
    argv = sys.argv[1:]
    args = []
    keep = False
    db_type = None
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_type = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--keep-db":
            keep = True
        elif not argv[i].startswith("--"):
            args.append(argv[i])
        i += 1
    sys.exit(run_tests(pattern=args[0] if args else None, keep_db=keep, db_type=db_type))
