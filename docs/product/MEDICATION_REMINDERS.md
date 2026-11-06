# Medication reminders — specification v1.1
Status: planned, not a certified medical device or emergency alert.

Plan: user-supplied medication label, user-entered dose text, optional instructions, one/multiple dose times, weekdays, start/end date, chosen timezone, privacy mode, and bounded follow-up notification interval/count.

Each dose occurrence is persisted. Actions: TAKEN (explicit acknowledgement timestamp), SKIPPED, SNOOZED, UNCONFIRMED. A snoozed notification points to the SAME original dose; no extra dose created. Do not calculate dosage, change frequencies/times automatically, advise doubling doses, or interpret no action as proof of non-adherence. Keep action history on edits and restarts.

Sensitive fields local only; generic lock-screen message by default; never in analytics, crash logging or remote push. Basic medication reminders are Free. Show notification permission status clearly and do not promise guaranteed life-critical delivery.

Acceptance: multiple daily times, crossing midnight, overlapping plans, zone/DST changes, stale action, duplicate taps, bounded followups, denied permissions, offline reboot/reopen, physical Android/iOS checks.
