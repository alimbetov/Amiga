# Amiga — ADR-MOT-004 v2: Shared Neutral Motivational Audio & User Stories

**Status:** ACCEPTED product architecture, PROPOSED API/data model; 2026-11-06.
**Supersedes:** this file's prior ADR-004 personalized weekly batch design. That design is deprecated for the motivation MVP.
**Important numbering:** `docs/architecture/ARCHITECTURE_AND_ADRS.md` already defines ADR-004 for **platform notifications**. The motivation decision uses a separate namespace **ADR-MOT-004**. Do not overwrite the existing ADR.

## Decision

**Amiga MVP = Local-first Scheduler + Shared Neutral Motivational Audio Catalog + Backend TTS Worker.**

- Motivation text is **neutral**: no `nickname`, no gender, no event title, medication, diagnosis, address or personal schedule in audio.
- `addressingStyle` (NEUTRAL/MASCULINE/FEMININE) is an **optional grammatical text preference**, not a mandatory gender identity attribute. Default `NEUTRAL`; only add localized variants where grammar demands them and real TTS qualification passes.
- Flutter/Drift owns UserProfile (with no mandatory nickname/gender), Event, EventSchedule, MotivationPreferences, used template codes and local OS notifications. No server event scheduler, APNs/FCM or multi-device sync for MVP.
- Server owns approved multilingual catalog, qualified voice/model registry, generation jobs, verified shared AudioAsset metadata and RustFS objects.
- Render **once per immutable text/voice/model/codec/pipeline combination** (and grammatical variant when relevant), then reuse the SAME MP3 across all users.
- Weekly batch is primarily **selection of already generated MP3**. It must NOT create 14 new TTS jobs for every user. Cache misses are generated asynchronously under bounded operator-controlled workload; preferred release workflow is pre-generating and qualifying assets before catalog publication.
- No per-user MotivationBatch/MotivationBatchItem database table required for READY-only synchronous selection. No server-side replay/used history. Rate limits and entitlement enforcement may require minimal pseudonymous installation credentials; privacy notice must accurately cover them.
- Goals: first ~100–150 editorially reviewed templates in first genuinely qualified locale/voice; eventually ~1000 per supported locale, only after quality and load acceptance.
- Existing PR #2 is Draft; don't claim live production Worker → RustFS E2E or voice readiness.

## User stories

| ID | User intent | Acceptance |
| --- | --- | --- |
| US-01 Local profile | Set UI language, speech locale, voice, timezone; optional addressing style | Drift only; no required nickname/sex |
| US-02 Reminder | Create one-time and recurring reminders | Local OS notification even when Backend unavailable |
| US-03 Actions | Complete, snooze, skip, cancel | Action history and recalculation in Drift |
| US-04 Events | Birthday, meeting, important date | Local-only title/notes/schedule |
| US-05 Medication | Private medication reminder and acknowledgement | No personal health information in catalog API |
| US-06 Wellness | Configure break/water/activity reminders | Local schedule, quiet hours and preferences |
| US-07 Voice | Select compatible language and voice | Backend catalog exposes QUALIFIED voice; actual READY separately |
| US-08 Generation | Operator publishes a neutral phrase/voice | One shared TTS job only on asset miss; verified MP3 reference |
| US-09 Playback | User taps local notification | Stream ready MP3 over HTTPS; offline text fallback |
| US-10 Content | Receive versioned motivational catalog updates | Validated published locale, cacheable metadata |
| US-11 Motivation preferences | Enable/disable, categories, voice, 1–N/day, quiet hours | All stored locally |
| US-12 Weekly selection | Request seven days × two messages/day | Exactly 14 different eligible codes when sufficient; server returns READY shared references synchronously |
| US-13 No repeats | Send previously used template codes | Exclusions applied, unique within request; explicit exhaustion |
| US-14 Trigger/listen | Local schedule fires, opens motivation | Local notification regardless of network; MP3 stream on tap when online |
| US-15 Recovery | Network, app or Worker interrupted | Local notifications persist; retry selection without replaying listens; missing media explicit |
| US-16 Privacy | No nickname, gender or personal events sent | Data flow audit, no per-user generation, no false anonymity claim |

## User journey

