# Amiga — Your Personal Voice Companion

**Amiga — рядом в нужный момент.**

Amiga is a privacy-first, offline-first Android/iOS application for reminders, events, medication schedules, daily encouragement and optional personalized voice content.

## Product scope

- **MVP**: local profile; reminders; recurrence; snooze/complete; medication acknowledgements; events; birthdays; notification permission handling; offline reopen.
- **Next**: content library, template planner, Kokoro TTS worker, audio storage and offline playback.
- **Later**: daily voice companion, dual voices, premium billing, extra features.

## Architecture

| Component | Technology | Responsibility |
| --- | --- | --- |
| Mobile | Flutter, Riverpod, Drift/SQLite, GoRouter | User-owned local data, reminders, scheduling, UI |
| API | Java 21, Spring Boot, PostgreSQL, Flyway | Content catalog, versions, TTS orchestration |
| Worker | Python, ONNX Runtime, Kokoro, FFmpeg | Speech synthesis; no direct access to personal DB |
| Assets | RustFS, MP3 | Content and audio assets |
| CI | GitHub Actions | Tests, static analysis, security/release checks |

**Non-negotiable:** local reminders work without API connectivity. A scheduled notification is not proof that it was delivered or read. Automatic speech on a locked device is not guaranteed.

## Development order

1. Flutter Foundation + Drift + Local Reminder Engine.
2. Offline and device notification acceptance.
3. Backend content catalog and TTS API contracts.
4. Kokoro Worker + MP3 → RustFS → Flutter offline playback.
5. Voice Companion and commercial purchase validation.

## Project documentation

- [Architecture and ADRs](docs/architecture/ARCHITECTURE_AND_ADRS.md)
- [Data model](docs/architecture/DATA_MODEL.md)
- [API contracts](docs/architecture/API_CONTRACT.md)
- [MVP backlog](docs/product/MVP_BACKLOG.md)
- [Roadmap and acceptance](docs/product/ROADMAP.md)
- [Source specification](docs/product/SPEC_SOURCE.md)

## Privacy and safety

Personal schedules and medication information stay on the device by default. Cloud speech rendering is opt-in and uses minimum necessary data. Medication reminders are user-configured and must not provide dosing advice or claim guaranteed critical delivery. App-store purchase restoration uses official store mechanisms, not device fingerprints.

## Status

Architecture / documentation bootstrap. No production implementation or CI acceptance is claimed yet.
