"""Confirmation run for the Daily Volume Breakout tuning.

Combines only the parameter changes that sat on a *plateau* in the
one-factor sweep (neighbouring values also good), deliberately excluding
high_prox=0.90 -- that one scored best in isolation but its neighbours at
0.85 and 0.95 were much worse, which is the signature of a lucky fit rather
than an edge. It is included here as a labelled control so the fragility is
visible rather than hidden.

Emits monthly + equity + trade detail for the variants worth charting.
"""

from __future__ import annotations

import pandas as pd

from daily_volume_breakout_lab import Config, run_config
from swing_volume_spike_review import (
    DEFAULT_DAILY_CACHE,
    DEFAULT_OUT_DIR,
    DEFAULT_PARQUET_DIR,
    DEFAULT_VOLUME_GROUPS,
    add_features,
    append_recent_parquet_daily,
    build_symbol_tables,
    load_daily_cache,
    load_volume_groups,
    period_analysis,
)

CONFIGS = [
    Config("A_baseline_frozen"),
    Config("B_tuned_pos10", close_loc_min=0.80, max_positions=10),
    Config("C_tuned_pos10_stop15", close_loc_min=0.80, max_positions=10, stop_atr=1.5),
    Config("D_tuned_pos10_stop25", close_loc_min=0.80, max_positions=10, stop_atr=2.5),
    Config("E_control_highprox090", high_prox=0.90),
]


def concentration(trades: pd.DataFrame) -> str:
    r = trades["net_return"].sort_values(ascending=False)
    tot = r.sum()
    if tot <= 0:
        return "n/a"
    return " | ".join(f"top{k}={r.head(k).sum() / tot * 100:.0f}%" for k in (5, 10, 20))


def main() -> None:
    print("Loading daily cache")
    raw = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw = append_recent_parquet_daily(raw, DEFAULT_PARQUET_DIR)
    df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    symbol_tables = build_symbol_tables(df)
    dates = df["trade_date"].drop_duplicates().sort_values()
    start, end = pd.Timestamp(df["trade_date"].min()), pd.Timestamp(df["trade_date"].max())

    rows, monthlies, equities = [], [], []
    for cfg in CONFIGS:
        print(f"Backtesting {cfg.name}")
        m, trades, daily = run_config(df, symbol_tables, dates, start, end, cfg)
        m["concentration"] = concentration(trades)
        rows.append(m)
        mo = period_analysis(daily, trades, "M")
        mo.insert(0, "strategy", cfg.name)
        monthlies.append(mo)
        eq = daily[["date", "equity", "drawdown"]].copy()
        eq.insert(0, "strategy", cfg.name)
        equities.append(eq)
        if cfg.name == "B_tuned_pos10":
            trades.to_csv(DEFAULT_OUT_DIR / "dvb_tuned_trades.csv", index=False)

    pd.concat(monthlies, ignore_index=True).to_csv(DEFAULT_OUT_DIR / "dvb_confirm_monthly.csv", index=False)
    pd.concat(equities, ignore_index=True).to_csv(DEFAULT_OUT_DIR / "dvb_confirm_equity.csv", index=False)
    out = pd.DataFrame(rows)
    cols = [
        "strategy", "trades", "win_rate_pct", "profit_factor", "expectancy_pct",
        "portfolio_return_pct", "daily_sharpe", "p_value", "max_drawdown_pct",
        "avg_hold_days", "avg_active_positions",
        "in_sample_return_pct", "validation_return_pct", "out_of_sample_return_pct",
        "out_of_sample_sharpe", "out_of_sample_trades", "concentration",
    ]
    out = out[[c for c in cols if c in out.columns]]
    out.to_csv(DEFAULT_OUT_DIR / "dvb_confirm_metrics.csv", index=False)
    print(out.to_string(index=False))
    print("Wrote dvb_confirm_*.csv")


if __name__ == "__main__":
    main()
