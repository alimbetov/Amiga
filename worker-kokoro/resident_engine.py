"""Resident single-engine TTS process: loads an actual model before announcing READY.

stdin/stdout: one JSON object per line. Used only by the local development HTTP
worker; stdout is reserved for protocol, underlying model diagnostics go to stderr.
"""
from __future__ import annotations
import argparse
import json
import sys
import wave
from pathlib import Path


def send(event, **data):
    sys.stdout.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=("kokoro", "piper"))
    parser.add_argument("--voice", required=True)
    parser.add_argument("--models", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.engine == "kokoro":
            import numpy as np
            import soundfile as sf
            from kokoro import KPipeline, KModel
            import torch
            import os
            # Pinned standalone mode loads ALL model bytes from the verified
            # local directory, not from an unrelated Hugging Face cache.
            config = args.models / "config.json"
            weights = args.models / "kokoro-v1_0.pth"
            voice_path = args.models / (args.voice + ".pt")
            pinned = os.environ.get("TTS_MODEL_MANIFEST", "").strip()
            if pinned:
                if not all(p.is_file() for p in (config, weights, voice_path)):
                    raise FileNotFoundError("Kokoro pinned config/weights/voice missing")
                model = KModel(config=str(config), model=str(weights)).eval()
                pipeline = KPipeline(lang_code="b" if args.voice.startswith("b") else "a",
                                     model=model)
                voice_ref = torch.load(str(voice_path), map_location="cpu",
                                       weights_only=True)
            else:
                # Backwards-compatible dev smoke path only. Reproducible
                # standalone deployment requires a pinned manifest above.
                pipeline = KPipeline(lang_code="b" if args.voice.startswith("b") else "a")
                voice_ref = args.voice
            probe = list(pipeline("Ready.", voice=voice_ref))
            if not probe:
                raise RuntimeError("Kokoro warmup produced no audio")
            def render(text, output):
                samples = [audio for _, _, audio in pipeline(text, voice=voice_ref)]
                if not samples:
                    raise RuntimeError("empty Kokoro output")
                sf.write(str(output), np.concatenate(samples), 24000, subtype="PCM_16")
        else:
            from piper import PiperVoice
            model = args.models / (args.voice + ".onnx")
            config = args.models / (args.voice + ".onnx.json")
            if not model.is_file() or not config.is_file():
                raise FileNotFoundError("Piper model or config unavailable")
            voice = PiperVoice.load(str(model))
            # An actual (small) synthesis exercises phonemization and ONNX runtime.
            import io
            with wave.open(io.BytesIO(), "wb") as probe:
                voice.synthesize_wav("Ready.", probe)
            def render(text, output):
                with wave.open(str(output), "wb") as wav:
                    voice.synthesize_wav(text, wav)
        send("READY", engine=args.engine, voice=args.voice)
    except Exception as exc:
        print(f"Engine startup failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        send("FAILED", engine=args.engine, voice=args.voice, code="MODEL_LOAD_FAILED")
        return 1
    for line in sys.stdin:
        try:
            task = json.loads(line)
            if task.get("action") == "ping":
                send("PONG")
                continue
            if task.get("action") != "render" or task.get("voice") != args.voice:
                send("FAILED", code="UNSUPPORTED_ACTION_OR_VOICE")
                continue
            text, dest = task.get("text"), task.get("output")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise ValueError("invalid text")
            if not isinstance(dest, str):
                raise ValueError("invalid destination")
            output = Path(dest)
            output.parent.mkdir(parents=True, exist_ok=True)
            render(text, output)
            if not output.is_file() or output.stat().st_size < 100:
                raise RuntimeError("no audio")
            send("RENDERED")
        except Exception as exc:
            print(f"Engine render failed: {type(exc).__name__}", file=sys.stderr, flush=True)
            send("FAILED", code="RENDER_FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
