import os
from dotenv import load_dotenv
from typing import Optional

# Load environment variables from .env file
load_dotenv()


class Settings:
    """Application configuration settings loaded from environment variables."""
    
    def __init__(self):
        # Application environment
        self.app_env: str = os.getenv("APP_ENV", "dev")
        
        # Supabase configuration
        self.supabase_url: Optional[str] = os.getenv("SUPABASE_URL")
        self.supabase_key: Optional[str] = os.getenv("SUPABASE_KEY")
        
        # OpenAI configuration
        self.openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
        
        # Telegram notifications
        self.telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")
        
        # Rate limiting
        self.daily_requests_per_user: int = int(os.getenv("DAILY_REQUESTS_PER_USER", "10"))
        
        # Admin access
        self.admin_key: Optional[str] = os.getenv("ADMIN_KEY")
        
        # Validate critical settings
        self._validate()
    
    def _validate(self) -> None:
        """Validate that critical configuration is present."""
        if not self.supabase_url or not self.supabase_key:
            print("⚠️  WARNING: SUPABASE_URL or SUPABASE_KEY not set")
        
        if not self.openai_api_key:
            print("⚠️  WARNING: OPENAI_API_KEY not set")
        
        if self.daily_requests_per_user < 1:
            print("⚠️  WARNING: DAILY_REQUESTS_PER_USER must be at least 1")
            self.daily_requests_per_user = 10
    
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env.lower() in ("prod", "production")
    
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.app_env.lower() in ("dev", "development")
    
    def __repr__(self) -> str:
        """String representation hiding sensitive values."""
        return (
            f"Settings(app_env={self.app_env}, "
            f"supabase_url={'***' if self.supabase_url else None}, "
            f"openai_api_key={'***' if self.openai_api_key else None}, "
            f"daily_requests_per_user={self.daily_requests_per_user})"
        )


# Global settings instance
settings = Settings()
