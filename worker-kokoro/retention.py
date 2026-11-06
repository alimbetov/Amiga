"""TTL policy for local audio and RustFS/S3 objects.

S3 TTL uses pre-provisioned bucket lifecycle rules based on an isolated key prefix.
Deletion is asynchronous and controlled by RustFS, not by this HTTP process.
"""
from __future__ import annotations

import time

# Only whole-day TTLs can be enforced by the S3 lifecycle rules used here.
TTL_DAYS = (1, 7, 30, 90)
DAY = 86400


class RetentionError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_retention(raw):
    """Return (ttl_seconds or None, lifecycle path component)."""
    if raw is None:
        return None, "permanent"
    if not isinstance(raw, dict):
        raise RetentionError("INVALID_RETENTION")
    if raw.get("mode") == "PERMANENT":
        if set(raw) != {"mode"}:
            raise RetentionError("INVALID_RETENTION")
        return None, "permanent"
    if raw.get("mode") not in (None, "TTL") or set(raw) - {"mode", "ttlSeconds"}:
        raise RetentionError("INVALID_RETENTION")
    seconds = raw.get("ttlSeconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds not in (d * DAY for d in TTL_DAYS):
        raise RetentionError("UNSUPPORTED_TTL")
    return seconds, "ttl-" + str(seconds // DAY) + "d"


def expires_at(ttl_seconds, now=None):
    if ttl_seconds is None:
        return None
    return int(time.time() if now is None else now) + ttl_seconds


def lifecycle_configuration(base_prefix="tts"):
    """Provision by bucket admin. Never overwrite unrelated lifecycle rules automatically."""
    prefix = base_prefix.strip("/")
    if not prefix or ".." in prefix or not all(c.isalnum() or c in "-_/" for c in prefix):
        raise RetentionError("INVALID_PREFIX")
    return {"Rules": [
        {"ID": "amiga-" + str(days) + "d",
         "Status": "Enabled",
         "Filter": {"Prefix": prefix + "/ttl-" + str(days) + "d/"},
         "Expiration": {"Days": days}}
        for days in TTL_DAYS
    ]}
