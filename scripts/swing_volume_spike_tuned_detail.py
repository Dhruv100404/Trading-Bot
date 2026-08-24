"""Full monthly + daily-equity detail for the three tuned (2.0 ATR stop)
variants identified in the stop-tuning sweep, run alongside their untouched
baselines so before/after can be compared month by month.
"""

from __future__ import annotations

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
    period_analysis,
    select_portfolio_trades,
)

SPECS = [
    StrategySpec("near52w_rv8_fixed30", "near52w baseline", "near_52w_high", 8.0, 0.00, 0.70, 30),
    StrategySpec("near52w_stop2", "near52w + 2.0 ATR stop", "near_52w_high", 8.0, 0.00, 0.70, 30,
                 exit_mode="target_stop", stop_atr=2.0, target_atr=None),
    StrategySpec("leader_fixed_baseline", "leader fixed30 baseline", "leader_uptrend", 8.0, 0.00, 0.70, 30),
    StrategySpec("leader_fixed_stop2", "leader fixed30 + 2.0 ATR stop", "leader_uptrend", 8.0, 0.00, 0.70, 30,
                 exit_mode="target_stop", stop_atr=2.0, target_atr=None),
    StrategySpec("leader_ema20_baseline", "leader ema20 baseline", "leader_uptrend", 8.0, 0.00, 0.70, 30,
                 exit_mode="ema20", min_hold_days=5),
    StrategySpec("leader_ema20_stop2", "leader ema20 + 2.0 ATR stop", "leader_uptrend", 8.0, 0.00, 0.70, 30,
                 exit_mode="atr_ema20", min_hold_days=5, stop_atr=2.0),
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

    monthly_frames = []
    equity_frames = []
    metric_rows = []
    for spec in SPECS:
        print(f"Backtesting {spec.name}")
        candidates = make_candidate_table(daily, symbol_tables, spec)
        trades = select_portfolio_trades(candidates, spec)
        strategy_daily = daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
        m = metrics_for_strategy(trades, strategy_daily, spec.name)
        metric_rows.append(m)
        monthly = period_analysis(strategy_daily, trades, "M")
        monthly.insert(0, "strategy", spec.name)
        monthly_frames.append(monthly)
        eq = strategy_daily[["date", "equity"]].copy()
        eq.insert(0, "strategy", spec.name)
        equity_frames.append(eq)

    pd.concat(monthly_frames, ignore_index=True).to_csv(DEFAULT_OUT_DIR / "tuned_monthly_analysis.csv", index=False)
    pd.concat(equity_frames, ignore_index=True).to_csv(DEFAULT_OUT_DIR / "tuned_equity_curves.csv", index=False)
    pd.DataFrame(metric_rows).to_csv(DEFAULT_OUT_DIR / "tuned_metrics.csv", index=False)
    print("Wrote tuned_monthly_analysis.csv, tuned_equity_curves.csv, tuned_metrics.csv")


if __name__ == "__main__":
    main()
