"""Bounded background SQLite maintenance for a single worker database.

Retention: terminal Inbox and cache metadata 30 days; active jobs and model
registry remain untouched. Does not delete RustFS assets.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .db import WorkerStore

LOG = logging.getLogger(__name__)


class SQLiteJanitor:
    def __init__(self, store: WorkerStore, interval_seconds: int = 3600,
                 batch_size: int = 500, max_batches: int = 20):
        if interval_seconds < 1 or not (1 <= max_batches <= 1000):
            raise ValueError("invalid janitor schedule")
        self.store = store
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.max_batches = max_batches

    def run_once(self, now: int | None = None) -> tuple[int, int]:
        """Bound runtime/lock duration; each batch commits independently."""
        total_jobs = total_cache = 0
        for _ in range(self.max_batches):
            jobs, cache = self.store.expire(now=now, batch_size=self.batch_size)
            total_jobs += jobs
            total_cache += cache
            if jobs < self.batch_size and cache < self.batch_size:
                break
        # TRUNCATE checkpoint can block readers; use passive, best-effort WAL checkpoint.
        self.store.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return total_jobs, total_cache

    def serve(self, stop: threading.Event) -> None:
        """Call only from the worker's SQLite-owner thread/process."""
        while not stop.is_set():
            try:
                removed = self.run_once()
                LOG.info("SQLite janitor removed inbox=%s cache=%s", *removed)
            except sqlite3.Error:
                LOG.exception("SQLite janitor retry on next interval")
            stop.wait(self.interval_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = WorkerStore(args.db)
    try:
        janitor = SQLiteJanitor(store, args.interval)
        if args.once:
            print("purged inbox/cache:", janitor.run_once())
        else:
            stop = threading.Event()
            try:
                janitor.serve(stop)
            except KeyboardInterrupt:
                stop.set()
    finally:
        store.close()


if __name__ == "__main__":
    main()
