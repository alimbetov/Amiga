"""Local development HTTP TTS endpoint with optional S3/RustFS delivery.

This is an isolated smoke API, not Backend authoritative leasing/production delivery.
Authentication is a Bearer token. Storage secrets can be provided inline only when
AMIGA_ALLOW_INLINE_STORAGE=1; Base64 is encoding, not encryption.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from retention import parse_retention, expires_at, RetentionError
from audio_pipeline import AudioPipelineError
from amiga_worker.db import WorkerStore, RETENTION_SECONDS, CollisionError
from amiga_worker.janitor import SQLiteJanitor
from durable_chunks import ChunkCheckpoint
from engine_supervisor import EngineSupervisor, EngineUnavailable
from health_info import worker_health_info
from s3_delivery import ensure_uploaded, RemoteObjectCollision, RemoteIntegrityError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY = 32768
SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$")
VOICE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


def decode_secret(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ApiError(400, "INVALID_SECRET")
    if value.startswith("base64:"):
        try:
            return base64.b64decode(value[7:], validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            raise ApiError(400, "INVALID_BASE64_SECRET") from None
    return value


def validate(req: object) -> dict:
    if not isinstance(req, dict):
        raise ApiError(400, "INVALID_BODY")
    required = ("jobId", "engine", "locale", "voice", "text", "fileName")
    if any(not isinstance(req.get(k), str) or not req[k] for k in required):
        raise ApiError(400, "MISSING_FIELDS")
    if req["engine"] not in ("kokoro", "piper"):
        raise ApiError(422, "UNKNOWN_ENGINE")
    if not SAFE.fullmatch(req["jobId"]) or not SAFE.fullmatch(req["fileName"]):
        raise ApiError(400, "UNSAFE_IDENTIFIER")
    if not req["fileName"].endswith((".wav", ".mp3")):
        raise ApiError(422, "WAV_OR_MP3_REQUIRED")
    if not VOICE.fullmatch(req["voice"]) or not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", req["locale"]):
        raise ApiError(400, "INVALID_VOICE_LOCALE")
    if len(req["text"]) > 2000 or not req["text"].strip():
        raise ApiError(400, "INVALID_TEXT")
    if req["engine"] == "kokoro" and req["locale"] not in ("en-US", "en-GB"):
        raise ApiError(422, "KOKORO_LOCAL_ADAPTER_ENGLISH_ONLY")
    if req["engine"] == "piper" and not req["voice"].startswith(req["locale"].replace("-", "_") + "-"):
        raise ApiError(422, "PIPER_VOICE_LOCALE_MISMATCH")
    destination = req.get("destination", {"type": "local"})
    if not isinstance(destination, dict) or destination.get("type") not in ("local", "s3"):
        raise ApiError(400, "INVALID_DESTINATION")
    if destination["type"] == "s3":
        keys = ("endpoint", "bucket", "accessKey", "secretKey")
        if any(not isinstance(destination.get(k), str) or not destination[k] for k in keys):
            raise ApiError(400, "MISSING_STORAGE_FIELDS")
        endpoint = urlsplit(destination["endpoint"])
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ("", "/"):
            raise ApiError(400, "INVALID_STORAGE_ENDPOINT")
        if not SAFE.fullmatch(destination["bucket"]):
            raise ApiError(400, "INVALID_BUCKET")
        if destination.get("prefix", "") and (not isinstance(destination["prefix"], str) or not re.fullmatch(r"[a-zA-Z0-9_/-]{1,150}", destination["prefix"]) or ".." in destination["prefix"]):
            raise ApiError(400, "INVALID_PREFIX")
    try:
        ttl_seconds, retention_prefix = parse_retention(destination.get("retention"))
    except RetentionError as exc:
        raise ApiError(422, exc.code) from None
    return {**{k: req[k] for k in required}, "destination": destination,
            "ttlSeconds": ttl_seconds, "retentionPrefix": retention_prefix}


def digest_request(req: dict) -> str:
    """Hash credentials rather than keeping request credentials in SQLite."""
    encoded = json.dumps(req, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class Renderer:
    def __init__(self, root: Path, token: str, allowed_hosts: set[str], allow_inline: bool):
        if len(token) < 20:
            raise ValueError("AMIGA_TTS_API_TOKEN must contain at least 20 characters")
        self.root = root.resolve()
        self.token = token
        self.allowed_hosts = allowed_hosts
        self.allow_inline = allow_inline
        self.out = self.root / "local-http-output"
        self.out.mkdir(exist_ok=True)
        self.store = WorkerStore(self.out / "jobs.sqlite")
        self.db = self.store.connection
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "expires_at" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN expires_at INTEGER")
        if "created_at" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN created_at INTEGER")
        # Existing rows are dated at migration so they remain replay-safe for 30 days.
        self.db.execute("UPDATE jobs SET created_at=? WHERE created_at IS NULL", (int(time.time()),))
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_http_jobs_created ON jobs(created_at)")
        self.db.commit()
        self.lock = threading.Lock()
        self.checkpoints = ChunkCheckpoint(self.store, self.out / "chunks")
        self.checkpoints.recover_interrupted()
        self.metrics = {"requests_total": 0, "render_success_total": 0,
                        "render_failed_total": 0, "replay_total": 0}
        self.metrics_lock = threading.Lock()
        self.engine_supervisor = None

    def health_info(self) -> dict:
        return worker_health_info(self.root, self.engine_supervisor)

    def tts_readiness(self) -> dict:
        if self.engine_supervisor is None:
            return {"status": "NOT_READY", "engines": {},
                    "reason": "MODEL_SUPERVISOR_NOT_STARTED"}
        return self.engine_supervisor.report()

    def readiness(self) -> dict:
        engines = {}
        for engine in ("kokoro", "piper"):
            root = self.root / (".venv-" + engine)
            engines[engine] = ((root / "Scripts" / "python.exe").is_file() or
                               (root / "bin" / "python").is_file())
        codecs = {"ffmpeg": bool(shutil.which("ffmpeg")),
                  "ffprobe": bool(shutil.which("ffprobe"))}
        ready = any(engines.values()) and all(codecs.values())
        return {"status": "READY" if ready else "NOT_READY", "engines": engines,
                "codecs": codecs, "note": "dev readiness only; not model warmup"}

    def observe(self, outcome: str) -> None:
        with self.metrics_lock:
            self.metrics["requests_total"] += 1
            self.metrics[outcome] += 1

    def metrics_snapshot(self) -> dict:
        with self.metrics_lock:
            return dict(self.metrics)

    def cleanup_expired_local(self, now: int | None = None) -> int:
        """Delete expired local audio only. RustFS deletion is owned by S3 lifecycle."""
        now = int(time.time()) if now is None else now
        removed = 0
        with self.lock:
            rows = self.db.execute(
                "SELECT job_id,result FROM jobs WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)
            ).fetchall()
            for job_id, raw in rows:
                result = json.loads(raw)
                local_path = result.get("localPath")
                if local_path:
                    file = Path(local_path).resolve()
                    directory = (self.out / job_id).resolve()
                    if file.parent == directory and file.is_file():
                        file.unlink()
                        removed += 1
                # Keep expired job IDs as tombstones for 409 and replay safety.
        return removed

    def purge_completed_metadata(self, now: int | None = None, batch_size: int = 500) -> int:
        """Bound SQLite growth: remove completed HTTP replay records after 30 days.

        Keeps future-dated TTL tombstones until expiry. Does not delete RustFS data.
        """
        if not 1 <= batch_size <= 10000:
            raise ValueError("invalid batch size")
        now = int(time.time()) if now is None else now
        with self.lock:
            removed = self.db.execute(
                """DELETE FROM jobs WHERE job_id IN (
                   SELECT job_id FROM jobs WHERE created_at<=?
                     AND (expires_at IS NULL OR expires_at<=?)
                   ORDER BY created_at LIMIT ?)""",
                (now - 30 * 86400, now, batch_size)
            ).rowcount
            # Durable accepted requests have their own terminal timestamps.
            # Never purge queued/inflight jobs, even if they are older than 30d.
            exists = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_jobs'"
            ).fetchone()
            if exists:
                self.db.execute(
                    """DELETE FROM durable_jobs WHERE job_id IN (
                       SELECT job_id FROM durable_jobs
                       WHERE status IN ('READY','FAILED') AND updated_at<=?
                       ORDER BY updated_at LIMIT ?)""",
                    (now - RETENTION_SECONDS, batch_size))
            self.db.commit()
            if removed:
                self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            return removed

    def purge_orphan_chunks(self, now: int | None = None, batch_size: int = 100) -> int:
        """Only remove old chunk directories after job is terminal or purged."""
        now = int(time.time()) if now is None else now
        removed = 0
        with self.lock:
            root = self.checkpoints.work_root
            if not root.is_dir():
                return 0
            for directory in root.iterdir():
                if removed >= batch_size:
                    break
                if not directory.is_dir() or directory.is_symlink():
                    continue
                if not SAFE.fullmatch(directory.name):
                    continue
                if directory.stat().st_mtime > now - RETENTION_SECONDS:
                    continue
                job = self.store.job(directory.name)
                if job is not None and job["status"] not in ("READY", "FAILED", "CANCELLED"):
                    continue
                self.checkpoints.cleanup_after_delivery(directory.name)
                removed += 1
        return removed

    def render(self, req: object, *, request_identity: str | None = None,
               trusted_profile: bool = False) -> tuple[int, dict]:
        data = validate(req)
        destination = data["destination"]
        if destination["type"] == "s3":
            if not self.allow_inline and not trusted_profile:
                raise ApiError(403, "INLINE_STORAGE_DISABLED")
            host = urlsplit(destination["endpoint"]).hostname.lower()
            if host not in self.allowed_hosts:
                raise ApiError(403, "STORAGE_HOST_NOT_ALLOWED")
            if urlsplit(destination["endpoint"]).scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
                # Internal remote HTTP must be explicitly acknowledged via environment flag.
                if os.environ.get("AMIGA_ALLOW_INSECURE_STORAGE") != "1":
                    raise ApiError(403, "STORAGE_TLS_REQUIRED")
        fingerprint = request_identity or digest_request(data)
        with self.lock:
            row = self.db.execute("SELECT fingerprint,result,expires_at FROM jobs WHERE job_id=?", (data["jobId"],)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ApiError(409, "JOB_ID_COLLISION")
                if row[2] is not None and row[2] <= int(time.time()):
                    raise ApiError(410, "JOB_EXPIRED_USE_NEW_JOB_ID")
                result = json.loads(row[1])
                local_path = result.get("localPath")
                if local_path and not Path(local_path).is_file():
                    raise ApiError(410, "LOCAL_FILE_MISSING_USE_NEW_JOB_ID")
                if request_identity is not None:
                    self.db.execute(
                        """UPDATE durable_jobs SET status='READY',result_json=?,
                           last_error=NULL,updated_at=?
                           WHERE job_id=? AND fingerprint=?
                           AND status IN ('PROCESSING','RETRY_PENDING')""",
                        (json.dumps(result), int(time.time()), data["jobId"], fingerprint))
                return 200, {**result, "replayed": True}
            try:
                state = self.store.receive(data["jobId"], fingerprint)
            except CollisionError:
                raise ApiError(409, "JOB_ID_COLLISION") from None
            if state not in ("RECEIVED", "RETRY_PENDING"):
                raise ApiError(409, "JOB_NOT_RETRYABLE")
            if not self.store.transition(data["jobId"], state, "PROCESSING"):
                raise ApiError(409, "JOB_STATE_CHANGED")
        try:
            engine = data["engine"]
            py = self.root / (".venv-" + engine) / "Scripts" / "python.exe"
            if not py.is_file():
                py = self.root / (".venv-" + engine) / "bin" / "python"
            if not py.is_file():
                raise ApiError(503, "ENGINE_ENV_NOT_FOUND")
            directory = self.out / data["jobId"]
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / data["fileName"]
            chunk_count = 1
            media_type = "audio/mpeg" if target.suffix == ".mp3" else "audio/wav"

            def render_one(piece: str, output_wav: Path) -> None:
                if self.engine_supervisor is not None:
                    try:
                        self.engine_supervisor.render(engine, data["voice"], piece, output_wav)
                        return
                    except EngineUnavailable:
                        raise ApiError(503, "ENGINE_NOT_READY_OR_FAILED") from None
                # Isolated smoke adapter. A production worker must keep the
                # model warm and persist chunk checkpoint states in SQLite.
                path = output_wav.parent / ("inference-" + output_wav.stem)
                path.mkdir(parents=True, exist_ok=True)
                command = [str(py), str(self.root / "local_smoke.py"), "run",
                           "--engine", engine, "--voice", data["voice"], "--text", piece,
                           "--data-dir", str(self.root / "local-tts-data"),
                           "--output-dir", str(path)]
                try:
                    subprocess.run(command, cwd=self.root, check=True,
                                   capture_output=True, timeout=300)
                except subprocess.TimeoutExpired:
                    raise ApiError(504, "INFERENCE_TIMEOUT") from None
                except subprocess.CalledProcessError:
                    raise ApiError(502, "INFERENCE_FAILED") from None
                source = path / (engine + ".wav")
                if not source.is_file() or source.stat().st_size < 100:
                    raise ApiError(502, "INVALID_WAV_OUTPUT")
                source.replace(output_wav)
                # Avoid leaving per-chunk diagnostic JSON and directories.
                for leftover in path.iterdir():
                    if leftover.is_file():
                        leftover.unlink()
                path.rmdir()

            if media_type == "audio/mpeg":
                try:
                    stats = self.checkpoints.assemble_mp3(data["jobId"], data["text"], target,
                                                          render_one, identity=fingerprint)
                    chunk_count = stats["chunkCount"]
                except AudioPipelineError:
                    raise ApiError(502, "MP3_PIPELINE_FAILED") from None
            else:
                render_one(data["text"], directory / "legacy.wav")
                (directory / "legacy.wav").replace(target)
            with target.open("rb") as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                hash_value = digest.hexdigest()
            expiry = expires_at(data["ttlSeconds"])
            result = {"jobId": data["jobId"], "engine": engine, "fileName": data["fileName"],
                      "sha256": hash_value, "sizeBytes": target.stat().st_size,
                      "ttlSeconds": data["ttlSeconds"], "expiresAt": expiry,
                      "contentType": media_type, "chunkCount": chunk_count,
                      "replayed": False}
            if destination["type"] == "local":
                result["localPath"] = str(target)
            else:
                try:
                    import boto3
                    from botocore.config import Config
                    client = boto3.client("s3", endpoint_url=destination["endpoint"],
                                          region_name=destination.get("region", "us-east-1"),
                                          aws_access_key_id=decode_secret(destination["accessKey"]),
                                          aws_secret_access_key=decode_secret(destination["secretKey"]),
                                          config=Config(signature_version="s3v4",
                                                        s3={"addressing_style": "path"},
                                                        connect_timeout=5, read_timeout=20,
                                                        retries={"max_attempts": 2}))
                    prefix = destination.get("prefix", "tts").strip("/")
                    key = "/".join(p for p in (prefix, data["retentionPrefix"], data["jobId"], data["fileName"]) if p)
                    ensure_uploaded(client, destination["bucket"], key, target,
                                    hash_value, media_type)
                except ApiError:
                    raise
                except RemoteObjectCollision:
                    raise ApiError(409, "REMOTE_OBJECT_COLLISION") from None
                except RemoteIntegrityError:
                    raise ApiError(502, "REMOTE_INTEGRITY_CHECK_FAILED") from None
                except Exception:
                    raise ApiError(502, "S3_UPLOAD_FAILED") from None
                result.update({"bucket": destination["bucket"], "objectKey": key, "verified": True})
            # One transaction: the delivered object and READY status
            # cannot diverge locally after power loss.
            now = int(time.time())
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT INTO jobs(job_id,fingerprint,result,expires_at,created_at) VALUES (?,?,?,?,?)",
                (data["jobId"], fingerprint, json.dumps(result), expiry, now))
            updated = self.db.execute(
                """UPDATE inbox_job SET status='READY',updated_at=?,expires_at=?
                   WHERE job_id=? AND status='PROCESSING'""",
                (now, now + RETENTION_SECONDS, data["jobId"])).rowcount
            if updated != 1:
                raise ApiError(409, "INBOX_STATE_CONFLICT")
            if request_identity is not None:
                done = self.db.execute(
                    """UPDATE durable_jobs SET status='READY',result_json=?,
                       last_error=NULL,updated_at=? WHERE job_id=?
                       AND status='PROCESSING' AND fingerprint=?""",
                    (json.dumps(result), now, data["jobId"], fingerprint)).rowcount
                if done != 1:
                    raise ApiError(409, "DURABLE_STATE_CONFLICT")
            self.db.execute("COMMIT")
            if media_type == "audio/mpeg":
                try:
                    self.checkpoints.cleanup_after_delivery(data["jobId"])
                except OSError:
                    pass  # Optional local disk cleanup; janitor can retry.
            return 201, result
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            # A killed process bypasses this handler; startup recovery
            # requeues durable PROCESSING jobs and preserves verified WAV.
            self.store.transition(data["jobId"], "PROCESSING", "RETRY_PENDING")
            raise


def make_handler(renderer: Renderer, queue=None):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health/live":
                return self.send_json(200, {"status": "UP"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + renderer.token):
                return self.send_json(401, {"error": "UNAUTHORIZED"})
            if self.path in ("/TTS/ready", "/tts/ready", "/tss/ready"):
                report = renderer.tts_readiness()
                return self.send_json(200 if report["status"] == "READY" else 503, report)
            if self.path == "/health/info":
                return self.send_json(200, renderer.health_info())
            if self.path == "/health/ready":
                report = renderer.readiness()
                return self.send_json(200 if report["status"] == "READY" else 503, report)
            if self.path == "/metrics":
                return self.send_json(200, renderer.metrics_snapshot())
            if self.path.startswith("/v1/tts/jobs/") and queue is not None:
                job_id = self.path[len("/v1/tts/jobs/"):]
                if not SAFE.fullmatch(job_id):
                    return self.send_json(400, {"error": "INVALID_JOB_ID"})
                try:
                    with renderer.lock:
                        result = queue.status(job_id)
                    return self.send_json(200, result)
                except ApiError as exc:
                    return self.send_json(exc.status, {"error": exc.code})
            return self.send_json(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            if self.path not in ("/v1/tts/render", "/v1/tts/jobs"):
                return self.send_json(404, {"error": "NOT_FOUND"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + renderer.token):
                return self.send_json(401, {"error": "UNAUTHORIZED"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ApiError(413, "INVALID_BODY_LENGTH")
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
                if self.path == "/v1/tts/jobs":
                    if queue is None:
                        raise ApiError(503, "QUEUE_NOT_RUNNING")
                    status, result = queue.submit(payload)
                else:
                    status, result = renderer.render(payload)
                if self.path == "/v1/tts/render":
                    renderer.observe("replay_total" if result.get("replayed") else "render_success_total")
                self.send_json(status, result)
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "INVALID_JSON"})
            except ApiError as exc:
                renderer.observe("render_failed_total")
                self.send_json(exc.status, {"error": exc.code})

        def log_message(self, fmt, *args):
            # Do not log raw HTTP bodies or Authorization header.
            return
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("AMIGA_TTS_API_TOKEN", "")
    hosts = {v.strip().lower() for v in os.environ.get("AMIGA_STORAGE_ALLOWED_HOSTS", "").split(",") if v.strip()}
    renderer = Renderer(Path(__file__).resolve().parent, token, hosts,
                        os.environ.get("AMIGA_ALLOW_INLINE_STORAGE") == "1")
    renderer.engine_supervisor = EngineSupervisor(renderer.root)
    renderer.engine_supervisor.start()
    def sweeper():
        while not httpd_sweep_stop.wait(60):
            try:
                renderer.cleanup_expired_local()
                renderer.purge_completed_metadata()
                with renderer.lock:
                    SQLiteJanitor(renderer.store, batch_size=500,
                                  max_batches=4).run_once()
                renderer.purge_orphan_chunks()
            except (OSError, sqlite3.Error, ValueError):
                # Keep the service alive; expired metadata remains retryable.
                pass

    from durable_executor import DurableQueue
    profiles = json.loads(os.environ.get("AMIGA_S3_PROFILES_JSON", "{}"))
    if not isinstance(profiles, dict):
        raise ValueError("AMIGA_S3_PROFILES_JSON must be an object")
    queue = DurableQueue(renderer, ApiError, validate, profiles=profiles)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(renderer, queue))
    queue.start()
    httpd_sweep_stop = threading.Event()
    sweep_thread = threading.Thread(target=sweeper, daemon=True, name="amiga-ttl-sweep")
    sweep_thread.start()
    print(f"Amiga local TTS on http://{args.host}:{args.port} (local acceptance only)", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd_sweep_stop.set()
        queue.stop()
        renderer.engine_supervisor.stop()
        sweep_thread.join(timeout=2)
        httpd.server_close()


if __name__ == "__main__":
    main()
