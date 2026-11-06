# Roadmap & acceptance gates

## A — Flutter foundation
Flutter SDK pinned, package boundaries, Riverpod, GoRouter, Drift v1, migrations, local profile, locale/theme, Android/iOS scaffolding and GitHub Actions. Gate: clean builds and verified migration/reopen test.

## B — Reminder Core (first implementation slice)
One-time/repeating reminders; recurrence materializer; notification scheduler adapter; snooze/complete/skip; event/medication extensions. Gate: on-device end-to-end create → persist → schedule → notification (OS permitting) → action → reopen offline; no backend dependency. Verify permission denied, cancellation, app restart, reboot, time zone/DST and clock shifts.

## C — Content service and TTS
Modular Spring Boot app; Flyway PostgreSQL schema; content/template management; immutable releases; Kokoro Python ONNX worker, lease/fencing and idempotent completion; RustFS. Gate: published content → job → real model inference → MP3 → backend verified asset → Flutter cached offline playback.

## D — Voice Companion
Daily scheduled messages, single/dual voice, digest, pacing, opt-out and deduplication. Gate: personalization consent, replay/caching tests, volume normalization, no dependency on long background audio.

## E — Commercial release
StoreKit/Google Play validation, restore, entitlement/revoke reconciliation, app policies, accessibility, privacy/data deletion, licensed voices and brand check. Gate: real sandbox purchase/restore/refund tests, security/CI qualification, Android and iOS real-device sign-off.

## Explicitly deferred
Horoscopes, equalizer, background music, speech recognition, cloud account sync, social features and AI chat.

## Current status
Only documentation bootstrap. No milestone is complete until the corresponding source, exact SHA checks and acceptance artifacts exist.
