"""News refresh orchestration -- mirrors engine/src/api/news.rs::refresh_all.

A single source failing (scrape error, RSS parse error) never fails the whole
refresh -- it's logged and skipped, matching fetch_articles_from_sources. Only
ClickHouse write failures propagate as a hard error to the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from swing_atlas.config import Settings
from swing_atlas.domain.news.aliases import resolve_mentions
from swing_atlas.domain.news.models import (
    NewsArticle,
    NewsSource,
    NewsSourceKind,
    parse_configured_sources,
)
from swing_atlas.domain.news.sentiment import score_mentions
from swing_atlas.domain.time_utils import now_sql
from swing_atlas.repositories.news_repo import NewsRepo
from swing_atlas.repositories.scraping.moneycontrol_news import fetch_moneycontrol_html_source
from swing_atlas.repositories.scraping.nse_deals import fetch_nse_large_deals
from swing_atlas.repositories.scraping.yahoo_rss import fetch_rss_source
from swing_atlas.repositories.watchlist_repo import WatchlistRepo

logger = logging.getLogger(__name__)

_USER_AGENT = "swing-atlas-news/1.0"
_FETCH_TIMEOUT_SECS = 15.0


@dataclass(frozen=True, slots=True)
class NewsRefreshSummary:
    ok: bool
    articles: int
    mentions: int
    scores: int
    deals: int
    watchlist_companies: int
    sources: list[NewsSource]
    refreshed_at: str
    deal_error: str | None


async def fetch_articles(sources: list[NewsSource], max_per_source: int) -> list[NewsArticle]:
    all_articles: list[NewsArticle] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT}, timeout=_FETCH_TIMEOUT_SECS
    ) as client:
        for source in sources:
            try:
                if source.kind == NewsSourceKind.RSS:
                    fetched = await fetch_rss_source(client, source, max_per_source)
                else:
                    fetched = await fetch_moneycontrol_html_source(client, source, max_per_source)
                all_articles.extend(fetched)
            except Exception as exc:  # noqa: BLE001 -- one source failing shouldn't sink the refresh
                logger.warning(
                    "[NEWS] source=%s category=%s fetch failed: %s",
                    source.source,
                    source.category,
                    exc,
                )

    seen: set[str] = set()
    deduped: list[NewsArticle] = []
    for article in all_articles:
        if article.url in seen:
            continue
        seen.add(article.url)
        deduped.append(article)
    return deduped


class NewsService:
    def __init__(
        self, settings: Settings, news_repo: NewsRepo, watchlist_repo: WatchlistRepo
    ) -> None:
        self._settings = settings
        self._news_repo = news_repo
        self._watchlist_repo = watchlist_repo

    async def refresh_all(self) -> NewsRefreshSummary:
        max_per_source = max(5, min(100, self._settings.news_max_per_source))
        sources = parse_configured_sources(self._settings.news_sources)

        articles = await fetch_articles(sources, max_per_source)
        companies = await self._watchlist_repo.load_companies()
        mentions = resolve_mentions(articles, companies)
        scores = score_mentions(articles, mentions)

        deal_error: str | None = None
        try:
            deals = await fetch_nse_large_deals(self._settings.nse_large_deals_lookback_days)
        except Exception as exc:  # noqa: BLE001 -- deals fetch failure is captured, not fatal
            logger.warning("[NEWS] NSE large-deals refresh failed: %s", exc)
            deal_error = str(exc)
            deals = []

        await self._news_repo.insert_articles(articles)
        await self._news_repo.insert_mentions(mentions)
        await self._news_repo.insert_scores(scores)
        await self._news_repo.insert_large_deals(deals)

        return NewsRefreshSummary(
            ok=True,
            articles=len(articles),
            mentions=len(mentions),
            scores=len(scores),
            deals=len(deals),
            watchlist_companies=len(companies),
            sources=sources,
            refreshed_at=now_sql(),
            deal_error=deal_error,
        )
