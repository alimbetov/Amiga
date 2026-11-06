import hashlib
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from amiga_worker.db import WorkerStore
from durable_chunks import ChunkCheckpoint
from local_http import Renderer


def pcm(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(bytes(32000))


class DurableChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = WorkerStore(self.root / "local-http-output" / "jobs.sqlite")
        self.cp = ChunkCheckpoint(self.store, self.root / "chunks")
        self.store.receive("j01", "hash")
        self.store.transition("j01", "RECEIVED", "PROCESSING")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_kill_resume_reuses_verified_pcm(self):
        calls = []
        text = "First sentence. Second sentence. Third sentence."
        def interrupted(piece, output):
            calls.append(piece)
            if len(calls) == 2:
                raise RuntimeError("simulated kill during second chunk")
            pcm(output)
        with self.assertRaises(RuntimeError):
            self.cp.assemble_mp3("j01", text, self.root / "final.mp3",
                                 interrupted, max_chars=32)
        done = self.store.connection.execute(
            "SELECT status,pcm_sha256 FROM tts_chunk WHERE job_id='j01' AND chunk_index=0"
        ).fetchone()
        self.assertEqual("DONE", done["status"])
        self.assertEqual(64, len(done["pcm_sha256"]))
        self.assertTrue((self.root / "chunks" / "j01" / "chunk-0000.wav").exists())
        self.store.close()
        self.store = WorkerStore(self.root / "local-http-output" / "jobs.sqlite")
        self.cp = ChunkCheckpoint(self.store, self.root / "chunks")
        self.assertEqual(1, self.cp.recover_interrupted())
        self.assertEqual("RETRY_PENDING", self.store.job("j01")["status"])
        self.assertTrue(self.store.transition("j01", "RETRY_PENDING", "PROCESSING"))
        recovered = []
        def resume(piece, output):
            recovered.append(piece)
            pcm(output)
        def mock_encoder(joined, target):
            target.write_bytes(b"ID3" + bytes(512))
            return {"codec": "mp3", "durationSeconds": 1, "sampleRate": 16000, "channels": 1}
        with patch("durable_chunks.encode_mp3", side_effect=mock_encoder):
            result = self.cp.assemble_mp3("j01", text, self.root / "final.mp3",
                                          resume, max_chars=32)
        self.assertEqual(2, result["chunkCount"])
        self.assertEqual(["Third sentence."], recovered)
        self.assertTrue(self.root.joinpath("final.mp3").exists())
        self.store.transition("j01", "PROCESSING", "READY")
        self.cp.cleanup_after_delivery("j01")
        self.assertFalse(self.root.joinpath("chunks", "j01").exists())

    def test_verified_final_mp3_reused_after_process_restart(self):
        output = self.root / "final.mp3"
        calls = []

        def render(text, path):
            calls.append(text)
            pcm(path)

        def encode(joined, target):
            target.write_bytes(b"ID3" + bytes(512))
            return {"codec": "mp3", "durationSeconds": 1,
                    "sampleRate": 16000, "channels": 1}

        with patch("durable_chunks.encode_mp3", side_effect=encode) as codec:
            first = self.cp.assemble_mp3(
                "j01", "First sentence. Second sentence.", output,
                render, identity="voice-model-sha")
            self.assertEqual(1, codec.call_count)

        self.store.close()
        self.store = WorkerStore(self.root / "local-http-output" / "jobs.sqlite")
        self.cp = ChunkCheckpoint(self.store, self.root / "chunks")
        with patch("durable_chunks.encode_mp3", side_effect=AssertionError("duplicate encoding")):
            repeated = self.cp.assemble_mp3(
                "j01", "First sentence. Second sentence.", output,
                lambda *_: self.fail("duplicate synthesis"),
                identity="voice-model-sha")
        self.assertEqual(first, repeated)
        self.assertTrue(output.exists())

    def test_changed_voice_identity_refuses_cached_chunk(self):
        self.cp.render("j01", 0, "text", lambda text, path: pcm(path),
                       identity="voice-A")
        with self.assertRaises(Exception):
            self.cp.render("j01", 0, "text", lambda text, path: pcm(path),
                           identity="voice-B")

    def test_corrupt_done_chunk_is_regenerated(self):
        counter = []
        self.cp.render("j01", 0, "hello", lambda text, path: (counter.append(text), pcm(path)))
        self.assertEqual(1, len(counter))
        self.cp.render("j01", 0, "hello", lambda text, path: (counter.append(text), pcm(path)))
        self.assertEqual(1, len(counter))
        self.root.joinpath("chunks", "j01", "chunk-0000.wav").write_bytes(b"corrupt")
        self.cp.render("j01", 0, "hello", lambda text, path: (counter.append(text), pcm(path)))
        self.assertEqual(2, len(counter))

    def test_chunk_identity_collision_rejected(self):
        self.cp.render("j01", 0, "original", lambda text, path: pcm(path))
        with self.assertRaises(Exception):
            self.cp.render("j01", 0, "changed", lambda text, path: pcm(path))

    def test_renderer_migrates_one_shared_database_and_requeues_killed(self):
        self.store.close()
        self.store = WorkerStore(self.root / "jobs.sqlite")
        self.store.connection.execute(
            "UPDATE inbox_job SET status='PROCESSING' WHERE job_id='j01'")
        api = Renderer(self.root, "A" * 32, set(), False)
        try:
            self.assertEqual("RETRY_PENDING", api.store.job("j01")["status"])
            self.assertEqual(api.db, api.store.connection)
        finally:
            api.db.close()
        # tearDown may close already-opened store independently


if __name__ == "__main__":
    unittest.main()
