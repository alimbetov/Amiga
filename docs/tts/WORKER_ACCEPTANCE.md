# Worker acceptance matrix and phased delivery

## T0 — Contracts and configuration
[ ] Canonical schema for init/manifest, claim/fencing, completion and asset verification; authenticated endpoints and errors.
[ ] License/voice catalog and language mapping qualified; fail closed on unsupported combinations.

## T1 — SQLite foundation (current implementation goal)
[ ] WAL/foreign_keys/busy_timeout, schema initialization.
[ ] Duplicate job same fingerprint idempotent; same job different fingerprint conflict.
[ ] Cache CRUD, expiry, hit and missing invalidation.
[ ] 30-day cleanup with nonterminal preservation.
[ ] Crash restart persists Inbox and models; transaction-safe job state.
[ ] Local no-network deterministic unit tests.

## T2 — Model manager
[ ] Init from Backend with typed validation, auth and trusted endpoint restrictions.
[ ] Download assets from RustFS using short-lived scoped URLs.
[ ] SHA-256/size checks and atomic cache activation; corrupted download refusal.
[ ] Concurrent download single-flight and restart reconciliation.
[ ] Preload/lazy load and safe old-model retirement.
[ ] Real ONNX voice bundle inference.

## T3 — Rendering pipeline
[ ] English/Russian/Kazakh sentence segmentation verified with abbreviations, dates/decimals.
[ ] Model-specific limits; DIRECT and CHUNKED.
[ ] Per-chunk checkpoint, ordered PCM assembly and single encoded MP3.
[ ] Corrupt/missing chunk re-render; crash recovery and audio continuity acceptance.

## T4 — RustFS & Backend
[ ] Cached object valid -> no inference; definitive missing -> regeneration; 403/timeout -> retry not regenerate.
[ ] Signed scoped upload, file checksum and MP3 decode validation, idempotent finalization.
[ ] Heartbeat/lease expiration, stale completion rejected, two workers contesting same fingerprint.
[ ] Job updated READY only after backend confirmed finalization.

## T5 — Real qualification
[ ] Real Kokoro ONNX / chosen voices -> MP3 -> RustFS -> Backend -> Flutter offline.
[ ] Qwen3/Piper engine separately validated with licenses and native-speaker audio reviews.
[ ] Exact-SHA CI, security, failure injection, restart/retry and profiling.

Current status: requirements + initial foundation only; no real inference, storage or end-to-end qualification claimed.
