"""Tests for evaluate_live_signal -- the single authority for the live entry/exit
decision. Mirrors engine/src/api/swing.rs::evaluate_live_signal.
"""

from swing_atlas.domain.swing.live_signal import (
    choose_live_signal,
    evaluate_live_signal,
    live_signal_rank,
    parse_volume,
    replace_latest_average,
    signal_confidence,
)
from swing_atlas.domain.swing.models import CandidateSeed, HistoricalScreenerFeatureRow, LiveSignal


def _seed(**overrides: object) -> CandidateSeed:
    defaults: dict[str, object] = dict(
        symbol="DEMO",
        company_name="Demo Ltd",
        tiers=[],
        liquidity_bucket="LARGE",
        open_price=100.0,
        high_price=102.0,
        low_price=99.0,
        last_price=101.0,
        prev_close=99.0,
        day_volume=500_000.0,
        day_change_pct=2.0,
        open_gap_pct=0.5,
        recovery_pct=1.0,
        distance_to_high_pct=1.0,
        intraday_range_pct=2.0,
        source="dhan-live",
    )
    defaults.update(overrides)
    return CandidateSeed(**defaults)  # type: ignore[arg-type]


def _baseline(**overrides: object) -> HistoricalScreenerFeatureRow:
    defaults: dict[str, object] = dict(
        symbol="DEMO",
        trade_date="2026-01-01",
        day_close=95.0,
        sma20=90.0,
        sma50=85.0,
        sma200=70.0,
        rsi10=55.0,
        avg_volume20=400_000.0,
        high_20d=101.0,
        high_52w=110.0,
        low_52w=60.0,
        atr14=2.0,
        prior_high20=100.0,
        prior_close3=98.0,
        rs60_rank=0.7,
        market_breadth200=0.6,
    )
    defaults.update(overrides)
    return HistoricalScreenerFeatureRow(**defaults)  # type: ignore[arg-type]


def test_no_baseline_returns_need_history() -> None:
    signal = evaluate_live_signal(_seed(), None, {}, entry_window_open=True)

    assert signal.status == "WAIT_FOR_TRIGGER"
    assert signal.label == "Need History"
    assert signal.strategy_id == "unscored"
    assert signal.score == 0


def test_non_positive_close_returns_default_signal() -> None:
    signal = evaluate_live_signal(
        _seed(last_price=0.0, high_price=0.0, low_price=0.0), _baseline(), {}, True
    )

    assert signal.status == "WAIT_FOR_TRIGGER"
    assert signal.label == "Wait For Trigger"
    assert signal.strategy_id == "unscored"


def test_lost_structure_below_sma50_is_invalidated() -> None:
    # close far below sma50*0.985 -> INVALIDATED regardless of setup match.
    seed = _seed(last_price=50.0, high_price=51.0, low_price=49.0)
    signal = evaluate_live_signal(seed, _baseline(sma50=85.0), {}, True)

    assert signal.status == "INVALIDATED"
    assert signal.label == "Invalidated"


def test_unlinked_screener_waits_for_trigger() -> None:
    # A baseline that doesn't match any setup_family/strategy rule at all --
    # trend down (sma20 > sma50), far from the 52w high, no panic/RSI/pullback
    # condition met -> falls through to "Trend Filter" setup_family and the
    # "unlinked-screener" catch-all strategy_id.
    seed = _seed(last_price=100.0, high_price=101.0, low_price=99.0, day_volume=300_000.0)
    baseline = _baseline(
        day_close=90.0, sma20=80.5, sma50=90.2, sma200=94.9, rsi10=55.0,
        avg_volume20=400_000.0, high_20d=105.0, high_52w=200.0, low_52w=30.0,
        prior_high20=100.0, prior_close3=100.0, rs60_rank=0.5, market_breadth200=0.5,
    )  # fmt: skip

    signal = evaluate_live_signal(seed, baseline, {}, entry_window_open=True)

    assert signal.setup_family == "Trend Filter"
    assert signal.strategy_id == "unlinked-screener"
    assert signal.status == "WAIT_FOR_TRIGGER"
    assert signal.label == "Wait For Trigger"


