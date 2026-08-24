"""Tests for the historical-screener row derivation -- mirrors
engine/src/api/swing.rs::map_historical_screener_row and the fresh-signals
paper-eligibility/staging rules.
"""

from datetime import timedelta

from swing_atlas.domain.swing.models import HistoricalScreenerFeatureRow, HistoricalScreenerRow
from swing_atlas.domain.swing.screener import (
    historical_trade_plan,
    is_paper_eligible_signal,
    map_historical_screener_row,
    matches_setup_filter,
    matches_strategy_filter,
    paper_rule_for_strategy,
    quantity_for_capital,
    signal_date_is_fresh,
    signal_key_for,
)
from swing_atlas.domain.time_utils import now_ist


def _feature_row(**overrides: object) -> HistoricalScreenerFeatureRow:
    defaults: dict[str, object] = dict(
        symbol="DEMO",
        trade_date="2026-01-01",
        day_close=90.0,
        day_high=91.0,
        day_low=88.0,
        day_volume="300000",
        sma20=80.5,
        sma50=90.2,
        avg_volume20=400_000.0,
        high_20d=105.0,
        high_52w=200.0,
        low_52w=30.0,
        prior_high20=100.0,
        prior_close3=100.0,
        rs60_rank=0.5,
        market_breadth200=0.5,
    )
    defaults.update(overrides)
    return HistoricalScreenerFeatureRow(**defaults)  # type: ignore[arg-type]


def _row(**overrides: object) -> HistoricalScreenerRow:
    defaults: dict[str, object] = dict(
        symbol="DEMO",
        as_of="2026-01-01",
        setup_family="Trend Filter",
        strategy_id="unlinked-screener",
        strategy_label="Unlinked Screen",
        strategy_status="Unlinked",
        score=50,
        trend_label="Needs Work",
        close=90.0,
        sma20=80.5,
        sma50=90.2,
        avg_volume20=400_000.0,
        volume_ratio=0.75,
        distance_to_20d_high_pct=14.29,
        distance_to_52w_high_pct=55.0,
        range_position_pct=35.29,
        atr14=3.0,
        atr_pct=3.33,
        close_location=66.67,
        gap_pct=0.0,
        rs60_rank=50.0,
        rs120_rank=50.0,
        market_breadth200=50.0,
        planned_entry="Next session confirmation near Rs 90.00",
        stop_loss=85.5,
        target_price=99.0,
        risk_reward=2.0,
    )
    defaults.update(overrides)
    return HistoricalScreenerRow(**defaults)  # type: ignore[arg-type]


def test_map_historical_screener_row_falls_through_to_trend_filter() -> None:
    row = map_historical_screener_row(_feature_row(), {})

    assert row is not None
    assert row.setup_family == "Trend Filter"
    assert row.strategy_id == "unlinked-screener"
    assert row.strategy_status == "Unlinked"


def test_map_historical_screener_row_missing_required_field_is_none() -> None:
    row = map_historical_screener_row(_feature_row(sma20=None), {})

    assert row is None


def test_map_historical_screener_row_non_positive_close_is_none() -> None:
    row = map_historical_screener_row(_feature_row(day_close=0.0), {})

    assert row is None


def test_map_historical_screener_row_panic_reversal() -> None:
    row = map_historical_screener_row(
        _feature_row(
            day_close=100.0,
            day_high=101.0,
            day_low=95.0,
            ret3=-0.10,
            range_atr=1.5,
            close_location=0.7,
            recovery_from_low_pct=0.02,
            atr14=2.0,
            high_20d=101.0,
            high_52w=110.0,
            low_52w=80.0,
        ),
        {},
    )

    assert row is not None
    assert row.setup_family == "Panic Reversal"
    assert row.strategy_id == "tuned-panic-reversal-v1"


def test_map_historical_screener_row_rsi10_pullback() -> None:
    row = map_historical_screener_row(
        _feature_row(
            day_close=100.0,
            day_high=101.0,
            day_low=99.0,
            sma20=95.0,
            sma50=90.0,
            sma200=90.0,
            rsi10=20.0,
            high_20d=102.0,
            high_52w=120.0,
            low_52w=60.0,
        ),  # fmt: skip
        {},
    )

    assert row is not None
    assert row.setup_family == "RSI10 Pullback Reversion"
    assert row.strategy_id == "rsi10-pullback-reversion-v1"


