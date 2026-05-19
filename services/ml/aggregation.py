"""
Aggregate per-photo CLIP vectors to a single flat-level vector (default: mean).
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import FlatPhoto, PhotoClipEmbedding


async def mean_clip_vector_for_flat(session: AsyncSession, flat_id: int) -> np.ndarray | None:
    rows = await session.execute(
        select(PhotoClipEmbedding.embedding)
        .join(FlatPhoto, FlatPhoto.id == PhotoClipEmbedding.photo_id)
        .where(FlatPhoto.flat_id == flat_id)
    )
    blobs = [r[0] for r in rows.all()]
    if not blobs:
        return None
    from services.ml.encoding import bytes_to_float32_vector

    stacked = np.stack([bytes_to_float32_vector(b) for b in blobs], axis=0)
    return stacked.mean(axis=0)
