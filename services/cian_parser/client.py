import httpx
import asyncio
from typing import Dict, Any, List, Optional

class CianClient:
    BASE_URL = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/118.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def search_offers(
        self, 
        region_id: int = 1,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        rooms: List[int] = [1, 2],
        area_min: Optional[int] = None,
        floor_pref: Optional[str] = None, # "Не первый", "Не последний"
        renovation: Optional[str] = None, # "Косметический+", etc.
        page: int = 1
    ) -> Dict[str, Any]:
        
        json_query = {
            "region": {"type": "terms", "value": [region_id]},
            "category": {"type": "terms", "value": ["flatRent"]},
            "room": {"type": "terms", "value": rooms},
            "_type": "flatrent",
            "engine_version": {"type": "term", "value": 2},
            "page": {"type": "term", "value": page},
        }

        # Price
        if min_price or max_price:
            price_range = {"type": "range"}
            if min_price:
                price_range["from"] = min_price
            if max_price:
                price_range["to"] = max_price
            json_query["price"] = price_range

        # Area
        if area_min and area_min > 0:
            json_query["total_area"] = {"type": "range", "from": area_min}

        # Floor
        if floor_pref:
            if "Не первый" in floor_pref:
                json_query["floor"] = {"type": "range", "from": 2}
            if "Не последний" in floor_pref:
                json_query["is_first_floor"] = {"type": "term", "value": False} # Usually separate param
                # CIAN API is tricky with 'not last'. Often 'is_not_last_floor'.
                # Let's keep it simple with min floor > 1.

        # Renovation (approximate mapping)
        # Renovations: standard, euro, design. (No 'grandmother' usually explicitly, maybe 'no')
        if renovation and renovation != "Любой":
            vals = []
            if "Косметический" in renovation:
                vals.extend(["standard", "euro", "design"])
            elif "Евро" in renovation:
                vals.extend(["euro", "design"])
            elif "Дизайнерский" in renovation:
                vals.extend(["design"])
            
            if vals:
                # Note: CIAN keys might differ, need to verify 'renovation' or 'repair'
                # Common key: "renovation". Values: [ "standard", "euro", "design" ] (need real API check)
                # Assuming 'renovation' works.
                json_query["renovation"] = {"type": "terms", "value": vals}

        payload = {"jsonQuery": json_query}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.BASE_URL, 
                json=payload, 
                headers=self.headers,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()

    async def get_all_offers(self, max_pages: int = 3, **kwargs) -> List[Dict[str, Any]]:
        all_offers = []
        for page in range(1, max_pages + 1):
            try:
                data = await self.search_offers(page=page, **kwargs)
                offers = data.get("data", {}).get("offersSerialized", [])
                if not offers:
                    break
                all_offers.extend(offers)
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break
        return all_offers
