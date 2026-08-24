"""Exit research for the Daily Volume Breakout setup.

Two questions, answered separately:

PART 1 -- when does the edge actually decay?
  The 20-session hold was assumed, not derived. This walks every signal's
  price path session by session and measures where the average trade stops
  making progress, plus MFE/MAE (how far trades run in favour before they
  finish, and how far underwater they go first). That is the empirical
  answer to "what is the best point to exit" rather than holding blindly.
  Run on raw signals, not portfolio-selected trades, so the answer is a
  property of the edge and not of the 10-slot capacity constraint.

PART 2 -- a health score instead of a single indicator.
  Rather than exiting on one EMA break, score the trade every session across
  trend, structure, momentum, give-back from peak, daily close strength,
  participation (volume), and market regime. Exit when the overall picture
  deteriorates past a threshold. Thresholds are swept.

No lookahead anywhere: session j uses only data through session j, and any
exit fills at session j's close. 30 bps/side.
"""

from __future__ import annotations

import math

import numpy as np
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
MAX_PATH = 40


# ----------------------------------------------------------------- part 1
def path_analysis(df, tables, cfg) -> pd.DataFrame:
    mask = build_signal(df, cfg)
    paths = []
    for row in df.loc[mask, ["symbol", "local_idx"]].itertuples(index=False):
        sdf = tables.get(row.symbol)
        if sdf is None:
            continue
        si = int(row.local_idx)
        ei = si + 1
        if ei + MAX_PATH >= len(sdf):
            continue
        entry = float(sdf.at[ei, "open"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        closes = sdf["close"].iloc[ei:ei + MAX_PATH].to_numpy(dtype=float)
        highs = sdf["high"].iloc[ei:ei + MAX_PATH].to_numpy(dtype=float)
        lows = sdf["low"].iloc[ei:ei + MAX_PATH].to_numpy(dtype=float)
        if len(closes) < MAX_PATH:
            continue
        paths.append((closes / entry - 1, highs / entry - 1, lows / entry - 1))
    if not paths:
        return pd.DataFrame()

    ret = np.vstack([p[0] for p in paths])
    hi = np.vstack([p[1] for p in paths])
    lo = np.vstack([p[2] for p in paths])
    cost = 2 * BPS_PER_SIDE / 10000.0

    rows = []
    for d in range(MAX_PATH):
        col = ret[:, d] - cost
        rows.append({
            "session": d + 1,
            "n": len(col),
            "mean_pct": round(float(col.mean()) * 100, 3),
            "median_pct": round(float(np.median(col)) * 100, 3),
            "pct_positive": round(float((col > 0).mean()) * 100, 1),
            "std_pct": round(float(col.std(ddof=1)) * 100, 2),
            "mean_over_std": round(float(col.mean() / col.std(ddof=1)), 4) if col.std(ddof=1) > 0 else None,
            # marginal contribution of holding one more session
            "marginal_pct": round(float(col.mean() - (ret[:, d - 1] - cost).mean()) * 100, 3) if d > 0 else None,
            "mfe_to_here_pct": round(float(hi[:, :d + 1].max(axis=1).mean()) * 100, 2),
            "mae_to_here_pct": round(float(lo[:, :d + 1].min(axis=1).mean()) * 100, 2),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- part 2
HEALTH_WEIGHTS = {
    "above_ema20": 20,     # trend the move rides is intact
    "ema_stack": 15,       # ema20 > ema50, structure intact
    "above_entry": 15,     # the trade is actually working
    "holding_peak": 15,    # not giving back more than 10% from peak close
    "close_strength": 10,  # closing in the upper half of its own range
    "market_ok": 10,       # broad regime still supportive
    "participation": 5,    # volume hasn't collapsed
    "no_slide": 10,        # not three consecutive lower closes
}
MAX_HEALTH = sum(HEALTH_WEIGHTS.values())


def health_score(sdf, j, entry, peak_close, down_streak) -> int:
    score = 0
    close_j = float(sdf.at[j, "close"])
    ema20 = float(sdf.at[j, "ema20"])
    ema50 = float(sdf.at[j, "ema50"])
    if math.isfinite(ema20) and close_j > ema20:
        score += HEALTH_WEIGHTS["above_ema20"]
    if math.isfinite(ema20) and math.isfinite(ema50) and ema20 > ema50:
        score += HEALTH_WEIGHTS["ema_stack"]
    if close_j > entry:
        score += HEALTH_WEIGHTS["above_entry"]
    if peak_close > 0 and close_j >= peak_close * 0.90:
        score += HEALTH_WEIGHTS["holding_peak"]
    cl = sdf.at[j, "close_location"]
    if pd.notna(cl) and float(cl) >= 0.5:
        score += HEALTH_WEIGHTS["close_strength"]
    if bool(sdf.at[j, "market_ok"]):
        score += HEALTH_WEIGHTS["market_ok"]
    rv = sdf.at[j, "relvol20"]
    if pd.notna(rv) and float(rv) >= 0.5:
        score += HEALTH_WEIGHTS["participation"]
    if down_streak < 3:
        score += HEALTH_WEIGHTS["no_slide"]
    return score


def simulate_health(sdf, signal_idx, hold_days, threshold, delay=2):
    entry_idx = signal_idx + 1
    max_exit = min(signal_idx + hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit:
        return -1, float("nan"), "invalid", 0
    entry = float(sdf.at[entry_idx, "open"])
    if not math.isfinite(entry) or entry <= 0:
        return -1, float("nan"), "invalid", 0

    peak_close = entry
    down_streak = 0
    prev_close = entry
    for j in range(entry_idx, max_exit + 1):
        hold = j - entry_idx + 1
        close_j = float(sdf.at[j, "close"])
        down_streak = down_streak + 1 if close_j < prev_close else 0
        if hold > delay:
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
            "strategy": "x", "symbol": row.symbol, "volume_group": row.volume_group,
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
    oos = sp[sp["split"] == "out_of_sample"]
    m["oos_return_pct"] = round(float(oos.iloc[0]["portfolio_return_pct"]), 2) if not oos.empty else None
    m["oos_sharpe"] = round(float(oos.iloc[0]["daily_sharpe"]), 3) if not oos.empty else None
    if not sel.empty and "exit_reason" in sel:
        m["pct_early_exit"] = round((sel["exit_reason"] != "time").mean() * 100, 1)
        r = sel["net_return"]
        m["worst_ex_split_pct"] = round(float(r[r > -0.6].min()) * 100, 1)
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

    cfg = Config("tuned", close_loc_min=0.80, max_positions=10)

    print("PART 1: session-by-session path analysis")
    paths = path_analysis(df, tables, cfg)
    paths.to_csv(DEFAULT_OUT_DIR / "dvb_exit_path.csv", index=False)
    print(paths.to_string(index=False))

    print()
    print("PART 2: health-score exits")
    rows = []
    spec20 = cfg.to_spec()
    rows.append(evaluate("baseline_hold20", build_trades(df, tables, cfg, lambda s, i: _fixed(s, i, 20)),
                         tables, dates, spec20, start, end))
    for th in (30, 40, 50, 60, 70):
        name = f"health_lt{th}"
        print(f"  {name}")
        tr = build_trades(df, tables, cfg, lambda s, i, t=th: simulate_health(s, i, 20, t))
        rows.append(evaluate(name, tr, tables, dates, spec20, start, end))

    # best empirical hold from part 1, with and without the health overlay
    best_n = int(paths.loc[paths["mean_over_std"].idxmax(), "session"]) if not paths.empty else 20
    print(f"  best risk-adjusted hold from path analysis: {best_n} sessions")
    cfg_best = Config("tuned", close_loc_min=0.80, max_positions=10, hold_days=best_n)
    spec_best = cfg_best.to_spec()
    rows.append(evaluate(f"hold{best_n}_plain",
                         build_trades(df, tables, cfg_best, lambda s, i: _fixed(s, i, best_n)),
                         tables, dates, spec_best, start, end))
    for th in (40, 50):
        rows.append(evaluate(f"hold{best_n}_health_lt{th}",
                             build_trades(df, tables, cfg_best, lambda s, i, t=th: simulate_health(s, i, best_n, t)),
                             tables, dates, spec_best, start, end))

    out = pd.DataFrame(rows)
    cols = ["strategy", "trades", "win_rate_pct", "profit_factor", "portfolio_return_pct",
            "daily_sharpe", "p_value", "max_drawdown_pct", "avg_hold_days", "pct_early_exit",
            "worst_ex_split_pct", "oos_return_pct", "oos_sharpe"]
    out = out[[c for c in cols if c in out.columns]]
    out.to_csv(DEFAULT_OUT_DIR / "dvb_health_exits.csv", index=False)
    print()
    print(out.to_string(index=False))
    print("Wrote dvb_exit_path.csv, dvb_health_exits.csv")


def _fixed(sdf, signal_idx, hold_days):
    entry_idx = signal_idx + 1
    max_exit = min(signal_idx + hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit:
        return -1, float("nan"), "invalid", 0
    return max_exit, float(sdf.at[max_exit, "close"]), "time", max_exit - entry_idx + 1


if __name__ == "__main__":
    main()
