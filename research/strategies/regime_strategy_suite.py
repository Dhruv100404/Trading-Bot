from __future__ import annotations

import pandas as pd

from quant_research_pipeline import StrategySpec


def _atr_expanding(d: pd.DataFrame) -> pd.Series:
    atr_pct_median = d.groupby("symbol")["atr_pct"].transform(
        lambda s: s.rolling(60, min_periods=30).median()
    )
    return d["atr_pct"] > atr_pct_median


def _multifactor_score(d: pd.DataFrame) -> pd.Series:
    momentum_score = (
        (d["ret20"] > 0).astype(int)
        + (d["rs60_rank"] > 0.65).astype(int)
        + (d["rs120_rank"] > 0.65).astype(int)
    )
    trend_score = (
        (d["close"] > d["ema50"]).astype(int)
        + (d["ema50"] > d["ema200"]).astype(int)
        + (d["close"] > d["sma200"]).astype(int)
    )
    volume_score = ((d["relvol"] > 1.0).astype(int) + (d["relvol"] > 1.4).astype(int))
    setup_score = (
        d["rsi14"].between(45, 70).astype(int)
        + d["zscore_20"].between(-0.8, 1.8).astype(int)
        + (d["close_location"] > 0.55).astype(int)
    )
    risk_score = (
        (d["volatility_regime"].eq("high")).astype(int)
        + (d["atr_pct"] > 0.08).astype(int)
        + (d["gap_pct"].abs() > 4).astype(int)
    )
    return momentum_score + trend_score + volume_score + setup_score - risk_score


def _regime_trend_signal(d: pd.DataFrame) -> pd.Series:
    return (
        d["liquid_research"]
        & d["trend_regime"].eq("trending")
        & d["volatility_regime"].ne("high")
        & (d["market_breadth200"] > 0.45)
        & (d["close"] > d["prior_high20"])
        & (d["relvol"] > 1.4)
        & (d["rs60_rank"] > 0.90)
        & (d["close_location"] > 0.85)
        & d["gap_pct"].between(-1.5, 3.0)
    )


def _mean_reversion_signal(d: pd.DataFrame) -> pd.Series:
    return (
        d["liquid_research"]
        & d["trend_regime"].ne("trending")
        & (d["adx14"] < 30)
        & (d["market_breadth200"] > 0.30)
        & (d["close"] > d["sma200"])
        & (d["zscore_20"] < -2.5)
        & (d["rsi14"] < 30)
        & (d["dist_ema20_atr"] < -1.5)
        & (d["close_location"] > 0.35)
        & (d["gap_pct"] > -8)
    )


def _breakout_volume_signal(d: pd.DataFrame) -> pd.Series:
    return (
        d["liquid_research"]
        & d["trend_regime"].eq("trending")
        & (d["market_breadth200"] > 0.45)
        & (d["close"] > d["prior_high55"])
        & (d["close"] > d["ema200"])
        & (d["ema50"] > d["ema200"])
        & (d["relvol"] > 2.6)
        & (d["rs60_rank"] > 0.60)
        & _atr_expanding(d)
        & (d["close_location"] > 0.85)
        & d["gap_pct"].between(-1.5, 3.5)
    )


def _multifactor_signal(d: pd.DataFrame) -> pd.Series:
    score = _multifactor_score(d)
    return (
        d["liquid_research"]
        & (score >= 9)
        & (d["market_breadth200"] > 0.45)
        & d["volatility_regime"].ne("high")
        & (d["close"] > d["prior_high20"])
        & (d["relvol"] > 1.2)
        & (d["rs60_rank"] > 0.75)
        & (d["close_location"] > 0.85)
        & d["gap_pct"].between(-2, 3)
    )


