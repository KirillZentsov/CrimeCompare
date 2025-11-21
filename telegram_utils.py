import requests
from typing import Optional
from config import settings


def send_telegram_alert(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Send an alert message to Telegram.
    
    Args:
        text: Message text to send
        parse_mode: Optional parse mode ("Markdown" or "HTML")
        
    Returns:
        True if successful, False otherwise
    """
    # Check if Telegram is configured
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("[Telegram] Bot token or chat ID not configured. Skipping alert.")
        return False
    
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text
    }
    
    if parse_mode:
        payload["parse_mode"] = parse_mode
    
    try:
        response = requests.post(url, data=payload, timeout=5)
        response.raise_for_status()
        print(f"[Telegram] Alert sent successfully")
        return True
        
    except requests.exceptions.Timeout:
        print("[Telegram] Request timed out")
        return False
        
    except requests.exceptions.RequestException as e:
        print(f"[Telegram] Failed to send alert: {e}")
        return False


def send_error_alert(error_msg: str, context: Optional[str] = None) -> bool:
    """
    Send a formatted error alert to Telegram.
    
    Args:
        error_msg: Error message
        context: Optional context information
        
    Returns:
        True if successful, False otherwise
    """
    from datetime import datetime
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    message = f"🚨 *Error Alert*\n\n"
    message += f"*Time:* {timestamp}\n"
    message += f"*Error:* {error_msg}\n"
    
    if context:
        message += f"*Context:* {context}\n"
    
    return send_telegram_alert(message, parse_mode="Markdown")


def send_budget_alert(total_spent: float, monthly_limit: float) -> bool:
    """
    Send a budget exceeded alert to Telegram.
    
    Args:
        total_spent: Current total spending
        monthly_limit: Monthly budget limit
        
    Returns:
        True if successful, False otherwise
    """
    message = (
        f"⛔ *Budget Alert*\n\n"
        f"Monthly budget has been exceeded!\n\n"
        f"*Total Spent:* ${total_spent:.4f}\n"
        f"*Monthly Limit:* ${monthly_limit:.2f}\n"
        f"*Overage:* ${(total_spent - monthly_limit):.4f}\n\n"
        f"Service has been automatically paused."
    )
    
    return send_telegram_alert(message, parse_mode="Markdown")


def send_daily_summary(stats: dict) -> bool:
    """
    Send a daily usage summary to Telegram.
    
    Args:
        stats: Dictionary with usage statistics
        
    Returns:
        True if successful, False otherwise
    """
    message = (
        f"📊 *Daily Summary*\n\n"
        f"*Requests:* {stats.get('total_requests', 0)}\n"
        f"*Cost:* ${stats.get('total_cost', 0):.4f}\n"
        f"*Tokens:* {stats.get('total_tokens', 0):,}\n"
        f"*Unique Users:* {stats.get('unique_users', 0)}"
    )
    
    return send_telegram_alert(message, parse_mode="Markdown")


def test_telegram_connection() -> bool:
    """
    Test the Telegram connection by sending a test message.
    
    Returns:
        True if successful, False otherwise
    """
    return send_telegram_alert("✅ Telegram connection test successful!")
