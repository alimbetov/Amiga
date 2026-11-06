#!/usr/bin/env bash
set -euo pipefail
# Local-only macOS/Linux smoke. Separate virtualenvs isolate Torch/Qwen dependencies.
# Usage: bash scripts/local-three-engine.sh setup|prepare|run|all [python executable]
ACTION="${1:-all}"
PYTHON="${2:-python3.12}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATA_DIR="${AMIGA_TTS_LOCAL_DATA:-$ROOT/local-tts-data}"
OUT_DIR="${AMIGA_TTS_LOCAL_OUTPUT:-$ROOT/local-tts-output}"
if [[ "$ACTION" != "setup" && "$ACTION" != "prepare" && "$ACTION" != "run" && "$ACTION" != "all" ]]; then
  echo "Usage: $0 setup|prepare|run|all [python3.12]" >&2
  exit 2
fi
setup_engine() {
  local engine="$1" py="$ROOT/.venv-$1/bin/python"
  if [[ ! -x "$py" ]]; then "$PYTHON" -m venv "$ROOT/.venv-$engine"; fi
  "$py" -m pip install --upgrade pip
  case "$engine" in
    kokoro) "$py" -m pip install 'kokoro>=0.9.4' soundfile numpy huggingface_hub ;;
    qwen3) "$py" -m pip install qwen-tts soundfile huggingface_hub ;;
    piper) "$py" -m pip install piper-tts ;;
  esac
}
run_engine() {
  local engine="$1" action="$2" py="$ROOT/.venv-$1/bin/python"
  if [[ ! -x "$py" ]]; then echo "Missing virtualenv $py; run setup" >&2; exit 3; fi
  "$py" "$ROOT/local_smoke.py" "$action" --engine "$engine" \
    --data-dir "$DATA_DIR" --output-dir "$OUT_DIR" \
    --device "${AMIGA_QWEN_DEVICE:-cpu}"
}
if [[ "$ACTION" == "setup" || "$ACTION" == "all" ]]; then
  for e in kokoro qwen3 piper; do setup_engine "$e"; done
fi
if [[ "$ACTION" == "prepare" || "$ACTION" == "all" ]]; then
  for e in kokoro qwen3 piper; do run_engine "$e" prepare; done
fi
if [[ "$ACTION" == "run" || "$ACTION" == "all" ]]; then
  for e in kokoro qwen3 piper; do run_engine "$e" run; done
  echo "Generated WAV and JSON reports in $OUT_DIR"
fi
