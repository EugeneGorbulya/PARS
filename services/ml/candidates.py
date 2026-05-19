"""Flat id enumeration for scoring: profile CIAN filters + at least one CLIP embedding."""
from __future__ import annotations

from typing import List

from sqlalchemy import select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from models import Flat, Profile, FlatPhoto, PhotoClipEmbedding


async def flat_ids_for_profile_filters(session: AsyncSession, profile_id: int) -> List[int]:
    pr = await session.execute(select(Profile).where(Profile.id == profile_id))
    profile = pr.scalar_one_or_none()
    if not profile:
        return []
    filters = profile.cian_filter or {}
    min_p = filters.get("min_price", 0)
    max_p = filters.get("max_price", 100000000)
    rooms = filters.get("rooms", [])

    has_clip = exists(
        select(1)
        .select_from(FlatPhoto)
        .join(PhotoClipEmbedding, PhotoClipEmbedding.photo_id == FlatPhoto.id)
        .where(FlatPhoto.flat_id == Flat.id)
    )

    q = (
        select(Flat.id)
        .where(
            Flat.active.is_(True),
            Flat.price_rub >= min_p,
            Flat.price_rub <= max_p,
            has_clip,
        )
        .order_by(Flat.id)
    )
    if rooms:
        q = q.where(Flat.rooms.in_(rooms))
    r = await session.execute(q)
    return [int(x[0]) for x in r.all()]
