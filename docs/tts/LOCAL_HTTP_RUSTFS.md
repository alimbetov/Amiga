# Amiga TTS local HTTP API (Kokoro + Piper, optional RustFS)

**Scope:** a developer/local acceptance endpoint alongside `local_smoke.py`. It does not replace the Backend-authoritative worker claim/lease/fencing, approved model manifest or production output finalization from PR #2.

## Setup (PowerShell, Windows)

Start from `worker-kokoro`. Install S3 client only in Python used for the HTTP server:

```powershell
py -3.12 -m venv .venv-http
.\.venv-http\Scripts\python.exe -m pip install boto3
$env:AMIGA_TTS_API_TOKEN = "local-test-token-change-this-32-chars"
$env:AMIGA_ALLOW_INLINE_STORAGE = "1"
$env:AMIGA_STORAGE_ALLOWED_HOSTS = "127.0.0.1,localhost"
.\.venv-http\Scripts\python.exe .\local_http.py --host 127.0.0.1 --port 8765
```

The server invokes the installed `.venv-kokoro` or `.venv-piper` interpreter. Their models must already have been prepared with `local_smoke.py prepare`.

## LOCAL: no storage server or credentials needed

In a **second** PowerShell window:

```powershell
$headers = @{ Authorization = "Bearer local-test-token-change-this-32-chars" }
$body = @{
  jobId = "reminder-001"
  engine = "piper"
  locale = "kk-KZ"
  voice = "kk_KZ-iseke-x_low"
  text = "Сәлем! Бұл Амига."
  fileName = "reminder-001.wav"
  destination = @{ type = "local" }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8765/v1/tts/render -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Returns `201` with jobId, SHA-256, sizeBytes, localPath. Repeat the same jobId/request to receive `200 replayed:true` without rerendering. A different request under the same jobId returns `409`.

## Dev health and metrics

- `GET /health/live` returns `{"status":"UP"}` without authentication (bind HTTP service to localhost).
- `GET /health/ready` requires the Bearer token; checks engine Python executables and FFmpeg/ffprobe; returns 200 READY or 503 NOT_READY. It **does not** prove the models have been loaded/warmed or RustFS is reachable.
- `GET /metrics` requires Bearer token and returns counters `requests_total`, `render_success_total`, `render_failed_total`, `replay_total`; input text and credentials are excluded. Counters are in-memory and reset on restart; production metrics/alerts remain pending.

## Chunked MP3 (Kokoro/Piper)

A request with a `.mp3` `fileName` now uses the ordered pipeline:

```text
Normalize → sentence-aware chunks (240 characters max each)
→ render each chunk as PCM WAV → verify mono/stereo 16-bit PCM
→ concatenate PCM with 120-ms pauses
→ encode once using ffmpeg/libmp3lame (96 kb/s)
→ inspect MP3 with ffprobe → SHA-256
→ local file or S3/RustFS PUT (audio/mpeg) + HEAD metadata verification
```

Install **FFmpeg and ffprobe** on the HTTP Worker machine (both must be on PATH). Windows: install from a trusted FFmpeg distribution and confirm `ffmpeg -version` and `ffprobe -version` in PowerShell.

```json
{
  "jobId": "long-kk-001",
  "engine": "piper",
  "locale": "kk-KZ",
  "voice": "kk_KZ-iseke-x_low",
  "text": "Сәлем! Бүгін үзіліс жасауды ұмытпаңыз. Көзді демалдырып, серуендеп қайтыңыз.",
  "fileName": "long-kk-001.mp3",
  "destination": {"type": "local"}
}
```

For RustFS, use the same `destination.type=s3` settings as below. The generated S3 object gets `Content-Type: audio/mpeg`. HTTP response includes `chunkCount`, `contentType`, `sizeBytes`, `sha256`, `objectKey` on S3.

Safety bounds: 2000 input characters, at most 64 chunks, 10 minutes assembled PCM, 300 seconds per inference subprocess, 120 seconds MP3 encode. Mixed sample rates/channel formats fail closed rather than silently corrupt audio. A failed request leaves no final MP3; intermediary chunks are removed. This **dev adapter launches one model subprocess per chunk** (slow for Kokoro). Production needs warm persistent engine models, post-phonemization token limits, durable SQLite per-chunk checkpoints, Backend fencing and retry orchestration.

Compatibility: `.wav` file names keep the prior single-pass local smoke behavior. The code does not automatically convert existing WAV requests into MP3; explicitly request `.mp3`.

## RustFS/S3-compatible destination

Only enable inline credentials in an isolated developer environment. Configure `AMIGA_STORAGE_ALLOWED_HOSTS` with explicit known endpoint hosts. Local loopback HTTP is allowed; for other HTTP endpoints explicit `AMIGA_ALLOW_INSECURE_STORAGE=1` is required. Otherwise use HTTPS.

```json
{
  "jobId": "reminder-002",
  "engine": "piper",
  "locale": "kk-KZ",
  "voice": "kk_KZ-iseke-x_low",
  "text": "Сәлем!",
  "fileName": "reminder-002.wav",
  "destination": {
    "type": "s3",
    "endpoint": "http://127.0.0.1:9000",
    "bucket": "amiga-audio",
    "prefix": "tts",
    "region": "us-east-1",
    "accessKey": "EXAMPLE_ACCESS",
    "secretKey": "base64:RVhBTVBMRV9TRUNSRVQ="
  }
}
```

Both accessKey and secretKey may be raw strings or prefixed `base64:`. Base64 **is not encryption** and is reversible. Do not send credentials to an untrusted server, store raw request bodies in logs, or expose this endpoint to the internet.

HTTP API uploads WAV with S3 SigV4, reads the object back via `HEAD`, checks ContentLength and custom SHA-256 metadata. Returns `bucket`, `objectKey`, `verified:true` on success. No arbitrary per-request file URLs are supported; the returned objectKey is generated from `prefix/jobId/fileName` and the S3 endpoint is checked against the server allowlist.

Example test command:

```powershell
$env:AMIGA_STORAGE_ALLOWED_HOSTS = "127.0.0.1,localhost"
$env:AMIGA_ALLOW_INLINE_STORAGE = "1"
```

The request uses the existing Bearer token independently of RustFS credentials. Errors never return raw credentials or underlying S3 response contents.


## Object TTL / Expiration (local + RustFS)

The request accepts `destination.retention`. Supported explicit TTL values are **1, 7, 30, 90 days** (`86400`, `604800`, `2592000`, `7776000` seconds). Omit `retention` or use `{"mode":"PERMANENT"}` to disable automatic expiration. Unsupported TTLs (including hour-level precision) return `422 UNSUPPORTED_TTL`; this version intentionally does not promise exact-second S3 deletion.

Example: 

```json
{
  "jobId": "reminder-ttl-001",
  "engine": "piper",
  "locale": "kk-KZ",
  "voice": "kk_KZ-iseke-x_low",
  "text": "Сәлем! Демалатын уақыт келді.",
  "fileName": "reminder.wav",
  "destination": {
    "type": "s3",
    "endpoint": "http://127.0.0.1:9000",
    "bucket": "amiga-audio",
    "prefix": "tts",
    "region": "us-east-1",
    "accessKey": "EXAMPLE_ACCESS",
    "secretKey": "base64:RVhBTVBMRV9TRUNSRVQ=",
    "retention": {"mode": "TTL", "ttlSeconds": 604800}
  }
}
```

Responses now include `expiresAt` (UTC Unix seconds, nullable) and `ttlSeconds`. The SQLite `jobs` table stores expiry. Completed requests with expired TTL return **410 JOB_EXPIRED_USE_NEW_JOB_ID**; reusing the same ID with changed content still returns 409. This deliberately retains expired job ID tombstones to avoid idempotency violations. New requests require a new `jobId`.

**Local destination**: a background sweep every 60 seconds deletes expired WAV files, using path validation to avoid deleting files outside the worker's output directory. It retains completed metadata/tombstones. This sweep is best-effort and runs only while the HTTP service is running.

**RustFS / S3**: object keys use `<prefix>/ttl-7d/<jobId>/<fileName>`; the corresponding bucket Lifecycle Rule deletes objects after 7 days (asynchronous, not precise to the second). Files with permanent retention use `<prefix>/permanent/...`. The Worker NEVER removes other tenants' objects or bucket rules, and does not store inline credentials in SQLite.

### Provision lifecycle rules on the bucket

From `worker-kokoro`, install boto3 in `.venv-http` and set standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for a bucket administrator. Do not commit these credentials.

```powershell
.\.venv-http\Scripts\python.exe .\configure_lifecycle.py --endpoint http://127.0.0.1:9000 --bucket amiga-audio --prefix tts
# Review dry-run output first; then:
.\.venv-http\Scripts\python.exe .\configure_lifecycle.py --endpoint http://127.0.0.1:9000 --bucket amiga-audio --prefix tts --apply
```

The provisioning utility preserves unrelated lifecycle rules by ID, updates only `amiga-{1,7,30,90}d`, and reads back the bucket configuration after applying. Verify that your RustFS build actually executes Lifecycle expirations: successful S3 API configuration alone does **not** demonstrate physical deletion. If your RustFS version lacks working Lifecycle, use a separate cleanup service with persistent server-side credentials; the HTTP worker cannot guarantee remote cleanup from ephemeral inline credentials.

**Current Amiga ownership decision:** published RustFS audio expiration and `DeleteObject` are handled by Backend FileAsset Lifecycle, not by Worker. No `AMIGA_S3_LIFECYCLE_CONFIGURED` flag is required for upload. The `retention` field and `expiresAt` in this local adapter are metadata/replay policy only and do not guarantee deletion of a remote object. The lifecycle utility above is optional for isolated DEV testing and must not be applied blindly to production buckets. Backend must explicitly track references, expiry, and deletion retries.

**Important**: S3 Lifecycle Expiration runs on whole-day boundaries and can be delayed. `expiresAt` is a logical deadline; physical deletion may occur later. S3 versioning, if enabled, can leave noncurrent versions or delete markers; configure noncurrent-version policies separately. This slice does not implement object reference counting, TTL extension, remote DELETE scheduling, or a global multi-worker lifecycle controller.

## Constraints, next hardening

- Synchronous API; one inference at a time per process, timeout 300 seconds. No local HTTP job lease; only completed responses are stored in the SQLite idempotency table.
- MVP returns WAV; MP3 encoding/codec version and independent object lifecycle have not been implemented.
- The smoke file is generated before upload; upload failure leaves it locally for diagnosis, retry with same jobId is allowed because a result is stored only after upload verification.
- Acceptable only for trusted clients on a closed network. Production needs a queue/202 job model, model checksums, per-engine safe voice registry, bounded incoming concurrency, retry scheduling, deduplicated object creation, S3 endpoint pinning, TLS, signed grants, backend/fencing and audited secret handling.
- Tests: `PYTHONPATH=. python -m unittest discover -s tests -v`.

## GET /health/info — initialized model and language inventory

Token-protected endpoint. This reports **only the voices configured at worker initialization**, not every language theoretically supported by Kokoro or Piper.

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health/info -Headers @{
    Authorization = "Bearer local-test-token-change-this-32-chars"
}
```

