import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from audio_pipeline import AudioPipelineError, assemble_wav, render_mp3
from local_http import validate


def wav(path: Path, seconds: float = 0.1, sample_rate: int = 16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(bytes(int(seconds * sample_rate) * 2))


class AudioPipelineTests(unittest.TestCase):
    def test_joins_chunks_in_order_and_adds_pause(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            paths = [directory / "a.wav", directory / "b.wav"]
            for path in paths:
                wav(path)
            out = directory / "joined.wav"
            report = assemble_wav(paths, out, silence_ms=125)
            self.assertEqual(2, report["chunkCount"])
            with wave.open(str(out), "rb") as audio:
                self.assertEqual(5200, audio.getnframes())
                self.assertEqual(16000, audio.getframerate())

    def test_mismatched_wav_format_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            a, b = directory / "a.wav", directory / "b.wav"
            wav(a, sample_rate=16000)
            wav(b, sample_rate=24000)
            with self.assertRaises(AudioPipelineError):
                assemble_wav([a, b], directory / "joined.wav")

    def test_empty_chunk_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root)
            with self.assertRaises(AudioPipelineError):
                assemble_wav([d / "missing.wav"], d / "out.wav")

    def test_chunking_integration_with_mock_mp3_codec(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            output = directory / "final.mp3"
            seen = []

            def render(text, destination):
                seen.append(text)
                wav(destination)

            def fake_encoder(joined, result):
                with wave.open(str(joined), "rb") as reader:
                    self.assertEqual(16000, reader.getframerate())
                result.write_bytes(b"ID3" + bytes(256))
                return {"codec": "mp3", "durationSeconds": 0.5,
                        "sampleRate": 16000, "channels": 1}

            with patch("audio_pipeline.encode_mp3", side_effect=fake_encoder):
                report = render_mp3(
                    "First sentence. Second sentence. Third sentence.",
                    output, render, max_chars=32)
            self.assertEqual(2, report["chunkCount"])
            self.assertEqual(2, len(seen))
            self.assertEqual("First sentence. Second sentence.", seen[0])
            self.assertTrue(output.is_file())
            self.assertFalse((directory / "final.mp3.chunks").exists())

    def test_failure_removes_incomplete_output(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "failed.mp3"

            def bad(_, path):
                wav(path)
                raise RuntimeError("inference failed")

            with self.assertRaises(RuntimeError):
                render_mp3("Hello everyone.", output, bad)
            self.assertFalse(output.exists())
            self.assertFalse((Path(root) / "failed.mp3.chunks").exists())

    def test_http_contract_accepts_mp3_and_rejects_other_formats(self):
        req = {"jobId": "mp3-1", "engine": "piper", "locale": "kk-KZ",
               "voice": "kk_KZ-iseke-x_low", "text": "Сәлем!",
               "fileName": "result.mp3", "destination": {"type": "local"}}
        self.assertEqual("result.mp3", validate(req)["fileName"])
        with self.assertRaises(Exception):
            validate({**req, "fileName": "result.exe"})


if __name__ == "__main__":
    unittest.main()
