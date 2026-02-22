from abc import ABC, abstractmethod
import random
import asyncio
import httpx
import os
import re
import json
from typing import Optional

class RateLimitExceeded(Exception):
    """Exception raised when API rate limit is exceeded"""
    pass

class BaseGeoProvider(ABC):
    @abstractmethod
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Returns travel time in minutes"""
        pass

class MockGeoProvider(BaseGeoProvider):
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        await asyncio.sleep(0.05) 
        return random.randint(15, 60)

class GoogleMapsProvider(BaseGeoProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://maps.googleapis.com/maps/api/directions/json"
        self.client = httpx.AsyncClient(timeout=10.0)
    
    def _map_mode(self, mode: str) -> str:
        mode_map = {
            'masstransit': 'transit',
            'metro': 'transit',  # Metro uses transit mode with subway preference
            'driving': 'driving',
            'walking': 'walking',
            'cycling': 'bicycling',
        }
        return mode_map.get(mode, 'driving')
    
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        travel_mode = self._map_mode(mode)
        
        params = {
            "origin": f"{lat_a},{lng_a}",
            "destination": f"{lat_b},{lng_b}",
            "mode": travel_mode,
            "key": self.api_key,
            "language": "ru",
        }
        
        if travel_mode == 'transit':
            if mode == 'metro':
                params["transit_mode"] = "subway"  # Only subway for metro mode
            else:
                params["transit_mode"] = "subway|bus|tram"  # All public transport for masstransit
        
        try:
            response = await self.client.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            status = data.get("status")
            if status == "OK" and data.get("routes"):
                duration_seconds = data["routes"][0]["legs"][0]["duration"]["value"]
                return max(1, int(duration_seconds / 60))
            elif status in ("OVER_QUERY_LIMIT", "REQUEST_DENIED"):
                # Rate limit exceeded or quota exceeded
                raise RateLimitExceeded(f"Google Maps API: {status}")
            else:
                return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Too Many Requests
                raise RateLimitExceeded("Google Maps API: HTTP 429 Too Many Requests")
            print(f"Google Maps API HTTP error: {e.response.status_code} - {e.response.text}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
        except RateLimitExceeded:
            raise
        except Exception as e:
            print(f"Google Maps API error: {e}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
    
    def _estimate_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        from math import sqrt, cos, radians, sin
        R = 6371
        dlat = radians(lat_b - lat_a)
        dlng = radians(lng_b - lng_a)
        a = sin(dlat/2)**2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(dlng/2)**2
        c = 2 * sqrt(a)
        distance_km = R * c
        
        if mode in ('masstransit', 'metro'):
            if mode == 'metro':
                walk_to_metro = 10
                walk_from_metro = 10
                metro_speed_kmh = 50
            else:
                walk_to_metro = 7
                walk_from_metro = 7
                metro_speed_kmh = 40
            metro_time = (distance_km / metro_speed_kmh) * 60
            total_time = walk_to_metro + metro_time + walk_from_metro
            return max(10, min(int(total_time), 120))
        else:
            speed_kmh = 30
            estimated_minutes = int((distance_km / speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()


class HereApiProvider(BaseGeoProvider):
    """
    HERE API provider for travel time calculation.
    
    HERE Routing API v8 supports public transit, driving, walking, and cycling.
    Requires HERE API key from https://developer.here.com/
    
    Free tier: 250,000 transactions/month
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://router.hereapi.com/v8/routes"
        self.client = httpx.AsyncClient(timeout=10.0)
    
    def _map_mode(self, mode: str) -> str:
        """Map internal mode to HERE transportMode"""
        mode_map = {
            'masstransit': 'publicTransport',
            'metro': 'publicTransport',  # HERE doesn't distinguish metro specifically
            'driving': 'car',
            'walking': 'pedestrian',
            'cycling': 'bicycle',
        }
        return mode_map.get(mode, 'car')
    
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Returns travel time in minutes"""
        transport_mode = self._map_mode(mode)
        
        params = {
            "origin": f"{lat_a},{lng_a}",
            "destination": f"{lat_b},{lng_b}",
            "transportMode": transport_mode,
            "return": "summary",
            "apiKey": self.api_key,
        }
        
        try:
            response = await self.client.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("routes") and len(data["routes"]) > 0:
                route = data["routes"][0]
                # HERE API v8 structure: routes[0].sections[].summary.duration
                sections = route.get("sections", [])
                if sections:
                    summary = sections[0].get("summary", {})
                    duration_seconds = summary.get("duration", 0)
                    if duration_seconds:
                        duration_minutes = int(duration_seconds / 60)
                        return max(1, duration_minutes)
                # Fallback: try route.summary
                route_summary = route.get("summary", {})
                duration_seconds = route_summary.get("duration", 0)
                if duration_seconds:
                    duration_minutes = int(duration_seconds / 60)
                    return max(1, duration_minutes)
            else:
                return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Too Many Requests
                raise RateLimitExceeded("HERE API: HTTP 429 Too Many Requests")
            print(f"HERE API HTTP error: {e.response.status_code} - {e.response.text}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
        except Exception as e:
            print(f"HERE API error: {e}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
    
    def _estimate_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Fallback estimation based on distance"""
        from math import sqrt, cos, radians, sin
        
        R = 6371  # Earth radius in km
        dlat = radians(lat_b - lat_a)
        dlng = radians(lng_b - lng_a)
        a = sin(dlat/2)**2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(dlng/2)**2
        c = 2 * sqrt(a)
        distance_km = R * c
        
        if mode in ('masstransit', 'metro'):
            if mode == 'metro':
                walk_to_metro = 10
                walk_from_metro = 10
                metro_speed_kmh = 50
            else:
                walk_to_metro = 7
                walk_from_metro = 7
                metro_speed_kmh = 40
            metro_time = (distance_km / metro_speed_kmh) * 60
            total_time = walk_to_metro + metro_time + walk_from_metro
            return max(10, min(int(total_time), 120))
        elif mode == 'walking':
            walk_speed_kmh = 5
            estimated_minutes = int((distance_km / walk_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        elif mode == 'cycling':
            bike_speed_kmh = 15
            estimated_minutes = int((distance_km / bike_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        else:
            speed_kmh = 30
            estimated_minutes = int((distance_km / speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()


class YandexMapsProvider(BaseGeoProvider):
    """
    Yandex Maps provider that parses the website to get travel time.
    
    Uses web scraping to extract route information from Yandex Maps.
    No API key required, but may be rate-limited by Yandex.
    """
    def __init__(self):
        self.base_url = "https://yandex.ru/maps"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/118.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://yandex.ru/maps/",
        }
        self.client = httpx.AsyncClient(timeout=15.0, headers=self.headers, follow_redirects=True)
    
    def _map_mode(self, mode: str) -> str:
        """Map internal mode to Yandex Maps route type"""
        mode_map = {
            'masstransit': 'mt',  # Общественный транспорт
            'metro': 'mt',  # Метро (используется mt, но фильтруем по типу транспорта)
            'driving': 'auto',  # Автомобиль
            'walking': 'pd',  # Пешком
            'cycling': 'bicycle',  # Велосипед
        }
        return mode_map.get(mode, 'auto')
    
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Returns travel time in minutes by parsing Yandex Maps website"""
        route_type = self._map_mode(mode)
        
        # Build URL for route calculation
        # Format: https://yandex.ru/maps/?rtext=lat1,lng1~lat2,lng2&rtt=route_type
        url = f"{self.base_url}/"
        params = {
            "rtext": f"{lat_a},{lng_a}~{lat_b},{lng_b}",
            "rtt": route_type,
        }
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            html = response.text
            
            # Try to extract route data from embedded JSON in HTML
            # Yandex Maps embeds route data in script tags or window.__INITIAL_STATE__
            duration_minutes = self._parse_route_from_html(html, mode)
            
            if duration_minutes:
                return max(1, duration_minutes)
            else:
                # Fallback to estimation
                return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RateLimitExceeded("Yandex Maps: HTTP 429 Too Many Requests")
            print(f"Yandex Maps HTTP error: {e.response.status_code} - {e.response.text[:200]}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
        except Exception as e:
            print(f"Yandex Maps parsing error: {e}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
    
    def _parse_route_from_html(self, html: str, mode: str) -> Optional[int]:
        """Parse route duration from HTML page"""
        # Method 1: Try to find JSON data in script tags
        # Look for window.__INITIAL_STATE__ or similar
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
            r'window\.__DATA__\s*=\s*({.+?});',
            r'"duration":\s*(\d+)',  # Direct duration in seconds
            r'"time":\s*(\d+)',  # Time in seconds
            r'(\d+)\s*мин',  # "XX мин" in Russian
            r'(\d+)\s*минут',  # "XX минут" in Russian
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
            if matches:
                try:
                    # Try to parse as JSON first
                    if pattern.startswith('window'):
                        json_str = matches[0]
                        data = json.loads(json_str)
                        # Navigate through JSON to find duration
                        duration = self._extract_duration_from_json(data, mode)
                        if duration:
                            return int(duration / 60)  # Convert seconds to minutes
                    elif pattern.startswith('"duration"') or pattern.startswith('"time"'):
                        # Direct duration value
                        duration_seconds = int(matches[0])
                        return int(duration_seconds / 60)
                    elif 'мин' in pattern:
                        # Already in minutes
                        return int(matches[0])
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue
        
        # Method 2: Try to find route summary in HTML
        # Look for common patterns like "XX мин" or "XX часов YY минут"
        time_patterns = [
            r'(\d+)\s*ч[а-я]*\s*(\d+)\s*мин',  # "1 час 30 мин"
            r'(\d+)\s*мин',  # "30 мин"
            r'(\d+)\s*минут',  # "30 минут"
        ]
        
        for pattern in time_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                try:
                    if isinstance(matches[0], tuple):
                        # "X часов Y минут"
                        hours, minutes = map(int, matches[0])
                        return hours * 60 + minutes
                    else:
                        # Just minutes
                        return int(matches[0])
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def _extract_duration_from_json(self, data: dict, mode: str) -> Optional[int]:
        """Recursively search for duration in JSON structure"""
        if isinstance(data, dict):
            # Check common keys
            for key in ['duration', 'time', 'travelTime', 'routeTime', 'totalTime']:
                if key in data:
                    value = data[key]
                    if isinstance(value, (int, float)):
                        return int(value)
            
            # Check route/plan structures
            if 'route' in data:
                return self._extract_duration_from_json(data['route'], mode)
            if 'plan' in data:
                return self._extract_duration_from_json(data['plan'], mode)
            if 'itineraries' in data and isinstance(data['itineraries'], list) and data['itineraries']:
                # For transit, get first itinerary
                return self._extract_duration_from_json(data['itineraries'][0], mode)
            
            # Recursively search in nested structures
            for value in data.values():
                result = self._extract_duration_from_json(value, mode)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._extract_duration_from_json(item, mode)
                if result:
                    return result
        
        return None
    
    def _estimate_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Fallback estimation based on distance"""
        from math import sqrt, cos, radians, sin
        
        R = 6371  # Earth radius in km
        dlat = radians(lat_b - lat_a)
        dlng = radians(lng_b - lng_a)
        a = sin(dlat/2)**2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(dlng/2)**2
        c = 2 * sqrt(a)
        distance_km = R * c
        
        if mode in ('masstransit', 'metro'):
            if mode == 'metro':
                walk_to_metro = 10
                walk_from_metro = 10
                metro_speed_kmh = 50
            else:
                walk_to_metro = 7
                walk_from_metro = 7
                metro_speed_kmh = 40
            metro_time = (distance_km / metro_speed_kmh) * 60
            total_time = walk_to_metro + metro_time + walk_from_metro
            return max(10, min(int(total_time), 120))
        elif mode == 'walking':
            walk_speed_kmh = 5
            estimated_minutes = int((distance_km / walk_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        elif mode == 'cycling':
            bike_speed_kmh = 15
            estimated_minutes = int((distance_km / bike_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        else:
            speed_kmh = 30
            estimated_minutes = int((distance_km / speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()


class CompositeGeoProvider(BaseGeoProvider):
    """
    Composite provider that tries Google Maps first, then falls back to HERE API
    when Google rate limits are exceeded.
    """
    def __init__(self, google_provider: GoogleMapsProvider, here_provider: HereApiProvider):
        self.google_provider = google_provider
        self.here_provider = here_provider
        self.using_here = False  # Track which provider we're currently using
    
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Try Google first, fallback to HERE on rate limit"""
        if not self.using_here:
            try:
                return await self.google_provider.calculate_travel_time(lat_a, lng_a, lat_b, lng_b, mode)
            except RateLimitExceeded as e:
                print(f"⚠️ Google Maps rate limit exceeded: {e}")
                print("🔄 Switching to HERE API...")
                self.using_here = True
                # Fall through to HERE
        
        # Use HERE API (either after switch or if already using)
        return await self.here_provider.calculate_travel_time(lat_a, lng_a, lat_b, lng_b, mode)


class OpenTripPlannerProvider(BaseGeoProvider):
    """
    OpenTripPlanner (OTP) provider for travel time calculation.
    
    OTP is an open-source multi-modal trip planner that supports public transit,
    walking, cycling, and driving. Requires a running OTP server instance.
    
    Setup:
    1. Run OTP server (usually on http://localhost:8080)
    2. Configure with GTFS data for your region
    3. Set OTP_BASE_URL environment variable (default: http://localhost:8080)
    4. Set OTP_ROUTER_ID (default: 'default')
    """
    def __init__(self, base_url: Optional[str] = None, router_id: Optional[str] = None):
        self.base_url = base_url or os.environ.get("OTP_BASE_URL", "http://localhost:8080")
        self.router_id = router_id or os.environ.get("OTP_ROUTER_ID", "default")
        self.client = httpx.AsyncClient(timeout=30.0)  # OTP can take longer
    
    def _map_mode(self, mode: str) -> str:
        """Map internal mode to OTP mode parameter"""
        mode_map = {
            'masstransit': 'TRANSIT,WALK',
            'metro': 'TRANSIT,WALK',  # Will filter for subway in response
            'driving': 'CAR',
            'walking': 'WALK',
            'cycling': 'BICYCLE',
        }
        return mode_map.get(mode, 'TRANSIT,WALK')
    
    def _itinerary_uses_subway(self, itinerary: dict) -> bool:
        """Check if itinerary uses subway/metro"""
        legs = itinerary.get("legs", [])
        for leg in legs:
            mode = leg.get("mode", "")
            # OTP uses "SUBWAY" as mode for metro/subway
            if mode == "SUBWAY":
                return True
        return False
    
    async def calculate_travel_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Returns travel time in minutes"""
        from datetime import datetime
        
        travel_mode = self._map_mode(mode)
        prefer_subway = (mode == 'metro')
        now = datetime.now()
        
        url = f"{self.base_url}/otp/routers/{self.router_id}/plan"
        params = {
            "fromPlace": f"{lat_a},{lng_a}",
            "toPlace": f"{lat_b},{lng_b}",
            "mode": travel_mode,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "arriveBy": "false",  # Leave at specified time (not arrive by)
        }
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("plan") and data["plan"].get("itineraries"):
                itineraries = data["plan"]["itineraries"]
                
                # If metro mode requested, prefer itineraries with subway
                if prefer_subway:
                    subway_itineraries = [it for it in itineraries if self._itinerary_uses_subway(it)]
                    if subway_itineraries:
                        # Use the fastest subway itinerary
                        itinerary = min(subway_itineraries, key=lambda x: x.get("duration", float('inf')))
                    else:
                        # No subway route found, use fallback
                        return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
                else:
                    # Use the first (best) itinerary
                    itinerary = itineraries[0]
                
                duration_ms = itinerary.get("duration", 0)
                duration_minutes = int(duration_ms / 1000 / 60)  # Convert ms to minutes
                return max(1, duration_minutes)
            else:
                # No route found
                return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
                
        except httpx.HTTPStatusError as e:
            print(f"OpenTripPlanner API HTTP error: {e.response.status_code} - {e.response.text}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
        except Exception as e:
            print(f"OpenTripPlanner API error: {e}")
            return self._estimate_time(lat_a, lng_a, lat_b, lng_b, mode)
    
    def _estimate_time(self, lat_a: float, lng_a: float, lat_b: float, lng_b: float, mode: str = 'masstransit') -> int:
        """Fallback estimation based on distance"""
        from math import sqrt, cos, radians, sin
        
        R = 6371  # Earth radius in km
        dlat = radians(lat_b - lat_a)
        dlng = radians(lng_b - lng_a)
        a = sin(dlat/2)**2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(dlng/2)**2
        c = 2 * sqrt(a)
        distance_km = R * c
        
        if mode == 'masstransit':
            # Approximate public transport calculation
            walk_to_metro = 7
            walk_from_metro = 7
            metro_speed_kmh = 40
            metro_time = (distance_km / metro_speed_kmh) * 60
            total_time = walk_to_metro + metro_time + walk_from_metro
            return max(10, min(int(total_time), 120))
        elif mode == 'walking':
            walk_speed_kmh = 5
            estimated_minutes = int((distance_km / walk_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        elif mode == 'cycling':
            bike_speed_kmh = 15
            estimated_minutes = int((distance_km / bike_speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
        else:
            # driving
            speed_kmh = 30
            estimated_minutes = int((distance_km / speed_kmh) * 60)
            return max(5, min(estimated_minutes, 120))
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()


def create_geo_provider() -> BaseGeoProvider:
    """
    Create geo provider based on configuration.
    
    Supports:
    - 'mock': Mock provider for testing
    - 'google': Google Maps API only
    - 'here': HERE API only
    - 'yandex': Yandex Maps (web scraping, no API key required)
    - 'auto' (recommended): Google Maps with HERE fallback on rate limits
    """
    provider_name = os.environ.get("GEO_PROVIDER", "mock")
    
    if provider_name == "mock":
        return MockGeoProvider()
    elif provider_name == "google":
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_MAPS_API_KEY not set")
        return GoogleMapsProvider(api_key)
    elif provider_name == "here":
        api_key = os.environ.get("HERE_API_KEY")
        if not api_key:
            raise ValueError("HERE_API_KEY not set")
        return HereApiProvider(api_key)
    elif provider_name == "auto":
        # Auto mode: Google with HERE fallback
        google_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        here_key = os.environ.get("HERE_API_KEY")
        
        if not google_key:
            raise ValueError("GOOGLE_MAPS_API_KEY not set (required for 'auto' mode)")
        if not here_key:
            raise ValueError("HERE_API_KEY not set (required for 'auto' mode)")
        
        return CompositeGeoProvider(
            GoogleMapsProvider(google_key),
            HereApiProvider(here_key)
        )
    elif provider_name == "yandex":
        return YandexMapsProvider()
    elif provider_name == "otp":
        return OpenTripPlannerProvider()
    else:
        # Default: try auto if keys are available, otherwise mock
        google_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        here_key = os.environ.get("HERE_API_KEY")
        
        if google_key and here_key:
            print("Using auto mode (Google + HERE fallback)")
            return CompositeGeoProvider(
                GoogleMapsProvider(google_key),
                HereApiProvider(here_key)
            )
        else:
            return MockGeoProvider()

