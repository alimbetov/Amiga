# Amiga Self-Managed TTS Worker — ТЗ на стабилизацию и завершение интеграции

**Статус:** PROPOSED / implementation not started.  
**Объект:** PR #2 (`feat/tts-self-managed-worker-v1`), аудит HEAD `59a0210791e82725aecd6f41bba1cc7cccd1e3f1`, 2026-11-06.  
**Назначение:** довести Worker от локального PoC до надёжного сервиса обработки текста с проверяемым MP3 и доставкой в RustFS.  
**Область изменений:** исключительно существующая ветка и PR #2; не создавать feature-веток или новые PR.  

## 1. Принятое архитектурное решение

**Минимальный надёжный вариант:** один Python Worker-процесс, один SQLite-файл с WAL, один последовательный job executor, два постоянных TTS subprocess (Kokoro и Piper), FFmpeg, S3 API RustFS. HTTP `ThreadingHTTPServer` остаётся временным транспортом. **Не вводить** Celery, Redis, RabbitMQ, PostgreSQL, отдельный scheduler/service или несколько конкурирующих job executors до появления доказанной потребности.

```text
Client / future Backend
  POST /v1/tts/jobs -> 202 Accepted
  GET  /v1/tts/jobs/{jobId}
             |
             v
HTTP validation, job fingerprint, SQLite transaction (PENDING)
             |
             v
One local executor (persistent queue; retry/backoff; no model reload per chunk)
             |
             +--> resident Kokoro / Piper (health, load, watchdog)
             |         |
             |         v
             |      WAV chunk (fsync + SHA-256 + durable DONE)
             |
             v
Ordered PCM assembly -> one MP3 -> ffprobe + SHA-256
             |
             v
RustFS S3 PUT -> HEAD (size + metadata digest) -> SQLite VERIFIED
             |
             v
Backend finalization (when Backend exists) -> local READY
             |
             v
Delete local PCM/WAV *only after durable completion*
```

**SQLite threading strategy (choose exactly one):** for a minimally invasive repair, use **thread-local SQLite connections** to the same WAL database, each initialized with `foreign_keys=ON`, `busy_timeout`, and short `BEGIN IMMEDIATE` transaction boundaries. Give the one executor exclusive job-claim ownership; HTTP handler opens its own connection. **Never share a `sqlite3.Connection` between threads.** Use `UNIQUE(job_id)` + atomic compare-and-swap UPDATE to prevent races. No lock may span inference/FFmpeg/S3 calls. A DB-owner thread with command queue is an acceptable alternative **only** if benchmark/proof shows thread-local connections unsuitable; do not implement both.

**Scope boundary:** Until Backend exists, `READY` means local upload has been confirmed through RustFS HEAD (or local file check). Once Backend integration is enabled, `READY` must require a verified Backend finalization response. Never claim production Backend acceptance based on a local-only result.

## 2. Mandatory invariants

1. **Before inference:** immutable job payload, fingerprint, destination profile ID, engine/voice/model identity and state committed in SQLite. Secrets from inline S3 credentials must never be persisted.
2. **At most one local active attempt per job:** atomic state transition `PENDING/RETRY_PENDING -> PROCESSING` with attempt number; no concurrent processing of same `jobId`.
3. **Deterministic idempotency:** same `jobId` + same immutable snapshot yields same final result while the retention tombstone exists; same ID + changed snapshot returns HTTP 409.
4. **No false readiness:** model READY only after real model initialization, successful warmup and healthy live subprocess; absent/dead/blocked model returns NOT_READY.
5. **Durable chunks:** `chunk_index`, canonical chunk fingerprint, model/voice/pipeline version identity, SHA-256, PCM path, state and timestamp are stored. A resumed run reuses `DONE` only after WAV structure + file hash verification.
6. **One final MP3:** concatenate decoded PCM in order (optional bounded pauses), never concatenate independently encoded MP3 bytes; verify output codec, duration, size and SHA-256 before delivery.
7. **No premature WAV deletion:** retain chunks through inference failure, kill, MP3 encoder failure, upload failure and pending Backend finalization. Delete after committed `READY` (and Backend confirmation when enabled).
8. **Bounded resources:** text length, chunk count, queue depth, duration, disk bytes, process memory, retries and timeouts have configurable safe limits.
9. **Retention:** SQLite terminal Inbox/results/cache retained 30 days from terminal transition; active jobs not purged by age. RustFS object deletion/asset TTL belongs to Backend; Worker only cleans its local temporary content.
10. **Fail-closed errors:** duplicate/corrupt/conflicting state never silently becomes READY; do not expose request text, credentials or absolute local file paths in health or error logs.
11. **Single deployment instance for local Worker DB:** multi-host leasing/fencing is Backend's responsibility. Starting multiple independent workers against the same SQLite file is not supported.

