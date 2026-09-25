"""Loads settings from .env and validates them."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PLACEHOLDER_MARKERS = ("paste", "your_", "xxxx", "<", "changeme")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _is_placeholder(value: str) -> bool:
    return not value or any(m in value.lower() for m in PLACEHOLDER_MARKERS)


@dataclass
class Settings:
    # Keys never contain whitespace; strip any that sneaks in from line-wrapped pastes
    telegram_token: str = field(default_factory=lambda: "".join(_get("TELEGRAM_BOT_TOKEN").split()))
    gemini_api_key: str = field(default_factory=lambda: "".join(_get("GEMINI_API_KEY").split()))
    # Shared secret Telegram sends with every webhook call (Vercel mode); set_webhook.py registers it
    webhook_secret: str = field(default_factory=lambda: _get("TELEGRAM_WEBHOOK_SECRET"))
    gemini_model: str = field(default_factory=lambda: _get("GEMINI_MODEL", "gemini-3.6-flash"))
    # Optional separate (stronger) model for the fact/voice reviewer; empty = same as GEMINI_MODEL
    # Used automatically when the main model is overloaded (429/5xx) after retries
    gemini_fallback_model: str = field(default_factory=lambda: _get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest"))
    gemini_review_model: str = field(default_factory=lambda: _get("GEMINI_REVIEW_MODEL"))
    allowed_chat_ids: set[int] = field(
        default_factory=lambda: {
            int(x) for x in _get("ALLOWED_CHAT_IDS").replace(" ", "").split(",") if x.lstrip("-").isdigit()
        }
    )
    # Quality gate: notes must score at least this (out of 10) before a post is drafted
    notes_min_score: int = field(default_factory=lambda: int(_get("NOTES_MIN_SCORE", "7") or 7))
    news_enabled: bool = field(default_factory=lambda: _get("NEWS_ENABLED", "true").lower() == "true")
    news_region: str = field(default_factory=lambda: _get("NEWS_REGION", "IN"))
    news_max_items: int = field(default_factory=lambda: int(_get("NEWS_MAX_ITEMS", "8") or 8))
    news_max_age_days: int = field(default_factory=lambda: int(_get("NEWS_MAX_AGE_DAYS", "30") or 30))

    # Optional - Phase 2 auto-publishing
    linkedin_access_token: str = field(default_factory=lambda: _get("LINKEDIN_ACCESS_TOKEN"))
    linkedin_author_urn: str = field(default_factory=lambda: _get("LINKEDIN_AUTHOR_URN"))
    linkedin_api_version: str = field(default_factory=lambda: _get("LINKEDIN_API_VERSION", "202608"))

    @property
    def linkedin_enabled(self) -> bool:
        return not _is_placeholder(self.linkedin_access_token)

    def validate(self, need_telegram: bool = True) -> None:
        missing = []
        if need_telegram and _is_placeholder(self.telegram_token):
            missing.append("TELEGRAM_BOT_TOKEN")
        if _is_placeholder(self.gemini_api_key):
            missing.append("GEMINI_API_KEY")
        if missing:
            raise SystemExit(
                f"Missing in .env: {', '.join(missing)}\n"
                f"Open {BASE_DIR / '.env'} and paste your keys there."
            )


settings = Settings()
