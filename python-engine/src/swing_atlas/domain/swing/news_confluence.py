"""Applies recent news evidence to swing candidates -- mirrors
engine/src/api/swing.rs's apply_news_confluence/apply_news_evidence/
news_direction_and_adjustment and the hex-decode helpers guarding untrusted
feed text.

A news catalyst can only adjust ranking (a small, capped score nudge plus a
research evidence/risk item) -- it can never independently turn a non-entry
signal into an entry. The trade_state/live_signal fields this function never
touches remain the sole authority for that.
"""

from __future__ import annotations

from dataclasses import dataclass

from swing_atlas.domain.swing.models import SwingCandidate
from swing_atlas.domain.swing.research_confluence import (
    refresh_confluence_pillar_counts,
    research_evidence_item,
)

NEWS_CONFLUENCE_LOOKBACK_HOURS = 72


@dataclass(frozen=True, slots=True)
class NewsConfluenceRow:
    symbol: str
    article_count: int
    bullish_articles: int
    bearish_articles: int
    avg_sentiment: float
    max_impact: float
    latest_reason: str
    latest_headline: str
    latest_source: str
    latest_url: str


def non_empty_text(value: str) -> str | None:
    trimmed = value.strip()
    return trimmed if trimmed else None


def hex_value(byte: int) -> int | None:
    if 0x30 <= byte <= 0x39:  # '0'-'9'
        return byte - 0x30
    if 0x61 <= byte <= 0x66:  # 'a'-'f'
        return byte - 0x61 + 10
    if 0x41 <= byte <= 0x46:  # 'A'-'F'
        return byte - 0x41 + 10
    return None


def decode_hex_text(value: str) -> str:
    """Feed text is external input. The ClickHouse query hex-encodes it before
    this decode step so one malformed source byte can't hide news evidence for
    every scanner symbol."""
    if not value or len(value) % 2 != 0:
        return ""
    raw = value.encode("ascii", errors="ignore")
    if len(raw) != len(value):
        return ""
    decoded = bytearray()
    for i in range(0, len(raw), 2):
        high = hex_value(raw[i])
        low = hex_value(raw[i + 1])
        if high is None or low is None:
            return ""
        decoded.append((high << 4) | low)
    return decoded.decode("utf-8", errors="replace")


def news_direction_and_adjustment(evidence: NewsConfluenceRow) -> tuple[str, int]:
    positive = (
        evidence.bullish_articles > evidence.bearish_articles
        and evidence.avg_sentiment >= 0.18
        and evidence.max_impact >= 1.2
    )
    negative = (
        evidence.bearish_articles > evidence.bullish_articles
        and evidence.avg_sentiment <= -0.18
        and evidence.max_impact >= 1.2
    )
    if positive:
        return "BULLISH", 3 + min(evidence.bullish_articles, 3) * 2
    if negative:
        return "BEARISH", -(3 + min(evidence.bearish_articles, 3) * 2)
    if evidence.bullish_articles > 0 and evidence.bearish_articles > 0:
        return "MIXED", 0
    return "NEUTRAL", 0


def apply_news_evidence(candidate: SwingCandidate, evidence: NewsConfluenceRow) -> None:
    direction, adjustment = news_direction_and_adjustment(evidence)
    reason = (
        non_empty_text(evidence.latest_reason)
        or "Recent news was scored without a reusable explanation."
    )

    candidate.confluence.news.lookback_hours = NEWS_CONFLUENCE_LOOKBACK_HOURS
    candidate.confluence.news.article_count = evidence.article_count
    candidate.confluence.news.bullish_articles = evidence.bullish_articles
    candidate.confluence.news.bearish_articles = evidence.bearish_articles
    candidate.confluence.news.average_sentiment = evidence.avg_sentiment
    candidate.confluence.news.max_impact = evidence.max_impact
    candidate.confluence.news.direction = direction
    candidate.confluence.news.score_adjustment = adjustment
    candidate.confluence.news.latest_reason = non_empty_text(evidence.latest_reason)
    candidate.confluence.news.latest_headline = non_empty_text(evidence.latest_headline)
    candidate.confluence.news.latest_source = non_empty_text(evidence.latest_source)
    candidate.confluence.news.latest_url = non_empty_text(evidence.latest_url)

    total_score = min(max(candidate.confluence.score_breakdown.model_score + adjustment, 0), 99)
    candidate.score = total_score
    candidate.confluence.research_score = total_score
    candidate.confluence.score_breakdown.catalyst_adjustment = adjustment
    candidate.confluence.score_breakdown.total_score = total_score

    if adjustment > 0:
        detail = (
            f"{evidence.bullish_articles} bullish article(s), average sentiment "
            f"{evidence.avg_sentiment:+.2f}, and impact up to {evidence.max_impact:.1f}/5. {reason}"
        )
        candidate.reasons.append(f"News confluence: {detail}")
        candidate.thesis = (
            f"{candidate.thesis} Price setup is supported by independent recent news evidence. "
            f"{reason}"
        )
        candidate.confluence.evidence.append(
            research_evidence_item("catalyst", "SUPPORT", "Recent bullish catalyst", detail)
        )
    elif adjustment < 0:
        detail = (
            f"{evidence.bearish_articles} bearish article(s), average sentiment "
            f"{evidence.avg_sentiment:+.2f}, and impact up to {evidence.max_impact:.1f}/5. {reason}"
        )
        candidate.risks.append(f"Conflicting news: {detail}")
        candidate.confluence.risks.append(
            research_evidence_item("catalyst", "RISK", "Recent conflicting news", detail)
        )
    elif evidence.article_count > 0:
        candidate.confluence.evidence.append(
            research_evidence_item(
                "catalyst",
                "NEUTRAL",
                "Recent news is not directional",
                f"{evidence.article_count} recent scored article(s) are {direction.lower()} "
                f"rather than a corroborating catalyst. {reason}",
            )
        )

    refresh_confluence_pillar_counts(candidate.confluence)


def apply_news_confluence(
    candidates: list[SwingCandidate], evidence_by_symbol: dict[str, NewsConfluenceRow]
) -> None:
    for candidate in candidates:
        evidence = evidence_by_symbol.get(candidate.symbol)
        if evidence is not None:
            apply_news_evidence(candidate, evidence)
