import requests
from collections import Counter
from typing import List, Dict, Any

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


def fetch_crimes_polygon(polygon: List[List[float]], date_ym: str) -> List[Dict[str, Any]]:
    """
    Fetch crime data from UK Police API for a polygon area.
    
    Args:
        polygon: List of [lat, lng] coordinate pairs forming a closed polygon
        date_ym: Date in YYYY-MM format
        
    Returns:
        List of crime events
        
    Raises:
        Exception: If API request fails
    """
    url = "https://data.police.uk/api/crimes-street/all-crime"
    
    # Format polygon for API (needs to be lat,lng pairs as string)
    poly_str = ":".join([f"{lat},{lng}" for lat, lng in polygon])
    
    try:
        response = requests.get(
            url,
            params={"poly": poly_str, "date": date_ym},
            timeout=15
        )
        response.raise_for_status()
        
        data = response.json()
        
        if not data:
            print(f"[Crime API] No crimes found for polygon in {date_ym}")
            return []
        
        return data
        
    except requests.exceptions.Timeout:
        raise Exception("Crime API request timed out. Please try again.")
    except requests.exceptions.ConnectionError:
        raise Exception("Could not connect to Crime API. Check your internet connection.")
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            raise Exception(f"No crime data available for {date_ym}")
        elif response.status_code == 503:
            raise Exception("Crime API is temporarily unavailable")
        else:
            raise Exception(f"Crime API error: {e}")


def fetch_crimes(lat: float, lng: float, date_ym: str) -> List[Dict[str, Any]]:
    """
    Fetch crime data from UK Police API for a specific location and date.
    
    Args:
        lat: Latitude
        lng: Longitude
        date_ym: Date in YYYY-MM format
        
    Returns:
        List of crime events
        
    Raises:
        requests.HTTPError: If API request fails
        ValueError: If invalid parameters
    """
    url = "https://data.police.uk/api/crimes-street/all-crime"
    
    try:
        response = requests.get(
            url, 
            params={"lat": lat, "lng": lng, "date": date_ym},
            timeout=10  # Add timeout
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Handle empty results
        if not data:
            print(f"[Crime API] No crimes found for lat={lat}, lng={lng}, date={date_ym}")
            return []
        
        return data
        
    except requests.exceptions.Timeout:
        raise Exception("Crime API request timed out. Please try again.")
    except requests.exceptions.ConnectionError:
        raise Exception("Could not connect to Crime API. Check your internet connection.")
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            raise Exception(f"No crime data available for {date_ym}")
        elif response.status_code == 503:
            raise Exception("Crime API is temporarily unavailable")
        else:
            raise Exception(f"Crime API error: {e}")


def summarize_crimes(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Summarize crime events and calculate risk score.
    
    Args:
        events: List of crime event dictionaries
        
    Returns:
        Dictionary with summary statistics:
        - total_crimes: Total number of crimes
        - by_category: Count by category
        - weighted_sum: Weighted severity sum
        - risk_score: Risk score 0-100
    """
    if not events:
        return {
            "total_crimes": 0,
            "by_category": {},
            "weighted_sum": 0,
            "risk_score": 0
        }
    
    # Extract categories (handle missing category gracefully)
    categories = [e.get("category", "other-crime") for e in events]
    total = len(categories)
    by_cat = dict(Counter(categories))
    
    # Calculate weighted severity
    weighted = sum(
        count * CRIME_WEIGHTS.get(cat, 1)
        for cat, count in by_cat.items()
    )
    
    # Calculate risk score (0-100)
    # Formula: normalize weighted sum, cap at 100
    risk = min(100, (weighted / 80) * 100)
    
    return {
        "total_crimes": total,
        "by_category": by_cat,
        "weighted_sum": weighted,
        "risk_score": round(risk, 2)
    }


def get_top_crime_categories(by_category: Dict[str, int], top_n: int = 3) -> List[tuple]:
    """
    Get top N crime categories by count.
    
    Args:
        by_category: Dictionary of category counts
        top_n: Number of top categories to return
        
    Returns:
        List of (category, count) tuples sorted by count
    """
    sorted_crimes = sorted(
        by_category.items(), 
        key=lambda x: x[1], 
        reverse=True
    )
    return sorted_crimes[:top_n]


def format_category_name(category: str) -> str:
    """
    Format category name for display.
    
    Args:
        category: Raw category string (e.g., "violence-and-sexual-offences")
        
    Returns:
        Formatted string (e.g., "Violence and Sexual Offences")
    """
    return category.replace("-", " ").title()
