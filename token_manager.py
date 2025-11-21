from typing import Optional
from supabase_utils import get_supabase_client, supabase_query
from telegram_utils import send_telegram_alert

# Pricing for gpt-4o-mini (per 1K tokens)
PRICE_IN = 0.00015  # $0.150 per 1M tokens
PRICE_OUT = 0.00060  # $0.600 per 1M tokens


def get_settings_row() -> dict:
    """
    Retrieve the settings row from the database.
    
    Returns:
        Dictionary with settings data
        
    Raises:
        RuntimeError: If query fails
    """
    def query():
        client = get_supabase_client()
        return client.table("settings").select("*").eq("id", 1).single().execute()
    
    result = supabase_query(query)
    return result.data


def record_token_usage(user_id: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """
    Record token usage and update total spend. Pauses service if budget exceeded.
    
    Args:
        user_id: User identifier
        model: Model name used
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
    """
    # Calculate cost
    cost = (prompt_tokens * PRICE_IN + completion_tokens * PRICE_OUT) / 1000
    
    client = get_supabase_client()
    
    # Insert token usage record
    def insert_usage():
        return client.table("token_usage").insert({
            "user_id": user_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost
        }).execute()
    
    try:
        supabase_query(insert_usage)
    except Exception as e:
        print(f"[Token Manager] Failed to record usage: {e}")
        # Don't fail the request if usage recording fails
        return

    # Update total spent
    try:
        settings_row = get_settings_row()
        new_total = float(settings_row["total_spent"]) + cost

        def update_total():
            return client.table("settings").update({
                "total_spent": new_total
            }).eq("id", 1).execute()
        
        supabase_query(update_total)

        # Check if budget exceeded
        monthly_limit = float(settings_row["monthly_budget_limit"])
        if new_total >= monthly_limit:
            def pause_service():
                return client.table("settings").update({
                    "service_paused": True
                }).eq("id", 1).execute()
            
            supabase_query(pause_service)
            
            alert_msg = (
                f"⛔ Budget Exceeded!\n"
                f"Total spent: ${new_total:.4f}\n"
                f"Monthly limit: ${monthly_limit:.2f}\n"
                f"Service has been paused."
            )
            send_telegram_alert(alert_msg)
            print(f"[Token Manager] {alert_msg}")
            
    except Exception as e:
        print(f"[Token Manager] Failed to update total spent: {e}")


def get_usage_stats(user_id: Optional[str] = None, days: int = 30) -> dict:
    """
    Get usage statistics for a user or all users.
    
    Args:
        user_id: Optional user ID to filter by
        days: Number of days to look back
        
    Returns:
        Dictionary with usage statistics
    """
    from datetime import datetime, timedelta
    
    client = get_supabase_client()
    cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
    
    def query():
        q = client.table("token_usage").select("*").gte("created_at", cutoff_date)
        if user_id:
            q = q.eq("user_id", user_id)
        return q.execute()
    
    try:
        result = supabase_query(query)
        data = result.data or []
        
        total_cost = sum(float(row.get("cost_usd", 0)) for row in data)
        total_prompt = sum(int(row.get("prompt_tokens", 0)) for row in data)
        total_completion = sum(int(row.get("completion_tokens", 0)) for row in data)
        
        return {
            "total_requests": len(data),
            "total_cost": round(total_cost, 4),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "days": days
        }
    except Exception as e:
        print(f"[Token Manager] Failed to get usage stats: {e}")
        return {
            "total_requests": 0,
            "total_cost": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "days": days
        }
