# Proposed local Drift data model — v1.1
Conceptual tables; actual Dart migrations still required.

- work_profile(id, occupation_group NULL, work_setting, work_character, screen_hours_bucket, zone_id, adaptive_opt_in)
- work_shift(id, profile_id, weekday, start_local, end_local, lunch_start NULL, lunch_end NULL)
- work_session(id, profile_id, state, start_utc, end_utc NULL)
- wellness_policy(id, type, interval_minutes, min_minutes, max_minutes, daily_cap, enabled, user_locked)
- wellness_feedback(id, occurrence_id, action, action_at_utc)
- adaptation_proposal(id, policy_id, old_minutes, suggested_minutes, reason, status)
- reminder_definition(id, kind, source_id, enabled, privacy_class, revision)
- schedule_rule(id, definition_id, zone_id, recurrence, local_date, local_time, dst_policy, valid_from, valid_until)
- reminder_occurrence(id, rule_id, revision, local_key, due_utc, status, UNIQUE(rule_id,revision,local_key))
- reminder_action(id, occurrence_id, action_id UNIQUE, action, action_at_utc, snooze_due_utc NULL)
- notification_outbox(id, occurrence_id, notification_id, operation, status, retries)
- medication_plan(id, display_name, dose_text, notes NULL, privacy_mode)
- medication_intake(id, occurrence_id, action_id UNIQUE, status, action_at_utc)

Foreign keys, referential integrity, indexes, migration tests and retention policy are mandatory. Protect health data in backup/lock-screen threat model. Backend never stores these tables in MVP.
