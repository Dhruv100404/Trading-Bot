"""Extend the leader_rv8_fixed30 stop-tuning finding to all 8 strategy
variants: for each, bolt an ATR stop onto its native exit (fixed-hold
strategies get exit_mode="target_stop" with no target, since targets were
shown to hurt; the two ema20-based strategies already support a stop via
exit_mode="atr_ema20", so those just get their stop_atr swept). Baseline
(no stop) is included for direct comparison, and each variant is run
through the same robustness_verdict/split_metrics gate as the original
review so watchlist/reject calls are apples-to-apples.
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
    robustness_verdict,
    select_portfolio_trades,
    split_metrics,
    strategy_specs,
)

STOP_GRID = [None, 1.5, 2.0, 2.5, 3.0]


def variant_for(base: StrategySpec, stop_atr: float | None) -> StrategySpec:
    if stop_atr is None:
        return StrategySpec(**{**base.__dict__, "name": f"{base.name}__baseline"})
    exit_mode = "atr_ema20" if base.exit_mode in ("ema20", "atr_ema20") else "target_stop"
    return StrategySpec(
        name=f"{base.name}__stop{stop_atr}",
        label=base.label,
        context=base.context,
        relvol50_min=base.relvol50_min,
        ret1_min=base.ret1_min,
        close_location_min=base.close_location_min,
        hold_days=base.hold_days,
        exit_mode=exit_mode,
        min_hold_days=base.min_hold_days,
        stop_atr=stop_atr,
        target_atr=None,
        max_new_per_day=base.max_new_per_day,
        max_positions=base.max_positions,
    )


def main() -> None:
    print("Loading daily cache")
    raw_daily = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw_daily = append_recent_parquet_daily(raw_daily, DEFAULT_PARQUET_DIR)
    symbol_to_group = load_volume_groups(DEFAULT_VOLUME_GROUPS)
    print("Adding swing features")
    daily = add_features(raw_daily, symbol_to_group)
    symbol_tables = build_symbol_tables(daily)
    trading_dates = daily["trade_date"].drop_duplicates().sort_values()
    start = pd.Timestamp(daily["trade_date"].min())
    end = pd.Timestamp(daily["trade_date"].max())

    rows: list[dict[str, object]] = []
    for base in strategy_specs():
        for stop_atr in STOP_GRID:
            spec = variant_for(base, stop_atr)
            print(f"Backtesting {spec.name}")
            candidates = make_candidate_table(daily, symbol_tables, spec)
            trades = select_portfolio_trades(candidates, spec)
            strategy_daily = daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
            metrics = metrics_for_strategy(trades, strategy_daily, spec.name)
            split = split_metrics(trades, strategy_daily, start, end)
            metrics_df = pd.DataFrame([metrics])
            split_map = {spec.name: split}
            verdict = robustness_verdict(metrics_df, split_map, pd.DataFrame()).iloc[0]

            oos = split[split["split"] == "out_of_sample"]
            oos_return = float(oos.iloc[0]["portfolio_return_pct"]) if not oos.empty else float("nan")
            oos_sharpe = float(oos.iloc[0]["daily_sharpe"]) if not oos.empty else float("nan")

            rows.append(
                {
                    "base_strategy": base.name,
                    "stop_atr": stop_atr if stop_atr is not None else "none",
                    "trades": metrics["trades"],
                    "win_rate_pct": metrics["win_rate_pct"],
                    "profit_factor": metrics["profit_factor"],
                    "portfolio_return_pct": metrics["portfolio_return_pct"],
                    "daily_sharpe": metrics["daily_sharpe"],
                    "p_value": metrics["p_value"],
                    "max_drawdown_pct": metrics["max_drawdown_pct"],
                    "avg_hold_days": metrics["avg_hold_days"],
                    "oos_return_pct": round(oos_return, 3),
                    "oos_sharpe": round(oos_sharpe, 3),
                    "verdict": verdict["label"],
                    "reasons": verdict["reasons"],
                }
            )

    out = pd.DataFrame(rows)
    out_path = DEFAULT_OUT_DIR / "stop_tuning_all_strategies.csv"
    out.to_csv(out_path, index=False)
    print(out.to_string(index=False))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
