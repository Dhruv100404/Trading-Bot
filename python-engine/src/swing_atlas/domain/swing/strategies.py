"""The strategy-ID priority chain -- mirrors engine/src/api/swing.rs::strategy_match_for_screener.

Ported as an ORDERED LIST rather than an if/elif chain on purpose: the Rust
original's ordering IS the specification (first match wins), and a plain list
makes that fact visible and hard to silently break by reordering entries during
a future edit. DO NOT reorder SCREENER_STRATEGY_CHAIN without checking backtest
impact -- each entry was validated against historical data in that exact position.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenerContext:
    """Inputs the priority chain's predicates are evaluated against."""

    setup_family: str
    score: int
    trend_up: bool
    pullback_zone: bool
    breakout_pct: float
    distance_to_52w_high_pct: float
    range_position_pct: float
    volume_ratio: float
    day_close: float
    sma20: float
    sma200: float
    rsi10: float
    tuned_panic_reversal: bool


@dataclass(frozen=True, slots=True)
class ScreenerStrategySpec:
    strategy_id: str
    strategy_label: str
    matches: Callable[[ScreenerContext], bool]


SCREENER_STRATEGY_CHAIN: tuple[ScreenerStrategySpec, ...] = (
    ScreenerStrategySpec(
        "tuned-panic-reversal-v1", "Panic Reversal Lab", lambda c: c.tuned_panic_reversal
    ),
    ScreenerStrategySpec(
        "rsi10-pullback-reversion-v1",
        "RSI10 Pullback",
        lambda c: c.day_close > c.sma200 and c.rsi10 < 30.0,
    ),
    ScreenerStrategySpec(
        "failed-breakdown-reclaim-v1",
        "Failed Breakdown Reclaim",
        lambda c: c.setup_family == "Failed Breakdown Reclaim" and c.score >= 86,
    ),
    ScreenerStrategySpec(
        "compression-breakout-v1",
        "Compression Breakout",
        lambda c: c.setup_family == "Compression Breakout" and c.score >= 88,
    ),
    ScreenerStrategySpec(
        "breakout-continuation-v1",
        "Breakout Continuation",
        lambda c: (
            c.setup_family == "Breakout Continuation"
            and c.score >= 88
            and c.volume_ratio >= 1.1
            and c.trend_up
        ),
    ),
    ScreenerStrategySpec(
        "rs-leader-continuation-v1",
        "RS Leader Continuation",
        lambda c: c.setup_family == "Relative Strength Leader" and c.score >= 86,
    ),
    ScreenerStrategySpec(
        "pullback-quality-v2",
        "Pullback Quality",
        lambda c: (
            c.setup_family == "Pullback To 20 DMA"
            and c.score >= 88
            and c.trend_up
            and c.pullback_zone
            and c.volume_ratio >= 0.8
            and c.day_close >= c.sma20
        ),
    ),
    ScreenerStrategySpec(
        "pullback-20dma-v1", "Pullback 20DMA", lambda c: c.setup_family == "Pullback To 20 DMA"
    ),
    ScreenerStrategySpec(
        "momentum-core-v1",
        "Momentum Core",
        lambda c: (
            c.distance_to_52w_high_pct <= 3.0
            and c.range_position_pct >= 85.0
            and c.trend_up
            and c.score >= 92
        ),
    ),
    ScreenerStrategySpec(
        "near-52w-high-runner-v2",
        "52W Runner",
        lambda c: (
            c.distance_to_52w_high_pct <= 3.0
            and c.trend_up
            and c.volume_ratio >= 0.8
            and c.score >= 90
        ),
    ),
    ScreenerStrategySpec(
        "near-52w-high-volume-v3",
        "52W Volume",
        lambda c: (
            c.distance_to_52w_high_pct <= 6.0
            and c.volume_ratio >= 1.15
            and c.range_position_pct >= 75.0
            and c.score >= 88
        ),
    ),
    ScreenerStrategySpec(
        "near-52w-high-v1",
        "Near 52W High",
        lambda c: c.setup_family == "Near 52W High" and c.score >= 80,
    ),
    ScreenerStrategySpec(
        "swing-breakout-v1",
        "Swing Breakout",
        lambda c: c.setup_family in ("Breakout Setup", "Breakout Continuation"),
    ),
)

_UNLINKED = ScreenerStrategySpec("unlinked-screener", "Unlinked Screen", lambda _c: True)


def strategy_match_for_screener(ctx: ScreenerContext) -> tuple[str, str]:
    """Returns (strategy_id, strategy_label) for the first chain entry that matches."""
    for spec in SCREENER_STRATEGY_CHAIN:
        if spec.matches(ctx):
            return spec.strategy_id, spec.strategy_label
    return _UNLINKED.strategy_id, _UNLINKED.strategy_label


_DEFAULT_STATUSES: dict[str, str] = {
    "king-candle-quality-v1": "Candidate",
    "weekly-supertrend-10-3": "Watch",
    "tuned-panic-reversal-v1": "Watch",
    "momentum-core-v1": "Candidate",
    "rsi10-pullback-reversion-v1": "Candidate",
    "failed-breakdown-reclaim-v1": "Candidate",
    "compression-breakout-v1": "Watch",
    "breakout-continuation-v1": "Watch",
    "rs-leader-continuation-v1": "Watch",
    "near-52w-high-runner-v2": "Watch",
    "near-52w-high-v1": "Fragile",
    "near-52w-high-volume-v3": "Fragile",
    "pullback-20dma-v1": "Rejected",
    "pullback-quality-v2": "Rejected",
    "swing-breakout-v1": "Rejected",
}


def default_strategy_status(strategy_id: str) -> str:
    return _DEFAULT_STATUSES.get(strategy_id, "Unlinked")


def strategy_status_rank(status: str) -> int:
    return {"Candidate": 0, "Watch": 1, "Fragile": 2, "Rejected": 3}.get(status, 4)
