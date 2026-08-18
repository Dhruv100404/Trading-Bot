"""Tests for compute_market_regime/build_live_candidate/compute_setup_mix --
mirrors engine/src/api/swing.rs's corresponding functions.
"""

from swing_atlas.domain.swing.candidate_builder import (
    build_live_candidate,
    compute_market_regime,
    compute_setup_mix,
)
from swing_atlas.domain.swing.models import CandidateSeed, MarketRegime, SwingCandidate


def _seed(**overrides: object) -> CandidateSeed:
    defaults: dict[str, object] = dict(
        symbol="DEMO", company_name="Demo Ltd", tiers=[], liquidity_bucket="LARGE",
        open_price=100.0, high_price=102.0, low_price=99.0, last_price=101.0, prev_close=99.0,
        day_volume=500_000.0, day_change_pct=2.0, open_gap_pct=0.5, recovery_pct=1.0,
        distance_to_high_pct=1.0, intraday_range_pct=2.0, source="dhan-live",
    )  # fmt: skip
    defaults.update(overrides)
    return CandidateSeed(**defaults)  # type: ignore[arg-type]


def test_compute_market_regime_bullish_breadth() -> None:
    seeds = [_seed(symbol=f"A{i}", day_change_pct=1.0) for i in range(7)] + [
        _seed(symbol=f"B{i}", day_change_pct=-1.0) for i in range(2)
    ]

    regime = compute_market_regime(seeds, live_quotes=True)

    assert regime.tone == "bullish"
    assert regime.advances == 7
    assert regime.declines == 2
    assert "advancing names" in regime.summary


def test_compute_market_regime_no_live_quotes_uses_fallback_summary() -> None:
    regime = compute_market_regime([], live_quotes=False)

    assert "fallback mode" in regime.summary


def test_compute_market_regime_no_declines_uses_advances_as_ratio() -> None:
    seeds = [_seed(symbol=f"A{i}", day_change_pct=1.0) for i in range(3)]

    regime = compute_market_regime(seeds, live_quotes=True)

    assert regime.breadth_ratio == 3.0
    assert regime.tone == "bullish"


def test_build_live_candidate_no_baseline_falls_back_to_classify_setup_family() -> None:
    seed = _seed(day_change_pct=2.5, distance_to_high_pct=0.5)
    regime = MarketRegime(
        label="Risk-On Breadth", tone="bullish", summary="s", advances=10, declines=2,
        breadth_ratio=5.0,
    )  # fmt: skip

    candidate = build_live_candidate(seed, regime, None, {}, None, entry_window_open=True)

    assert isinstance(candidate, SwingCandidate)
    assert candidate.setup_family == "Breakout Continuation"  # classify_setup_family fallback
    assert candidate.live_signal.strategy_id == "unscored"
    assert candidate.symbol == "DEMO"
    assert candidate.stop_loss < candidate.last_price < candidate.target_price


def test_compute_setup_mix_groups_and_sorts_by_count_then_score() -> None:
    def candidate(setup_family: str, score: int) -> SwingCandidate:
        regime = MarketRegime(
            label="l", tone="neutral", summary="s", advances=0, declines=0, breadth_ratio=1.0
        )
        base = build_live_candidate(
            _seed(symbol=setup_family + str(score)), regime, None, {}, None, entry_window_open=True
        )
        base.setup_family = setup_family
        base.score = score
        return base

    candidates = [candidate("A", 80), candidate("A", 90), candidate("B", 70)]

    mix = compute_setup_mix(candidates)

    assert mix[0].family == "A"
    assert mix[0].count == 2
    assert mix[0].avg_score == 85.0
    assert mix[1].family == "B"
    assert mix[1].count == 1
