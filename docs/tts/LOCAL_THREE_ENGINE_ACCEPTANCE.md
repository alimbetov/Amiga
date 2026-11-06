# Amiga — Local 3-Engine TTS Acceptance (NO Backend / NO RustFS)

This is **standalone native inference**, not the production Self-Managed Worker bootstrap. It deliberately bypasses `NO_MANIFEST`, PostgreSQL, claim/leases, RustFS, and global job finalization. Its purpose is to verify that each model can download and synthesize audible local speech on your machine.

## Machine prerequisites

- macOS (Apple Silicon or Intel) / Linux. Python **3.12** recommended.
- Several GB free for Qwen3 and dependencies. Qwen3's 0.6B variant is much heavier than Kokoro or Piper.
- Internet for **setup** and **prepare** only. Run after weights are cached.
- macOS: `brew install python@3.12 espeak-ng ffmpeg` (Homebrew required). `ffmpeg` is optional if you only need WAV.
- If Apple Silicon Qwen3 MPS fails on an unsupported operation, run on CPU, or use a supported CUDA machine. CPU inference can be slow and memory-intensive.
- Piper model and `MODEL_CARD` require commercial-license review independently of this test.

## Checkout PR branch

```bash
git clone https://github.com/alimbetov/Amiga.git
cd Amiga
git fetch origin feat/local-three-engine-tts-smoke
git switch --track origin/feat/local-three-engine-tts-smoke
cd worker-kokoro
```

## Three engines (single command per stage)

```bash
bash scripts/local-three-engine.sh setup
bash scripts/local-three-engine.sh prepare
bash scripts/local-three-engine.sh run
```

Or do all stages: `bash scripts/local-three-engine.sh all`.

`setup` creates `.venv-kokoro`, `.venv-qwen3` and `.venv-piper` to avoid dependency conflicts. `prepare` downloads original model files to the Hugging Face/Piper caches. `run` produces three WAV files, validates headers, and writes JSON reports.

Output (ignored by Git):

```text
worker-kokoro/
  local-tts-data/
    cache/huggingface/
    models/kokoro/
    models/qwen3/
    models/piper/
  local-tts-output/
    kokoro.wav + kokoro.json      # English / af_heart
    qwen3.wav  + qwen3.json       # Russian / Ryan
    piper.wav  + piper.json       # Kazakh / iseke x_low
```

Run one engine only:

```bash
.venv-kokoro/bin/python local_smoke.py both --engine kokoro
.venv-qwen3/bin/python local_smoke.py both --engine qwen3 --device cpu
.venv-piper/bin/python local_smoke.py both --engine piper
```

To override the voice and text:

```bash
.venv-kokoro/bin/python local_smoke.py run --engine kokoro --voice am_michael --text "Good morning. Time for a break."
```

Play on Mac:

```bash
afplay local-tts-output/kokoro.wav
afplay local-tts-output/qwen3.wav
afplay local-tts-output/piper.wav
```

Optional MP3 transcode (not necessary for local smoke):

```bash
ffmpeg -y -i local-tts-output/kokoro.wav -codec:a libmp3lame -q:a 3 local-tts-output/kokoro.mp3
```

## Acceptance checklist

1. Each engine's `prepare` completes, and the expected local model files are present.
2. WAV exists, opens in `afplay`, has nonzero duration, and a valid sample rate/frame count.
3. English Kokoro, Russian Qwen3 and Kazakh Piper are comprehensible to a human reviewer.
4. JSON reports explicitly show `backendUsed:false` and `rustfsUsed:false`; inspect SHA-256, duration, file size and elapsed time.
5. Repeated `run` after `prepare` does not re-download models unless an engine requires a cache repair.
6. For full offline testing, disconnect internet **after prepare** and rerun. Hugging Face libraries can attempt metadata lookups; strict network-isolation behavior requires separate validation.
7. Unit tests: `PYTHONPATH=. python -m unittest discover -s tests -v`.

## Troubleshooting

- **Kokoro espeak-ng missing:** install `espeak-ng` and ensure it is discoverable in PATH.
- **Qwen3 CPU performance:** run only its single engine, close other workloads, and avoid running all 3 inference processes in parallel. Use a GPU/CUDA machine if available.
- **Qwen3 speaker:** `Ryan` is a model preset; verify `get_supported_speakers()` for the selected checkpoint.
- **Piper voice not found:** rerun `prepare`; it should yield `kk_KZ-iseke-x_low.onnx` and `.onnx.json`. Inspect the voice MODEL_CARD.
- **A mismatch in expected language/voice quality:** do not mark that voice qualified for the international release.
- **Package compatibility:** if pip cannot install on a particular Mac/Python architecture, record the exact failure; do not claim acceptance for that platform.

## Architecture boundary

The local smoke runner is intentionally not wired into SQLite Inbox or any production model registry. After local qualification, integrate the adapters into the actual Worker state machine, add immutable model checksums and manifest enrollment, then run RustFS/Backend E2E separately.

Primary upstream documentation:
- https://github.com/hexgrad/kokoro
- https://github.com/QwenLM/Qwen3-TTS
- https://github.com/OHF-Voice/piper1-gpl
- https://huggingface.co/rhasspy/piper-voices
