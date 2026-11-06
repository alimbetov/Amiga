# Architecture & ADRs (draft v1)

## Context
Amiga is an offline-first mobile app. The mobile app owns reminders, medication schedules, important dates, and history; the server owns reusable, versioned content, voices, TTS jobs and premium entitlements. **Do not synchronize sensitive user events by default.**

## Boundaries
```text
Flutter UI -> Reminder domain -> Drift/SQLite -> OS notification adapter
          -> Catalog cache -> HTTPS Spring Boot modular monolith
                                 |-> PostgreSQL/Flyway
                                 |-> TTS job/lease API -> Python Kokoro Worker
                                 |-> file asset metadata -> RustFS
```
A local notification is the delivery mechanism subject to platform permission/OS limitations; automatic speech from a locked device is not an MVP guarantee.

## ADR-001: Offline first and data ownership
**Decision:** recurring rules, occurrences, state transitions, medication intake logs and user profile persist locally in Drift. The API is optional for reminder execution. **Trade-off:** uninstall/reinstall may lose data until an explicit export/restore solution ships.

## ADR-002: Modular monolith
**Decision:** one Spring Boot app with internal modules (catalog, templates, localization, audio, assets, entitlement, admin). One PostgreSQL instance. Avoid microservice decomposition before measured scaling needs.

## ADR-003: Time semantics
**Decision:** recurring schedules store local time + IANA time zone + recurrence rule + DST policy; each materialized occurrence stores UTC instant and occurrence ID. Recalculate future occurrences upon zone, clock or DST change. Decide and document nonexistent/ambiguous local time behavior before release.

## ADR-004: Platform notification model
**Decision:** Flutter schedules a bounded rolling window; persist intended schedule in Drift; reconcile on app start/permission changes/eligible platform hooks. On Android validate notification and exact-alarm policies; on iOS respect pending notification limits, Focus, and OS background restrictions. Never infer display/reading from scheduling.

## ADR-005: TTS engines
**Decision:** backend engine abstraction. Kokoro ONNX is a candidate for qualified English voices, not presumed to handle Russian/Kazakh. Verify phoneme quality, licenses, bundles and attribution per released voice. Cache by normalized text + model/bundle identity + voice + speed + processing configuration.

## ADR-006: Privacy
**Decision:** content rendering locally when possible; server rendering opt-in, minimal payload, controlled retention, unguessable asset access, short-lived URLs, no sensitive message bodies in logs. Avoid collecting names, medication details or addresses for generic catalog delivery.

## ADR-007: Medication safety
**Decision:** user enters schedule and dosage; Amiga does not calculate or advise prescriptions. Acknowledgement is explicit; no automatic doubling of missed doses. Sensitive notification bodies hidden by preference. No promise of guaranteed time-critical reminders.

## ADR-008: Purchase ownership
**Decision:** premium is verified with platform-issued transaction evidence; backend entitlement state reflects revocation/refunds, renewals and restore. Local anonymous installation IDs are not purchase ownership identifiers.

## ADR-009: Release gates
**Decision:** exact-SHA CI + real device Android/iOS acceptance are required. Unit/widget/integration tests alone cannot qualify OS notification reliability.

## Unresolved before implementation
- Exact iOS/Android notification plugin integration and rolling window.
- DST ambiguity policy, data export/restore format, and encryption threat model.
- Russian and Kazakh licensed speech engines/voices.
- Store monetization model and license clearance for the Amiga brand.
- Operational retention for personalized speech and TTS costs.
