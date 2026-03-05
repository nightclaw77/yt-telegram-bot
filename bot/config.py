"""Configuration management using environment variables."""
import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)


class Config:
    """Bot configuration loaded from environment variables."""
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ALLOWED_USER_IDS: List[int] = []
    
    # Downloads
    DOWNLOADS_DIR: Path = Path(os.getenv("DOWNLOADS_DIR", "./downloads"))
    
    # AI Summary
    YTSUMMARIZE_CLI: str = os.getenv("YTSUMMARIZE_CLI", "/usr/local/bin/ytsummarize")
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")

    # Bale Bridge (optional)
    BALE_BOT_TOKEN: str = os.getenv("BALE_BOT_TOKEN", "")
    BALE_CHAT_ID: str = os.getenv("BALE_CHAT_ID", "")
    BALE_FORWARD_ENABLED: bool = os.getenv("BALE_FORWARD_ENABLED", "false").lower() == "true"

    # File cache (reuse recent outputs)
    FILE_CACHE_ENABLED: bool = os.getenv("FILE_CACHE_ENABLED", "true").lower() == "true"
    FILE_CACHE_TTL_SECONDS: int = int(os.getenv("FILE_CACHE_TTL_SECONDS", "3600"))

    # Bale safe upload tuning
    BALE_SAFE_MAX_MB: int = int(os.getenv("BALE_SAFE_MAX_MB", "20"))
    
    @classmethod
    def load(cls):
        """Load and validate configuration."""
        # Parse allowed user IDs
        user_ids_str = os.getenv("ALLOWED_USER_IDS", "")
        if user_ids_str:
            cls.ALLOWED_USER_IDS = [
                int(uid.strip()) 
                for uid in user_ids_str.split(",") 
                if uid.strip().isdigit()
            ]
        
        # Create downloads directory if it doesn't exist
        cls.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        
        return cls
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not cls.ALLOWED_USER_IDS:
            raise ValueError("ALLOWED_USER_IDS is required")
        return True


# Global config instance
config = Config()