def _near_52w_high_fixture() -> tuple[CandidateSeed, HistoricalScreenerFeatureRow, str]:
    """A fixture that reliably lands on the near-52w-high-volume-v3 strategy_id
    (score 90, setup_family "Near 52W High") -- verified directly against
    evaluate_live_signal's output, not hand-derived, since the setup_family
    evaluate_live_signal computes locally and the setup_family strings
    strategy_match_for_screener checks only partially overlap.
    """
    seed = _seed(last_price=111.0, high_price=111.0, low_price=105.0, day_volume=600_000.0)
    baseline = _baseline(
        day_close=100.0, sma20=200.0, sma50=95.0, sma200=70.0, rsi10=55.0,
        avg_volume20=400_000.0, high_20d=109.0, high_52w=110.0, low_52w=80.0,
        prior_high20=100.0, prior_close3=111.0, rs60_rank=0.8, market_breadth200=0.6,
    )  # fmt: skip
    baseline_signal = evaluate_live_signal(seed, baseline, {}, entry_window_open=True)
    return seed, baseline, baseline_signal.strategy_id


def test_candidate_status_with_trigger_hit_and_window_open_enters_now() -> None:
    seed, baseline, strategy_id = _near_52w_high_fixture()

    signal = evaluate_live_signal(
        seed, baseline, {strategy_id: "Candidate"}, entry_window_open=True
    )

    assert signal.setup_family == "Near 52W High"
    assert signal.status == "ENTRY_NOW"
    assert signal.label == "Enter Now"
    assert signal.trigger_price is not None


def test_candidate_status_with_entry_window_closed_is_signal_ready() -> None:
    seed, baseline, strategy_id = _near_52w_high_fixture()

    signal = evaluate_live_signal(
        seed, baseline, {strategy_id: "Candidate"}, entry_window_open=False
    )

    assert signal.status == "WAIT_FOR_TRIGGER"
    assert signal.label == "Signal Ready"


def test_watch_status_produces_watch_signal() -> None:
    seed, baseline, strategy_id = _near_52w_high_fixture()

    signal = evaluate_live_signal(seed, baseline, {strategy_id: "Watch"}, entry_window_open=True)

    assert signal.status == "WATCH"
    assert signal.label == "Watch Only"


def test_no_status_override_defaults_to_no_trade_for_fragile_strategy() -> None:
    seed, baseline, strategy_id = _near_52w_high_fixture()

    signal = evaluate_live_signal(seed, baseline, {}, entry_window_open=True)

    assert signal.strategy_status == "Fragile"  # near-52w-high-volume-v3's default status
    assert signal.status == "NO_TRADE"
    assert signal.label == "No Trade"


def test_panic_reversal_setup_takes_priority_over_ma_breakout() -> None:
    # ret3 <= -0.08 with a strong intraday recovery from the low -- panic reversal
    # rule fires; this must win even though the price is also technically near a
    # 20-day high (panic reversal is checked first in the priority chain).
    seed = _seed(last_price=100.0, high_price=100.0, low_price=90.0, day_volume=600_000.0)
    baseline = _baseline(
        sma20=95.0, sma50=90.0, sma200=70.0, high_20d=101.0, high_52w=120.0, low_52w=60.0,
        avg_volume20=400_000.0, atr14=2.0, prior_close3=110.0,  # ret3 = 100/110 - 1 ~= -0.09
    )  # fmt: skip

    signal = evaluate_live_signal(seed, baseline, {}, entry_window_open=True)

    assert signal.setup_family == "Panic Reversal"
    assert signal.strategy_id == "tuned-panic-reversal-v1"


