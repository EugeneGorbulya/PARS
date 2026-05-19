"""
Сбор квартир с Циан: только тех, кого ещё нет в БД (без дубликатов).
Набирает TARGET_NEW_FLATS (100) новых — текущую страницу дочитывает, следующую не открывает.
Запуск: CIAN_LIVE=1 python3 scripts/tests/test_fetcher.py
        MINIMAL=1 CIAN_LIVE=1 python3 scripts/tests/test_fetcher.py  — минимальные фильтры.
"""
import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.session import async_session
from services.cian_parser.service import CianFetcherService

# Минимальные фильтры — обычно дают объявления
MINIMAL_FILTERS = {
    "region_id": 1,
    "min_price": 70000,
    "max_price": 90000,
    "rooms": [3],
    "max_pages": 1,
}

# Полный набор (как в профиле бота) — может вернуть 0 из‑за жёсткой комбинации
TEST_FILTERS = {
    "tv": 1,
    "wm": 1,
    "bath": 1,
    "kids": 1,
    "pets": 1,
    "rfgr": 1,
    "type": 4,
    "mebel": 1,
    "phone": 1,
    "rooms": [1, 2],
    "mebel_k": 1,
    "minlift": 1,
    "area_min": 35,
    "currency": 2,
    "foot_min": 10,
    "internet": 1,
    "minfloor": 1,
    "minkarea": 10,
    "minlarea": 10,
    "deal_type": "rent",
    "max_price": 75000,
    "min_price": 65000,
    "minfloorn": 1,
    "only_flat": 1,
    "only_foot": 2,
    "region_id": 1,
    "room_type": 2,
    "floor_pref": "Любой",
    "offer_type": "flat",
    "renovation": "Евро+",
    "repair_ids": [3],
    "conditioner": 1,
    "dish_washer": 1,
    "parking_type": [2],
    "windows_type": 0,
    "kitchen_stove": "electric",
    "min_balconies": 1,
    "engine_version": 2,
    "house_material": [1],
    "max_commission": 50,
    "min_house_year": 2013,
    "is_by_homeowner": 1,
    "min_ceiling_height": 2.5,
    "demolished_in_moscow_programm": 0,
}

# Собрать 100 новых квартир (которых ещё нет в БД), без дубликатов; после набора 100 — стоп, новую страницу не открывать
TARGET_NEW_FLATS = 100
MAX_PAGES = 50  # потолок страниц, чтобы не уходить в бесконечность

async def main():
    parser = argparse.ArgumentParser(description="Fetch flats from Cian")
    parser.add_argument("--page", type=int, default=1, help="Start from page N (default 1)")
    args = parser.parse_args()

    use_minimal = os.environ.get("MINIMAL", "").lower() in ("1", "true", "yes")
    filters = dict(MINIMAL_FILTERS if use_minimal else TEST_FILTERS)
    if use_minimal:
        print("Using MINIMAL filters (region, price 65–75k, rooms 1–2).")
    if args.page > 1:
        print(f"Starting from page {args.page}.")
    region_id = filters.pop("region_id", 1)
    filters.pop("max_pages", None)
    async with async_session() as session:
        fetcher = CianFetcherService(session)
        count, flat_ids = await fetcher.fetch_until_new_count(
            region_id=region_id,
            max_new_flats=TARGET_NEW_FLATS,
            max_pages=MAX_PAGES,
            start_page=args.page,
            **filters,
        )
        print(f"Done. New flats saved: {count} (ids: {flat_ids[:5]}{'...' if len(flat_ids) > 5 else ''})")

if __name__ == "__main__":
    asyncio.run(main())

