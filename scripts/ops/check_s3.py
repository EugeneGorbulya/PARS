"""
Проверка S3: список объектов (по умолчанию префикс flats/), опционально скачать несколько в папку для просмотра.
  python3 scripts/ops/check_s3.py              — список до 50 ключей в flats/
  python3 scripts/ops/check_s3.py --preview 5   — то же + скачать 5 фото в s3_preview/
"""
import argparse
import asyncio
import sys
import os
import aioboto3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import settings

async def main():
    parser = argparse.ArgumentParser(description="Check S3 bucket and optionally download preview")
    parser.add_argument("--preview", type=int, default=0, metavar="N", help="Download N photos to s3_preview/ folder")
    parser.add_argument("--prefix", type=str, default="flats/", help="List objects with this prefix (default: flats/)")
    parser.add_argument("--max-keys", type=int, default=50, help="Max keys to list (default 50)")
    args = parser.parse_args()

    session = aioboto3.Session()
    config = {
        "service_name": "s3",
        "endpoint_url": settings.S3_ENDPOINT,
        "aws_access_key_id": settings.S3_ACCESS_KEY,
        "aws_secret_access_key": settings.S3_SECRET_KEY,
    }
    bucket_name = settings.S3_BUCKET

    print(f"Bucket: {bucket_name} @ {settings.S3_ENDPOINT}")
    print(f"Prefix: {args.prefix!r}\n")

    async with session.client(**config) as client:
        try:
            response = await client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=args.prefix,
                MaxKeys=args.max_keys,
            )
        except Exception as e:
            print(f"Error listing objects: {e}")
            return

        if "Contents" not in response:
            print("No objects found (bucket empty or no match for prefix).")
            return

        items = response["Contents"]
        total = response.get("KeyCount", len(items))
        print(f"--- Objects (showing up to {args.max_keys}) ---")
        for obj in items:
            print(f"  {obj['Key']}  ({obj['Size']} bytes)")

        # Optional: download first N to s3_preview/
        if args.preview > 0 and items:
            preview_dir = "s3_preview"
            os.makedirs(preview_dir, exist_ok=True)
            to_download = items[: args.preview]
            for obj in to_download:
                key = obj["Key"]
                name = key.replace("/", "_")
                path = os.path.join(preview_dir, name)
                try:
                    resp = await client.get_object(Bucket=bucket_name, Key=key)
                    data = await resp["Body"].read()
                    with open(path, "wb") as f:
                        f.write(data)
                    print(f"  -> {path}")
                except Exception as e:
                    print(f"  -> {key}: error {e}")
            print(f"\nSaved {len(to_download)} file(s) to {preview_dir}/ — открой папку и посмотри фото.")

if __name__ == "__main__":
    asyncio.run(main())

