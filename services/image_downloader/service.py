import asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models import FlatPhoto, PhotoEmbedding
from services.s3.client import S3Client

class ImageDownloaderService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.s3 = S3Client()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/118.0.0.0 Safari/537.36",
            "Referer": "https://cian.ru/"
        }

    async def run_worker(self, batch_size: int = 10, sleep_time: int = 60):
        """
        Infinite loop worker that downloads photos in batches.
        """
        print("Starting Image Downloader Worker...")
        await self.s3.ensure_bucket_exists()

        while True:
            try:
                count = await self.process_batch(batch_size)
                if count == 0:
                    print("No photos to download. Sleeping...")
                    await asyncio.sleep(sleep_time)
                else:
                    print(f"Batch finished. Sleeping for {sleep_time}s...")
                    await asyncio.sleep(sleep_time)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 429):
                    print(f"🚨 BLOCKED! (Status {e.response.status_code}). Sleeping for 1 HOUR...")
                    await asyncio.sleep(3600)
                else:
                    print(f"Worker HTTP error: {e}")
                    await asyncio.sleep(sleep_time)
            except Exception as e:
                print(f"Worker unexpected error: {e}")
                await asyncio.sleep(sleep_time)

    async def process_photos_for_flat_ids(self, flat_ids: list, max_photos_per_flat: int = 10) -> int:
        """
        Скачивает фото только для указанных квартир (для каждой — до max_photos_per_flat первых без эмбеддинга).
        Возвращает количество обработанных фото.
        """
        if not flat_ids:
            return 0
        stmt = (
            select(FlatPhoto)
            .outerjoin(PhotoEmbedding, FlatPhoto.id == PhotoEmbedding.photo_id)
            .where(PhotoEmbedding.id == None)
            .where(FlatPhoto.flat_id.in_(flat_ids))
        )
        result = await self.session.execute(stmt)
        photos = result.scalars().all()
        # Ограничиваем по квартирам: не больше max_photos_per_flat с одной квартиры
        by_flat: dict = {}
        for p in photos:
            by_flat.setdefault(p.flat_id, []).append(p)
        to_process = []
        for fid in flat_ids:
            to_process.extend(by_flat.get(fid, [])[:max_photos_per_flat])
        if not to_process:
            return 0
        async with httpx.AsyncClient(timeout=15.0, headers=self.headers, follow_redirects=True) as http_client:
            for photo in to_process:
                try:
                    await self._process_photo(http_client, photo)
                    await asyncio.sleep(0.5)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (403, 429):
                        raise e
                    await self._mark_as_failed(photo.id, str(e))
                except Exception as e:
                    await self._mark_as_failed(photo.id, str(e))
        return len(to_process)

    async def process_batch(self, limit: int) -> int:
        # Find photos without embeddings (order for deterministic batches)
        stmt = (
            select(FlatPhoto)
            .outerjoin(PhotoEmbedding, FlatPhoto.id == PhotoEmbedding.photo_id)
            .where(PhotoEmbedding.id == None)
            .order_by(FlatPhoto.id)
            .limit(limit)
        )
        
        result = await self.session.execute(stmt)
        photos = result.scalars().all()

        if not photos:
            return 0
        
        async with httpx.AsyncClient(timeout=15.0, headers=self.headers, follow_redirects=True) as http_client:
            for photo in photos:
                try:
                    await self._process_photo(http_client, photo)
                    # Small delay between requests within batch to be polite
                    await asyncio.sleep(0.5) 
                except httpx.HTTPStatusError as e:
                    print(f"HTTP Error processing photo {photo.id} ({photo.url}): {e}")
                    
                    # If blocked, re-raise to outer loop to trigger long sleep
                    if e.response.status_code in (403, 429):
                        raise e 
                    
                    # For other errors (404, 500), mark as failed and continue
                    await self._mark_as_failed(photo.id, str(e))
                except Exception as e:
                    print(f"Error processing photo {photo.id} ({photo.url}): {e}")
                    # Mark as failed to prevent infinite retry loop
                    await self._mark_as_failed(photo.id, str(e))
        
        return len(photos)

    async def _process_photo(self, http_client: httpx.AsyncClient, photo: FlatPhoto):
        # 1. Download
        response = await http_client.get(photo.url)
        response.raise_for_status()
        content = response.content
        
        # 2. Upload to S3
        object_name = f"flats/{photo.flat_id}/{photo.id}.jpg"
        s3_uri = await self.s3.upload_file(content, object_name)
        
        # 3. Create record in PhotoEmbedding
        embedding_record = PhotoEmbedding(
            photo_id=photo.id,
            flat_id=photo.flat_id,
            storage_uri=s3_uri,
            model="raw_image", 
            dim=0
        )
        self.session.add(embedding_record)
        await self.session.commit()
        print(f"Downloaded: {s3_uri}")

    async def _mark_as_failed(self, photo_id: int, error: str):
        # Create a dummy record with error info so we don't try again
        # We use 'FAILED' as model name
        embedding_record = PhotoEmbedding(
            photo_id=photo_id,
            storage_uri=f"error://{error[:200]}", # Store part of error
            model="FAILED", 
            dim=0
        )
        self.session.add(embedding_record)
        await self.session.commit()
