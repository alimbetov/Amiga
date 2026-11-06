import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from standalone_models import (
    ModelInstallError, S3ModelMirror, install_models, validate_manifest
)


class Missing(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class MemoryS3:
    def __init__(self):
        self.data = {}
        self.uploads = 0

    def head_object(self, Bucket, Key):
        if Key not in self.data:
            raise Missing()
        raw, metadata = self.data[Key]
        return {"ContentLength": len(raw), "Metadata": metadata}

    def download_fileobj(self, Bucket, Key, Fileobj):
        Fileobj.write(self.data[Key][0])

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs):
        self.data[Key] = (Fileobj.read(), ExtraArgs["Metadata"])
        self.uploads += 1


def descriptor(raw=b"approved-model"):
    return {"engine": "piper", "fileName": "voice.onnx",
            "url": "https://models.example.test/voice.onnx",
            "sha256": hashlib.sha256(raw).hexdigest(), "sizeBytes": len(raw)}


class FakeResponse:
    status = 200
    def __init__(self, raw):
        self.raw = io.BytesIO(raw)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, n):
        return self.raw.read(n)


class FakeOpener:
    def __init__(self, raw):
        self.raw = raw
        self.calls = 0
    def open(self, request, timeout):
        self.calls += 1
        return FakeResponse(self.raw)


class StandaloneModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = b"approved-model"
        self.asset = descriptor(self.raw)
        self.manifest = {"schemaVersion": 1, "assets": [self.asset]}
        self.hosts = {"models.example.test"}
        self.storage = MemoryS3()
        self.mirror = S3ModelMirror(self.storage, "models")

    def tearDown(self):
        self.temp.cleanup()

    def test_download_once_and_mirror_then_offline_restore(self):
        opener = FakeOpener(self.raw)
        report = install_models(self.manifest, self.root, self.hosts,
                                mirror=self.mirror, opener=opener)
        self.assertEqual(1, report["downloaded"])
        self.assertEqual(1, report["mirrored"])
        self.assertEqual(1, self.storage.uploads)
        (self.root / "piper" / "voice.onnx").unlink()
        report = install_models(self.manifest, self.root, self.hosts,
                                mirror=self.mirror, offline=True)
        self.assertEqual(1, report["restored"])
        self.assertEqual(self.raw, (self.root / "piper" / "voice.onnx").read_bytes())
        self.assertEqual(1, opener.calls)

    def test_local_verified_cache_requires_no_network(self):
        target = self.root / "piper" / "voice.onnx"
        target.parent.mkdir()
        target.write_bytes(self.raw)
        report = install_models(self.manifest, self.root, self.hosts, offline=True)
        self.assertEqual(1, report["cached"])

    def test_corrupt_remote_bytes_rejected_even_when_head_matches(self):
        key = self.mirror.key(self.asset)
        self.storage.data[key] = (b"broken--bytes", {"sha256": self.asset["sha256"]})
        # Keep length equal to avoid short-circuiting on HEAD size.
        self.storage.data[key] = (b"invalid bytes!", {"sha256": self.asset["sha256"]})
        with self.assertRaises(ModelInstallError):
            install_models(self.manifest, self.root, self.hosts,
                           mirror=self.mirror, offline=True, mirror_required=True)
        self.assertFalse((self.root / "piper" / "voice.onnx").exists())

    def test_no_overwrite_on_conflicting_mirror(self):
        self.storage.data[self.mirror.key(self.asset)] = (b"other", {"sha256": "bad"})
        with self.assertRaises(ModelInstallError):
            self.mirror.backup(self.asset, self.root / "no-file.onnx")
        self.assertEqual(0, self.storage.uploads)

    def test_manifest_refuses_http_credentials_and_traversal(self):
        for replacement in (
            {**self.asset, "url": "http://models.example.test/voice.onnx"},
            {**self.asset, "url": "https://other.example.test/voice.onnx"},
            {**self.asset, "fileName": "../escape.onnx"},
            {**self.asset, "sizeBytes": True},
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaises(ModelInstallError):
                    validate_manifest({"schemaVersion": 1, "assets": [replacement]}, self.hosts)

    def test_optional_mirror_outage_does_not_block_approved_origin(self):
        class BadMirror:
            def restore(self, *args):
                raise ModelInstallError("mirror down")
            def backup(self, *args):
                raise ModelInstallError("mirror down")
        report = install_models(self.manifest, self.root, self.hosts,
                                mirror=BadMirror(), opener=FakeOpener(self.raw))
        self.assertEqual(1, report["downloaded"])
        self.assertGreaterEqual(report["mirrorErrors"], 1)


if __name__ == "__main__":
    unittest.main()
