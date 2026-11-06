# Amiga — User Stories and ADR-004: Weekly Motivational Audio Batch

Status: **ACCEPTED product direction / PROPOSED API contract**, 2026-11-06.
Scope: MVP architecture and future rollout. No implementation or production qualification is claimed.

## Prior decisions and boundary

- ADR-001: **Local-first Scheduler + Backend TTS**. Flutter + Drift own profile, reminders, schedules, occurrence/actions and motivation notification cadence; notifications must still appear offline. No server event scheduler, server push, FCM/APNs or multi-device sync in MVP.
- ADR-002: abstract versioned voice templates; Flutter can resolve `{{nickname}}` locally for single TTS rendering. For the explicitly requested **weekly batch REST convenience endpoint**, Backend MAY accept `nickname` as a temporary input and resolve approved catalog templates server-side. This is an intentional *narrow exception* to local-only resolution: no permanent server UserProfile or nickname column, no linkage to the user's private Event.
- ADR-003: voice/template compatibility is based on **Backend-approved model/voice manifest + actual warmed Worker readiness + qualification**, not translation availability or worker self-assertion. UI locale, text locale and speech locale are distinct. Unsupported pairs fail explicitly; no cross-language silent fallback.
- ADR-004: **7-day randomized batch generation**, with client-side used-template history and explicit exclusions. Motive templates are abstract, and no private reminder, medication, calendar, location, or event details should be transmitted.

## User stories

| ID | User story | Expected outcome |
|---|---|---|
| US-01 Profile | As a user, I choose nickname, language, timezone and voice. | Profile stored in Drift; nickname optional, nonunique; no real name required. |
| US-02 Reminder | I create one-time/repeating reminders. | Drift persists schedule and system local notification. |
| US-03 Action | I snooze, complete, skip or cancel a reminder. | Local history and schedule update; no false guarantee of OS delivery. |
| US-04 Events | I create birthdays, meetings and dates. | Private notes/title remain local. |
| US-05 Medication | I create a private medication reminder and acknowledge it. | Sensitive details remain on-device; no medical dosing advice. |
| US-06 Wellness | I configure breaks, hydration or routine reminders. | Local schedule and notification permissions handled. |
| US-07 Voice | I choose a supported speech locale/voice. | Only QUALIFIED compatible voices shown; unavailable Worker reflected separately. |
| US-08 TTS | I request audio for a neutral template. | Async TTS job, verified MP3 in RustFS, READY asset reference. |
| US-09 Playback | I open a reminder/motivation notification. | Flutter streams MP3 by authorized HTTPS; if offline, text notification remains. |
| US-10 Catalog | I receive updated abstract templates. | Versioned localized catalog and offline cache. |
| **US-11 Motivation preferences** | I opt in to motivation, select categories, daily count, time window, voice, and nickname personalization. | All preferences and schedule local; default opt-out for sending nickname. |
| **US-12 Weekly batch** | I request e.g. 7 days × 2 messages/day. | 14 distinct eligible templates reserved atomically and rendered asynchronously; HTTP 202 with batchId. |
| **US-13 No repeats** | On subsequent batch request I submit previously used template codes. | Excluded codes not selected, no duplicates within batch, explicit exhausted-catalog response. |
| **US-14 Delivery and listening** | I receive local notification and open it. | Device maps scheduled item to batch/audioAssetId and plays stream; no guaranteed lock-screen autoplay. |
| **US-15 Recovery** | My app closes, network drops, or Worker restarts. | Local schedule remains; resumable batch polling and safe retry; previously READY items remain valid until expiry. |
| **US-16 Privacy/deletion** | I disable personalization or delete local data. | No nickname sent on future requests; personal audio retention/deletion policy and user-authorized deletion endpoint. |

## Example end-to-end journey

1. Flutter saves `nickname=Alex`, `locale=en-US`, `voice=af_heart`, motivation enabled, two/day, allowed windows, and usage history in Drift.
2. Client requests seven-day motivation batch. In the **single-call convenience mode**, client sends nickname (only with user opt-in), locale, qualified voice, count and `excludeTemplateCodes`; no events/scheduledAt/medical text.
3. Backend authorizes request, validates limits, locale/voice, filters PUBLISHED/QUALIFIED templates, shuffles without duplicates, stores immutable templateCode/version per batch item and enqueues 14 idempotent TTS jobs. Responds `202 Accepted` quickly.
4. Template text is server-rendered only for the explicit batch mode and only from allowed placeholders; no arbitrary user-authored script. Worker sees final text and pinned model/voice/speed/codec identity, never nickname as a structured profile attribute.
5. Worker generates chunked or direct WAV/PCM -> MP3, uploads to RustFS; Backend checks ownership, lease/fencing and object integrity before READY.
6. Flutter polls batch status and stores READY audio references locally; its local scheduler chooses the times. User taps system notification and audio streams if online; offline notification still works.
7. After playback Flutter records the **used** template code in Drift. Next batch excludes used codes. Optional downloads for offline speech are a separate feature.

## Proposed REST contract (not implemented)

`POST /api/v1/motivations/batches` — request requires authorization, Idempotency-Key scoped to authenticated installation; max 7 days and configurable server caps on count/history/size.

```json
{
  "nickname": "Alex",
  "locale": "en-US",
  "voiceCode": "af_heart",
  "days": 7,
  "messagesPerDay": 2,
  "categories": ["GOAL", "MORNING"],
  "excludeTemplateCodes": ["MOT_GOAL_0001", "MOT_MORNING_0004"],
  "personalizationEnabled": true
}
```

