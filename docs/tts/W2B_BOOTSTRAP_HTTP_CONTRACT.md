# W2B Bootstrap HTTP contract — implemented Worker side / Backend pending

**Status:** Worker HTTP client and local staged coordinator exist. Backend endpoints are proposed contracts and are **not implemented** in this repository. Tests use an injected fake transport, not live RustFS.

## Protocol
1. `POST /internal/v1/workers/init`: if manifest exists, return 200 with authoritative manifest; if not, return HTTP 404 with `{"code":"NO_MANIFEST"}`. 401/403/5xx/timeouts never trigger seed bootstrap.
2. Approved seed catalog is operator provisioned, immutable and pins upstream HTTPS URLs, SHA-256, size, model engine/revision, licenses and voice/locale. An empty seed is intentionally nonfunctional.
3. Worker streams each artifact to a private `.part-` file, verifies checksum + exact bytes, atomically renames to content-addressed disk cache, tracks it in local SQLite.
4. `POST /internal/v1/worker-model-proposals/upload-grants`: authenticated Worker request `{workerId,sha256,sizeBytes,assetId}`, `Idempotency-Key: <sha256>`. Backend issues narrowly scoped short-lived RustFS PUT URL plus immutable `objectKey = models/sha256/<sha256>`.
5. Worker uploads (PUT) only to allowlisted RustFS HTTPS origin. Redirects are refused. `POST /internal/v1/worker-model-proposals/verify-asset` with `{objectKey,sha256,sizeBytes}` must return 200 `{"verified":true}` only if Backend independently checked bytes (not just untrusted ETag or user metadata). Confirmed 404/missing means upload again; 403/network outage means retry, NOT consider missing.
6. `POST /internal/v1/worker-model-proposals` with `Idempotency-Key: <candidateDigest>` and canonical candidate includes uploaded immutable keys. Backend validates catalog allowlist, artifact hashes, licenses, tenant/worker authority, idempotency, model capacity and replay, then approves or rejects. Unique constraint on `candidateDigest` and engine/model/version.
7. Even with `{"status":"APPROVED"}`, Worker must call `init` again. It checks authoritative manifest matches model ID/revision, license, role/asset IDs, SHA-256/size/object key, and voice/locale. Only then does this foundation return `MANIFEST_APPROVED`. Actual inference readiness additionally requires verified model load/warmup (not built in W2B).

## SQLite and crash recovery
Separate `bootstrap_artifact` (SHA-256, local file, verified uploaded key) and `bootstrap_proposal` (digest, status) survive process crash. Re-run verifies local bytes and, if previously uploaded, remotely checks the object. If present and correct no redownload/reupload. Interrupted download is not checkpointed until full file is verified. An interrupted upload may be repeated idempotently. Candidate proposal may be resent with same digest.

## Limitations before real acceptance
- Backend contract is a stub; requires actual Spring Boot endpoints and security authorization.
- Models are not yet loaded by ONNX; a manifest approval is **not** model READY.
- No Kubernetes lease for multiple Worker bootstrap processes; only one local SQLite writer/process expected. Backend needs a global registration mutex/idempotent grants.
- Physical downloads from pinned upstream and RustFS need integration tests with real credentials; fake transport tests do not prove service connectivity.
- HTTP PUT streaming and vendor S3 gateway compatibility require real acceptance.
- Operator must pin actual model and voice bundle hashes; placeholders cannot bootstrap.
- Source URL allowlist and refusal of redirects are defense-in-depth, but network-level egress filtering remains required.
