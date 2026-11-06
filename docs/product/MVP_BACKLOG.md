# MVP epics and user stories

Priority: P0 blocks offline MVP; P1 next voice-content release; P2 commercial/future.

## E0 — Foundation (P0)
- US-001: As a user, I can launch Android/iOS without an account. **Accept:** cold launch, app restart, no network.
- US-002: As a user, I can change locale/time zone/notification preferences. **Accept:** values persist in Drift after upgrade.
- US-003: As a developer, I get deterministic CI, lint, unit/widget and migration checks. **Accept:** exact-SHA green on PR.

## E1 — Reminder engine (P0)
- US-010: Create/edit/delete a one-time reminder. **Accept:** Drift persisted; OS schedules/cancels expected notification.
- US-011: Recurring reminder (daily/weekly/monthly/annual). **Accept:** materialized occurrences stay unique across repeated reconciliation.
- US-012: Snooze, complete and skip from app/action. **Accept:** actions durable; no duplicated next occurrence.
- US-013: Permission revoked, device reboot, zone/DST change, manual clock change. **Accept:** deterministic recovery and visible unavailable-permission state.
- US-014: Reminders work offline after process restart. **Accept:** device E2E and no backend.

## E2 — Medication, events and important dates (P0)
- US-020: Set a medication reminder with user-entered dosing label. **Accept:** log taken/skipped; no medical advice or dose calculation.
- US-021: Hide sensitive lock-screen text. **Accept:** tested on supported platforms.
- US-022: Create an event with multiple reminders and annual birthday. **Accept:** correct reschedule and cancellation.

## E3 — Content and TTS (P1)
- US-030: Read/pin published immutable content catalog offline.
- US-031: Validate and publish localized templates with safe placeholders.
- US-032: Run real Kokoro voice through claim/heartbeat/finalize into RustFS, with hash verification.
- US-033: Play cached MP3 offline with single/dual voice segments.

## E4 — Companion (P1)
- US-040: Configure morning/afternoon/evening voice messages or disable completely.
- US-041: Receive local-only daily digest from locally available schedule data.

## E5 — Monetization (P2)
- US-050: Premium purchase verified server-side and restored through Google/Apple.
- US-051: Entitlements reflect refunds, revocations and expiration; core medication reminders remain Free.

## Definition of Done
Acceptance tests for happy paths + boundary cases, migrated state, offline reopen, exact SHA CI, privacy/security checks, real-device Android and iOS where OS notifications are involved. A mock notification test is not sufficient proof of device delivery.
