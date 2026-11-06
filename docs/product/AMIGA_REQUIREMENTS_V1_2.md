# AMIGA — Consolidated Product & Technical Requirements v1.2
Date: 2026-11-06. Status: agreed architecture; implementation stages not completed.

## Source of truth and scope
Combines original Product Vision & Technical Specification v1.0, architecture/bootstrap documentation, Smart Reminder/Wellness/Medication/International Privacy additions from PR #1, and approved Self-Managed Multi-Engine TTS Worker requirements. Where an older file conflicts, this document and the linked ADRs take precedence until formal review.

## Product
Android/iOS Flutter, optional registration, privacy-first and offline-first. Core benefit: reminders, calendar, medication acknowledgment, birthdays and user-controlled encouragement. Avoid medical diagnoses, dosage recommendations, intrusive psychological messaging or claiming notification delivery is guaranteed.

## Functional domains
1. Local profile: name/pseudonym, UI/content/voice languages independently, IANA timezone, voice preference, optional general occupation and type of work; values can be skipped, reviewed, edited and deleted.
2. Smart Reminder: one-time, recurring weekdays/monthly/annual, prior alerts, pause, complete, skip, snooze, history, reliable SQLite persistence, OS permission/clock/DST/restart reconciliation; intended schedule != receipt.
3. Medication: user-entered name/dose/schedule, same-occurrence followups and explicit taken/skipped history; no automatic adjustments or doubling; local private by default; neutral lock-screen.
4. Work & Wellness: office/home/hybrid/mobile, seated/screen time, workday/shift/lunch/quiet hours; configurable eye/stretch/walk reminders; adaptation **opt-in**, explainable, bounded and only after user confirmation; cannot modify medication.
5. Events, birthdays, habits, optional support and daily locally assembled digest.
6. Local notifications work offline; future remote FCM/APNs only for opt-in online content events (not sole medication delivery).
7. Content Studio, localized templates, voice catalog, deterministic scripts, immutable releases, worker jobs, RustFS audio and Flutter cache.
8. Free core reminders including medication; Premium voice/content features; verified platform transactions and restore/refund/revoke prior to paid release.

## Languages and TTS
- UI locale, content locale, speech locale are distinct. No speech claim without qualified model/voice.
- Kokoro-82M (Apache-2.0 model): primary; en-US initially af_heart / am_michael after acceptance, then qualified additional supported languages.
- Qwen3-TTS (verify exact model/checkpoint license): RU expressive candidate, independent resource-heavy worker.
- Piper (runtime MIT archival / GPL newer, per-voice model/license audit): KK/RU CPU candidate.
- Unsupported text remains displayed without misleading cross-language audio. Voice assets need approval including license SPDX, source, attribution and immutable checksum.
- Language selection is by qualified capabilities, not guessed by filename.

## Architecture
Flutter (Riverpod, Drift/SQLite, GoRouter, local notifications, audio playback) ↔ Spring Boot modular monolith (Java 21, PostgreSQL, Flyway) ↔ independent Python Workers (SQLite each, engine adapter, inbox and content cache, bootstrap model manifest, audio assembly) ↔ RustFS.
Backend owns global jobs, published assets and policy. Worker owns local processing, manifests, model download/cache, retry and recovery. Workers cannot access backend PostgreSQL directly.

## Worker requirements
- Self-managed engine-scoped bootstrap via authenticated init; versioned manifest describing allowed model/voice bundles with sha256, size, license approval, model download descriptor, capabilities and refresh interval.
- Disk cache models on persistent volume; checksum verification before activation; atomic rename; preload critical voices, lazy-load optional; no active-job model eviction.
- SQLite WAL inbox keyed by jobId and immutable request fingerprint, model registry, cache, chunk states, events; persistent crash recovery; retry budget; 30-day completed inbox and cache metadata retention.
- Fingerprint includes canonical normalized text, locale, engine, model + checksum, voice bundle checksum, voice, speed, phonemizer/chunker/encoder/normalizer and content config. Do not include signed URL.
- First validate RustFS cached object by trusted metadata HEAD/checksum (HEAD alone insufficient to establish integrity); confirmed missing => regenerate, transient outage => retry; cache metadata can expire independently of persistent MP3.
- DIRECT short-text or CHUNKED long-text path. Split on language-aware sentence boundaries with engine token limits (never arbitrary mid-word truncation), preserve speaker/voice boundary, synthesize chunk PCM, resample/concatenate/pause/normalize, encode **one MP3**; per-chunk checkpoint/recovery; version chunking policy in hash.
- Backend leases with fencing, immutable job snapshots, idempotent upload/finalization; Worker may not complete stale claim; two workers must not rely on separate SQLite for global exclusion.
- No sensitive text in logs, storage object keys or unsigned external endpoints. Transient local plaintext is bounded/protected, erased after success as appropriate.
- 30-day cleanup is not permission to remove published objects. RustFS retention / reference counting is Backend-controlled.

## Acceptance gates
See docs/tts/WORKER_ACCEPTANCE.md. A real Kokoro → MP3 → RustFS → Backend FINALIZED → Flutter offline playback test on an exact SHA is required before 'production accepted'. First independent slice may use fakes and unit tests but must be labelled foundation only.
