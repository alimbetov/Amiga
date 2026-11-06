"""Portable, read-only Worker diagnostic preflight; no sensitive output."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from runtime_config import WorkerPaths, check_free_disk


def doctor(code_root: Path, *, min_free_mb: int = 256,
           require_models: bool = False) -> dict:
    paths = WorkerPaths.from_environment(code_root)
    checks = {}
    checks["python"] = {"ok": sys.version_info[:2] == (3, 12),
                        "version": f"{sys.version_info.major}.{sys.version_info.minor}"}
    checks["ffmpeg"] = {"ok": bool(shutil.which("ffmpeg"))}
    checks["ffprobe"] = {"ok": bool(shutil.which("ffprobe"))}
    disk = check_free_disk(paths.output, min_free_mb)
    checks["disk"] = {"ok": disk["sufficient"], **disk}
    try:
        conn = sqlite3.connect(":memory:")
        try:
            checks["sqlite"] = {"ok": conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"}
        finally:
            conn.close()
    except sqlite3.Error:
        checks["sqlite"] = {"ok": False}
    engines = {}
    for engine in ("kokoro", "piper"):
        root = paths.code_root / (".venv-" + engine)
        executable = ((root / "bin" / "python").is_file()
                      or (root / "Scripts" / "python.exe").is_file())
        engines[engine] = {"ok": executable, "installed": executable}
    checks["engines"] = engines
    if require_models:
        checks["modelData"] = {"ok": paths.models.is_dir()
                              and any(paths.models.rglob("*.onnx"))}
    return {"status": "PASS" if all(c.get("ok", False) for c in
                                  checks.values() if isinstance(c, dict)
                                  and "ok" in c) and
            all(x["ok"] for x in engines.values()) else "FAIL",
            "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-free-mb", type=int, default=256)
    parser.add_argument("--require-models", action="store_true")
    args = parser.parse_args()
    report = doctor(Path(__file__).resolve().parent,
                    min_free_mb=args.min_free_mb, require_models=args.require_models)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
