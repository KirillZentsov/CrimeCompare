from supabase import create_client, Client
import time
from typing import Optional, Callable, TypeVar
from config import settings

# Global variables
_supabase_client: Optional[Client] = None
_supabase_available: bool = False

T = TypeVar('T')


def init_supabase() -> None:
    """Initialize the Supabase client. Call once at app startup."""
    global _supabase_client, _supabase_available
    
    if not settings.supabase_url or not settings.supabase_key:
        print("[Supabase] Missing SUPABASE_URL or SUPABASE_KEY in environment")
        _supabase_available = False
        return
    
    try:
        _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
        _supabase_available = True
        print("[Supabase] Successfully initialized")
    except Exception as e:
        print(f"[Supabase] Initialization failed: {e}")
        _supabase_available = False


def get_supabase_client() -> Client:
    """Get the Supabase client instance. Raises RuntimeError if not initialized."""
    if _supabase_client is None:
        raise RuntimeError("Supabase client not initialized. Call init_supabase() first.")
    return _supabase_client


def supabase_available() -> bool:
    """Check if Supabase is available."""
    return _supabase_available and _supabase_client is not None


def supabase_query(fn: Callable[[], T], retries: int = 3, delay: float = 1.0) -> T:
    """
    Execute a Supabase query with retry logic and exponential backoff.
    
    Args:
        fn: A callable that performs the Supabase query
        retries: Number of retry attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
    
    Returns:
        The result of the query
        
    Raises:
        RuntimeError: If Supabase is not available or query fails after retries
    """
    global _supabase_available
    
    if _supabase_client is None:
        _supabase_available = False
        raise RuntimeError("Supabase not initialized. Call init_supabase() first.")

    last_error = None
    
    for attempt in range(retries):
        try:
            result = fn()
            _supabase_available = True
            return result
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            
            # Для timeout увеличиваем задержку экспоненциально
            if 'timeout' in error_str or 'statement timeout' in error_str:
                wait_time = delay * (2 ** attempt)  # Экспоненциальная задержка
                print(f"[Supabase] Timeout on attempt {attempt + 1}/{retries}, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
            else:
                print(f"[Supabase] Query failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)

    # All retries failed
    _supabase_available = False
    raise RuntimeError(f"Supabase query failed after {retries} attempts: {last_error}")


def test_connection() -> bool:
    """Test the Supabase connection by attempting a simple query."""
    try:
        client = get_supabase_client()
        # Try to query the settings table
        client.table("settings").select("id").limit(1).execute()
        return True
    except Exception as e:
        print(f"[Supabase] Connection test failed: {e}")
        return False
