"""
Compute CLIP image embeddings for photos that are already in S3 (photo_embeddings.model=raw_image)
and store rows in photo_clip_embeddings (idempotent per photo_id).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from core.session import async_session
from services.ml.clip_embedder import ClipImageEmbedder
from services.ml.clip_job import compute_clip_for_batch, fetch_photo_batch
from services.s3.client import S3Client


async def run_once(limit: int, dry_run: bool) -> int:
    s3 = S3Client()
    embedder = None if dry_run else ClipImageEmbedder.get()
    total = 0
    async with async_session() as session:
        items = await fetch_photo_batch(session, limit)
        if dry_run:
            print(f"dry-run: would process {len(items)} photos")
            return len(items)
        if not items:
            return 0
        n = await compute_clip_for_batch(session, s3, embedder, items)
        total += n
    return total


async def main_async(args):
    batches = 0
    grand = 0
    while batches < args.max_batches:
        n = await run_once(args.limit, args.dry_run)
        grand += n
        batches += 1
        if args.dry_run or n == 0:
            break
        print(f"batch {batches}: wrote {n} embeddings (total {grand})")
    print("done.", grand)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=32)
    p.add_argument("--max-batches", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