Response shape (example after both default models finish resident warmup):

```json
{
  "schemaVersion": 1,
  "status": "READY",
  "summary": {
    "expectedModels": 2,
    "loadedModels": 2,
    "expectedLanguages": 2,
    "loadedLanguages": 2
  },
  "languages": [
    {
      "locale": "en-US",
      "variants": [
        {"engine": "kokoro", "modelId": "hexgrad/Kokoro-82M",
         "voiceCode": "af_heart", "expected": true, "loaded": true,
         "status": "READY"}
      ]
    },
    {
      "locale": "kk-KZ",
      "variants": [
        {"engine": "piper", "modelId": "piper-onnx",
         "voiceCode": "kk_KZ-iseke-x_low", "expected": true,
         "loaded": true, "status": "READY"}
      ]
    }
  ],
  "models": [
    {
      "engine": "kokoro", "modelId": "hexgrad/Kokoro-82M",
      "voiceCode": "af_heart", "locale": "en-US",
      "expected": true, "loaded": true, "status": "READY",
      "artifacts": [
        {"role": "SNAPSHOT_REFERENCE",
         "fileName": "huggingface-snapshot-path.txt", "present": true}
      ]
    },
    {
      "engine": "piper", "modelId": "piper-onnx",
      "voiceCode": "kk_KZ-iseke-x_low", "locale": "kk-KZ",
      "expected": true, "loaded": true, "status": "READY",
      "artifacts": [
        {"role": "MODEL", "fileName": "kk_KZ-iseke-x_low.onnx", "present": true},
        {"role": "CONFIG", "fileName": "kk_KZ-iseke-x_low.onnx.json", "present": true}
      ]
    }
  ]
}
```

**Interpretation:** `expected` is from the actual startup supervisor configuration. `loaded` becomes true only after child model construction and warmup. The `artifacts[].present` flag independently checks paths on disk and **does not** prove SHA-256 integrity or a usable model. If a child crashes after warmup, `loaded` becomes false and overall status becomes `NOT_READY`. An empty/not-started supervisor returns an empty inventory, never invented languages. Currently one selected voice per engine is resident; multiple simultaneous voices per engine require a later model pool design. The DTO never includes text, secret keys, local absolute paths or tokens.

`GET /TTS/ready` (also `/tts/ready`) reports overall warmed-model readiness; `GET /health/info` reports its inventory and per-model detail. The existing `GET /health/ready` is a weaker dependency-installation check.