## 3. Required development slices

### H1 — SQLite thread safety and single durable Inbox — P0

**Defect:** `WorkerStore` creates a default `check_same_thread=True` connection on the main thread, but HTTP requests and the executor operate on other threads. `Renderer` currently also holds a lock through inference.

**Changes:**
- Move DB access behind a thin `WorkerStore` repository API; open per-thread connections, WAL, FK and timeout PRAGMAs; close on thread shutdown.
- Consolidate `inbox_job`, `durable_jobs` and `jobs` into **one authoritative job state** (migration preserving old records where possible). Avoid permanent parallel sources of job truth. Persist nonsecret request snapshot and result in same DB.
- Move expensive inference/FFmpeg/S3 calls outside DB transactions and locks. Define claim, complete, fail, reschedule as atomic DB operations.
- Explicit schema version + migration tests for existing `local-http-output/jobs.sqlite`; no destructive reset on startup.
- Reconcile existing states on startup; do not modify completed READY jobs.

**Acceptance:** actual HTTP server with 10 concurrent submits/status queries; no SQLite `ProgrammingError`, no duplicate `jobId`, no database locked under normal load, no lost accepted job on forced restart; migration preserves completed/queued jobs.

### H2 — Durable job lifecycle, crash recovery and bounded retries — P0

**States:** `PENDING -> PROCESSING -> ENCODED -> UPLOADED -> VERIFIED -> (BACKEND_CONFIRMED) -> READY`. Errors use `RETRY_PENDING`, terminal `FAILED`, optional `CANCELLED`. Intermediate states are persisted with artifact identity and attempt count. On restart reconcile from the last **verified** checkpoint, not by blindly setting READY.

**Changes:**
- Single job executor loop; queue depth limit, 202 enqueue, status API and explicit, immutable retry policy.
- Per-stage checkpoints and reconciliation, including the case where S3 PUT succeeds but process dies before local commit: HEAD existing deterministic object key and reuse only if size/hash match.
- Maximum attempts and exponential backoff with jitter; distinguish retryable timeout/storage errors and terminal bad input/unknown voice.
- Stop gracefully: stop accepting new work, finish or checkpoint current stage, release resources. `kill -9`/TerminateProcess recovery tested separately.
- Remove invalid state transitions (e.g. trying `RETRY_PENDING -> FAILED` without valid state) and ensure atomic result + READY persistence.
- Keep 30-day tombstones; Backend must maintain global idempotency beyond this window.

**Acceptance:** failure injection **before/after every commit**, kill during chunk N, after MP3, after PUT and after HEAD; restart completes with no lost requests or corrupt MP3. Duplicate delivery is benign and idempotent.

### H3 — Resident EngineSupervisor hardening — P0

