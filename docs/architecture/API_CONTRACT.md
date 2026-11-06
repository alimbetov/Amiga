# Initial API contract boundaries (proposal, not implemented)

All network traffic HTTPS. Specify OpenAPI, DTO JSON schemas, typed errors and auth before backend implementation. This file is a contract backlog, not a deployed endpoint list.

## Public catalog
- `GET /api/v1/catalog/releases/current` — release ID, version, manifest sha256, locale availability.
- `GET /api/v1/content/categories?locale=en-US` — paginated published categories.
- `GET /api/v1/content/messages?locale=en-US&category=...` — published messages, pagination.
- `GET /api/v1/voices?language=en-US` — qualified voices only.
- `GET /api/v1/assets/{assetId}` — authorized asset metadata/download descriptor, not unbounded public bucket access.

## Opt-in personalized rendering
`POST /api/v1/audio/render`:
```json
{
  "idempotencyKey": "a-client-generated-uuid",
  "scriptId": "morning-support-001",
  "templateVersion": 1,
  "locale": "en-US",
  "segments": [
    {"voiceCode": "af_heart", "text": "Good morning."}
  ]
}
```
Response: `202 Accepted` with `jobId`, `status`, `pollAfterSeconds`; repeated valid key+same payload returns same job; same key+different payload is a conflict. Enforce caller quotas, length/segment limits, template access, consent and expiry. Prefer placeholder/local assembly so the backend does not need personal names or schedules.

`GET /api/v1/audio/jobs/{jobId}`: status, error code or ready asset metadata; ownership authorization and lifecycle retention required.

## Internal worker API
- `POST /internal/v1/tts/jobs/claim`
- `POST /internal/v1/tts/jobs/{id}/heartbeat`
- `POST /internal/v1/tts/jobs/{id}/complete`
- `POST /internal/v1/tts/jobs/{id}/fail`

Worker must authenticate using service credentials; the job claim returns an immutable snapshot containing engine + model revision + bundle checksum + voice code + locale + speed + text hash + output format. Require claim token/lease fencing on heartbeat and completion; validate file checksum, duration, MIME, ownership and single finalization. Storage upload should use short-lived scoped credentials, not database access.

## Admin API
- `POST /api/v1/admin/templates`
- `PUT /api/v1/admin/templates/{id}`
- `POST /api/v1/admin/templates/{id}/preview`
- `POST /api/v1/admin/templates/{id}/publish`

All admin writes audited, role restricted, and version-checked.

## Cross-cutting guarantees
- Error envelope: `{code, message, correlationId, details?}` without sensitive payloads.
- `Idempotency-Key` (HTTP header) and request field strategy must be unified before implementation.
- Pagination and content hashes for offline cache reconciliation.
- Strict validation, rate limits, authentication/authorization, timeouts and replay protection.
- Never upload local medication schedules or personal events by default.
- Store billing verification contracts are a separate milestone; do not grant entitlements from client-reported purchase success.
