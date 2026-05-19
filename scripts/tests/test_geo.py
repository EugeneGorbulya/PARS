import asyncio
import sys
import os
from sqlalchemy import select

# Add project root to python path
sys.path.append(os.getcwd())

from core.session import async_session
from models import User, Profile, POI, ProfilePOI
from services.geo.service import GeoEnricherService

async def main():
    async with async_session() as session:
        # 1. Create/Get User
        user_result = await session.execute(select(User).where(User.username == 'test_user'))
        user = user_result.scalar_one_or_none()
        
        if not user:
            print("Creating test user...")
            user = User(username='test_user', tg_user_id=123456789)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # 2. Create/Get POI (Kremlin)
        poi_result = await session.execute(select(POI).where(POI.user_id == user.id, POI.label == 'Work'))
        poi = poi_result.scalar_one_or_none()
        
        if not poi:
            print("Creating test POI...")
            poi = POI(user_id=user.id, label='Work', lat=55.7520, lng=37.6175)
            session.add(poi)
            await session.commit()
            await session.refresh(poi)

        # 3. Create/Get Profile
        profile_result = await session.execute(select(Profile).where(Profile.user_id == user.id, Profile.alias == 'test_profile'))
        profile = profile_result.scalar_one_or_none()
        
        if not profile:
            print("Creating test Profile...")
            profile = Profile(
                user_id=user.id, 
                alias='test_profile', 
                city='Moscow', 
                cian_filter={},
                weight_beauty=0.5,
                weight_price_quality=0.3,
                weight_distance=0.2
            )
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            
            # Link POI to Profile
            profile_poi = ProfilePOI(
                profile_id=profile.id,
                poi_id=poi.id,
                mode='masstransit',
                max_travel_min=60
            )
            session.add(profile_poi)
            await session.commit()

        print(f"User ID: {user.id}, Profile ID: {profile.id}, POI ID: {poi.id}")

        # 4. Run Enricher
        print("Running Geo Enricher...")
        enricher = GeoEnricherService(session)
        count = await enricher.enrich_profile_flats(profile.id)
        
        print(f"Enriched {count} travel times.")

if __name__ == "__main__":
    asyncio.run(main())