1. User enables motivational messages, selects `locale=en-US`, a QUALIFIED voice, two/day and categories. Flutter saves preferences in Drift.
2. Flutter sends a weekly selection request with locale, voice, categories, number needed and locally tracked used `templateCode`s. It sends no nickname, gender, schedule, device health data or personal event descriptions.
3. Backend selects only PUBLISHED template locale versions for which a VERIFIED READY shared MP3 exists and the selected voice/model is QUALIFIED. Choose without replacement, excluding prior codes. A published voice can be temporarily unavailable for new synthesis without invalidating already verified assets.
4. Backend returns `200` with 14 items, code/version/audioAssetId and metadata. It does not store a user batch or generate new MP3.
5. Flutter schedules 14 local notification occurrences for local time windows; opening a notification obtains a short-lived authorized media descriptor and streams the MP3. Offline: show local text notification, no automatic online audio.
6. On actual playback, mark code used in Drift. The next request excludes all used codes for strict no-repeat; client may explicitly reset cycle when catalog exhausted.
7. Operator adds a template/voice/model revision: TTS job runs ONCE per required immutable combination; upload and verify RustFS asset, then mark locale/voice release READY.

## Proposed endpoints (NOT IMPLEMENTED)

- `GET /api/v1/voices?language=en-US`: qualified voice catalog, `speechUnsupported` for unsupported language.
- `GET /api/v1/motivations/catalog?locale=en-US`: versioned published catalog and capability metadata, with ETag.
- `POST /api/v1/motivations/selections`: READY-only synchronous weekly selection; 200, no persisted per-user batch; capped input (max days, messages/day, excluded codes, total bytes), quota/rate limiting; optional idempotency token for network retries.
- `GET /api/v1/assets/{audioAssetId}`: authenticated or strictly scoped short-lived media descriptor; never permanent public RustFS bucket.
- Admin/operator (protected): draft/review/publish template locale; pre-generate/qualify missing shared audio; inspect asset coverage/errors.

Example selection request:
```json
{
  "locale": "en-US",
  "voiceCode": "af_heart",
  "days": 7,
  "messagesPerDay": 2,
  "categories": ["GOAL", "PRODUCTIVITY"],
  "excludeTemplateCodes": ["MOT_GOAL_0001", "MOT_PRODUCTIVITY_0015"]
}
```

READY response 200 (sample two items shown; real 7×2 request must return 14 or a typed insufficient response):
```json
{
  "catalogVersion": 5,
  "selectionId": "84b682b8-fbf1-4a43-bc03-df603ac36e08",
  "items": [
    {"templateCode":"MOT_GOAL_0042","templateVersion":2,"audioAssetId":"e76142a1-35bd-4cf9-b54b-534e2c0ff3a7","locale":"en-US"},
    {"templateCode":"MOT_PRODUCTIVITY_0028","templateVersion":1,"audioAssetId":"db4bb7e3-5699-44f0-9229-1ef4d770fcd8","locale":"en-US"}
  ]
}
```

Errors: `INVALID_REQUEST` (400), `VOICE_LOCALE_UNSUPPORTED` (422), `INSUFFICIENT_READY_TEMPLATES` (409 with availableCount; NEVER silently repeat), `RATE_LIMITED` (429), `ASSET_UNAVAILABLE` (503). Ensure error envelope matches project API conventions; do not expose private values. When 200 is returned all references must be currently READY/verified; asset disappearance may occur later and requires retry/fallback.

**Revision policy:** `templateCode` remains dedup identity across content revisions; `templateVersion` pins actual audio. Voice/model revision, voice bundle digest, speed, codec settings and processing version are part of the immutable audio fingerprint. Never share old audio as if it matched new text.

**Selection order:** validated voice+locale → eligible category → PUBLISHED version → verified READY asset for approved voice/model and current audio policy → exclude previously used codes → uniform sampling without replacement. For large catalogs avoid expensive unbounded `ORDER BY random()`; use index-friendly candidate fetch and bounded sampling. Server never chooses an unqualified voice or silently substitutes language.

## Entity boundaries

