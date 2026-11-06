"""Portable pinned-model bootstrap with optional RustFS backup.

Source order: verified local -> verified S3 mirror -> approved HTTPS origin.
A model is only activated after exact size and SHA-256 validation.
No Backend is required. S3 credentials are never stored in SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
MAX_BYTES = 4_000_000_000
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,149}$")
SHA = re.compile(r"^[a-f0-9]{64}$")


class ModelInstallError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ModelInstallError("upstream redirect refused")


def validate_manifest(manifest: dict, allowed_hosts: set[str]) -> list[dict]:
    if type(manifest) is not dict or set(manifest) != {"schemaVersion", "assets"} or manifest.get("schemaVersion") != 1:
        raise ModelInstallError("invalid manifest schema")
    assets = manifest["assets"]
    if not isinstance(assets, list) or not assets:
        raise ModelInstallError("missing model assets")
    ids = set()
    for item in assets:
        if type(item) is not dict or set(item) != {"engine", "fileName", "url", "sha256", "sizeBytes"}:
            raise ModelInstallError("invalid asset fields")
        engine = item["engine"]
        name = item["fileName"]
        digest = item["sha256"]
        size = item["sizeBytes"]
        if engine not in ("piper", "kokoro") or not isinstance(name, str) or not NAME.fullmatch(name):
            raise ModelInstallError("invalid engine or model fileName")
        if type(digest) is not str or not SHA.fullmatch(digest):
            raise ModelInstallError("invalid SHA-256")
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ModelInstallError("invalid asset size")
        if (engine, name) in ids:
            raise ModelInstallError("duplicate model asset")
        ids.add((engine, name))
        url = item["url"]
        if not isinstance(url, str):
            raise ModelInstallError("invalid URL")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443) or parsed.hostname not in allowed_hosts):
            raise ModelInstallError("upstream origin not allowed")
    return assets


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verified(path: Path, item: dict) -> bool:
    return (path.is_file() and not path.is_symlink()
            and path.stat().st_size == item["sizeBytes"]
            and file_sha256(path) == item["sha256"])


class S3ModelMirror:
    """Content-addressed, immutable-by-contract S3 mirror. No remote deletion."""

    def __init__(self, client, bucket: str, prefix: str = "tts-models/sha256"):
        if not isinstance(bucket, str) or not bucket:
            raise ValueError("bucket required")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def key(self, item):
        return f"{self.prefix}/{item['sha256']}"

    def restore(self, item: dict, destination: Path) -> bool:
        key = self.key(item)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise ModelInstallError("model mirror HEAD failed") from exc
        if head.get("ContentLength") != item["sizeBytes"] or head.get("Metadata", {}).get("sha256") != item["sha256"]:
            raise ModelInstallError("model mirror object identity mismatch")
        # Download and verify bytes; S3 HEAD metadata is not an integrity proof.
        with destination.open("wb") as output:
            self.client.download_fileobj(self.bucket, key, output)
            output.flush()
            os.fsync(output.fileno())
        if not verified(destination, item):
            destination.unlink(missing_ok=True)
            raise ModelInstallError("model mirror bytes failed SHA-256 verification")
        return True

    def backup(self, item: dict, path: Path) -> bool:
        key = self.key(item)
        if not verified(path, item):
            raise ModelInstallError("refusing to mirror unverified model")
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise ModelInstallError("model mirror HEAD failed") from exc
            head = None
        if head:
            if head.get("ContentLength") != item["sizeBytes"] or head.get("Metadata", {}).get("sha256") != item["sha256"]:
                raise ModelInstallError("refusing to overwrite conflicting mirrored object")
            return False
        with path.open("rb") as handle:
            self.client.upload_fileobj(
                handle, self.bucket, key,
                ExtraArgs={"Metadata": {"sha256": item["sha256"]},
                           "ContentType": "application/octet-stream"})
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get("ContentLength") != item["sizeBytes"] or head.get("Metadata", {}).get("sha256") != item["sha256"]:
            raise ModelInstallError("mirror upload verification failed")
        return True


def install_models(manifest: dict, root: Path, allowed_hosts: set[str], *,
                   mirror: S3ModelMirror | None = None, offline: bool = False,
                   opener=None, mirror_required: bool = False) -> dict:
    assets = validate_manifest(manifest, allowed_hosts)
    root = Path(root).resolve()
    opener = opener or urllib.request.build_opener(NoRedirect())
    summary = {"cached": 0, "restored": 0, "downloaded": 0, "mirrored": 0, "mirrorErrors": 0}
    for item in assets:
        directory = root / item["engine"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / item["fileName"]
        if target.is_symlink():
            raise ModelInstallError("model target is a symlink")
        if verified(target, item):
            summary["cached"] += 1
            # Best-effort mirror of cached models, useful for migration to new hosts.
            if mirror and not offline:
                try:
                    if mirror.backup(item, target):
                        summary["mirrored"] += 1
                except ModelInstallError:
                    if mirror_required:
                        raise
                    summary["mirrorErrors"] += 1
            continue
        temporary = directory / (item["fileName"] + ".partial")
        temporary.unlink(missing_ok=True)
        try:
            found = False
            if mirror:
                try:
                    found = mirror.restore(item, temporary)
                except ModelInstallError:
                    if mirror_required:
                        raise
                    summary["mirrorErrors"] += 1
            if found:
                summary["restored"] += 1
            elif offline:
                raise ModelInstallError("required model absent from local cache and mirror")
            else:
                request = urllib.request.Request(item["url"], method="GET")
                try:
                    with opener.open(request, timeout=60) as response, temporary.open("wb") as out:
                        if response.status != 200:
                            raise ModelInstallError("upstream model download failed")
                        count = 0
                        while True:
                            block = response.read(CHUNK)
                            if not block:
                                break
                            count += len(block)
                            if count > item["sizeBytes"]:
                                raise ModelInstallError("upstream model exceeds approved size")
                            out.write(block)
                        out.flush()
                        os.fsync(out.fileno())
                except (OSError, ValueError) as exc:
                    raise ModelInstallError("upstream model unavailable") from exc
                if not verified(temporary, item):
                    raise ModelInstallError("downloaded model failed integrity validation")
                summary["downloaded"] += 1
            os.replace(temporary, target)
            if mirror and not offline and not found:
                try:
                    if mirror.backup(item, target):
                        summary["mirrored"] += 1
                except ModelInstallError:
                    if mirror_required:
                        raise
                    summary["mirrorErrors"] += 1
        finally:
            temporary.unlink(missing_ok=True)
    return summary



def bootstrap_from_environment(model_root: Path) -> dict:
    """Invoked once before EngineSupervisor starts resident processes."""
    manifest_path = os.environ.get("TTS_MODEL_MANIFEST", "").strip()
    if not manifest_path:
        if os.environ.get("TTS_REQUIRE_MODEL_MANIFEST") == "1":
            raise ModelInstallError("TTS_MODEL_MANIFEST is required")
        return {"mode": "EXISTING_CACHE", "validated": False}
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    hosts = {name.strip().lower() for name in
             os.environ.get("TTS_MODEL_ALLOWED_HOSTS", "").split(",") if name.strip()}
    mirror = None
    bucket = os.environ.get("TTS_MODEL_MIRROR_BUCKET")
    if bucket:
        import boto3
        from botocore.config import Config
        mirror = S3ModelMirror(
            boto3.client(
                "s3", endpoint_url=os.environ.get("TTS_MODEL_MIRROR_ENDPOINT"),
                region_name=os.environ.get("TTS_MODEL_MIRROR_REGION", "us-east-1"),
                config=Config(s3={"addressing_style": "path"},
                              connect_timeout=5, read_timeout=30,
                              retries={"max_attempts": 2})),
            bucket, os.environ.get("TTS_MODEL_MIRROR_PREFIX", "tts-models/sha256"))
    if os.environ.get("TTS_MODEL_MIRROR_REQUIRED") == "1" and mirror is None:
        raise ModelInstallError("required RustFS mirror has no bucket configuration")
    summary = install_models(
        manifest, model_root, hosts, mirror=mirror,
        offline=os.environ.get("TTS_MODEL_OFFLINE") == "1",
        mirror_required=os.environ.get("TTS_MODEL_MIRROR_REQUIRED") == "1")
    return {"mode": "STANDALONE_PINNED", "validated": True, **summary}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--allowed-hosts", required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    mirror = None
    bucket = os.environ.get("TTS_MODEL_MIRROR_BUCKET")
    if bucket:
        import boto3
        from botocore.config import Config
        mirror = S3ModelMirror(
            boto3.client("s3", endpoint_url=os.environ.get("TTS_MODEL_MIRROR_ENDPOINT"),
                         region_name=os.environ.get("TTS_MODEL_MIRROR_REGION", "us-east-1"),
                         config=Config(s3={"addressing_style": "path"})),
            bucket, os.environ.get("TTS_MODEL_MIRROR_PREFIX", "tts-models/sha256"))
    print(json.dumps(install_models(
        manifest, args.model_root, set(args.allowed_hosts.split(",")),
        mirror=mirror, offline=args.offline,
        mirror_required=os.environ.get("TTS_MODEL_MIRROR_REQUIRED") == "1"), indent=2))


if __name__ == "__main__":
    main()
