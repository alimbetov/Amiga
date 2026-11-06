# Worker API contract examples (proposed, not deployed)

**Init** `POST /internal/v1/workers/init`

```json
{"workerId":"kokoro-01","engineCode":"KOKORO","engineVersion":"1.0","supportedManifestSchemaVersions":[1],"capabilities":{"formats":["mp3"]}}
```

```json
{
  "schemaVersion":1,"manifestId":"manifest-kokoro-12","manifestVersion":12,
  "manifestSha256":"<canonical-manifest-sha256>","refreshAfterSeconds":1800,
  "models":[{
    "modelId":"kokoro-82m","modelRevision":"1.0","engineCode":"KOKORO",
    "enabled":true,"commercialApproved":true,"licenseSpdx":"Apache-2.0",
    "model":{"assetId":"model-kokoro-1","downloadUrl":"https://rustfs.example.invalid/model-presigned","sha256":"<sha256>","sizeBytes":100},
    "voices":[{"voiceCode":"af_heart","locale":"en-US","assetId":"voice-heart-1","downloadUrl":"https://rustfs.example.invalid/voice-presigned","sha256":"<sha256>","sizeBytes":100}],
    "preload":true,"limits":{"maxInputTokens":400,"targetChunkTokens":200,"maxAudioSeconds":120}
  }]
}
```

The digest includes only canonical stable fields and must be computed by a shared contract library; `<...>` placeholders are not real values. Download URLs expire and are not part of fingerprint. Backend can return pre-signed URLs just in time.

**Claim** `POST /internal/v1/tts/jobs/claim`
Response includes immutable snapshot {jobId,claimToken,leaseEpoch,engineCode,modelId,modelRevision,modelSha256,voiceCode,voiceBundleSha256,locale,textHash,speed,pipelineVersion,objectKey}. For long text prefer short-lived encrypted payload fetch over logging/persistent plaintext.

**Complete** `POST /internal/v1/tts/jobs/{jobId}/complete`
{claimToken,leaseEpoch,fingerprint,objectKey,sha256,sizeBytes,durationMs,codec:"mp3"}. Backend owns object verification and READY transition.
