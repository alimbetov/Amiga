"""Crash-safe PCM chunk checkpoints for the local MP3 HTTP adapter.

Each DONE record is fsync-backed SQLite metadata with a SHA-256 verified WAV.
Intermediate WAV files survive a process kill and are removed only after final
output delivery has completed and its result is durably recorded.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
import wave
from pathlib import Path

from amiga_worker.chunking import chunks, normalize
from audio_pipeline import AudioPipelineError, MAX_CHUNKS, MAX_TEXT_CHARS, assemble_wav, encode_mp3

MAX_ATTEMPTS = 3


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sync_file_and_parent(path: Path) -> None:
    """Durable promotion on supported filesystems; directory fsync best effort."""
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    if hasattr(os, "O_DIRECTORY"):
        try:
            fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # Windows/filesystems without directory fsync keep atomic rename.
            pass


def verified_pcm(path: Path, sha: str) -> bool:
    if not path.is_file() or path.is_symlink() or not sha:
        return False
    try:
        with wave.open(str(path), "rb") as pcm:
            if pcm.getnframes() <= 0 or pcm.getsampwidth() != 2 or pcm.getcomptype() != "NONE":
                return False
        return file_sha256(path) == sha
    except (OSError, ValueError, EOFError, wave.Error):
        return False


class ChunkCheckpoint:
    """One SQLite owner connection, shared with the worker Inbox."""

    def __init__(self, store, work_root: Path):
        self.store = store
        self.work_root = work_root.resolve()
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS tts_final_checkpoint (
                 job_id TEXT PRIMARY KEY REFERENCES inbox_job(job_id) ON DELETE CASCADE,
                 fingerprint TEXT NOT NULL,
                 file_path TEXT NOT NULL,
                 sha256 TEXT NOT NULL,
                 size_bytes INTEGER NOT NULL,
                 info_json TEXT NOT NULL,
                 updated_at INTEGER NOT NULL
               )""")

    def _path(self, job_id: str, index: int) -> Path:
        if not job_id.isascii() or not job_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid job ID")
        return self.work_root / job_id / f"chunk-{index:04d}.wav"

    def render(self, job_id: str, index: int, text: str, render_one, identity: str = "") -> Path:
        expected = hashlib.sha256((identity + ":" + text).encode("utf-8")).hexdigest()
        path = self._path(job_id, index)
        row = self.store.connection.execute(
            "SELECT * FROM tts_chunk WHERE job_id=? AND chunk_index=?",
            (job_id, index)).fetchone()
        if row and row["fingerprint"] != expected:
            raise AudioPipelineError("chunk plan changed for existing job")
        if row and row["status"] == "DONE" and verified_pcm(path, row["pcm_sha256"]):
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".rendering.wav")
        temp.unlink(missing_ok=True)
        now = int(time.time())
        self.store.connection.execute(
            """INSERT INTO tts_chunk(job_id,chunk_index,fingerprint,status,updated_at)
               VALUES (?, ?, ?, 'PROCESSING', ?)
               ON CONFLICT(job_id,chunk_index) DO UPDATE SET
               status='PROCESSING',pcm_path=NULL,pcm_sha256=NULL,updated_at=excluded.updated_at""",
            (job_id, index, expected, now))
        try:
            render_one(text, temp)
            if not verified_pcm(temp, file_sha256(temp)):
                raise AudioPipelineError("invalid generated WAV")
            digest = file_sha256(temp)
            _sync_file_and_parent(temp)
            os.replace(temp, path)
            _sync_file_and_parent(path)
            self.store.connection.execute(
                """UPDATE tts_chunk SET status='DONE',pcm_path=?,pcm_sha256=?,updated_at=?
                   WHERE job_id=? AND chunk_index=?""",
                (str(path), digest, int(time.time()), job_id, index))
            return path
        except BaseException:
            self.store.connection.execute(
                "UPDATE tts_chunk SET status='FAILED',updated_at=? WHERE job_id=? AND chunk_index=?",
                (int(time.time()), job_id, index))
            raise
        finally:
            temp.unlink(missing_ok=True)

    def assemble_mp3(self, job_id: str, text: str, target: Path, render_one,
                     max_chars: int = 240, identity: str = "") -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise AudioPipelineError("invalid text")
        if not 32 <= max_chars <= 400:
            raise AudioPipelineError("invalid chunk size")
        planned = chunks(normalize(text), max_chars=max_chars)
        if len(planned) > MAX_CHUNKS:
            raise AudioPipelineError("too many chunks")
        import json
        plan_digest = hashlib.sha256(
            json.dumps({"identity": identity, "chunks": planned,
                        "maxChars": max_chars, "pipeline": "mp3-v1"},
                       ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        checkpoint = self.store.connection.execute(
            "SELECT * FROM tts_final_checkpoint WHERE job_id=?", (job_id,)
        ).fetchone()
        if checkpoint:
            if checkpoint["fingerprint"] != plan_digest:
                raise AudioPipelineError("final MP3 plan identity changed")
            if (target.is_file() and not target.is_symlink()
                    and checkpoint["file_path"] == str(target)
                    and target.stat().st_size == checkpoint["size_bytes"]
                    and file_sha256(target) == checkpoint["sha256"]):
                return json.loads(checkpoint["info_json"])
        paths = [self.render(job_id, i, part, render_one, identity=identity)
                 for i, part in enumerate(planned)]
        # A previous failed final encoding may leave a partial MP3; only successful
        # encoding atomically promotes it. Do not delete PCM chunks on failure.
        joined = self.work_root / job_id / "joined.wav"
        info = assemble_wav(paths, joined)
        encoded = encode_mp3(joined, target)
        result = {**encoded, "chunkCount": len(paths),
                  "pcmDurationSeconds": info["durationSeconds"]}
        digest = file_sha256(target)
        self.store.connection.execute(
            """INSERT INTO tts_final_checkpoint
               (job_id,fingerprint,file_path,sha256,size_bytes,info_json,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 fingerprint=excluded.fingerprint,file_path=excluded.file_path,
                 sha256=excluded.sha256,size_bytes=excluded.size_bytes,
                 info_json=excluded.info_json,updated_at=excluded.updated_at""",
            (job_id, plan_digest, str(target), digest, target.stat().st_size,
             json.dumps(result, sort_keys=True), int(time.time())))
        return result

    def cleanup_after_delivery(self, job_id: str) -> None:
        """Invoke only AFTER S3 verification and durable READY result storage."""
        directory = (self.work_root / job_id).resolve()
        if directory.parent != self.work_root or not directory.exists():
            return
        shutil.rmtree(directory)

    def recover_interrupted(self) -> int:
        """One worker owns this database and invokes recovery during startup."""
        db = self.store.connection
        return db.execute(
            """UPDATE inbox_job SET status='RETRY_PENDING',updated_at=?
               WHERE status='PROCESSING'""",
            (int(time.time()),)).rowcount
