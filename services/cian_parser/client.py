import os
import json
import httpx
import asyncio
from typing import Dict, Any, List, Optional, AsyncIterator, Tuple

# Реальные запросы к Циану только при явном разрешении (антибот).
# По умолчанию — заглушка: запросы не выполняются, возвращается пустой результат.
def _cian_live_enabled() -> bool:
    return os.environ.get("CIAN_LIVE", "").lower() in ("1", "true", "yes")

class CianClient:
    """
    Запрос к API Циан. Базовая логика (URL, заголовки, прогрев, форма jsonQuery и разбор ответа)
    совпадает с рабочим скриптом search_cian_api.py (аренда, Москва, сортировка по дате).
    """
    BASE_URL = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"

    HEADERS = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.cian.ru",
        "Referer": "https://www.cian.ru/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self):
        self.headers = self.HEADERS.copy()

    def _build_payload(
        self,
        page: int = 1,
        region_id: int = 1,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        rooms: List[int] = [1, 2],
        area_min: Optional[int] = None,
        floor_pref: Optional[str] = None,
        foot_min: Optional[int] = None,
        min_house_year: Optional[int] = None,
        renovation: Optional[str] = None,
        repair_ids: Optional[List[int]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Собирает jsonQuery (как в search_cian_api.py). Без запросов к сети."""
        json_query = {
            "region": {"type": "terms", "value": [region_id]},
            "category": {"type": "terms", "value": ["flatRent"]},
            "room": {"type": "terms", "value": rooms},
            "_type": "flatrent",
            "engine_version": {"type": "term", "value": 2},
            "page": {"type": "term", "value": page},
            "sort": {"type": "term", "value": "creation_date_desc"},
        }
        if min_price is not None or max_price is not None:
            value = {}
            if min_price is not None:
                value["from"] = min_price
            if max_price is not None:
                value["to"] = max_price
            json_query["price"] = {"type": "range", "value": value}
        if area_min is not None and area_min > 0:
            json_query["total_area"] = {"type": "range", "value": {"from": area_min}}
        if foot_min is not None and foot_min > 0:
            json_query["foot_min"] = {"type": "term", "value": foot_min}
        if floor_pref:
            if "Не первый" in floor_pref:
                json_query["floor"] = {"type": "range", "value": {"from": 2}}
            if "Не последний" in floor_pref:
                json_query["is_first_floor"] = {"type": "term", "value": False}
        if min_house_year is not None and min_house_year > 0:
            json_query["min_house_year"] = {"type": "term", "value": min_house_year}
        if repair_ids:
            json_query["repair"] = {"type": "terms", "value": repair_ids}
        elif renovation and renovation != "Любой":
            vals = []
            if "Косметический" in renovation:
                vals.extend(["standard", "euro", "design"])
            elif "Евро" in renovation:
                vals.extend(["euro", "design"])
            elif "Дизайнерский" in renovation:
                vals.extend(["design"])
            if vals:
                json_query["renovation"] = {"type": "terms", "value": vals}
        _skip = {"max_pages", "region_id", "min_price", "max_price", "rooms", "area_min", "floor_pref", "foot_min", "min_house_year", "renovation", "repair_ids", "page"}
        _terms_keys = {"repair", "house_material", "parking_type"}
        for key, value in kwargs.items():
            if key in _skip or value is None:
                continue
            if key in json_query:
                continue
            if key in _terms_keys or isinstance(value, list):
                val_list = value if isinstance(value, list) else [value]
                json_query[key] = {"type": "terms", "value": val_list}
            else:
                json_query[key] = {"type": "term", "value": value}
        return {"jsonQuery": json_query}

    async def search_offers(
        self,
        region_id: int = 1,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        rooms: List[int] = [1, 2],
        area_min: Optional[int] = None,
        floor_pref: Optional[str] = None,
        foot_min: Optional[int] = None,
        min_house_year: Optional[int] = None,
        renovation: Optional[str] = None,
        repair_ids: Optional[List[int]] = None,
        page: int = 1,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = self._build_payload(
            page=page, region_id=region_id, min_price=min_price, max_price=max_price,
            rooms=rooms, area_min=area_min, floor_pref=floor_pref, foot_min=foot_min,
            min_house_year=min_house_year, renovation=renovation, repair_ids=repair_ids,
            **kwargs,
        )
        if not _cian_live_enabled():
            if page == 1:
                print("[Cian] Режим заглушки: запросы к API отключены. Для реального парсинга: CIAN_LIVE=1")
            return {"data": {"offersSerialized": []}}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            client.headers.update(self.headers)
            await client.get("https://www.cian.ru/")
            await asyncio.sleep(1.0)
            response = await client.post(self.BASE_URL, json=payload)
            response.raise_for_status()
            return response.json()

    async def get_all_offers(self, max_pages: int = 3, **kwargs) -> List[Dict[str, Any]]:
        """
        Один сеанс (как requests.Session в search_cian_api.py): один GET на cian.ru,
        затем все POST с теми же cookies — иначе возможен 302 (редирект на капчу).
        """
        api_kwargs = {k: v for k, v in kwargs.items() if k != "max_pages"}
        if not _cian_live_enabled():
            print("[Cian] Режим заглушки. Для реального парсинга: CIAN_LIVE=1")
            return []
        all_offers = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            client.headers.update(self.headers)
            await client.get("https://www.cian.ru/")
            await asyncio.sleep(1.0)
            for page in range(1, max_pages + 1):
                try:
                    payload = self._build_payload(page=page, **api_kwargs)
                    response = await client.post(self.BASE_URL, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    offers = data.get("data", {}).get("offersSerialized", [])
                    if not offers:
                        break
                    all_offers.extend(offers)
                    await asyncio.sleep(2.5)
                except Exception as e:
                    msg = str(e).strip() or repr(e) or type(e).__name__
                    print(f"Error fetching page {page}: {msg}")
                    break
        return all_offers

    async def fetch_pages(
        self, max_pages: int = 50, start_page: int = 1, **kwargs
    ) -> AsyncIterator[Tuple[int, List[Dict[str, Any]]]]:
        """
        Один сеанс: перед каждой страницей заход на главную cian.ru, затем POST на API.
        Yields (page_num, offers) для каждой страницы. start_page — с какой страницы начать (1 по умолчанию).
        """
        api_kwargs = {k: v for k, v in kwargs.items() if k not in ("max_pages", "start_page")}
        if not _cian_live_enabled():
            print("[Cian] Режим заглушки. Для реального парсинга: CIAN_LIVE=1")
            return
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            client.headers.update(self.headers)
            for page in range(start_page, max_pages + 1):
                try:
                    await client.get("https://www.cian.ru/")
                    await asyncio.sleep(1.0)
                    payload = self._build_payload(page=page, **api_kwargs)
                    response = await client.post(self.BASE_URL, json=payload)
                    response.raise_for_status()
                    text = response.text
                    if not text or not text.strip():
                        print(f"Error fetching page {page}: empty response (status={response.status_code})")
                        break
                    try:
                        data = json.loads(text)
                    except ValueError:
                        # Ответ не JSON — капча, блок или редирект (HTML)
                        if "captcha" in text.lower() or "капча" in text.lower():
                            print(f"Error fetching page {page}: Циан вернул капчу (после страницы {page - 1}). Остановка. Можно запустить снова позже или с другой сети.")
                        else:
                            snippet = (text.strip()[:200] + "…") if len(text) > 200 else text.strip()
                            print(f"Error fetching page {page}: response is not JSON. First chars: {snippet!r}")
                        break
                    offers = data.get("data", {}).get("offersSerialized", [])
                    yield page, offers
                    if not offers:
                        break
                    await asyncio.sleep(2.5)
                except httpx.HTTPStatusError as e:
                    print(f"Error fetching page {page}: HTTP {e.response.status_code} — {e.response.text[:200]!r}")
                    break
                except Exception as e:
                    msg = str(e).strip() or repr(e) or type(e).__name__
                    print(f"Error fetching page {page}: {msg}")
                    break
