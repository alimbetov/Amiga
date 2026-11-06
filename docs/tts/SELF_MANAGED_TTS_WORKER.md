# ADR-TTS-010 — Self-Managed Multi-Engine Worker v1.0

Status: accepted design, implementation staged.

## Responsibilities
Backend: qualified catalogs and manifest/version, job creation/routing, atomic claim/lease and fencing, global dedup/outbox, asset authorization and finalization; owns RustFS lifecycle/published references.
Worker: init and refresh manifest, secure download and sha256 validation of model + voice assets, SQLite WAL Inbox/cache, admission control and retries, inference adapter, chunking/assembly/MP3 upload, heartbeat and fenced completion, cleanup and local health.

Independent process + SQLite volume for Kokoro, Qwen3, Piper. Workers may share a common core library but never a writable SQLite file over NFS. File cache atomic activation via temp file + fsync + rename. Model URLs are ephemeral and must be refetched after expiry; validate scheme, hosts, size and hash to avoid SSRF and unbounded downloads. Never copy secrets/URLs into permanent logs.

## Init contract
POST /internal/v1/workers/init, authenticated mTLS or scoped bearer token.
Request: {workerId, engineCode, engineVersion, supportedManifestSchemaVersions:[1], runtimes, capabilities}
Response: {schemaVersion:1, manifestId, manifestVersion, manifestSha256, refreshAfterSeconds, models:[{modelId,modelRevision,engineCode,enabled,commercialApproved,licenseSpdx,model:{assetId,downloadUrl,sha256,sizeBytes},voices:[{voiceCode,locale,assetId,downloadUrl,sha256,sizeBytes}],preload,limits:{maxInputTokens,targetChunkTokens,maxAudioSeconds}}]}.
Manifest digest must cover canonical stable fields (not expiring URLs); integrity must be verified against trusted authenticated Backend and local checksums. Do NOT trust Worker self-reported capabilities as model authorization. Model manifest reconciliation never changes immutable in-flight snapshots.

## Job contract
POST /internal/v1/tts/jobs/claim -> jobId, claimToken, leaseEpoch, leaseExpiresAt, request fingerprint, text/script references, locale, engine/model/voice artifact identities, speed, codec settings, target object descriptor and algorithm revisions.
Worker checks manifest membership, checksum and license allowlist. If not ready: retry with structured reason; no fallback to an unqualified voice.
Heartbeat includes claimToken + leaseEpoch; completion includes all immutable identity fields, checksum, audio duration, storage key, and claim token. Backend rejects stale epoch, non-owning worker, repeat completion with mismatch.

## Cache rules
Job idempotency key protects same operation; fingerprint reuses same output across different jobs where privacy policy permits. If cached RustFS object passes validation, complete without inference. If 404/410 (with valid authorization and authoritative storage check), mark stale and regenerate. If 403/5xx/network error, retain and retry: NEVER treat inability to validate as absence.
In-progress same fingerprint must be single-flight per worker via transactional unique lock; use Backend-level coordination across worker instances.
Store key derived from opaque fingerprint namespace plus format, not raw text. Server-side finalization must ensure visibility and integrity before READY.

## Database
- inbox_job: jobId PK, immutable fingerprint, source snapshot identity, state, attempts, claimEpoch, lease deadline, errors, created/updated/expires.
- model_registry/model_artifact: pinned hashes, paths and VERIFIED/ACTIVE status, referenced by in-flight jobs.
- tts_cache: fingerprint PK, object key, checksum, size, duration, verifiedAt, lastUsedAt, expiry.
- tts_chunk: jobId+index unique, chunk hash, text encrypted/ephemeral if persisted, PCM path and hash, state/attempts.
- worker_event: redacted transitions.
SQLite durability: WAL, foreign_keys ON, busy_timeout, BEGIN IMMEDIATE for claims/cache lock. Atomic DB transitions; external RustFS operations handled by retryable outbox rather than assuming distributed transaction.

## Chunk policy
Short messages DIRECT, longer ones CHUNKED. Token limits are engine/version specific and validated *after* phonemization. Protect abbreviations, decimals, multilingual punctuation and speakers. Long sentence fallback splits at clause then word boundaries without empty chunks. Synthesis PCM/WAV → uniform sample rate/channels → ordered joins with controlled silence → loudness normalization → single MP3. Record deterministic chunk plan hash. Pipeline versions included in fingerprint. Fail closed if text cannot fit.

## Retention/recovery
Completed inbox and cache metadata: 30 days; queued/leased jobs stay until terminal or controlled dead-letter; cap retries. RustFS published MP3 independent. On reboot reconcile expired local jobs against Backend leases; never replay upload/complete using stale epoch. Model eviction only for unpinned, inactive artifacts and after capacity checks. If Backend unavailable, known verified assets remain on disk but no unauthorized new jobs accepted. Health != readiness: readiness advertises qualified capabilities only.
