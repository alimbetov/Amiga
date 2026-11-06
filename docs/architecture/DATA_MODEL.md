# Data model (logical draft)

## Drift / SQLite — device owned
- `local_profile(id, display_name, address_form, locale, speech_locale, zone_id, preferred_voice, updated_at)`
- `reminder(id, title, note, kind, enabled, created_at, updated_at, deleted_at)`
- `reminder_schedule(id, reminder_id, local_time, zone_id, rrule, starts_on, ends_on, dst_policy, lead_minutes)`
- `reminder_occurrence(id, schedule_id, planned_at_utc, scheduled_notification_id, state, fired_at_utc, completed_at_utc, updated_at)`
- `reminder_action_history(id, occurrence_id, action, occurred_at_utc, snooze_until_utc)`
- `medication_plan(id, user_label, user_dose_text, note, starts_on, ends_on, enabled)`
- `medication_schedule(id, medication_plan_id, local_time, zone_id, rrule)`
- `medication_intake_log(id, medication_plan_id, occurrence_id, action, action_at_utc)`
- `calendar_event(id, title, starts_at_utc, zone_id, ends_at_utc, note)`
- `important_date(id, title, local_date, recurrence_rule, zone_id)`
- `content_cache(id, release_id, locale, payload_sha256, downloaded_at)`
- `audio_cache(asset_id, content_hash, local_path, downloaded_at, expires_at)`
- `preferences(key, value)`
- `schema_migrations` managed by Drift.

Rules: IDs use stable UUIDs; foreign keys enforced; deletion of a reminder cancels pending notifications; actions are append-only records; schedule rule and concrete occurrence are distinct; state never means guaranteed OS display. Store UTC timestamps in a consistent portable representation. Do not write private medication text to analytics.

## PostgreSQL — backend owned
- `content_category`
- `content_template`
- `content_template_translation`
- `content_script` and `content_script_segment`
- `content_release` and `catalog_manifest`
- `tts_engine`, `tts_voice`, `tts_voice_model`
- `tts_job` (request hash, immutable snapshot, lease owner/expiry, attempt, state)
- `file_asset` (sha256, storage key, MIME, duration, provenance)
- `premium_product`, `entitlement`, `store_transaction` (minimal verified store identity)

All migrations via Flyway. Unique constraints: `tts_job.idempotency_key` per caller/operation scope, content immutable version identity, file asset checksum/provenance; store transaction identity unique per platform. No mobile medication/appointment table in default backend schema.

## State machines
Occurrence: PLANNED → (NOTIFICATION_ATTEMPTED) → COMPLETED | SKIPPED | MISSED | CANCELLED; SNOOZED creates/updates the next trigger without losing history. These are internal tracked actions, not delivery receipts.
TTS: PENDING → CLAIMED → PROCESSING → UPLOADING → READY; failures use RETRY_PENDING/FAILED/CANCELLED. All transitions must be validated and versioned.

**Next:** implement real Drift migrations and JPA/Flyway constraints with migration/rollback tests rather than treating this draft as executable DDL.