def test_map_historical_screener_row_uses_strategy_status_override() -> None:
    row = map_historical_screener_row(_feature_row(), {"unlinked-screener": "Watch"})

    assert row is not None
    assert row.strategy_status == "Watch"


def test_historical_trade_plan_ma_breakout() -> None:
    planned_entry, stop_loss, target_price, risk_reward = historical_trade_plan(
        "MA Breakout", close=100.0, low=97.0, sma20=95.0, atr14=2.0,
        prior_high20=99.0, prior_close3=95.0, prior_low20=90.0,
    )  # fmt: skip

    assert "Breakout trigger above Rs 99.00" in planned_entry
    assert stop_loss < 100.0
    assert target_price > 100.0
    assert risk_reward > 0


def test_historical_trade_plan_panic_reversal_targets_pre_panic_close() -> None:
    _, _, target_price, _ = historical_trade_plan(
        "Panic Reversal", close=100.0, low=90.0, sma20=95.0, atr14=2.0,
        prior_high20=99.0, prior_close3=110.0, prior_low20=90.0,
    )  # fmt: skip

    assert target_price == 110.0


def test_historical_trade_plan_stop_loss_never_exceeds_close() -> None:
    _, stop_loss, _, _ = historical_trade_plan(
        "Trend Filter", close=100.0, low=99.5, sma20=99.0, atr14=0.01,
        prior_high20=100.5, prior_close3=100.0, prior_low20=98.0,
    )  # fmt: skip

    assert stop_loss < 100.0


def test_matches_setup_filter() -> None:
    row = _row(setup_family="Panic Reversal")

    assert matches_setup_filter(row, "all")
    assert matches_setup_filter(row, "panic")
    assert matches_setup_filter(row, "panic-reversal")
    assert not matches_setup_filter(row, "ma")


def test_matches_strategy_filter() -> None:
    row = _row(
        strategy_id="rsi10-pullback-reversion-v1",
        strategy_label="RSI10 Pullback",
        strategy_status="Candidate",
    )

    assert matches_strategy_filter(row, "all")
    assert matches_strategy_filter(row, "fresh")
    assert matches_strategy_filter(row, "rsi10-pullback-reversion-v1")
    assert matches_strategy_filter(row, "rsi10-pullback")
    assert matches_strategy_filter(row, "candidate")
    assert not matches_strategy_filter(row, "swing-breakout-v1")


def test_signal_key_for() -> None:
    row = _row(symbol="TCS", strategy_id="near-52w-high-v1")

    assert signal_key_for(row) == "TCS|near-52w-high-v1"


def test_signal_date_is_fresh() -> None:
    today = now_ist().date()

    assert signal_date_is_fresh(today.isoformat())
    assert signal_date_is_fresh((today - timedelta(days=4)).isoformat())
    assert not signal_date_is_fresh((today - timedelta(days=5)).isoformat())
    assert not signal_date_is_fresh("not-a-date")


def test_paper_rule_for_strategy_known_and_unknown() -> None:
    rule = paper_rule_for_strategy("rsi10-pullback-reversion-v1")
    assert rule is not None
    assert rule.stop_loss_pct == 4.0
    assert rule.take_profit_pct == 4.0

    assert paper_rule_for_strategy("some-unknown-id") is None


def test_is_paper_eligible_signal() -> None:
    today = now_ist().date().isoformat()
    eligible = _row(
        strategy_id="rsi10-pullback-reversion-v1",
        strategy_status="Candidate",
        close=100.0,
        as_of=today,
    )
    assert is_paper_eligible_signal(eligible)

    wrong_status = _row(
        strategy_id="rsi10-pullback-reversion-v1", strategy_status="Rejected", as_of=today
    )
    assert not is_paper_eligible_signal(wrong_status)

    no_rule = _row(strategy_id="unlinked-screener", strategy_status="Candidate", as_of=today)
    assert not is_paper_eligible_signal(no_rule)

    stale = _row(
        strategy_id="rsi10-pullback-reversion-v1", strategy_status="Candidate", as_of="2020-01-01"
    )
    assert not is_paper_eligible_signal(stale)


def test_quantity_for_capital() -> None:
    assert quantity_for_capital(price=100.0, capital=50_000.0) == 500
    assert quantity_for_capital(price=0.0, capital=50_000.0) == 5_000_000
    assert quantity_for_capital(price=100_000.0, capital=1.0) == 1
