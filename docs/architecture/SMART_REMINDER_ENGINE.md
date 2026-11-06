# Smart Reminder Engine — architecture v1.1
Status: design only, no deployed functionality.

## Separation of concerns
Flutter modules General / Medication / Wellness / Events -> type-specific policy -> recurrence materializer -> Drift transactional schedule + occurrence + notification outbox -> platform local notifications. A reconciliation loop updates OS schedules after process restart, settings/timezone changes and eligible platform callbacks. OS and SQLite cannot share a transaction, so changes are idempotent and replay-safe. No network dependency.

A scheduled notification does not prove delivery, reading or completion. Use a bounded rolling scheduling window; test Android exact-alarm/Doze and iOS Focus/background/pending-notification limits on physical devices.

## Strict business boundaries
- GENERAL: user-configured repeating/one-time reminders.
- WELLNESS: workday break suggestions, optional opt-in adaptive intervals; never infer that user was actively using a PC.
- MEDICATION: prescribed times, frequency and user-entered dose text can be changed ONLY by user, never automatically; any snooze or follow-up pertains to the original dose and is NOT a second dose.
- EVENTS: local-calendar recurrences and pre-event offsets.

Each schedule stores local civil time, IANA zone, recurrence, revision, validity range and DST resolution policy; each occurrence has deterministic unique key, UTC due instant and state. Actions are append-only with idempotency keys. Timezone and DST gap/overlap behavior explicitly tested; preserve history on edits. Unanswered != confirmed missed.

## Privacy
All personal schedules, occupation, work sessions and medication remain local in Drift by default. Data minimized and deletable; avoid sensitive logs, analytics and cloud TTS payloads. Explicitly address backup/encryption and lock-screen privacy separately.

## Adaptive engine
Only WELLNESS: allow feedback to create an explainable, reversible proposed change within user-set min/max and quiet hours. Off by default; acceptance required for a proposal. Never change MEDICATION rules. Limit notification density and deduplicate simultaneous alerts.

## Notifications
MVP: local scheduled notifications offline, action buttons where OS supports; in-app fallback. Future remote FCM/APNs for opt-in online content alerts only, not required for medication or other local reminders. No sensitive data in push payloads.

## Acceptance
Deterministic recurrence and DST unit tests; SQLite migration/outbox crash recovery; double-tap and duplicate event replay; offline reopen; exact-SHA CI; real Android/iOS permission/lock-screen/background/reboot/timezone tests. No claim of guaranteed medically critical delivery.
