from __future__ import annotations

import pandas as pd

from quant_research_pipeline import StrategySpec


def _rolling_symbol_quantile(d: pd.DataFrame, column: str, window: int, q: float) -> pd.Series:
    return d.groupby("symbol")[column].transform(
        lambda s: s.rolling(window, min_periods=max(30, window // 2)).quantile(q)
    )


def _atr_pct_median_120(d: pd.DataFrame) -> pd.Series:
    return _rolling_symbol_quantile(d, "atr_pct", 120, 0.50)


def _relvol_q60(d: pd.DataFrame, q: float) -> pd.Series:
    return _rolling_symbol_quantile(d, "relvol", 60, q)


def _breadth_ok(d: pd.DataFrame, floor: float = 0.35) -> pd.Series:
    return d["market_breadth200"].fillna(0) > floor


def _healthy_liquidity(d: pd.DataFrame) -> pd.Series:
    return d["liquid_research"] & (d["close"] >= 80) & (d["adv20"] >= 30_000_000)


def _adaptive_stretch_reclaim(d: pd.DataFrame) -> pd.Series:
    stretch = (d["ema20"] - d["close"]) / d["atr14"].replace(0, pd.NA)
    return (
        _healthy_liquidity(d)
        & _breadth_ok(d, 0.32)
        & d["volatility_regime"].ne("high")
        & (d["close"] > d["sma200"])
        & (d["rs120_rank"] > 0.45)
        & (stretch > 2.15)
        & (d["rsi14"] < 38)
        & (d["close_location"] > 0.45)
        & d["gap_pct"].between(-7.0, 2.0)
        & (d["atr_pct"] <= _atr_pct_median_120(d) * 1.35)
    )


def _failed_breakdown_plus_breadth(d: pd.DataFrame) -> pd.Series:
    return (
        _healthy_liquidity(d)
        & _breadth_ok(d, 0.34)
        & (d["close"] > d["sma200"])
        & (d["low"] < d["prior_low20"])
        & (d["close"] > d["prior_low20"])
        & (d["close_location"] >= 0.72)
        & (d["relvol"] > 0.75)
        & (d["relvol"] < _relvol_q60(d, 0.90).fillna(3.0))
        & (d["rs60_rank"] > 0.35)
        & d["gap_pct"].between(-6.0, 3.0)
    )


def _quiet_rs_pullback(d: pd.DataFrame) -> pd.Series:
    return (
        _healthy_liquidity(d)
        & _breadth_ok(d, 0.45)
        & d["trend_regime"].isin(["trending", "sideways"])
        & (d["rs60_rank"] > 0.75)
        & (d["rs120_rank"] > 0.70)
        & (d["close"] > d["sma200"])
        & (d["ema20"] > d["ema50"])
        & d["rsi14"].between(38, 58)
        & d["relvol"].between(0.45, 1.15)
        & (d["close"] <= d["ema20"] * 1.012)
        & (d["close"] >= d["ema20"] * 0.955)
        & d["gap_pct"].between(-3.5, 2.5)
    )


def _compression_leader_breakout(d: pd.DataFrame) -> pd.Series:
    return (
        _healthy_liquidity(d)
        & _breadth_ok(d, 0.42)
        & (d["rs60_rank"] > 0.78)
        & (d["close"] > d["sma200"])
        & (d["bb_width"] < d["bb_width_q20"] * 1.10)
        & (d["range_pct"] <= d["range7_min"] * 1.15)
        & (d["close"] > d["prior_high20"])
        & (d["close_location"] > 0.72)
        & (d["relvol"] > 1.05)
        & d["gap_pct"].between(-2.0, 3.0)
        & (d["atr_pct"] < 0.075)
    )


def _sma50_reclaim_continuation(d: pd.DataFrame) -> pd.Series:
    return (
        _healthy_liquidity(d)
        & _breadth_ok(d, 0.36)
        & d["was_below_sma50_20"]
        & d["reclaim_sma50"]
        & (d["close"] > d["sma200"])
        & (d["close"] > d["prior_high20"])
        & (d["rs60_rank"] > 0.45)
        & (d["close_location"] > 0.68)
        & d["relvol"].between(0.9, 2.5)
        & d["gap_pct"].between(-3.0, 4.0)
    )


def _market_panic_leader_bounce(d: pd.DataFrame) -> pd.Series:
    stretch = (d["ema20"] - d["close"]) / d["atr14"].replace(0, pd.NA)
    return (
        _healthy_liquidity(d)
        & d["market_breadth200"].between(0.22, 0.45)
        & (d["close"] > d["sma200"])
        & (d["rs120_rank"] > 0.65)
        & (stretch > 1.7)
        & (d["rsi14"] < 42)
        & (d["close_location"] > 0.55)
        & d["gap_pct"].between(-5.5, 1.5)
        & (d["atr_pct"] < 0.085)
    )


STRATEGIES = [
    StrategySpec(
        name="complex_adaptive_stretch_reclaim",
        family="Adaptive mean reversion",
        entry_rule=(
            "Liquid long-term uptrend stock stretches more than 2.15 ATR below EMA20, "
            "recovers off the low, avoids high volatility, and passes breadth/RS gates."
        ),
        stop_atr=1.25,
        target_atr=2.05,
        max_hold_days=6,
        signal_fn=_adaptive_stretch_reclaim,
        thesis="A tuned version of the strongest existing edge, with volatility and liquidity gates to reduce bad-year damage.",
        required_columns=(
            "liquid_research",
            "adv20",
            "market_breadth200",
            "volatility_regime",
            "sma200",
            "ema20",
            "atr14",
            "rs120_rank",
            "rsi14",
            "close_location",
            "gap_pct",
            "atr_pct",
        ),
        invalidation="Reject if out-of-sample PF drops below 1.0 or walk-forward losses cluster during 2025-style regimes.",
        execution_caveat="Daily-bar research only. Live promotion needs paper fills and slippage confirmation.",
    ),
    StrategySpec(
        name="complex_failed_breakdown_plus_breadth",
        family="Failed breakdown reversal",
        entry_rule=(
            "Liquid uptrend stock undercuts the prior 20-day low, closes back above it near the high, "
            "with constructive breadth and non-exhaustive volume."
        ),
        stop_atr=1.15,
        target_atr=2.25,
        max_hold_days=7,
        signal_fn=_failed_breakdown_plus_breadth,
        thesis="Trap failed breakdowns where sellers lose control, while avoiding panic gaps and volume exhaustion.",
        invalidation="Reject if stress-cost PF falls below 1.05 or winners concentrate in too few symbols.",
        execution_caveat="Needs next-day confirmation before any live use.",
    ),
    StrategySpec(
        name="complex_quiet_rs_pullback",
        family="Relative strength pullback",
        entry_rule=(
            "Relative-strength leader remains above SMA200/EMA50, quietly pulls into EMA20, "
            "and keeps volume calm instead of chasing a breakout candle."
        ),
        stop_atr=1.25,
        target_atr=2.35,
        max_hold_days=8,
        signal_fn=_quiet_rs_pullback,
        thesis="Buy leadership on controlled pullbacks, not obvious green candles.",
        invalidation="Reject if 2025/2026 OOS behavior is negative after costs.",
        execution_caveat="Paper-test only until the scanner proves it can avoid overnight gap risk.",
    ),
    StrategySpec(
        name="complex_compression_leader_breakout",
        family="Compression breakout",
        entry_rule=(
            "Liquid RS leader breaks a 20-day high from low Bollinger width and narrow range, "
            "with controlled ATR and normal gap."
        ),
        stop_atr=1.35,
        target_atr=2.70,
        max_hold_days=10,
        signal_fn=_compression_leader_breakout,
        thesis="Only take breakouts after volatility contraction and broad-market support.",
        invalidation="Reject if breakout edge disappears under stress slippage.",
        execution_caveat="Breakout fills can slip; paper fills matter more than backtest fills here.",
    ),
    StrategySpec(
        name="complex_sma50_reclaim_continuation",
        family="Trend reversal continuation",
        entry_rule=(
            "Liquid stock regains SMA50 after recent weakness, clears 20-day resistance, "
            "and closes strong with bounded volume and gap."
        ),
        stop_atr=1.35,
        target_atr=2.55,
        max_hold_days=9,
        signal_fn=_sma50_reclaim_continuation,
        thesis="Catch early trend repair rather than late 52-week-high continuation.",
        invalidation="Reject if false reclaims dominate in weak breadth months.",
        execution_caveat="Needs regime-aware position sizing if promoted.",
    ),
    StrategySpec(
        name="complex_market_panic_leader_bounce",
        family="Breadth-aware mean reversion",
        entry_rule=(
            "During weak but not broken breadth, buy liquid RS leaders above SMA200 that are stretched below EMA20 "
            "and close strongly off the lows."
        ),
        stop_atr=1.20,
        target_atr=2.00,
        max_hold_days=5,
        signal_fn=_market_panic_leader_bounce,
        thesis="Use market weakness as context for selective mean reversion instead of blindly shutting the system down.",
        invalidation="Reject if drawdown increases versus plain ATR stretch.",
        execution_caveat="This is the most regime-sensitive idea; keep small in any paper allocation.",
    ),
]
