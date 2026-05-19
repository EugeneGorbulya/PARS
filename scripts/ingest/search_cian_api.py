"""
Прямой запрос к API CIAN: аренда 1–2 комнат, Москва, 40–90 тыс., сортировка по дате.
Запуск: python3 scripts/ingest/search_cian_api.py
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
os.chdir(_project_root)

import json
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

API_URL = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"

payload = {
    "jsonQuery": {
        "region": {"type": "terms", "value": [1]},
        "category": {"type": "terms", "value": ["flatRent"]},
        "room": {"type": "terms", "value": [1, 2]},
        "price": {"type": "range", "value": {"from": 40000, "to": 90000}},
        "_type": "flatrent",
        "engine_version": {"type": "term", "value": 2},
        "page": {"type": "term", "value": 1},
        "sort": {"type": "term", "value": "creation_date_desc"},
    }
}

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

session = requests.Session()
session.headers.update(HEADERS)
session.get("https://www.cian.ru/", timeout=30)

try:
    response = session.post(
        API_URL,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        print("Ошибка запроса!")
        print("HTTP код:", response.status_code)
        print("Ответ сервера:")
        print(response.text)
        sys.exit(1)

    try:
        data = response.json()
    except json.JSONDecodeError:
        print("Ошибка парсинга JSON!")
        print("HTTP код:", response.status_code)
        print("Ответ сервера:")
        print(response.text)
        sys.exit(1)

except Timeout:
    print("Ошибка: превышено время ожидания запроса (timeout)")
    sys.exit(1)

except ConnectionError:
    print("Ошибка соединения с сервером")
    sys.exit(1)

except RequestException as e:
    print("Ошибка при выполнении запроса:")
    print(str(e))
    sys.exit(1)


# ---- Если всё успешно ----
offers = data.get("data", {}).get("offersSerialized", [])
print(f"Всего объявлений на странице: {len(offers)}")

for offer in offers:
    url = offer.get("fullUrl") or f"https://www.cian.ru/rent/flat/{offer.get('cianId')}/"
    price = offer.get("bargainTerms", {}).get("priceRur")
    area = offer.get("totalArea")
    floor = offer.get("floorNumber")
    floors_total = (offer.get("building") or {}).get("floorsCount")
    address = ", ".join(c.get("title", "") for c in (offer.get("geo") or {}).get("address") or [])
    photos = [p.get("fullUrl") for p in (offer.get("photos") or []) if p.get("fullUrl")]

    print(f"\n[{offer.get('cianId')}] {price:,} ₽/мес | {area} м² | эт. {floor}/{floors_total}")
    print(f"  {address}")
    print(f"  {url}")
    if photos:
        print(f"  Фото ({len(photos)}): {photos[0]}")
