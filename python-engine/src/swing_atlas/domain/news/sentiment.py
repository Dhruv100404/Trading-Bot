"""Keyword-heuristic sentiment/impact scoring -- NOT an ML model, despite the
config having a (dead, unused) GEMINI_API_KEY. Mirrors engine/src/news.rs's
score_article_for_symbol; this weighted keyword-hit formula IS the entire model,
and the word lists below must be ported verbatim.
"""

from __future__ import annotations

from swing_atlas.domain.news.models import NewsArticle, NewsMention, NewsScore
from swing_atlas.domain.news.text_utils import normalize_for_match
from swing_atlas.domain.numeric import round2

MODEL_NAME = "news_heuristic_v1"

BULLISH_KEYWORDS = [
    "gain",
    "gains",
    "rally",
    "surges",
    "jumps",
    "beats",
    "profit jumps",
    "record high",
    "wins order",
    "order win",
    "approval",
    "upgrade",
    "raises target",
    "buyback",
    "dividend",
    "strong demand",
    "stake buy",
    "expansion",
]

BEARISH_KEYWORDS = [
    "falls",
    "fall",
    "drops",
    "plunges",
    "slumps",
    "loss",
    "losses",
    "misses",
    "downgrade",
    "cuts target",
    "probe",
    "penalty",
    "fine",
    "fraud",
    "default",
    "resigns",
    "weak demand",
    "selloff",
    "stake sale",
]

IMPACT_KEYWORDS = [
    "earnings",
    "results",
    "profit",
    "revenue",
    "margin",
    "order",
    "merger",
    "acquisition",
    "stake",
    "block deal",
    "bulk deal",
    "sebi",
    "rbi",
    "ipo",
    "buyback",
    "dividend",
    "approval",
    "guidance",
    "promoter",
]

INTRADAY_KEYWORDS = [
    "block deal",
    "bulk deal",
    "intraday",
    "today",
    "opening",
    "pre market",
    "volume",
]

SWING_KEYWORDS = [
    "ipo",
    "merger",
    "acquisition",
    "capacity",
    "expansion",
    "capex",
    "guidance",
    "tariff",
]


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if normalize_for_match(keyword) in text]


def _build_reason(bullish: list[str], bearish: list[str], impact: list[str]) -> str:
    parts = []
    if bullish:
        parts.append(f"bullish: {', '.join(bullish[:3])}")
    if bearish:
        parts.append(f"bearish: {', '.join(bearish[:3])}")
    if impact:
        parts.append(f"impact: {', '.join(impact[:3])}")
    return " | ".join(parts) if parts else "symbol mention with no strong directional keyword"


def score_article_for_symbol(article: NewsArticle, mention: NewsMention) -> NewsScore:
    text = normalize_for_match(f"{article.title} {article.summary}")
    bullish = keyword_hits(text, BULLISH_KEYWORDS)
    bearish = keyword_hits(text, BEARISH_KEYWORDS)
    impact = keyword_hits(text, IMPACT_KEYWORDS)
    intraday = keyword_hits(text, INTRADAY_KEYWORDS)
    swing = keyword_hits(text, SWING_KEYWORDS)

    bull_count = float(len(bullish))
    bear_count = float(len(bearish))
    signed = bull_count - bear_count
    total_directional = max(bull_count + bear_count, 1.0)
    sentiment = max(-1.0, min(1.0, signed / total_directional))

    impact_score = max(
        0.0,
        min(
            5.0,
            1.0
            + len(impact) * 0.38
            + (bull_count + bear_count) * 0.24
            + mention.match_confidence * 0.75,
        ),
    )
    confidence = max(
        0.0,
        min(
            0.95,
            0.32
            + mention.match_confidence * 0.35
            + len(impact) * 0.05
            + (bull_count + bear_count) * 0.04,
        ),
    )

    if sentiment > 0.18:
        direction = "BULLISH"
    elif sentiment < -0.18:
        direction = "BEARISH"
    elif impact_score >= 2.2:
        direction = "WATCH"
    else:
        direction = "NEUTRAL"

    if intraday:
        horizon = "intraday"
    elif swing:
        horizon = "swing"
    else:
        horizon = "1d"

    return NewsScore(
        article_id=article.article_id,
        symbol=mention.symbol,
        sentiment=round2(sentiment),
        impact_score=round2(impact_score),
        direction=direction,
        horizon=horizon,
        confidence=round2(confidence),
        reason=_build_reason(bullish, bearish, impact),
        model=MODEL_NAME,
    )


def score_mentions(articles: list[NewsArticle], mentions: list[NewsMention]) -> list[NewsScore]:
    articles_by_id = {article.article_id: article for article in articles}
    scores = []
    for mention in mentions:
        article = articles_by_id.get(mention.article_id)
        if article is None:
            continue
        scores.append(score_article_for_symbol(article, mention))
    return scores