Response `202 Accepted`:
```json
{
  "batchId": "2f55e1d0-0a41-48ae-a2fc-b042a8f792b5",
  "status": "PROCESSING",
  "requestedCount": 14,
  "readyCount": 0
}
```

`GET /api/v1/motivations/batches/{batchId}` returns `PROCESSING|PARTIALLY_READY|READY|PARTIALLY_FAILED|FAILED|EXPIRED`, counters and per-item `templateCode`, `templateVersion`, `status`, and `audioAssetId` only when READY. Expose authenticated, short-lived media access through an appropriate separate endpoint. No permanent direct public URLs.

Alternative strict ADR-002 route: backend selects eligible templates and sends text to Flutter; Flutter resolves nickname and submits one render-batch. This avoids structured nickname as batch input but uses an extra round trip. The convenience route above is the agreed candidate for US-12, not a change to single-template rendering.

## Entity ownership

**Backend / PostgreSQL**: MotivationTemplate(code, category, status, version), MotivationTemplateTranslation(templateId, locale, text, approved placeholders), MotivationBatch(id, callerScope, status, requestedCount, readyCount, createdAt, expiresAt), MotivationBatchItem(batchId, templateCode, templateVersion, ttsJobId, audioAssetId, status), existing TtsGenerationJob and AudioAsset. Do **not** add UserProfile or permanent nickname fields. Personal text in temporary TTS job snapshot only with strict retention, encryption where needed and redacted logs.

**Flutter / Drift**: UserProfile(local UUID, nickname, preferredLanguage, timezone, preferredVoiceId), MotivationPreferences, MotivationUsedTemplate(code, version, playedAt), MotivationScheduledItem(local occurrence id, templateCode, audioAssetId, schedule), Event and EventSchedule. Device owns local notification scheduling.

## Selection algorithm and safety

- Filter by locale, categories, PUBLISHED template versions, qualified compatible voice/model capabilities and optional exclusions; do not advertise voice support from translation alone.
- Uniform randomized selection without replacement within a batch; protect concurrent retries by request idempotency and unique `(batchId,templateCode)`.
- Send **all used codes** when global no-repeat until exhaustion is required. A rolling 100–200-code window deliberately relaxes that guarantee. Version policy: treat template code as identity for dedup even across versions unless explicitly changed in future.
- If fewer than requested, return explicit `INSUFFICIENT_TEMPLATES` with availableCount and no silently repeated selections. Empty nickname selects a reviewed non-personalized variant.
- Rate-limit batch creation, avoid per-nickname global cache sharing, ensure user/installation-scoped authorization for personal audio, avoid predictable public SHA keys for short text, and define retention (temporary input snapshots, batch metadata, RustFS objects and backups).
- Do not claim random motivational audio is an urgent alarm. Mobile OS notification and background autoplay restrictions apply. No remote push in MVP.
- Content review: avoid medical/financial guarantees, coercive or shaming language; localize idiomatically, test native-speaker quality and actual TTS pronunciations.

## Worker compatibility and acceptance

- Existing worker policy: Kokoro en-US (`af_heart`, `am_michael`) is a **phase-1 target**, not production-qualified by documentation alone. Piper locales require loaded/approved model/voice bundle; ru-RU/kk-KZ must not be silently routed to English Kokoro. Qwen3 is future scope.
- Use backend approved manifest, engine/model/voice artifact hashes, commercial license allowlist, runtime warm readiness (`/TTS/ready`, `/health/info`) and accepted inference tests.
- Short templates target no chunking; max recommended rendered text ~150 chars is **product policy, not engine cap**. Enforce actual engine/phonemization token limits.
- PR #2 remains Draft; current dev MP3/health slices are not real Backend→Worker→RustFS production acceptance.

## Acceptance criteria

1. Exactly 14 unique eligible codes for 7 × 2 if catalog sufficient; exclusions honored across batches and retries.
2. Bad locale/voice returns typed validation error, no job created; absent/stale worker readiness is distinct from unsupported catalog.
3. Idempotent re-submit returns same batch; no duplicate TTS work; worker crash/replay/lease cannot double-publish asset.
4. Batch progresses asynchronously and reports per-item partial failure, retry, expiration and verified audio metadata.
5. No permanent profile/nickname database column; no private event text transmitted; no raw nickname or rendered personal phrase in routine logs.
6. Flutter notification triggers offline; tap-to-stream uses authorized MP3 online, with accessible text fallback offline.
7. User changes nickname/voice: new batches reflect the change; older audio remains immutable and expires under retention policy.
8. Motivational catalogue entries are published only when locale and actual voice/model qualify; human review of selected utterances.
9. Race and failure tests: concurrent requests, exhaustion, empty nickname, invalid placeholders, RustFS errors, Worker unavailable, revoked voice, expired media, timezone changes.
10. End-to-end smoke on exact code SHA: Flutter → Batch API → TTS Job → Worker → MP3 → RustFS → READY → Flutter playback.

## Delivery order

M1: review 100–150 neutral motivation templates in first qualified speech locale. M2: publish catalog and validate capability compatibility. M3: batch selection/idempotency/jobs and results API. M4: worker/RustFS E2E. M5: Flutter Drift history + local scheduler + audio playback. M6: grow to ~1000 reviewed templates per supported locale when quality and operational costs pass acceptance.

This specification augments existing docs in `docs/architecture/` and `docs/tts/`; it does not rewrite prior implementation plans or indicate completed code.
