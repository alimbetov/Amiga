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
