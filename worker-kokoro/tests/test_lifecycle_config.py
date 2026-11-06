import unittest
from unittest.mock import Mock

from configure_lifecycle import configure, merge_lifecycle


class LifecycleProvisionTests(unittest.TestCase):
    def test_preserves_unrelated_rules_and_replaces_our_rules(self):
        unrelated = {"ID": "other-team", "Status": "Enabled",
                     "Filter": {"Prefix": "other/"}, "Expiration": {"Days": 365}}
        old = {"ID": "amiga-7d", "Status": "Disabled",
               "Filter": {"Prefix": "bad/"}, "Expiration": {"Days": 99}}
        s3 = Mock()
        s3.get_bucket_lifecycle_configuration.side_effect = [
            {"Rules": [unrelated, old]},
            {"Rules": [unrelated] + [
                {"ID": "amiga-" + str(d) + "d", "Status": "Enabled",
                 "Filter": {"Prefix": "tts/ttl-" + str(d) + "d/"},
                 "Expiration": {"Days": d}} for d in (1, 7, 30, 90)]
            }]
        merged = configure(s3, "audio", "tts", dry_run=False)
        self.assertEqual(5, len(merged))
        self.assertEqual(1, sum(r["ID"] == "amiga-7d" for r in merged))
        self.assertIn(unrelated, merged)
        args = s3.put_bucket_lifecycle_configuration.call_args.kwargs
        self.assertEqual("audio", args["Bucket"])
        self.assertEqual(merged, args["LifecycleConfiguration"]["Rules"])

    def test_dry_run_does_not_write(self):
        s3 = Mock()
        s3.get_bucket_lifecycle_configuration.return_value = {"Rules": []}
        merged = configure(s3, "audio", "tts", dry_run=True)
        self.assertEqual(4, len(merged))
        s3.put_bucket_lifecycle_configuration.assert_not_called()

    def test_merge_idempotent(self):
        incoming = [{"ID": "amiga-1d"}, {"ID": "amiga-7d"}]
        once = merge_lifecycle([], incoming)
        self.assertEqual(once, merge_lifecycle(once, incoming))


if __name__ == "__main__":
    unittest.main()
