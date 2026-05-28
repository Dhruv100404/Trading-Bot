from __future__ import annotations

import argparse
import math
import textwrap
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except Exception:  # pragma: no cover
    pq = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_OUT_DIR = ROOT / "docs" / "moving_average_strategy_lab"


@dataclass(frozen=True)
class MAStrategy:
    name: str
    family: str
    thesis: str
    signal: callable
    trail: str
    initial_stop: str = "signal_low"
    target: str = "none"
    max_hold: int = 60
    min_hold: int = 1
    max_per_day: int = 8
    direction: str = "long"


COST_SCENARIOS = {
    "optimistic": {"cost_bps_side": 4.0, "slippage_bps_side": 3.0},
    "base": {"cost_bps_side": 8.0, "slippage_bps_side": 5.0},
    "stress": {"cost_bps_side": 8.0, "slippage_bps_side": 15.0},
}


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def build_daily_cache(parquet_dir: Path, cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)
    if pq is None:
        raise RuntimeError("pyarrow is required to aggregate parquet candles locally.")

    frames: list[pd.DataFrame] = []
    columns = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume", "day_open", "gap_pct", "vwap"]
    for path in monthly_files(parquet_dir):
        print(f"Aggregating {path.name}")
        df = pq.read_table(path, columns=columns).to_pandas()
        df = df.dropna(subset=["date", "symbol", "open", "high", "low", "close"])
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
        df = df[(df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1))]
        df = df.sort_values(["symbol", "date", "bucket"])
        grouped = df.groupby(["symbol", "date"], sort=False)
        daily = grouped.agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            day_open=("day_open", "first"),
            gap_pct=("gap_pct", "first"),
            volume=("volume", "sum"),
            close_vwap=("vwap", "last"),
            buckets=("bucket", "nunique"),
        ).reset_index()
        daily = daily[daily["buckets"] >= 300]
        frames.append(daily)

    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = pd.to_datetime(out.pop("date"))
    out = out.sort_values(["symbol", "trade_date"]).drop_duplicates(["symbol", "trade_date"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache_path, index=False)
    return out


def add_ma_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["ret1"] = df["close"] / prev_close - 1
    df["atr14"] = tr.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    for n in [5, 10, 20, 50, 100, 200]:
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())
        df[f"sma{n}_slope5"] = df[f"sma{n}"] / g[f"sma{n}"].shift(5) - 1
    for n in [10, 20, 50, 200]:
        df[f"ema{n}"] = g["close"].transform(lambda s, n=n: s.ewm(span=n, adjust=False, min_periods=max(5, n // 2)).mean())
        df[f"ema{n}_slope5"] = df[f"ema{n}"] / g[f"ema{n}"].shift(5) - 1

    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)
    df["prior_high20"] = prior_high.groupby(df["symbol"]).rolling(20, min_periods=10).max().reset_index(level=0, drop=True)
    df["prior_high55"] = prior_high.groupby(df["symbol"]).rolling(55, min_periods=30).max().reset_index(level=0, drop=True)
    df["prior_low20"] = prior_low.groupby(df["symbol"]).rolling(20, min_periods=10).min().reset_index(level=0, drop=True)
    df["prior_high252"] = prior_high.groupby(df["symbol"]).rolling(252, min_periods=120).max().reset_index(level=0, drop=True)
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    df["adv20"] = g.apply(lambda x: (x["close"] * x["volume"]).rolling(20, min_periods=10).mean()).reset_index(level=0, drop=True)
    df["relvol"] = df["volume"] / df["vol20"].replace(0, np.nan)
    delta = g["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    avg_loss = loss.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))
    df["ret20"] = df["close"] / g["close"].shift(20) - 1
    df["ret60"] = df["close"] / g["close"].shift(60) - 1
    df["rs60_rank"] = df.groupby("trade_date")["ret60"].rank(pct=True)
    df["market_breadth200"] = (df["close"] > df["sma200"]).groupby(df["trade_date"]).transform("mean")
    df["close_location"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["dist_sma20_atr"] = (df["close"] - df["sma20"]) / df["atr14"].replace(0, np.nan)
    df["dist_sma200_pct"] = df["close"] / df["sma200"].replace(0, np.nan) - 1
    df["touch_sma10"] = (df["low"] <= df["sma10"] * 1.01) & (df["close"] >= df["sma10"])
    df["touch_sma20"] = (df["low"] <= df["sma20"] * 1.015) & (df["close"] >= df["sma20"])
    df["prev_close"] = prev_close
    df["prev_sma20"] = g["sma20"].shift(1)
    df["prev_sma200"] = g["sma200"].shift(1)
    df["reclaim_sma20"] = (df["close"] > df["sma20"]) & (df["prev_close"] <= df["prev_sma20"])
    df["reclaim_sma200"] = (df["close"] > df["sma200"]) & (df["prev_close"] <= df["prev_sma200"])
    df["next_open"] = g["open"].shift(-1)
    df["liquid"] = (df["close"] >= 50) & (df["vol20"] >= 100000) & (df["adv20"] >= 20_000_000)
    df["year"] = df["trade_date"].dt.year
    return df


def market_ok(d: pd.DataFrame) -> pd.Series:
    return d["market_breadth200"].fillna(0) >= 0.38


def make_strategies() -> list[MAStrategy]:
    return [
        MAStrategy(
            "kk_breakout_sma10_trail",
            "Qualamagi breakout",
            "10SMA above rising 20SMA, liquid RS leader breaks prior 20D high on volume; exit on first close below 10SMA.",
            lambda d: d.liquid & market_ok(d) & (d.sma10 > d.sma20) & (d.sma10_slope5 > 0) & (d.sma20_slope5 > 0)
            & (d.close > d.prior_high20) & (d.relvol > 1.4) & (d.rs60_rank > 0.65),
            trail="sma10",
            max_hold=60,
            max_per_day=6,
        ),
        MAStrategy(
            "kk_breakout_sma20_trail",
            "Qualamagi breakout",
            "Slower version for steadier leaders: same breakout, but trail on first close below 20SMA.",
            lambda d: d.liquid & market_ok(d) & (d.sma10 > d.sma20) & (d.sma20 > d.sma50) & (d.sma20_slope5 > 0)
            & (d.close > d.prior_high20) & (d.relvol > 1.2) & (d.rs60_rank > 0.60),
            trail="sma20",
            max_hold=90,
            max_per_day=6,
        ),
        MAStrategy(
            "kk_stage2_55d_breakout_20trail",
            "Qualamagi breakout",
            "Stage-2 style close above prior 55D high while above rising 10/20/50 SMAs; trail 20SMA.",
            lambda d: d.liquid & market_ok(d) & (d.close > d.prior_high55) & (d.sma10 > d.sma20) & (d.sma20 > d.sma50)
            & (d.sma50_slope5 > 0) & (d.relvol > 1.1) & (d.rs60_rank > 0.70),
            trail="sma20",
            max_hold=100,
            max_per_day=5,
        ),
        MAStrategy(
            "kk_fast_ema10_20_breakout",
            "Fast EMA breakout",
            "Fast-moving version using EMA10/EMA20 alignment and a prior-high breakout; trail EMA10.",
            lambda d: d.liquid & market_ok(d) & (d.ema10 > d.ema20) & (d.ema10_slope5 > 0) & (d.close > d.prior_high20)
            & (d.relvol > 1.5) & (d.close_location > 0.60),
            trail="ema10",
            max_hold=45,
            max_per_day=7,
        ),
        MAStrategy(
            "ma_surf_10_20_continuation",
            "MA surf pullback",
            "Leader remains above rising 10/20SMAs, tags one of them intraday, then closes strong; trail 20SMA.",
            lambda d: d.liquid & market_ok(d) & (d.sma10 > d.sma20) & (d.sma20 > d.sma50) & (d.sma20_slope5 > 0)
            & (d.touch_sma10 | d.touch_sma20) & (d.close_location > 0.55) & (d.rs60_rank > 0.65),
            trail="sma20",
            max_hold=45,
            max_per_day=6,
        ),
        MAStrategy(
            "ma_surf_10_tight_exit",
            "MA surf pullback",
            "Same surf entry, but exits on close below 10SMA for more aggressive profit protection.",
            lambda d: d.liquid & market_ok(d) & (d.sma10 > d.sma20) & (d.sma20 > d.sma50) & (d.sma20_slope5 > 0)
            & d.touch_sma10 & (d.close_location > 0.60) & (d.rs60_rank > 0.70),
            trail="sma10",
            max_hold=35,
            max_per_day=6,
        ),
        MAStrategy(
            "lance_sma20_reclaim_reversal",
            "SMA20 reclaim",
            "Price was below SMA20, reclaims and closes above it with a strong candle; exit close below SMA20.",
            lambda d: d.liquid & market_ok(d) & (d.close > d.sma200) & d.reclaim_sma20 & (d.close > d.open)
            & (d.close_location > 0.65) & (d.relvol > 0.9),
            trail="sma20",
            max_hold=30,
            max_per_day=8,
        ),
        MAStrategy(
            "sma200_bounce_leader",
            "200SMA bounce",
            "Long-term leader pulls into the 200SMA and closes green; exit below 200SMA or after trend resumes.",
            lambda d: d.liquid & (d.rs60_rank > 0.55) & (d.low <= d.sma200 * 1.025) & (d.close > d.sma200)
            & (d.close > d.open) & (d.close_location > 0.55),
            trail="sma200",
            target="atr4",
            max_hold=35,
            max_per_day=5,
        ),
        MAStrategy(
            "sma200_reclaim_breakout",
            "200SMA reclaim",
            "Stock reclaims the 200SMA and clears prior 20D resistance with volume; trail SMA20.",
            lambda d: d.liquid & market_ok(d) & d.reclaim_sma200 & (d.close > d.prior_high20) & (d.relvol > 1.15)
            & (d.close_location > 0.60),
            trail="sma20",
            max_hold=60,
            max_per_day=5,
        ),
        MAStrategy(
            "ma_stretch_to_sma20_target",
            "Mean reversion to MA",
            "Uptrend stock closes more than 2 ATR below SMA20; target is a snapback to SMA20.",
            lambda d: d.liquid & (d.close > d.sma200) & (d.sma200_slope5 > -0.002) & (d.dist_sma20_atr < -2.0)
            & (d.rsi14 < 38) & (d.close_location > 0.35),
            trail="none",
            target="sma20",
            max_hold=12,
            max_per_day=10,
        ),
    ]


def trail_value(row: pd.Series, trail: str) -> float | None:
    if trail == "none":
        return None
    value = row.get(trail)
    if value is None or pd.isna(value):
        return None
    return float(value)


def target_value(entry: float, signal: pd.Series, row: pd.Series, strategy: MAStrategy) -> float | None:
    if strategy.target == "none":
        return None
    if strategy.target == "sma20":
        value = row.get("sma20")
        return float(value) if value is not None and not pd.isna(value) and value > entry else None
    if strategy.target == "atr4":
        atr = float(signal["atr14"])
        return entry + 4 * atr if atr > 0 else None
    return None


def stop_value(entry: float, signal: pd.Series, strategy: MAStrategy) -> float:
    if strategy.initial_stop == "signal_low":
        return min(float(signal["low"]), entry - 0.75 * float(signal["atr14"]))
    return entry - float(signal["atr14"])


def score_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    c = candidates.copy()
    c["rank_score"] = (
        c["score_relvol"].fillna(0) * 2.0
        + c["rs60_rank"].fillna(0) * 4.0
        + c["close_location"].fillna(0)
        - c["dist_sma20_atr"].fillna(0).clip(lower=0) * 0.2
    )
    return c.sort_values(["trade_date", "rank_score"], ascending=[True, False])


def backtest_strategy(df: pd.DataFrame, strategy: MAStrategy, cost_bps_side: float, slippage_bps_side: float) -> pd.DataFrame:
    d = df.copy()
    signal_mask = strategy.signal(d).fillna(False)
    candidates = d.loc[signal_mask & d["next_open"].notna() & d["atr14"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates["score_relvol"] = candidates["relvol"]
    candidates = score_candidates(candidates).groupby("trade_date", group_keys=False).head(strategy.max_per_day)
    by_symbol = {symbol: sdf.reset_index(drop=True) for symbol, sdf in d.groupby("symbol", sort=False)}
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    trades: list[dict] = []

    for signal in candidates.to_dict("records"):
        sdf = by_symbol[signal["symbol"]]
        idx = np.flatnonzero(sdf["trade_date"].values == np.datetime64(signal["trade_date"]))
        if len(idx) == 0:
            continue
        signal_idx = int(idx[0])
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue
        entry = float(sdf.at[entry_idx, "open"])
        if entry <= 0:
            continue
        stop = stop_value(entry, pd.Series(signal), strategy)
        exit_price = None
        exit_date = None
        exit_reason = "time"
        hold = 0

        for j in range(entry_idx, min(entry_idx + strategy.max_hold, len(sdf))):
            hold = j - entry_idx + 1
            bar = sdf.iloc[j]
            exit_date = bar["trade_date"]
            low = float(bar["low"])
            high = float(bar["high"])
            close = float(bar["close"])
            target = target_value(entry, pd.Series(signal), bar, strategy)
            trail = trail_value(bar, strategy.trail)

            if low <= stop:
                exit_price = stop
                exit_reason = "initial_stop"
                break
            if target is not None and high >= target:
                exit_price = target
                exit_reason = strategy.target
                break
            if hold >= strategy.min_hold and trail is not None and close < trail:
                exit_price = close
                exit_reason = f"close_below_{strategy.trail}"
                break
            exit_price = close

        if exit_price is None or exit_date is None:
            continue
        gross_return = exit_price / entry - 1
        net_return = gross_return - round_cost
        trades.append({
            "strategy": strategy.name,
            "family": strategy.family,
            "symbol": signal["symbol"],
            "signal_date": signal["trade_date"],
            "entry_date": sdf.at[entry_idx, "trade_date"],
            "exit_date": exit_date,
            "entry": entry,
            "exit": exit_price,
            "stop": stop,
            "exit_reason": exit_reason,
            "hold_days": hold,
            "gross_return": gross_return,
            "net_return": net_return,
            "score": signal.get("rank_score", 0),
            "relvol": signal.get("relvol", np.nan),
            "rs60_rank": signal.get("rs60_rank", np.nan),
            "year": pd.Timestamp(sdf.at[entry_idx, "trade_date"]).year,
        })
    return pd.DataFrame(trades)


def metrics_for_trades(trades: pd.DataFrame, name: str) -> dict:
    if trades.empty:
        return {"strategy": name, "trades": 0}
    r = trades["net_return"]
    wins = r > 0
    gross_profit = r[r > 0].sum()
    gross_loss = -r[r <= 0].sum()
    pf = gross_profit / gross_loss if gross_loss > 0 else math.inf
    eq = (1 + r / 10).cumprod()
    dd = eq / eq.cummax() - 1
    downside = r[r < 0].std(ddof=0)
    span_days = max((pd.to_datetime(trades["entry_date"]).max() - pd.to_datetime(trades["entry_date"]).min()).days, 1)
    return {
        "strategy": name,
        "family": trades["family"].iloc[0],
        "trades": int(len(trades)),
        "trades_per_month": round(len(trades) / (span_days / 30.4375), 2),
        "win_rate": round(float(wins.mean() * 100), 2),
        "profit_factor": round(float(pf), 3) if math.isfinite(pf) else 99.0,
        "expectancy_pct": round(float(r.mean() * 100), 3),
        "median_return_pct": round(float(r.median() * 100), 3),
        "avg_win_pct": round(float(r[wins].mean() * 100), 3) if wins.any() else 0,
        "avg_loss_pct": round(float(r[~wins].mean() * 100), 3) if (~wins).any() else 0,
        "total_return_proxy_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "max_drawdown_proxy_pct": round(float(dd.min() * 100), 2),
        "sharpe_trade": round(float(r.mean() / r.std(ddof=0) * math.sqrt(252)), 3) if r.std(ddof=0) > 0 else 0,
        "sortino_trade": round(float(r.mean() / downside * math.sqrt(252)), 3) if downside and downside > 0 else 0,
        "avg_hold_days": round(float(trades["hold_days"].mean()), 2),
    }


def chronological_cutoffs(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    span = end - start
    return start + span * 0.60, start + span * 0.80


def split_metrics(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    cut1, cut2 = chronological_cutoffs(start, end)
    for strategy, part in trades.groupby("strategy"):
        for label, lo, hi, mask in [
            ("in_sample", start, cut1, part["entry_date"] < cut1),
            ("validation", cut1, cut2, (part["entry_date"] >= cut1) & (part["entry_date"] < cut2)),
            ("out_of_sample", cut2, end, part["entry_date"] >= cut2),
        ]:
            m = metrics_for_trades(part.loc[mask], strategy)
            m["split"] = label
            m["range_start"] = str(pd.Timestamp(lo).date())
            m["range_end"] = str(pd.Timestamp(hi).date())
            rows.append(m)
    return pd.DataFrame(rows)


def yearly_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (strategy, year), part in trades.groupby(["strategy", "year"]):
        m = metrics_for_trades(part, strategy)
        m["year"] = int(year)
        rows.append(m)
    return pd.DataFrame(rows)


def exit_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["strategy", "exit_reason"])
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
            expectancy_pct=("net_return", lambda s: round(float(s.mean() * 100), 3)),
            avg_hold_days=("hold_days", "mean"),
        )
        .reset_index()
    )


def symbol_contribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["strategy", "symbol"])
        .agg(
            trades=("net_return", "size"),
            net_return_sum=("net_return", "sum"),
            avg_net_return=("net_return", "mean"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
        )
        .reset_index()
        .sort_values(["strategy", "net_return_sum"], ascending=[True, False])
    )


def robustness_score(metrics: pd.DataFrame, splits: pd.DataFrame, yearly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, m in metrics.iterrows():
        s = splits[splits["strategy"] == m["strategy"]]
        y = yearly[yearly["strategy"] == m["strategy"]]
        oos = s[s["split"] == "out_of_sample"]
        val = s[s["split"] == "validation"]
        positive_years = float((y["expectancy_pct"] > 0).mean() * 100) if not y.empty else 0
        oos_exp = float(oos["expectancy_pct"].iloc[0]) if not oos.empty else 0
        val_exp = float(val["expectancy_pct"].iloc[0]) if not val.empty else 0
        score = (
            min(float(m["profit_factor"]), 3.0) * 18
            + max(float(m["expectancy_pct"]), -2.0) * 8
            + positive_years * 0.25
            + max(oos_exp, -2.0) * 8
            + max(val_exp, -2.0) * 5
            - abs(min(float(m["max_drawdown_proxy_pct"]), 0)) * 0.6
        )
        rows.append({
            "strategy": m["strategy"],
            "family": m["family"],
            "robustness_score": round(score, 2),
            "positive_years_pct": round(positive_years, 2),
            "validation_expectancy_pct": round(val_exp, 3),
            "oos_expectancy_pct": round(oos_exp, 3),
            "verdict": "promote_to_paper" if score >= 65 and oos_exp > 0 and val_exp > 0 and m["trades"] >= 80 else "watch" if score >= 45 else "reject",
        })
    return pd.DataFrame(rows).sort_values("robustness_score", ascending=False)


def save_charts(out_dir: Path, trade_log: pd.DataFrame, metrics: pd.DataFrame, yearly: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    if not metrics.empty:
        m = metrics.sort_values("expectancy_pct", ascending=True)
        plt.figure(figsize=(11, 6))
        plt.barh(m["strategy"], m["expectancy_pct"], color=np.where(m["expectancy_pct"] > 0, "#00a86b", "#d9534f"))
        plt.axvline(0, color="#222", linewidth=0.8)
        plt.title("Moving Average Strategy Expectancy After Costs")
        plt.xlabel("Expectancy per trade (%)")
        plt.tight_layout()
        plt.savefig(chart_dir / "expectancy_by_strategy.png", dpi=160)
        plt.close()

        plt.figure(figsize=(9, 6))
        plt.scatter(metrics["trades"], metrics["profit_factor"], s=np.maximum(metrics["win_rate"], 10) * 3, alpha=0.72)
        for _, row in metrics.iterrows():
            plt.annotate(row["strategy"].replace("_", "\n"), (row["trades"], row["profit_factor"]), fontsize=6, alpha=0.8)
        plt.axhline(1, color="#222", linewidth=0.8)
        plt.title("Profit Factor vs Sample Size")
        plt.xlabel("Trades")
        plt.ylabel("Profit factor")
        plt.tight_layout()
        plt.savefig(chart_dir / "pf_vs_sample_size.png", dpi=160)
        plt.close()

    if not trade_log.empty and not metrics.empty:
        top = metrics.sort_values("expectancy_pct", ascending=False).head(5)["strategy"].tolist()
        plt.figure(figsize=(12, 6))
        for strategy in top:
            t = trade_log[trade_log["strategy"] == strategy].sort_values("entry_date")
            eq = (1 + t["net_return"] / 10).cumprod()
            plt.plot(pd.to_datetime(t["entry_date"]), eq, label=strategy)
        plt.title("Top MA Variants - Proxy Equity Curves")
        plt.ylabel("Growth of 1.0, 10 equal slots")
        plt.legend(fontsize=7)
        plt.tight_layout()
        plt.savefig(chart_dir / "top_equity_curves.png", dpi=160)
        plt.close()

    if not yearly.empty:
        piv = yearly.pivot_table(index="strategy", columns="year", values="expectancy_pct", aggfunc="first").fillna(0)
        plt.figure(figsize=(12, 6))
        plt.imshow(piv.values, aspect="auto", cmap="RdYlGn", vmin=-2, vmax=2)
        plt.colorbar(label="Expectancy %")
        plt.xticks(range(len(piv.columns)), piv.columns, rotation=45)
        plt.yticks(range(len(piv.index)), piv.index, fontsize=7)
        plt.title("Year-by-Year Expectancy Heatmap")
        plt.tight_layout()
        plt.savefig(chart_dir / "yearly_expectancy_heatmap.png", dpi=160)
        plt.close()


def write_report(out_dir: Path, metrics: pd.DataFrame, robustness: pd.DataFrame, splits: pd.DataFrame, exits: pd.DataFrame, symbols: pd.DataFrame, strategies: list[MAStrategy]) -> None:
    top_metrics = metrics.merge(robustness, on=["strategy", "family"], how="left").sort_values("robustness_score", ascending=False)
    promoted = top_metrics[top_metrics["verdict"] == "promote_to_paper"]
    watch = top_metrics[top_metrics["verdict"] == "watch"]
    strategy_notes = "\n".join(f"- `{s.name}` ({s.family}): {s.thesis}" for s in strategies)
    top_table = top_metrics[[
        "strategy", "family", "trades", "win_rate", "profit_factor", "expectancy_pct",
        "max_drawdown_proxy_pct", "positive_years_pct", "validation_expectancy_pct", "oos_expectancy_pct",
        "robustness_score", "verdict",
    ]].to_markdown(index=False)
    split_table = splits.sort_values(["strategy", "split"])[[
        "strategy", "split", "trades", "win_rate", "profit_factor", "expectancy_pct", "range_start", "range_end",
    ]].to_markdown(index=False)
    exit_table = exits.sort_values(["strategy", "trades"], ascending=[True, False]).head(40).to_markdown(index=False)
    symbol_table = symbols.groupby("strategy").head(5).to_markdown(index=False) if not symbols.empty else "No symbol contribution rows."

    report = f"""# Moving Average Strategy Lab

Generated from local parquet daily bars. This test converts the moving-average concepts from Christian Qullamaggie / Lance Breitstein style discussion into explicit daily-bar rules.

## Pro-Trader Read

- Best candidates to paper trade: {', '.join(promoted['strategy'].tolist()) if not promoted.empty else 'none passed strict promotion gates'}.
- Watchlist candidates: {', '.join(watch['strategy'].tolist()) if not watch.empty else 'none'}.
- A moving-average idea is only interesting here if it survives costs, has enough trades, does not rely on one year, and stays positive in validation and out-of-sample windows.
- Daily bars cannot fully model opening-range breakout fills, partial exits after 3-5 days, intraday MA reclaims, or real slippage in fast leaders. Treat the best rows as paper-desk candidates, not live approval.

## Strategy Variants Tested

{strategy_notes}

## Ranked Results

{top_table}

## Chronological Robustness

{split_table}

## Exit Behavior

{exit_table}

## Top Symbol Contribution

{symbol_table}

## Visual Outputs

- `charts/expectancy_by_strategy.png`
- `charts/pf_vs_sample_size.png`
- `charts/top_equity_curves.png`
- `charts/yearly_expectancy_heatmap.png`

## What I Would Analyze Next

1. Add true opening-range entries for breakout variants instead of next-session open proxies.
2. Model Qullamaggie partial exits: sell 1/3 to 1/2 after day 3-5, move stop to breakeven, then trail 10/20SMA.
3. Split by market regime: 10SMA above 20SMA for benchmark/index, breadth above/below 50%, and high/low volatility windows.
4. Add short-side Lance variants: downtrend MA resistance, breakdown through SMA20, and mean reversion target to SMA20.
5. Wire the top one or two variants into Scanner/Paper Desk as `Candidate`, not as live-trading approval.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    daily = build_daily_cache(args.parquet_dir, out_dir / "daily_bars_cache.parquet", refresh=args.refresh_cache)
    daily = add_ma_features(daily)
    start = pd.Timestamp(daily["trade_date"].min())
    end = pd.Timestamp(daily["trade_date"].max())
    strategies = make_strategies()

    all_trades = []
    cost_frames = []
    for strategy in strategies:
        print(f"Backtesting {strategy.name}")
        for scenario, costs in COST_SCENARIOS.items():
            trades = backtest_strategy(daily, strategy, **costs)
            if trades.empty:
                continue
            trades["cost_scenario"] = scenario
            cost_frames.append(trades)
            if scenario == "base":
                all_trades.append(trades)

    trade_log = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    cost_trade_log = pd.concat(cost_frames, ignore_index=True) if cost_frames else pd.DataFrame()
    metrics = pd.DataFrame([metrics_for_trades(part, strategy) for strategy, part in trade_log.groupby("strategy")])
    splits = split_metrics(trade_log, start, end) if not trade_log.empty else pd.DataFrame()
    yearly = yearly_metrics(trade_log) if not trade_log.empty else pd.DataFrame()
    exits = exit_metrics(trade_log)
    symbols = symbol_contribution(trade_log)
    robust = robustness_score(metrics, splits, yearly) if not metrics.empty else pd.DataFrame()
    cost_metrics = pd.DataFrame([
        {**metrics_for_trades(part, strategy), "cost_scenario": scenario}
        for (strategy, scenario), part in cost_trade_log.groupby(["strategy", "cost_scenario"])
    ]) if not cost_trade_log.empty else pd.DataFrame()

    trade_log.to_csv(out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    cost_metrics.to_csv(out_dir / "cost_scenario_metrics.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    exits.to_csv(out_dir / "exit_metrics.csv", index=False)
    symbols.to_csv(out_dir / "symbol_contribution.csv", index=False)
    robust.to_csv(out_dir / "robustness_scorecard.csv", index=False)
    save_charts(out_dir, trade_log, metrics, yearly)
    write_report(out_dir, metrics, robust, splits, exits, symbols, strategies)
    print(f"Wrote {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
