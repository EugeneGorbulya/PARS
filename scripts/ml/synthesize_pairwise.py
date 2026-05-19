"""
Генерирует синтетические PairwiseRating из уже существующих оценок Stage A.

Логика: если flat A получила score_a > score_b по какому-то фактору,
значит в дуэли по этому фактору пользователь предпочёл бы A.
Тай (score_a == score_b) — пропускаем, нет сигнала.

Пары нормализуются: flat_a_id < flat_b_id (как в uix_pairwise).
ON CONFLICT DO NOTHING — реальные дуэли не перезаписываются.

Запуск:
  # Сухой прогон: показать сколько дуэлей и записей будет
  python3 -m scripts.ml.synthesize_pairwise --profile-id 2 --duels 200 --dry-run

  # Применить N случайных дуэлей (как будто пользователь прошёл N раундов вручную)
  python3 -m scripts.ml.synthesize_pairwise --profile-id 2 --duels 200

  # Все возможные пары (осторожно — может быть 50 000+)
  python3 -m scripts.ml.synthesize_pairwise --profile-id 2 --all-pairs
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import sys
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import PairwiseRating, Rating, User, Profile
from services.ml.dataset import train_flat_ids

FACTORS = ["beauty", "price_quality", "distance_pref"]
FACTOR_FIELDS = {
    "beauty": "beauty",
    "price_quality": "price_quality",
    "distance_pref": "distance_pref",
}


async def run(profile_id: int, n_duels: int | None, all_pairs: bool, dry_run: bool) -> None:
    import random
    from collections import Counter

    async with async_session() as session:
        pr = (await session.execute(
            select(Profile).where(Profile.id == profile_id)
        )).scalar_one_or_none()
        if not pr:
            print(f"Profile {profile_id} not found")
            return
        user_id = pr.user_id

        rows = (await session.execute(
            select(Rating.flat_id, Rating.beauty, Rating.price_quality, Rating.distance_pref)
            .where(Rating.profile_id == profile_id)
            .where(Rating.skipped.is_(False))
            .where(
                (Rating.beauty.is_not(None))
                | (Rating.price_quality.is_not(None))
                | (Rating.distance_pref.is_not(None))
            )
        )).all()

        # Только train-часть: пары с val-квартирами утекают лейблы в Stage B.
        train_ids = await train_flat_ids(session, profile_id)

    if not rows:
        print("No ratings found")
        return

    scores: dict[int, dict[str, int | None]] = {}
    for flat_id, beauty, pq, dist in rows:
        scores[int(flat_id)] = {
            "beauty": beauty,
            "price_quality": pq,
            "distance_pref": dist,
        }

    n_total = len(scores)
    flat_ids = [fid for fid in scores.keys() if fid in train_ids]
    all_possible_pairs = list(itertools.combinations(flat_ids, 2))
    max_duels = len(all_possible_pairs)
    print(f"Оценённых квартир: {n_total} (train: {len(flat_ids)}, val отрезан: {n_total - len(flat_ids)})")
    print(f"Максимально возможных train-дуэлей: {max_duels}")

    if all_pairs:
        selected_pairs = all_possible_pairs
    elif n_duels is not None:
        if n_duels > max_duels:
            print(f"  ⚠️  --duels {n_duels} > {max_duels} доступных — берём все")
            selected_pairs = all_possible_pairs
        else:
            selected_pairs = random.sample(all_possible_pairs, n_duels)
    else:
        print("Укажите --duels N или --all-pairs")
        return

    # Для каждой выбранной пары генерируем записи по всем факторам (как в реальной дуэли)
    records: List[Tuple[int, int, str, int]] = []
    for a_id, b_id in selected_pairs:
        if a_id > b_id:
            a_id, b_id = b_id, a_id
        for factor in FACTORS:
            sa = scores[a_id].get(factor)
            sb = scores[b_id].get(factor)
            if sa is None or sb is None:
                continue
            if sa == sb:
                continue  # тай — нет сигнала
            preferred = a_id if sa > sb else b_id
            records.append((a_id, b_id, factor, preferred))

    factor_counts = Counter(f for _, _, f, _ in records)
    print(f"\nДуэлей выбрано: {len(selected_pairs)}")
    print(f"Записей для вставки: {len(records)}")
    for f, n in sorted(factor_counts.items()):
        print(f"  {f}: {n}")
    print(f"\n  (тай-пары пропущены — разница в оценках отсутствует)")

    if dry_run:
        print("\nDRY-RUN: ничего не записано")
        return

    batch_size = 500
    inserted = 0
    async with async_session() as session:
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            for a_id, b_id, factor, preferred_id in batch:
                stmt = (
                    insert(PairwiseRating)
                    .values(
                        user_id=user_id,
                        profile_id=profile_id,
                        flat_a_id=a_id,
                        flat_b_id=b_id,
                        factor=factor,
                        preferred_flat_id=preferred_id,
                    )
                    .on_conflict_do_nothing(constraint="uix_pairwise")
                )
                await session.execute(stmt)
            await session.commit()
            inserted += len(batch)
            if inserted % 2000 == 0 or inserted == len(records):
                print(f"  ...{inserted}/{len(records)}")

    print(f"\nГотово. До {len(records)} записей вставлено (ON CONFLICT DO NOTHING).")
    print(f"Откат — удалить по времени:")
    print(f"  DELETE FROM pairwise_ratings WHERE profile_id = {profile_id} AND created_at >= '<timestamp до запуска>';")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--duels", type=int, default=None,
                   help="Число случайных дуэлей (пар). Каждая даёт до 3 записей (по факторам).")
    p.add_argument("--all-pairs", action="store_true",
                   help="Использовать все возможные пары (осторожно: ~50 000 при 320 квартирах)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args.profile_id, args.duels, args.all_pairs, args.dry_run))


if __name__ == "__main__":
    main()
