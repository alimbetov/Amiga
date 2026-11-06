import copy
import unittest
from amiga_worker.bootstrap import SeedValidationError, validate_seed, build_candidate, should_bootstrap

SEED = {
    "schemaVersion": 1, "seedRevision": "pinned-v1", "engineCode": "KOKORO",
    "models": [{
        "engineCode": "KOKORO", "modelId": "kokoro-82m", "modelRevision": "1",
        "licenseSpdx": "Apache-2.0", "commercialApproved": True,
        "artifacts": [{"assetId": "model", "role": "MODEL",
                       "sha256": "a"*64, "sizeBytes": 100,
                       "upstreamUrl": "https://huggingface.co/owner/repo/resolve/pinned/model.onnx"}],
        "voices": [{"voiceCode": "af_heart", "locale": "en-US"}]
    }]
}
HOSTS = frozenset({"huggingface.co"})


class BootstrapTests(unittest.TestCase):
    def test_no_manifest_only(self):
        self.assertTrue(should_bootstrap(404, "NO_MANIFEST"))
        for status, code in ((401,"NO_MANIFEST"),(403,"NO_MANIFEST"),
                             (500,"NO_MANIFEST"),(404,None),(None,"NO_MANIFEST")):
            self.assertFalse(should_bootstrap(status, code))

    def test_seed_candidate_not_authoritative(self):
        seed = validate_seed(SEED, allowed_hosts=HOSTS)
        local = build_candidate(seed, verified_object_keys={})
        uploaded = build_candidate(seed, verified_object_keys={"a"*64:"models/sha256/aa"})
        self.assertEqual("LOCAL_STAGED", local["state"])
        self.assertEqual("ASSETS_UPLOADED", uploaded["state"])
        self.assertNotIn("manifestVersion", uploaded)
        self.assertEqual(uploaded["candidateDigest"], build_candidate(seed, verified_object_keys={"a"*64:"models/sha256/aa"})["candidateDigest"])

    def test_unapproved_model_rejected(self):
        x = copy.deepcopy(SEED)
        x["models"][0]["commercialApproved"] = False
        with self.assertRaises(SeedValidationError):
            validate_seed(x, allowed_hosts=HOSTS)

    def test_checksum_and_size_rejected(self):
        for field, value in (("sha256","bad"),("sizeBytes",0),("sizeBytes",5_000_000_000)):
            x = copy.deepcopy(SEED)
            x["models"][0]["artifacts"][0][field] = value
            with self.assertRaises(SeedValidationError):
                validate_seed(x, allowed_hosts=HOSTS)

    def test_forbid_untrusted_upstream_and_credentials(self):
        for url in ("http://huggingface.co/model", "https://evil.example/model",
                    "https://user:pass@huggingface.co/model",
                    "https://huggingface.co:8443/model"):
            x = copy.deepcopy(SEED)
            x["models"][0]["artifacts"][0]["upstreamUrl"] = url
            with self.assertRaises(SeedValidationError):
                validate_seed(x, allowed_hosts=HOSTS)

    def test_empty_seed_is_not_automatic_discovery(self):
        with self.assertRaises(SeedValidationError):
            validate_seed({"schemaVersion":1,"seedRevision":"v1",
                           "engineCode":"KOKORO","models":[]},allowed_hosts=HOSTS)


if __name__ == "__main__":
    unittest.main()
