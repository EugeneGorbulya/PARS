from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.dialects.postgresql import insert
from typing import List, Dict, Any
from datetime import datetime

from models import Flat, FlatPhoto
from services.cian_parser.client import CianClient

class CianFetcherService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.client = CianClient()

    async def fetch_and_save(self, region_id: int = 1, **filters):
        """
        Main entry point: fetches offers from CIAN and saves/updates them in DB.
        """
        print(f"Starting fetch for region {region_id} with filters {filters}")
        raw_offers = await self.client.get_all_offers(region_id=region_id, **filters)
        print(f"Fetched {len(raw_offers)} offers")

        saved_count = 0
        for offer_data in raw_offers:
            try:
                await self._save_offer(offer_data)
                saved_count += 1
            except Exception as e:
                print(f"Failed to save offer {offer_data.get('id')}: {e}")
        
        await self.session.commit()
        return saved_count

    async def _save_offer(self, data: Dict[str, Any]):
        cian_id = data.get("id")
        if not cian_id:
            return

        # 1. Parse Flat fields
        flat_values = {
            "cian_id": cian_id,
            "url": data.get("fullUrl", ""),
            "city": data.get("geo", {}).get("userInput", "Unknown"), # Simplification
            "address": self._build_address(data.get("geo", {})),
            "lat": data.get("geo", {}).get("coordinates", {}).get("lat"),
            "lng": data.get("geo", {}).get("coordinates", {}).get("lng"),
            "price_rub": data.get("bargainTerms", {}).get("price"),
            "rooms": self._parse_rooms(data),
            "area_sqm": float(data.get("totalArea", 0) or 0),
            "floor": data.get("floorNumber"),
            "floors_total": data.get("building", {}).get("floorsCount"),
            "building_year": data.get("building", {}).get("buildYear"),
            "material": data.get("building", {}).get("materialType"),
            "active": True,
            "fetched_at": datetime.now()
        }
        
        # Upsert Flat (Insert or Update)
        stmt = insert(Flat).values(flat_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Flat.cian_id],
            set_={
                "price_rub": stmt.excluded.price_rub,
                "active": True,
                "fetched_at": datetime.now()
            }
        ).returning(Flat.id)
        
        result = await self.session.execute(stmt)
        flat_id = result.scalar_one()

        # 2. Process Photos
        photos = data.get("photos", [])
        if photos:
            await self._save_photos(flat_id, photos)

    async def _save_photos(self, flat_id: int, photos: List[Dict[str, Any]]):
        # Optionally: clear old photos or only add new ones. 
        # For simplicity, we'll ignore duplicates handled by unique constraint logic inside DB or just append.
        # But our schema has UniqueConstraint(flat_id, seq).
        # Let's try to insert, ignoring conflicts.
        
        for i, photo in enumerate(photos):
            photo_url = photo.get("fullUrl")
            if not photo_url:
                continue
            
            photo_values = {
                "flat_id": flat_id,
                "seq": i,
                "url": photo_url,
                "room_type": None # CIAN usually doesn't give this explicitly in simple list
            }
            
            stmt = insert(FlatPhoto).values(photo_values)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["flat_id", "seq"]
            )
            await self.session.execute(stmt)

    def _build_address(self, geo: Dict[str, Any]) -> str:
        # Simplistic address builder. CIAN geo object is complex.
        # Often 'userInput' is enough for display.
        return geo.get("userInput", "")

    def _parse_rooms(self, data: Dict[str, Any]) -> int:
        # "roomsCount": 1, or "isStudio": true
        if data.get("isStudio"):
            return 0
        return data.get("roomsCount", 1)

