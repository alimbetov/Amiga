import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

from local_http import ApiError, Renderer, decode_secret, validate


def example():
    return {
        "jobId": "reminder-001", "engine": "piper", "locale": "kk-KZ",
        "voice": "kk_KZ-iseke-x_low", "text": "Сәлем!",
        "fileName": "reminder.wav", "destination": {"type": "local"}
    }


class RequestValidationTests(unittest.TestCase):
    def test_metrics_and_readiness_have_no_text_or_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            api = Renderer(Path(temp), "A" * 32, set(), False)
            try:
                self.assertEqual("NOT_READY", api.readiness()["status"])
                api.observe("render_failed_total")
                api.observe("render_success_total")
                metrics = api.metrics_snapshot()
                self.assertEqual(2, metrics["requests_total"])
                self.assertEqual(1, metrics["render_failed_total"])
                self.assertEqual(1, metrics["render_success_total"])
                self.assertNotIn("password", str(metrics).lower())
            finally:
                api.db.close()

    def test_accepts_local(self):
        self.assertEqual("local", validate(example())["destination"]["type"])

    def test_base64_is_only_encoding(self):
        self.assertEqual("password", decode_secret("base64:" + base64.b64encode(b"password").decode()))

    def test_invalid_base64_rejected(self):
        with self.assertRaises(ApiError):
            decode_secret("base64:***")

    def test_traversal_rejected(self):
        for name in ("../secret.wav", "a\\b.wav", "foo/bar.wav", ".hidden.wav"):
            with self.subTest(name=name):
                with self.assertRaises(ApiError):
                    validate({**example(), "fileName": name})

    def test_disallow_arbitrary_scheme_and_userinfo(self):
        for endpoint in ("file:///etc/passwd", "http://user:pass@127.0.0.1:9000",
                         "http://127.0.0.1:9000/other", "http://127.0.0.1:9000?x=1"):
            request = example()
            request["destination"] = {"type": "s3", "endpoint": endpoint,
                                      "bucket": "audio", "accessKey": "a", "secretKey": "s"}
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ApiError):
                    validate(request)

    def test_kokoro_nonenglish_fail_closed(self):
        with self.assertRaises(ApiError) as ctx:
            validate({**example(), "engine": "kokoro", "voice": "af_heart"})
        self.assertEqual("KOKORO_LOCAL_ADAPTER_ENGLISH_ONLY", ctx.exception.code)

    def test_piper_voice_language_mismatch(self):
        with self.assertRaises(ApiError):
            validate({**example(), "voice": "ru_RU-ruslan-medium"})

    def test_no_unapproved_s3(self):
        with tempfile.TemporaryDirectory() as temp:
            api = Renderer(Path(temp), "A" * 32, set(), False)
            req = example()
            req["destination"] = {"type": "s3", "endpoint": "https://storage.example.com",
                                  "bucket": "audio", "accessKey": "a", "secretKey": "s"}
            with self.assertRaises(ApiError) as ctx:
                api.render(req)
            self.assertEqual(403, ctx.exception.status)

    def test_idempotent_local_render_no_duplicate_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / ".venv-piper" / "bin" / "python"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"placeholder")
            api = Renderer(root, "A" * 32, set(), False)

            def fake_run(command, **kwargs):
                out = Path(command[command.index("--output-dir") + 1])
                out.mkdir(parents=True, exist_ok=True)
                with wave.open(str(out / "piper.wav"), "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(bytes(32000))

            with patch("local_http.subprocess.run", side_effect=fake_run) as run:
                code, first = api.render(example())
                self.assertEqual(201, code)
                self.assertEqual(32044, first["sizeBytes"])
                code, second = api.render(example())
                self.assertEqual(200, code)
                self.assertTrue(second["replayed"])
                self.assertEqual(1, run.call_count)
                with self.assertRaises(ApiError) as ctx:
                    api.render({**example(), "text": "Different"})
                self.assertEqual(409, ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
