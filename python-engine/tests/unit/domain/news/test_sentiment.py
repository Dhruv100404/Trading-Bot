from swing_atlas.domain.news.models import NewsArticle, NewsMention
from swing_atlas.domain.news.sentiment import score_article_for_symbol


def _article(title: str, summary: str = "") -> NewsArticle:
    return NewsArticle(
        article_id="a1",
        source="test",
        source_kind="rss",
        category="markets",
        title=title,
        url="https://example.com/a1",
        summary=summary,
        published_at=None,
        fetched_at="2026-01-01 00:00:00",
    )


def _mention(confidence: float = 0.88) -> NewsMention:
    return NewsMention(
        article_id="a1",
        symbol="DEMO",
        security_id="1",
        company_name="Demo Ltd",
        match_confidence=confidence,
        matched_text="DEMO",
    )


def test_bullish_keywords_produce_bullish_direction() -> None:
    article = _article("Demo Ltd shares rally on record high profit gains")

    score = score_article_for_symbol(article, _mention())

    assert score.direction == "BULLISH"
    assert score.sentiment > 0.18
    assert score.model == "news_heuristic_v1"


def test_bearish_keywords_produce_bearish_direction() -> None:
    article = _article("Demo Ltd shares fall after probe and penalty for fraud")

    score = score_article_for_symbol(article, _mention())

    assert score.direction == "BEARISH"
    assert score.sentiment < -0.18


def test_no_keywords_with_low_impact_is_neutral() -> None:
    article = _article("Demo Ltd holds annual general meeting")

    score = score_article_for_symbol(article, _mention(confidence=0.3))

    assert score.direction == "NEUTRAL"
    assert score.sentiment == 0.0


def test_intraday_keyword_sets_intraday_horizon() -> None:
    article = _article("Demo Ltd sees block deal volume spike today")

    score = score_article_for_symbol(article, _mention())

    assert score.horizon == "intraday"


def test_swing_keyword_sets_swing_horizon_when_no_intraday_keyword() -> None:
    article = _article("Demo Ltd announces capacity expansion and merger talks")

    score = score_article_for_symbol(article, _mention())

    assert score.horizon == "swing"


def test_no_horizon_keywords_defaults_to_1d() -> None:
    article = _article("Demo Ltd shares rally on strong quarter")

    score = score_article_for_symbol(article, _mention())

    assert score.horizon == "1d"


def test_scores_are_clamped_and_rounded() -> None:
    article = _article(
        "Demo gains rally surges jumps beats record high wins order approval "
        "upgrade raises target buyback dividend strong demand stake buy expansion"
    )

    score = score_article_for_symbol(article, _mention(confidence=0.98))

    assert 0.0 <= score.impact_score <= 5.0
    assert 0.0 <= score.confidence <= 0.95
    assert score.impact_score == round(score.impact_score, 2)
