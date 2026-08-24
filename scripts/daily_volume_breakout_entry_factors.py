"""Entry-quality factor analysis for the Daily Volume Breakout setup.

Parameter sweeping on this strategy has a measured in-sample -> holdout
Sharpe correlation of -0.31, so searching thresholds finds noise. This takes
the factor-research route instead: hold the entry rule fixed, take every
signal it produces, and ask which *characteristics measurable at the signal
close* predict the forward 20-session return.

A characteristic is only worth acting on if it is:
  (a) monotonic across quintiles -- a graded relationship, not one lucky
      bucket, which is what a real effect looks like; and
  (b) stable -- the same sign and rough shape in the in-sample window and
      in the untouched holdout.

Reported per feature: quintile forward returns and the rank information
coefficient (Spearman corr of feature vs forward return), computed
separately for in-sample / validation / holdout so instability is visible
rather than averaged away.

Measured on the raw signal population, not portfolio-selected trades, so it
reflects signal quality rather than the 10-slot capacity constraint.
No lookahead: every feature is known at the signal close; the forward
return buys the next open and sells 20 sessions later. 30 bps/side.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from daily_volume_breakout_lab import Config, build_signal
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
)

COST = 2 * 30.0 / 10000.0
HOLD = 20
IS_END = pd.Timestamp("2024-05-18")
OOS_START = pd.Timestamp("2025-07-03")

# Feature -> what an edge would mean, so results can be read against a prior
# rather than pattern-matched after the fact.
FEATURES = {
    "relvol50": "size of the volume spike",
    "ret1": "size of the signal-day move",
    "close_location": "where it closed in the day's range",
    "ext_ema20": "extension above EMA20 (chasing?)",
    "atr_pct": "stock volatility (ATR / price)",
    "prox_52w": "closeness to the 52-week high",
    "rs60_rank": "60-day relative strength vs universe",
    "prior_ret20": "how much it already ran before the signal",
    "market_breadth200": "market regime at signal",
    "log_adv20": "liquidity / size proxy",
    "range_vs_atr": "signal-day range vs normal range",
}


def collect_signals(df, tables, cfg) -> pd.DataFrame:
    mask = build_signal(df, cfg)
    cols = ["symbol", "trade_date", "local_idx", "relvol50", "ret1", "close_location",
            "close", "ema20", "atr14", "prior_high252", "rs60_rank", "prior_ret20",
            "market_breadth200", "adv20_prior", "high", "low", "volume_group"]
    rows = []
    for r in df.loc[mask, cols].itertuples(index=False):
        sdf = tables.get(r.symbol)
        if sdf is None:
            continue
        si = int(r.local_idx)
        ei = si + 1
        if ei + HOLD - 1 >= len(sdf):
            continue
        entry = float(sdf.at[ei, "open"])
        exit_px = float(sdf.at[ei + HOLD - 1, "close"])
        if not math.isfinite(entry) or entry <= 0 or not math.isfinite(exit_px):
            continue
        fwd = exit_px / entry - 1 - COST
        # A 1:N split shows up as a ~-90% "return"; exclude so factor stats
        # are not driven by unadjusted corporate actions.
        if fwd < -0.6:
            continue
        atr = float(r.atr14)
        close = float(r.close)
        rows.append({
            "symbol": r.symbol, "trade_date": pd.Timestamp(r.trade_date), "fwd": fwd,
            "relvol50": float(r.relvol50), "ret1": float(r.ret1),
            "close_location": float(r.close_location),
            "ext_ema20": close / float(r.ema20) - 1 if float(r.ema20) > 0 else np.nan,
            "atr_pct": atr / close if close > 0 else np.nan,
            "prox_52w": close / float(r.prior_high252) if float(r.prior_high252) > 0 else np.nan,
            "rs60_rank": float(r.rs60_rank) if pd.notna(r.rs60_rank) else np.nan,
            "prior_ret20": float(r.prior_ret20) if pd.notna(r.prior_ret20) else np.nan,
            "market_breadth200": float(r.market_breadth200) if pd.notna(r.market_breadth200) else np.nan,
            "log_adv20": math.log10(float(r.adv20_prior)) if float(r.adv20_prior) > 0 else np.nan,
            "range_vs_atr": (float(r.high) - float(r.low)) / atr if atr > 0 else np.nan,
            "volume_group": r.volume_group,
        })
    return pd.DataFrame(rows)


def period_of(d: pd.Timestamp) -> str:
    if d < IS_END:
        return "in_sample"
    return "holdout" if d >= OOS_START else "validation"


def spearman(a: pd.Series, b: pd.Series) -> float:
    ok = a.notna() & b.notna()
    if ok.sum() < 30:
        return np.nan
    return float(a[ok].rank().corr(b[ok].rank()))


def main() -> None:
    print("Loading daily cache")
    raw = append_recent_parquet_daily(load_daily_cache(DEFAULT_DAILY_CACHE), DEFAULT_PARQUET_DIR)
    df = add_features(raw, load_volume_groups(DEFAULT_VOLUME_GROUPS))
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    tables = build_symbol_tables(df)

    cfg = Config("tuned", close_loc_min=0.80, max_positions=10)
    sig = collect_signals(df, tables, cfg)
    sig["period"] = sig["trade_date"].apply(period_of)
    print(f"signals: {len(sig)}  mean fwd {sig['fwd'].mean()*100:.2f}%  "
          f"hit {(sig['fwd']>0).mean()*100:.1f}%")
    print(sig.groupby("period").agg(n=("fwd", "size"), mean_pct=("fwd", lambda s: round(s.mean()*100, 2)),
                                    hit=("fwd", lambda s: round((s > 0).mean()*100, 1))).to_string())
    sig.to_csv(DEFAULT_OUT_DIR / "dvb_signal_features.csv", index=False)

    ic_rows = []
    print("\n=== QUINTILE FORWARD RETURNS (%) and INFORMATION COEFFICIENT ===")
    for feat, meaning in FEATURES.items():
        sub = sig[sig[feat].notna()].copy()
        if len(sub) < 200:
            continue
        try:
            sub["q"] = pd.qcut(sub[feat], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        except ValueError:
            continue
        overall = sub.groupby("q", observed=True)["fwd"].mean() * 100
        ic_all = spearman(sub[feat], sub["fwd"])
        ic_is = spearman(sub[sub.period == "in_sample"][feat], sub[sub.period == "in_sample"]["fwd"])
        ic_oos = spearman(sub[sub.period == "holdout"][feat], sub[sub.period == "holdout"]["fwd"])
        q = [round(float(overall.get(i, np.nan)), 2) for i in [1, 2, 3, 4, 5]]
        # monotonic if consistently rising or falling across quintiles
        diffs = [q[i + 1] - q[i] for i in range(4) if not (math.isnan(q[i]) or math.isnan(q[i + 1]))]
        mono = "up" if all(d > 0 for d in diffs) else ("down" if all(d < 0 for d in diffs) else "-")
        stable = "yes" if (not math.isnan(ic_is) and not math.isnan(ic_oos)
                           and np.sign(ic_is) == np.sign(ic_oos) and abs(ic_is) > 0.03) else "no"
        ic_rows.append({"feature": feat, "meaning": meaning, "q1": q[0], "q2": q[1], "q3": q[2],
                        "q4": q[3], "q5": q[4], "spread_q5_q1": round(q[4] - q[0], 2),
                        "ic_all": round(ic_all, 3), "ic_is": round(ic_is, 3),
                        "ic_holdout": round(ic_oos, 3), "monotonic": mono, "stable_sign": stable})

    out = pd.DataFrame(ic_rows).sort_values("ic_all", key=abs, ascending=False)
    out.to_csv(DEFAULT_OUT_DIR / "dvb_entry_factors.csv", index=False)
    print(out.to_string(index=False))

    print("\n=== BY VOLUME GROUP ===")
    g = sig.groupby("volume_group").agg(n=("fwd", "size"),
                                        mean_pct=("fwd", lambda s: round(s.mean() * 100, 2)),
                                        hit=("fwd", lambda s: round((s > 0).mean() * 100, 1)))
    print(g[g["n"] >= 40].sort_values("mean_pct", ascending=False).to_string())
    print("\nWrote dvb_entry_factors.csv, dvb_signal_features.csv")


if __name__ == "__main__":
    main()
