import contextlib
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from local_smoke import DEFAULTS, ENGINES, main, sha256, validate_wav


class LocalRunnerTests(unittest.TestCase):
    def test_engines_and_locale_defaults(self):
        self.assertEqual(set(ENGINES), set(DEFAULTS))
        self.assertEqual("en-US", DEFAULTS["kokoro"][0])
        self.assertEqual("ru-RU", DEFAULTS["qwen3"][0])
        self.assertEqual("kk-KZ", DEFAULTS["piper"][0])

    def test_valid_wav_report(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "valid.wav"
            with wave.open(str(out), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(24000)
                f.writeframes(b"\x00\x00" * 24000)
            report = validate_wav(out)
            self.assertEqual(1.0, report["durationSeconds"])
            self.assertEqual(24000, report["sampleRate"])
            self.assertEqual(sha256(out), report["sha256"])

    def test_missing_wav_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                validate_wav(Path(d) / "missing.wav")

    def test_bad_engine_rejected_before_any_network(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["run", "--engine", "unknown"])
        self.assertEqual(2, cm.exception.code)


if __name__ == "__main__":
    unittest.main()
