import json
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from durable_executor import DurableQueue
from local_http import Renderer, ApiError, validate


def job():
    return {
        "jobId": "async-001", "engine": "piper", "locale": "kk-KZ",
        "voice": "kk_KZ-iseke-x_low", "text": "Сәлем!",
        "fileName": "output.wav", "destination": {"type": "local"}
    }


def fake_inference(command, **kwargs):
    dest = Path(command[command.index("--output-dir") + 1])
    dest.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest / "piper.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(32000))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        interpreter = root / ".venv-piper" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fake")
        self.renderer = Renderer(root, "A"*32, set(), False)
        self.queue = DurableQueue(self.renderer, ApiError, validate)

    def tearDown(self):
        self.renderer.db.close()
        self.temp.cleanup()

    def test_accept_before_inference_and_identical_replay(self):
        with patch("local_http.subprocess.run", side_effect=fake_inference) as inference:
            code, response = self.queue.submit(job())
            self.assertEqual(202, code)
            self.assertEqual("PENDING", response["status"])
            self.assertEqual(0, inference.call_count)
            code, _ = self.queue.submit(job())
            self.assertEqual(202, code)
            with self.assertRaises(ApiError) as ctx:
                self.queue.submit({**job(), "text": "different"})
            self.assertEqual(409, ctx.exception.status)
            self.assertTrue(self.queue.run_once())
            self.assertEqual(1, inference.call_count)
            self.assertEqual("READY", self.queue.status("async-001")["status"])
            self.assertEqual("READY", self.renderer.store.job("async-001")["status"])
            code, replay = self.queue.submit(job())
            self.assertEqual(200, code)
            self.assertEqual("READY", replay["status"])
            self.assertEqual(1, inference.call_count)

    def test_transient_failure_retry_and_durable_recovery_after_restart(self):
        self.queue.submit(job())
        with patch("local_http.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["python"])):
            self.assertTrue(self.queue.run_once())
        self.assertEqual("RETRY_PENDING", self.queue.status("async-001")["status"])
        self.assertEqual("RETRY_PENDING", self.renderer.store.job("async-001")["status"])
        # Simulate kill after next attempt was claimed, before inference completed.
        self.renderer.db.execute(
            "UPDATE durable_jobs SET status='PROCESSING' WHERE job_id='async-001'")
        self.renderer.store.transition("async-001", "RETRY_PENDING", "PROCESSING")
        self.renderer.db.close()
        self.renderer = Renderer(Path(self.temp.name), "A"*32, set(), False)
        self.queue = DurableQueue(self.renderer, ApiError, validate)
        self.assertEqual("RETRY_PENDING", self.queue.status("async-001")["status"])
        self.assertEqual("RETRY_PENDING", self.renderer.store.job("async-001")["status"])
        with patch("local_http.subprocess.run", side_effect=fake_inference):
            self.assertTrue(self.queue.run_once(now=int(time.time())+10))
        self.assertEqual("READY", self.queue.status("async-001")["status"])

    def test_no_inline_secret_in_durable_sqlite(self):
        remote = job()
        remote["destination"] = {
            "type": "s3", "endpoint": "http://127.0.0.1:9000",
            "bucket": "audio", "accessKey": "public", "secretKey": "SECRET_NOT_STORED"
        }
        with self.assertRaises(ApiError) as ctx:
            self.queue.submit(remote)
        self.assertEqual("DURABLE_REQUIRES_STORAGE_PROFILE", ctx.exception.code)

    def test_profile_id_persists_without_storage_secrets(self):
        self.queue.profiles["office"] = {
            "endpoint": "http://127.0.0.1:9000", "bucket": "audio",
            "accessKey": "key", "secretKey": "SECRET_NOT_STORED"
        }
        req = job()
        req["destination"] = {"type": "profile", "profileId": "office"}
        code, _ = self.queue.submit(req)
        self.assertEqual(202, code)
        raw = self.renderer.db.execute(
            "SELECT request_json FROM durable_jobs WHERE job_id='async-001'").fetchone()[0]
        self.assertNotIn("SECRET_NOT_STORED", raw)
        self.assertIn("office", raw)

    def test_bounded_queue(self):
        self.queue.max_queue = 1
        self.queue.submit(job())
        with self.assertRaises(ApiError) as ctx:
            self.queue.submit({**job(), "jobId": "async-002"})
        self.assertEqual(429, ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
