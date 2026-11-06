# Portable TTS Worker — standalone deployment (D1/D2)

This is the **generic** Kokoro/Piper Worker, reusable across projects. It needs
no Amiga-specific Backend to render local audio or upload MP3 to S3-compatible
RustFS. Production Backend claiming/finalization is a separate integration.

## Startup source order and backup

For each pinned asset in `models-manifest.json`:

1. Use already-cached local file **only after byte length + SHA-256 verification**.
2. Otherwise restore from RustFS `tts-models/sha256/<sha256>` and hash actual downloaded bytes.
3. Otherwise fetch exactly the approved HTTPS URL, without redirects, and check bytes.
4. Atomically activate the local file; best-effort back it up to RustFS if configured.
5. Load pinned Kokoro/Piper model into resident process and run warmup before `/TTS/ready=READY`.

No model is downloaded automatically if manifest is missing. The Compose setup
requires a manifest. RustFS is optional: the Worker continues from a verified
local cache without network. To REQUIRE successful backups, set
`TTS_MODEL_MIRROR_REQUIRED=1` (use cautiously, because RustFS outages then fail
initialization). Without a mirror configured, this is a fully standalone worker.

**Manifest must contain actual published checksums/sizes and approved licensing**.
Do not copy placeholder values into production. Every required file has one
`assets[]` entry with `engine`, `fileName`, `url`, `sha256`, `sizeBytes`.
For Piper, include `<voice>.onnx` and `<voice>.onnx.json`.
For Kokoro, include `config.json`, `kokoro-v1_0.pth` and
`<voice>.pt` (for example `af_heart.pt`).
All entries of a given engine are stored in the same verified model directory.
The Kokoro resident loader uses these files directly, not an independent
Hugging Face download, when `TTS_MODEL_MANIFEST` is set.

### Linux CPU / Docker Compose

```bash
cd worker-kokoro/deploy
cp .env.example .env
cp models-manifest.example.json models-manifest.json
# Edit both. Replace all placeholder model paths, SHA-256, sizes and secrets.
docker compose config
docker compose up -d --build
docker compose logs -f worker
```

```bash
curl -fsS http://127.0.0.1:8765/health/live
curl -fsS -H "Authorization: Bearer $TTS_API_TOKEN" \
  http://127.0.0.1:8765/health/info
curl -fsS -H "Authorization: Bearer $TTS_API_TOKEN" \
  http://127.0.0.1:8765/TTS/ready
```

`/health/info` reports expected and actually loaded models/voice variants.
`/TTS/ready` is 503 until both required voices are warmed. The Docker health
check intentionally tests process liveness, not model readiness (models may
take significant time to load). Bind the published port only to loopback;
use HTTPS reverse proxy and separate auth if accessed remotely.

Configure `TTS_ENGINE_VOICES_JSON` for the set of required resident models. A project needing only one engine may set `{"piper":"kk_KZ-iseke-x_low"}`; readiness then requires only that configured engine. Unconfigured engine/voice requests are rejected rather than silently adding models.\n\nThe Compose setup persists `tts-state` (SQLite, WAV/MP3 checkpoints) and
`tts-models` (validated model weights). No downloaded model is baked into
the image. Do **not** mount the same state volume into two Worker instances:
single-instance OS lock forbids concurrent executors. Model sharing can use
RustFS mirror instead.

**S3/RustFS mirror:** supply `TTS_MODEL_MIRROR_BUCKET`,
`TTS_MODEL_MIRROR_ENDPOINT`, plus standard AWS credentials securely.
Never commit production secrets in `.env`. The endpoint must resolve from
*inside* the container (Docker `localhost` is not the RustFS host). S3 model
objects are content-addressed and must not be expired together with temporary
TTS MP3 outputs. Remote model deletion is not managed by this Worker.

### Developer native mode

```bash
cd worker-kokoro
python3.12 -m venv .venv-kokoro
.venv-kokoro/bin/pip install 'kokoro==0.9.4' soundfile numpy
python3.12 -m venv .venv-piper
.venv-piper/bin/pip install 'piper-tts==1.3.0'
python3.12 -m pip install boto3
python3.12 doctor.py
# Set TTS_API_TOKEN and TTS_MODEL_MANIFEST, TTS_MODEL_ALLOWED_HOSTS
python3.12 local_http.py
```

For Windows substitute `.venv-*/Scripts/python.exe` and use PowerShell
environment variables. The CLI `python standalone_models.py --manifest ... --model-root ... --allowed-hosts ...`
also supports explicit bootstrap without launching HTTP.

### SQLite backup and restore

From `worker-kokoro`:

```bash
python3.12 sqlite_maintenance.py check --db /var/lib/tts/state/jobs.sqlite
python3.12 sqlite_maintenance.py backup --db /var/lib/tts/state/jobs.sqlite \
  --output /backup/tts-jobs.sqlite
```

Restore is offline-only and refuses to overwrite a DB:

```bash
# Stop Worker and move existing state out of the way first.
python3.12 sqlite_maintenance.py restore --input /backup/tts-jobs.sqlite \
  --db /var/lib/tts/state/jobs.sqlite --worker-stopped
```

The online SQLite Backup API preserves a consistent snapshot even with WAL
enabled. The model volume is separate and should be backed up via RustFS
content-addressed mirror or host snapshot. Keep off-host backups and test
restore procedures. The 30-day Inbox retention is not a substitute for backup.

## Release qualification limits

The pinned **direct** dependency versions in Dockerfile are a development
baseline, *not a fully hash-locked transitive dependency set*. Before release
pin the full wheel graph and image digest, run vulnerability and license checks.
Piper package licensing and the chosen voice/model license must be reviewed
before commercial use.

Not yet accepted: real clean-machine Docker image build, Kokoro/Piper warmup
on target CPU, real RustFS mirror backup/restore, duration/throughput soak,
Backend lease/fencing/finalization and crash testing with actual models.
Do not consider this production certified merely because unit CI is green.
