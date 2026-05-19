"""Stage B: pairwise comparisons with flat features for logistic/BT-style loss."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models import PairwiseRating, Flat
from services.ml.config import CLIP_IMAGE_DIM, SENTINEL_TRAVEL_MIN
from services.ml.dataset import (
    _mean_clip_by_flat,
    _min_travel_by_flat,
    _tabular_row,
    train_flat_ids,
)


FACTOR_TO_IDX = {"beauty": 0, "price_quality": 1, "distance_pref": 2, "distance": 2}


@dataclass
class PairwiseBundle:
    clip_a: np.ndarray
    tab_a: np.ndarray
    clip_b: np.ndarray
    tab_b: np.ndarray
    factor_idx: np.ndarray
    prefer_a: np.ndarray  # True if preferred_flat_id == flat_a_id


async def load_pairwise_bundle(session: AsyncSession, profile_id: int) -> PairwiseBundle:
    Fa = aliased(Flat)
    Fb = aliased(Flat)
    stmt = (
        select(PairwiseRating, Fa, Fb)
        .join(Fa, Fa.id == PairwiseRating.flat_a_id)
        .join(Fb, Fb.id == PairwiseRating.flat_b_id)
        .where(PairwiseRating.profile_id == profile_id)
        .order_by(PairwiseRating.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    zimg = np.zeros((0, CLIP_IMAGE_DIM), np.float32)
    ztab = np.zeros((0, 6), np.float32)
    if not rows:
        return PairwiseBundle(zimg, ztab, zimg, ztab, np.zeros((0,), np.int64), np.zeros((0,), bool))

    # Train/val isolation: a pair is allowed into Stage B only if BOTH flats
    # belong to the chronological train split. Without this, a duel that touches
    # a val flat would leak its label into Stage B and inflate eval metrics.
    train_ids = await train_flat_ids(session, profile_id)

    flat_ids = list({fa.id for _, fa, _ in rows} | {fb.id for _, _, fb in rows})
    clip_map = await _mean_clip_by_flat(session, flat_ids)
    travel_map = await _min_travel_by_flat(session, profile_id, flat_ids)

    ca, ta, cb, tb, fi, pref = [], [], [], [], [], []
    for pr, fa, fb in rows:
        if pr.factor not in FACTOR_TO_IDX:
            continue
        if fa.id not in train_ids or fb.id not in train_ids:
            continue
        va = clip_map.get(fa.id)
        vb = clip_map.get(fb.id)
        if va is None or vb is None:
            continue
        ca.append(va.astype(np.float32))
        cb.append(vb.astype(np.float32))
        ta.append(_tabular_row(fa, travel_map.get(fa.id, SENTINEL_TRAVEL_MIN)))
        tb.append(_tabular_row(fb, travel_map.get(fb.id, SENTINEL_TRAVEL_MIN)))
        fi.append(FACTOR_TO_IDX[pr.factor])
        pref.append(pr.preferred_flat_id == fa.id)

    if not ca:
        return PairwiseBundle(zimg, ztab, zimg, ztab, np.zeros((0,), np.int64), np.zeros((0,), bool))

    return PairwiseBundle(
        np.stack(ca, axis=0),
        np.stack(ta, axis=0),
        np.stack(cb, axis=0),
        np.stack(tb, axis=0),
        np.array(fi, dtype=np.int64),
        np.array(pref, dtype=bool),
    )
