# Work & Wellness — specification v1.1
Status: proposed optional module, follows core reminder engine.

## Onboarding (all questions skippable)
- Profession/occupation category: optional broad grouping, not employer or exact title.
- Work setting: office, home, hybrid, mobile, not specified.
- Work character: primarily seated, standing, moving, mixed.
- Screen hours: bucket or custom value.
- Workdays, shift start/end, lunch break, quiet hours and time zone.
- Individually enable eye break, stretch, short walk, lunch and end-of-day reminder.

Occupation alone never establishes actual activity or health. Do not monitor computer activity in MVP. Store profession/work-profile only locally; no analytics, logs or server transmission by default. Users can view, edit, remove or skip occupation.

## Configurable starting intervals (not clinical guarantees)
Eye breaks: suggested every 20 minutes while active; briefly look away.
Stretch/stand: every 60 minutes.
Walk: every 120 minutes.
Lunch and end-of-work: user-selected time.
All intervals configurable, with separate min/max, quiet hours and a daily cap. Work session states INACTIVE, ACTIVE, PAUSED, ENDED. Suppress during pause, lunch and nonworkdays; coalesce collisions.

## Adaptation
Off by default. Aggregate local snooze/skip feedback to *propose* longer intervals within limits (e.g. after three snoozes). User confirms; changes reversible and explained. Does not affect medications.

## Acceptance
No account/network needed; profession optional and erasable; work profile edits reconcile notifications; DST/overnight shifts; no duplicate prompts; offline persistence; tested on physical Android and iOS devices.