- Configure required engine+voice entries at startup (`kokoro:af_heart`, `piper:kk_KZ-iseke-x_low` initially); only configured entries are advertised in `/health/info`. No Qwen3 in active runtime.
- `LOADING/READY/FAILED/RESTARTING` per runtime. Cold initialization and warmup are required before `READY`.
- Add startup deadline, per-chunk synthesis deadline and watchdog. Terminate hung child; retry with bounded exponential backoff and limited restart count.
- Avoid blocking forever on pipe `readline()`: implement a timeout-capable message channel or reader thread/queue; protocol messages have request IDs, bounded size and error codes.
- Keep stderr diagnostic logs with redaction and rotation instead of discarding diagnostics; no stdout contamination of protocol.
- Handle unsupported voices/languages as validation errors, not backend timeouts.
- `GET /TTS/ready`: HTTP 200 iff all configured required models are warmed and alive; otherwise 503. `GET /health/info`: expected/loaded model and language variants from live supervisor inventory. `GET /health/live`: process liveness, no model readiness claim.

**Acceptance:** kill one child -> readiness immediately 503; auto-restart -> 200 only after new warmup; child hangs on render -> timeout + controlled recovery; wrong voice fails clearly. A model file on disk without an active process never counts as loaded.

### H4 — Durable chunk -> final MP3 -> RustFS — P0

- Chunk fingerprints must include canonical text, locale, model digest/revision, voice identity, chunker/encoder settings; not only the raw substring.
- Chunk WAV artifacts are written using temp file + atomic rename and verified SHA; validate complete sample format and size. Use fsync when durability is claimed.
- Reuse successful chunks after kill; corrupt WAV is re-rendered. Prevent deleting checkpoint directories on retry or before finalization.
- PCM assembly respects chunk order, consistent format and audio duration; ffmpeg creates a **single** final MP3; ffprobe checks codec/duration and SHA.
- Deterministic S3 object key from job ID/content hash; S3 HEAD checks content length and SHA metadata; reject mismatches.
- Cleanup temporary WAV/PCM only after committed final READY (or verified Backend confirmation when integrated). Add separate orphan cleanup for jobs already terminal after 30-day retention.

**Acceptance:** real Piper and Kokoro chunked MP3 generation; decode final MP3 to audio to validate; RustFS PUT+HEAD in local integration; abort during upload and re-run without regenerating verified chunks; no WAV left after completed retention cleanup. 

### H5 — Backend lifecycle adapter — P0 for production (not for isolated local MVP)

**Backend-owned contracts:** initialize model manifest, claim job with lease/fencing token, heartbeat and lease renewal, fail/retry, file finalization and idempotent completed result. RustFS FileAsset lifecycle and remote DELETE belong to Backend.

- Produce contract DTO + API tests first, then a small adapter with structured responses and strict timeouts.
- Snapshot approved manifest model SHA/voice SHA/pipeline version/lease token for each job.
- No READY until Backend explicitly acknowledges current lease/fencing generation and immutable MP3 artifact identity; reject stale finalization.
- Backend temporarily unavailable -> persist `VERIFIED`, retry finalization without re-synthesis/upload.
- If Backend code is not in this repository, mark this stage **BLOCKED_EXTERNAL_BACKEND**, do not simulate it as completed.

**Acceptance:** real PostgreSQL/Backend -> HTTP Worker -> MP3 -> RustFS -> Backend READY; stale lease rejected; duplicate finalize returns original result; Backend down/up test passes.

### H6 — Operational hardening and observability — P1

- Attach SQLite janitor **to the running worker**, at most one owner; 30-day terminal cleanup in small batches; WAL checkpoint best effort; never delete active jobs or model registry.
- Bounded orphan file cleanup, disk quota low-watermark, startup diagnostics and graceful shutdown.
- Metrics: queue depth, jobs by state, inference time per engine/voice, chunk retries, upload retries, model load time, readiness, MP3 duration, cache hit/miss; avoid labeling with job ID or text.
- Structured logs and correlation ID, no secrets in logs. Liveness/readiness/read-only information endpoints have documented auth policy.
- Remove Qwen3 from the active engine picker and dependency expectations; retain experimental notes only as archival documentation.
- Do not auto-provision RustFS lifecycle rules in Worker when Backend controls remote expiry.

