"""
Async batch: photos with raw_image in photo_embeddings but no photo_clip_embeddings → CLIP → insert.
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple

from sqlalchemy import select, and_, not_, exists
from sqlalchemy.ext.asyncio import AsyncSession

from models import FlatPhoto, PhotoEmbedding, PhotoClipEmbedding
from services.ml.clip_embedder import ClipImageEmbedder
from services.ml.encoding import float32_vector_to_bytes
from services.s3.client import S3Client


async def fetch_photo_batch(session: AsyncSession, limit: int) -> List[Tuple[int, int, str]]:
    """
    Returns list of (photo_id, flat_id, s3_uri) for photos ready for CLIP.
    """
    has_clip = exists().where(PhotoClipEmbedding.photo_id == FlatPhoto.id)
    stmt = (
        select(FlatPhoto.id, FlatPhoto.flat_id, PhotoEmbedding.storage_uri)
        .join(PhotoEmbedding, PhotoEmbedding.photo_id == FlatPhoto.id)
        .where(
            and_(
                PhotoEmbedding.model == "raw_image",
                PhotoEmbedding.dim == 0,
                not_(has_clip),
            )
        )
        .order_by(FlatPhoto.id)
        .limit(limit)
    )
    res = await session.execute(stmt)
    return [(int(r[0]), int(r[1]), str(r[2])) for r in res.all()]


async def compute_clip_for_batch(
    session: AsyncSession,
    s3: S3Client,
    embedder: ClipImageEmbedder,
    items: List[Tuple[int, int, str]],
) -> int:
    if not items:
        return 0
    bodies: List[bytes] = []
    meta: List[Tuple[int, int]] = []
    for photo_id, flat_id, uri in items:
        try:
            data = await s3.download_bytes(s3_uri=uri)
            bodies.append(data)
            meta.append((photo_id, flat_id))
        except Exception:
            continue
    if not bodies:
        return 0

    def _encode():
        return embedder.encode_jpeg_bytes_batch(bodies)

    vecs = await asyncio.to_thread(_encode)
    dim = int(vecs.shape[1])
    written = 0
    for i, (photo_id, flat_id) in enumerate(meta):
        blob = float32_vector_to_bytes(vecs[i])
        row = PhotoClipEmbedding(
            photo_id=photo_id,
            flat_id=flat_id,
            model=embedder.model_tag,
            dim=dim,
            embedding=blob,
        )
        session.add(row)
        written += 1
    await session.commit()
    return written
