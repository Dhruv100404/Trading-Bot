"""Market-activity refresh orchestration -- mirrors
engine/src/api/market_activity.rs::refresh_all. A single source failing is
logged and skipped, matching fetch_market_activity_from_sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from swing_atlas.config import Settings
from swing_atlas.domain.market_activity.models import (
    MarketActivityRow,
    MarketActivitySource,
    parse_configured_sources,
)
from swing_atlas.domain.time_utils import now_sql
from swing_atlas.repositories.market_activity_repo import MarketActivityRepo
from swing_atlas.repositories.scraping.moneycontrol_activity import fetch_moneycontrol_source

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
_FETCH_TIMEOUT_SECS = 20.0


@dataclass(frozen=True, slots=True)
class MarketActivityRefreshSummary:
    ok: bool
    rows: int
    sources: list[MarketActivitySource]
    refreshed_at: str


async def fetch_market_activity(
    sources: list[MarketActivitySource], max_rows_per_source: int
) -> list[MarketActivityRow]:
    rows: list[MarketActivityRow] = []
    async with httpx.AsyncClient(headers=_BROWSER_HEADERS, timeout=_FETCH_TIMEOUT_SECS) as client:
        for source in sources:
            try:
                rows.extend(await fetch_moneycontrol_source(client, source, max_rows_per_source))
            except Exception as exc:  # noqa: BLE001 -- one source failing shouldn't sink the refresh
                logger.warning(
                    "[MARKET-ACTIVITY] source=%s metric=%s fetch failed: %s",
                    source.source,
                    source.metric_type,
                    exc,
                )
    return rows


class MarketActivityService:
    def __init__(self, settings: Settings, repo: MarketActivityRepo) -> None:
        self._settings = settings
        self._repo = repo

    async def refresh_all(self) -> MarketActivityRefreshSummary:
        max_rows = max(10, min(250, self._settings.market_activity_max_rows_per_source))
        sources = parse_configured_sources(self._settings.market_activity_sources)

        rows = await fetch_market_activity(sources, max_rows)
        await self._repo.insert_rows(rows)

        return MarketActivityRefreshSummary(
            ok=True, rows=len(rows), sources=sources, refreshed_at=now_sql()
        )
