#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.environ.get("CHATBOT_DB", "chatbot.db")


def timestamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def backup_db(source: str, dest: str) -> None:
    if not os.path.exists(source):
        raise FileNotFoundError(f"Source database not found: {source}")

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a point-in-time SQLite backup for syncing between machines."
    )
    parser.add_argument(
        "--source",
        default=DB_PATH,
        help="Source database path (default: CHATBOT_DB or chatbot.db)",
    )
    parser.add_argument(
        "--dest",
        help="Destination database path",
    )
    args = parser.parse_args()

    dest = args.dest
    if not dest:
        base, ext = os.path.splitext(args.source)
        ext = ext or ".db"
        dest = f"{base}-backup-{timestamp()}{ext}"

    try:
        backup_db(args.source, dest)
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"Error: {exc}")
        return 1

    print(f"Backup created: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
