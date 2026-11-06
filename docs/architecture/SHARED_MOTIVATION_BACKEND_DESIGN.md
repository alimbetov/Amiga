# Amiga Backend Design — Shared Motivation Catalog v1

**Status:** DESIGN PROPOSAL (not implemented). 2026-11-06.
**Decision source:** [ADR-MOT-004 v2](../product/MOTIVATION_WEEKLY_BATCH_USER_STORIES.md).
**Target:** Java 21, Spring Boot modular monolith, PostgreSQL/Flyway, TTS Worker and RustFS; reuse existing modules/contracts, not competing tables.

## Context and design invariants

1. Public neutral motivational audio, generated once per immutable script+qualified voice/model+encoding pipeline, then shared.
2. No server-side mobile Event, EventSchedule, UserProfile, gender, nickname or per-installation motivation history for the motivation MVP.
3. Ready-only synchronous weekly selections; no TTS generation in end-user selection transaction and no user-owned Batch tables.
4. Operator content pipeline can asynchronously generate missing MP3 and publish READY variants after authoritative verification.
5. Public metadata access does not imply direct unlimited bucket access. Apply per-caller request quotas and short-lived scoped media links.
6. Mobile is source of truth for OS notification schedule and used-template exclusions.
7. Locale text availability != speech capability; engine/voice must be approved, commercially licensed and actually qualified.

## Domain model

### motivation_template
- `id uuid PK`
- `code varchar(100) NOT NULL UNIQUE` (stable across revisions, e.g. MOT_GOAL_0042)
- `category varchar(40) NOT NULL` (controlled enumeration)
- `status varchar(20) NOT NULL` DRAFT|ACTIVE|ARCHIVED
- `created_at timestamptz NOT NULL`, `updated_at timestamptz NOT NULL`
- Optional editorial metadata (source, license, review notes) is admin-only.

### motivation_template_locale
- `id uuid PK`, `template_id uuid NOT NULL FK motivation_template`
- `locale varchar(20) NOT NULL`, `version integer NOT NULL CHECK(version>0)`
- `text_plain text NOT NULL`, `review_status varchar(20) NOT NULL` DRAFT|REVIEWED|PUBLISHED|RETIRED
- `text_sha256 char(64) NOT NULL`, `published_at timestamptz NULL`
- `UNIQUE(template_id,locale,version)`
- Preserve immutable published content; changes create a NEW version. Text must not contain personalization tokens, private content or markup commands in v1. Validate length/canonicalization.

### motivation_audio_variant
- `id uuid PK`, `template_locale_id uuid NOT NULL FK motivation_template_locale`
- `voice_id uuid NOT NULL FK existing tts_voice`
- `addressing_style varchar(16) NOT NULL DEFAULT 'NEUTRAL'` (v1 supports NEUTRAL only)
- `audio_fingerprint varchar(128) NOT NULL UNIQUE` (opaque deterministic canonical identity; versioned)
- `asset_id uuid NULL FK existing file_asset`
- `status varchar(20) NOT NULL` PENDING|GENERATING|VERIFYING|READY|FAILED|RETIRED
- `tts_job_id uuid NULL FK existing tts_job`, `updated_at timestamptz NOT NULL`
- `UNIQUE(template_locale_id,voice_id,addressing_style,audio_fingerprint)`; all READY rows require verified existing asset id and matching immutable identity.
- Model and bundle SHA, voice revision, speed, codec and pipeline version must be captured in fingerprint and immutable job snapshot; store qualified references as existing TTS job/voice data or additional columns only if existing schema cannot represent them.
- If voice qualified model revision changes, create new variant/fingerprint rather than silently rebinding.

### Existing entities to reuse
- `tts_voice` / `tts_voice_model` / `tts_engine`: approval, license, locale and runtime readiness as defined by authoritative manifest.
- `tts_job`: idempotent, leased/fenced worker processing and output finalization.
- `file_asset`: verified RustFS object metadata (hash, size, duration, codec, key, lifecycle); NEVER treat an unverified upload as READY.

### Indexes
- `motivation_template(category,status,code)`
- `motivation_template_locale(locale,review_status,template_id,version DESC)`
- `motivation_audio_variant(voice_id,status,template_locale_id)`
- Unique constraints as above; avoid simultaneously publishing ambiguous current versions, using a transactionally maintained current-publication pointer or query rule with explicit newest approved version and tested concurrent publish semantics.

## Lifecycle

```text
Template DRAFT -> REVIEWED -> PUBLISHED (immutable locale version) -> RETIRED
      ↓ qualified voice / approved manifest
AudioVariant PENDING -> GENERATING -> VERIFYING -> READY -> RETIRED
                          | failure -> FAILED -> retry by operator policy
```

Publication gate: locale reviewed, model/voice qualified and commercially approved, worker warm for synthesis, generated MP3 verified in RustFS (MIME, duration, SHA256, object existence and auth); only READY variant becomes selectable. Missing assets trigger reconciliation/repair but never produce a READY selection. Do not purge shared assets while referenced by published variants.

## REST contracts

