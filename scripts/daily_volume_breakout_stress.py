"""Stress test + dry run for the tuned Daily Volume Breakout config.

Part 1 -- stress: re-runs the recommended config (close_loc 0.80, 10
positions) under progressively harsher slippage, and against a universe
restricted to symbols that already had data at the start of 2021. The
restricted universe does not fully remove survivorship (those names are
still survivors), but it removes the "universe grows from ~500 to ~1160
symbols" artifact, so if the edge only exists in the later-added names it
will show up here.

Part 2 -- dry run: what the tuned config actually signalled recently, which
positions would still be open as of the last bar, and what closed.

Cost constants are module-level in the two source modules, so both are
patched: metrics/net_return read ROUND_TRIP_COST from the lab module, while
daily P&L reads SIDE_COST from the review module.
"""

from __future__ import annotations

import pandas as pd

import daily_volume_breakout_lab as lab
import swing_volume_spike_review as rev
from daily_volume_breakout_lab import Config, candidates_for, run_config
from swing_volume_spike_review import (
    DEFAULT_DAILY_CACHE,
    DEFAULT_OUT_DIR,
    DEFAULT_PARQUET_DIR,
    DEFAULT_VOLUME_GROUPS,
    add_features,
    append_recent_parquet_daily,
    build_symbol_tables,
    daily_returns_from_trades,
    load_daily_cache,
    load_volume_groups,
    select_portfolio_trades,
)

TUNED = dict(close_loc_min=0.80, max_positions=10)


def set_costs(bps_per_side: float) -> None:
    side = bps_per_side / 10000.0
    rev.SIDE_COST = side
    rev.ROUND_TRIP_COST = 2 * side
    lab.ROUND_TRIP_COST = 2 * side


def prepare(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.Series, pd.Timestamp, pd.Timestamp]:
    df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    tables = build_symbol_tables(df)
    dates = df["trade_date"].drop_duplicates().sort_values()
    return df, tables, dates, pd.Timestamp(df["trade_date"].min()), pd.Timestamp(df["trade_date"].max())


def main() -> None:
    print("Loading daily cache")
    raw_full = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw_full = append_recent_parquet_daily(raw_full, DEFAULT_PARQUET_DIR)

    first_seen = raw_full.groupby("symbol")["trade_date"].min()
    early_symbols = set(first_seen[first_seen < pd.Timestamp("2021-02-01")].index)
    raw_early = raw_full[raw_full["symbol"].isin(early_symbols)].copy()
    print(f"Full universe: {raw_full['symbol'].nunique():,} symbols")
    print(f"Restricted (present at start of 2021): {raw_early['symbol'].nunique():,} symbols")

    rows = []
    for uni_name, raw in (("full", raw_full), ("restricted_2021", raw_early)):
        df, tables, dates, start, end = prepare(raw)
        for bps in (13.0, 20.0, 30.0, 50.0):
            set_costs(bps)
            cfg = Config(f"{uni_name}_{bps:.0f}bps", **TUNED)
            print(f"Backtesting {cfg.name}")
            m, trades, daily = run_config(df, tables, dates, start, end, cfg)
            m["universe"] = uni_name
            m["bps_per_side"] = bps
            rows.append(m)

    out = pd.DataFrame(rows)
    cols = [
        "universe", "bps_per_side", "trades", "win_rate_pct", "profit_factor",
        "portfolio_return_pct", "daily_sharpe", "p_value", "max_drawdown_pct",
        "in_sample_return_pct", "validation_return_pct", "out_of_sample_return_pct",
        "out_of_sample_sharpe",
    ]
    out = out[[c for c in cols if c in out.columns]]
    out.to_csv(DEFAULT_OUT_DIR / "dvb_stress.csv", index=False)
    print()
    print(out.to_string(index=False))

    # ---- Part 2: dry run at realistic 30bps on the full universe ----
    set_costs(30.0)
    df, tables, dates, start, end = prepare(raw_full)
    cfg = Config("dry_run", **TUNED)
    cand = candidates_for(df, tables, cfg)
    trades = select_portfolio_trades(cand, cfg.to_spec())
    daily = daily_returns_from_trades(trades, tables, dates, cfg.max_positions)

    cutoff = end - pd.Timedelta(days=120)
    recent = trades[trades["signal_date"] >= cutoff].copy()
    recent["status"] = recent["exit_date"].apply(lambda d: "OPEN" if pd.Timestamp(d) >= end else "closed")
    recent["net_return_pct"] = (recent["net_return"] * 100).round(2)
    recent = recent[
        ["signal_date", "entry_date", "symbol", "entry", "relvol50", "exit_date", "status", "net_return_pct"]
    ].sort_values("signal_date", ascending=False)
    recent.to_csv(DEFAULT_OUT_DIR / "dvb_dry_run_recent.csv", index=False)

    print()
    print(f"=== DRY RUN (30bps/side) -- last bar in data: {end.date()} ===")
    print(f"signals in last 120 days: {len(recent)}   still open: {(recent['status'] == 'OPEN').sum()}")
    print(recent.head(25).to_string(index=False))
    closed = recent[recent["status"] == "closed"]
    if not closed.empty:
        print()
        print(f"closed in window: {len(closed)}  win rate {(closed['net_return_pct'] > 0).mean() * 100:.0f}%  "
              f"avg {closed['net_return_pct'].mean():.2f}%  total {closed['net_return_pct'].sum():.1f}%")
    print("Wrote dvb_stress.csv, dvb_dry_run_recent.csv")


if __name__ == "__main__":
    main()
