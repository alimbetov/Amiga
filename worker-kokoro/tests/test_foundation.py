import sqlite3
import tempfile
import unittest
from pathlib import Path
from amiga_worker.db import WorkerStore, CollisionError, InvalidTransition
from amiga_worker.chunking import normalize, chunks, fingerprint


class WorkerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "db" / "worker.sqlite"
        self.store = WorkerStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_inbox_duplicate_and_collision(self):
        self.assertEqual("RECEIVED", self.store.receive("j1", "a", now=1))
        self.assertEqual("RECEIVED", self.store.receive("j1", "a", now=2))
        with self.assertRaises(CollisionError):
            self.store.receive("j1", "b", now=3)
        self.assertEqual("a", self.store.job("j1")["fingerprint"])

    def test_transitions(self):
        self.store.receive("j", "x", now=1)
        self.assertTrue(self.store.transition("j", "RECEIVED", "PROCESSING", now=2))
        self.assertFalse(self.store.transition("j", "RECEIVED", "PROCESSING", now=3))
        self.assertTrue(self.store.transition("j", "PROCESSING", "RETRY_PENDING", now=4))
        self.assertTrue(self.store.transition("j", "RETRY_PENDING", "PROCESSING", now=5))
        self.assertTrue(self.store.transition("j", "PROCESSING", "READY", now=6))
        with self.assertRaises(InvalidTransition):
            self.store.transition("j", "READY", "RECEIVED")

    def test_cache_expiry_and_invalidation(self):
        self.store.save_cache("fp", "opaque/key.mp3", "a" * 64, 123, 456, now=10)
        self.assertEqual("opaque/key.mp3", self.store.cache("fp", now=11)["object_key"])
        self.assertIsNone(self.store.cache("fp", now=10+30*86400))
        self.store.invalidate_cache("fp")
        self.assertIsNone(self.store.cache("fp", now=12))

    def test_retention_preserves_active_jobs_and_rustfs(self):
        self.store.receive("done", "x", now=1)
        self.store.transition("done", "RECEIVED", "PROCESSING", now=2)
        self.store.transition("done", "PROCESSING", "READY", now=3)
        self.store.receive("active", "y", now=1)
        self.store.save_cache("x", "published.mp3", "a" * 64, 123, 456, now=1)
        self.assertEqual((1,1), self.store.expire(now=31*86400))
        self.assertIsNone(self.store.job("done"))
        self.assertIsNotNone(self.store.job("active"))

    def test_restart_persists_job_and_wal(self):
        self.store.receive("persist", "hash", now=2)
        self.store.close()
        self.store = WorkerStore(self.path)
        self.assertEqual("hash", self.store.job("persist")["fingerprint"])
        self.assertEqual("wal", self.store.connection.execute("PRAGMA journal_mode").fetchone()[0])
        self.assertEqual(1, self.store.connection.execute("PRAGMA foreign_keys").fetchone()[0])


class ChunkingTests(unittest.TestCase):
    def test_normalize_and_pack(self):
        self.assertEqual("One. Two.", normalize("  One.  Two.  "))
        self.assertEqual(["Alpha first.", "Beta second."], chunks("Alpha first. Beta second.", max_chars=16))
        self.assertEqual(["Hello. Bye."], chunks("Hello. Bye.", max_chars=24))

    def test_reject_long_unbreakable_word(self):
        with self.assertRaises(ValueError):
            chunks("A"*100, max_chars=24)

    def test_fingerprint_engine_voice_and_version_sensitive(self):
        args = dict(locale="en-US", engine="KOKORO", model_sha256="a"*64,
                    voice_sha256="b"*64, voice="af_heart")
        a = fingerprint("Hello.", **args)
        self.assertEqual(a, fingerprint(" Hello.  ", **args))
        self.assertNotEqual(a, fingerprint("Hello.", **(args | {"voice": "am_michael"})))
        self.assertNotEqual(a, fingerprint("Hello.", **(args | {"engine": "PIPER"})))
        self.assertNotEqual(a, fingerprint("Hello.", **args, pipeline_version="pcm-v2"))


if __name__ == "__main__":
    unittest.main()
