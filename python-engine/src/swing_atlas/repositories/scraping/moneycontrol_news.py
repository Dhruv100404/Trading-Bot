"""Moneycontrol news link scraper -- mirrors engine/src/news.rs::fetch_moneycontrol_html_source.

No structured data extraction: walks every anchor tag and keeps links that look
like Moneycontrol article pages. Cruder than the market-activity scraper but simpler
and less coupled to any particular frontend framework version.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup, Tag

from swing_atlas.domain.hashing import hash_id
from swing_atlas.domain.news.models import NewsArticle, NewsSource
from swing_atlas.domain.news.text_utils import canonical_url, clean_text, resolve_url
from swing_atlas.domain.time_utils import now_sql


def _href_of(element: Tag) -> str | None:
    href = element.get("href")
    if isinstance(href, list):
        return href[0] if href else None
    return href if isinstance(href, str) else None


async def fetch_moneycontrol_html_source(
    client: httpx.AsyncClient, source: NewsSource, max_per_source: int
) -> list[NewsArticle]:
    response = await client.get(source.url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    fetched_at = now_sql()

    seen: set[str] = set()
    out: list[NewsArticle] = []
    for element in soup.select("a[href]"):
        href = _href_of(element)
        if not href:
            continue
        url = resolve_url(source.url, href)
        if url is None:
            continue
        url = canonical_url(url)
        if "moneycontrol.com/news/" not in url or not url.endswith(".html") or "/photos/" in url:
            continue

        title = clean_text(element.get_text(" "))
        if len(title) < 25 or url in seen:
            continue
        seen.add(url)

        out.append(
            NewsArticle(
                article_id=hash_id([source.source, url]),
                source=source.source,
                source_kind=source.kind.value,
                category=source.category,
                title=title,
                url=url,
                summary="",
                published_at=None,
                fetched_at=fetched_at,
            )
        )
        if len(out) >= max_per_source:
            break

    return out
