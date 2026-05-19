"""
Честная оценка качества рекомендера: метрики ранжирования top-K,
ранговые корреляции и качество per-head регрессии — для нашей модели
и нескольких baseline-методов.

  1. Median baseline      — всегда предсказывает медиану train
  2. Sort by distance     — ниже travel_min = выше место
  3. Sort by price        — ниже цена = выше место
  4. Ручная формула       — −travel_norm − price_norm
  5. Наша модель          — score из profile_flat_scores

По умолчанию (--val-only) метрики считаются только на val-части (последние
VAL_RATIO оценок по времени), которую модель не видела при обучении ни в
Stage A (регрессия), ни в Stage B (синтез пар фильтруется через train_flat_ids).
--all-data считает по всем оценённым квартирам (in-sample, для сравнения).

Запуск:
    python3 -m scripts.eval.eval_baselines --profile-id 2
    python3 -m scripts.eval.eval_baselines --profile-id 2 --all-data
    python3 -m scripts.eval.eval_baselines --profile-id 2 --k 5

  # what-if без перетренировки: переопределить веса (target + модель пересчитываются на лету)
    python3 -m scripts.eval.eval_baselines --profile-id 2 --weights 0.55,0.15,0.30
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from typing import Callable, Dict, List, Tuple

import numpy as np
from sqlalchemy import select, func

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import Flat, FlatPoiTravel, Profile, ProfileFlatScore, ProfilePOI, Rating
from services.ml.config import VAL_RATIO


# ──────────────────────────────────────────────
# Ranking metrics
# ──────────────────────────────────────────────

def hit_at_k(ranked_ids: List[int], relevant: set, k: int) -> float:
    return 1.0 if any(fid in relevant for fid in ranked_ids[:k]) else 0.0


def precision_at_k(ranked_ids: List[int], relevant: set, k: int) -> float:
    top = ranked_ids[:k]
    if not top:
        return 0.0
    return sum(1 for i in top if i in relevant) / min(k, len(top))


def recall_at_k(ranked_ids: List[int], relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    top_rel = sum(1 for i in ranked_ids[:k] if i in relevant)
    return top_rel / len(relevant)


def dcg_at_k(ranked_ids: List[int], rel_scores: Dict[int, float], k: int) -> float:
    return sum(
        rel_scores.get(fid, 0.0) / math.log2(i + 2)
        for i, fid in enumerate(ranked_ids[:k])
    )


def ndcg_at_k(ranked_ids: List[int], rel_scores: Dict[int, float], k: int) -> float:
    ideal = sorted(rel_scores.values(), reverse=True)[:k]
    idcg = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    if idcg <= 0:
        return 0.0
    return dcg_at_k(ranked_ids, rel_scores, k) / idcg


def mrr(ranked_ids: List[int], relevant: set) -> float:
    """Reciprocal rank первой релевантной квартиры. 0 если ни одной."""
    for i, fid in enumerate(ranked_ids, 1):
        if fid in relevant:
            return 1.0 / i
    return 0.0


def pairwise_accuracy(ranked_ids: List[int], rel_scores: Dict[int, float]) -> float:
    """Доля верно упорядоченных пар (concordance / Kendall ≈ τ × 0.5 + 0.5)."""
    ids = [i for i in ranked_ids if i in rel_scores]
    correct = total = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sa, sb = rel_scores[ids[i]], rel_scores[ids[j]]
            if abs(sa - sb) < 1e-6:
                continue
            total += 1
            if sa > sb:
                correct += 1
    return correct / total if total else 0.0


# ──────────────────────────────────────────────
# Correlations
# ──────────────────────────────────────────────

def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    return _pearson(ra, rb)


def _kendall_tau(a: np.ndarray, b: np.ndarray) -> float:
    """τ-b: учитывает ties. O(n²), но n тут < 1000 — ок."""
    n = len(a)
    if n < 2:
        return float("nan")
    concordant = discordant = ties_a = ties_b = 0
    for i in range(n):
        for j in range(i + 1, n):
            da = a[i] - a[j]
            db = b[i] - b[j]
            if da == 0 and db == 0:
                continue
            if da == 0:
                ties_a += 1
                continue
            if db == 0:
                ties_b += 1
                continue
            if da * db > 0:
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + ties_a) * (concordant + discordant + ties_b))
    if denom <= 0:
        return float("nan")
    return (concordant - discordant) / denom


def _mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

async def run(
    profile_id: int,
    k: int,
    val_only: bool,
    weights_override: Tuple[float, float, float] | None = None,
) -> None:
    async with async_session() as session:
        pr = (await session.execute(
            select(Profile).where(Profile.id == profile_id)
        )).scalar_one_or_none()
        if not pr:
            print(f"Profile {profile_id} not found")
            return

        if weights_override is not None:
            wb, wpq, wd = weights_override
            weights_src = "override"
        else:
            wb  = float(pr.weight_beauty)
            wpq = float(pr.weight_price_quality)
            wd  = float(pr.weight_distance)
            weights_src = "profile"

        all_rows = (await session.execute(
            select(Rating.flat_id, Rating.beauty, Rating.price_quality, Rating.distance_pref,
                   Rating.created_at, Flat.price_rub)
            .join(Flat, Flat.id == Rating.flat_id)
            .where(Rating.profile_id == profile_id)
            .where(Rating.skipped.is_(False))
            .where(Rating.beauty.is_not(None))
            .where(Rating.price_quality.is_not(None))
            .where(Rating.distance_pref.is_not(None))
            .order_by(Rating.created_at.asc())
        )).all()

        if not all_rows:
            print("No fully-rated flats found")
            return

        n = len(all_rows)
        split = int(n * (1.0 - VAL_RATIO))
        train_rows = all_rows[:split]
        val_rows   = all_rows[split:]

        eval_rows = val_rows if val_only else all_rows
        label     = f"VAL ({len(val_rows)} квартир, out-of-sample)" if val_only \
                    else f"ALL ({n} квартир, in-sample)"

        flat_ids = [int(r.flat_id) for r in eval_rows]

        poi_ids = (await session.execute(
            select(ProfilePOI.poi_id).where(ProfilePOI.profile_id == profile_id)
        )).scalars().all()

        travel_rows = (await session.execute(
            select(FlatPoiTravel.flat_id, func.min(FlatPoiTravel.travel_min))
            .where(FlatPoiTravel.flat_id.in_(flat_ids))
            .where(FlatPoiTravel.poi_id.in_(list(poi_ids)))
            .where(FlatPoiTravel.travel_min < 999)
            .group_by(FlatPoiTravel.flat_id)
        )).all()
        travel_map: Dict[int, float] = {int(fid): float(t) for fid, t in travel_rows}

        # full prediction (score + per-head) для нашей модели
        model_rows = (await session.execute(
            select(
                ProfileFlatScore.flat_id,
                ProfileFlatScore.score,
                ProfileFlatScore.beauty_hat,
                ProfileFlatScore.price_quality_hat,
                ProfileFlatScore.distance_hat,
            )
            .where(ProfileFlatScore.profile_id == profile_id)
            .where(ProfileFlatScore.flat_id.in_(flat_ids))
        )).all()
        beauty_hat: Dict[int, float] = {int(r[0]): float(r[2]) for r in model_rows if r[2] is not None}
        pq_hat:     Dict[int, float] = {int(r[0]): float(r[3]) for r in model_rows if r[3] is not None}
        dist_hat:   Dict[int, float] = {int(r[0]): float(r[4]) for r in model_rows if r[4] is not None}

        # Модельный final score пересчитываем из голов с текущими весами —
        # это позволяет --weights работать без перескоринга profile_flat_scores.
        # Fallback на сохранённый score, если какая-то из голов отсутствует.
        model_map: Dict[int, float] = {}
        for r in model_rows:
            fid = int(r[0])
            bh, pqh, dh = r[2], r[3], r[4]
            if bh is not None and pqh is not None and dh is not None:
                model_map[fid] = wb * float(bh) + wpq * float(pqh) + wd * float(dh)
            elif r[1] is not None:
                model_map[fid] = float(r[1])

    # Сбор данных
    rel_scores: Dict[int, float] = {}
    y_beauty:   Dict[int, float] = {}
    y_pq:       Dict[int, float] = {}
    y_dist:     Dict[int, float] = {}
    prices:     Dict[int, float] = {}
    travels:    Dict[int, float] = {}

    for r in eval_rows:
        fid = int(r.flat_id)
        rel_scores[fid] = wb * float(r.beauty) + wpq * float(r.price_quality) + wd * float(r.distance_pref)
        y_beauty[fid] = float(r.beauty)
        y_pq[fid]     = float(r.price_quality)
        y_dist[fid]   = float(r.distance_pref)
        prices[fid]   = float(r.price_rub or 0)
        travels[fid]  = travel_map.get(fid, 999.0)

    # Релевантные = выше медианы взвешенного score по TRAIN
    train_rel_scores = [
        wb * float(r.beauty) + wpq * float(r.price_quality) + wd * float(r.distance_pref)
        for r in train_rows
    ]
    median_score = float(np.median(train_rel_scores)) if train_rel_scores else \
                   float(np.median(list(rel_scores.values())))
    relevant = {fid for fid, s in rel_scores.items() if s >= median_score}

    # Нормализация для ручной формулы (по eval-выборке)
    valid_t = [v for v in travels.values() if v < 999]
    max_p = max(prices.values()) or 1
    min_p = min(prices.values())
    max_t, min_t = (max(valid_t), min(valid_t)) if valid_t else (1, 0)

    def norm(v, lo, hi):
        return (v - lo) / (hi - lo + 1e-9)

    med_b  = float(np.median([r.beauty        for r in train_rows]))
    med_pq = float(np.median([r.price_quality for r in train_rows]))
    med_d  = float(np.median([r.distance_pref for r in train_rows]))
    median_pred = wb * med_b + wpq * med_pq + wd * med_d

    def rank_by(score_fn: Callable[[int], float]) -> Tuple[List[int], Dict[int, float]]:
        scored = {fid: score_fn(fid) for fid in flat_ids}
        ranked = sorted(flat_ids, key=lambda x: scored[x], reverse=True)
        return ranked, scored

    methods: List[Tuple[str, Callable[[int], float]]] = [
        ("1. Median baseline",  lambda _: median_pred + np.random.uniform(-1e-9, 1e-9)),
        ("2. Sort by distance", lambda fid: -travels.get(fid, 999.0)),
        ("3. Sort by price",    lambda fid: -prices[fid]),
        ("4. Ручная формула",   lambda fid: -norm(travels.get(fid, max_t), min_t, max_t)
                                            - norm(prices[fid], min_p, max_p)),
        ("5. Наша модель",      lambda fid: model_map.get(fid, 0.0)),
    ]

    # ─── Вывод ─────────────────────────────────────
    print(f"\nПрофиль #{profile_id} · {label} · k={k}")
    print(
        f"Веса ({weights_src}): beauty={wb} / pq={wpq} / dist={wd}  Σ={wb + wpq + wd:.2f}"
        + ("  ⚠️ сумма != 1.0" if abs((wb + wpq + wd) - 1.0) > 1e-3 else "")
    )
    print(f"Train: {len(train_rows)} / Val: {len(val_rows)} (split {int((1-VAL_RATIO)*100)}/{int(VAL_RATIO*100)})")
    print(f"Релевантные (score ≥ train-median {median_score:.2f}): {len(relevant)}/{len(eval_rows)} квартир в eval\n")

    # 1) Ranking-метрики
    hdr = f"{'Метод':<22} {'Hit@'+str(k):>7} {'P@'+str(k):>7} {'R@'+str(k):>7} {'NDCG@'+str(k):>9} {'MRR':>6} {'Spear':>7} {'Kend τ':>7} {'PairAcc':>8}"
    print(hdr)
    print("─" * len(hdr))
    rel_arr = np.array([rel_scores[fid] for fid in flat_ids], dtype=np.float64)
    for name, score_fn in methods:
        ranked, scored = rank_by(score_fn)
        score_arr = np.array([scored[fid] for fid in flat_ids], dtype=np.float64)
        print(
            f"{name:<22} "
            f"{hit_at_k(ranked, relevant, k):>7.3f} "
            f"{precision_at_k(ranked, relevant, k):>7.3f} "
            f"{recall_at_k(ranked, relevant, k):>7.3f} "
            f"{ndcg_at_k(ranked, rel_scores, k):>9.3f} "
            f"{mrr(ranked, relevant):>6.3f} "
            f"{_spearman(score_arr, rel_arr):>7.3f} "
            f"{_kendall_tau(score_arr, rel_arr):>7.3f} "
            f"{pairwise_accuracy(ranked, rel_scores):>8.3f}"
        )

    # 2) Per-head метрики (только наша модель — baseline'ы факторно не предсказывают)
    print()
    print("Качество per-head регрессии нашей модели (на eval-выборке):")
    print(f"{'голова':<10} {'n':>5} {'MAE':>7} {'Pearson r':>11} {'Spearman ρ':>12}")
    print("─" * 50)
    for head, y_map, p_map in [
        ("beauty",   y_beauty, beauty_hat),
        ("pq",       y_pq,     pq_hat),
        ("distance", y_dist,   dist_hat),
    ]:
        common = [fid for fid in flat_ids if fid in p_map]
        if not common:
            print(f"{head:<10} {'—':>5}")
            continue
        ya = np.array([y_map[fid] for fid in common], dtype=np.float64)
        pa = np.array([p_map[fid] for fid in common], dtype=np.float64)
        print(
            f"{head:<10} {len(common):>5} "
            f"{_mae(pa, ya):>7.3f} "
            f"{_pearson(pa, ya):>11.3f} "
            f"{_spearman(pa, ya):>12.3f}"
        )

    # 3) Final score: насколько модельный score близок к истинному взвешенному
    common_score = [fid for fid in flat_ids if fid in model_map]
    if common_score:
        ya = np.array([rel_scores[fid] for fid in common_score], dtype=np.float64)
        pa = np.array([model_map[fid] for fid in common_score], dtype=np.float64)
        print()
        print(
            f"Final score (wb·b + wpq·pq + wd·d): "
            f"MAE={_mae(pa, ya):.3f}  "
            f"Pearson={_pearson(pa, ya):.3f}  "
            f"Spearman={_spearman(pa, ya):.3f}"
        )

    print()


def _parse_weights(s: str) -> Tuple[float, float, float]:
    parts = [x.strip() for x in s.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "--weights ожидает 3 числа через запятую: 'wb,wpq,wd'"
        )
    try:
        wb, wpq, wd = (float(x) for x in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--weights: не удалось распарсить '{s}': {e}")
    return wb, wpq, wd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--all-data", action="store_true",
                   help="Считать по всем оценённым квартирам (in-sample). По умолчанию — только val.")
    p.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="Переопределить веса профиля: 'wb,wpq,wd' (например '0.55,0.15,0.30'). "
             "Без флага — веса из БД. Не требует перетренировки или перескоринга.",
    )
    args = p.parse_args()
    asyncio.run(run(
        args.profile_id,
        args.k,
        val_only=not args.all_data,
        weights_override=args.weights,
    ))


if __name__ == "__main__":
    main()
