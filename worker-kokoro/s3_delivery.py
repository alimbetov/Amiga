"""Idempotent S3/RustFS object delivery after crash/uncertain PUT.

Reuses a correctly verified existing object; refuses to overwrite an object
whose identity disagrees. Metadata SHA is uploader-declared, not independent
remote byte rehash; full GET validation belongs to integration acceptance.
"""
from __future__ import annotations

class RemoteObjectCollision(RuntimeError):
    pass

class RemoteIntegrityError(RuntimeError):
    pass


def _head_or_none(client, bucket, key):
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        response = getattr(exc, "response", {})
        if isinstance(response, dict) and str(
                response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def _matching(head, size, digest):
    return (head.get("ContentLength") == size
            and head.get("Metadata", {}).get("sha256") == digest)


def ensure_uploaded(client, bucket, key, path, digest, content_type):
    """Return True if uploaded, False if an existing verified object was reused."""
    size = path.stat().st_size
    existing = _head_or_none(client, bucket, key)
    if existing is not None:
        if not _matching(existing, size, digest):
            raise RemoteObjectCollision("existing RustFS object has different identity")
        return False
    with path.open("rb") as stream:
        client.put_object(Bucket=bucket, Key=key, Body=stream,
                          ContentLength=size, ContentType=content_type,
                          Metadata={"sha256": digest})
    confirmed = client.head_object(Bucket=bucket, Key=key)
    if not _matching(confirmed, size, digest):
        raise RemoteIntegrityError("RustFS HEAD disagrees with uploaded object")
    return True
