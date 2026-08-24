"""Current signals and open positions for the tuned Daily Volume Breakout.

Runs the recommended config (close_loc 0.80, 10 positions, 20-session hold,
no stop) over the refreshed cache and reports what is open right now plus
what fired recently. 30 bps/side.
"""

from __future__ import annotations

import pandas as pd

import daily_volume_breakout_lab as lab
import swing_volume_spike_review as rev
from daily_volume_breakout_lab import Config, candidates_for
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
    select_portfolio_trades,
)

side = 30.0 / 10000.0
rev.SIDE_COST = side
rev.ROUND_TRIP_COST = 2 * side
lab.ROUND_TRIP_COST = 2 * side

raw = append_recent_parquet_daily(load_daily_cache(DEFAULT_DAILY_CACHE), DEFAULT_PARQUET_DIR)
df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
tables = build_symbol_tables(df)
end = pd.Timestamp(df["trade_date"].max())

cfg = Config("live", close_loc_min=0.80, max_positions=10)
trades = select_portfolio_trades(candidates_for(df, tables, cfg), cfg.to_spec())

# A position is still open if its scheduled exit is at/after the last bar.
trades["open_now"] = trades["exit_date"] >= end
openpos = trades[trades["open_now"]].copy()
openpos["days_held"] = openpos["exit_idx"] - openpos["entry_idx"] + 1
openpos["unrealised_pct"] = (openpos["net_return"] * 100).round(2)
openpos["sessions_left"] = cfg.hold_days - openpos["days_held"]

print(f"=== LAST BAR IN DATA: {end.date()} ===")
print(f"\nOPEN POSITIONS ({len(openpos)} of {cfg.max_positions} slots):")
print(
    openpos[["symbol", "entry_date", "entry", "relvol50", "days_held", "sessions_left", "unrealised_pct"]]
    .sort_values("entry_date")
    .to_string(index=False)
)

recent = trades[trades["signal_date"] >= end - pd.Timedelta(days=21)].copy()
recent["net_return_pct"] = (recent["net_return"] * 100).round(2)
print(f"\nSIGNALS IN LAST 21 DAYS ({len(recent)}):")
print(
    recent[["signal_date", "entry_date", "symbol", "entry", "relvol50", "net_return_pct"]]
    .sort_values("signal_date", ascending=False)
    .to_string(index=False)
)

openpos.to_csv(DEFAULT_OUT_DIR / "dvb_open_positions.csv", index=False)
print(f"\nWrote dvb_open_positions.csv")
