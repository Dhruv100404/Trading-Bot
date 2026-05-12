from __future__ import annotations

from quant_research_pipeline import StrategySpec


def make_strategies() -> list[StrategySpec]:
    return [
        StrategySpec(
            name="example_python_strategy",
            family="Research template",
            entry_rule="Replace this with a precise human-readable rule.",
            stop_atr=1.4,
            target_atr=2.4,
            max_hold_days=8,
            signal_fn=lambda d: (
                d.liquid_research
                & (d.market_breadth200 > 0.40)
                & (d.close > d.sma200)
                & (d.relvol > 1.0)
            ),
            thesis="Copy this file, rename the strategy, and edit the Python signal_fn.",
            execution_caveat="Template only. Do not promote.",
        )
    ]
