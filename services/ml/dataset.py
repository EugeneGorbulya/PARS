"""
Stage A: ratings joined with flats, mean CLIP vector per flat, min travel to profile POIs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Rating,
    Flat,
    ProfilePOI,
    FlatPoiTravel,
    FlatPhoto,
    PhotoClipEmbedding,
)
from services.ml.config import (
    CLIP_IMAGE_DIM,
    SENTINEL_TRAVEL_MIN,
    TABULAR_FEATURE_KEYS,
    VAL_RATIO,
)
from services.ml.encoding import bytes_to_float32_vector


def _log1p(x: float) -> float:
    return float(math.log1p(max(x, 0.0)))


async def _profile_poi_ids(session: AsyncSession, profile_id: int) -> List[int]:
    r = await session.execute(select(ProfilePOI.poi_id).where(ProfilePOI.profile_id == profile_id))
    return [int(x[0]) for x in r.all()]


async def _min_travel_by_flat(
    session: AsyncSession, profile_id: int, flat_ids: Sequence[int]
) -> Dict[int, float]:
    if not flat_ids:
        return {}
    poi_ids = await _profile_poi_ids(session, profile_id)
    if not poi_ids:
        return {fid: SENTINEL_TRAVEL_MIN for fid in flat_ids}
    stmt = (
        select(FlatPoiTravel.flat_id, func.min(FlatPoiTravel.travel_min))
        .where(
            FlatPoiTravel.flat_id.in_(flat_ids),
            FlatPoiTravel.poi_id.in_(poi_ids),
        )
        .group_by(FlatPoiTravel.flat_id)
    )
    rows = await session.execute(stmt)
    out = {int(fid): SENTINEL_TRAVEL_MIN for fid in flat_ids}
    for fid, tmin in rows.all():
        if tmin is not None:
            out[int(fid)] = float(tmin)
    return out


async def train_flat_ids(
    session: AsyncSession,
    profile_id: int,
    *,
    val_ratio: float = VAL_RATIO,
) -> set[int]:
    """
    Возвращает множество flat_id, относящихся к train-части того же временного
    сплита, что использует load_stage_a_bundle (первые 1-val_ratio оценок
    по created_at, без skipped и без полностью-None строк).

    Нужно для синтеза пар и любых внешних шагов, которые не должны видеть
    val-квартиры (иначе утечка лейблов в Stage B / в любые downstream-метрики).
    """
    rows = (
        await session.execute(
            select(Rating.flat_id)
            .where(Rating.profile_id == profile_id)
            .where(Rating.skipped.is_(False))
            .where(
                (Rating.beauty.is_not(None))
                | (Rating.price_quality.is_not(None))
                | (Rating.distance_pref.is_not(None))
            )
            .order_by(Rating.created_at.asc())
        )
    ).scalars().all()
    n = len(rows)
    if n == 0:
        return set()
    if val_ratio <= 0 or n < 2:
        return {int(x) for x in rows}
    split = int(n * (1.0 - val_ratio))
    split = min(max(split, 1), n - 1)
    return {int(x) for x in rows[:split]}


async def _mean_clip_by_flat(
    session: AsyncSession, flat_ids: Sequence[int]
) -> Dict[int, np.ndarray]:
    if not flat_ids:
        return {}
    stmt = (
        select(FlatPhoto.flat_id, PhotoClipEmbedding.embedding)
        .join(PhotoClipEmbedding, PhotoClipEmbedding.photo_id == FlatPhoto.id)
        .where(FlatPhoto.flat_id.in_(flat_ids))
    )
    rows = await session.execute(stmt)
    buckets: Dict[int, List[np.ndarray]] = {int(fid): [] for fid in flat_ids}
    for fid, blob in rows.all():
        buckets[int(fid)].append(bytes_to_float32_vector(blob))
    out: Dict[int, np.ndarray] = {}
    for fid, vecs in buckets.items():
        if vecs:
            out[fid] = np.stack(vecs, axis=0).mean(axis=0)
    return out


def _tabular_row(flat: Flat, travel_min: float) -> np.ndarray:
    price = float(flat.price_rub or 0)
    area = float(flat.area_sqm or 0.0)
    rooms = float(flat.rooms if flat.rooms is not None else -1.0)
    fl = float(flat.floor if flat.floor is not None else -1.0)
    ft = float(flat.floors_total if flat.floors_total is not None else -1.0)
    return np.array(
        [
            _log1p(price),
            area,
            rooms,
            fl,
            ft,
            _log1p(travel_min),
        ],
        dtype=np.float32,
    )


@dataclass
class StageABundle:
    clip: np.ndarray
    tab: np.ndarray
    y_beauty: np.ndarray
    y_pq: np.ndarray
    y_dist: np.ndarray
    mask_beauty: np.ndarray
    mask_pq: np.ndarray
    mask_dist: np.ndarray
    meta: List[Dict[str, Any]]


async def load_stage_a_bundle(
    session: AsyncSession,
    profile_id: int,
    *,
    val_ratio: float = VAL_RATIO,
) -> Tuple[StageABundle, StageABundle]:
    stmt = (
        select(Rating, Flat)
        .join(Flat, Flat.id == Rating.flat_id)
        .where(
            Rating.profile_id == profile_id,
            Rating.skipped.is_(False),
        )
        .order_by(Rating.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    kept: List[Tuple[Rating, Flat]] = []
    for rating, flat in rows:
        if rating.beauty is None and rating.price_quality is None and rating.distance_pref is None:
            continue
        kept.append((rating, flat))
    if not kept:
        empty = StageABundle(
            np.zeros((0, CLIP_IMAGE_DIM), np.float32),
            np.zeros((0, len(TABULAR_FEATURE_KEYS)), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            [],
        )
        return empty, empty

    flat_ids = list({f.id for _, f in kept})
    travel_map = await _min_travel_by_flat(session, profile_id, flat_ids)
    clip_map = await _mean_clip_by_flat(session, flat_ids)

    clip_rows: List[np.ndarray] = []
    tab_rows: List[np.ndarray] = []
    yb, ypq, yd = [], [], []
    mb, mpq, md = [], [], []
    meta: List[Dict[str, Any]] = []
    for rating, flat in kept:
        vec = clip_map.get(flat.id)
        if vec is None:
            continue
        tmin = travel_map.get(flat.id, SENTINEL_TRAVEL_MIN)
        clip_rows.append(vec.astype(np.float32))
        tab_rows.append(_tabular_row(flat, tmin))
        yb.append(float(rating.beauty) if rating.beauty is not None else 0.0)
        ypq.append(float(rating.price_quality) if rating.price_quality is not None else 0.0)
        yd.append(float(rating.distance_pref) if rating.distance_pref is not None else 0.0)
        mb.append(rating.beauty is not None)
        mpq.append(rating.price_quality is not None)
        md.append(rating.distance_pref is not None)
        meta.append({"rating_id": rating.id, "flat_id": flat.id})

    if not clip_rows:
        empty = StageABundle(
            np.zeros((0, CLIP_IMAGE_DIM), np.float32),
            np.zeros((0, len(TABULAR_FEATURE_KEYS)), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            [],
        )
        return empty, empty

    clip = np.stack(clip_rows, axis=0)
    tab = np.stack(tab_rows, axis=0)
    bundle = StageABundle(
        clip,
        tab,
        np.array(yb, dtype=np.float32),
        np.array(ypq, dtype=np.float32),
        np.array(yd, dtype=np.float32),
        np.array(mb, dtype=bool),
        np.array(mpq, dtype=bool),
        np.array(md, dtype=bool),
        meta,
    )
    n = clip.shape[0]
    if val_ratio <= 0 or n < 2:
        empty_val = StageABundle(
            np.zeros((0, CLIP_IMAGE_DIM), np.float32),
            np.zeros((0, len(TABULAR_FEATURE_KEYS)), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), np.float32),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            np.zeros((0,), bool),
            [],
        )
        return bundle, empty_val

    split = int(n * (1.0 - val_ratio))
    split = min(max(split, 1), n - 1)

    def split_at(b: StageABundle, a: int) -> Tuple[StageABundle, StageABundle]:
        tr = StageABundle(
            b.clip[:a],
            b.tab[:a],
            b.y_beauty[:a],
            b.y_pq[:a],
            b.y_dist[:a],
            b.mask_beauty[:a],
            b.mask_pq[:a],
            b.mask_dist[:a],
            b.meta[:a],
        )
        va = StageABundle(
            b.clip[a:],
            b.tab[a:],
            b.y_beauty[a:],
            b.y_pq[a:],
            b.y_dist[a:],
            b.mask_beauty[a:],
            b.mask_pq[a:],
            b.mask_dist[a:],
            b.meta[a:],
        )
        return tr, va

    return split_at(bundle, split)
