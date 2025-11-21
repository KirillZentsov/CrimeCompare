from datetime import date
from supabase_utils import get_supabase_client, supabase_query
from config import settings


def check_limit(user_id: str) -> bool:
    """
    Check if a user has exceeded their daily request limit.
    
    Args:
        user_id: User identifier
        
    Returns:
        True if limit exceeded, False otherwise
    """
    today = date.today().isoformat()
    client = get_supabase_client()

    def query():
        return client.table("usage_limits").select("*").eq("user_id", user_id).eq("date", today).maybe_single().execute()

    try:
        result = supabase_query(query)
        row = result.data

        if not row:
            # No record for today, create one
            def insert():
                return client.table("usage_limits").insert({
                    "user_id": user_id,
                    "date": today,
                    "requests_used": 0
                }).execute()
            
            supabase_query(insert)
            return False

        # Check if limit exceeded
        requests_used = row.get("requests_used", 0)
        limit = settings.daily_requests_per_user
        
        return requests_used >= limit
        
    except Exception as e:
        print(f"[Quota Manager] Failed to check limit for {user_id[:10]}: {e}")
        # On error, allow the request (fail open)
        return False


def increment_usage(user_id: str) -> None:
    """
    Increment the usage counter for a user.
    
    Args:
        user_id: User identifier
    """
    today = date.today().isoformat()
    client = get_supabase_client()

    def get_current():
        return client.table("usage_limits").select("id, requests_used").eq("user_id", user_id).eq("date", today).maybe_single().execute()

    try:
        result = supabase_query(get_current)
        row = result.data

        if not row:
            # Create initial record
            def insert():
                return client.table("usage_limits").insert({
                    "user_id": user_id,
                    "date": today,
                    "requests_used": 1
                }).execute()
            
            supabase_query(insert)
        else:
            # Increment existing record
            def update():
                return client.table("usage_limits").update({
                    "requests_used": row["requests_used"] + 1
                }).eq("id", row["id"]).execute()
            
            supabase_query(update)
            
    except Exception as e:
        print(f"[Quota Manager] Failed to increment usage for {user_id[:10]}: {e}")
        # Don't fail the request if increment fails


def get_remaining_requests(user_id: str) -> int:
    """
    Get the number of remaining requests for a user today.
    
    Args:
        user_id: User identifier
        
    Returns:
        Number of remaining requests
    """
    today = date.today().isoformat()
    client = get_supabase_client()

    def query():
        return client.table("usage_limits").select("requests_used").eq("user_id", user_id).eq("date", today).maybe_single().execute()

    try:
        result = supabase_query(query)
        row = result.data

        if not row:
            return settings.daily_requests_per_user

        requests_used = row.get("requests_used", 0)
        remaining = settings.daily_requests_per_user - requests_used
        
        return max(0, remaining)
        
    except Exception as e:
        print(f"[Quota Manager] Failed to get remaining requests: {e}")
        return settings.daily_requests_per_user


def reset_user_quota(user_id: str, target_date: str = None) -> bool:
    """
    Reset a user's quota for a specific date.
    
    Args:
        user_id: User identifier
        target_date: Date in ISO format (YYYY-MM-DD). Defaults to today.
        
    Returns:
        True if successful, False otherwise
    """
    if target_date is None:
        target_date = date.today().isoformat()
    
    client = get_supabase_client()

    def update():
        return client.table("usage_limits").update({
            "requests_used": 0
        }).eq("user_id", user_id).eq("date", target_date).execute()

    try:
        supabase_query(update)
        return True
    except Exception as e:
        print(f"[Quota Manager] Failed to reset quota: {e}")
        return False