**Acceptance:** restart and cleanup leave SQLite consistent; 30-day data removed, active work preserved; 24-hour soak (dev acceptance, with reduced test intervals where appropriate), no unbounded queue/disk growth.

## 4. External API contract

| Method | Path | Purpose | Expected |
| --- | --- | --- | --- |
| POST | `/v1/tts/jobs` | Durable enqueue with job ID, engine, locale, voice, text, `fileName: *.mp3`, destination profile ID | 202 PENDING, same job may return existing status |
| GET | `/v1/tts/jobs/{jobId}` | Current stage and final result (redacted) | 200/404 |
| GET | `/health/live` | Process liveness | 200 |
| GET | `/health/ready` | Overall dependencies and runtime readiness, semantics documented | 200/503 |
| GET | `/TTS/ready` | All *required* resident models loaded and warmed | 200/503 |
| GET | `/health/info` | DTO expected/loaded model identities, locale variations, capabilities | 200 |
| GET | `/metrics` | Bounded metrics, no text/secrets | 200 |

Example durable request:

```json
{
  "jobId": "amiga-1001",
  "engine": "piper",
  "locale": "kk-KZ",
  "voice": "kk_KZ-iseke-x_low",
  "text": "Сәлем! Демалатын уақыт келді.",
  "fileName": "amiga-1001.mp3",
  "destination": {"type": "profile", "profileId": "rustfs-main"}
}
```

Server config keeps S3 endpoint, bucket, access key and secret key for `rustfs-main`. **Do not persist the inline password in SQLite**. A legacy `POST /v1/tts/render` may remain temporarily for manual smoke, but production jobs always use the durable path.

Suggested status DTO:

```json
{
  "jobId": "amiga-1001",
  "status": "VERIFIED",
  "attempt": 1,
  "engine": "piper",
  "locale": "kk-KZ",
  "voice": "kk_KZ-iseke-x_low",
  "artifact": {
    "bucket": "amiga-audio",
    "objectKey": "tts/amiga-1001.mp3",
    "contentType": "audio/mpeg",
    "sha256": "<sha256>",
    "sizeBytes": 12345
  }
}
```

`READY` is returned only after applicable delivery/finalization gates pass. All states, error codes, retryability and schema versions must be documented. `/health/info` represents **initialized configuration**, not every language supported by the underlying model family.

## 5. Tests and CI gates

Maintain quick `unit (3.10)` + `unit (3.12)`, and add explicit jobs:

| Job | Evidence required |
| --- | --- |
| `sqlite-http-integration` | Real socket HTTP under concurrent requests, migration, transactional atomicity |
| `durable-restart-acceptance` | Hard kill/power loss simulation at all important checkpoints; preserve job and chunks |
| `engine-resident-acceptance` | Real Kokoro/Piper model warmup, process kill, reload and watchdog (model-cached runner) |
| `mp3-rustfs-acceptance` | Real FFmpeg + RustFS S3 PUT/HEAD, corrupt/collision/timeout |
| `backend-e2e-acceptance` | Real Backend lease/fencing/finalization (BLOCKED until backend exists) |
| `security-resources` | Invalid paths, arbitrary endpoint rejection, credential redaction, queue/disk bounds |

**Exact-SHA discipline:** each qualification result must correspond to the latest PR HEAD SHA. A green unit run on an older commit is insufficient. Mock-only tests cannot establish real RustFS/model acceptance.

## 6. Implementation order and gate definitions

| Slice | Objective | Exit gate |
| --- | --- | --- |
| H1 | SQLite repair and consolidated durable job state | Real HTTP concurrency + migration GREEN |
| H2 | Executor, atomic result, crash recovery | Hard-kill matrix GREEN |
| H3 | Resident processes, watchdog, readiness | Real model load/restart GREEN |
| H4 | Chunk/MP3/RustFS reliable delivery | Real S3/MP3 E2E GREEN |
| H5 | Backend lifecycle | Real backend/fencing E2E GREEN (production only) |
| H6 | Cleanup, metrics, docs, quality | Soak + resource/cleanup checks GREEN |

