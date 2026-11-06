# Portable Self-Managed TTS Worker

A reusable Kokoro/Piper text-to-speech worker: local SQLite durable Inbox,
resident engine processes, WAV checkpoints, ordered PCM assembly, one MP3,
optional S3/RustFS upload, and automatic local cleanup. **No Backend required**
for standalone operation. Backend-specific leasing and authoritative FileAsset
finalization are separate and not yet completed.

## Recommended deployment

See **[deploy/README.md](deploy/README.md)** and
**[deploy/compose.yaml](deploy/compose.yaml)**.

```bash
cd worker-kokoro/deploy
cp .env.example .env
cp models-manifest.example.json models-manifest.json
# Supply real reviewed SHA-256/size/HTTPS URLs and an API token.
docker compose config
docker compose up -d --build
docker compose logs -f worker
```

Current Compose is a CPU **development reference**; clean-machine Docker build,
actual Kokoro/Piper inference and real RustFS acceptance are separate release
gates. Do not start with template model placeholders.

## Portable configuration

- `TTS_API_TOKEN`: HTTP Bearer token (required).
- `TTS_ENGINE_VOICES_JSON`: configured resident voices, for example
  `{"kokoro":"af_heart","piper":"kk_KZ-iseke-x_low"}`, or a single engine.
- `TTS_MODEL_MANIFEST` and `TTS_MODEL_ALLOWED_HOSTS`: approved immutable
  HTTPS model assets with SHA-256 and byte lengths. Startup checks the local
  volume, optionally restores from RustFS, otherwise downloads and verifies.
- `TTS_MODEL_MIRROR_BUCKET`, `TTS_MODEL_MIRROR_ENDPOINT` and standard AWS
  environment/secret credentials: **optional RustFS backup of approved model
  weights**. Content-addressed objects are never removed by the Worker.
- `TTS_OUTPUT_DIR`: persistent SQLite and intermediate audio volume.
- `TTS_MODEL_DATA_DIR`: persistent versioned-model storage volume.
- `TTS_MIN_FREE_MB`: admission check before generating new audio.
- `TTS_STORAGE_PROFILES_JSON`: named S3 output destinations for durable jobs.
  Secrets are not persisted in SQLite job snapshots.

Legacy `AMIGA_*` settings remain as backwards-compatible aliases where
implemented; newly integrated projects should use `TTS_*`.

## Job processing

`POST /v1/tts/jobs` -> 202, `GET /v1/tts/jobs/{jobId}` -> status.
The legacy synchronous `POST /v1/tts/render` remains for development.

```text
PENDING -> PROCESSING -> chunk WAV + SHA-256 checkpoints
-> verified single MP3 -> S3 HEAD/PUT/HEAD (or local)
-> atomic SQLite READY
```

A hard kill leaves durable WAV/MP3 files for validated replay. S3 collisions
are rejected. SQLite stores terminal Inbox/cache for 30 days; the local janitor
cleans expired records and old terminal temporary files. **Only Backend**
is allowed to delete published RustFS audio according to FileAsset TTL.

`GET /health/live` is HTTP process liveness. Token-protected `/TTS/ready`
requires configured resident models warmed. `/health/info` reports expected
and loaded engines, voice variants and model artifact availability.

## Maintenance and tests

```bash
cd worker-kokoro
PYTHONPATH=. python3.12 -m unittest discover -s tests -v
python3.12 doctor.py
python3.12 sqlite_maintenance.py check --db local-http-output/jobs.sqlite
python3.12 sqlite_maintenance.py backup --db local-http-output/jobs.sqlite --output backup/jobs.sqlite
```

SQLite restore is intentionally offline-only and refuses existing destinations.
A portable OS lock prevents two Worker processes from sharing a state directory.

## Acceptance status

Unit CI (Python 3.10/3.12), HTTP socket concurrency tests, hard process exit,
SQLite backup/restore, model mirror offline recovery and mock S3 integrity tests
exist. **Not yet qualified:** real Kokoro/Piper integration with the pinned
manifest on a fresh CPU container, actual RustFS multipart behavior and
integrity readback, 24-hour soak, Backend lease/fencing/finalization, or a
fully hash-locked transitive dependency graph.

Keep PR #2 Draft until appropriate real E2E gates pass. Additional project
requirements: [hardening specification](../docs/tts/WORKER_HARDENING_TZ.md).
