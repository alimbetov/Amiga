"""Read-only initialization inventory DTO for the local Amiga TTS Worker.

Expected entries come from the EngineSupervisor startup configuration;
loaded entries reflect the live warmed subprocess state. Model files alone
never qualify a model as READY.
"""
from __future__ import annotations

import re
from pathlib import Path
from runtime_config import WorkerPaths

MODEL_IDS = {
    "kokoro": "hexgrad/Kokoro-82M",
    "piper": "piper-onnx",
}


def voice_locale(engine: str, voice: str) -> str | None:
    if engine == "piper":
        match = re.match(r"^([a-z]{2,3})_([A-Z]{2})-", voice)
        return f"{match.group(1)}-{match.group(2)}" if match else None
    if engine == "kokoro":
        # Kokoro a = American English; b = British English.
        if voice.startswith(("af_", "am_")):
            return "en-US"
        if voice.startswith(("bf_", "bm_")):
            return "en-GB"
    return None


def expected_artifacts(root: Path, engine: str, voice: str) -> list[dict]:
    if engine == "piper":
        directory = WorkerPaths.from_environment(root).models / "piper"
        return [
            {"role": "MODEL", "fileName": voice + ".onnx",
             "present": (directory / (voice + ".onnx")).is_file()},
            {"role": "CONFIG", "fileName": voice + ".onnx.json",
             "present": (directory / (voice + ".onnx.json")).is_file()},
        ]
    if engine == "kokoro":
        directory = WorkerPaths.from_environment(root).models / "kokoro"
        return [
            {"role": role, "fileName": name,
             "present": (directory / name).is_file()}
            for role, name in (
                ("CONFIG", "config.json"),
                ("MODEL", "kokoro-v1_0.pth"),
                ("VOICE_BUNDLE", voice + ".pt"))
        ]
    return []


def worker_health_info(root: Path, supervisor) -> dict:
    root = Path(root)
    if supervisor is None:
        return {
            "schemaVersion": 1,
            "status": "NOT_READY",
            "models": [],
            "languages": [],
            "summary": {"expectedModels": 0, "loadedModels": 0,
                        "expectedLanguages": 0, "loadedLanguages": 0},
            "reason": "MODEL_SUPERVISOR_NOT_STARTED",
        }
    report = supervisor.report()
    models = []
    languages = {}
    for engine, voice in supervisor.voices.items():
        runtime = report.get("engines", {}).get(engine, {})
        status = runtime.get("status", "NOT_READY")
        loaded = status == "READY"
        locale = voice_locale(engine, voice)
        artifacts = expected_artifacts(root, engine, voice)
        models.append({
            "engine": engine,
            "modelId": MODEL_IDS.get(engine, engine),
            "voiceCode": voice,
            "locale": locale,
            "expected": True,
            "loaded": loaded,
            "status": status,
            "artifacts": artifacts,
        })
        if locale is not None:
            item = languages.setdefault(locale, {"locale": locale, "variants": []})
            item["variants"].append({
                "engine": engine,
                "modelId": MODEL_IDS.get(engine, engine),
                "voiceCode": voice,
                "expected": True,
                "loaded": loaded,
                "status": status,
            })
    language_list = [languages[locale] for locale in sorted(languages)]
    return {
        "schemaVersion": 1,
        "status": report["status"],
        "models": models,
        "languages": language_list,
        "summary": {
            "expectedModels": len(models),
            "loadedModels": sum(bool(model["loaded"]) for model in models),
            "expectedLanguages": len(language_list),
            "loadedLanguages": sum(
                any(variant["loaded"] for variant in language["variants"])
                for language in language_list
            ),
        },
    }
