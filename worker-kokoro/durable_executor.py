"""One-owner SQLite durable local TTS execution queue.

POST /v1/tts/jobs stores an immutable, secret-free request *before* rendering.
Executor replays after kill; WAV checkpoints survive and are hash-validated.
Remote S3 needs a server-side profileId; inline credentials must never be stored.
Backend distributed leases and authoritative finalization are separate.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time


MAX_QUEUE = 500
MAX_ATTEMPTS = 3


def stable_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


class DurableQueue:
    def __init__(self, renderer, error_type, validate_request, profiles=None, *,
                 max_queue=MAX_QUEUE, max_attempts=MAX_ATTEMPTS):
        self.renderer = renderer
        self.ApiError = error_type
        self.validate_request = validate_request
        self.lock = renderer.lock
        self.db = renderer.db
        self.profiles = profiles or {}
        self.max_queue = max_queue
        self.max_attempts = max_attempts
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.db.execute("""CREATE TABLE IF NOT EXISTS durable_jobs (
            job_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
               ('PENDING','PROCESSING','RETRY_PENDING','READY','FAILED')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            last_error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_durable_queue ON durable_jobs(status,next_attempt_at)")
        self.db.execute("""UPDATE durable_jobs
                           SET status='RETRY_PENDING',next_attempt_at=0
                           WHERE status='PROCESSING'""")

    def _resolve(self, safe):
        dest = safe["destination"]
        if dest["type"] == "local":
            request = dict(safe)
        else:
            profile = self.profiles.get(dest["profileId"])
            if not isinstance(profile, dict):
                raise self.ApiError(503, "STORAGE_PROFILE_UNAVAILABLE")
            if not {"endpoint", "bucket", "accessKey", "secretKey"}.issubset(profile):
                raise self.ApiError(503, "STORAGE_PROFILE_INVALID")
            configured = {**profile, "type": "s3"}
            if "retention" in dest:
                configured["retention"] = dest["retention"]
            request = {**safe, "destination": configured}
        self.validate_request(request)
        return request

    def _safe_request(self, data):
        if not isinstance(data, dict):
            raise self.ApiError(400, "INVALID_BODY")
        dest = data.get("destination", {"type": "local"})
        if not isinstance(dest, dict):
            raise self.ApiError(400, "INVALID_DESTINATION")
        if dest.get("type") == "local":
            if set(dest) - {"type", "retention"}:
                raise self.ApiError(400, "INVALID_DESTINATION")
        elif dest.get("type") == "profile":
            if set(dest) - {"type", "profileId", "retention"}:
                raise self.ApiError(400, "INVALID_PROFILE_DESTINATION")
            if not isinstance(dest.get("profileId"), str) or dest["profileId"] not in self.profiles:
                raise self.ApiError(422, "UNKNOWN_STORAGE_PROFILE")
        else:
            raise self.ApiError(422, "DURABLE_REQUIRES_STORAGE_PROFILE")
        keys = ("jobId", "engine", "locale", "voice", "text", "fileName")
        if any(k not in data for k in keys):
            raise self.ApiError(400, "MISSING_FIELDS")
        if set(data) - set(keys) - {"destination"}:
            raise self.ApiError(400, "UNEXPECTED_FIELDS")
        safe = {k: data[k] for k in keys}
        safe["destination"] = dest
        self._resolve(safe)
        return safe

    def submit(self, request):
        safe = self._safe_request(request)
        identity = stable_digest(safe)
        job_id = safe["jobId"]
        now = int(time.time())
        with self.lock:
            existing = self.db.execute(
                "SELECT fingerprint,status FROM durable_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != identity:
                    raise self.ApiError(409, "JOB_ID_COLLISION")
                status = existing["status"]
                return (200 if status == "READY" else 202), self.status(job_id)
            pending = self.db.execute(
                "SELECT COUNT(*) FROM durable_jobs WHERE status IN ('PENDING','PROCESSING','RETRY_PENDING')"
            ).fetchone()[0]
            if pending >= self.max_queue:
                raise self.ApiError(429, "QUEUE_FULL")
            self.db.execute(
                """INSERT INTO durable_jobs
                   (job_id,fingerprint,request_json,status,created_at,updated_at)
                   VALUES (?,?,?,'PENDING',?,?)""",
                (job_id, identity, json.dumps(safe, ensure_ascii=False), now, now))
        self.wake.set()
        return 202, {"jobId": job_id, "status": "PENDING", "statusUrl": "/v1/tts/jobs/" + job_id}

    def status(self, job_id):
        row = self.db.execute(
            "SELECT job_id,status,attempts,result_json,last_error FROM durable_jobs WHERE job_id=?",
            (job_id,)).fetchone()
        if not row:
            raise self.ApiError(404, "JOB_NOT_FOUND")
        result = {"jobId": job_id, "status": row["status"], "attempts": row["attempts"]}
        if row["status"] == "READY" and row["result_json"]:
            result["result"] = json.loads(row["result_json"])
        if row["status"] == "FAILED":
            result["error"] = row["last_error"]
        return result

    def run_once(self, now=None):
        now = int(time.time()) if now is None else now
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM durable_jobs WHERE status IN ('PENDING','RETRY_PENDING')
                     AND next_attempt_at<=? ORDER BY created_at,job_id LIMIT 1""",
                (now,)).fetchone()
            if not row:
                return False
            job_id = row["job_id"]
            attempt = row["attempts"] + 1
            self.db.execute(
                """UPDATE durable_jobs
                   SET status='PROCESSING',attempts=?,updated_at=?
                   WHERE job_id=?""", (attempt, now, job_id))
            safe = json.loads(row["request_json"])
            identity = row["fingerprint"]
        try:
            resolved = self._resolve(safe)
            _, result = self.renderer.render(
                resolved, request_identity=identity,
                trusted_profile=safe["destination"]["type"] == "profile")
        except Exception as exc:
            # Return public error *codes* only; never persist raw endpoint errors
            # or text/secret data in the status table.
            code = exc.code if isinstance(exc, self.ApiError) else "INTERNAL_WORKER_ERROR"
            retryable = isinstance(exc, ApiError) and exc.status in (502, 503, 504)
            final = attempt >= self.max_attempts or not retryable
            with self.lock:
                self.db.execute(
                    """UPDATE durable_jobs SET status=?,last_error=?,next_attempt_at=?,
                       updated_at=? WHERE job_id=?""",
                    ("FAILED" if final else "RETRY_PENDING", code,
                     now + min(300, 2 ** attempt) if not final else 0,
                     int(time.time()), job_id))
                if final:
                    self.renderer.store.transition(job_id, "RETRY_PENDING", "FAILED")
            return True
        with self.lock:
            self.db.execute(
                """UPDATE durable_jobs SET status='READY',result_json=?,
                   last_error=NULL,updated_at=? WHERE job_id=?""",
                (json.dumps(result), int(time.time()), job_id))
        return True

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                processed = self.run_once()
            except (sqlite3.Error, ValueError):
                processed = False
            if not processed:
                self.wake.wait(timeout=2)
                self.wake.clear()

    def start(self):
        if self.thread is not None:
            raise RuntimeError("queue already running")
        self.thread = threading.Thread(target=self._loop, daemon=True, name="amiga-durable-jobs")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
