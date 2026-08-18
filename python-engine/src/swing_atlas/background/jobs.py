"""Background refresh job bodies -- registered onto the scheduler in app.py's lifespan.

Each wraps its service call in try/except + logging, matching the Rust originals'
per-iteration error handling (belt-and-suspenders alongside the scheduler's own
EVENT_JOB_ERROR listener -- see background/scheduler.py).
"""

from __future__ import annotations

import logging

from swing_atlas.services.market_activity_service import MarketActivityService
from swing_atlas.services.news_service import NewsService

logger = logging.getLogger(__name__)


async def news_refresh_job(service: NewsService) -> None:
    try:
        summary = await service.refresh_all()
        logger.info(
            "[NEWS] refreshed articles=%d mentions=%d scores=%d deals=%d deal_error=%s",
            summary.articles,
            summary.mentions,
            summary.scores,
            summary.deals,
            summary.deal_error or "none",
        )
    except Exception:
        logger.exception("[NEWS] refresh failed")


async def market_activity_refresh_job(service: MarketActivityService) -> None:
    try:
        summary = await service.refresh_all()
        logger.info(
            "[MARKET-ACTIVITY] refreshed rows=%d sources=%d", summary.rows, len(summary.sources)
        )
    except Exception:
        logger.exception("[MARKET-ACTIVITY] refresh failed")
