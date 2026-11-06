"""Standalone local smoke test of Kokoro, Qwen3-TTS and Piper.

This script intentionally never calls Amiga Backend or RustFS. Each engine runs
from its own Python environment. Models/output persist under --data-dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

ENGINES = ("kokoro", "qwen3", "piper")
DEFAULTS = {
    "kokoro": ("en-US", "af_heart", "Hello from Amiga. Please take a short break and rest your eyes."),
    "qwen3": ("ru-RU", "Ryan", "Привет! Это Amiga. Пора сделать небольшой перерыв и отдохнуть."),
    "piper": ("kk-KZ", "kk_KZ-iseke-x_low", "Сәлем! Бұл Амига. Біраз демалып, серуендеп қайтыңыз."),
}
QWEN_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
KOKORO_REPO = "hexgrad/Kokoro-82M"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_wav(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError("missing or empty WAV output")
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        frames = wav.getnframes()
        sample_width = wav.getsampwidth()
    if channels not in (1, 2) or sample_width not in (2, 3, 4) or rate < 8000 or frames == 0:
        raise RuntimeError("invalid WAV header or no audio frames")
    return {"sampleRate": rate, "channels": channels,
            "frames": frames, "durationSeconds": round(frames / rate, 3),
            "sizeBytes": path.stat().st_size, "sha256": sha256(path)}


def _env(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # This is set only for child Python processes before model-library imports.
    os.environ["HF_HOME"] = str(cache_dir / "huggingface")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def prepare(engine: str, data_dir: Path, voice: str) -> Path:
    models = data_dir / "models" / engine
    models.mkdir(parents=True, exist_ok=True)
    if engine == "kokoro":
        from huggingface_hub import snapshot_download
        snapshot = snapshot_download(repo_id=KOKORO_REPO,
                          allow_patterns=["*.json", "*.pth", "voices/*.pt"])
        (models / "huggingface-snapshot-path.txt").write_text(snapshot, encoding="utf-8")
        return models
    if engine == "qwen3":
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=QWEN_REPO, local_dir=str(models))
        return models
    if engine == "piper":
        # Official Piper CLI fetches matching .onnx and .onnx.json files.
        subprocess.run([sys.executable, "-m", "piper.download_voices",
                        "--data-dir", str(models), voice], check=True)
        if not (models / (voice + ".onnx")).is_file() or not (models / (voice + ".onnx.json")).is_file():
            raise RuntimeError("Piper voice model or matching config is missing")
        return models
    raise ValueError("unsupported engine")


def synthesize(engine: str, models: Path, text: str, voice: str,
               output: Path, device: str) -> None:
    if not text.strip():
        raise ValueError("nonempty text required")
    output.parent.mkdir(parents=True, exist_ok=True)
    if engine == "kokoro":
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
        # The official KPipeline uses Hugging Face cached weights; it can
        # resolve the model itself. The preparatory snapshot documents assets.
        pipeline = KPipeline(lang_code="a")
        audio_parts = [audio for _, _, audio in pipeline(text, voice=voice)]
        if not audio_parts:
            raise RuntimeError("Kokoro produced no audio")
        sf.write(str(output), np.concatenate(audio_parts), 24000, subtype="PCM_16")
    elif engine == "qwen3":
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel
        if not models.is_dir() or not any(models.iterdir()):
            raise RuntimeError("Qwen3 model not downloaded; run prepare first")
        use_cuda = device.startswith("cuda")
        dtype = torch.bfloat16 if use_cuda else torch.float32
        model = Qwen3TTSModel.from_pretrained(
            str(models), device_map=device, dtype=dtype
        )
        wavs, rate = model.generate_custom_voice(
            text=text, language="Russian", speaker=voice
        )
        if not wavs:
            raise RuntimeError("Qwen3 produced no audio")
        sf.write(str(output), wavs[0], rate, subtype="PCM_16")
    elif engine == "piper":
        if not (models / (voice + ".onnx")).is_file():
            raise RuntimeError("Piper model not downloaded; run prepare first")
        subprocess.run([
            sys.executable, "-m", "piper", "-m", voice,
            "--data-dir", str(models), "-f", str(output), "--", text
        ], check=True)
    else:
        raise ValueError("unsupported engine")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amiga local TTS smoke, no Backend or RustFS")
    parser.add_argument("action", choices=("prepare", "run", "both"))
    parser.add_argument("--engine", choices=ENGINES, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("local-tts-data"))
    parser.add_argument("--output-dir", type=Path, default=Path("local-tts-output"))
    parser.add_argument("--voice", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--device", default="cpu", help="Qwen3: cpu or cuda:0; Mac MPS requires separate qualification")
    args = parser.parse_args(argv)
    locale, default_voice, default_text = DEFAULTS[args.engine]
    voice = args.voice or default_voice
    text = args.text if args.text is not None else default_text
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    _env(data_dir / "cache")
    model_dir = data_dir / "models" / args.engine
    if args.action in ("prepare", "both"):
        model_dir = prepare(args.engine, data_dir, voice)
        print(json.dumps({"event": "MODELS_DOWNLOADED", "engine": args.engine,
                          "localPath": str(model_dir)}, ensure_ascii=False))
    if args.action in ("run", "both"):
        out = output_dir / (args.engine + ".wav")
        start = time.perf_counter()
        synthesize(args.engine, model_dir, text, voice, out, args.device)
        report = validate_wav(out)
        report.update({"event": "LOCAL_TTS_SUCCESS", "engine": args.engine,
                       "locale": locale, "voice": voice, "output": str(out),
                       "elapsedSeconds": round(time.perf_counter() - start, 3),
                       "rustfsUsed": False, "backendUsed": False})
        report_file = output_dir / (args.engine + ".json")
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
