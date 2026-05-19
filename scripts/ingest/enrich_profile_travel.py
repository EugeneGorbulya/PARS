"""
Считает время в пути для всех квартир, попадающих под фильтры профиля,
по всем POI этого профиля. Записывает в flat_poi_travel.

Использует реального гео-провайдера через get_geo_provider():
- 2GIS, если в окружении задан DGIS_API_KEY;
- иначе MockGeoProvider.

Запуск:
    python -m scripts.ingest.enrich_profile_travel --profile-id 1
    python -m scripts.ingest.enrich_profile_travel --profile-id 1 --dry-run
    python -m scripts.ingest.enrich_profile_travel --profile-id 1 --max-per-poi 500
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import Flat, FlatPoiTravel, POI, Profile, ProfilePOI
from services.geo.provider import get_geo_provider


async def enrich(profile_id: int, dry_run: bool = False, max_per_poi: int | None = None, commit_every: int = 50) -> None:
    provider = get_geo_provider()
    provider_name = type(provider).__name__
    print(f"Provider: {provider_name}")

    async with async_session() as session:
        pr = (
            await session.execute(select(Profile).where(Profile.id == profile_id))
        ).scalar_one_or_none()
        if not pr:
            print(f"Profile {profile_id} not found")
            return

        filters = pr.cian_filter or {}
        min_p = int(filters.get("min_price", 0))
        max_p = int(filters.get("max_price", 10**9))
        rooms = filters.get("rooms", []) or []
        print(f"Profile #{pr.id} alias={pr.alias} filters: price=[{min_p}, {max_p}] rooms={rooms}")

        pois_rows = (
            await session.execute(
                select(POI, ProfilePOI.mode)
                .join(ProfilePOI, ProfilePOI.poi_id == POI.id)
                .where(ProfilePOI.profile_id == profile_id)
            )
        ).all()
        if not pois_rows:
            print("Profile has no POIs — nothing to do")
            return

        total = 0
        for poi, mode in pois_rows:
            stmt = (
                select(Flat)
                .outerjoin(
                    FlatPoiTravel,
                    (FlatPoiTravel.flat_id == Flat.id)
                    & (FlatPoiTravel.poi_id == poi.id)
                    & (FlatPoiTravel.mode == mode),
                )
                .where(FlatPoiTravel.flat_id.is_(None))
                .where(Flat.lat.is_not(None))
                .where(Flat.lng.is_not(None))
                .where(Flat.price_rub >= min_p)
                .where(Flat.price_rub <= max_p)
            )
            if rooms:
                stmt = stmt.where(Flat.rooms.in_(rooms))
            if max_per_poi:
                stmt = stmt.limit(max_per_poi)

            flats = (await session.execute(stmt)).scalars().all()
            print(f"  POI '{poi.label}' ({mode}): {len(flats)} flats to enrich")

            if dry_run:
                continue

            done = 0
            for flat in flats:
                minutes = await provider.calculate_travel_time(
                    float(flat.lat), float(flat.lng),
                    float(poi.lat), float(poi.lng),
                    mode,
                )
                stmt_ins = (
                    insert(FlatPoiTravel)
                    .values(flat_id=flat.id, poi_id=poi.id, mode=mode, travel_min=minutes)
                    .on_conflict_do_update(
                        index_elements=["flat_id", "poi_id", "mode"],
                        set_={"travel_min": minutes},
                    )
                )
                await session.execute(stmt_ins)
                total += 1
                done += 1
                if done % commit_every == 0:
                    await session.commit()
                    print(f"    ...{done}/{len(flats)} for {poi.label}")

            await session.commit()

        print(f"Done. enriched_records={total}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--dry-run", action="store_true", help="Только показать сколько надо посчитать")
    p.add_argument("--max-per-poi", type=int, default=None, help="Ограничить число квартир на одну POI")
    p.add_argument("--commit-every", type=int, default=50)
    args = p.parse_args()
    asyncio.run(
        enrich(
            args.profile_id,
            dry_run=args.dry_run,
            max_per_poi=args.max_per_poi,
            commit_every=args.commit_every,
        )
    )


if __name__ == "__main__":
    main()
