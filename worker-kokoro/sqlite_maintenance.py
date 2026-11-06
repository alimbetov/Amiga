"""Offline SQLite maintenance: consistent backup, integrity check and restore.

Backups use SQLite's online backup API, avoiding raw copies of a live WAL
database. Restore is deliberately offline-only; require the Worker stopped.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from instance_lock import InstanceLock, WorkerAlreadyRunning


def integrity_check(database: Path) -> str:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(path)
    # mode=ro refuses to accidentally create an empty source DB.
    uri = "file:" + str(path.resolve()).replace("?", "%3F") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as source:
        result = source.execute("PRAGMA integrity_check").fetchone()[0]
    return result


def backup_database(database: Path, target: Path) -> Path:
    database = Path(database)
    target = Path(target)
    if not database.is_file():
        raise FileNotFoundError(database)
    if database.resolve() == target.resolve():
        raise ValueError("backup source and destination must differ")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + ".partial")
    stage.unlink(missing_ok=True)
    try:
        source_uri = "file:" + str(database.resolve()).replace("?", "%3F") + "?mode=ro"
        source = sqlite3.connect(source_uri, uri=True, timeout=30)
        try:
            target_conn = sqlite3.connect(stage)
            try:
                source.backup(target_conn, pages=100, sleep=0.05)
                target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                target_conn.close()
        finally:
            source.close()
        if integrity_check(stage) != "ok":
            raise RuntimeError("backup failed SQLite integrity_check")
        with stage.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(stage, target)
        return target
    finally:
        stage.unlink(missing_ok=True)


def restore_database(source: Path, destination: Path, *, stopped: bool = False) -> Path:
    if not stopped:
        raise ValueError("restore requires explicit stopped=True")
    source = Path(source)
    destination = Path(destination)
    if integrity_check(source) != "ok":
        raise RuntimeError("backup is corrupt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Require the operator to stop the Worker, but also acquire its OS lock.
    with InstanceLock(destination.parent):
        if destination.exists() or Path(str(destination) + "-wal").exists():
            raise FileExistsError("restore refuses to overwrite existing SQLite state")
        return backup_database(source, destination)


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    check = subs.add_parser("check")
    check.add_argument("--db", type=Path, required=True)
    backup = subs.add_parser("backup")
    backup.add_argument("--db", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    restore = subs.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--db", type=Path, required=True)
    restore.add_argument("--worker-stopped", action="store_true")
    args = parser.parse_args()
    if args.command == "check":
        print(integrity_check(args.db))
    elif args.command == "backup":
        print(backup_database(args.db, args.output))
    else:
        print(restore_database(args.input, args.db, stopped=args.worker_stopped))


if __name__ == "__main__":
    main()
