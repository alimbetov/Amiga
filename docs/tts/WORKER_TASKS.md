# Development tasks for implementation agents

1. **W0 contracts (blocker):** finalize DTO schemas for init/manifest/asset URL renewal, immutable claim snapshot, lease token/epoch, engine identity, replay-safe complete/fail. API contract tests.
2. **W1 SQLite:** transactional Inbox, cache, model/artifact registry, indexes and retention; duplicate/collision/crash tests.
3. **W2 Bootstrap:** authenticated Backend init, typed manifest validation, trusted downloads, sha256/size validation, atomically stored models; preload/lazy load and refresh.
4. **W3 Inference adapter:** EngineRuntime protocol with Kokoro ONNX first; Qwen3 and Piper adapters independently, license gates.
5. **W4 Text processing:** engine-aware normalization/splitting/token count, per-chunk WAL checkpoint and cached PCM, deterministic assembly and one MP3.
6. **W5 RustFS:** object HEAD vs verified digest, 404 regeneration, transient retry, signed upload, provenance, idempotent Backend finalization and fencing.
7. **W6 Hardening:** metrics without text, readiness per model, cleanup 30 days, disk quotas, safe shutdown and failure injection.
8. **W7 Qualification:** exact-SHA CI and real E2E; do not claim production accepted before passing.

Each stage must include unit + integration + adversarial tests and document its evidence. One backend global job registry remains source of truth; no Kafka/RabbitMQ required for MVP.
