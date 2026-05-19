"""
2GIS Public Transport API provider.
Docs: https://docs.2gis.com/api/navigation/public-transport/overview
"""
import os
from typing import Optional

import httpx

from services.geo.provider import BaseGeoProvider

DGIS_API_URL = "https://routing.api.2gis.com/public_transport/2.0"

# Все виды ОТ по документации (для маршрута «квартира → точка назначения»)
DEFAULT_TRANSPORT = [
    "pedestrian",
    "metro",
    "light_metro",
    "suburban_train",
    "aeroexpress",
    "tram",
    "bus",
    "trolleybus",
    "shuttle_bus",
    "monorail",
    "funicular_railway",
    "river_transport",
    "cable_car",
    "light_rail",
    "premetro",
    "mcc",
    "mcd",
]


class DGisGeoProvider(BaseGeoProvider):
    """Считает время в пути на общественном транспорте через 2GIS Public Transport API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("DGIS_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("DGIS_API_KEY is required for DGisGeoProvider")

    async def calculate_travel_time(
        self,
        lat_a: float,
        lng_a: float,
        lat_b: float,
        lng_b: float,
        mode: str = "masstransit",
    ) -> int:
        """
        Время в пути от (lat_a, lng_a) до (lat_b, lng_b) в минутах.
        source = квартира, target = точка назначения пользователя.
        """
        if mode != "masstransit":
            # API 2GIS только общественный транспорт
            return await self._request(lat_a, lng_a, lat_b, lng_b)

        return await self._request(lat_a, lng_a, lat_b, lng_b)

    async def _request(
        self,
        lat_from: float,
        lon_from: float,
        lat_to: float,
        lon_to: float,
    ) -> int:
        payload = {
            "source": {"point": {"lat": lat_from, "lon": lon_from}},
            "target": {"point": {"lat": lat_to, "lon": lon_to}},
            "transport": DEFAULT_TRANSPORT,
            "locale": "ru",
            "max_result_count": 1,
        }
        url = f"{DGIS_API_URL}?key={self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 204:
                    # Маршрут не построен (документация 2GIS)
                    return 999
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return 999

        if not isinstance(data, list) or len(data) == 0:
            return 999

        # Берём первый (обычно самый быстрый) вариант; время в секундах
        first_route = data[0]
        total_seconds = first_route.get("total_duration") or 999 * 60
        return max(1, (total_seconds + 29) // 60)
