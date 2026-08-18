"""Tests for news-evidence application on swing candidates -- mirrors
engine/src/api/swing.rs's apply_news_evidence/news_direction_and_adjustment
and the hex-decode helpers.
"""

from swing_atlas.domain.swing.models import (
    LiveSignal,
    ResearchConfluence,
    ResearchScoreBreakdown,
    SwingCandidate,
)
from swing_atlas.domain.swing.news_confluence import (
    NewsConfluenceRow,
    apply_news_evidence,
    decode_hex_text,
    news_direction_and_adjustment,
    non_empty_text,
)
from swing_atlas.domain.swing.research_confluence import default_news_evidence_summary


def _news_row(**overrides: object) -> NewsConfluenceRow:
    defaults: dict[str, object] = dict(
        symbol="DEMO", article_count=0, bullish_articles=0, bearish_articles=0,
        avg_sentiment=0.0, max_impact=0.0, latest_reason="", latest_headline="",
        latest_source="", latest_url="",
    )  # fmt: skip
    defaults.update(overrides)
    return NewsConfluenceRow(**defaults)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> SwingCandidate:
    signal = LiveSignal(
        status="ENTRY_NOW", label="l", reason="r", strategy_id="momentum-core-v1",
        strategy_label="Momentum Core", strategy_status="Candidate", setup_family="f",
        score=80, as_of="now", trigger_price=None, trigger_source=None,
    )  # fmt: skip
    confluence = ResearchConfluence(
        research_score=80,
        research_state="ENTRY_READY",
        trade_state="ENTRY_READY",
        confluence_state="CONFIRMED",
        pillar_count=0,
        supporting_pillars=0,
        conflicting_pillars=0,
        score_breakdown=ResearchScoreBreakdown(
            model_score=80,
            technical_quality=70,
            trend_quality=70,
            volume_quality=70,
            regime_quality=70,
            risk_reward_quality=70,
            catalyst_adjustment=0,
            total_score=80,
        ),
        strategy_matches=[],
        evidence=[],
        risks=[],
        news=default_news_evidence_summary(),
    )
    defaults: dict[str, object] = dict(
        symbol="DEMO", company_name="Demo Ltd", setup_family="f", bias="Long", score=80,
        confidence="High Conviction", regime_fit=80, risk_reward=2.0, last_price=100.0,
        day_change_pct=1.0, open_gap_pct=0.5, distance_to_high_pct=1.0, liquidity_bucket="LARGE",
        entry_zone="Trigger Rs 100.00", stop_loss=95.0, target_price=110.0, expected_hold="10",
        thesis="thesis text", reasons=["reason 1"], risks=["risk 1"], source="dhan-live",
        live_signal=signal, confluence=confluence,
    )  # fmt: skip
    defaults.update(overrides)
    return SwingCandidate(**defaults)  # type: ignore[arg-type]


def test_decode_hex_text_roundtrip() -> None:
    encoded = "48656c6c6f"  # "Hello" hex-encoded, matching ClickHouse's hex()
    assert decode_hex_text(encoded) == "Hello"


def test_decode_hex_text_rejects_odd_length_or_bad_chars() -> None:
    assert decode_hex_text("abc") == ""
    assert decode_hex_text("zz") == ""
    assert decode_hex_text("") == ""


def test_non_empty_text() -> None:
    assert non_empty_text("  hello  ") == "hello"
    assert non_empty_text("   ") is None
    assert non_empty_text("") is None


def test_news_direction_and_adjustment_bullish() -> None:
    row = _news_row(bullish_articles=5, bearish_articles=1, avg_sentiment=0.3, max_impact=2.0)

    direction, adjustment = news_direction_and_adjustment(row)

    assert direction == "BULLISH"
    assert adjustment == 3 + 3 * 2  # capped at 3 bullish articles


def test_news_direction_and_adjustment_bearish() -> None:
    row = _news_row(bullish_articles=0, bearish_articles=2, avg_sentiment=-0.25, max_impact=1.5)

    direction, adjustment = news_direction_and_adjustment(row)

    assert direction == "BEARISH"
    assert adjustment == -(3 + 2 * 2)


def test_news_direction_and_adjustment_mixed_and_neutral() -> None:
    mixed = _news_row(bullish_articles=1, bearish_articles=1, avg_sentiment=0.0, max_impact=0.5)
    assert news_direction_and_adjustment(mixed) == ("MIXED", 0)

    neutral = _news_row(bullish_articles=0, bearish_articles=0)
    assert news_direction_and_adjustment(neutral) == ("NEUTRAL", 0)


def test_apply_news_evidence_bullish_boosts_score_and_adds_reason() -> None:
    candidate = _candidate(score=80)
    row = _news_row(
        bullish_articles=2, bearish_articles=0, avg_sentiment=0.3, max_impact=2.0,
        latest_reason="strong earnings beat",
    )  # fmt: skip

    apply_news_evidence(candidate, row)

    assert candidate.score == 80 + (3 + 2 * 2)
    assert candidate.confluence.score_breakdown.catalyst_adjustment == 3 + 2 * 2
    assert any("News confluence" in reason for reason in candidate.reasons)
    assert candidate.confluence.news.direction == "BULLISH"


def test_apply_news_evidence_bearish_adds_risk_not_reason() -> None:
    candidate = _candidate(score=80)
    row = _news_row(
        bullish_articles=0, bearish_articles=2, avg_sentiment=-0.3, max_impact=2.0,
        latest_reason="regulatory probe announced",
    )  # fmt: skip

    apply_news_evidence(candidate, row)

    assert candidate.score < 80
    assert any("Conflicting news" in risk for risk in candidate.risks)


def test_apply_news_evidence_score_clamped_to_99() -> None:
    candidate = _candidate(score=95)
    candidate.confluence.score_breakdown.model_score = 95
    row = _news_row(bullish_articles=5, bearish_articles=0, avg_sentiment=0.5, max_impact=5.0)

    apply_news_evidence(candidate, row)

    assert candidate.score <= 99
