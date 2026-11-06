# Reference CPU development image. Qualify on a clean runner before production use.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /opt/tts-worker
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng libsndfile1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY . /opt/tts-worker
# Isolate engine dependencies, but keep fixed Python 3.12 ABI.
RUN python -m venv .venv-kokoro && .venv-kokoro/bin/pip install --no-cache-dir \
      'kokoro==0.9.4' 'soundfile==0.13.1' 'numpy==2.2.6' && \
    python -m venv .venv-piper && .venv-piper/bin/pip install --no-cache-dir \
      'piper-tts==1.3.0' && \
    pip install --no-cache-dir 'boto3==1.43.125'
# User writable data on volumes. Production should pin transitive wheels by hashes.
RUN mkdir -p /var/lib/tts/state /var/lib/tts/models && \
    useradd -u 10001 -m -s /usr/sbin/nologin tts && \
    chown -R tts:tts /var/lib/tts /opt/tts-worker
USER tts
ENV TTS_HOST=0.0.0.0 TTS_PORT=8765 \
    TTS_OUTPUT_DIR=/var/lib/tts/state \
    TTS_MODEL_DATA_DIR=/var/lib/tts/models \
    TTS_REQUIRE_MODEL_MANIFEST=1 \
    TTS_MIN_FREE_MB=256
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health/live',timeout=3)"
CMD ["python", "local_http.py"]
