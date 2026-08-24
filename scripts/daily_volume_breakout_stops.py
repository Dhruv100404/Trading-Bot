"""Smarter stop-loss designs for the tuned Daily Volume Breakout config.

The plain 1.5 ATR stop tested earlier fires on the intraday LOW, so a single
volatile wick ejects the trade -- average hold collapsed 19.7 -> 11.3
sessions and win rate fell to 35%. These variants test the obvious ways to
be less trigger-happy:

  close-based   -- only exit if the CLOSE breaks the stop, ignoring wicks
  delayed       -- no stop for the first N sessions, letting the post-spike
                   volatility settle before risk management engages
  trailing      -- stop rides up under the highest close so far, so the
                   protection tightens only as the trade works
  breakeven     -- start wide, pull the stop to entry once the trade is up

All decisions use data through session j only; exits price at that session's
close (close-based/trailing) or at the stop level (intraday), matching the
existing engine's convention. Run at 30 bps/side, the realistic cost level.
"""

from __future__ import annotations

import math

import pandas as pd

import daily_volume_breakout_lab as lab
import swing_volume_spike_review as rev
from daily_volume_breakout_lab import Config, build_signal
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
    metrics_for_strategy,
    select_portfolio_trades,
    split_metrics,
)

BPS_PER_SIDE = 30.0


class Exit:
    def __init__(self, name, mode="none", mult=0.0, delay=0, breakeven_at=None):
        self.name = name
        self.mode = mode          # none | intraday | close | trail_close
        self.mult = mult
        self.delay = delay        # sessions before the stop engages
        self.breakeven_at = breakeven_at


EXITS = [
    Exit("00_no_stop"),
    Exit("01_intraday_1.5", "intraday", 1.5),
    Exit("02_intraday_2.0", "intraday", 2.0),
    Exit("03_close_1.5", "close", 1.5),
    Exit("04_close_2.0", "close", 2.0),
    Exit("05_close_2.5", "close", 2.5),
    Exit("06_close_2.0_delay3", "close", 2.0, delay=3),
    Exit("07_close_2.0_delay5", "close", 2.0, delay=5),
    Exit("08_close_1.5_delay5", "close", 1.5, delay=5),
    Exit("09_trail_close_2.5", "trail_close", 2.5),
    Exit("10_trail_close_3.0", "trail_close", 3.0),
    Exit("11_trail_close_2.5_delay5", "trail_close", 2.5, delay=5),
    Exit("12_close_2.5_breakeven8", "close", 2.5, breakeven_at=0.08),
]


def simulate(sdf, signal_idx: int, hold_days: int, ex: Exit):
    entry_idx = signal_idx + 1
    max_exit = min(signal_idx + hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit:
        return -1, float("nan"), "invalid", 0
    entry = float(sdf.at[entry_idx, "open"])
    atr = float(sdf.at[signal_idx, "atr14"])
    if not math.isfinite(entry) or entry <= 0 or not math.isfinite(atr):
        return -1, float("nan"), "invalid", 0

    peak_close = entry
    for j in range(entry_idx, max_exit + 1):
        hold = j - entry_idx + 1
        low_j = float(sdf.at[j, "low"])
        close_j = float(sdf.at[j, "close"])
        open_j = float(sdf.at[j, "open"])

        if ex.mode != "none" and hold > ex.delay:
            base = peak_close if ex.mode == "trail_close" else entry
            stop = base - ex.mult * atr
            if ex.breakeven_at is not None and peak_close >= entry * (1 + ex.breakeven_at):
                stop = max(stop, entry)
            if ex.mode == "intraday":
                if low_j <= stop:
                    price = open_j if open_j < stop else stop
                    return j, price, "stop", hold
            elif close_j < stop:
                return j, close_j, "stop", hold

        peak_close = max(peak_close, close_j)

    return max_exit, float(sdf.at[max_exit, "close"]), "time", max_exit - entry_idx + 1


def candidates(df, tables, cfg: Config, ex: Exit) -> pd.DataFrame:
    mask = build_signal(df, cfg)
    cols = ["symbol", "trade_date", "local_idx", "relvol50", "volume_group"]
    rows = []
    for row in df.loc[mask, cols].itertuples(index=False):
        sdf = tables.get(row.symbol)
        if sdf is None:
            continue
        si = int(row.local_idx)
        ei, px, reason, hold = simulate(sdf, si, cfg.hold_days, ex)
        if ei < 0:
            continue
        entry = float(sdf.at[si + 1, "open"])
        rows.append(
            {
                "strategy": ex.name, "symbol": row.symbol, "volume_group": row.volume_group,
                "signal_date": pd.Timestamp(row.trade_date),
                "entry_date": pd.Timestamp(sdf.at[si + 1, "trade_date"]),
                "exit_date": pd.Timestamp(sdf.at[ei, "trade_date"]),
                "signal_idx": si, "entry_idx": si + 1, "exit_idx": ei,
                "entry": entry, "exit": px, "exit_reason": reason, "hold_days": hold,
                "net_return": px / entry - 1 - lab.ROUND_TRIP_COST,
                "rank_score": float(row.relvol50),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    side = BPS_PER_SIDE / 10000.0
    rev.SIDE_COST = side
    rev.ROUND_TRIP_COST = 2 * side
    lab.ROUND_TRIP_COST = 2 * side

    print("Loading daily cache")
    raw = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw = append_recent_parquet_daily(raw, DEFAULT_PARQUET_DIR)
    df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    tables = build_symbol_tables(df)
    dates = df["trade_date"].drop_duplicates().sort_values()
    start, end = pd.Timestamp(df["trade_date"].min()), pd.Timestamp(df["trade_date"].max())

    cfg = Config("tuned", close_loc_min=0.80, max_positions=10)
    spec = cfg.to_spec()
    rows = []
    for ex in EXITS:
        print(f"Backtesting {ex.name}")
        cand = candidates(df, tables, cfg, ex)
        trades = select_portfolio_trades(cand, spec)
        daily = daily_returns_from_trades(trades, tables, dates, spec.max_positions)
        m = metrics_for_strategy(trades, daily, ex.name)
        sp = split_metrics(trades, daily, start, end)
        oos = sp[sp["split"] == "out_of_sample"]
        m["oos_return_pct"] = round(float(oos.iloc[0]["portfolio_return_pct"]), 2) if not oos.empty else None
        m["oos_sharpe"] = round(float(oos.iloc[0]["daily_sharpe"]), 3) if not oos.empty else None
        if not trades.empty:
            m["pct_stopped"] = round((trades["exit_reason"] == "stop").mean() * 100, 1)
            r = trades["net_return"].sort_values(ascending=False)
            m["top20_share_pct"] = round(r.head(20).sum() / r.sum() * 100, 1) if r.sum() > 0 else None
            m["worst_trade_pct"] = round(r.min() * 100, 1)
        rows.append(m)

    out = pd.DataFrame(rows)
    cols = ["strategy", "trades", "win_rate_pct", "profit_factor", "portfolio_return_pct",
            "daily_sharpe", "p_value", "max_drawdown_pct", "avg_hold_days", "pct_stopped",
            "worst_trade_pct", "top20_share_pct", "oos_return_pct", "oos_sharpe"]
    out = out[[c for c in cols if c in out.columns]]
    out.to_csv(DEFAULT_OUT_DIR / "dvb_stop_designs.csv", index=False)
    print()
    print(out.to_string(index=False))
    print("Wrote dvb_stop_designs.csv")


if __name__ == "__main__":
    main()
