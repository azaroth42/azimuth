#!/usr/bin/env python3
"""Migrate a file-based world (db/*.json) into the SQLite backend.

Usage:
    python run-migrate-sqlite.py [--db-dir db] [--sqlite-path db/azimuth.db]

One-way: the JSON files are read, never deleted. To switch the server over,
set AZIMUTH_DB_TYPE=sqlite (and AZIMUTH_SQLITE_PATH if non-default) in .env
*after* running this. The re-import is idempotent -- it upserts by id, so
re-running after further play on the file backend re-syncs the database.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dotenv

from azimuth.persistence import SqliteStorage

dotenv.load_dotenv()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-dir", default="db")
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv(
            "AZIMUTH_SQLITE_PATH", os.path.join("db", "azimuth.db")
        ),
    )
    args = parser.parse_args()

    if not os.path.isdir(args.db_dir):
        sys.exit(f"file db dir {args.db_dir!r} does not exist")

    db = SqliteStorage(args.sqlite_path)
    count = 0
    try:
        for fn in sorted(os.listdir(args.db_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(args.db_dir, fn)) as fh:
                doc = json.load(fh)
            if "id" not in doc:
                # The players file is a username->id map with no id of its
                # own; the file backend addresses it by filename.
                doc["id"] = fn[: -len(".json")]
            db.save(doc)
            count += 1
        print(f"Migrated {count} documents from {args.db_dir}/ into {args.sqlite_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
