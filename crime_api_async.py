"""
Async Crime API Module
Handles asynchronous requests to UK Police API with rate limiting and error handling
"""

import aiohttp
import asyncio
from typing import List, Dict, Any, Optional
from collections import Counter
import math

# Rate limiting configuration
RATE_LIMIT = 15  # requests per second
MAX_CONCURRENT = 10  # max concurrent requests
REQUEST_TIMEOUT = 15  # seconds

# Semaphore for limiting concurrent requests
SEM = asyncio.Semaphore(MAX_CONCURRENT)

# Crime severity weights (higher = more severe)
CRIME_WEIGHTS = {
    "violence-and-sexual-offences": 10,
    "robbery": 9,
    "possession-of-weapons": 8,
    "burglary": 7,
    "criminal-damage-arson": 6,
    "vehicle-crime": 5,
    "drugs": 4,
    "public-order": 4,
    "anti-social-behaviour": 3,
    "theft-from-the-person": 3,
    "other-theft": 2,
    "bicycle-theft": 2,
    "shoplifting": 1,
    "other-crime": 1
}


async def fetch_with_retry(
    url: str, 
    session: aiohttp.ClientSession,
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> List[Dict[str, Any]]:
    """
    Fetch data from URL with retry logic for rate limiting.
    
    Args:
        url: API endpoint URL
        session: aiohttp session
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        
    Returns:
        List of crime events or empty list on failure
    """
    async with SEM:
        for attempt in range(max_retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data if data else []
                    
                    elif resp.status == 429:
                        # Rate limit exceeded
                        wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                        print(f"[Crime API] Rate limit reached. Waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    elif resp.status == 503:
                        # Service unavailable
                        print(f"[Crime API] Service unavailable. Retrying...")
                        await asyncio.sleep(retry_delay)
                        continue
                    
                    else:
                        print(f"[Crime API] HTTP {resp.status} for {url}")
                        return []
                        
            except asyncio.TimeoutError:
                print(f"[Crime API] Timeout on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                return []
            
            except Exception as e:
                print(f"[Crime API] Error: {e}")
                return []
        
        print(f"[Crime API] Max retries exceeded for {url}")
        return []


async def fetch_crimes_polygon_async(
    polygon: List[List[float]], 
    date_ym: str
) -> List[Dict[str, Any]]:
    """
    Fetch crime data for a polygon area asynchronously.
    
    Args:
        polygon: List of [lat, lng] coordinate pairs
        date_ym: Date in YYYY-MM format
        
    Returns:
        List of crime events
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    
    # Format polygon for API
    poly_str = ":".join([f"{lat},{lng}" for lat, lng in polygon])
    url = f"{base_url}?poly={poly_str}&date={date_ym}"
    
    async with aiohttp.ClientSession() as session:
        return await fetch_with_retry(url, session)


async def fetch_crimes_point_async(
    lat: float, 
    lng: float, 
    date_ym: str
) -> List[Dict[str, Any]]:
    """
    Fetch crime data for a point location asynchronously.
    
    Args:
        lat: Latitude
        lng: Longitude
        date_ym: Date in YYYY-MM format
        
    Returns:
        List of crime events
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    url = f"{base_url}?lat={lat}&lng={lng}&date={date_ym}"
    
    async with aiohttp.ClientSession() as session:
        return await fetch_with_retry(url, session)


async def fetch_multiple_months_async(
    lat: float,
    lng: float,
    months: List[str]
) -> List[Dict[str, Any]]:
    """
    Fetch crime data for multiple months in parallel.
    
    Args:
        lat: Latitude
        lng: Longitude
        months: List of dates in YYYY-MM format
        
    Returns:
        Combined list of all crime events
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for month in months:
            url = f"{base_url}?date={month}&lat={lat}&lng={lng}"
            tasks.append(fetch_with_retry(url, session))
        
        # Execute all requests concurrently
        results = await asyncio.gather(*tasks)
        
        # Flatten results
        all_crimes = []
        for result in results:
            all_crimes.extend(result)
        
        return all_crimes


async def fetch_polygon_multiple_months_async(
    polygon: List[List[float]],
    months: List[str]
) -> List[Dict[str, Any]]:
    """
    Fetch crime data for a polygon across multiple months in parallel.
    
    Args:
        polygon: List of [lat, lng] coordinate pairs
        months: List of dates in YYYY-MM format
        
    Returns:
        Combined list of all crime events for the period
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    poly_str = ":".join([f"{lat},{lng}" for lat, lng in polygon])
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for month in months:
            url = f"{base_url}?poly={poly_str}&date={month}"
            tasks.append(fetch_with_retry(url, session))
        
        results = await asyncio.gather(*tasks)
        
        all_crimes = []
        for result in results:
            all_crimes.extend(result)
        
        return all_crimes


async def fetch_polygon_monthly_data_async(
    polygon: List[List[float]],
    months: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch crime data for a polygon across multiple months and keep month grouping.
    
    Args:
        polygon: List of [lat, lng] coordinate pairs
        months: List of dates in YYYY-MM format
        
    Returns:
        Dict mapping month -> list of crime events
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    poly_str = ":".join([f"{lat},{lng}" for lat, lng in polygon])
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for month in months:
            url = f"{base_url}?poly={poly_str}&date={month}"
            tasks.append(fetch_with_retry(url, session))
        
        results = await asyncio.gather(*tasks)
        
        return {month: events for month, events in zip(months, results)}


async def fetch_both_postcodes_async(
    postcode_a_data: Dict[str, Any],
    postcode_b_data: Dict[str, Any],
    polygon_a: List[List[float]],
    polygon_b: List[List[float]],
    month: str
) -> tuple:
    """
    Fetch crime data for both postcodes concurrently.
    
    Args:
        postcode_a_data: Postcode A info (postcode, lat, lng)
        postcode_b_data: Postcode B info (postcode, lat, lng)
        polygon_a: Polygon coordinates for postcode A
        polygon_b: Polygon coordinates for postcode B
        month: Date in YYYY-MM format
        
    Returns:
        Tuple of (events_a, events_b)
    """
    base_url = "https://data.police.uk/api/crimes-street/all-crime"
    
    # Format polygons
    poly_a_str = ":".join([f"{lat},{lng}" for lat, lng in polygon_a])
    poly_b_str = ":".join([f"{lat},{lng}" for lat, lng in polygon_b])
    
    url_a = f"{base_url}?poly={poly_a_str}&date={month}"
    url_b = f"{base_url}?poly={poly_b_str}&date={month}"
    
    async with aiohttp.ClientSession() as session:
        # Fetch both concurrently
        results = await asyncio.gather(
            fetch_with_retry(url_a, session),
            fetch_with_retry(url_b, session)
        )
        
        return results[0], results[1]


def make_circle_polygon(lat: float, lng: float, radius_m: float, num_points: int = 32) -> List[List[float]]:
    """
    Create circular polygon coordinates for given center and radius.
    
    Args:
        lat: Center latitude
        lng: Center longitude
        radius_m: Radius in meters
        num_points: Number of points forming the circle
        
    Returns:
        List of [lat, lng] coordinate pairs
    """
    pts = []
    R = 6371000  # Earth radius in meters
    
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        d_lat = (radius_m / R) * math.cos(angle)
        d_lng = (radius_m / (R * math.cos(math.radians(lat)))) * math.sin(angle)
        new_lat = lat + math.degrees(d_lat)
        new_lng = lng + math.degrees(d_lng)
        pts.append([new_lat, new_lng])
    
    return pts


def summarize_crimes(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Summarize crime events and calculate risk score.
    
    Args:
        events: List of crime event dictionaries
        
    Returns:
        Dictionary with summary statistics
    """
    if not events:
        return {
            "total_crimes": 0,
            "by_category": {},
            "weighted_sum": 0,
            "risk_score": 0,
            "top_categories": []
        }
    
    # Extract categories
    categories = [e.get("category", "other-crime") for e in events]
    total = len(categories)
    by_cat = dict(Counter(categories))
    
    # Calculate weighted severity
    weighted = sum(
        count * CRIME_WEIGHTS.get(cat, 1)
        for cat, count in by_cat.items()
    )
    
    # Calculate risk score (0-100)
    risk = min(100, (weighted / 80) * 100)
    
    # Get top 3 categories
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
    
    return {
        "total_crimes": total,
        "by_category": by_cat,
        "weighted_sum": weighted,
        "risk_score": round(risk),
        "top_categories": top_cats
    }


def format_category_name(category: str) -> str:
    """Format category name for display."""
    return category.replace("-", " ").title()


def get_risk_level(risk_score: float) -> str:
    """Get risk level label based on score."""
    if risk_score < 40:
        return "Low"
    elif risk_score < 70:
        return "Medium"
    else:
        return "High"


def get_risk_badge_class(risk_score: float) -> str:
    """Get CSS class for risk badge."""
    if risk_score < 40:
        return "badge-success"
    elif risk_score < 70:
        return "badge-warning"
    else:
        return "badge-danger"


# Convenience function to run async code from sync context
def run_async(coro):
    """
    Run async coroutine from synchronous code.
    
    Args:
        coro: Coroutine to run
        
    Returns:
        Result of the coroutine
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(coro)
