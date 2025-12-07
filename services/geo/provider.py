from abc import ABC, abstractmethod
import random
import asyncio

class BaseGeoProvider(ABC):
    @abstractmethod
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Returns travel time in minutes"""
        pass

class MockGeoProvider(BaseGeoProvider):
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        # Simulate network delay
        await asyncio.sleep(0.05) 
        
        # Simple logic: prevent negative, just random
        # Maybe slightly depend on coordinate difference to look semi-real (optional)
        dist = abs(lat_a - lat_b) + abs(lng_a - lng_b)
        base_time = int(dist * 100) # Very rough approximation
        
        return random.randint(15, 60)

