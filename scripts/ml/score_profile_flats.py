"""Fill profile_flat_score using latest (or chosen) model snapshot."""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from core.session import async_session
from services.ml.inference import score_profile_flats
from services.s3.client import S3Client


async def run(profile_id: int, snapshot_id: int | None, device: str | None):
    s3 = S3Client()
    async with async_session() as session:
        n = await score_profile_flats(
            session,
            s3,
            profile_id,
            device=device,
            snapshot_id=snapshot_id,
        )
        print("scored rows:", n)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--snapshot-id", type=int, default=None)
    p.add_argument("--device", type=str, default=None)
    args = p.parse_args()
    asyncio.run(run(args.profile_id, args.snapshot_id, args.device))


if __name__ == "__main__":
    main()
