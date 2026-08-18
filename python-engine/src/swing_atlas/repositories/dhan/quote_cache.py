"""Process-wide Dhan live-quote cache -- mirrors the double-checked-locking cache
in engine/src/api/swing.rs::get_live_quotes, paired with engine/src/api/mod.rs's
CachedQuotes/quote_cache/quote_fetch_lock AppState fields.

A single TTL window covers the whole cached batch (not per-security-id): if a
request asks for a security_id the cache doesn't have, it's a full cache miss.
This exactly matches the Rust original's read_cached_quotes/read_stale_cached_quotes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from swing_atlas.repositories.dhan.market_data import QuoteItem, fetch_quotes

logger = logging.getLogger(__name__)

_FRESH_TTL_SECS = 20.0
_STALE_TTL_SECS = 15 * 60.0


@dataclass
class _CachedQuotes:
    fetched_at: float
    by_security_id: dict[str, QuoteItem]


class QuoteCache:
    def __init__(self) -> None:
        self._cache: _CachedQuotes | None = None
        self._fetch_lock = asyncio.Lock()

    async def get_live_quotes(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        access_token: str,
        client_id: str,
        quote_endpoint: str,
        security_ids: list[str],
    ) -> dict[str, QuoteItem]:
        cached = self._read(security_ids, _FRESH_TTL_SECS)
        if cached is not None:
            return cached

        async with self._fetch_lock:
            cached = self._read(security_ids, _FRESH_TTL_SECS)
            if cached is not None:
                return cached

            try:
                fetched = await fetch_quotes(
                    client, base_url, access_token, client_id, security_ids, quote_endpoint
                )
            except Exception as exc:
                stale = self._read(security_ids, _STALE_TTL_SECS)
                if stale is not None:
                    logger.warning("Dhan quote fetch failed; using stale cached quotes: %s", exc)
                    return stale
                raise

            if not fetched:
                raise RuntimeError(
                    "Dhan quote response did not include any NSE_EQ quotes for the "
                    "requested security ids"
                )

            self._cache = _CachedQuotes(fetched_at=time.monotonic(), by_security_id=fetched)
            return fetched

    def _read(self, security_ids: list[str], max_age_secs: float) -> dict[str, QuoteItem] | None:
        cache = self._cache
        if cache is None or time.monotonic() - cache.fetched_at > max_age_secs:
            return None
        result: dict[str, QuoteItem] = {}
        for security_id in security_ids:
            quote = cache.by_security_id.get(security_id)
            if quote is None:
                return None
            result[security_id] = quote
        return result
