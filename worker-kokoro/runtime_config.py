"""Reusable Worker runtime settings (TTS_* preferred, AMIGA_* legacy aliases).

No secrets are printed or persisted. Filesystem roots are resolved once and
may be mounted outside the repository for containerized operation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def setting(name: str, default: str = "", legacy: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None and legacy is not None:
        value = os.environ.get(legacy)
    return default if value is None else value


def positive_int(name: str, default: int, lower: int = 1,
                 upper: int = 1_000_000) -> int:
    raw = setting(name, str(default))
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not lower <= number <= upper:
        raise ValueError(f"{name} must be in [{lower},{upper}]")
    return number


@dataclass(frozen=True)
class WorkerPaths:
    code_root: Path
    output: Path
    model_data: Path

    @property
    def models(self) -> Path:
        return self.model_data / "models"

    @classmethod
    def from_environment(cls, code_root: str | Path):
        base = Path(code_root).resolve()
        output = Path(setting("TTS_OUTPUT_DIR",
                              str(base / "local-http-output"))).expanduser().resolve()
        model_data = Path(setting("TTS_MODEL_DATA_DIR",
                                  str(base / "local-tts-data"))).expanduser().resolve()
        if output == model_data or output == base or model_data == base:
            raise ValueError("Worker data directories must be distinct from code root")
        return cls(base, output, model_data)

    def prepare(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.model_data.mkdir(parents=True, exist_ok=True)
        return self


def check_free_disk(path: Path, minimum_mb: int) -> dict:
    import shutil
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    threshold = minimum_mb * 1024 * 1024
    return {"freeBytes": free, "minimumBytes": threshold,
            "sufficient": free >= threshold}
