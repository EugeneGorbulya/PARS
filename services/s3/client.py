from __future__ import annotations

from contextlib import asynccontextmanager
import aioboto3
from core.config import settings

class S3Client:
    def __init__(self):
        self.session = aioboto3.Session()
        self.config = {
            "service_name": "s3",
            "endpoint_url": settings.S3_ENDPOINT,
            "aws_access_key_id": settings.S3_ACCESS_KEY,
            "aws_secret_access_key": settings.S3_SECRET_KEY,
        }
        self.bucket = settings.S3_BUCKET

    @asynccontextmanager
    async def get_client(self):
        async with self.session.client(**self.config) as client:
            yield client

    async def ensure_bucket_exists(self):
        async with self.get_client() as client:
            try:
                await client.head_bucket(Bucket=self.bucket)
            except client.exceptions.ClientError:
                # Bucket does not exist, create it
                await client.create_bucket(Bucket=self.bucket)

    async def upload_file(self, file_data: bytes, object_name: str, content_type: str = "image/jpeg"):
        async with self.get_client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=object_name,
                Body=file_data,
                ContentType=content_type
            )
        return f"s3://{self.bucket}/{object_name}"

    def _parse_s3_uri(self, uri: str) -> tuple[str, str]:
        if not uri.startswith("s3://"):
            raise ValueError(f"Expected s3:// URI, got: {uri[:40]}...")
        rest = uri[5:]
        if "/" not in rest:
            raise ValueError(f"Invalid S3 URI: {uri[:60]}...")
        bucket, key = rest.split("/", 1)
        return bucket, key

    async def download_bytes(self, *, bucket: str | None = None, key: str | None = None, s3_uri: str | None = None) -> bytes:
        if s3_uri is not None:
            b, k = self._parse_s3_uri(s3_uri)
        else:
            b, k = bucket or self.bucket, key or ""
            if not k:
                raise ValueError("download_bytes requires key= or s3_uri=")
        async with self.get_client() as client:
            resp = await client.get_object(Bucket=b, Key=k)
            body = resp["Body"]
            return await body.read()

    async def upload_bytes(
        self,
        file_data: bytes,
        object_name: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        async with self.get_client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=object_name,
                Body=file_data,
                ContentType=content_type,
            )
        return f"s3://{self.bucket}/{object_name}"

