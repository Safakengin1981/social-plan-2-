"""Tenant-isolated S3-compatible durable media storage.

No local filesystem fallback. Configure a private bucket with durable storage.
Provider credentials are read only from environment variables.
"""
import hashlib
import os
import re
import secrets

MAX_BYTES = 25 * 1024 * 1024
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
         "video/mp4": ".mp4"}
KEY_RE = re.compile(r"^tenants/([0-9]+)/([a-f0-9]{32})\.(jpg|png|webp|mp4)$")


def client():
    import boto3
    required = ("SOCIALPLAN_S3_ENDPOINT", "SOCIALPLAN_S3_BUCKET",
                "SOCIALPLAN_S3_ACCESS_KEY", "SOCIALPLAN_S3_SECRET_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Durable media storage not configured: " + ", ".join(missing))
    return boto3.client(
        "s3", endpoint_url=os.environ["SOCIALPLAN_S3_ENDPOINT"],
        aws_access_key_id=os.environ["SOCIALPLAN_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["SOCIALPLAN_S3_SECRET_KEY"],
        region_name=os.getenv("SOCIALPLAN_S3_REGION", "auto"),
    )


def bucket():
    return os.environ["SOCIALPLAN_S3_BUCKET"]


def save(business_id, data, content_type):
    if not isinstance(business_id, int) or business_id <= 0:
        raise ValueError("Invalid business ID")
    if content_type not in TYPES:
        raise ValueError("Unsupported media type")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Media must be between 1 byte and 25 MB")
    key = f"tenants/{business_id}/{secrets.token_hex(16)}{TYPES[content_type]}"
    # Private by default; do not set public-read ACL or expose raw bucket URLs.
    client().put_object(
        Bucket=bucket(), Key=key, Body=data, ContentType=content_type,
        Metadata={"sha256": hashlib.sha256(data).hexdigest()},
    )
    return key


def read(business_id, key):
    match = KEY_RE.fullmatch(key)
    if not match or int(match.group(1)) != business_id:
        raise PermissionError("Media does not belong to this business")
    obj = client().get_object(Bucket=bucket(), Key=key)
    content_type = obj.get("ContentType", "application/octet-stream")
    # Never trust a remote ContentLength alone: enforce a hard bound on reads.
    if int(obj.get("ContentLength", 0)) > MAX_BYTES:
        raise ValueError("Stored media exceeds the configured limit")
    data = obj["Body"].read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Stored media exceeds the configured limit")
    checksum = obj.get("Metadata", {}).get("sha256")
    if not checksum or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Stored media checksum missing or invalid")
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Stored media checksum mismatch")
    return data, content_type
