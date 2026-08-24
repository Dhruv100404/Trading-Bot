"""Parameter sweep: does adding an ATR stop-loss + take-profit target improve
on leader_rv8_fixed30's plain 30-day-hold, no-risk-exit design?

leader_rv8_fixed30 (and 5 of the other 6 watchlist variants) currently exit
purely on a hold-days timer -- exit_for_candidate only checks the ATR stop
when exit_mode is "atr"/"atr_ema20", which "fixed" never is. This sweep
reuses the same entry signal (leader_uptrend context, 8x fresh relvol, green
close in the top 30% of range) and only varies the exit: a grid of
(stop_atr, target_atr) pairs against the exit_mode="target_stop" path added
to exit_for_candidate, still capped at the same 30-day hold ceiling.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from swing_volume_spike_review import (
    DEFAULT_DAILY_CACHE,
    DEFAULT_OUT_DIR,
    DEFAULT_PARQUET_DIR,
    DEFAULT_VOLUME_GROUPS,
    StrategySpec,
    add_features,
    append_recent_parquet_daily,
    build_symbol_tables,
    daily_returns_from_trades,
    load_daily_cache,
    load_volume_groups,
    make_candidate_table,
    metrics_for_strategy,
    select_portfolio_trades,
)

BASE_KWARGS = dict(context="leader_uptrend", relvol50_min=8.0, ret1_min=0.0, close_location_min=0.70, hold_days=30)

GRID = [
    (None, None),   # baseline: identical to leader_rv8_fixed30, no risk exit at all
    (1.5, None),    # stop only
    (2.0, None),
    (2.5, None),
    (1.5, 3.0),     # stop + target combos
    (1.5, 4.0),
    (2.0, 3.0),
    (2.0, 4.0),
    (2.0, 5.0),
    (2.5, 4.0),
    (2.5, 5.0),
    (2.5, 6.0),
    (3.0, 6.0),
]


def main() -> None:
    print("Loading daily cache")
    raw_daily = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw_daily = append_recent_parquet_daily(raw_daily, DEFAULT_PARQUET_DIR)
    symbol_to_group = load_volume_groups(DEFAULT_VOLUME_GROUPS)
    print("Adding swing features")
    daily = add_features(raw_daily, symbol_to_group)
    symbol_tables = build_symbol_tables(daily)
    trading_dates = daily["trade_date"].drop_duplicates().sort_values()

    rows: list[dict[str, object]] = []
    for stop_atr, target_atr in GRID:
        name = f"stop{stop_atr}_target{target_atr}"
        exit_mode = "target_stop" if (stop_atr is not None or target_atr is not None) else "fixed"
        spec = StrategySpec(
            name=name,
            label=name,
            exit_mode=exit_mode,
            stop_atr=stop_atr if stop_atr is not None else 2.5,
            target_atr=target_atr,
            **BASE_KWARGS,
        )
        print(f"Backtesting {name}")
        candidates = make_candidate_table(daily, symbol_tables, spec)
        trades = select_portfolio_trades(candidates, spec)
        strategy_daily = daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
        metrics = metrics_for_strategy(trades, strategy_daily, name)
        metrics["stop_atr"] = stop_atr
        metrics["target_atr"] = target_atr
        if not trades.empty:
            reason_counts = trades["exit_reason"].value_counts(normalize=True).round(3).to_dict()
            metrics["exit_reasons"] = reason_counts
        rows.append(metrics)

    out = pd.DataFrame(rows)
    cols = [
        "stop_atr", "target_atr", "trades", "win_rate_pct", "profit_factor", "expectancy_pct",
        "avg_win_pct", "avg_loss_pct", "portfolio_return_pct", "daily_sharpe", "p_value",
        "max_drawdown_pct", "avg_hold_days", "exit_reasons",
    ]
    out = out[[c for c in cols if c in out.columns]]
    out_path = DEFAULT_OUT_DIR / "target_stop_sweep.csv"
    out.to_csv(out_path, index=False)
    print(out.to_string(index=False))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
