"""Volatility-based position sizing for the Daily Volume Breakout.

Entry-factor research found no predictive characteristic among qualifying
signals (all |IC| < 0.07, none monotonic), so further return forecasting is
exhausted. What remains is risk accounting: with equal capital per slot, a
9%-ATR smallcap contributes roughly three times the portfolio risk of a
3%-ATR name. Sizing inversely to ATR equalises risk contribution.

This is not a forecast and has no fitted threshold -- the reference ATR is
fixed a priori at 4% rather than tuned, and the only choice is how hard to
cap the resulting weights. Caps exist because uncapped 1/vol sizing piles
capital into the lowest-volatility name.

    weight = (1 / max_positions) * clip(0.04 / atr_pct, lo, hi)

atr_pct is known at the signal close, so sizing is decided before entry.
Reported per window so the holdout stays an honest test. 30 bps/side.
"""

from __future__ import annotations

import math

import numpy as np
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
    max_drawdown_info,
    p_value_from_t,
    select_portfolio_trades,
)

BPS_PER_SIDE = 30.0
REF_ATR_PCT = 0.04
IS_END = pd.Timestamp("2024-05-18")
OOS_START = pd.Timestamp("2025-07-03")


def weighted_daily(trades, tables, dates, max_positions, lo=None, hi=None):
    """Daily portfolio returns with per-trade weights (equal weight if lo is None)."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates).sort_values().unique())
    pos = {d: i for i, d in enumerate(idx)}
    ret = np.zeros(len(idx))
    gross = np.zeros(len(idx))
    base = 1.0 / max_positions
    side = BPS_PER_SIDE / 10000.0

    for row in trades.itertuples(index=False):
        sdf = tables[str(row.symbol)]
        atr_pct = float(sdf.at[row.signal_idx, "atr14"]) / float(sdf.at[row.signal_idx, "close"])
        if lo is None or not math.isfinite(atr_pct) or atr_pct <= 0:
            w = base
        else:
            w = base * float(np.clip(REF_ATR_PCT / atr_pct, lo, hi))
        prev = float(row.entry)
        for j in range(int(row.entry_idx), int(row.exit_idx) + 1):
            d = pd.Timestamp(sdf.at[j, "trade_date"])
            k = pos.get(d)
            if k is None:
                continue
            price = float(row.exit) if j == int(row.exit_idx) else float(sdf.at[j, "close"])
            if not math.isfinite(price) or prev <= 0:
                continue
            step = price / prev - 1
            if j == int(row.entry_idx):
                step -= side
            if j == int(row.exit_idx):
                step -= side
            ret[k] += w * step
            gross[k] += w
            prev = price

    out = pd.DataFrame({"date": idx, "daily_return": ret, "gross_exposure": gross})
    out["equity"] = (1 + out["daily_return"]).cumprod()
    out["drawdown"] = out["equity"] / out["equity"].cummax() - 1
    return out


def stats(name, daily, trades):
    r = daily["daily_return"]
    active = r[r != 0]
    mean, sd = float(active.mean()), float(active.std(ddof=1))
    sharpe = mean / sd * math.sqrt(252) if sd > 0 else float("nan")
    t = mean / (sd / math.sqrt(len(active))) if sd > 0 else float("nan")
    dd = max_drawdown_info(daily["equity"], daily["date"])
    row = {
        "config": name,
        "trades": len(trades),
        "return_pct": round((float(daily["equity"].iloc[-1]) - 1) * 100, 1),
        "sharpe": round(sharpe, 3),
        "p_value": round(p_value_from_t(t, len(active)), 5),
        "max_dd_pct": dd["max_drawdown_pct"],
        "mean_gross": round(float(daily["gross_exposure"].mean()), 3),
        "max_gross": round(float(daily["gross_exposure"].max()), 3),
    }
    for tag, lo_d, hi_d in (("is", daily["date"].min(), IS_END),
                            ("val", IS_END, OOS_START),
                            ("oos", OOS_START, daily["date"].max())):
        sub = daily[(daily["date"] >= lo_d) & (daily["date"] < hi_d)]
        a = sub["daily_return"][sub["daily_return"] != 0]
        if len(a) < 10:
            continue
        m, s = float(a.mean()), float(a.std(ddof=1))
        row[f"{tag}_sharpe"] = round(m / s * math.sqrt(252), 3) if s > 0 else None
        row[f"{tag}_return"] = round((float((1 + sub["daily_return"]).prod()) - 1) * 100, 1)
        row[f"{tag}_dd"] = round(float((sub["equity"] / sub["equity"].cummax() - 1).min()) * 100, 1)
    return row


def main() -> None:
    side = BPS_PER_SIDE / 10000.0
    rev.SIDE_COST = side
    rev.ROUND_TRIP_COST = 2 * side
    lab.ROUND_TRIP_COST = 2 * side

    print("Loading daily cache")
    raw = append_recent_parquet_daily(load_daily_cache(DEFAULT_DAILY_CACHE), DEFAULT_PARQUET_DIR)
    df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    tables = build_symbol_tables(df)
    dates = df["trade_date"].drop_duplicates().sort_values()

    cfg = Config("tuned", close_loc_min=0.80, max_positions=10)
    trades = select_portfolio_trades(candidates_for(df, tables, cfg), cfg.to_spec())
    print(f"trades: {len(trades)}")

    rows = []
    for name, lo, hi in (
        ("equal_weight", None, None),
        ("riskparity_cap_0.75_1.33", 0.75, 1.33),
        ("riskparity_cap_0.67_1.50", 0.67, 1.50),
        ("riskparity_cap_0.50_2.00", 0.50, 2.00),
        ("riskparity_cap_0.40_2.50", 0.40, 2.50),
    ):
        print(f"  {name}")
        daily = weighted_daily(trades, tables, dates, cfg.max_positions, lo, hi)
        rows.append(stats(name, daily, trades))

    out = pd.DataFrame(rows)
    out.to_csv(DEFAULT_OUT_DIR / "dvb_position_sizing.csv", index=False)
    print()
    print(out.to_string(index=False))
    print("Wrote dvb_position_sizing.csv")


if __name__ == "__main__":
    main()