STRATEGIES = [
    StrategySpec(
        name="strategy_1_regime_trend",
        family="Regime trend following",
        entry_rule=(
            "Liquid RS leader in trending regime breaks a 20-day high with above-average volume, "
            "constructive breadth, strong close location, and no high-volatility flag."
        ),
        stop_atr=1.5,
        target_atr=3.0,
        max_hold_days=12,
        signal_fn=_regime_trend_signal,
        thesis="Use the trend playbook only when ADX, EMA structure, breadth, and volatility regime agree.",
        required_columns=(
            "liquid_research",
            "trend_regime",
            "volatility_regime",
            "market_breadth200",
            "prior_high20",
            "relvol",
            "rs60_rank",
            "close_location",
            "gap_pct",
        ),
        invalidation="Reject if walk-forward tests show the edge only survives in one regime or one calendar segment.",
        execution_caveat="Research-only long setup. Current Python backtester does not model position-size reduction in high volatility.",
    ),
    StrategySpec(
        name="strategy_2_mean_reversion",
        family="Volatility-filtered mean reversion",
        entry_rule=(
            "Liquid long-term uptrend stock in non-trending, ADX-below-30 regime closes below -2.5 z-score, "
            "more than 1.5 ATR under EMA20, with RSI14 below 30 and a non-disastrous gap."
        ),
        stop_atr=1.3,
        target_atr=2.1,
        max_hold_days=6,
        signal_fn=_mean_reversion_signal,
        thesis="Test pull-to-mean behavior only when ADX/regime says the market is not strongly trending.",
        required_columns=(
            "liquid_research",
            "trend_regime",
            "adx14",
            "market_breadth200",
            "sma200",
            "zscore_20",
            "rsi14",
            "dist_ema20_atr",
            "close_location",
            "gap_pct",
        ),
        invalidation="Reject if losses cluster during trend-regime transitions or cost stress turns expectancy negative.",
        execution_caveat="Long-only expression of the idea; short-side mean reversion needs a separate short-capable engine.",
    ),
    StrategySpec(
        name="strategy_3_breakout_volume",
        family="Breakout with volume confirmation",
        entry_rule=(
            "Liquid trending stock closes above a prior 55-day high with relative volume above 2.6, "
            "ATR expansion, RS leadership, and a very strong close location."
        ),
        stop_atr=1.8,
        target_atr=3.6,
        max_hold_days=15,
        signal_fn=_breakout_volume_signal,
        thesis="Reduce classic false breakouts by requiring trend regime, volume confirmation, and ATR expansion.",
        required_columns=(
            "liquid_research",
            "trend_regime",
            "market_breadth200",
            "prior_high55",
            "ema50",
            "ema200",
            "relvol",
            "rs60_rank",
            "atr_pct",
            "close_location",
            "gap_pct",
        ),
        invalidation="Reject if winners are concentrated in very few symbols or the stress slippage scenario erases PF.",
        execution_caveat="Stops are daily OHLC approximations; intraday stop/target sequencing remains conservative.",
    ),
    StrategySpec(
        name="strategy_4_multifactor_score",
        family="Multi-factor scoring",
        entry_rule=(
            "Liquid stock clears a 20-day high when weighted momentum, trend, volume, RSI/z-score, "
            "and close-location score is at least 9 after risk penalties."
        ),
        stop_atr=1.4,
        target_atr=2.8,
        max_hold_days=10,
        signal_fn=_multifactor_signal,
        thesis="Combine several weak but explainable factors instead of relying on a single indicator trigger.",
        required_columns=(
            "liquid_research",
            "ret20",
            "rs60_rank",
            "rs120_rank",
            "ema50",
            "ema200",
            "sma200",
            "relvol",
            "rsi14",
            "zscore_20",
            "close_location",
            "volatility_regime",
            "atr_pct",
            "gap_pct",
            "prior_high20",
        ),
        invalidation="Reject if parameter sensitivity shows the score threshold is brittle.",
        execution_caveat="The score is intentionally transparent; ML should be layered later as a filter, not a replacement.",
    ),
]
