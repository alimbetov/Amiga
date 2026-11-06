"""Hard process termination after first WAV: no exception/unwind cleanup permitted."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from amiga_worker.db import WorkerStore
from durable_chunks import ChunkCheckpoint


class HardKillRecoveryTests(unittest.TestCase):
    def test_sigkill_equivalent_exit_preserves_verified_wav(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            worker_root = Path(__file__).resolve().parent.parent
            script = r'''
import os, sys, wave
from pathlib import Path
from amiga_worker.db import WorkerStore
from durable_chunks import ChunkCheckpoint

root = Path(sys.argv[1])
store = WorkerStore(root / "worker.sqlite")
store.receive("kill-test", "immutable")
store.transition("kill-test", "RECEIVED", "PROCESSING")
cp = ChunkCheckpoint(store, root / "chunks")
def render(text, target):
    if text.startswith("Third"):
        os._exit(47)  # hard termination: no finally/cleanup
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(32000))
cp.assemble_mp3(
    "kill-test",
    "First sentence. Second sentence. Third sentence.",
    root / "audio.mp3", render, max_chars=32)
'''
            env = dict(os.environ)
            env["PYTHONPATH"] = str(worker_root)
            proc = subprocess.run([sys.executable, "-c", script, str(root)],
                                  cwd=str(worker_root), env=env,
                                  capture_output=True, timeout=15)
            self.assertEqual(47, proc.returncode, proc.stderr.decode(errors="replace"))
            store = WorkerStore(root / "worker.sqlite")
            try:
                self.assertEqual("PROCESSING", store.job("kill-test")["status"])
                self.assertEqual("DONE", store.connection.execute(
                    "SELECT status FROM tts_chunk WHERE job_id='kill-test' AND chunk_index=0"
                ).fetchone()[0])
                cp = ChunkCheckpoint(store, root / "chunks")
                self.assertEqual(1, cp.recover_interrupted())
                self.assertEqual("RETRY_PENDING", store.job("kill-test")["status"])
                self.assertTrue((root / "chunks" / "kill-test" / "chunk-0000.wav").exists())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
