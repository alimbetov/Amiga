"""Portable single-instance lock for a Worker state directory.

Do not unlink a lock file on release. The kernel owns lock lifetime, so a
stale file left after SIGKILL is harmless, and another process cannot steal
the lock by replacing the inode.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class WorkerAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, state_dir: str | Path):
        self.directory = Path(state_dir).resolve()
        self.file = None

    def acquire(self):
        if self.file is not None:
            raise RuntimeError("lock already acquired")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ".worker-instance.lock"
        handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                # Windows locking requires at least one byte.
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise WorkerAlreadyRunning(
                "Worker data directory is already owned by another process"
            ) from exc
        self.file = handle
        return self

    def release(self):
        handle, self.file = self.file, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
