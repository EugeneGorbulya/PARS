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

    async def fetch_and_save(self, region_id: int = 1, max_flats: int | None = None, **filters):
        """
        Main entry point: fetches offers from CIAN and saves/updates them in DB.
        max_flats: если задано, сохраняем не больше этого числа.
        Returns: (saved_count, list of flat_id) — список id квартир, которые только что сохранили.
        """
        print(f"Starting fetch for region {region_id} with filters {filters}" + (f" (max {max_flats} flats)" if max_flats else ""))
        client_filters = {k: v for k, v in filters.items() if k != "max_flats"}
        raw_offers = await self.client.get_all_offers(region_id=region_id, **client_filters)
        print(f"Fetched {len(raw_offers)} offers")

        saved_count = 0
        saved_flat_ids: List[int] = []
        for offer_data in raw_offers:
            if max_flats is not None and saved_count >= max_flats:
                break
            try:
                flat_id = await self._save_offer(offer_data)
                if flat_id is not None:
                    saved_count += 1
                    saved_flat_ids.append(flat_id)
            except Exception as e:
                print(f"Failed to save offer {offer_data.get('id')}: {e}")
        
        await self.session.commit()
        return saved_count, saved_flat_ids

    async def fetch_until_new_count(
        self,
        region_id: int = 1,
        max_new_flats: int = 100,
        max_pages: int = 50,
        start_page: int = 1,
        **filters,
    ):
        """
        Собирает квартиры постранично; сохраняет только те, которых ещё нет в БД (по cian_id).
        start_page — с какой страницы начать (1 по умолчанию).
        Returns: (new_count, list of flat_id).
        """
        client_filters = {k: v for k, v in filters.items() if k not in ("max_flats", "max_pages", "start_page")}
        new_count = 0
        saved_flat_ids: List[int] = []
        async for page, offers in self.client.fetch_pages(
            max_pages=max_pages, start_page=start_page, region_id=region_id, **client_filters
        ):
            print(f"Page {page}: {len(offers)} offers", end="")
            saved_this_page = 0
            for offer_data in offers:
                if new_count >= max_new_flats:
                    break
                cian_id = offer_data.get("cianId") or offer_data.get("id")
                if not cian_id:
                    continue
                if await self._cian_id_exists(int(cian_id)):
                    continue
                try:
                    flat_id = await self._save_offer(offer_data)
                    if flat_id is not None:
                        new_count += 1
                        saved_this_page += 1
                        saved_flat_ids.append(flat_id)
                        print(f"  New flat #{new_count}: cian_id={cian_id} -> flat_id={flat_id}")
                except Exception as e:
                    print(f"Failed to save offer {cian_id}: {e}")
            print(f" -> {saved_this_page} new (total {new_count})")
            if new_count >= max_new_flats:
                print(f"Reached {max_new_flats} new flats after page {page}. Stopping (no new page).")
                break
        await self.session.commit()
        return new_count, saved_flat_ids

    async def _cian_id_exists(self, cian_id: int) -> bool:
        """Проверяет, есть ли уже квартира с таким cian_id в БД."""
        stmt = select(Flat.id).where(Flat.cian_id == cian_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _save_offer(self, data: Dict[str, Any]) -> int | None:
        """Сохраняет объявление и фото. Возвращает flat_id или None при ошибке."""
        # API может отдавать id или cianId
        cian_id = data.get("cianId") or data.get("id")
        if not cian_id:
            return None

        bargain = data.get("bargainTerms") or {}
        # В ответе API: priceRur (руб) или price
        price_rub_val = bargain.get("priceRur") or bargain.get("price")
        if price_rub_val is not None:
            price_rub_val = int(price_rub_val)

        building = data.get("building") or {}
        build_year = building.get("buildYear")
        if build_year is not None:
            try:
                build_year = int(build_year)
            except (TypeError, ValueError):
                build_year = None

        geo = data.get("geo") or {}
        coords = geo.get("coordinates") or {}
        url = data.get("fullUrl") or f"https://www.cian.ru/rent/flat/{cian_id}/"
        if not url.strip():
            url = f"https://www.cian.ru/rent/flat/{cian_id}/"

        def _num(v):
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _int(v):
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        # 1. Parse Flat fields (поля как в модели Flat)
        flat_values = {
            "cian_id": int(cian_id),
            "url": url,
            "city": geo.get("userInput") or "Unknown",
            "address": self._build_address(geo),
            "lat": _num(coords.get("lat")),
            "lng": _num(coords.get("lng")),
            "price_rub": price_rub_val,
            "rooms": self._parse_rooms(data),
            "area_sqm": float(data.get("totalArea") or 0),
            "floor": _int(data.get("floorNumber")),
            "floors_total": _int(building.get("floorsCount")),
            "building_year": build_year,
            "material": building.get("materialType"),
            "active": True,
            "fetched_at": datetime.now(),
        }
        
        # Upsert Flat (Insert or Update по cian_id)
        stmt = insert(Flat).values(flat_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Flat.cian_id],
            set_={
                "url": stmt.excluded.url,
                "city": stmt.excluded.city,
                "address": stmt.excluded.address,
                "lat": stmt.excluded.lat,
                "lng": stmt.excluded.lng,
                "price_rub": stmt.excluded.price_rub,
                "rooms": stmt.excluded.rooms,
                "area_sqm": stmt.excluded.area_sqm,
                "floor": stmt.excluded.floor,
                "floors_total": stmt.excluded.floors_total,
                "building_year": stmt.excluded.building_year,
                "material": stmt.excluded.material,
                "active": True,
                "fetched_at": stmt.excluded.fetched_at,
            }
        ).returning(Flat.id)
        
        result = await self.session.execute(stmt)
        flat_id = result.scalar_one()

        # 2. Process Photos
        photos = data.get("photos", [])
        if photos:
            await self._save_photos(flat_id, photos)
        return flat_id

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

