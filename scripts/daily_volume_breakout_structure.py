"""Structure-based exits for the tuned Daily Volume Breakout config.

ATR stops measure distance; they don't know whether the trade's premise is
still intact. This tests the alternative: exit when the breakout structure
itself fails. The reason for being in the trade is a high-volume breakout
holding above a rising short-term trend -- so the natural invalidation
levels are the breakout candle's own low, the EMA the move is riding, and
recent swing lows.

Levels tested (all evaluated on the CLOSE, so intraday wicks don't eject):
  sig_low   -- close below the signal candle's low: the breakout failed
  ema20     -- close below EMA20: the trend the move rides has broken
  ema10     -- faster version of the same
  ema50     -- loose backstop
  swingN    -- close below the lowest low of the prior N sessions
  confirm2  -- requires two consecutive closes below the level
  combos    -- either-of / both-of pairings

No lookahead: at session j the level uses data through j-1 (or the signal
bar), and the exit fills at session j's close. 30 bps/side.
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


class Rule:
    def __init__(self, name, kind="none", delay=0, confirm=1, swing_n=5, atr_mult=0.0):
        self.name = name
        self.kind = kind          # none | sig_low | ema10 | ema20 | ema50 | swing | either | both | atr
        self.delay = delay        # sessions before the rule engages
        self.confirm = confirm    # consecutive closes below the level required
        self.swing_n = swing_n
        self.atr_mult = atr_mult


RULES = [
    Rule("00_no_stop"),
    Rule("01_atr_intraday_2.0", "atr", atr_mult=2.0),
    Rule("02_sig_low", "sig_low"),
    Rule("03_sig_low_delay2", "sig_low", delay=2),
    Rule("04_ema20", "ema20"),
    Rule("05_ema20_delay3", "ema20", delay=3),
    Rule("06_ema20_delay5", "ema20", delay=5),
    Rule("07_ema20_confirm2", "ema20", confirm=2),
    Rule("08_ema10", "ema10"),
    Rule("09_ema50", "ema50"),
    Rule("10_swing5", "swing", swing_n=5),
    Rule("11_swing10", "swing", swing_n=10),
    Rule("12_either_siglow_ema20", "either"),
    Rule("13_both_siglow_ema20", "both"),
    Rule("14_ema20_confirm2_delay3", "ema20", confirm=2, delay=3),
]


def level_for(sdf, j, signal_idx, kind, swing_n):
    """Structural level for session j, using only data available by then."""
    if kind == "sig_low":
        return float(sdf.at[signal_idx, "low"])
    if kind in ("ema10", "ema20", "ema50"):
        v = float(sdf.at[j, kind])
        return v if math.isfinite(v) else None
    if kind == "swing":
        lo = max(0, j - swing_n)
        if lo >= j:
            return None
        return float(sdf["low"].iloc[lo:j].min())
    return None


def simulate(sdf, signal_idx: int, hold_days: int, rule: Rule):
    entry_idx = signal_idx + 1
    max_exit = min(signal_idx + hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit:
        return -1, float("nan"), "invalid", 0
    entry = float(sdf.at[entry_idx, "open"])
    atr = float(sdf.at[signal_idx, "atr14"])
    if not math.isfinite(entry) or entry <= 0 or not math.isfinite(atr):
        return -1, float("nan"), "invalid", 0

    breaches = 0
    for j in range(entry_idx, max_exit + 1):
        hold = j - entry_idx + 1
        close_j = float(sdf.at[j, "close"])

        if rule.kind != "none" and hold > rule.delay:
            if rule.kind == "atr":
                stop = entry - rule.atr_mult * atr
                if float(sdf.at[j, "low"]) <= stop:
                    op = float(sdf.at[j, "open"])
                    return j, (op if op < stop else stop), "structure", hold
            else:
                if rule.kind == "either":
                    a = level_for(sdf, j, signal_idx, "sig_low", 0)
                    b = level_for(sdf, j, signal_idx, "ema20", 0)
                    hit = (a is not None and close_j < a) or (b is not None and close_j < b)
                elif rule.kind == "both":
                    a = level_for(sdf, j, signal_idx, "sig_low", 0)
                    b = level_for(sdf, j, signal_idx, "ema20", 0)
                    hit = (a is not None and close_j < a) and (b is not None and close_j < b)
                else:
                    lv = level_for(sdf, j, signal_idx, rule.kind, rule.swing_n)
                    hit = lv is not None and close_j < lv
                breaches = breaches + 1 if hit else 0
                if breaches >= rule.confirm:
                    return j, close_j, "structure", hold

    return max_exit, float(sdf.at[max_exit, "close"]), "time", max_exit - entry_idx + 1


def candidates(df, tables, cfg: Config, rule: Rule) -> pd.DataFrame:
    mask = build_signal(df, cfg)
    cols = ["symbol", "trade_date", "local_idx", "relvol50", "volume_group"]
    rows = []
    for row in df.loc[mask, cols].itertuples(index=False):
        sdf = tables.get(row.symbol)
        if sdf is None:
            continue
        si = int(row.local_idx)
        ei, px, reason, hold = simulate(sdf, si, cfg.hold_days, rule)
        if ei < 0:
            continue
        entry = float(sdf.at[si + 1, "open"])
        rows.append({
            "strategy": rule.name, "symbol": row.symbol, "volume_group": row.volume_group,
            "signal_date": pd.Timestamp(row.trade_date),
            "entry_date": pd.Timestamp(sdf.at[si + 1, "trade_date"]),
            "exit_date": pd.Timestamp(sdf.at[ei, "trade_date"]),
            "signal_idx": si, "entry_idx": si + 1, "exit_idx": ei,
            "entry": entry, "exit": px, "exit_reason": reason, "hold_days": hold,
            "net_return": px / entry - 1 - lab.ROUND_TRIP_COST,
            "rank_score": float(row.relvol50),
        })
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
    print(f"Window {start.date()} -> {end.date()}")

    cfg = Config("tuned", close_loc_min=0.80, max_positions=10)
    spec = cfg.to_spec()
    rows = []
    for rule in RULES:
        print(f"Backtesting {rule.name}")
        cand = candidates(df, tables, cfg, rule)
        trades = select_portfolio_trades(cand, spec)
        daily = daily_returns_from_trades(trades, tables, dates, spec.max_positions)
        m = metrics_for_strategy(trades, daily, rule.name)
        sp = split_metrics(trades, daily, start, end)
        oos = sp[sp["split"] == "out_of_sample"]
        m["oos_return_pct"] = round(float(oos.iloc[0]["portfolio_return_pct"]), 2) if not oos.empty else None
        m["oos_sharpe"] = round(float(oos.iloc[0]["daily_sharpe"]), 3) if not oos.empty else None
        if not trades.empty:
            m["pct_structure_exit"] = round((trades["exit_reason"] == "structure").mean() * 100, 1)
            # PRECWIRE Dec-2021 is an unadjusted 1:5 split, not a real loss --
            # report the worst genuine trade alongside the raw worst.
            r = trades["net_return"]
            m["worst_trade_pct"] = round(r.min() * 100, 1)
            m["worst_ex_split_pct"] = round(r[r > -0.6].min() * 100, 1)
        rows.append(m)

    out = pd.DataFrame(rows)
    cols = ["strategy", "trades", "win_rate_pct", "profit_factor", "portfolio_return_pct",
            "daily_sharpe", "p_value", "max_drawdown_pct", "avg_hold_days",
            "pct_structure_exit", "worst_trade_pct", "worst_ex_split_pct",
            "oos_return_pct", "oos_sharpe"]
    out = out[[c for c in cols if c in out.columns]]
    out.to_csv(DEFAULT_OUT_DIR / "dvb_structure_exits.csv", index=False)
    print()
    print(out.to_string(index=False))
    print("Wrote dvb_structure_exits.csv")


if __name__ == "__main__":
    main()
