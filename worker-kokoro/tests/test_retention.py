import json
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from local_http import ApiError, Renderer, validate
from retention import DAY, TTL_DAYS, RetentionError, lifecycle_configuration, parse_retention


def sample(ttl=DAY):
    return {
        "jobId": "ttl-001", "engine": "piper", "locale": "kk-KZ",
        "voice": "kk_KZ-iseke-x_low", "text": "Сәлем!",
        "fileName": "audio.wav",
        "destination": {"type": "local", "retention": {"ttlSeconds": ttl}}
    }


def simulated_inference(command, **kwargs):
    directory = Path(command[command.index("--output-dir") + 1])
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / "piper.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(32000))


class RetentionContractTests(unittest.TestCase):
    def test_supported_days(self):
        for days in TTL_DAYS:
            self.assertEqual((days * DAY, f"ttl-{days}d"),
                             parse_retention({"mode": "TTL", "ttlSeconds": days * DAY}))

    def test_permanent_and_default(self):
        self.assertEqual((None, "permanent"), parse_retention(None))
        self.assertEqual((None, "permanent"), parse_retention({"mode": "PERMANENT"}))

    def test_reject_floats_bools_short_and_unknown(self):
        for value in (True, 3600, 1.0, 0, -1, 86401, 99999999, "86400"):
            with self.subTest(value=value), self.assertRaises(RetentionError):
                parse_retention({"ttlSeconds": value})
        for value in ({"mode": "PERMANENT", "ttlSeconds": DAY}, [], {"mode": "TTL"}):
            with self.assertRaises(RetentionError):
                parse_retention(value)

    def test_request_rejects_invalid_ttl(self):
        with self.assertRaises(ApiError) as ctx:
            validate(sample(3600))
        self.assertEqual("UNSUPPORTED_TTL", ctx.exception.code)

    def test_lifecycle_prefixes_are_isolated(self):
        rules = lifecycle_configuration("tts")["Rules"]
        self.assertEqual(4, len(rules))
        for days, rule in zip(TTL_DAYS, rules):
            self.assertEqual(days, rule["Expiration"]["Days"])
            self.assertEqual(f"tts/ttl-{days}d/", rule["Filter"]["Prefix"])
            self.assertEqual("Enabled", rule["Status"])


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        path = self.root / ".venv-piper" / "bin" / "python"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fake")
        self.renderer = Renderer(self.root, "A"*32, {"127.0.0.1"}, True)

    def tearDown(self):
        self.renderer.db.close()
        self.tmp.cleanup()

    def test_expiry_replay_tombstone_and_local_cleanup(self):
        with patch("local_http.subprocess.run", side_effect=simulated_inference) as inference:
            status, first = self.renderer.render(sample())
            self.assertEqual(201, status)
            self.assertEqual(DAY, first["ttlSeconds"])
            self.assertIsInstance(first["expiresAt"], int)
            self.assertTrue(Path(first["localPath"]).is_file())
            status, replay = self.renderer.render(sample())
            self.assertEqual(200, status)
            self.assertTrue(replay["replayed"])
            self.assertEqual(1, inference.call_count)
            expired = int(time.time()) - 1
            self.renderer.db.execute("UPDATE jobs SET expires_at=? WHERE job_id='ttl-001'", (expired,))
            self.renderer.db.commit()
            self.assertEqual(1, self.renderer.cleanup_expired_local(now=expired))
            self.assertFalse(Path(first["localPath"]).exists())
            self.assertEqual(0, self.renderer.cleanup_expired_local(now=expired))
            with self.assertRaises(ApiError) as ctx:
                self.renderer.render(sample())
            self.assertEqual(410, ctx.exception.status)
            self.assertEqual(1, inference.call_count)

    def test_permanent_is_not_cleaned(self):
        req = sample()
        req["destination"]["retention"] = {"mode": "PERMANENT"}
        with patch("local_http.subprocess.run", side_effect=simulated_inference):
            _, result = self.renderer.render(req)
            self.assertIsNone(result["expiresAt"])
            self.assertEqual(0, self.renderer.cleanup_expired_local(now=int(time.time())+10000))
            self.assertTrue(Path(result["localPath"]).exists())

    def test_s3_ttl_is_metadata_and_backend_owns_deletion(self):
        req = sample()
        req["destination"] = {
            "type": "s3", "endpoint": "http://127.0.0.1:9000",
            "bucket": "testbucket", "accessKey": "key", "secretKey": "secret",
            "retention": {"ttlSeconds": DAY}}
        self.assertEqual(DAY, validate(req)["ttlSeconds"])
        # No bucket lifecycle operator acknowledgement is required; this
        # internal worker does not own remote object expiration.
        self.renderer.allow_inline = False
        with patch.dict(os.environ, {"AMIGA_S3_LIFECYCLE_CONFIGURED": "0"}):
            with self.assertRaises(ApiError) as ctx:
                self.renderer.render(req)
        self.assertEqual("INLINE_STORAGE_DISABLED", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
