"""Backtest strategy specs and the SQL fragments they render into.

Mirrors engine/src/api/backtest.rs's BacktestStrategySpec + built_in_variant_specs +
strategy_entry_select/build_entries_cte. These functions build SQL TEXT (not execute
it) -- kept byte-for-byte identical to the Rust originals per the migration plan's
design decision to keep the backtest math in ClickHouse SQL, not re-derive it in
Python, so results can't silently drift from the validated baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BACKTEST_CAPITAL_PER_TRADE = 10_000.0
BACKTEST_MAX_NEW_POSITIONS_PER_DAY = 3
BACKTEST_CASH_ACCOUNT_CAPITAL = 30_000.0
CASH_PORTFOLIO_STRATEGY_ID = "cash-portfolio-all"
# A one-off result is research, not validation. This prevents sparse rules
# from appearing as Candidate merely because their only trade was positive.
MIN_BACKTEST_TRADES_FOR_VALIDATION = 30

DEPRECATED_BACKTEST_STRATEGY_IDS = (
    "near-52w-high-tight-v2",
    "breakout-volume-v2",
    "tuned-ma-breakout-v1",
    # Rejected by the latest completed database run. Keep them out of future
    # runs and purge their persisted trade rows during this cleanup.
    "near-52w-high-runner-v2",
    "pullback-quality-v2",
    "regime-trend-breakout-v1",
    "regime-breakout-volume-v1",
    "regime-multifactor-score-v1",
)


def escape_sql(value: str) -> str:
    return value.replace("'", "''")


def is_deprecated_backtest_strategy(strategy_id: str) -> bool:
    return strategy_id in DEPRECATED_BACKTEST_STRATEGY_IDS


def deprecated_strategy_sql_clause(alias: str) -> str:
    ids = ", ".join(f"'{escape_sql(sid)}'" for sid in DEPRECATED_BACKTEST_STRATEGY_IDS)
    return f" AND {alias}.strategy_id NOT IN ({ids})"


_METHOD_FAMILY_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("regime-mean", "Regime Mean Reversion"),
    ("regime-trend", "Regime Trend"),
    ("regime-breakout", "Regime Breakout"),
    ("regime-multifactor", "Multi-Factor"),
    ("supertrend", "Weekly Supertrend"),
    ("king-candle", "King Candle"),
    ("reversal", "Reversal"),
    ("breakout", "Breakout"),
    ("pullback", "Pullback"),
    ("stretch", "Mean Reversion"),
    ("rsi10", "Mean Reversion"),
    ("52w", "52W Momentum"),
    ("momentum", "Momentum"),
)


def strategy_method_family(strategy_id: str) -> str:
    lowered = strategy_id.lower()
    for needle, family in _METHOD_FAMILY_SUBSTRINGS:
        if needle in lowered:
            return family
    return "Other"


@dataclass(frozen=True, slots=True)
class BacktestStrategySpec:
    strategy_id: str
    strategy_name: str
    setup_family: str
    min_score: int
    tp_pct: float
    sl_pct: float
    target_atr: float | None
    stop_atr: float | None
    max_hold_sessions: int
    max_positions_per_day: int
    capital_per_trade: float
    entry_condition_sql: str | None


def built_in_variant_specs() -> list[BacktestStrategySpec]:
    return [
        BacktestStrategySpec(
            strategy_id="regime-mean-reversion-v1",
            strategy_name="Regime Mean Reversion V1",
            setup_family="Regime Mean Reversion",
            min_score=50,
            tp_pct=5.0,
            sl_pct=4.0,
            target_atr=2.1,
            stop_atr=1.3,
            max_hold_sessions=6,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="regime-trend-breakout-v1",
            strategy_name="Regime Trend Breakout V1",
            setup_family="Regime Trend Breakout",
            min_score=50,
            tp_pct=8.0,
            sl_pct=4.0,
            target_atr=3.0,
            stop_atr=1.5,
            max_hold_sessions=12,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="regime-breakout-volume-v1",
            strategy_name="Regime Breakout Volume V1",
            setup_family="Regime Breakout Volume",
            min_score=50,
            tp_pct=10.0,
            sl_pct=5.0,
            target_atr=3.6,
            stop_atr=1.8,
            max_hold_sessions=15,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="regime-multifactor-score-v1",
            strategy_name="Regime Multi-Factor Score V1",
            setup_family="Regime Multi-Factor Score",
            min_score=50,
            tp_pct=8.0,
            sl_pct=4.0,
            target_atr=2.8,
            stop_atr=1.4,
            max_hold_sessions=10,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="pullback-quality-v2",
            strategy_name="Pullback Quality V2",
            setup_family="Pullback To 20 DMA",
            min_score=88,
            tp_pct=7.0,
            sl_pct=3.0,
            target_atr=None,
            stop_atr=None,
            max_hold_sessions=12,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="near-52w-high-runner-v2",
            strategy_name="Near 52W High Runner V2",
            setup_family="Near 52W High",
            min_score=90,
            tp_pct=12.0,
            sl_pct=5.0,
            target_atr=None,
            stop_atr=None,
            max_hold_sessions=20,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="near-52w-high-volume-v3",
            strategy_name="Near 52W High Volume V3",
            setup_family="Near 52W High",
            min_score=88,
            tp_pct=10.0,
            sl_pct=4.5,
            target_atr=None,
            stop_atr=None,
            max_hold_sessions=15,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
        BacktestStrategySpec(
            strategy_id="momentum-core-v1",
            strategy_name="Momentum Core V1",
            setup_family="Near 52W High",
            min_score=92,
            tp_pct=15.0,
            sl_pct=6.0,
            target_atr=None,
            stop_atr=None,
            max_hold_sessions=25,
            max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql=None,
        ),
    ]


def load_backtest_strategy_specs() -> list[BacktestStrategySpec]:
    # Backtest strategy behavior is code-owned. Do not load strategy behavior from
    # external JSON files here; promote validated Python research into explicit
    # engine specs or a dedicated Python backtest service.
    specs = [
        s for s in built_in_variant_specs() if not is_deprecated_backtest_strategy(s.strategy_id)
    ]
    if not specs:
        specs.append(
            BacktestStrategySpec(
                strategy_id="near-52w-high-v1",
                strategy_name="Near 52W High V1",
                setup_family="Near 52W High",
                min_score=80,
                tp_pct=10.0,
                sl_pct=5.0,
                target_atr=None,
                stop_atr=None,
                max_hold_sessions=15,
                max_positions_per_day=BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
                capital_per_trade=BACKTEST_CAPITAL_PER_TRADE,
                entry_condition_sql=None,
            )
        )
    return specs


_DEFAULT_CONDITIONS: dict[str, str] = {
    "pullback-quality-v2": (
        "sig.trend_up = 1 AND sig.pullback_zone = 1 AND sig.volume_ratio >= 0.8 "
        "AND sig.day_close >= sig.sma20"
    ),
    "near-52w-high-runner-v2": (
        "sig.distance_to_52w_high_pct <= 3.0 AND sig.trend_up = 1 AND sig.volume_ratio >= 0.8"
    ),
    "near-52w-high-volume-v3": (
        "sig.distance_to_52w_high_pct <= 6.0 AND sig.volume_ratio >= 1.15 "
        "AND sig.range_position_pct >= 75.0"
    ),
    "momentum-core-v1": (
        "sig.distance_to_52w_high_pct <= 3.0 AND sig.range_position_pct >= 85.0 "
        "AND sig.trend_up = 1"
    ),
    "rsi10-pullback-reversion-v1": "sig.day_close > sig.sma200 AND sig.rsi10 < 30",
    "atr-stretch-liquid-only-v1": "sig.atr_stretch_liquid_only = 1",
    "regime-mean-reversion-v1": "sig.regime_mean_reversion = 1",
    "regime-trend-breakout-v1": "sig.regime_trend_breakout = 1",
    "regime-breakout-volume-v1": "sig.regime_breakout_volume = 1",
    "regime-multifactor-score-v1": (
        "sig.regime_multifactor_score >= 9 AND sig.regime_breakout_core = 1"
    ),
    "compression-breakout-v1": "sig.compression_breakout = 1",
    "strong-stock-pullback-v1": "sig.strong_stock_pullback = 1",
    "trend-reversal-failed-breakdown-v1": "sig.trend_reversal_breakout = 1",
}


def default_strategy_condition(spec: BacktestStrategySpec) -> str:
    condition = _DEFAULT_CONDITIONS.get(spec.strategy_id)
    if condition is not None:
        return condition
    return f"sig.setup_family = '{escape_sql(spec.setup_family)}'"


def dynamic_exit_pct(atr_multiple: float | None, fallback_pct: float) -> str:
    if atr_multiple is not None and math.isfinite(atr_multiple) and atr_multiple > 0:
        return (
            f"greatest(0.1, 100.0 * ({atr_multiple} * sig.atr14) / "
            "nullIf(toFloat64(e.day_open), 0))"
        )
    return str(fallback_pct)


def strategy_entry_select(spec: BacktestStrategySpec) -> str:
    condition = spec.entry_condition_sql or default_strategy_condition(spec)
    tp_expr = dynamic_exit_pct(spec.target_atr, spec.tp_pct)
    sl_expr = dynamic_exit_pct(spec.stop_atr, spec.sl_pct)

    return (
        f"SELECT '{escape_sql(spec.strategy_id)}' AS strategy_id, {tp_expr} AS tp_pct, "
        f"{sl_expr} AS sl_pct, {spec.max_hold_sessions} AS max_hold_sessions, "
        f"{spec.max_positions_per_day} AS max_positions_per_day, "
        f"{spec.capital_per_trade} AS capital_per_trade, sig.symbol AS entry_symbol, "
        f"sig.signal_date, '{escape_sql(spec.setup_family)}' AS entry_setup_family, "
        f"sig.score AS entry_score, sig.volume_ratio AS rank_volume_ratio, "
        f"e.trade_date AS entry_date, e.rn AS entry_rn, toFloat64(e.day_open) AS entry_price, "
        f"toUInt32(greatest(1, floor({spec.capital_per_trade} / "
        "nullIf(toFloat64(e.day_open), 0)))) AS quantity "
        f"FROM signals sig "
        f"INNER JOIN features e ON e.symbol = sig.symbol AND e.rn = sig.signal_rn + 1 "
        f"WHERE e.day_open > 0 AND sig.score >= {spec.min_score} AND ({condition})"
    )


def build_entries_cte(specs: list[BacktestStrategySpec]) -> str:
    return " UNION ALL ".join(strategy_entry_select(spec) for spec in specs)
