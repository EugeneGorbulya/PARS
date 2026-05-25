"""
Честная оценка качества рекомендера: метрики ранжирования top-K,
ранговые корреляции и качество per-head регрессии — для нашей модели
и нескольких baseline-методов.

  1. Median baseline      — всегда предсказывает медиану train
  2. Sort by distance     — ниже travel_min = выше место
  3. Sort by price        — ниже цена = выше место
  4. Ручная формула       — −travel_norm − price_norm  (normalizer fitted on TRAIN)
  5. Linear regression    — OLS(mean_CLIP + 6 tab → final weighted target), train-only fit
  6. Stage A only         — наша модель с последним Stage A snapshot (без Stage B)
  7. Наша модель          — score из profile_flat_scores (Stage A + Stage B)

Last two (--extra-baselines) require torch + scikit-learn-free in-script OLS.
Бутстрап-доверительные интервалы (--bootstrap N) и альтернативные определения
релевантности (--relevance median|top20|ge4) включаются отдельными флагами.

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

  # дополнительные ML-baseline'ы и ablation
    python3 -m scripts.eval.eval_baselines --profile-id 2 --extra-baselines

  # bootstrap CI
    python3 -m scripts.eval.eval_baselines --profile-id 2 --bootstrap 1000

  # alternative relevance threshold
    python3 -m scripts.eval.eval_baselines --profile-id 2 --relevance top20
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
from models import Flat, FlatPoiTravel, ModelSnapshot, Profile, ProfileFlatScore, ProfilePOI, Rating
from services.ml.config import VAL_RATIO
from services.ml.dataset import _mean_clip_by_flat, _tabular_row


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
# Extra baselines: Linear Regression + Stage A only
# ──────────────────────────────────────────────

async def _build_features(
    session,
    profile_id: int,
    flat_ids_in_order: List[int],
) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """
    Возвращает (CLIP-mean [n,512], tab [n,6], ids) для указанного списка flat_ids.
    Квартиры без CLIP-эмбеддингов или без табличных данных выкидываются.
    Порядок сохраняется относительно входного.
    """
    if not flat_ids_in_order:
        return np.zeros((0, 512), np.float32), np.zeros((0, 6), np.float32), []
    poi_ids = (await session.execute(
        select(ProfilePOI.poi_id).where(ProfilePOI.profile_id == profile_id)
    )).scalars().all()
    travel_rows = (await session.execute(
        select(FlatPoiTravel.flat_id, func.min(FlatPoiTravel.travel_min))
        .where(FlatPoiTravel.flat_id.in_(flat_ids_in_order))
        .where(FlatPoiTravel.poi_id.in_(list(poi_ids)))
        .group_by(FlatPoiTravel.flat_id)
    )).all()
    travel_map = {int(fid): float(t) for fid, t in travel_rows if t is not None}
    clip_map = await _mean_clip_by_flat(session, flat_ids_in_order)
    fr = await session.execute(select(Flat).where(Flat.id.in_(flat_ids_in_order)))
    flats = {f.id: f for f in fr.scalars().all()}

    clip_rows, tab_rows, kept = [], [], []
    for fid in flat_ids_in_order:
        f = flats.get(fid)
        v = clip_map.get(fid)
        if f is None or v is None:
            continue
        clip_rows.append(v.astype(np.float32))
        tab_rows.append(_tabular_row(f, travel_map.get(fid, 999.0)))
        kept.append(fid)
    if not clip_rows:
        return np.zeros((0, 512), np.float32), np.zeros((0, 6), np.float32), []
    return np.stack(clip_rows, axis=0), np.stack(tab_rows, axis=0), kept


def _ols_fit_predict(
    X_tr: np.ndarray, y_tr: np.ndarray, X_val: np.ndarray
) -> np.ndarray:
    """
    Простейший OLS через np.linalg.lstsq с интерсептом.
    Возвращает предсказания на X_val. Никаких внешних зависимостей.
    """
    Xb_tr = np.concatenate([X_tr, np.ones((X_tr.shape[0], 1), dtype=X_tr.dtype)], axis=1)
    Xb_val = np.concatenate([X_val, np.ones((X_val.shape[0], 1), dtype=X_val.dtype)], axis=1)
    w, *_ = np.linalg.lstsq(Xb_tr, y_tr, rcond=None)
    return Xb_val @ w


async def _stage_a_only_predictions(
    session,
    profile_id: int,
    flat_ids_in_order: List[int],
    weights: Tuple[float, float, float],
) -> Dict[int, float]:
    """
    Загружает последний snapshot с head_type='PreferenceMLP_stage_a' (если есть)
    и прогоняет на указанных квартирах. Возвращает {flat_id: weighted_final_score}.
    Если Stage A snapshot отсутствует — возвращает пустой словарь (тогда метод
    просто не появится в выводе).
    """
    # Импортируем torch / services тут, чтобы скрипт не падал, если torch не нужен
    import torch  # noqa: F401
    from services.ml.inference import (
        build_model_from_package,
        load_checkpoint_bytes,
        predict_batch,
    )
    from services.s3.client import S3Client

    snap_r = await session.execute(
        select(ModelSnapshot)
        .where(ModelSnapshot.profile_id == profile_id)
        .where(ModelSnapshot.head_type == "PreferenceMLP_stage_a")
        .order_by(ModelSnapshot.created_at.desc())
        .limit(1)
    )
    snap = snap_r.scalar_one_or_none()
    if not snap:
        return {}

    s3 = S3Client()
    raw = await s3.download_bytes(s3_uri=snap.storage_uri)
    pkg = load_checkpoint_bytes(raw)
    model, scaler = build_model_from_package(pkg)

    clip, tab, kept = await _build_features(session, profile_id, flat_ids_in_order)
    if not kept:
        return {}
    bh, pqh, dh = predict_batch(model, scaler, clip, tab, "cpu")
    wb, wpq, wd = weights
    out: Dict[int, float] = {}
    for j, fid in enumerate(kept):
        out[int(fid)] = wb * float(bh[j]) + wpq * float(pqh[j]) + wd * float(dh[j])
    return out


# ──────────────────────────────────────────────
# Bootstrap CI for ranking metrics
# ──────────────────────────────────────────────

def _bootstrap_method_metrics(
    flat_ids: List[int],
    scored: Dict[int, float],
    rel_scores: Dict[int, float],
    relevant: set,
    *,
    k: int,
    n_iters: int,
    seed: int = 0,
) -> Dict[str, Tuple[float, float, float]]:
    """
    Bootstrap по val-flats (ресэмпл с возвращением). На каждой итерации:
      1) сэмплируем n_iters_per_resample = len(flat_ids) flat-ов с возвращением,
      2) пересортируем по predicted-score,
      3) пересчитываем P@K / NDCG@K / PairAcc.
    Возвращает {'p@k': (mean, lo, hi), 'ndcg@k': ..., 'pairacc': ...}.
    """
    rng = np.random.default_rng(seed)
    n = len(flat_ids)
    if n == 0 or n_iters <= 0:
        nan = (float("nan"), float("nan"), float("nan"))
        return {"p@k": nan, "ndcg@k": nan, "pairacc": nan}

    samples_p, samples_n, samples_pa = [], [], []
    for _ in range(n_iters):
        idxs = rng.integers(0, n, size=n)
        sampled = [flat_ids[i] for i in idxs]
        # local view: только сэмплированные flat_id, c множественностью
        local_scored = sampled
        # сортировка по predicted score (стабильна по индексу)
        ranked = sorted(
            range(len(local_scored)),
            key=lambda j: scored.get(local_scored[j], 0.0),
            reverse=True,
        )
        ranked_ids = [local_scored[j] for j in ranked]

        # P@K и NDCG@K считаем напрямую (используют relevant и rel_scores
        # по реальным id; повторяющиеся id просто учитываются несколько раз).
        top = ranked_ids[:k]
        if top:
            p = sum(1 for fid in top if fid in relevant) / min(k, len(top))
        else:
            p = 0.0
        samples_p.append(p)

        dcg = sum(
            rel_scores.get(fid, 0.0) / math.log2(t + 2)
            for t, fid in enumerate(top)
        )
        # IDCG: лучшие k значений из rel_scores ПО САМПЛУ (а не глобально)
        sorted_rels = sorted(
            (rel_scores.get(fid, 0.0) for fid in sampled), reverse=True
        )[:k]
        idcg = sum(v / math.log2(t + 2) for t, v in enumerate(sorted_rels))
        samples_n.append(dcg / idcg if idcg > 0 else 0.0)

        # PairAcc по уникальным id в сэмпле, в порядке ranked_ids
        seen = set()
        uniq_ranked = []
        for fid in ranked_ids:
            if fid in seen:
                continue
            seen.add(fid)
            if fid in rel_scores:
                uniq_ranked.append(fid)
        samples_pa.append(pairwise_accuracy(uniq_ranked, rel_scores))

    def pack(arr):
        a = np.array(arr, dtype=np.float64)
        return float(a.mean()), float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))

    return {
        "p@k":    pack(samples_p),
        "ndcg@k": pack(samples_n),
        "pairacc": pack(samples_pa),
    }


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

async def run(
    profile_id: int,
    k: int,
    val_only: bool,
    weights_override: Tuple[float, float, float] | None = None,
    *,
    extra_baselines: bool = False,
    bootstrap: int = 0,
    relevance_mode: str = "median",
) -> None:
    np.random.seed(0)
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
        train_flat_id_list = [int(r.flat_id) for r in train_rows]
        # Все id, которые могут понадобиться: eval (для метрик), train (для
        # extra-baselines и нормализаторов ручной формулы).
        all_needed_flat_ids = list({*flat_ids, *train_flat_id_list})

        poi_ids = (await session.execute(
            select(ProfilePOI.poi_id).where(ProfilePOI.profile_id == profile_id)
        )).scalars().all()

        travel_rows = (await session.execute(
            select(FlatPoiTravel.flat_id, func.min(FlatPoiTravel.travel_min))
            .where(FlatPoiTravel.flat_id.in_(all_needed_flat_ids))
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

    # Релевантные: режим выбирается флагом --relevance.
    # median  — score ≥ train-median (стандартный)
    # top20   — топ-20% val по true score
    # ge4     — true взвешенный score >= 4 на шкале 1-5
    train_rel_scores = [
        wb * float(r.beauty) + wpq * float(r.price_quality) + wd * float(r.distance_pref)
        for r in train_rows
    ]
    median_score = float(np.median(train_rel_scores)) if train_rel_scores else \
                   float(np.median(list(rel_scores.values())))
    if relevance_mode == "median":
        relevant = {fid for fid, s in rel_scores.items() if s >= median_score}
        rel_label = f"train-median {median_score:.2f}"
    elif relevance_mode == "top20":
        vals = sorted(rel_scores.values(), reverse=True)
        cutoff_n = max(1, int(round(0.20 * len(vals))))
        cutoff = vals[cutoff_n - 1] if vals else float("inf")
        relevant = {fid for fid, s in rel_scores.items() if s >= cutoff}
        rel_label = f"top-20% (score ≥ {cutoff:.2f})"
    elif relevance_mode == "ge4":
        relevant = {fid for fid, s in rel_scores.items() if s >= 4.0}
        rel_label = "score ≥ 4.0"
    else:
        raise ValueError(f"Unknown relevance mode: {relevance_mode}")

    # Нормализация для ручной формулы: статистики считаются ТОЛЬКО на TRAIN
    # (раньше использовалась eval-выборка — формальная утечка через нормализатор).
    train_prices = [float(r.price_rub or 0) for r in train_rows]
    train_travels = [travel_map[int(r.flat_id)] for r in train_rows if int(r.flat_id) in travel_map]
    max_p = max(train_prices) if train_prices else 1
    min_p = min(train_prices) if train_prices else 0
    max_t, min_t = (max(train_travels), min(train_travels)) if train_travels else (1, 0)

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
    ]

    # ── Extra ML baselines / Stage A ablation (только если включено явно) ──
    linreg_map: Dict[int, float] = {}
    stage_a_map: Dict[int, float] = {}
    if extra_baselines:
        async with async_session() as s2:
            # 1. Linear regression on (mean_CLIP[512] + 6 tab) → final weighted target.
            tr_clip, tr_tab, tr_kept = await _build_features(s2, profile_id, train_flat_id_list)
            ev_clip, ev_tab, ev_kept = await _build_features(s2, profile_id, flat_ids)
            if len(tr_kept) and len(ev_kept):
                # y_tr пересобираем по совпадению id, чтобы порядок соответствовал
                # tr_kept (квартиры без CLIP/tab выкидываются _build_features'ом).
                rating_by_fid = {int(r.flat_id): r for r in train_rows}
                y_tr = np.array(
                    [
                        wb * float(rating_by_fid[fid].beauty)
                        + wpq * float(rating_by_fid[fid].price_quality)
                        + wd * float(rating_by_fid[fid].distance_pref)
                        for fid in tr_kept
                    ],
                    dtype=np.float64,
                )
                X_tr = np.concatenate([tr_clip, tr_tab], axis=1).astype(np.float64)
                X_val = np.concatenate([ev_clip, ev_tab], axis=1).astype(np.float64)
                y_hat_val = _ols_fit_predict(X_tr, y_tr, X_val)
                linreg_map = {int(fid): float(y) for fid, y in zip(ev_kept, y_hat_val)}

            # 2. Stage A-only ablation (загружаем последний stage_a snapshot).
            stage_a_map = await _stage_a_only_predictions(
                s2, profile_id, flat_ids, (wb, wpq, wd)
            )

        if linreg_map:
            methods.append(("5. Linear regression",  lambda fid: linreg_map.get(fid, 0.0)))
        if stage_a_map:
            methods.append(("6. Stage A only",       lambda fid: stage_a_map.get(fid, 0.0)))

    methods.append(("7. Наша модель (A+B)" if extra_baselines else "5. Наша модель",
                    lambda fid: model_map.get(fid, 0.0)))

    # ─── Вывод ─────────────────────────────────────
    print(f"\nПрофиль #{profile_id} · {label} · k={k}")
    print(
        f"Веса ({weights_src}): beauty={wb} / pq={wpq} / dist={wd}  Σ={wb + wpq + wd:.2f}"
        + ("  ⚠️ сумма != 1.0" if abs((wb + wpq + wd) - 1.0) > 1e-3 else "")
    )
    print(f"Train: {len(train_rows)} / Val: {len(val_rows)} (split {int((1-VAL_RATIO)*100)}/{int(VAL_RATIO*100)})")
    print(f"Релевантность [{relevance_mode}]: {rel_label} → {len(relevant)}/{len(eval_rows)} квартир в eval\n")

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

    # 1a) Bootstrap 95% CI для трёх ключевых метрик
    if bootstrap > 0:
        print()
        print(f"Bootstrap 95% CI (n_iters={bootstrap}, ресэмпл val-flats с возвращением):")
        hdr2 = f"{'Метод':<22} {'P@'+str(k)+' [95% CI]':>22} {'NDCG@'+str(k)+' [95% CI]':>24} {'PairAcc [95% CI]':>22}"
        print(hdr2)
        print("─" * len(hdr2))
        for name, score_fn in methods:
            _, scored_method = rank_by(score_fn)
            bs = _bootstrap_method_metrics(
                flat_ids, scored_method, rel_scores, relevant,
                k=k, n_iters=bootstrap, seed=0,
            )
            mp, lop, hip = bs["p@k"]
            mn, lon, hin = bs["ndcg@k"]
            ma, loa, hia = bs["pairacc"]
            print(
                f"{name:<22} "
                f"{mp:.3f} [{lop:.3f},{hip:.3f}]  "
                f"{mn:.3f} [{lon:.3f},{hin:.3f}]  "
                f"{ma:.3f} [{loa:.3f},{hia:.3f}]"
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
    p.add_argument(
        "--extra-baselines",
        action="store_true",
        help="Включить дополнительные ML-baseline'ы: Linear Regression (OLS на mean_CLIP+tab) "
             "и Stage A only (наша модель без Stage B). Stage A-only требует наличия snapshot'а "
             "с head_type='PreferenceMLP_stage_a'.",
    )
    p.add_argument(
        "--bootstrap",
        type=int,
        default=0,
        help="Число bootstrap-ресэмплов для 95% CI на P@K / NDCG@K / PairAcc. 0 — отключено.",
    )
    p.add_argument(
        "--relevance",
        choices=["median", "top20", "ge4"],
        default="median",
        help="Определение релевантности для top-K метрик. "
             "median (default): y ≥ train-median; "
             "top20: верхние 20%% val по true score; "
             "ge4: y ≥ 4.0 на шкале 1-5.",
    )
    args = p.parse_args()
    asyncio.run(run(
        args.profile_id,
        args.k,
        val_only=not args.all_data,
        weights_override=args.weights,
        extra_baselines=args.extra_baselines,
        bootstrap=args.bootstrap,
        relevance_mode=args.relevance,
    ))


if __name__ == "__main__":
    main()
