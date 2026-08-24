"""Final exit design for the Daily Volume Breakout -- selected on in-sample only.

The path analysis showed the average trade is underwater for its first six
sessions, so a health check that engages on session 3 is judging the trade
during the phase where it is *expected* to look bad. This sweeps the delay
before the health check engages, alongside the health threshold and the
hold length, and adds an optional very wide "disaster" stop that stays
active from day one purely to cap catastrophes.

Methodology note: ~130 configurations have been evaluated across this
research session, so ranking by full-period Sharpe would be meaningless.
Selection here uses the IN-SAMPLE window only (2021-01 -> 2024-05); the
validation and out-of-sample windows are reported but never used to choose,
so the out-of-sample column is an honest holdout estimate for whatever the
in-sample ranking picks.

30 bps/side. No lookahead: session j uses data through j, exits fill at j's
close (or the stop level for the disaster stop).
"""

from __future__ import annotations

import math

import pandas as pd

import daily_volume_breakout_lab as lab
import swing_volume_spike_review as rev
from daily_volume_breakout_exit_research import health_score
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


def simulate(sdf, signal_idx, hold_days, threshold, delay, disaster_atr=None):
    entry_idx = signal_idx + 1
    max_exit = min(signal_idx + hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit:
        return -1, float("nan"), "invalid", 0
    entry = float(sdf.at[entry_idx, "open"])
    atr = float(sdf.at[signal_idx, "atr14"])
    if not math.isfinite(entry) or entry <= 0 or not math.isfinite(atr):
        return -1, float("nan"), "invalid", 0

    disaster = entry - disaster_atr * atr if disaster_atr else None
    peak_close = entry
    prev_close = entry
    down_streak = 0
    for j in range(entry_idx, max_exit + 1):
        hold = j - entry_idx + 1
        close_j = float(sdf.at[j, "close"])

        # Disaster stop is live from day one -- it exists only to cap tail
        # events, not to manage the normal early-drawdown phase.
        if disaster is not None and float(sdf.at[j, "low"]) <= disaster:
            op = float(sdf.at[j, "open"])
            return j, (op if op < disaster else disaster), "disaster", hold

        down_streak = down_streak + 1 if close_j < prev_close else 0
        if threshold is not None and hold > delay:
            if health_score(sdf, j, entry, peak_close, down_streak) < threshold:
                return j, close_j, "unhealthy", hold
        peak_close = max(peak_close, close_j)
        prev_close = close_j

    return max_exit, float(sdf.at[max_exit, "close"]), "time", max_exit - entry_idx + 1


def build_trades(df, tables, cfg, exit_fn) -> pd.DataFrame:
    mask = build_signal(df, cfg)
    rows = []
    for row in df.loc[mask, ["symbol", "trade_date", "local_idx", "relvol50", "volume_group"]].itertuples(index=False):
        sdf = tables.get(row.symbol)
        if sdf is None:
            continue
        si = int(row.local_idx)
        ei, px, reason, hold = exit_fn(sdf, si)
        if ei < 0:
            continue
        entry = float(sdf.at[si + 1, "open"])
        rows.append({
            "symbol": row.symbol, "volume_group": row.volume_group,
            "signal_date": pd.Timestamp(row.trade_date),
            "entry_date": pd.Timestamp(sdf.at[si + 1, "trade_date"]),
            "exit_date": pd.Timestamp(sdf.at[ei, "trade_date"]),
            "signal_idx": si, "entry_idx": si + 1, "exit_idx": ei,
            "entry": entry, "exit": px, "exit_reason": reason, "hold_days": hold,
            "net_return": px / entry - 1 - lab.ROUND_TRIP_COST,
            "rank_score": float(row.relvol50),
        })
    return pd.DataFrame(rows)


def evaluate(name, trades, tables, dates, spec, start, end) -> dict:
    sel = select_portfolio_trades(trades, spec)
    daily = daily_returns_from_trades(sel, tables, dates, spec.max_positions)
    m = metrics_for_strategy(sel, daily, name)
    sp = split_metrics(sel, daily, start, end)
    for tag, short in (("in_sample", "is"), ("validation", "val"), ("out_of_sample", "oos")):
        sub = sp[sp["split"] == tag]
        m[f"{short}_sharpe"] = round(float(sub.iloc[0]["daily_sharpe"]), 3) if not sub.empty else None
        m[f"{short}_return"] = round(float(sub.iloc[0]["portfolio_return_pct"]), 1) if not sub.empty else None
    if not sel.empty:
        m["pct_early"] = round((sel["exit_reason"] != "time").mean() * 100, 1)
        r = sel["net_return"]
        m["worst_ex_split"] = round(float(r[r > -0.6].min()) * 100, 1)
    return m


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
    start, end = pd.Timestamp(df["trade_date"].min()), pd.Timestamp(df["trade_date"].max())

    rows = []

    def add(name, hold, threshold, delay, disaster=None):
        cfg = Config("t", close_loc_min=0.80, max_positions=10, hold_days=hold)
        tr = build_trades(df, tables, cfg,
                          lambda s, i: simulate(s, i, hold, threshold, delay, disaster))
        m = evaluate(name, tr, tables, dates, cfg.to_spec(), start, end)
        m.update(hold=hold, threshold=threshold if threshold else "none",
                 delay=delay, disaster=disaster if disaster else "none")
        rows.append(m)
        print(f"  {name}: is_sharpe={m.get('is_sharpe')} oos_sharpe={m.get('oos_sharpe')}")

    print("Baselines")
    add("base_hold20_plain", 20, None, 0)
    add("base_hold27_plain", 27, None, 0)

    print("Grid: threshold x delay x hold")
    for hold in (20, 24, 27):
        for th in (25, 30, 35):
            for delay in (2, 6, 10):
                add(f"h{hold}_t{th}_d{delay}", hold, th, delay)

    out = pd.DataFrame(rows)
    out = out.sort_values("is_sharpe", ascending=False).reset_index(drop=True)

    print("\nDisaster-stop check on the top-3 IN-SAMPLE configs")
    for r in out.head(3).to_dict("records"):
        if r["threshold"] == "none":
            continue
        add(f"{r['strategy']}_disaster4", int(r["hold"]), int(r["threshold"]), int(r["delay"]), 4.0)

    final = pd.DataFrame(rows).sort_values("is_sharpe", ascending=False).reset_index(drop=True)
    cols = ["strategy", "hold", "threshold", "delay", "disaster", "trades", "win_rate_pct",
            "portfolio_return_pct", "daily_sharpe", "max_drawdown_pct", "avg_hold_days",
            "pct_early", "worst_ex_split", "is_sharpe", "is_return", "val_sharpe",
            "val_return", "oos_sharpe", "oos_return"]
    final = final[[c for c in cols if c in final.columns]]
    final.to_csv(DEFAULT_OUT_DIR / "dvb_final_selection.csv", index=False)
    print()
    print("=== RANKED BY IN-SAMPLE SHARPE (selection metric); oos_* is untouched holdout ===")
    print(final.to_string(index=False))
    print("Wrote dvb_final_selection.csv")


if __name__ == "__main__":
    main()
