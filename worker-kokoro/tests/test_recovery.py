import hashlib
import tempfile
import unittest
from pathlib import Path

from amiga_worker.db import WorkerStore
from amiga_worker.recovery import BootstrapCoordinator, BootstrapError, ArtifactCorrupt, AwaitingApproval
from amiga_worker.transport import HTTPTransport

DATA = b"model-artifact-pinned-for-test"
DIGEST = hashlib.sha256(DATA).hexdigest()
KEY = "models/sha256/" + DIGEST
SEED = {
    "schemaVersion": 1, "seedRevision": "test-seed-v1", "engineCode": "KOKORO",
    "models": [{
        "engineCode": "KOKORO", "modelId": "kokoro", "modelRevision": "1",
        "licenseSpdx": "Apache-2.0", "commercialApproved": True,
        "artifacts": [{"role": "MODEL", "assetId": "model-1",
                       "upstreamUrl": "https://huggingface.co/pinned/model.onnx",
                       "sha256": DIGEST, "sizeBytes": len(DATA)}],
        "voices": [{"voiceCode": "af_heart", "locale": "en-US"}]
    }]
}


class FakeTransport:
    def __init__(self):
        self.missing = True
        self.download_count = 0
        self.upload_count = 0
        self.proposal_count = 0
        self.remote = {}
        self.approve = True
        self.network_fail = False
        self.corrupt = False
        self.init_count = 0

    def init(self):
        self.init_count += 1
        if self.missing:
            return 404, {"code": "NO_MANIFEST"}
        return 200, {"schemaVersion": 1, "manifestId": "manifest-accepted",
                      "models": [{
                        "engineCode": "KOKORO", "modelId": "kokoro",
                        "modelRevision": "1",
                        "artifacts": [{"sha256": DIGEST, "rustfsKey": KEY}]
                      }]}

    def download(self, url, target, expected_size, allowed_hosts):
        self.download_count += 1
        if self.network_fail:
            raise BootstrapError("network interrupted")
        target.write(b"not-the-expected-content" if self.corrupt else DATA)

    def upload(self, asset, path):
        self.upload_count += 1
        self.remote[KEY] = path.read_bytes()
        return KEY

    def object_exists(self, key, asset):
        data = self.remote.get(key)
        if data is None:
            return False
        return len(data) == asset["sizeBytes"] and hashlib.sha256(data).hexdigest() == asset["sha256"]

    def propose(self, candidate, key):
        self.proposal_count += 1
        if not self.approve:
            return {"status": "PENDING_APPROVAL"}
        self.missing = False
        return {"status": "APPROVED"}

    def manifest_covers(self, manifest, candidate):
        return HTTPTransport.manifest_covers(None, manifest, candidate)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dbpath = self.root / "state.sqlite"
        self.store = WorkerStore(self.dbpath)
        self.remote = FakeTransport()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def coordinator(self):
        return BootstrapCoordinator(self.store, self.remote, self.root / "models",
                                    frozenset({"huggingface.co"}))

    def test_positive_fallback_to_approved_manifest(self):
        result = self.coordinator().run(SEED)
        self.assertEqual("MANIFEST_APPROVED", result["state"])
        self.assertEqual(1, self.remote.download_count)
        self.assertEqual(1, self.remote.upload_count)
        self.assertEqual(1, self.remote.proposal_count)
        self.assertEqual("APPROVED", self.coordinator().state.proposal(result["candidateDigest"])["status"])

    def test_restart_after_network_error(self):
        self.remote.network_fail = True
        with self.assertRaises(BootstrapError):
            self.coordinator().run(SEED)
        self.remote.network_fail = False
        self.store.close()
        self.store = WorkerStore(self.dbpath)
        result = self.coordinator().run(SEED)
        self.assertEqual("MANIFEST_APPROVED", result["state"])
        self.assertEqual(1, self.remote.upload_count)

    def test_corruption_rejected_then_retried(self):
        self.remote.corrupt = True
        with self.assertRaises(ArtifactCorrupt):
            self.coordinator().run(SEED)
        self.assertEqual(0, self.remote.upload_count)
        self.remote.corrupt = False
        self.assertEqual("MANIFEST_APPROVED", self.coordinator().run(SEED)["state"])

    def test_restart_after_upload_before_approval_avoids_duplicate_upload(self):
        self.remote.approve = False
        with self.assertRaises(AwaitingApproval):
            self.coordinator().run(SEED)
        self.assertEqual(1, self.remote.upload_count)
        self.store.close()
        self.store = WorkerStore(self.dbpath)
        self.remote.approve = True
        result = self.coordinator().run(SEED)
        self.assertEqual("MANIFEST_APPROVED", result["state"])
        self.assertEqual(1, self.remote.upload_count)

    def test_corrupt_cached_file_forces_redownload(self):
        self.remote.approve = False
        with self.assertRaises(AwaitingApproval):
            self.coordinator().run(SEED)
        (self.root / "models" / (DIGEST + ".bin")).write_bytes(b"corrupt")
        self.remote.approve = True
        self.coordinator().run(SEED)
        self.assertEqual(2, self.remote.download_count)

    def test_storage_missing_after_checkpoint_reuploads(self):
        self.remote.approve = False
        with self.assertRaises(AwaitingApproval):
            self.coordinator().run(SEED)
        self.remote.remote.clear()
        self.remote.approve = True
        self.coordinator().run(SEED)
        self.assertEqual(2, self.remote.upload_count)
        self.assertEqual(1, self.remote.download_count)

    def test_init_unauthorized_or_unavailable_never_falls_back(self):
        for status in (401, 403, 500, 503):
            self.remote.init = lambda: (status, {"code": "NO_MANIFEST"})
            with self.assertRaises(BootstrapError):
                self.coordinator().run(SEED)
        self.assertEqual(0, self.remote.download_count)

    def test_no_serving_while_pending(self):
        self.remote.approve = False
        with self.assertRaises(AwaitingApproval):
            self.coordinator().run(SEED)
        self.assertTrue(self.remote.missing)


if __name__ == "__main__":
    unittest.main()
