# Amiga Worker — initial foundation

Python 3.10+ standard library SQLite foundation. **Not yet a running TTS service.**

This initial implementation provides transactional local Inbox duplicate detection, state transitions, cache metadata and 30-day retention, persistent model registry schema, and deterministic conservative chunk planning/fingerprint. It also validates operator-pinned upstream seed catalogs, permits bootstrap only after explicit Backend NO_MANIFEST, and constructs deterministic LOCAL_STAGED/ASSETS_UPLOADED candidate descriptors. It does NOT yet download or upload models, perform Backend proposal/approval, render speech, contact Backend/RustFS, perform PCM assembly or distributed fencing.

Run locally from this directory:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

Implementation plan: [worker tasks](../docs/tts/WORKER_TASKS.md) and [acceptance matrix](../docs/tts/WORKER_ACCEPTANCE.md).

Reminder: direct SQLite connection access assumes one process controls the local volume; additional concurrency management and global Backend lease/fencing are prerequisites for production.
