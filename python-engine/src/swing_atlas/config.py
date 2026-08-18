"""Env-loaded settings, mirroring engine/src/config.rs (dead fields dropped, see migration plan)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dhan_base_url: str = "https://api.dhan.co/v2"
    dhan_quote_endpoint: str = "/marketfeed/quote"
    dhan_access_token: str = ""
    dhan_client_id: str = ""

    clickhouse_url: str = "http://localhost:8123"

    debug: bool = False

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    news_auto_refresh: bool = True
    news_refresh_interval_secs: int = 900
    news_max_per_source: int = 40
    news_sources: str = ""

    nse_large_deals_lookback_days: int = 7

    market_activity_auto_refresh: bool = True
    market_activity_refresh_interval_secs: int = 900
    market_activity_max_rows_per_source: int = 75
    market_activity_sources: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
