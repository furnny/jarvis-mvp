"""
Jarvis MVP Configuration
Rule thresholds and system settings
"""
from pydantic_settings import BaseSettings
from typing import Dict


class Settings(BaseSettings):
    """Application settings loaded from .env"""
    
    # Binance API
    BINANCE_API_KEY: str
    BINANCE_API_SECRET: str
    BINANCE_TESTNET: bool = True
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str
    
    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/jarvis"
    
    # Rule Thresholds
    MAX_RISK_PCT: float = 2.0
    MIN_LIQ_DISTANCE_PCT: float = 5.0
    NO_SL_TIMEOUT_MINUTES: int = 3
    REVENGE_WINDOW_MINUTES: int = 15
    COOLDOWN_MINUTES: int = 30
    
    # Alert Cooldowns (seconds) - prevent spam
    ALERT_COOLDOWNS: Dict[str, int] = {
        "high_risk": 300,      # 5 minutes
        "liq_risk": 180,       # 3 minutes
        "no_sl": 600,          # 10 minutes
        "revenge": 900         # 15 minutes
    }
    
    # System
    POLL_INTERVAL_SECONDS: int = 15
    DAILY_RECAP_HOUR: int = 20  # UTC
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()


# Rule configurations with descriptions
RULES = {
    "high_risk": {
        "name": "High Risk Alert",
        "threshold": settings.MAX_RISK_PCT,
        "emoji": "⚠️",
        "severity": "warning"
    },
    "liq_risk": {
        "name": "Liquidation Risk",
        "threshold": settings.MIN_LIQ_DISTANCE_PCT,
        "emoji": "🔴",
        "severity": "critical"
    },
    "no_sl": {
        "name": "No Stop Loss",
        "threshold": settings.NO_SL_TIMEOUT_MINUTES,
        "emoji": "🛡️",
        "severity": "warning"
    },
    "revenge": {
        "name": "Revenge Pattern",
        "threshold": None,  # Complex condition
        "emoji": "🧠",
        "severity": "warning"
    }
}


# Button configurations
BUTTON_CONFIGS = {
    "ack": {"label": "✅ Acknowledge", "score": 0},
    "cooldown": {"label": "🧊 Cooldown 30m", "score": 5},
    "reduce": {"label": "📉 Reduce size", "score": 3},
    "set_sl": {"label": "🛡️ Setting SL", "score": 5},
    "add_margin": {"label": "💰 Adding margin", "score": 3},
    "view_stats": {"label": "📊 Show stats", "score": 0}
}


# Discipline score tiers
SCORE_TIERS = [
    (90, 100, "🏆 Diamond", "Excellent"),
    (75, 89, "💎 Platinum", "Good"),
    (60, 74, "🥈 Silver", "Careful"),
    (40, 59, "🥉 Bronze", "Warning"),
    (0, 39, "⚠️ Alert", "Critical")
]


def get_score_tier(score: float) -> tuple:
    """Get badge and status for a discipline score"""
    for min_score, max_score, badge, status in SCORE_TIERS:
        if min_score <= score <= max_score:
            return badge, status
    return "⚠️ Alert", "Critical"
