from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    host: str
    port: int
    webhook_url: str | None
    webhook_path: str
    webhook_secret: str | None
    request_timeout: float
    search_timeout: float
    result_limit: int
    inline_result_limit: int
    auto_pick_score: int
    auto_pick_gap: int
    search_session_ttl_seconds: int
    search_page_size: int
    log_level: str

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url)

    @property
    def webhook_endpoint(self) -> str | None:
        if not self.webhook_url:
            return None
        return f"{self.webhook_url.rstrip('/')}{self.webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or BOT_TOKEN environment variable.")

    webhook_url = (os.getenv("WEBHOOK_URL") or "").strip() or None
    webhook_secret = (os.getenv("WEBHOOK_SECRET") or "").strip() or None

    return Settings(
        bot_token=token.strip(),
        host=os.getenv("HOST", "0.0.0.0").strip(),
        port=_env_int("PORT", 8080),
        webhook_url=webhook_url,
        webhook_path=os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip() or "/telegram/webhook",
        webhook_secret=webhook_secret,
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "20")),
        search_timeout=float(os.getenv("SEARCH_TIMEOUT", "12")),
        result_limit=_env_int("RESULT_LIMIT", 10),
        inline_result_limit=_env_int("INLINE_RESULT_LIMIT", 5),
        auto_pick_score=_env_int("AUTO_PICK_SCORE", 94),
        auto_pick_gap=_env_int("AUTO_PICK_GAP", 12),
        search_session_ttl_seconds=_env_int("SEARCH_SESSION_TTL_SECONDS", 1800),
        search_page_size=_env_int("SEARCH_PAGE_SIZE", 6),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )

