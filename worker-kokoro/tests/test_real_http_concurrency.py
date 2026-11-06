"""Real socket HTTP concurrency acceptance (no TTS model download)."""
import concurrent.futures
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from durable_executor import DurableQueue
from local_http import ApiError, Renderer, make_handler, validate


TOKEN = "test-a-very-long-private-token-1234"


def wav_subprocess(argv, **kwargs):
    output_dir = Path(argv[argv.index("--output-dir") + 1])
    output_dir.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_dir / "piper.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(32000))


class RealHTTPConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        py = self.root / ".venv-piper" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_bytes(b"test")
        self.renderer = Renderer(self.root, TOKEN, set(), False)
        self.queue = DurableQueue(self.renderer, ApiError, validate)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         make_handler(self.renderer, self.queue))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.renderer.db.close()
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        address = "http://127.0.0.1:" + str(self.httpd.server_port) + path
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            address, data=body, method=method,
            headers={"Authorization": "Bearer " + TOKEN,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def job(self, index=1):
        return {"jobId": f"socket-{index:03d}", "engine": "piper",
                "locale": "kk-KZ", "voice": "kk_KZ-iseke-x_low",
                "text": "Hello", "fileName": "out.wav",
                "destination": {"type": "local"}}

    def test_ten_concurrent_enqueues_and_status_checks(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            outputs = list(pool.map(
                lambda i: self.request("POST", "/v1/tts/jobs", self.job(i)),
                range(10)))
        self.assertEqual([202] * 10, [x[0] for x in outputs])
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(
                lambda i: self.request("GET", f"/v1/tts/jobs/socket-{i:03d}"),
                range(10)))
        self.assertTrue(all(status == 200 and body["status"] == "PENDING"
                            for status, body in statuses))
        self.assertEqual(10, self.renderer.db.execute(
            "SELECT COUNT(*) FROM durable_jobs").fetchone()[0])

    def test_concurrent_duplicate_job_id_and_real_background_executor(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(
                lambda _: self.request("POST", "/v1/tts/jobs", self.job()),
                range(10)))
        self.assertTrue(all(status == 202 for status, _ in results))
        self.assertEqual(1, self.renderer.db.execute(
            "SELECT COUNT(*) FROM durable_jobs").fetchone()[0])
        with patch("local_http.subprocess.run", side_effect=wav_subprocess) as infer:
            self.queue.start()
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status, body = self.request("GET", "/v1/tts/jobs/socket-001")
                if body.get("status") == "READY":
                    break
                time.sleep(0.02)
            self.queue.stop()
            self.assertEqual("READY", body["status"])
            self.assertEqual(1, infer.call_count)
        status, body = self.request("POST", "/v1/tts/jobs", self.job())
        self.assertEqual(200, status)
        self.assertEqual("READY", body["status"])

    def test_model_status_and_info_require_token_except_tts_ready(self):
        status, body = self.request("GET", "/health/info")
        self.assertEqual(200, status)
        self.assertEqual("NOT_READY", body["status"])
        status, body = self.request("GET", "/TTS/ready")
        self.assertEqual(503, status)
