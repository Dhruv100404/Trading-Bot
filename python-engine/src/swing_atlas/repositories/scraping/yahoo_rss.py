"""Yahoo Finance RSS ingestion -- mirrors engine/src/news.rs::fetch_rss_source."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from swing_atlas.domain.hashing import hash_id
from swing_atlas.domain.news.models import NewsArticle, NewsSource
from swing_atlas.domain.news.text_utils import canonical_url, clean_text, truncate_chars
from swing_atlas.domain.time_utils import now_sql, sql_time


def _parse_pub_date(entry: dict[str, Any]) -> str | None:
    parsed = entry.get("published_parsed")
    if parsed is None:
        return None
    epoch = calendar.timegm(parsed)  # feedparser normalizes *_parsed to UTC already
    return sql_time(datetime.fromtimestamp(epoch, tz=UTC))


async def fetch_rss_source(
    client: httpx.AsyncClient, source: NewsSource, max_per_source: int
) -> list[NewsArticle]:
    response = await client.get(source.url)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    fetched_at = now_sql()

    out: list[NewsArticle] = []
    # Takes the first max_per_source entries THEN filters -- if some of those get
    # filtered out, the result can have fewer than max_per_source articles (matches
    # the Rust original, which does not backfill from later entries).
    for entry in feed.entries[:max_per_source]:
        title = clean_text(entry.get("title", ""))
        url = canonical_url(entry.get("link") or entry.get("id") or "")
        if len(title) < 8 or not url:
            continue

        summary = clean_text(entry.get("summary", ""))
        out.append(
            NewsArticle(
                article_id=hash_id([source.source, url]),
                source=source.source,
                source_kind=source.kind.value,
                category=source.category,
                title=title,
                url=url,
                summary=truncate_chars(summary, 700),
                published_at=_parse_pub_date(entry),
                fetched_at=fetched_at,
            )
        )
    return out
