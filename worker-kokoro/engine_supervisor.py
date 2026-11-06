"""Fail-closed readiness of real, resident Kokoro and Piper models.

Each engine must emit READY after loading weights and a warmup synthesis.
A process exit makes readiness false immediately. No startup file-only shortcuts.
"""
from __future__ import annotations
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from runtime_config import WorkerPaths

DEFAULT_VOICES = {"kokoro": "af_heart", "piper": "kk_KZ-iseke-x_low"}


class EngineUnavailable(RuntimeError):
    pass


class ResidentEngine:
    def __init__(self, engine: str, voice: str, root: Path, popen=subprocess.Popen):
        self.engine = engine
        self.voice = voice
        self.root = Path(root)
        self.popen = popen
        self.process = None
        self.state = "LOADING"
        self.lock = threading.Lock()
        self.error = None
        self._responses = queue.Queue(maxsize=8)
        self.startup_timeout = 180
        self.render_timeout = 300

    def _read_loop(self, process):
        try:
            for line in process.stdout:
                try:
                    self._responses.put(json.loads(line), timeout=2)
                except (ValueError, queue.Full):
                    break
        finally:
            try:
                self._responses.put_nowait({"event": "EXITED"})
            except queue.Full:
                pass

    def _receive(self, timeout):
        try:
            event = self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            raise EngineUnavailable("ENGINE_TIMEOUT") from exc
        if event.get("event") == "EXITED":
            raise EngineUnavailable("ENGINE_EXITED")
        return event

    def start(self):
        self.state = "LOADING"
        self._responses = queue.Queue(maxsize=8)
        env = self.root / (".venv-" + self.engine)
        interpreter = env / "Scripts" / "python.exe"
        if not interpreter.is_file():
            interpreter = env / "bin" / "python"
        if not interpreter.is_file():
            self.state, self.error = "FAILED", "ENV_NOT_FOUND"
            return
        models = WorkerPaths.from_environment(self.root).models / self.engine
        try:
            proc = self.popen(
                [str(interpreter), "-u", str(self.root / "resident_engine.py"),
                 "--engine", self.engine, "--voice", self.voice,
                 "--models", str(models)],
                cwd=str(self.root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            self.process = proc
            # Startup runs in a daemon thread; HTTP remains responsive (503)
            # while a slow model initializes. No READY without valid handshake.
            thread = threading.Thread(target=self._read_loop, args=(proc,),
                                      daemon=True, name="amiga-reader-" + self.engine)
            thread.start()
            event = self._receive(self.startup_timeout)
            if (event.get("event") != "READY" or event.get("engine") != self.engine
                    or event.get("voice") != self.voice or proc.poll() is not None):
                raise EngineUnavailable("LOAD_FAILED")
            self.state = "READY"
        except (OSError, ValueError, EngineUnavailable):
            self.state, self.error = "FAILED", "LOAD_FAILED"
            self.stop(final_state="FAILED")

    def ready(self) -> bool:
        if self.state != "READY":
            return False
        if self.process is None or self.process.poll() is not None:
            self.state, self.error = "FAILED", "PROCESS_EXITED"
            return False
        return True

    def render(self, text: str, output: Path, voice: str) -> None:
        with self.lock:
            if not self.ready():
                raise EngineUnavailable("ENGINE_NOT_READY")
            if voice != self.voice:
                raise EngineUnavailable("VOICE_NOT_LOADED")
            try:
                self.process.stdin.write(json.dumps({
                    "action": "render", "text": text, "output": str(output),
                    "voice": voice}, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                response = self._receive(self.render_timeout)
                if response.get("event") != "RENDERED":
                    raise EngineUnavailable("ENGINE_RENDER_FAILED")
                if not output.is_file() or output.stat().st_size < 100:
                    raise EngineUnavailable("INVALID_ENGINE_OUTPUT")
            except (OSError, ValueError, BrokenPipeError, EngineUnavailable) as exc:
                self.state, self.error = "FAILED", "PROCESS_FAILURE"
                self.stop(final_state="FAILED")
                raise EngineUnavailable("PROCESS_FAILURE") from exc

    def stop(self, final_state="STOPPED"):
        proc = self.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        self.state = final_state


class EngineSupervisor:
    def __init__(self, root: Path, voices=None, engine_factory=ResidentEngine):
        self.root = Path(root)
        self.voices = dict(DEFAULT_VOICES if voices is None else voices)
        self.engines = {
            engine: engine_factory(engine, voice, self.root)
            for engine, voice in self.voices.items()
        }
        self.threads = []
        self._stop = threading.Event()
        self._watchdog_thread = None
        self._restart_count = {name: 0 for name in self.engines}

    def _launch(self, engine):
        thread = threading.Thread(target=engine.start, daemon=True,
                                  name="amiga-model-" + engine.engine)
        thread.start()
        self.threads.append(thread)

    def start(self):
        self._stop.clear()
        for engine in self.engines.values():
            self._launch(engine)
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()

    def _watchdog(self):
        while not self._stop.wait(2):
            for name, engine in self.engines.items():
                if engine.ready():
                    self._restart_count[name] = 0
                    continue
                if any(t.name == "amiga-model-" + name and t.is_alive()
                       for t in self.threads):
                    continue
                if self._restart_count[name] >= 3:
                    continue
                self._restart_count[name] += 1
                if self._stop.wait(min(30, 2 ** self._restart_count[name])):
                    return
                engine.state = "RESTARTING"
                self._launch(engine)

    def report(self) -> dict:
        details = {}
        for name, engine in self.engines.items():
            ready = engine.ready()
            details[name] = {
                "status": "READY" if ready else engine.state,
                "voice": engine.voice}
            if engine.error and not ready:
                details[name]["code"] = engine.error
        all_ready = bool(details) and all(
            item["status"] == "READY" for item in details.values())
        return {"status": "READY" if all_ready else "NOT_READY",
                "engines": details, "required": list(self.engines)}

    def render(self, engine, voice, text, output):
        target = self.engines.get(engine)
        if target is None:
            raise EngineUnavailable("UNSUPPORTED_ENGINE")
        target.render(text, output, voice)

    def stop(self):
        self._stop.set()
        for engine in self.engines.values():
            engine.stop()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=3)
        for thread in self.threads:
            thread.join(timeout=1)
