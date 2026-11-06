"""W2B fallback coordinator: secure staged download, upload, proposal and approval.

Production transport is injectable; no unapproved model is marked READY.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from .bootstrap import build_candidate, should_bootstrap, validate_seed


class BootstrapError(RuntimeError):
    pass


class ArtifactCorrupt(BootstrapError):
    pass


class AwaitingApproval(BootstrapError):
    pass


def digest_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


def verified(path: Path, asset: dict) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    digest, n = digest_file(path)
    return digest == asset["sha256"] and n == asset["sizeBytes"]


class BootstrapState:
    """Persistent artifact checkpoints, keyed by immutable asset digest."""
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS bootstrap_artifact (
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            local_path TEXT NOT NULL,
            object_key TEXT,
            status TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS bootstrap_proposal (
            candidate_digest TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            manifest_id TEXT,
            updated_at INTEGER NOT NULL
        )""")

    def artifact(self, sha: str):
        return self.db.execute("SELECT * FROM bootstrap_artifact WHERE sha256=?", (sha,)).fetchone()

    def put_artifact(self, asset: dict, path: Path, status: str, object_key: str | None = None):
        self.db.execute("""INSERT INTO bootstrap_artifact
         (sha256,size_bytes,local_path,object_key,status,updated_at)
         VALUES (?,?,?,?,?,?)
         ON CONFLICT(sha256) DO UPDATE SET
          size_bytes=excluded.size_bytes,local_path=excluded.local_path,
          object_key=excluded.object_key,status=excluded.status,updated_at=excluded.updated_at""",
          (asset["sha256"], asset["sizeBytes"], str(path), object_key, status, int(time.time())))

    def proposal(self, digest: str):
        return self.db.execute("SELECT * FROM bootstrap_proposal WHERE candidate_digest=?", (digest,)).fetchone()

    def put_proposal(self, digest: str, status: str, manifest_id: str | None = None):
        self.db.execute("""INSERT INTO bootstrap_proposal(candidate_digest,status,manifest_id,updated_at)
         VALUES (?,?,?,?) ON CONFLICT(candidate_digest) DO UPDATE SET
         status=excluded.status,manifest_id=excluded.manifest_id,updated_at=excluded.updated_at""",
         (digest, status, manifest_id, int(time.time())))


class BootstrapCoordinator:
    """Orchestrates a single bootstrap run. One worker process owns the local SQLite DB.

    transport.download(url, file_handle, expected_size) streams into the handle.
    transport.upload(asset, path) returns a trusted immutable RustFS object key.
    transport.propose(candidate, idempotency_key) returns status and manifestId.
    transport.init() returns (HTTP status, body) and is authoritative.
    """
    def __init__(self, store, transport, root: Path, allowed_hosts: frozenset[str]):
        self.state = BootstrapState(store.connection)
        self.transport = transport
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_hosts = allowed_hosts

    def _fetch(self, asset: dict) -> Path:
        target = self.root / (asset["sha256"] + ".bin")
        if verified(target, asset):
            self.state.put_artifact(asset, target, "VERIFIED",
                                    self._old_key(asset, target))
            return target
        # A corrupt cached object is never trusted or reused.
        if target.exists():
            target.unlink()
        fd, tempname = tempfile.mkstemp(prefix=".part-", dir=self.root)
        temp = Path(tempname)
        try:
            with os.fdopen(fd, "wb") as handle:
                self.transport.download(asset["upstreamUrl"], handle, asset["sizeBytes"],
                                        self.allowed_hosts)
                handle.flush()
                os.fsync(handle.fileno())
            if not verified(temp, asset):
                raise ArtifactCorrupt("download checksum or byte count mismatch")
            os.replace(temp, target)
            # Persist the staged state only after atomic durable promotion.
            self.state.put_artifact(asset, target, "VERIFIED")
            return target
        finally:
            temp.unlink(missing_ok=True)

    def _old_key(self, asset: dict, target: Path) -> str | None:
        row = self.state.artifact(asset["sha256"])
        if row and row["object_key"] and row["size_bytes"] == asset["sizeBytes"] and row["local_path"] == str(target):
            return row["object_key"]
        return None

    def run(self, seed: dict):
        http_status, init_body = self.transport.init()
        if http_status == 200:
            return {"state": "BACKEND_MANIFEST", "manifest": init_body}
        if not should_bootstrap(http_status, init_body.get("code") if isinstance(init_body, dict) else None):
            raise BootstrapError("init unavailable or not explicitly NO_MANIFEST; fallback forbidden")
        validated = validate_seed(seed, allowed_hosts=self.allowed_hosts)
        keys = {}
        for model in validated["models"]:
            for asset in model["artifacts"]:
                path = self._fetch(asset)
                row = self.state.artifact(asset["sha256"])
                if row and row["object_key"]:
                    # Object must be re-verified remotely, not assumed because SQLite says uploaded.
                    if self.transport.object_exists(row["object_key"], asset):
                        keys[asset["sha256"]] = row["object_key"]
                        continue
                key = self.transport.upload(asset, path)
                if not isinstance(key, str) or not key.startswith("models/sha256/"):
                    raise BootstrapError("invalid immutable RustFS object key")
                if not self.transport.object_exists(key, asset):
                    raise BootstrapError("RustFS upload not verified")
                self.state.put_artifact(asset, path, "UPLOADED", key)
                keys[asset["sha256"]] = key
        candidate = build_candidate(validated, verified_object_keys=keys)
        if candidate["state"] != "ASSETS_UPLOADED":
            raise BootstrapError("incomplete artifact upload")
        digest = candidate["candidateDigest"]
        self.state.put_proposal(digest, "PENDING_APPROVAL")
        decision = self.transport.propose(candidate, digest)
        if decision.get("status") != "APPROVED":
            self.state.put_proposal(digest, decision.get("status", "PENDING_APPROVAL"))
            raise AwaitingApproval("Backend has not approved candidate")
        # A proposal response is insufficient: final readiness must derive from init.
        status, manifest = self.transport.init()
        if status != 200 or not self.transport.manifest_covers(manifest, candidate):
            raise AwaitingApproval("approved manifest not yet published or mismatched")
        self.state.put_proposal(digest, "APPROVED", manifest.get("manifestId"))
        return {"state": "MANIFEST_APPROVED", "manifest": manifest, "candidateDigest": digest}
