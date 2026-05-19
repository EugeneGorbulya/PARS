"""
Синтетическая аугментация данных для head_d.

Режимы:
  1. Близкие (default): берём квартиры c distance_pref=4, ставим 5, занижаем travel_min.
  2. Далёкие (--keep-travel --travel-min-gt N): берём квартиры с distance_pref=X
     И travel_min > N, меняем оценку, travel_min не трогаем.

Запись делается через JSON-бэкап, чтобы можно было откатить.

Примеры:
  # Сухой прогон
  python -m scripts.ml.synthesize_close_flats --profile-id 1 --count 30 --dry-run

  # «Близко=5»: занижаем travel_min до [5..15], ставим distance_pref=5
  python -m scripts.ml.synthesize_close_flats --profile-id 1 --count 30 --travel-min 5 --travel-min-max 15

  # «Далеко=3»: у квартир с travel_min>55 меняем 4→3, travel_min не трогаем
  python -m scripts.ml.synthesize_close_flats --profile-id 1 --count 30 \\
      --source-score 4 --target-score 3 --keep-travel --travel-min-gt 55

  # Откат
  python -m scripts.ml.synthesize_close_flats --restore synth_distance_backup_1_1716000000.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from typing import Dict, List

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import FlatPoiTravel, ProfilePOI, Rating


async def _profile_poi_modes(session, profile_id: int) -> List[tuple[int, str]]:
    rows = await session.execute(
        select(ProfilePOI.poi_id, ProfilePOI.mode).where(ProfilePOI.profile_id == profile_id)
    )
    return [(int(p), str(m)) for p, m in rows.all()]


async def apply_synthesis(
    profile_id: int,
    count: int,
    travel_min: int,
    travel_min_max: int,
    target_score: int,
    source_score: int,
    keep_travel: bool,
    travel_min_gt: int | None,
    dry_run: bool,
) -> str | None:
    if not keep_travel and travel_min_max < travel_min:
        raise SystemExit("--travel-min-max must be >= --travel-min")

    async with async_session() as session:
        pois = await _profile_poi_modes(session, profile_id)
        if not pois:
            print("Profile has no POIs, nothing to do")
            return None

        poi_ids = [p for p, _ in pois]

        if keep_travel:
            # Режим «далёкие»: джойним travel_min, сортируем по убыванию (самые далёкие — первые)
            cand_q = (
                select(
                    Rating.id,
                    Rating.flat_id,
                    Rating.distance_pref,
                    FlatPoiTravel.travel_min,
                )
                .join(
                    FlatPoiTravel,
                    (FlatPoiTravel.flat_id == Rating.flat_id)
                    & (FlatPoiTravel.poi_id.in_(poi_ids)),
                )
                .where(Rating.profile_id == profile_id)
                .where(Rating.skipped.is_(False))
                .where(Rating.distance_pref == source_score)
                .where(FlatPoiTravel.travel_min.is_not(None))
            )
            if travel_min_gt is not None:
                cand_q = cand_q.where(FlatPoiTravel.travel_min > travel_min_gt)
            cand_q = cand_q.order_by(FlatPoiTravel.travel_min.desc())

            rows = (await session.execute(cand_q)).all()
            # deduplicate by flat_id (может быть несколько POI), берём максимальный travel_min
            seen: dict[int, tuple] = {}
            for row in rows:
                fid = int(row.flat_id)
                if fid not in seen or int(row.travel_min) > int(seen[fid].travel_min):
                    seen[fid] = row
            candidates = sorted(seen.values(), key=lambda r: int(r.travel_min), reverse=True)
        else:
            # Режим «близкие»: просто берём всех matching, потом shuffle
            cand_q = (
                select(Rating.id, Rating.flat_id, Rating.distance_pref)
                .where(Rating.profile_id == profile_id)
                .where(Rating.skipped.is_(False))
                .where(Rating.distance_pref == source_score)
            )
            if travel_min_gt is not None:
                cand_q = cand_q.where(
                    Rating.flat_id.in_(
                        select(FlatPoiTravel.flat_id)
                        .where(FlatPoiTravel.poi_id.in_(poi_ids))
                        .where(FlatPoiTravel.travel_min > travel_min_gt)
                    )
                )
            candidates = list((await session.execute(cand_q)).all())
            random.shuffle(candidates)

        if not candidates:
            print(f"No ratings matching criteria in profile {profile_id}")
            return None

        chosen = candidates[: max(1, count)]
        chosen_ids = [int(r.flat_id) for r in chosen]
        chosen_rating_ids = [int(r.id) for r in chosen]

        travel_vals = [int(r.travel_min) for r in chosen if hasattr(r, "travel_min") and r.travel_min is not None]
        travel_info = f"  travel_min range: {min(travel_vals)}..{max(travel_vals)} мин" if travel_vals else ""
        print(f"Pool: {len(candidates)}, taking: {len(chosen)} flats{(' (sorted by travel_min DESC)' if keep_travel else '')}")
        if travel_info:
            print(travel_info)
        print(f"flat_ids: {chosen_ids}")

        poi_ids = [p for p, _ in pois]
        old_travel = await session.execute(
            select(FlatPoiTravel.flat_id, FlatPoiTravel.poi_id, FlatPoiTravel.mode, FlatPoiTravel.travel_min)
            .where(FlatPoiTravel.flat_id.in_(chosen_ids))
            .where(FlatPoiTravel.poi_id.in_(poi_ids))
        )
        old_travel_rows = old_travel.all()
        travel_backup = [
            {"flat_id": int(fid), "poi_id": int(pid), "mode": str(mode), "travel_min": (None if tm is None else int(tm))}
            for fid, pid, mode, tm in old_travel_rows
        ]

        rating_backup = [
            {"rating_id": int(r.id), "flat_id": int(r.flat_id), "distance_pref": int(r.distance_pref)}
            for r in chosen
        ]

        if dry_run:
            print("DRY-RUN: no changes written")
            print(f"  would update ratings.distance_pref: {source_score} → {target_score} for {len(chosen_rating_ids)} rows")
            if keep_travel:
                print("  travel_min: NOT changed (--keep-travel)")
            else:
                print(f"  would update flat_poi_travel.travel_min in [{travel_min}..{travel_min_max}] for {len(old_travel_rows)} rows")
            return None

        for rid in chosen_rating_ids:
            await session.execute(
                update(Rating).where(Rating.id == rid).values(distance_pref=target_score)
            )

        if not keep_travel:
            for fid in chosen_ids:
                new_t = random.randint(travel_min, travel_min_max)
                for poi_id, mode in pois:
                    stmt_ins = (
                        insert(FlatPoiTravel)
                        .values(flat_id=fid, poi_id=poi_id, mode=mode, travel_min=new_t)
                        .on_conflict_do_update(
                            index_elements=["flat_id", "poi_id", "mode"],
                            set_={"travel_min": new_t},
                        )
                    )
                    await session.execute(stmt_ins)

        await session.commit()

        ts = int(time.time())
        backup_path = f"synth_distance_backup_{profile_id}_{ts}.json"
        backup = {
            "profile_id": profile_id,
            "source_score": source_score,
            "target_score": target_score,
            "keep_travel": keep_travel,
            "travel_min_gt_filter": travel_min_gt,
            "travel_min_range": [travel_min, travel_min_max],
            "ratings": rating_backup,
            "flat_poi_travel": travel_backup,
        }
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print(f"Applied. Backup: {backup_path}")
        return backup_path


async def restore(backup_path: str) -> None:
    with open(backup_path, "r", encoding="utf-8") as f:
        backup = json.load(f)

    async with async_session() as session:
        for r in backup.get("ratings", []):
            await session.execute(
                update(Rating).where(Rating.id == int(r["rating_id"])).values(distance_pref=int(r["distance_pref"]))
            )

        for row in backup.get("flat_poi_travel", []):
            tm = row["travel_min"]
            await session.execute(
                update(FlatPoiTravel)
                .where(
                    (FlatPoiTravel.flat_id == int(row["flat_id"]))
                    & (FlatPoiTravel.poi_id == int(row["poi_id"]))
                    & (FlatPoiTravel.mode == str(row["mode"]))
                )
                .values(travel_min=None if tm is None else int(tm))
            )

        await session.commit()
        print(f"Restored {len(backup.get('ratings', []))} ratings and "
              f"{len(backup.get('flat_poi_travel', []))} travel rows from {backup_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, default=None)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--source-score", type=int, default=4, help="distance_pref значение, у которого подменяем")
    p.add_argument("--target-score", type=int, default=5, help="новое значение distance_pref")
    p.add_argument("--travel-min", type=int, default=5, help="нижняя граница случайного travel_min (при обновлении)")
    p.add_argument("--travel-min-max", type=int, default=15, help="верхняя граница travel_min (при обновлении)")
    p.add_argument("--keep-travel", action="store_true",
                   help="не менять travel_min, только перебить оценку (для режима «далеко=3»)")
    p.add_argument("--travel-min-gt", type=int, default=None,
                   help="фильтровать кандидатов: только квартиры с travel_min > N")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--restore", type=str, default=None, help="путь к JSON-бэкапу для отката")
    args = p.parse_args()

    if args.restore:
        asyncio.run(restore(args.restore))
        return

    if args.profile_id is None:
        raise SystemExit("--profile-id is required (unless --restore)")

    asyncio.run(
        apply_synthesis(
            args.profile_id,
            args.count,
            args.travel_min,
            args.travel_min_max,
            args.target_score,
            args.source_score,
            args.keep_travel,
            args.travel_min_gt,
            args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
