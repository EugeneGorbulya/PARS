import asyncio
import sys
import os

# Add project root to python path
sys.path.append(os.getcwd())

from core.session import async_session
from services.cian_parser.service import CianFetcherService

async def main():
    async with async_session() as session:
        fetcher = CianFetcherService(session)
        
        # Test fetch: Moscow, 1-2 rooms, price 40-90k (like in your screenshot)
        count = await fetcher.fetch_and_save(
            region_id=1,
            min_price=40000,
            max_price=90000,
            rooms=[1, 2],
            max_pages=1 # Just 1 page for test
        )
        
        print(f"Successfully saved/updated {count} flats.")

if __name__ == "__main__":
    asyncio.run(main())

