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

Only after independently provisioning/validating the lifecycle on the target bucket, start the HTTP server with:

```powershell
$env:AMIGA_S3_LIFECYCLE_CONFIGURED = "1"
```

Without that flag, an S3 upload requesting TTL fails closed with **409 S3_LIFECYCLE_NOT_CONFIGURED**. This flag is operator acknowledgement, not an automatic remote verification. For each bucket and prefix, the rule must match the value supplied in the request. A changed prefix requires corresponding lifecycle rules.

**Important**: S3 Lifecycle Expiration runs on whole-day boundaries and can be delayed. `expiresAt` is a logical deadline; physical deletion may occur later. S3 versioning, if enabled, can leave noncurrent versions or delete markers; configure noncurrent-version policies separately. This slice does not implement object reference counting, TTL extension, remote DELETE scheduling, or a global multi-worker lifecycle controller.

## Constraints, next hardening

- Synchronous API; one inference at a time per process, timeout 300 seconds. No local HTTP job lease; only completed responses are stored in the SQLite idempotency table.
- MVP returns WAV; MP3 encoding/codec version and independent object lifecycle have not been implemented.
- The smoke file is generated before upload; upload failure leaves it locally for diagnosis, retry with same jobId is allowed because a result is stored only after upload verification.
- Acceptable only for trusted clients on a closed network. Production needs a queue/202 job model, model checksums, per-engine safe voice registry, bounded incoming concurrency, retry scheduling, deduplicated object creation, S3 endpoint pinning, TLS, signed grants, backend/fencing and audited secret handling.
- Tests: `PYTHONPATH=. python -m unittest discover -s tests -v`.
