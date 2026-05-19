"""
Показывает топ квартир профиля по скору модели.

Запуск:
    python3 -m scripts.eval.show_top_flats --profile-id 2
    python3 -m scripts.eval.show_top_flats --profile-id 2 --top 20 --not-rated
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select, exists
from sqlalchemy.orm import selectinload

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import Flat, Profile, ProfileFlatScore, Rating


async def show(profile_id: int, top: int, not_rated: bool, show_rated: bool) -> None:
    async with async_session() as session:
        pr = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
        if not pr:
            print(f"Profile {profile_id} not found")
            return

        q = (
            select(ProfileFlatScore, Flat)
            .join(Flat, Flat.id == ProfileFlatScore.flat_id)
            .where(ProfileFlatScore.profile_id == profile_id)
            .order_by(ProfileFlatScore.score.desc())
        )

        if not_rated:
            rated_sq = select(1).where(
                (Rating.profile_id == profile_id) & (Rating.flat_id == Flat.id)
            )
            q = q.where(~exists(rated_sq))
        elif not show_rated:
            pass  # показываем всех

        q = q.limit(top)
        rows = (await session.execute(q)).all()

        if not rows:
            print("Нет результатов — запустите score_profile_flats сначала.")
            return

        print(f"\nТоп-{top} квартир профиля #{profile_id} «{pr.alias}»"
              + (" (только не оценённые)" if not_rated else "") + "\n")
        print(f"{'#':>3}  {'flat_id':>7}  {'score':>6}  {'beauty':>6}  {'pq':>6}  {'dist':>6}  "
              f"{'price':>10}  {'area':>6}  {'rooms':>5}  url")
        print("─" * 110)
        for i, (pfs, flat) in enumerate(rows, 1):
            price_str = f"{int(flat.price_rub):,}".replace(",", " ") if flat.price_rub else "—"
            area_str = f"{flat.area_sqm:.1f}" if flat.area_sqm else "—"
            rooms_str = str(flat.rooms) if flat.rooms is not None else "—"
            url = flat.url or "—"
            print(
                f"{i:>3}  {flat.id:>7}  {pfs.score:>6.2f}  "
                f"{(pfs.beauty_hat or 0):>6.2f}  {(pfs.price_quality_hat or 0):>6.2f}  "
                f"{(pfs.distance_hat or 0):>6.2f}  "
                f"{price_str:>10}  {area_str:>6}  {rooms_str:>5}  {url}"
            )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--top", type=int, default=30, help="сколько квартир показать")
    p.add_argument("--not-rated", action="store_true", help="только те, которые ещё не оценены")
    p.add_argument("--show-rated", action="store_true", help="только оценённые (для проверки модели)")
    args = p.parse_args()
    asyncio.run(show(args.profile_id, args.top, args.not_rated, args.show_rated))


if __name__ == "__main__":
    main()
