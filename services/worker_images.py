import asyncio
import sys
import os

# Add project root to python path
sys.path.append(os.getcwd())

from core.session import async_session
from services.image_downloader.service import ImageDownloaderService

async def main():
    async with async_session() as session:
        worker = ImageDownloaderService(session)
        # Batch size 10, sleep 60s
        await worker.run_worker(batch_size=10, sleep_time=60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Worker stopped by user.")

