"""Tests for the research-confluence dossier -- mirrors the ~800-line block in
engine/src/api/swing.rs (research_trade_state through build_research_confluence).
"""

from swing_atlas.domain.swing.models import (
    CandidateSeed,
    HistoricalScreenerFeatureRow,
    LiveSignal,
    MarketRegime,
)
from swing_atlas.domain.swing.research_confluence import (
    build_research_confluence,
    collapse_research_model_matches,
    independent_current_daily_matches,
    model_confirmation_should_replace,
    research_confluence_state,
    research_model_family_key,
    research_model_family_label,
    research_trade_state,
    signal_as_of_is_current,
    signal_is_current_research_confirmation,
    signal_matches_selected,
    strategy_match_from_signal,
    strategy_status_is_research_valid,
)
from swing_atlas.domain.time_utils import now_ist


def _signal(**overrides: object) -> LiveSignal:
    defaults: dict[str, object] = dict(
        status="WATCH", label="l", reason="r", strategy_id="x", strategy_label="l",
        strategy_status="Watch", setup_family="f", score=70, as_of="now",
        trigger_price=None, trigger_source=None,
    )  # fmt: skip
    defaults.update(overrides)
    return LiveSignal(**defaults)  # type: ignore[arg-type]


def _seed(**overrides: object) -> CandidateSeed:
    defaults: dict[str, object] = dict(
        symbol="DEMO", company_name="Demo Ltd", tiers=[], liquidity_bucket="LARGE",
        open_price=100.0, high_price=102.0, low_price=99.0, last_price=101.0, prev_close=99.0,
        day_volume=500_000.0, day_change_pct=2.0, open_gap_pct=0.5, recovery_pct=1.0,
        distance_to_high_pct=1.0, intraday_range_pct=2.0, source="dhan-live",
    )  # fmt: skip
    defaults.update(overrides)
    return CandidateSeed(**defaults)  # type: ignore[arg-type]


def _baseline(**overrides: object) -> HistoricalScreenerFeatureRow:
    defaults: dict[str, object] = dict(
        symbol="DEMO", trade_date="2026-01-01", day_close=95.0, sma20=90.0, sma50=85.0,
        sma200=70.0, rsi10=55.0, avg_volume20=400_000.0, high_20d=101.0, high_52w=110.0,
        low_52w=60.0, atr14=2.0, prior_high20=100.0, prior_close3=98.0, rs60_rank=0.7,
        market_breadth200=0.6,
    )  # fmt: skip
    defaults.update(overrides)
    return HistoricalScreenerFeatureRow(**defaults)  # type: ignore[arg-type]


def test_research_trade_state_mapping() -> None:
    assert research_trade_state("ENTRY_NOW") == "ENTRY_READY"
    assert research_trade_state("WAIT_FOR_TRIGGER") == "ARMED"
    assert research_trade_state("WATCH") == "WATCH"
    assert research_trade_state("INVALIDATED") == "INVALIDATED"
    assert research_trade_state("NO_TRADE") == "NO_TRADE"
    assert research_trade_state("something-unknown") == "NO_TRADE"


def test_research_confluence_state_mapping() -> None:
    assert research_confluence_state("ENTRY_READY") == "CONFIRMED"
    assert research_confluence_state("ARMED") == "BUILDING"
    assert research_confluence_state("WATCH") == "WATCH_ONLY"
    assert research_confluence_state("INVALIDATED") == "BLOCKED"
    assert research_confluence_state("NO_TRADE") == "BLOCKED"
    assert research_confluence_state("???") == "MIXED"


def test_signal_matches_selected() -> None:
    a = _signal(strategy_id="momentum-core-v1", as_of="2026-01-01", status="ENTRY_NOW", score=90)
    b = _signal(strategy_id="momentum-core-v1", as_of="2026-01-01", status="ENTRY_NOW", score=90)
    c = _signal(strategy_id="momentum-core-v1", as_of="2026-01-01", status="ENTRY_NOW", score=80)

    assert signal_matches_selected(a, b)
    assert not signal_matches_selected(a, c)


def test_strategy_status_is_research_valid() -> None:
    assert strategy_status_is_research_valid("Candidate")
    assert strategy_status_is_research_valid("Watch")
    assert not strategy_status_is_research_valid("Rejected")
    assert not strategy_status_is_research_valid("Fragile")


def test_signal_as_of_is_current() -> None:
    today = now_ist().date().isoformat()
    fresh = _signal(as_of=f"dhan-live / baseline {today}")
    assert signal_as_of_is_current(fresh, 4)

    stale = _signal(as_of="dhan-live / baseline 2020-01-01")
    assert not signal_as_of_is_current(stale, 4)

    no_date = _signal(as_of="no date token here")
    assert not signal_as_of_is_current(no_date, 4)


