# Source specification

Source: **AMIGA — Product Vision & Technical Specification v1.0**, dated 2026-11-06, provided as project initiation material.

Product equation: **Reminder + Voice + Personal Support**.

Key commitments:
- User account optional.
- Privacy-first: personal events, medication schedules and history remain local by default.
- Offline-first: reminders work without server.
- Flutter + Dart + Riverpod + Drift; Java 21 + Spring Boot + PostgreSQL; Python + Kokoro ONNX; RustFS.
- Core reminders, events, birthdays and medication acknowledgement are first release priorities.
- Local notification scheduling, not guaranteed locked-screen long-form auto-play.
- English Kokoro voices are candidates; Russian/Kazakh TTS require independent quality/license validation.
- Content templates, TTS worker and premium purchases are separate later releases.

This folder contains an implementation-oriented interpretation of the source, **not** a claim that all listed features are built. Preserve the full original specification in the project knowledge/base document archive. Resolve open ADRs and contracts before implementing network-dependent behavior.
