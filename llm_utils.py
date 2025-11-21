from openai import OpenAI
import json
from typing import List, Dict, Any
from config import settings
from token_manager import record_token_usage, get_settings_row
from telegram_utils import send_telegram_alert

# Initialize OpenAI client
client = OpenAI(api_key=settings.openai_api_key)
MODEL = "gpt-4o-mini"


def is_service_paused() -> bool:
    """Check if the service is currently paused."""
    try:
        return get_settings_row()["service_paused"]
    except Exception as e:
        print(f"[LLM Utils] Failed to check service status: {e}")
        return False


def summarize_crime_comparison_llm(user_id: str, rows: List[Dict[str, Any]], date_ym: str) -> str:
    """
    Generate an AI summary comparing crime data across postcodes.
    
    Args:
        user_id: User identifier for tracking
        rows: List of crime data dictionaries
        date_ym: Date in YYYY-MM format
        
    Returns:
        AI-generated summary text
    """
    if is_service_paused():
        return "⛔ AI service is temporarily paused due to budget limits."

    # Filter out rows with errors
    structured = []
    for r in rows:
        if "error" in r:
            continue
        structured.append({
            "postcode": r["postcode"],
            "total_crimes": r["total_crimes"],
            "risk_score": r["risk_score"],
            "by_category": r["by_category"]
        })

    if not structured:
        return "❌ No valid crime data available to analyze."

    system_prompt = """You are a UK crime analysis expert. Compare crime data across postcodes clearly and concisely.

Guidelines:
- Highlight the safest and most dangerous areas
- Mention specific crime categories that stand out
- Provide actionable insights
- Keep it under 200 words
- Use a professional but accessible tone"""

    user_prompt = f"""Analyze this crime data for {date_ym}:

{json.dumps(structured, indent=2)}

Provide a concise comparison highlighting key differences and safety insights."""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )

        # Record token usage
        usage = response.usage
        if usage:
            try:
                record_token_usage(
                    user_id=user_id,
                    model=MODEL,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens
                )
            except Exception as e:
                print(f"[LLM Utils] Failed to record token usage: {e}")

        return response.choices[0].message.content.strip()

    except Exception as e:
        error_msg = f"LLM error for user {user_id[:10]}: {str(e)}"
        print(f"[LLM Utils] {error_msg}")
        send_telegram_alert(f"🚨 {error_msg}")
        
        return f"❌ Failed to generate AI summary. Please try again later. (Error: {str(e)[:50]})"


def generate_safety_recommendations(postcode: str, crime_data: Dict[str, Any]) -> str:
    """
    Generate personalized safety recommendations for a specific postcode.
    
    Args:
        postcode: The postcode to analyze
        crime_data: Crime statistics for the postcode
        
    Returns:
        Safety recommendations text
    """
    if is_service_paused():
        return "Service paused."

    prompt = f"""Based on this crime data for {postcode}:
- Total crimes: {crime_data.get('total_crimes', 0)}
- Risk score: {crime_data.get('risk_score', 0)}
- Top categories: {json.dumps(crime_data.get('by_category', {}), indent=2)}

Provide 3-5 specific safety recommendations for residents or visitors."""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a UK crime prevention advisor."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=300
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[LLM Utils] Failed to generate recommendations: {e}")
        return "Unable to generate recommendations at this time."