def test_signal_is_current_research_confirmation_rejects_unscored_and_bad_status() -> None:
    today = now_ist().date().isoformat()
    unscored = _signal(as_of=today, strategy_status="Candidate", strategy_id="unscored")
    assert not signal_is_current_research_confirmation("daily", unscored)

    rejected = _signal(as_of=today, strategy_status="Rejected", strategy_id="swing-breakout-v1")
    assert not signal_is_current_research_confirmation("daily", rejected)

    invalidated = _signal(as_of=today, strategy_status="Candidate", status="INVALIDATED")
    assert not signal_is_current_research_confirmation("daily", invalidated)

    valid = _signal(as_of=today, strategy_status="Candidate", status="WAIT_FOR_TRIGGER")
    assert signal_is_current_research_confirmation("daily", valid)


def test_research_model_family_key_and_label() -> None:
    assert research_model_family_key("momentum-core-v1", "x") == "trend-breakout"
    assert research_model_family_key("pullback-20dma-v1", "x") == "pullback"
    assert research_model_family_key("tuned-panic-reversal-v1", "x") == "reversal"
    assert research_model_family_key("some-unknown-id", "Unscored") == "unscored"
    assert research_model_family_key("some-unknown-id", "Trend Filter") == "other"

    assert research_model_family_label("momentum-core-v1", "x") == "Trend Breakout"
    assert research_model_family_label("some-unknown-id", "Trend Filter") == "Trend Filter"


def test_model_confirmation_should_replace_prefers_selected_then_candidate_then_score() -> None:
    base = strategy_match_from_signal("daily", _signal(strategy_id="a", score=50), False)

    higher_score = strategy_match_from_signal("daily", _signal(strategy_id="a", score=90), False)
    assert model_confirmation_should_replace(higher_score, base)

    selected = strategy_match_from_signal("daily", _signal(strategy_id="a", score=10), True)
    assert model_confirmation_should_replace(selected, base)

    candidate_status = strategy_match_from_signal(
        "daily", _signal(strategy_id="a", score=10, strategy_status="Candidate"), False
    )
    watch_status = strategy_match_from_signal(
        "daily", _signal(strategy_id="a", score=90, strategy_status="Watch"), False
    )
    assert model_confirmation_should_replace(candidate_status, watch_status)


def test_collapse_research_model_matches_keeps_one_per_family_per_timeframe() -> None:
    momentum = _signal(strategy_id="momentum-core-v1", setup_family="Trend Breakout", score=80)
    near_high = _signal(strategy_id="near-52w-high-v1", setup_family="Trend Breakout", score=95)
    rsi10 = _signal(
        strategy_id="rsi10-pullback-reversion-v1", setup_family="Oversold Reversal", score=70
    )
    matches = [
        strategy_match_from_signal("daily", momentum, False),
        strategy_match_from_signal("daily", near_high, False),
        strategy_match_from_signal("daily", rsi10, False),
    ]  # fmt: skip

    collapsed = collapse_research_model_matches(matches)

    trend_breakout_matches = [
        m
        for m in collapsed
        if research_model_family_key(m.strategy_id, m.setup_family) == "trend-breakout"
    ]
    assert len(trend_breakout_matches) == 1
    assert trend_breakout_matches[0].strategy_id == "near-52w-high-v1"  # higher score wins
    assert len(collapsed) == 2


def test_independent_current_daily_matches_empty_without_baseline() -> None:
    assert independent_current_daily_matches(_seed(), None, {}) == []


def test_independent_current_daily_matches_empty_when_baseline_stale() -> None:
    matches = independent_current_daily_matches(_seed(), _baseline(trade_date="2020-01-01"), {})
    assert matches == []


def test_independent_current_daily_matches_momentum_core() -> None:
    today = now_ist().date().isoformat()
    seed = _seed(last_price=110.0, high_price=110.0, low_price=105.0, day_volume=600_000.0)
    baseline = _baseline(
        trade_date=today, day_close=100.0, sma20=95.0, sma50=90.0, sma200=70.0,
        high_20d=101.0, high_52w=113.0, low_52w=60.0, avg_volume20=400_000.0, rs60_rank=0.8,
    )  # fmt: skip

    matches = independent_current_daily_matches(seed, baseline, {})

    ids = {m.strategy_id for m in matches}
    assert "momentum-core-v1" in ids
    for match in matches:
        assert match.timeframe == "daily"
        assert not match.selected


def test_build_research_confluence_entry_ready_shape() -> None:
    daily = _signal(status="ENTRY_NOW", strategy_status="Candidate", score=90)
    seed = _seed()
    regime = MarketRegime(
        label="Risk-On Breadth",
        tone="bullish",
        summary="s",
        advances=10,
        declines=2,
        breadth_ratio=5.0,
    )

    confluence = build_research_confluence(
        seed, regime, None, daily, [], None, daily, 90, 3.0, 90.0
    )

    assert confluence.trade_state == "ENTRY_READY"
    assert confluence.confluence_state == "CONFIRMED"
    assert confluence.research_score == 90
    assert confluence.supporting_pillars == confluence.pillar_count
    assert any(
        item.pillar == "decision" and item.stance == "SUPPORT" for item in confluence.evidence
    )
