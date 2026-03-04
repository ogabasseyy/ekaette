"""S3 media storage adapter."""

from __future__ import annotations

import os
from typing import Any
import uuid

import boto3


class S3MediaStore:
    """Upload/download helper for media artifacts."""

    def __init__(self, bucket: str | None = None, *, region: str | None = None) -> None:
        self.bucket = bucket or os.getenv("S3_MEDIA_BUCKET", "")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._client = boto3.client("s3", region_name=self.region)

    async def upload_bytes(
        self,
        *,
        data: bytes,
        mime_type: str,
        user_id: str,
        session_id: str,
        key_prefix: str = "uploads",
    ) -> dict[str, Any]:
        if not self.bucket:
            return {"error": "S3_MEDIA_BUCKET not configured"}
        object_key = f"{key_prefix}/{user_id}/{session_id}/{uuid.uuid4().hex}"
        self._client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=data,
            ContentType=mime_type,
        )
        return {
            "s3_uri": f"s3://{self.bucket}/{object_key}",
            "bucket": self.bucket,
            "key": object_key,
        }

