import random

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists, func, desc
from sqlalchemy.orm import selectinload, aliased

from models import Flat, FlatPhoto, Profile, Rating, PhotoEmbedding, FlatPoiTravel, ProfileFlatScore

class RecommendationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_next_flat(self, user_id: int, profile_id: int):
        """
        Returns the next best flat for the user/profile.
        Conditions:
        1. Matches profile filters (price, rooms).
        2. Not rated by user in this profile.
        3. Has photos.
        4. (Optional) Has photo embeddings (meaning photos are downloaded).
        """
        # 1. Get Profile to check filters
        profile_result = await self.session.execute(select(Profile).where(Profile.id == profile_id))
        profile = profile_result.scalar_one_or_none()
        
        if not profile:
            return None

        filters = profile.cian_filter
        min_p = filters.get("min_price", 0)
        max_p = filters.get("max_price", 10000000)
        rooms = filters.get("rooms", [])

        # 2. Build Query
        # Subquery for flats already rated by this user in this profile
        rated_subquery = select(1).where(
            (Rating.user_id == user_id) & 
            (Rating.profile_id == profile_id) & 
            (Rating.flat_id == Flat.id)
        )

        # Subquery to ensure flat has downloaded photos (at least one)
        # We check if ANY photo of this flat has a corresponding PhotoEmbedding record
        has_photos_subquery = select(1).where(
            (FlatPhoto.flat_id == Flat.id) &
            exists(select(1).where(PhotoEmbedding.photo_id == FlatPhoto.id))
        )

        pfs = aliased(ProfileFlatScore)
        query = (
            select(Flat)
            .options(selectinload(Flat.photos).selectinload(FlatPhoto.embedding))
            .outerjoin(
                pfs,
                (pfs.flat_id == Flat.id) & (pfs.profile_id == profile_id),
            )
            .where(Flat.price_rub >= min_p)
            .where(Flat.price_rub <= max_p)
            .where(~exists(rated_subquery))
            .where(exists(has_photos_subquery))
        )

        if rooms:
            if 0 in rooms:
                query = query.where(Flat.rooms.in_(rooms))
            else:
                query = query.where(Flat.rooms.in_(rooms))

        eps = float(profile.epsilon_explore)
        if random.random() < eps:
            query = query.order_by(func.random()).limit(1)
        else:
            query = query.order_by(desc(pfs.score).nulls_last(), func.random()).limit(1)

        result = await self.session.execute(query)
        flat = result.scalar_one_or_none()
        
        if flat:
            # Fetch Travel Time separately (or via relationship if added)
            # Let's fetch simple travel info
            travel_res = await self.session.execute(
                select(FlatPoiTravel).where(FlatPoiTravel.flat_id == flat.id)
            )
            flat.travel_times = travel_res.scalars().all()

        return flat

    async def count_available_flats(self, user_id: int, profile_id: int) -> int:
        """
        Количество квартир, которые можно оценить без ожидания (под фильтры профиля, не оценены, с фото).
        """
        profile_result = await self.session.execute(select(Profile).where(Profile.id == profile_id))
        profile = profile_result.scalar_one_or_none()
        if not profile:
            return 0

        filters = profile.cian_filter
        min_p = filters.get("min_price", 0)
        max_p = filters.get("max_price", 10000000)
        rooms = filters.get("rooms", [])

        rated_subquery = select(1).where(
            (Rating.user_id == user_id) &
            (Rating.profile_id == profile_id) &
            (Rating.flat_id == Flat.id)
        )
        has_photos_subquery = select(1).where(
            (FlatPhoto.flat_id == Flat.id) &
            exists(select(1).where(PhotoEmbedding.photo_id == FlatPhoto.id))
        )

        query = (
            select(func.count())
            .select_from(Flat)
            .where(Flat.price_rub >= min_p)
            .where(Flat.price_rub <= max_p)
            .where(~exists(rated_subquery))
            .where(exists(has_photos_subquery))
        )
        if rooms:
            query = query.where(Flat.rooms.in_(rooms))

        result = await self.session.execute(query)
        return result.scalar() or 0

