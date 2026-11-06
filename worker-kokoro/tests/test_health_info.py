import tempfile
import unittest
from pathlib import Path

from engine_supervisor import EngineSupervisor
from health_info import worker_health_info, voice_locale
from local_http import Renderer


class FakeEngine:
    def __init__(self, engine, voice, root):
        self.engine = engine
        self.voice = voice
        self.state = "LOADING"
        self.error = None
        self.alive = True

    def start(self):
        self.state = "READY"

    def ready(self):
        if not self.alive:
            self.state = "FAILED"
            self.error = "PROCESS_EXITED"
        return self.state == "READY"

    def stop(self):
        self.state = "STOPPED"


class HealthInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.renderer = Renderer(self.root, "A" * 32, set(), False)
        self.supervisor = EngineSupervisor(self.root, engine_factory=FakeEngine)
        self.renderer.engine_supervisor = self.supervisor

    def tearDown(self):
        self.renderer.db.close()
        self.temp.cleanup()

    def test_expected_but_not_loaded_on_startup(self):
        info = self.renderer.health_info()
        self.assertEqual(1, info["schemaVersion"])
        self.assertEqual("NOT_READY", info["status"])
        self.assertEqual(2, info["summary"]["expectedModels"])
        self.assertEqual(0, info["summary"]["loadedModels"])
        self.assertEqual({"en-US", "kk-KZ"},
                         {lang["locale"] for lang in info["languages"]})
        self.assertTrue(all(not m["loaded"] for m in info["models"]))
        self.assertEqual({"kokoro", "piper"},
                         {model["engine"] for model in info["models"]})
        self.assertTrue(all("artifacts" in model for model in info["models"]))

    def test_actual_warm_models_are_marked_loaded(self):
        self.supervisor.engines["kokoro"].start()
        info = self.renderer.health_info()
        self.assertEqual("NOT_READY", info["status"])
        self.assertEqual(1, info["summary"]["loadedModels"])
        self.assertEqual(1, info["summary"]["loadedLanguages"])
        self.supervisor.engines["piper"].start()
        info = self.renderer.health_info()
        self.assertEqual("READY", info["status"])
        self.assertEqual(2, info["summary"]["loadedModels"])
        self.assertEqual(2, info["summary"]["loadedLanguages"])
        self.supervisor.engines["piper"].alive = False
        info = self.renderer.health_info()
        self.assertEqual("NOT_READY", info["status"])
        self.assertEqual("FAILED", next(m for m in info["models"] if m["engine"] == "piper")["status"])

    def test_expected_artifacts_are_independent_of_load_status(self):
        directory = self.root / "local-tts-data" / "models" / "piper"
        directory.mkdir(parents=True)
        (directory / "kk_KZ-iseke-x_low.onnx").write_bytes(b"model")
        info = self.renderer.health_info()
        piper = next(m for m in info["models"] if m["engine"] == "piper")
        self.assertFalse(piper["loaded"])
        self.assertTrue(piper["artifacts"][0]["present"])
        self.assertFalse(piper["artifacts"][1]["present"])
        self.assertEqual("NOT_READY", info["status"])

    def test_no_supervisor_returns_fail_closed_empty_inventory(self):
        self.renderer.engine_supervisor = None
        info = self.renderer.health_info()
        self.assertEqual("NOT_READY", info["status"])
        self.assertEqual(0, info["summary"]["expectedModels"])

    def test_supported_configured_locale_variants(self):
        self.assertEqual("ru-RU", voice_locale("piper", "ru_RU-ruslan-medium"))
        self.assertEqual("en-GB", voice_locale("kokoro", "bf_emma"))
        self.assertIsNone(voice_locale("kokoro", "unknown"))
        self.assertIsNone(voice_locale("piper", "broken"))

    def test_response_has_no_secrets_or_text(self):
        payload = str(self.renderer.health_info()).lower()
        for secret in ("secretkey", "accesskey", "authorization", "plaintext", "password"):
            self.assertNotIn(secret, payload)


if __name__ == "__main__":
    unittest.main()
