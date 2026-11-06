"""Durable local SQLite inbox and content-addressed cache.

Global job leasing/fencing and authoritative asset finalization remain backend duties.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_job (
  job_id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'RECEIVED',
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_epoch INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  CHECK (status IN ('RECEIVED','PROCESSING','RETRY_PENDING','READY','FAILED','CANCELLED'))
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_job(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_inbox_expiry ON inbox_job(status, expires_at);
CREATE TABLE IF NOT EXISTS tts_cache (
  fingerprint TEXT PRIMARY KEY,
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
  duration_ms INTEGER NOT NULL CHECK(duration_ms > 0),
  verified_at INTEGER,
  last_used_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_expiry ON tts_cache(expires_at);
CREATE TABLE IF NOT EXISTS model_registry (
  model_id TEXT NOT NULL,
  revision TEXT NOT NULL,
  model_sha256 TEXT NOT NULL,
  local_path TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('DISCOVERED','DOWNLOADING','VERIFIED','ACTIVE','FAILED','RETIRED')),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (model_id, revision)
);
CREATE TABLE IF NOT EXISTS tts_chunk (
  job_id TEXT NOT NULL REFERENCES inbox_job(job_id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED')),
  pcm_path TEXT,
  pcm_sha256 TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (job_id, chunk_index)
);
"""

RETENTION_SECONDS = 30 * 86400
TERMINAL = ("READY", "FAILED", "CANCELLED")
ACTIVE = ("RECEIVED", "PROCESSING", "RETRY_PENDING")


class CollisionError(ValueError):
    """An existing job ID has a different immutable fingerprint."""


class InvalidTransition(ValueError):
    """Requested state transition is not legal."""


class ThreadLocalConnection:
    """Connection facade: each caller thread owns its SQLite connection.

    Keeps existing WorkerStore API compatible; SQLite's check_same_thread
    remains enabled. A connection is created lazily with identical PRAGMAs.
    """

    def __init__(self, path: Path):
        self.path = str(path)
        self.local = threading.local()

    def _current(self):
        connection = getattr(self.local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            self.local.connection = connection
        return connection

    def __getattr__(self, name):
        return getattr(self._current(), name)

    def close(self):
        connection = getattr(self.local, "connection", None)
        if connection is not None:
            connection.close()
            del self.local.connection


class WorkerStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = ThreadLocalConnection(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def receive(self, job_id: str, fingerprint: str, now: int | None = None) -> str:
        if not job_id or not fingerprint:
            raise ValueError("job_id and fingerprint required")
        now = int(time.time()) if now is None else now
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT fingerprint, status FROM inbox_job WHERE job_id=?", (job_id,)
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise CollisionError("job ID reused with a different fingerprint")
                status = row["status"]
            else:
                conn.execute(
                    """INSERT INTO inbox_job(job_id,fingerprint,status,created_at,updated_at,expires_at)
                       VALUES (?,?,'RECEIVED',?,?,?)""",
                    (job_id, fingerprint, now, now, now + RETENTION_SECONDS),
                )
                status = "RECEIVED"
            conn.execute("COMMIT")
            return status
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def transition(self, job_id: str, from_status: str, to_status: str,
                   now: int | None = None) -> bool:
        allowed = {
            "RECEIVED": {"PROCESSING", "CANCELLED"},
            "PROCESSING": {"RETRY_PENDING", "READY", "FAILED", "CANCELLED"},
            "RETRY_PENDING": {"PROCESSING", "FAILED", "CANCELLED"},
        }
        if to_status not in allowed.get(from_status, set()):
            raise InvalidTransition(f"{from_status} -> {to_status}")
        now = int(time.time()) if now is None else now
        cur = self.connection.execute(
            """UPDATE inbox_job
               SET status=?, updated_at=?, attempts=attempts + ?,
                   expires_at=CASE WHEN ? THEN ? ELSE expires_at END
               WHERE job_id=? AND status=?""",
            (to_status, now, int(to_status == "PROCESSING"),
             int(to_status in TERMINAL), now + RETENTION_SECONDS, job_id, from_status),
        )
        return cur.rowcount == 1

    def job(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM inbox_job WHERE job_id=?", (job_id,)
        ).fetchone()

    def save_cache(self, fingerprint: str, object_key: str, sha256: str,
                   size_bytes: int, duration_ms: int,
                   now: int | None = None) -> None:
        if not fingerprint or not object_key or len(sha256) != 64 or size_bytes <= 0 or duration_ms <= 0:
            raise ValueError("invalid cache metadata")
        now = int(time.time()) if now is None else now
        self.connection.execute(
            """INSERT INTO tts_cache(fingerprint,object_key,sha256,size_bytes,
                     duration_ms,verified_at,last_used_at,expires_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                 object_key=excluded.object_key,sha256=excluded.sha256,
                 size_bytes=excluded.size_bytes,duration_ms=excluded.duration_ms,
                 verified_at=excluded.verified_at,last_used_at=excluded.last_used_at,
                 expires_at=excluded.expires_at""",
            (fingerprint, object_key, sha256, size_bytes, duration_ms,
             now, now, now + RETENTION_SECONDS),
        )

    def cache(self, fingerprint: str, now: int | None = None):
        now = int(time.time()) if now is None else now
        return self.connection.execute(
            "SELECT * FROM tts_cache WHERE fingerprint=? AND expires_at>?",
            (fingerprint, now),
        ).fetchone()

    def invalidate_cache(self, fingerprint: str) -> None:
        self.connection.execute("DELETE FROM tts_cache WHERE fingerprint=?", (fingerprint,))

    def expire(self, now: int | None = None, batch_size: int = 500) -> tuple[int, int]:
        """Only completed jobs are purged; published RustFS assets are untouched."""
        if not 1 <= batch_size <= 10000:
            raise ValueError("batch_size must be 1..10000")
        now = int(time.time()) if now is None else now
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            j = conn.execute(
                """DELETE FROM inbox_job WHERE job_id IN (
                       SELECT job_id FROM inbox_job
                       WHERE status IN ('READY','FAILED','CANCELLED') AND expires_at<=?
                       ORDER BY expires_at LIMIT ?)""", (now, batch_size)
            ).rowcount
            c = conn.execute(
                """DELETE FROM tts_cache WHERE fingerprint IN (
                       SELECT fingerprint FROM tts_cache WHERE expires_at<=?
                       ORDER BY expires_at LIMIT ?)""", (now, batch_size)
            ).rowcount
            conn.execute("COMMIT")
            return j, c
        except BaseException:
            conn.execute("ROLLBACK")
            raise
