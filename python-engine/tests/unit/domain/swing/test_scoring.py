from swing_atlas.domain.swing.models import (
    CandidateSeed,
    HistoricalScreenerFeatureRow,
    LiveSignal,
    MarketRegime,
)
from swing_atlas.domain.swing.scoring import (
    fallback_candidate_score,
    live_strategy_score,
    regime_quality,
    risk_reward_quality,
    signal_quality,
    technical_quality,
    trend_quality,
    volume_quality,
)


def _signal(
    status: str = "WATCH", strategy_status: str = "Watch", strategy_id: str = "x"
) -> LiveSignal:
    return LiveSignal(
        status=status,
        label="l",
        reason="r",
        strategy_id=strategy_id,
        strategy_label="l",
        strategy_status=strategy_status,
        setup_family="f",
        score=70,
        as_of="now",
        trigger_price=None,
        trigger_source=None,
    )


def test_live_strategy_score_base_case() -> None:
    # No bonuses apply -> stays at base 50.
    assert live_strategy_score(False, 10.0, 20.0, 0.5, False, 10.0) == 50


def test_live_strategy_score_all_bonuses_clamped_to_96() -> None:
    score = live_strategy_score(
        trend_up=True,
        breakout_pct=1.0,
        distance_to_52w_high_pct=5.0,
        volume_ratio=1.5,
        pullback_zone=True,
        range_position_pct=80.0,
    )
    # 50 + 18 + 14 + 10 + 10 + 8 + 6 = 116, clamped to 96.
    assert score == 96


def test_live_strategy_score_tiered_breakout_bonus() -> None:
    tight = live_strategy_score(False, 1.5, 50.0, 0.0, False, 0.0)
    loose = live_strategy_score(False, 3.0, 50.0, 0.0, False, 0.0)
    none = live_strategy_score(False, 10.0, 50.0, 0.0, False, 0.0)
    assert tight == 50 + 14
    assert loose == 50 + 8
    assert none == 50


def test_signal_quality_priority_invalidated_beats_everything() -> None:
    # INVALIDATED wins even if strategy_status would otherwise imply something else.
    assert signal_quality(_signal(status="INVALIDATED", strategy_status="Candidate")) == 15


def test_signal_quality_rejected_or_no_trade() -> None:
    assert signal_quality(_signal(status="WATCH", strategy_status="Rejected")) == 30
    assert signal_quality(_signal(status="NO_TRADE", strategy_status="Watch")) == 30


def test_signal_quality_entry_now_and_watch() -> None:
    assert signal_quality(_signal(status="ENTRY_NOW", strategy_status="Candidate")) == 92
    assert signal_quality(_signal(status="WATCH", strategy_status="Watch")) == 68


def test_signal_quality_unscored_fallback() -> None:
    assert (
        signal_quality(
            _signal(status="WAIT_FOR_TRIGGER", strategy_status="Unknown", strategy_id="unscored")
        )
        == 35
    )


def test_signal_quality_default() -> None:
    assert signal_quality(_signal(status="WAIT_FOR_TRIGGER", strategy_status="Watch")) == 76


def test_trend_quality_averages_daily_and_weekly() -> None:
    daily = _signal(status="ENTRY_NOW", strategy_status="Candidate")  # quality 92
    weekly = _signal(status="WATCH", strategy_status="Watch")  # quality 68
    assert trend_quality(daily, weekly) == (92 + 68) // 2


def test_trend_quality_no_weekly_uses_daily_only() -> None:
    daily = _signal(status="ENTRY_NOW", strategy_status="Candidate")
    assert trend_quality(daily, None) == 92


def test_trend_quality_unscored_daily_uses_weekly_only() -> None:
    daily = _signal(status="WATCH", strategy_status="Watch", strategy_id="unscored")
    weekly = _signal(status="ENTRY_NOW", strategy_status="Candidate")
    assert trend_quality(daily, weekly) == 92


def test_volume_quality_uses_ratio_when_baseline_available() -> None:
    seed = CandidateSeed(
        symbol="X", company_name="X", tiers=[], liquidity_bucket="MID",
        open_price=1, high_price=1, low_price=1, last_price=1, prev_close=1,
        day_volume=200_000.0, day_change_pct=0, open_gap_pct=0, recovery_pct=0,
        distance_to_high_pct=0, intraday_range_pct=0, source="s",
    )  # fmt: skip
    baseline = HistoricalScreenerFeatureRow(avg_volume20=100_000.0)
    assert volume_quality(seed, baseline) == 95  # ratio 2.0 >= 1.5


def test_volume_quality_falls_back_to_liquidity_bucket_without_baseline() -> None:
    seed = CandidateSeed(
        symbol="X", company_name="X", tiers=[], liquidity_bucket="MEGA",
        open_price=1, high_price=1, low_price=1, last_price=1, prev_close=1,
        day_volume=0.0, day_change_pct=0, open_gap_pct=0, recovery_pct=0,
        distance_to_high_pct=0, intraday_range_pct=0, source="s",
    )  # fmt: skip
    assert volume_quality(seed, None) == 70


def test_regime_quality() -> None:
    assert regime_quality(MarketRegime("l", "bullish", "s", 0, 0, 0.0)) == 85
    assert regime_quality(MarketRegime("l", "neutral", "s", 0, 0, 0.0)) == 65
    assert regime_quality(MarketRegime("l", "cautious", "s", 0, 0, 0.0)) == 40
    assert regime_quality(MarketRegime("l", "unknown", "s", 0, 0, 0.0)) == 50


def test_risk_reward_quality_tiers() -> None:
    assert risk_reward_quality(5.0) == 100
    assert risk_reward_quality(3.5) == 90
    assert risk_reward_quality(2.5) == 80
    assert risk_reward_quality(1.8) == 65
    assert risk_reward_quality(0.5) == 40


def test_technical_quality_invalidated_penalty_applies_after_clamp_inputs() -> None:
    seed = CandidateSeed(
        symbol="X", company_name="X", tiers=[], liquidity_bucket="MID",
        open_price=1, high_price=1, low_price=1, last_price=1, prev_close=1,
        day_volume=0.0, day_change_pct=3.0, open_gap_pct=0, recovery_pct=2.0,
        distance_to_high_pct=1.0, intraday_range_pct=0, source="s",
    )  # fmt: skip
    invalidated = _signal(status="INVALIDATED")
    watch = _signal(status="WATCH")
    # base 35 +25 (dist<=1.5) +15 (change>=2.0) +8 (recovery>=1.0) + min((90-50)//4,12)=10 = 93
    assert technical_quality(seed, watch, 90) == 93
    assert technical_quality(seed, invalidated, 90) == 93 - 30


def test_fallback_candidate_score_is_bounded() -> None:
    seed = CandidateSeed(
        symbol="X", company_name="X", tiers=["Tier1"], liquidity_bucket="MEGA",
        open_price=1, high_price=1, low_price=1, last_price=1, prev_close=1,
        day_volume=0.0, day_change_pct=10.0, open_gap_pct=0, recovery_pct=10.0,
        distance_to_high_pct=0.0, intraday_range_pct=0.0, source="s",
    )  # fmt: skip
    regime = MarketRegime("l", "bullish", "s", 0, 0, 0.0)
    score = fallback_candidate_score(seed, "Breakout Continuation", regime)
    assert 58 <= score <= 96
