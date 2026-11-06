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
import sqlite3
import subprocess
import threading
import time
from retention import parse_retention, expires_at, RetentionError
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
    if not req["fileName"].endswith(".wav"):
        raise ApiError(422, "WAV_ONLY")
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
        self.db = sqlite3.connect(self.out / "jobs.sqlite", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "expires_at" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN expires_at INTEGER")
        self.db.commit()
        self.lock = threading.Lock()

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

    def render(self, req: object) -> tuple[int, dict]:
        data = validate(req)
        destination = data["destination"]
        if destination["type"] == "s3":
            if not self.allow_inline:
                raise ApiError(403, "INLINE_STORAGE_DISABLED")
            host = urlsplit(destination["endpoint"]).hostname.lower()
            if host not in self.allowed_hosts:
                raise ApiError(403, "STORAGE_HOST_NOT_ALLOWED")
            if data["ttlSeconds"] is not None and os.environ.get("AMIGA_S3_LIFECYCLE_CONFIGURED") != "1":
                raise ApiError(409, "S3_LIFECYCLE_NOT_CONFIGURED")
            if urlsplit(destination["endpoint"]).scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
                # Internal remote HTTP must be explicitly acknowledged via environment flag.
                if os.environ.get("AMIGA_ALLOW_INSECURE_STORAGE") != "1":
                    raise ApiError(403, "STORAGE_TLS_REQUIRED")
        fingerprint = digest_request(data)
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
                return 200, {**result, "replayed": True}
            engine = data["engine"]
            py = self.root / (".venv-" + engine) / "Scripts" / "python.exe"
            if not py.is_file():
                py = self.root / (".venv-" + engine) / "bin" / "python"
            if not py.is_file():
                raise ApiError(503, "ENGINE_ENV_NOT_FOUND")
            directory = self.out / data["jobId"]
            directory.mkdir(parents=True, exist_ok=True)
            command = [str(py), str(self.root / "local_smoke.py"), "run",
                       "--engine", engine, "--voice", data["voice"], "--text", data["text"],
                       "--data-dir", str(self.root / "local-tts-data"),
                       "--output-dir", str(directory)]
            try:
                subprocess.run(command, cwd=self.root, check=True, capture_output=True, timeout=300)
            except subprocess.TimeoutExpired:
                raise ApiError(504, "INFERENCE_TIMEOUT") from None
            except subprocess.CalledProcessError:
                raise ApiError(502, "INFERENCE_FAILED") from None
            source = directory / (engine + ".wav")
            if not source.is_file() or source.stat().st_size < 100:
                raise ApiError(502, "INVALID_WAV_OUTPUT")
            target = directory / data["fileName"]
            if source != target:
                source.replace(target)
            with target.open("rb") as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                hash_value = digest.hexdigest()
            expiry = expires_at(data["ttlSeconds"])
            result = {"jobId": data["jobId"], "engine": engine, "fileName": data["fileName"],
                      "sha256": hash_value, "sizeBytes": target.stat().st_size,
                      "ttlSeconds": data["ttlSeconds"], "expiresAt": expiry, "replayed": False}
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
                    with target.open("rb") as stream:
                        client.put_object(Bucket=destination["bucket"], Key=key, Body=stream,
                                          ContentLength=target.stat().st_size, ContentType="audio/wav",
                                          Metadata={"sha256": hash_value})
                    head = client.head_object(Bucket=destination["bucket"], Key=key)
                    if head["ContentLength"] != target.stat().st_size or head.get("Metadata", {}).get("sha256") != hash_value:
                        raise ApiError(502, "REMOTE_INTEGRITY_CHECK_FAILED")
                except ApiError:
                    raise
                except Exception:
                    raise ApiError(502, "S3_UPLOAD_FAILED") from None
                result.update({"bucket": destination["bucket"], "objectKey": key, "verified": True})
            self.db.execute("INSERT INTO jobs(job_id,fingerprint,result,expires_at) VALUES (?,?,?,?)",
                            (data["jobId"], fingerprint, json.dumps(result), expiry))
            self.db.commit()
            return 201, result


def make_handler(renderer: Renderer):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/v1/tts/render":
                return self.send_json(404, {"error": "NOT_FOUND"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + renderer.token):
                return self.send_json(401, {"error": "UNAUTHORIZED"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ApiError(413, "INVALID_BODY_LENGTH")
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
                status, result = renderer.render(payload)
                self.send_json(status, result)
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self.send_json(400, {"error": "INVALID_JSON"})
            except ApiError as exc:
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
    def sweeper():
        while not httpd_sweep_stop.wait(60):
            try:
                renderer.cleanup_expired_local()
            except (OSError, sqlite3.Error, ValueError):
                # Keep the service alive; expired metadata remains retryable.
                pass

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(renderer))
    httpd_sweep_stop = threading.Event()
    sweep_thread = threading.Thread(target=sweeper, daemon=True, name="amiga-ttl-sweep")
    sweep_thread.start()
    print(f"Amiga local TTS on http://{args.host}:{args.port} (local acceptance only)", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd_sweep_stop.set()
        sweep_thread.join(timeout=2)
        httpd.server_close()


if __name__ == "__main__":
    main()
