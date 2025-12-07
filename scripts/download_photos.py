import asyncio
import sys
import os

# Add project root to python path
sys.path.append(os.getcwd())

from core.session import async_session
from services.image_downloader.service import ImageDownloaderService

async def main():
    async with async_session() as session:
        downloader = ImageDownloaderService(session)
        
        # Download batch of 50 photos
        print("Downloading batch of 50 photos...")
        await downloader.process_batch(limit=50)
        print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
