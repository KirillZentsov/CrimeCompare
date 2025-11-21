import streamlit as st
import hashlib
import uuid


def get_user_id() -> str:
    """
    Generate a consistent user ID for the current session.
    
    Combines user agent with a session-specific UUID to create
    a fingerprint that persists across page reloads but resets
    when the browser is closed.
    
    Returns:
        SHA256 hash as hexadecimal string (64 characters)
    """
    # Try to get user agent from headers
    headers = {}
    try:
        if hasattr(st, "context") and hasattr(st.context, "headers"):
            headers = st.context.headers
    except Exception as e:
        print(f"[Fingerprint] Could not access headers: {e}")
    
    user_agent = headers.get("user-agent", "unknown")
    
    # Get or create session ID
    session_id = st.session_state.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        st.session_state["session_id"] = session_id
    
    # Create fingerprint
    raw = f"{user_agent}-{session_id}"
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    
    return fingerprint


def get_short_user_id(length: int = 10) -> str:
    """
    Get a shortened version of the user ID for display.
    
    Args:
        length: Number of characters to return
        
    Returns:
        Shortened user ID
    """
    return get_user_id()[:length]


def get_user_info() -> dict:
    """
    Get detailed user information (for debugging/analytics).
    
    Returns:
        Dictionary with user information
    """
    headers = {}
    try:
        if hasattr(st, "context") and hasattr(st.context, "headers"):
            headers = st.context.headers
    except:
        pass
    
    return {
        "user_id": get_user_id(),
        "short_id": get_short_user_id(),
        "user_agent": headers.get("user-agent", "unknown"),
        "session_id": st.session_state.get("session_id", "unknown"),
        "referer": headers.get("referer", "direct"),
    }
