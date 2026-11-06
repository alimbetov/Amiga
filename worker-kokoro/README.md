# Amiga Worker — initial foundation

Python 3.10+ standard library SQLite foundation. **Not yet a running TTS service.**

This initial implementation provides transactional local Inbox duplicate detection, state transitions, cache metadata and 30-day retention, persistent model registry schema, and deterministic conservative chunk planning/fingerprint. It also validates operator-pinned upstream seed catalogs, permits bootstrap only after explicit Backend NO_MANIFEST, and constructs deterministic LOCAL_STAGED/ASSETS_UPLOADED candidate descriptors. W2B now includes a checkpointed fallback coordinator and HTTPS transport for upstream download, scoped RustFS PUT grants, authoritative integrity checking, candidate proposals and approved-manifest reconciliation. Its unit/integration-style tests use fake network services. **A real Backend implementation, actual RustFS integration, model activation/inference, PCM assembly and distributed fencing are still missing.**

Run locally from this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

W2B HTTP API contract: [bootstrap contract](../docs/tts/W2B_BOOTSTRAP_HTTP_CONTRACT.md). The seed example is intentionally empty; only an operator-reviewed, checksum-pinned seed can bootstrap. W2B requires explicit Backend NO_MANIFEST, not network errors. Returning MANIFEST_APPROVED does not mean model runtime READY.\n\nImplementation plan: [worker tasks](../docs/tts/WORKER_TASKS.md) and [acceptance matrix](../docs/tts/WORKER_ACCEPTANCE.md).

Reminder: direct SQLite connection access assumes one process controls the local volume; additional concurrency management and global Backend lease/fencing are prerequisites for production.
