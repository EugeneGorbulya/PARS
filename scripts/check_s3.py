import asyncio
import sys
import os
import aioboto3

# Add project root to python path
sys.path.append(os.getcwd())

from core.config import settings

async def main():
    session = aioboto3.Session()
    config = {
        "service_name": "s3",
        "endpoint_url": settings.S3_ENDPOINT,
        "aws_access_key_id": settings.S3_ACCESS_KEY,
        "aws_secret_access_key": settings.S3_SECRET_KEY,
    }
    bucket_name = settings.S3_BUCKET

    print(f"Checking bucket: {bucket_name} at {settings.S3_ENDPOINT}")

    async with session.client(**config) as client:
        # 1. List objects
        try:
            response = await client.list_objects_v2(Bucket=bucket_name, MaxKeys=5)
        except Exception as e:
            print(f"Error listing objects: {e}")
            return

        if 'Contents' not in response:
            print("Bucket is empty!")
            return

        print("\n--- First 5 files in S3 ---")
        for obj in response['Contents']:
            print(f"- {obj['Key']} (Size: {obj['Size']} bytes)")

        # 2. Download one file
        first_key = response['Contents'][0]['Key']
        print(f"\nDownloading {first_key} to 'test_download.jpg'...")
        
        try:
            obj = await client.get_object(Bucket=bucket_name, Key=first_key)
            data = await obj['Body'].read()
            
            with open("test_download.jpg", "wb") as f:
                f.write(data)
            
            print("Success! File saved to test_download.jpg")
        except Exception as e:
            print(f"Error downloading file: {e}")

if __name__ == "__main__":
    asyncio.run(main())

