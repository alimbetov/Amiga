import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from engine_supervisor import EngineSupervisor, EngineUnavailable
from local_http import Renderer


class DummyEngine:
    def __init__(self, engine, voice, root):
        self.engine = engine
        self.voice = voice
        self.state = "LOADING"
        self.error = None
        self.alive = True
        self.renders = 0

    def start(self):
        self.state = "READY"

    def ready(self):
        if not self.alive:
            self.state = "FAILED"
            self.error = "PROCESS_EXITED"
            return False
        return self.state == "READY"

    def render(self, text, output, voice):
        if not self.ready() or voice != self.voice:
            raise EngineUnavailable("NOT_READY")
        self.renders += 1
        Path(output).write_bytes(bytes(200))

    def stop(self):
        self.state = "STOPPED"


class ModelReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.renderer = Renderer(self.root, "A" * 32, set(), False)

    def tearDown(self):
        self.renderer.db.close()
        self.temp.cleanup()

    def test_not_ready_before_models_loaded(self):
        self.assertEqual("NOT_READY", self.renderer.tts_readiness()["status"])
        supervisor = EngineSupervisor(self.root, engine_factory=DummyEngine)
        self.renderer.engine_supervisor = supervisor
        status = self.renderer.tts_readiness()
        self.assertEqual("NOT_READY", status["status"])
        self.assertEqual("LOADING", status["engines"]["kokoro"]["status"])
        self.assertEqual("LOADING", status["engines"]["piper"]["status"])

    def test_requires_both_models_and_detects_crashed_process(self):
        supervisor = EngineSupervisor(self.root, engine_factory=DummyEngine)
        self.renderer.engine_supervisor = supervisor
        supervisor.engines["kokoro"].start()
        self.assertEqual("NOT_READY", self.renderer.tts_readiness()["status"])
        supervisor.engines["piper"].start()
        status = self.renderer.tts_readiness()
        self.assertEqual("READY", status["status"])
        self.assertEqual(["kokoro", "piper"], status["required"])
        supervisor.engines["piper"].alive = False
        status = self.renderer.tts_readiness()
        self.assertEqual("NOT_READY", status["status"])
        self.assertEqual("FAILED", status["engines"]["piper"]["status"])

    def test_render_uses_resident_model(self):
        supervisor = EngineSupervisor(self.root, engine_factory=DummyEngine)
        supervisor.engines["piper"].start()
        output = self.root / "sound.wav"
        supervisor.render("piper", "kk_KZ-iseke-x_low", "Hello", output)
        self.assertTrue(output.is_file())
        self.assertEqual(1, supervisor.engines["piper"].renders)
        with self.assertRaises(EngineUnavailable):
            supervisor.render("piper", "wrong_voice", "Hello", output)

    def test_no_ready_without_supervisor(self):
        self.assertEqual("NOT_READY", self.renderer.tts_readiness()["status"])
        self.assertNotEqual("READY", self.renderer.readiness()["status"])


if __name__ == "__main__":
    unittest.main()
