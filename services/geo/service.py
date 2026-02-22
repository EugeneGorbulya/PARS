from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from typing import List

from models import Flat, POI, FlatPoiTravel, ProfilePOI
from services.geo.provider import BaseGeoProvider, create_geo_provider

class GeoEnricherService:
    def __init__(self, session: AsyncSession, provider: BaseGeoProvider = None):
        self.session = session
        self.provider = provider or create_geo_provider()

    async def enrich_profile_flats(self, profile_id: int, limit: int = 100):
        """
        Calculates travel time for flats relevant to a specific profile.
        1. Get POIs associated with this profile.
        2. Find flats that don't have travel time calculated for these POIs.
        3. Calculate and save.
        """
        # 1. Get Profile POIs
        stmt_pois = (
            select(POI, ProfilePOI.mode)
            .join(ProfilePOI, POI.id == ProfilePOI.poi_id)
            .where(ProfilePOI.profile_id == profile_id)
        )
        pois_result = await self.session.execute(stmt_pois)
        pois_data = pois_result.all() # List of (POI, mode)

        if not pois_data:
            print(f"No POIs found for profile {profile_id}")
            return 0

        total_calculated = 0

        for poi, mode in pois_data:
            # 2. Find flats missing this POI calculation
            # Left join flat_poi_travel on flat_id AND poi_id AND mode
            stmt_flats = (
                select(Flat)
                .outerjoin(
                    FlatPoiTravel, 
                    (Flat.id == FlatPoiTravel.flat_id) & 
                    (FlatPoiTravel.poi_id == poi.id) & 
                    (FlatPoiTravel.mode == mode)
                )
                .where(FlatPoiTravel.flat_id == None) # Missing records
                .where(Flat.lat != None) # Only flats with coordinates
                .limit(limit)
            )
            
            result_flats = await self.session.execute(stmt_flats)
            flats = result_flats.scalars().all()
            
            print(f"Found {len(flats)} flats to enrich for POI '{poi.label}' ({mode})")

            # 3. Calculate
            for flat in flats:
                minutes = await self.provider.calculate_travel_time(
                    float(flat.lat), float(flat.lng),
                    float(poi.lat), float(poi.lng),
                    mode
                )
                
                # 4. Save
                # Use Insert with ignore on conflict just in case
                stmt_insert = insert(FlatPoiTravel).values(
                    flat_id=flat.id,
                    poi_id=poi.id,
                    mode=mode,
                    travel_min=minutes
                )
                stmt_insert = stmt_insert.on_conflict_do_update(
                    index_elements=['flat_id', 'poi_id', 'mode'],
                    set_={'travel_min': minutes}
                )
                await self.session.execute(stmt_insert)
                total_calculated += 1
        
        await self.session.commit()
        return total_calculated