### POST /api/v1/motivations/selections
- Request: `{locale,voiceCode,days,messagesPerDay,categories,excludeTemplateCodes}`.
- Validate BCP-47 locale, max 7 days, messages/day configurable (e.g. 1–5), non-empty allowed categories, bounded exclude list (e.g. max 2000 codes and request bytes), and approved locale/voice.
- Ignore nothing silently; typed error response `{code,message,correlationId,details?}`.
- Determine requestedCount = days × messagesPerDay.
- Acquire consistent published-catalog snapshot; select only eligible template codes with verified READY shared assets, excluding submitted codes and duplicates. Select unbiased without replacement from bounded eligible candidates. Ensure multiple locale versions of same code do NOT produce duplicate codes.
- Return HTTP 200 with `catalogVersion,selectionId,items[]`, each `templateCode,templateVersion,locale,voiceCode,audioAssetId`; `selectionId` is response correlation ID only and is NOT a server-owned user history.
- Empty result/exhaustion: 409 `INSUFFICIENT_READY_TEMPLATES`, details `{requestedCount,availableCount}`; client may explicitly reset its used cycle. No hidden substitution across categories/languages/voices.
- Repeated submissions with same input may choose different random outputs; for retry stability client should pass an optional bounded idempotency key, supported by ephemeral TTL cache keyed to authenticated caller/installation scope and payload digest, or Flutter should persist the successful returned selection before resending. Do not store permanent user batch history.
- Rate-limit and protect excessive media streaming; unknown installation ID alone is not authentication.

### GET /api/v1/motivations/catalog?locale=...
- Versioned visible neutral template metadata, supported categories and optional readable text. ETag/If-None-Match for offline cache.
- Do not advertise a voice until qualification is approved; if template text available but speech unsupported return explicit capability flag.

### GET /api/v1/assets/{id}
- Existing authorized or narrow scoped media descriptor with short expiry, no permanent public RustFS links. Grant policy for **public reusable audio** may be public read subject to rate limits, while private files remain separately scoped.

### Admin
- Create/update draft; stage translations; editorial review and publish immutable version.
- Trigger pre-generation for selected qualified voice(s), poll coverage, inspect failures and request retry. All admin writes audited and role restricted.
- Editing published content makes new version and invalidates matching audio fingerprint; do not mutate historical READY file.

## Service interfaces

```java
interface MotivationSelectionService {
    MotivationSelectionResponse select(MotivationSelectionRequest request, CallerContext caller);
}
interface MotivationCatalogService {
    MotivationCatalogResponse publishedCatalog(String locale, String etag);
}
interface SharedMotivationAudioService {
    AudioVariant ensureGenerated(TemplateLocaleVersion script, QualifiedVoice voice);
    boolean verifyReady(AudioVariant variant);
}
interface QualifiedVoiceRegistry {
    QualifiedVoice requireSpeechCapable(String locale, String voiceCode);
}
```

Suggested modules: `motivation.domain`, `motivation.application`, `motivation.api`, `motivation.persistence`, integrated with existing `tts` and `assets`; no new microservice/broker.

## Selection pseudocode

```text
validateRequestAndCaller()
voice = qualifiedVoiceRegistry.requireSpeechCapable(locale, voiceCode)
candidates = repository.findPublishedCurrentLocaleWithVerifiedReadyAsset(
   locale, voice.identity, categories, notIn(excludeTemplateCodes))
candidates = distinctByStableTemplateCode(candidates)
if candidates.size < days * messagesPerDay:
    return INSUFFICIENT_READY_TEMPLATES(requested, candidates.size)
selected = secureRandomSampleWithoutReplacement(candidates, requested)
return selected.mapToAudioAssetReferences()
```

For 1000 templates, fetching eligible codes then Fisher-Yates sampling is acceptable after measuring. On large catalogs move toward indexed random keys/reservoir sampling; don't rely on unbounded `ORDER BY random()` at scale.

## Migration outline

Flyway sequence with review and existing schema inspection: `V...__motivation_template.sql`, `V...__motivation_template_locale.sql`, `V...__motivation_audio_variant.sql`. Do not hardcode a version number before inspecting current migrations. Include CHECKs, FK policies, unique indexes, published-version immutability enforcement in application + DB where appropriate, and rollback/forward migration tests.

## Test plan

- Contract: 7×2 yields 14 unique codes; exact locale/voice matching; exclude all codes; 0 remaining → typed 409; stable code across versions.
- Unit: unbiased sampling, malformed/max-size excludes, categories, no nickname/gender fields, locale mismatch, unavailable qualified voice.
- DB integration/Testcontainers PostgreSQL: Flyway migration, uniqueness, concurrency, double publish, immutable version and FK consistency, READY requires verified asset.
- Job integration: one job/asset for concurrent identical variant requests; model/voice/codec revision changes fingerprint; Worker lease/replay/crash recovery, upload verification.
- Resilience: RustFS 404, 403 and 5xx distinguished; failed media not served READY; no unauthorized fallback.
- Flutter: Drift used-code tracking, offline text notification, schedule after reboot/timezone change under OS constraints; streamed audio on tap; no background autoplay guarantee.
- Security: SSRF-resistant storage access, quotas, media access scope, admin roles, redacted logs; verify analytics/installation token privacy disclosures.
- Acceptance: exact-SHA CI + real device Android/iOS + real Backend→Worker→RustFS MP3→Flutter playback. Current Worker PR #2 Draft is a blocker, not accepted infrastructure.

## Release sequencing

1. Complete existing Worker PR #2 hardening and real qualified language/voice/model path (Kokoro en-US is target, not proven production READY).
2. Add Flyway entities/catalog editorial workflow and generate 100–150 neutral phrases for qualified voice(s).
3. Verify every published MP3 and catalog link, add selection API and tests.
4. Flutter local scheduler + 7-day selection cache and streaming media.
5. Run small user pilot; measure D7/D30 retention, listens, push-free notification reliability, content quality and network/storage costs.
6. Only then expand to ~1000 reviewed templates and further locale/voice pairs.

**Non-goals:** persistent nickname, gender, user event data, personalized TTS batch per user, server scheduler, remote push, per-user selection batch tables and forced audio download.
