"""Provision isolated Amiga TTL rules in an S3/RustFS bucket without overwriting others.

Requires bucket lifecycle administrator credentials through standard AWS_* env vars.
Use only on test bucket first; check RustFS version's lifecycle behavior.
"""
import argparse
import json
import os

from retention import lifecycle_configuration


def merge_lifecycle(existing, incoming):
    incoming_ids = {r["ID"] for r in incoming}
    return [r for r in existing if r["ID"] not in incoming_ids] + incoming


def configure(s3, bucket, prefix, dry_run=True):
    try:
        existing = s3.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", [])
    except Exception as exc:
        from botocore.exceptions import ClientError
        if not isinstance(exc, ClientError) or exc.response.get("Error", {}).get("Code") not in ("NoSuchLifecycleConfiguration", "NoSuchConfiguration"):
            raise
        existing = []
    new = lifecycle_configuration(prefix)["Rules"]
    merged = merge_lifecycle(existing, new)
    if not dry_run:
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket, LifecycleConfiguration={"Rules": merged})
        verification = s3.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", [])
        for rule in new:
            if rule not in verification:
                raise RuntimeError("bucket lifecycle verification failed")
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="tts")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--apply", action="store_true",
                        help="Write bucket lifecycle; default is read-only dry-run")
    args = parser.parse_args()
    import boto3
    from botocore.config import Config
    s3 = boto3.client("s3", endpoint_url=args.endpoint, region_name=args.region,
                      config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    rules = configure(s3, args.bucket, args.prefix, dry_run=not args.apply)
    print(json.dumps({"applied": args.apply, "bucket": args.bucket, "rules": rules}, indent=2))


if __name__ == "__main__":
    main()
