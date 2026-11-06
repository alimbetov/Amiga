"""Fail-closed bootstrap policy validation and candidate generation.

Does not perform downloads or approve manifests. A future transport/storage
adapter must verify bytes, upload with scoped grants and obtain Backend approval.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

SHA256_CHARS = frozenset("0123456789abcdef")
ARTIFACT_ROLES = frozenset({"MODEL", "VOICE_BUNDLE", "TOKENIZER", "PHONEMIZER"})


class SeedValidationError(ValueError):
    pass


def _sha256(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in SHA256_CHARS for c in value):
        raise SeedValidationError("expected lowercase 64-character sha256")
    return value


def _validate_url(url: object, allowed_hosts: frozenset[str]) -> str:
    if not isinstance(url, str):
        raise SeedValidationError("URL must be a string")
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise SeedValidationError("only HTTPS without credentials/custom ports allowed")
    host = (p.hostname or "").lower()
    if host not in allowed_hosts or p.fragment:
        raise SeedValidationError("upstream host not approved")
    if not p.path or p.path == "/":
        raise SeedValidationError("upstream path is missing")
    return url


def validate_seed(seed: dict, *, allowed_hosts: frozenset[str], max_size: int = 4_000_000_000) -> dict:
    if type(seed) is not dict or seed.get("schemaVersion") != 1:
        raise SeedValidationError("unsupported seed schema")
    if seed.get("engineCode") not in {"KOKORO", "QWEN3", "PIPER"}:
        raise SeedValidationError("unknown engine")
    if not isinstance(seed.get("seedRevision"), str) or not seed["seedRevision"]:
        raise SeedValidationError("seed revision required")
    models = seed.get("models")
    if not isinstance(models, list) or not models:
        raise SeedValidationError("approved seed must contain models")
    approved = []
    for model in models:
        if type(model) is not dict or not model.get("commercialApproved"):
            raise SeedValidationError("model is not approved for commercial distribution")
        if model.get("engineCode") != seed["engineCode"]:
            raise SeedValidationError("model engine mismatch")
        if not all(isinstance(model.get(k), str) and model[k] for k in ("modelId", "modelRevision", "licenseSpdx")):
            raise SeedValidationError("model identity and license required")
        assets = model.get("artifacts")
        if not isinstance(assets, list) or not any(a.get("role") == "MODEL" for a in assets if isinstance(a, dict)):
            raise SeedValidationError("model artifact required")
        checked_assets = []
        roles = set()
        for a in assets:
            if not isinstance(a, dict) or a.get("role") not in ARTIFACT_ROLES:
                raise SeedValidationError("invalid artifact role")
            key = (a.get("role"), a.get("assetId"))
            if key in roles:
                raise SeedValidationError("duplicate artifact")
            roles.add(key)
            n = a.get("sizeBytes")
            if type(n) is not int or n <= 0 or n > max_size:
                raise SeedValidationError("asset size invalid")
            checked_assets.append({
                "role": a["role"], "assetId": str(a["assetId"]),
                "upstreamUrl": _validate_url(a.get("upstreamUrl"), allowed_hosts),
                "sha256": _sha256(a.get("sha256")), "sizeBytes": n,
                "licenseSpdx": a.get("licenseSpdx") or model["licenseSpdx"]
            })
        approved.append({
            "modelId": model["modelId"], "modelRevision": model["modelRevision"],
            "engineCode": model["engineCode"], "licenseSpdx": model["licenseSpdx"],
            "artifacts": checked_assets, "voices": model.get("voices", []),
        })
    return {"schemaVersion": 1, "seedRevision": seed["seedRevision"],
            "engineCode": seed["engineCode"], "models": approved}


def build_candidate(validated_seed: dict, *, verified_object_keys: dict[str, str]) -> dict:
    """Build a deterministic LOCAL_STAGED proposal; caller must have verified bytes.

    `verified_object_keys` maps sha256 to content-addressed RustFS keys after upload.
    Missing keys are allowed while local staging, but such candidate cannot be proposed.
    """
    models = []
    for m in validated_seed["models"]:
        assets = []
        for a in m["artifacts"]:
            item = {k: a[k] for k in ("role", "assetId", "sha256", "sizeBytes", "licenseSpdx")}
            item["rustfsKey"] = verified_object_keys.get(a["sha256"])
            assets.append(item)
        models.append({k: m[k] for k in ("modelId", "modelRevision", "engineCode", "licenseSpdx", "voices")} | {"artifacts": assets})
    base = {"schemaVersion": 1, "seedRevision": validated_seed["seedRevision"],
            "engineCode": validated_seed["engineCode"], "models": models}
    canonical = json.dumps(base, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base | {"candidateDigest": hashlib.sha256(canonical).hexdigest(),
                   "state": "ASSETS_UPLOADED" if all(a["rustfsKey"] for m in models for a in m["artifacts"]) else "LOCAL_STAGED"}


def should_bootstrap(http_status: int | None, error_code: str | None) -> bool:
    """An explicit missing-manifest response is required; outages must fail closed."""
    return http_status in (404, 409) and error_code == "NO_MANIFEST"
