import tempfile
import unittest
from pathlib import Path

from amiga_worker.db import WorkerStore, RETENTION_SECONDS
from amiga_worker.janitor import SQLiteJanitor
from local_http import Renderer


class JanitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.sqlite"
        self.store = WorkerStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_terminal_retained_30_days_after_completion(self):
        self.store.receive("late", "fp", now=1)
        self.store.transition("late", "RECEIVED", "PROCESSING", now=2)
        complete = 29 * 86400
        self.store.transition("late", "PROCESSING", "READY", now=complete)
        self.assertEqual((0, 0), self.store.expire(now=30 * 86400))
        self.assertIsNotNone(self.store.job("late"))
        self.assertEqual((1, 0), self.store.expire(now=complete + RETENTION_SECONDS))
        self.assertIsNone(self.store.job("late"))

    def test_active_never_expired_and_chunks_cascade(self):
        self.store.receive("active", "afp", now=1)
        self.store.receive("done", "dfp", now=1)
        self.store.transition("done", "RECEIVED", "PROCESSING", now=2)
        self.store.transition("done", "PROCESSING", "READY", now=3)
        self.store.connection.execute(
            "INSERT INTO tts_chunk(job_id,chunk_index,fingerprint,status,updated_at) VALUES (?,?,?,?,?)",
            ("done", 0, "chunkfp", "DONE", 3))
        removed = SQLiteJanitor(self.store).run_once(now=RETENTION_SECONDS + 4)
        self.assertEqual((1, 0), removed)
        self.assertIsNotNone(self.store.job("active"))
        self.assertEqual(0, self.store.connection.execute(
            "SELECT COUNT(*) FROM tts_chunk WHERE job_id='done'").fetchone()[0])

    def test_batching_and_idempotence(self):
        for idx in range(5):
            name = "job" + str(idx)
            self.store.receive(name, name, now=1)
            self.store.transition(name, "RECEIVED", "PROCESSING", now=2)
            self.store.transition(name, "PROCESSING", "FAILED", now=3)
        janitor = SQLiteJanitor(self.store, batch_size=2, max_batches=2)
        self.assertEqual((4, 0), janitor.run_once(now=RETENTION_SECONDS + 5))
        self.assertEqual((1, 0), janitor.run_once(now=RETENTION_SECONDS + 5))
        self.assertEqual((0, 0), janitor.run_once(now=RETENTION_SECONDS + 5))

    def test_cache_purged_without_remote_delete(self):
        self.store.save_cache("a", "remote-object", "1" * 64, 12, 500, now=1)
        self.assertEqual((0, 1), SQLiteJanitor(self.store).run_once(now=RETENTION_SECONDS+2))
        self.assertIsNone(self.store.cache("a", now=RETENTION_SECONDS+2))

    def test_restart_and_repeat_cleanup(self):
        self.store.receive("done", "fp", now=1)
        self.store.transition("done", "RECEIVED", "PROCESSING", now=2)
        self.store.transition("done", "PROCESSING", "READY", now=3)
        self.store.close()
        self.store = WorkerStore(self.db)
        self.assertEqual((1, 0), SQLiteJanitor(self.store).run_once(now=RETENTION_SECONDS+3))


class HttpMetadataTests(unittest.TestCase):
    def test_http_metadata_bounded_and_ttl_guard(self):
        with tempfile.TemporaryDirectory() as d:
            api = Renderer(Path(d), "A" * 32, set(), False)
            try:
                api.db.execute(
                    "INSERT INTO jobs(job_id,fingerprint,result,expires_at,created_at) VALUES (?,?,?,?,?)",
                    ("old", "f", "{}", None, 1))
                api.db.execute(
                    "INSERT INTO jobs(job_id,fingerprint,result,expires_at,created_at) VALUES (?,?,?,?,?)",
                    ("not-yet-expired", "g", "{}", 100 * 86400, 1))
                api.db.execute(
                    "INSERT INTO jobs(job_id,fingerprint,result,expires_at,created_at) VALUES (?,?,?,?,?)",
                    ("recent", "h", "{}", None, 29 * 86400))
                api.db.commit()
                self.assertEqual(1, api.purge_completed_metadata(now=31 * 86400))
                self.assertEqual({"not-yet-expired", "recent"}, {
                    row[0] for row in api.db.execute("SELECT job_id FROM jobs")})
                self.assertEqual(1, api.purge_completed_metadata(now=101 * 86400))
                self.assertEqual(0, api.purge_completed_metadata(now=101 * 86400))
            finally:
                api.db.close()


if __name__ == "__main__":
    unittest.main()
