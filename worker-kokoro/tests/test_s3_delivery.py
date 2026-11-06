import tempfile
import unittest
from pathlib import Path

from s3_delivery import ensure_uploaded, RemoteObjectCollision, RemoteIntegrityError


class NotFound(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.object = None
        self.uploads = 0

    def head_object(self, Bucket, Key):
        if self.object is None:
            raise NotFound()
        return self.object

    def put_object(self, Bucket, Key, Body, ContentLength, ContentType, Metadata):
        data = Body.read()
        self.uploads += 1
        self.object = {"ContentLength": len(data), "Metadata": Metadata}


class RustFSReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "output.mp3"
        self.path.write_bytes(b"ID3" + bytes(1000))
        self.sha = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def test_upload_once_then_replay_after_power_loss(self):
        client = FakeS3()
        self.assertTrue(ensure_uploaded(client, "audio", "tts/job.mp3",
                                        self.path, self.sha, "audio/mpeg"))
        self.assertFalse(ensure_uploaded(client, "audio", "tts/job.mp3",
                                         self.path, self.sha, "audio/mpeg"))
        self.assertEqual(1, client.uploads)

    def test_mismatch_must_not_overwrite_existing_object(self):
        client = FakeS3()
        client.object = {"ContentLength": 50, "Metadata": {"sha256": "wrong"}}
        with self.assertRaises(RemoteObjectCollision):
            ensure_uploaded(client, "audio", "tts/job.mp3",
                            self.path, self.sha, "audio/mpeg")
        self.assertEqual(0, client.uploads)

    def test_post_upload_head_mismatch_fails(self):
        class BadS3(FakeS3):
            def put_object(self, **kwargs):
                self.object = {"ContentLength": 2, "Metadata": {"sha256": "wrong"}}
        with self.assertRaises(RemoteIntegrityError):
            ensure_uploaded(BadS3(), "audio", "tts/job.mp3",
                            self.path, self.sha, "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
