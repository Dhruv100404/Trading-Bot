"""Value objects for the news domain -- mirrors the structs in engine/src/news.rs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NewsSourceKind(StrEnum):
    RSS = "rss"
    MONEYCONTROL_HTML = "moneycontrol_html"

    @classmethod
    def from_str(cls, value: str) -> NewsSourceKind | None:
        normalized = value.strip().lower()
        if normalized == "rss":
            return cls.RSS
        if normalized in ("html", "moneycontrol_html", "moneycontrol"):
            return cls.MONEYCONTROL_HTML
        return None


@dataclass(frozen=True, slots=True)
class NewsSource:
    source: str
    kind: NewsSourceKind
    category: str
    url: str


@dataclass(frozen=True, slots=True)
class NewsArticle:
    article_id: str
    source: str
    source_kind: str
    category: str
    title: str
    url: str
    summary: str
    published_at: str | None
    fetched_at: str


@dataclass(frozen=True, slots=True)
class WatchlistCompany:
    symbol: str
    security_id: str
    company_name: str


@dataclass(frozen=True, slots=True)
class NewsMention:
    article_id: str
    symbol: str
    security_id: str
    company_name: str
    match_confidence: float
    matched_text: str


@dataclass(frozen=True, slots=True)
class NewsScore:
    article_id: str
    symbol: str
    sentiment: float
    impact_score: float
    direction: str
    horizon: str
    confidence: float
    reason: str
    model: str


@dataclass(frozen=True, slots=True)
class NseLargeDeal:
    deal_id: str
    deal_type: str
    deal_date: str
    deal_date_raw: str
    symbol: str
    security_name: str
    client_name: str
    side: str
    quantity: float
    price: float
    value_lakh: float
    source_url: str
    fetched_at: str


def default_sources() -> list[NewsSource]:
    return [
        NewsSource(
            source="moneycontrol",
            kind=NewsSourceKind.MONEYCONTROL_HTML,
            category="markets",
            url="https://www.moneycontrol.com/news/business/markets/",
        ),
        NewsSource(
            source="moneycontrol",
            kind=NewsSourceKind.MONEYCONTROL_HTML,
            category="stocks",
            url="https://www.moneycontrol.com/news/business/stocks/",
        ),
        NewsSource(
            source="yahoo_finance",
            kind=NewsSourceKind.RSS,
            category="latest",
            url="https://finance.yahoo.com/news/rssindex",
        ),
    ]


def parse_configured_sources(raw: str) -> list[NewsSource]:
    """Parses NEWS_SOURCES ('source|kind|category|url' entries, ';'-separated).
    Falls back to default_sources() if raw is blank or nothing valid parses."""
    if not raw.strip():
        return default_sources()

    sources: list[NewsSource] = []
    for entry in raw.split(";"):
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) != 4:
            continue
        kind = NewsSourceKind.from_str(parts[1])
        if kind is None:
            continue
        sources.append(NewsSource(source=parts[0], kind=kind, category=parts[2], url=parts[3]))

    return sources if sources else default_sources()
