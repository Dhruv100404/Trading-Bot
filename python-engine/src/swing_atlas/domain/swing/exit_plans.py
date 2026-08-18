"""Per-strategy stop/target buffers -- mirrors engine/src/api/swing.rs::strategy_exit_plan.

This is genuinely just data (a lookup table), so a dict is more honest here than
a function -- unlike the strategy-ID priority chain, there's no meaningful order.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExitPlan:
    target_buffer_pct: float
    stop_buffer_pct: float
    hold_label: str


EXIT_PLAN_TABLE: dict[str, ExitPlan] = {
    "king-candle-quality-v1": ExitPlan(40.0, 8.0, "20-30 weeks / weekly ST trail"),
    "weekly-supertrend-10-3": ExitPlan(40.0, 10.0, "20-30 weeks / weekly ST trail"),
    "swing-breakout-v1": ExitPlan(8.0, 4.0, "10 sessions"),
    "pullback-20dma-v1": ExitPlan(6.0, 3.0, "10 sessions"),
    "pullback-quality-v2": ExitPlan(7.0, 3.0, "12 sessions"),
    "rsi10-pullback-reversion-v1": ExitPlan(4.0, 4.0, "5 sessions"),
    "near-52w-high-v1": ExitPlan(10.0, 5.0, "15 sessions"),
    "near-52w-high-runner-v2": ExitPlan(12.0, 5.0, "20 sessions"),
    "near-52w-high-volume-v3": ExitPlan(10.0, 4.5, "15 sessions"),
    "momentum-core-v1": ExitPlan(15.0, 6.0, "25 sessions"),
}


def strategy_exit_plan(strategy_id: str) -> ExitPlan | None:
    return EXIT_PLAN_TABLE.get(strategy_id)
