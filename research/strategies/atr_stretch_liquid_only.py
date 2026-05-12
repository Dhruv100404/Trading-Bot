from __future__ import annotations

from quant_research_pipeline import StrategySpec


STRATEGIES = [
    StrategySpec(
        name="atr_stretch_liquid_only_python",
        family="Mean reversion",
        entry_rule=(
            "Liquid stock above SMA200 closes more than 2.5 ATR below EMA20, "
            "RSI14 below 35, constructive market breadth, and no extreme gap."
        ),
        stop_atr=1.4,
        target_atr=2.0,
        max_hold_days=7,
        signal_fn=lambda d: (
            d.liquid_research
            & (d.market_breadth200 > 0.35)
            & (d.close > d.sma200)
            & ((d.ema20 - d.close) > 2.5 * d.atr14)
            & (d.rsi14 < 35)
            & (d.close_location > 0.35)
            & (d.gap_pct > -8)
        ),
        thesis=(
            "This keeps the strongest existing research family in Python so exits, "
            "filters, and follow-up experiments are not constrained by app JSON."
        ),
        required_columns=(
            "liquid_research",
            "market_breadth200",
            "close",
            "sma200",
            "ema20",
            "atr14",
            "rsi14",
            "close_location",
            "gap_pct",
        ),
        invalidation="Reject if OOS PF falls below 1.0 or 2026-style weakness persists in forward paper trades.",
        execution_caveat="Research only. Promote to app/live scanner only after paper trading confirms fills and slippage.",
    )
]
