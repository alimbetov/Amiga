# Notifications
P0 local notifications: medication, personal reminders, work breaks, eye rest and walks work without network, subject to OS permissions/restrictions. Button actions require idempotency and fallback in-app navigation. Medical lock-screen preview neutral by default. Do not claim guaranteed critical delivery.

Later opt-in remote push: FCM/APNs for content announcements or server events. Remote push may be delayed, suppressed or undeliverable; never used as sole delivery path for medication. No sensitive data in remote payloads.