def test_rsi10_pullback_setup() -> None:
    seed = _seed(last_price=100.0, high_price=101.0, low_price=99.0, day_volume=300_000.0)
    baseline = _baseline(
        sma20=95.0, sma50=90.0, sma200=80.0, rsi10=20.0, high_20d=102.0, high_52w=120.0,
        low_52w=60.0, avg_volume20=400_000.0,
    )  # fmt: skip

    signal = evaluate_live_signal(seed, baseline, {}, entry_window_open=True)

    assert signal.setup_family == "RSI10 Pullback Reversion"
    assert signal.strategy_id == "rsi10-pullback-reversion-v1"


def test_choose_live_signal_prefers_king_candle_quality_weekly() -> None:
    daily = LiveSignal(
        status="WAIT_FOR_TRIGGER", label="l", reason="r", strategy_id="unlinked-screener",
        strategy_label="l", strategy_status="Unlinked", setup_family="f", score=50,
        as_of="a", trigger_price=None, trigger_source=None,
    )  # fmt: skip
    weekly = LiveSignal(
        status="ENTRY_NOW", label="l", reason="r", strategy_id="king-candle-quality-v1",
        strategy_label="l", strategy_status="Candidate", setup_family="f", score=90,
        as_of="a", trigger_price=None, trigger_source=None,
    )  # fmt: skip

    assert choose_live_signal(daily, weekly) is weekly


def test_choose_live_signal_prefers_daily_when_actionable() -> None:
    daily = LiveSignal(
        status="ENTRY_NOW", label="l", reason="r", strategy_id="tuned-ma-breakout-v1",
        strategy_label="l", strategy_status="Candidate", setup_family="f", score=90,
        as_of="a", trigger_price=None, trigger_source=None,
    )  # fmt: skip
    weekly = LiveSignal(
        status="WATCH", label="l", reason="r", strategy_id="weekly-supertrend-10-3",
        strategy_label="l", strategy_status="Watch", setup_family="f", score=70,
        as_of="a", trigger_price=None, trigger_source=None,
    )  # fmt: skip

    assert choose_live_signal(daily, weekly) is daily


def test_choose_live_signal_falls_back_to_weekly_when_daily_inactive() -> None:
    daily = LiveSignal(
        status="NO_TRADE", label="l", reason="r", strategy_id="x", strategy_label="l",
        strategy_status="Rejected", setup_family="f", score=50, as_of="a",
        trigger_price=None, trigger_source=None,
    )  # fmt: skip
    weekly = LiveSignal(
        status="WATCH", label="l", reason="r", strategy_id="weekly-supertrend-10-3",
        strategy_label="l", strategy_status="Watch", setup_family="f", score=70,
        as_of="a", trigger_price=None, trigger_source=None,
    )  # fmt: skip

    assert choose_live_signal(daily, weekly) is weekly


def test_replace_latest_average() -> None:
    # Removing the oldest value and adding today's closes back to a plain mean
    # when window == 1: new average should just be new_value.
    assert replace_latest_average(avg=10.0, old_value=10.0, new_value=20.0, window=1.0) == 20.0


def test_parse_volume_handles_none_and_invalid() -> None:
    assert parse_volume(None) == 0.0
    assert parse_volume("not-a-number") == 0.0
    assert parse_volume("12345.5") == 12345.5


def test_signal_confidence_by_status_and_score() -> None:
    assert signal_confidence("ENTRY_NOW", 50) == "Enter Now"
    assert signal_confidence("WATCH", 50) == "Watch Only"
    assert signal_confidence("NO_TRADE", 50) == "No Trade"
    assert signal_confidence("INVALIDATED", 50) == "Invalidated"
    assert signal_confidence("WAIT_FOR_TRIGGER", 90) == "Wait For Trigger"
    assert signal_confidence("WAIT_FOR_TRIGGER", 50) == "Developing"


def test_live_signal_rank_ordering() -> None:
    assert live_signal_rank("ENTRY_NOW") < live_signal_rank("WATCH")
    assert live_signal_rank("WATCH") < live_signal_rank("WAIT_FOR_TRIGGER")
    assert live_signal_rank("WAIT_FOR_TRIGGER") < live_signal_rank("NO_TRADE")
    assert live_signal_rank("NO_TRADE") < live_signal_rank("INVALIDATED")
