"""
Скачать фото квартир по URL из БД и залить в S3 (создаётся запись в photo_embeddings).
Внутри пачки между фото — 0.5 с (в сервисе). Между пачками — --pause сек (по умолчанию 60 при --all).
Запуск из корня:
  python3 scripts/ingest/download_photos.py --all --batch 100           — пачки по 100, пауза 1 мин между пачками
  python3 scripts/ingest/download_photos.py --all --batch 100 --pause 120
"""
import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.session import async_session
from services.image_downloader.service import ImageDownloaderService

async def main():
    parser = argparse.ArgumentParser(description="Download flat photos to S3")
    parser.add_argument("--all", action="store_true", help="Process all remaining photos in a loop")
    parser.add_argument("--batch", type=int, default=100, help="Batch size (default 100)")
    parser.add_argument("--pause", type=int, default=60, help="Seconds to wait between batches when --all (default 60)")
    args = parser.parse_args()

    async with async_session() as session:
        downloader = ImageDownloaderService(session)
        total = 0
        while True:
            n = await downloader.process_batch(limit=args.batch)
            total += n
            if n == 0:
                break
            print(f"Processed {n} photos (total this run: {total})")
            if not args.all:
                break
            print(f"Pausing {args.pause}s before next batch...")
            await asyncio.sleep(args.pause)
        print("Done." if total else "No photos left to download.")

if __name__ == "__main__":
    asyncio.run(main())
