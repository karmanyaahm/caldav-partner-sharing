"""Publishing to Cloudflare R2 over its S3-compatible API."""

from __future__ import annotations

import boto3
from botocore.config import Config as BotoConfig

CONTENT_TYPE = "text/calendar; charset=utf-8"

# A long cache is a revocation hole: the edge keeps serving a deleted object
# until it expires. Five minutes is short enough that revocation is prompt and
# long enough that a scraped token cannot hammer the origin.
CACHE_CONTROL = "public, max-age=300"

PREFIX = "f/"


def client(config):
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.r2_access_key_id,
        aws_secret_access_key=config.r2_secret_access_key,
        region_name="auto",
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
    )


def key_for(token: str) -> str:
    return f"{PREFIX}{token}.ics"


def put(s3, bucket: str, token: str, body: bytes) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key_for(token),
        Body=body,
        ContentType=CONTENT_TYPE,
        CacheControl=CACHE_CONTROL,
    )


def delete(s3, bucket: str, token: str) -> None:
    s3.delete_object(Bucket=bucket, Key=key_for(token))


def list_keys(s3, bucket: str) -> list[str]:
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": PREFIX}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")
