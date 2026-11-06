# International release privacy and notifications — ADR
Status: initial engineering baseline, not jurisdiction-specific legal opinion.

Treat occupational groups, work schedules, screen-time estimates and medical data as potentially personal data. GDPR/UK GDPR privacy-by-design as a conservative baseline: legitimate processing basis, minimization, purpose limitation, transparency, retention policy, access/deletion/export, security and data-flow documentation. Local storage is not an automatic legal exemption. Assess applicability, international transfers, subprocessors, age suitability and market-specific rules before release.

Profession is optional, prefer broad grouping, never collect employer or exact job title as a requirement. Work profile and medical regimens stay in local Drift by default, not in backend, remote push, analytics or technical logs. User can inspect, edit, erase. Medication information is sensitive health-related data, requiring extra controls. Review encryption, OS backups, lockscreen exposure and user-controlled export.

Cloud TTS is an optional separate flow with minimal payload, transparent retention and access controls. Notification permission is distinct from marketing consent. Prepare Apple App Privacy and Google Play Data Safety disclosures based on the actual app and SDK behavior. Remote push FCM/APNs is future opt-in online content alerting; core reminders always scheduled locally.

Release gates: privacy/data flow inventory; SDK review; security and data deletion tests; country-by-country legal review as applicable; real-device permission and notification acceptance.