**Backend PostgreSQL**:
- `motivation_template`: id, code UNIQUE, category, status, created_at.
- `motivation_template_locale`: id, template_id FK, locale, version, neutral_text, review_status, published_at; UNIQUE(template_id,locale,version).
- `motivation_audio_variant`: id, template_locale_id FK, voice_id FK, addressing_style (default NEUTRAL), model_revision/bundle digest, audio_fingerprint, audio_asset_id FK, status; UNIQUE on immutable variant identity.
- Existing `tts_job` and `file_asset`/`audio_asset`; avoid parallel, duplicative job/asset models.
- Backend approval/qualification registry remains authoritative; model and voice storage do not depend on mobile installation.

**Flutter Drift**: local profile without required nickname/gender, motivations preferences, `motivation_used_template(code, played_at)`, local scheduled motivation item(occurrence ID, code, version, audioAssetId, due UTC), reminder/event/schedule definitions. Event and medical details are not sent to Backend. Optional `addressingStyle` only for locales with a qualified variant; do not introduce sex/gender field for the sake of grammar.

**No mandatory server MotivationBatch/BatchItem or persistent per-user history.** If future offline preload or async on-demand content is required, introduce a versioned separate API/ADR and bounded storage.

## Legal, safety and content policy

- Neutral public text is preferable to per-person audio, but IP addresses, purchase information, analytics and installation tokens can still be personal data. Do not claim zero data collection without a data flow audit.
- Check Kazakhstan personal-data localization/cross-border constraints if applicable; GDPR/UK GDPR, platform privacy disclosures, minors policies and processor contracts by target market.
- License approval, provenance and attribution for voice/model bundles, text authorship, libraries and content delivery.
- No diagnostic/therapeutic promises, coercive language, shaming, medical dosing guidance or deceptive autoplay claims. Native-speaker editorial review for translations and human listening acceptance for each qualified voice.
- Avoid compulsory identity fields. Keep permission choices and quiet hours local.

## Acceptance gates

1. 7×2 returns 14 distinct, approved, READY template codes when sufficient inventory; excludes every submitted used code, across versions.
2. Same source text/voice/model/config creates at most one durable shared READY AudioAsset; no N×users generation.
3. Missing/invalid audio is NOT returned READY. Production publication requires verified object and Worker readiness/qualification at generation time.
4. Unsupported speech locale/voice yields explicit typed response without cross-language fallback.
5. Catalog exhaustion returns explicit insufficient count; client may reset cycle with user control.
6. Flutter reminders work offline, survive allowed OS restarts and reconcile permission/timezone changes; user tap-to-stream MP3 is not guaranteed offline.
7. No nickname, gender or private Event details in selection API or backend content storage; secure media URLs and rate limits.
8. Source and DB migrations use immutable versions, FK/unique constraints; admin publish is audited.
9. Test concurrent audio publication, duplicate selections, failed worker, RustFS 404/403/5xx, revoked voice/model, missing media, latency/load and recoverable retries.
10. Real E2E on exact SHA: operator publish → qualified Worker → MP3 RustFS → VERIFIED READY catalog → selections API → Flutter notification → audio stream; no Draft PR treated as production ready.

## Phased delivery

P0: qualify actual Worker/model/locale/voice, integrate Backend/RustFS and verify audio.
P1: approve 100–150 neutral phrases in first qualified locale; enforce editorial publication.
P2: Flyway entities + shared audio fingerprint/asset lifecycle; pre-generate/validate files.
P3: READY-only selection REST, exclusion and coverage/error tests.
P4: Flutter Drift local scheduler, playback and fallback; device acceptance.
P5: pilot metrics (opt-in retention, week-2 return, actual plays, storage/egress); expand to ~1000 qualified phrases and additional languages only after evidence.

## Existing docs and compatibility

- `docs/architecture/ARCHITECTURE_AND_ADRS.md`: existing ADR-004 is platform notifications, NOT motivational audio.
- `docs/architecture/DATA_MODEL.md`, `docs/tts/SELF_MANAGED_TTS_WORKER.md`, `docs/tts/LANGUAGE_SUPPORT_KOKORO.md` remain source constraints.
- This update supersedes the earlier personal `nickname` weekly batch payload in this document. Dedicated opt-in arbitrary personal TTS rendering remains separately scoped; not part of Shared Motivation MVP.