**Gate A — Local Worker Accepted:** H1–H4 and H6, exact-SHA CI and real local end-to-end passing. Enables controlled single-instance internal use, not backend-integrated production.

**Gate B — Production Worker Accepted:** Gate A + H5, integration with real Backend and RustFS, security review, operational documentation, rollback, no P0 defects. Only then take PR #2 out of Draft and consider merge for production.

## 7. Explicit non-goals

- No third TTS engine (Qwen3), automatic language expansion or voice marketplace.
- No new Python web framework or distributed queue for this slice.
- No auto-delete of published RustFS objects from Worker; Backend owns retention.
- No multi-instance Worker with a shared SQLite file or distributed leasing.
- No claims of EXACTLY ONCE remote side effects; use at-least-once attempts with idempotent storage/finalization.
- No rewrite of working PCM assembly or model bootstrap purely for stylistic reasons.

## 8. Primary risks and decisions

- **SQLite:** real thread-affinity defect is blocking; repair H1 first.
- **Process hang:** pipe `readline()` must be bounded; H3 required before real unattended service.
- **Remote commit ambiguity:** S3 HEAD/reconciliation after uncertain PUT; H2/H4.
- **Backend availability:** H5 explicitly depends on a real Backend implementation; keep it separate from Gate A.
- **Audio quality:** consistent sample rates and engine-appropriate phonemization; verify on actual multilingual sample corpus.
- **Licenses:** before commercial usage, track license terms per model/voice and FFmpeg distribution.

## 9. Definition of Done for each slice

- Code and documentation committed to `feat/tts-self-managed-worker-v1`.
- New acceptance tests reproduce prior defect and demonstrate fix.
- Regression tests (Python 3.10/3.12) green on exact SHA.
- User-visible API and DTO examples match implementation, including failure status.
- No new P0, no silent data loss, retry safety and credentials unchanged.
- PR #2 description updated with evidence and explicit limitations.

**Approval required:** acceptance of this ТЗ as the execution baseline. Creating this file does **not** mean the remediation items have been implemented.

## 10. Исполнение H1–H6 (срез стабилизации 2026-11-06)

| Срез | Подтверждённое изменение в коде | Что ещё не принято |
| --- | --- | --- |
| H1 | Thread-local SQLite WAL соединения, реальный многопоточный HTTP и atomic READY между `jobs`/`inbox_job`/`durable_jobs` | Полная миграция 3 таблиц в одну схему остаётся техническим долгом; проверка долгой нагрузки нужна |
| H2 | Persistent enqueue, bounded retries, hard-exit WAV recovery, checksum-based final MP3 reuse и RustFS HEAD-before-PUT | Полная fault-injection matrix на реальном RustFS и после каждого checkpoint |
| H3 | Resident startup/render IPC timeouts, bounded process restart, fail-closed `/TTS/ready` | Real Kokoro/Piper warmup, restart/hang на целевой платформе |
| H4 | PCM assembly, ffmpeg MP3, SHA-256, safe S3 HEAD/PUT reconciliation | Настоящий RustFS и проверка MP3 playback/readback |
| H5 | Не реализован (нет Backend implementation/contract) | Все реальные claim/lease/heartbeat/finalization acceptance |
| H6 | 30-дневный SQLite Janitor подключён, terminal WAV orphan cleanup, auth на диагностических endpoint | Disk quota, длительный soak, production observability |

**Квалификация:** успешный Python CI доказывает устойчивость mock-интеграции, но не Gate A и не Gate B, пока настоящие модели и RustFS не проверены. Никакого автоматического закрытия Draft или merge на основании одного CI.
