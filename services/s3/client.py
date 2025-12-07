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

