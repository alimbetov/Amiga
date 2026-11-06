# Amiga Worker — initial foundation

Python 3.10+ standard library SQLite foundation. **Not yet a running TTS service.**

This initial implementation provides transactional local Inbox duplicate detection, state transitions, cache metadata and 30-day retention, persistent model registry schema, and deterministic conservative chunk planning/fingerprint. It also validates operator-pinned upstream seed catalogs, permits bootstrap only after explicit Backend NO_MANIFEST, and constructs deterministic LOCAL_STAGED/ASSETS_UPLOADED candidate descriptors. W2B now includes a checkpointed fallback coordinator and HTTPS transport for upstream download, scoped RustFS PUT grants, authoritative integrity checking, candidate proposals and approved-manifest reconciliation. Its unit/integration-style tests use fake network services. **A real Backend implementation, actual RustFS integration, model activation/inference, PCM assembly and distributed fencing are still missing.**

Run locally from this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

W2B HTTP API contract: [bootstrap contract](../docs/tts/W2B_BOOTSTRAP_HTTP_CONTRACT.md). The seed example is intentionally empty; only an operator-reviewed, checksum-pinned seed can bootstrap. W2B requires explicit Backend NO_MANIFEST, not network errors. Returning MANIFEST_APPROVED does not mean model runtime READY.\n\nImplementation plan: [worker tasks](../docs/tts/WORKER_TASKS.md) and [acceptance matrix](../docs/tts/WORKER_ACCEPTANCE.md).

Reminder: direct SQLite connection access assumes one process controls the local volume; additional concurrency management and global Backend lease/fencing are prerequisites for production.

## SQLite maintenance: 30 days

The worker uses SQLite for crash-safe Inbox, chunk checkpoints and bounded cache metadata, not as a permanent audit archive. **Backend** is the authoritative owner of FileAsset lifecycle and RustFS object deletion.

- `inbox_job`: terminal jobs (`READY`, `FAILED`, `CANCELLED`) expire **30 days after their terminal transition**; `RECEIVED`, `PROCESSING` and `RETRY_PENDING` are never purged purely because of age.
- `tts_chunk`: foreign key `ON DELETE CASCADE` removes completed job chunk metadata together with its Inbox row.
- `tts_cache`: metadata expires after 30 days from its last successful upsert; deleting it **does not delete** the RustFS object.
- `model_registry` and model bootstrap state: retained; the janitor never deletes active model identities or weights.
- Developer HTTP endpoint's separate `local-http-output/jobs.sqlite`: completed replay metadata is limited to 30 days, but entries with a future audio TTL are held until expiry to avoid returning an invalid result prematurely. Its existing 60-second sweep handles local WAV expiration.
- Purging uses small transactions (default 500 records per table, up to 20 batches each pass) and a passive WAL checkpoint. SQLite WAL/DB file size is not guaranteed to shrink immediately; online VACUUM is deliberately avoided while the worker is serving requests.

Run one cleanup pass (from `worker-kokoro`):

```powershell
py -3.12 -m amiga_worker.janitor --db .\\data\\worker.sqlite --once
```

Run periodic janitor in the **same SQLite owner process** with `SQLiteJanitor(store).serve(stop_event)`, or the standalone maintenance process when no other worker is using the DB:

```powershell
py -3.12 -m amiga_worker.janitor --db .\\data\\worker.sqlite --interval 3600
```

The production Worker runtime has not been wired to this scheduler yet; the standalone CLI is available now. Never run concurrent SQLite-owner processes merely to force cleanup. Configure external monitoring and alerts for database file growth and jobs stuck in active states. Idempotent job replay protection is local for the 30-day retention window; Backend must provide durable/global fencing beyond that window.

## Chunked MP3 smoke pipeline

The local HTTP endpoint now supports `fileName: "audio.mp3"` for both **Kokoro** and **Piper**. It normalizes text, plans ordered sentence chunks, runs an engine for each WAV chunk, validates consistent PCM, joins frames with a short pause, encodes **one** MP3 using `ffmpeg` and validates it via `ffprobe` before S3/RustFS upload (`audio/mpeg`). The output metadata includes `chunkCount` and SHA-256. Both executables must be on PATH; WAV requests remain backwards-compatible.

Implementation: `audio_pipeline.py`, `local_http.py`, test suite `tests/test_audio_pipeline.py`. HTTP/S3 walkthrough: `../docs/tts/LOCAL_HTTP_RUSTFS.md`.

**Explicit limitations:** current chunk generation starts a model subprocess per chunk (no persistent warm runtime); no SQLite per-chunk checkpoint or crash recovery yet; the full Backend claim/heartbeat/finalization lifecycle remains absent. Do not advertise this dev controller as the production Worker.

## Hardening progress — 2026-11-06

The local HTTP worker now uses **thread-local SQLite connections** on one WAL database rather than passing the main-thread `sqlite3.Connection` into HTTP/executor threads. Each connection enables foreign keys and busy timeout; a lock protects job acceptance/state claims but is **not held** while inference or S3 calls run. The result, Inbox READY and durable queue READY commit atomically in one transaction.

The durable pipeline checkpoints SHA-256 validated WAV parts **and the finished MP3**. After an abrupt process exit, valid WAV/MP3 files are reused. RustFS delivery checks existing objects with HEAD before PUT: matching objects are reused, mismatches fail closed and are not overwritten. Actual remote byte validation is not implied by HEAD metadata. Local terminal SQLite rows are retained 30 days and the HTTP runtime invokes the bounded Janitor; terminal orphan WAV directories are cleaned after their retention age.

Resident Kokoro/Piper IPC now has time-limited model startup and rendering, with a bounded restart watchdog. `GET /TTS/ready` requires warmed engines, and `GET /health/info` returns expected vs loaded voices/languages; both require a Bearer token. `GET /health/live` remains public for loopback liveness.

CI runs Python 3.10/3.12 unit tests, **real socket HTTP concurrency**, explicit child hard-exit checkpoint tests, MP3 replay tests and RustFS HEAD-before-PUT fake transport tests. These do not substitute for real Kokoro/Piper inference, real RustFS, or Backend acceptance.

**Remaining release gates:** real warmed model end-to-end inference and watchdog behavior on target OS, real RustFS S3/MP3 upload/readback, approved model-manifest integration, resource/soak acceptance and the Backend claim/lease/heartbeat/fencing/finalization contract. The Backend implementation is not present in this repository. PR #2 must remain Draft until these conditions are met.

The published RustFS object TTL is **Backend-owned**. Worker TTL is only local replay/file metadata; the optional S3 Lifecycle config script is not required by the HTTP render pipeline. Backend deletion with reference safety is required before production qualification.
