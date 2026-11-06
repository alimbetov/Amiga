import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from engine_supervisor import EngineUnavailable, ResidentEngine


class ResidentTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = ResidentEngine("piper", "kk_KZ-iseke-x_low", self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_ipc_deadline_fail_closed(self):
        self.engine._responses = queue.Queue()
        with self.assertRaises(EngineUnavailable) as failure:
            self.engine._receive(0.01)
        self.assertEqual("ENGINE_TIMEOUT", str(failure.exception))

    def test_render_timeout_stops_hung_child(self):
        process = Mock()
        process.poll.return_value = None
        self.engine.process = process
        self.engine.state = "READY"
        self.engine.render_timeout = 0.01
        with self.assertRaises(EngineUnavailable):
            self.engine.render("Hello", self.root / "output.wav",
                               "kk_KZ-iseke-x_low")
        self.assertEqual("FAILED", self.engine.state)
        process.terminate.assert_called_once()
        self.assertFalse((self.root / "output.wav").exists())

    def test_failed_initialization_never_reports_ready(self):
        self.engine.startup_timeout = 0.01
        venv = self.root / ".venv-piper" / "bin" / "python"
        venv.parent.mkdir(parents=True)
        venv.write_bytes(b"fake")
        process = Mock()
        process.poll.return_value = None
        process.stdout = iter([])
        self.engine.popen = lambda *args, **kwargs: process
        self.engine.start()
        self.assertFalse(self.engine.ready())
        self.assertEqual("FAILED", self.engine.state)
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
