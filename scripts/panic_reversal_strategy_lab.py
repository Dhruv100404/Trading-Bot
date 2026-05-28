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
DEFAULT_OUT_DIR = ROOT / "docs" / "panic_reversal_strategy_lab"
MA_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"


@dataclass(frozen=True)
class PanicStrategy:
    name: str
    family: str
    thesis: str
    signal: callable
    exit_style: str
    stop_style: str = "signal_low"
    max_hold: int = 12
    max_per_day: int = 10
    min_hold: int = 1


COST_SCENARIOS = {
    "optimistic": {"cost_bps_side": 4.0, "slippage_bps_side": 4.0},
    "base": {"cost_bps_side": 8.0, "slippage_bps_side": 8.0},
    "stress": {"cost_bps_side": 10.0, "slippage_bps_side": 20.0},
}


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def build_daily_cache(parquet_dir: Path, cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)
    if MA_CACHE.exists() and not refresh:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        daily = pd.read_parquet(MA_CACHE)
        daily.to_parquet(cache_path, index=False)
        return daily
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


def add_panic_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["prev_close"] = prev_close
    df["ret1"] = df["close"] / prev_close - 1
    for n in [2, 3, 5, 10, 20, 60]:
        df[f"ret{n}"] = df["close"] / g["close"].shift(n) - 1
    df["atr14"] = tr.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    df["atr_pct"] = df["atr14"] / df["close"].replace(0, np.nan)
    for n in [5, 10, 20, 50, 200]:
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())
        df[f"sma{n}_slope5"] = df[f"sma{n}"] / g[f"sma{n}"].shift(5) - 1
    prior_low = g["low"].shift(1)
    prior_high = g["high"].shift(1)
    for n in [3, 5, 10, 20]:
        df[f"prior_low{n}"] = prior_low.groupby(df["symbol"]).rolling(n, min_periods=max(2, n // 2)).min().reset_index(level=0, drop=True)
        df[f"prior_high{n}"] = prior_high.groupby(df["symbol"]).rolling(n, min_periods=max(2, n // 2)).max().reset_index(level=0, drop=True)
    df["pre_panic_close3"] = g["close"].shift(3)
    df["pre_panic_close5"] = g["close"].shift(5)
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    df["adv20"] = g.apply(lambda x: (x["close"] * x["volume"]).rolling(20, min_periods=10).mean()).reset_index(level=0, drop=True)
    df["relvol"] = df["volume"] / df["vol20"].replace(0, np.nan)
    df["gap_open_pct"] = df["open"] / prev_close - 1
    df["range_atr"] = (df["high"] - df["low"]) / df["atr14"].replace(0, np.nan)
    df["close_location"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["recovery_from_low_pct"] = df["close"] / df["low"].replace(0, np.nan) - 1
    df["open_to_low_pct"] = df["low"] / df["open"].replace(0, np.nan) - 1
    df["close_above_open"] = df["close"] > df["open"]
    df["failed_20d_breakdown"] = (df["low"] < df["prior_low20"]) & (df["close"] > df["prior_low20"])
    df["failed_10d_breakdown"] = (df["low"] < df["prior_low10"]) & (df["close"] > df["prior_low10"])
    df["above_sma200"] = df["close"] > df["sma200"]
    df["distance_sma20_atr"] = (df["close"] - df["sma20"]) / df["atr14"].replace(0, np.nan)
    df["distance_sma200_pct"] = df["close"] / df["sma200"].replace(0, np.nan) - 1
    df["next_open"] = g["open"].shift(-1)
    df["next_close"] = g["close"].shift(-1)
    df["liquid"] = (df["close"] >= 50) & (df["vol20"] >= 100000) & (df["adv20"] >= 20_000_000)
    df["mega_liquid"] = (df["close"] >= 100) & (df["vol20"] >= 400000) & (df["adv20"] >= 100_000_000)
    df["year"] = df["trade_date"].dt.year

    by_date = df.groupby("trade_date")
    df["market_avg_ret1"] = by_date["ret1"].transform("mean")
    df["market_avg_ret3"] = by_date["ret3"].transform("mean")
    df["market_down_3pct"] = (df["ret1"] <= -0.03).groupby(df["trade_date"]).transform("mean")
    df["market_down_5pct_3d"] = (df["ret3"] <= -0.05).groupby(df["trade_date"]).transform("mean")
    df["market_breadth200"] = df["above_sma200"].groupby(df["trade_date"]).transform("mean")
    df["rs60_rank"] = df.groupby("trade_date")["ret60"].rank(pct=True)
    return df


def broad_panic(d: pd.DataFrame) -> pd.Series:
    return (d["market_avg_ret1"] <= -0.012) | (d["market_down_3pct"] >= 0.28) | (d["market_down_5pct_3d"] >= 0.40)


def make_strategies() -> list[PanicStrategy]:
    return [
        PanicStrategy(
            "lance_3day_capitulation_reclaim",
            "Capitulation reversal",
            "Liquid stock is down hard over three days, prints a large ATR range, reclaims from the low, and closes in the upper half.",
            lambda d: d.liquid & (d.ret3 <= -0.10) & (d.range_atr >= 1.6) & (d.close_location >= 0.55)
            & (d.recovery_from_low_pct >= 0.025) & (d.relvol >= 1.15),
            exit_style="prior_low_trail",
            max_hold=15,
            max_per_day=10,
        ),
        PanicStrategy(
            "historic_panic_basket_reversal",
            "Broad panic basket",
            "Only trades during broad market panic; buys the most liquid symbols hit hard but closing off lows.",
            lambda d: d.mega_liquid & broad_panic(d) & (d.ret3 <= -0.075) & (d.range_atr >= 1.35)
            & (d.close_location >= 0.45) & (d.recovery_from_low_pct >= 0.018),
            exit_style="pre_panic_3d_or_prior_low",
            max_hold=10,
            max_per_day=15,
        ),
        PanicStrategy(
            "failed_breakdown_20d_panic",
            "Failed breakdown",
            "Undercuts the prior 20-day low during a selloff, then closes back above that level with volume.",
            lambda d: d.liquid & (d.ret5 <= -0.09) & d.failed_20d_breakdown & (d.close_location >= 0.50)
            & (d.relvol >= 1.20),
            exit_style="sma10_or_prior_low",
            max_hold=14,
            max_per_day=8,
        ),
        PanicStrategy(
            "gap_down_reclaim_panic",
            "Gap panic reclaim",
            "Big gap down after a multi-day drop, then closes green/off lows; this approximates forced open liquidation.",
            lambda d: d.liquid & (d.ret3 <= -0.08) & (d.gap_open_pct <= -0.035) & (d.close_location >= 0.58)
            & (d.close_above_open | (d.recovery_from_low_pct >= 0.035)),
            exit_style="pre_panic_3d_or_sma10",
            max_hold=12,
            max_per_day=8,
        ),
        PanicStrategy(
            "washout_close_strong_next_open",
            "Washout momentum",
            "Largest down days that reject the lows and close very strong; faster exit into the first snapback.",
            lambda d: d.liquid & (d.ret1 <= -0.045) & (d.range_atr >= 1.7) & (d.close_location >= 0.72)
            & (d.recovery_from_low_pct >= 0.03) & (d.relvol >= 1.25),
            exit_style="two_atr_or_prior_low",
            max_hold=8,
            max_per_day=10,
        ),
        PanicStrategy(
            "deep_stretch_to_sma20_mean_revert",
            "MA snapback",
            "Stock is deeply stretched below the 20SMA after a panic; target is a reflex move back toward equilibrium.",
            lambda d: d.liquid & (d.ret5 <= -0.12) & (d.distance_sma20_atr <= -2.2) & (d.close_location >= 0.40)
            & (d.range_atr >= 1.25),
            exit_style="sma20_target_or_time",
            max_hold=12,
            max_per_day=10,
        ),
        PanicStrategy(
            "sma200_panic_undercut_reclaim",
            "Long-term level reclaim",
            "Panic undercuts or nears the 200SMA and closes back above it; tries to catch institutional support.",
            lambda d: d.liquid & (d.ret5 <= -0.10) & (d.low <= d.sma200 * 1.015) & (d.close > d.sma200)
            & (d.close_location >= 0.55) & (d.recovery_from_low_pct >= 0.025),
            exit_style="sma20_target_or_prior_low",
            max_hold=18,
            max_per_day=7,
        ),
        PanicStrategy(
            "late_confirmation_higher_close",
            "Confirmation entry",
            "Waits one day after the panic for a higher close, reducing knife-catching but entering later.",
            lambda d: d.liquid & (d.ret3.shift(1) <= -0.10) & (d.close.shift(1).notna())
            & (d.close > d.close.shift(1)) & (d.low > d.low.shift(1)) & broad_panic(d).shift(1).fillna(False),
            exit_style="prior_low_trail",
            max_hold=14,
            max_per_day=10,
        ),
        PanicStrategy(
            "relative_strength_panic_survivor",
            "Panic survivor",
            "Broad market panic but symbol loses less than the tape and reclaims intraday lows; buys relative strength.",
            lambda d: d.mega_liquid & broad_panic(d) & (d.ret1 >= d.market_avg_ret1) & (d.ret1 <= -0.015)
            & (d.close_location >= 0.62) & (d.rs60_rank >= 0.55),
            exit_style="sma10_or_prior_low",
            max_hold=10,
            max_per_day=12,
        ),
        PanicStrategy(
            "knife_catch_no_confirmation_control",
            "Control group",
            "Naive version: buys deep multi-day selloffs without requiring reclaim/close strength. Included as a warning baseline.",
            lambda d: d.liquid & (d.ret3 <= -0.12) & (d.range_atr >= 1.2),
            exit_style="prior_low_trail",
            max_hold=12,
            max_per_day=10,
        ),
    ]


def score_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    c = candidates.copy()
    c["panic_depth"] = (-c["ret3"].fillna(0)).clip(lower=0)
    c["rank_score"] = (
        c["panic_depth"] * 18
        + c["recovery_from_low_pct"].fillna(0) * 22
        + c["close_location"].fillna(0) * 2.5
        + c["relvol"].fillna(0).clip(upper=5) * 0.7
        + c["range_atr"].fillna(0).clip(upper=5) * 0.5
        + c["adv20"].rank(pct=True).fillna(0)
    )
    return c.sort_values(["trade_date", "rank_score"], ascending=[True, False])


def stop_value(entry: float, signal: pd.Series, strategy: PanicStrategy) -> float:
    atr = float(signal["atr14"])
    low = float(signal["low"])
    if strategy.stop_style == "tight_atr":
        return min(low, entry - 0.75 * atr)
    if strategy.stop_style == "wide_atr":
        return min(low, entry - 1.5 * atr)
    return min(low, entry - 0.5 * atr)


def exit_target(entry: float, signal: pd.Series, bar: pd.Series, strategy: PanicStrategy) -> tuple[float | None, str | None]:
    atr = float(signal["atr14"])
    if strategy.exit_style in {"pre_panic_3d_or_prior_low", "pre_panic_3d_or_sma10"}:
        target = float(signal["pre_panic_close3"])
        if math.isfinite(target) and target > entry:
            return target, "pre_panic_close3"
    if strategy.exit_style in {"sma20_target_or_time", "sma20_target_or_prior_low"}:
        target = float(bar["sma20"])
        if math.isfinite(target) and target > entry:
            return target, "sma20_target"
    if strategy.exit_style == "two_atr_or_prior_low":
        return entry + 2 * atr, "two_atr_target"
    return None, None


def should_exit_on_trail(bar: pd.Series, prev_bar: pd.Series | None, strategy: PanicStrategy) -> tuple[bool, float, str]:
    close = float(bar["close"])
    low = float(bar["low"])
    if prev_bar is not None and strategy.exit_style in {
        "prior_low_trail",
        "pre_panic_3d_or_prior_low",
        "two_atr_or_prior_low",
        "sma20_target_or_prior_low",
    }:
        trail = float(prev_bar["low"])
        if low <= trail:
            return True, trail, "prior_daily_low_trail"
    if strategy.exit_style in {"sma10_or_prior_low", "pre_panic_3d_or_sma10"}:
        sma10 = float(bar["sma10"])
        if math.isfinite(sma10) and close < sma10:
            return True, close, "close_below_sma10"
    return False, close, "time"


def backtest_strategy(df: pd.DataFrame, strategy: PanicStrategy, cost_bps_side: float, slippage_bps_side: float) -> pd.DataFrame:
    d = df.copy()
    signal_mask = strategy.signal(d).fillna(False)
    candidates = d.loc[signal_mask & d["next_open"].notna() & d["atr14"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()
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
        signal_series = pd.Series(signal)
        stop = stop_value(entry, signal_series, strategy)
        exit_price = None
        exit_date = None
        exit_reason = "time"
        hold = 0

        for j in range(entry_idx, min(entry_idx + strategy.max_hold, len(sdf))):
            hold = j - entry_idx + 1
            bar = sdf.iloc[j]
            prev_bar = sdf.iloc[j - 1] if j > entry_idx else None
            exit_date = bar["trade_date"]
            low = float(bar["low"])
            close = float(bar["close"])
            high = float(bar["high"])

            if low <= stop:
                exit_price = stop
                exit_reason = "panic_low_stop"
                break

            target, target_reason = exit_target(entry, signal_series, bar, strategy)
            if target is not None and high >= target:
                exit_price = target
                exit_reason = target_reason or "target"
                break

            if hold >= strategy.min_hold:
                trail_exit, trail_price, trail_reason = should_exit_on_trail(bar, prev_bar, strategy)
                if trail_exit:
                    exit_price = trail_price
                    exit_reason = trail_reason
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
            "rank_score": signal.get("rank_score", 0),
            "ret3_signal": signal.get("ret3", np.nan),
            "ret5_signal": signal.get("ret5", np.nan),
            "gap_open_pct": signal.get("gap_open_pct", np.nan),
            "range_atr": signal.get("range_atr", np.nan),
            "close_location": signal.get("close_location", np.nan),
            "recovery_from_low_pct": signal.get("recovery_from_low_pct", np.nan),
            "relvol": signal.get("relvol", np.nan),
            "market_avg_ret1": signal.get("market_avg_ret1", np.nan),
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
    tail_5 = r.quantile(0.05)
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
        "tail_5pct_return_pct": round(float(tail_5 * 100), 3),
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


def event_anatomy(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    bins = pd.cut(
        trades["ret3_signal"],
        bins=[-1, -0.20, -0.15, -0.10, -0.075, 0],
        labels=["<= -20%", "-20%..-15%", "-15%..-10%", "-10%..-7.5%", "> -7.5%"],
    )
    return (
        trades.assign(panic_depth_bucket=bins)
        .groupby(["strategy", "panic_depth_bucket"], observed=True)
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
            expectancy_pct=("net_return", lambda s: round(float(s.mean() * 100), 3)),
            avg_recovery_from_low_pct=("recovery_from_low_pct", lambda s: round(float(s.mean() * 100), 2)),
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
            min(float(m["profit_factor"]), 3.0) * 20
            + max(float(m["expectancy_pct"]), -2.0) * 8
            + max(float(m["tail_5pct_return_pct"]), -12.0) * 0.8
            + positive_years * 0.25
            + max(oos_exp, -2.0) * 8
            + max(val_exp, -2.0) * 6
            - abs(min(float(m["max_drawdown_proxy_pct"]), 0)) * 0.45
        )
        verdict = "promote_to_paper" if score >= 65 and oos_exp > 0 and val_exp > 0 and m["trades"] >= 80 else "watch" if score >= 45 else "reject"
        rows.append({
            "strategy": m["strategy"],
            "family": m["family"],
            "robustness_score": round(score, 2),
            "positive_years_pct": round(positive_years, 2),
            "validation_expectancy_pct": round(val_exp, 3),
            "oos_expectancy_pct": round(oos_exp, 3),
            "verdict": verdict,
        })
    return pd.DataFrame(rows).sort_values("robustness_score", ascending=False)


def save_charts(out_dir: Path, trade_log: pd.DataFrame, metrics: pd.DataFrame, yearly: pd.DataFrame, anatomy: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    if not metrics.empty:
        m = metrics.sort_values("expectancy_pct", ascending=True)
        plt.figure(figsize=(11, 6))
        colors = ["#1b998b" if x > 0 else "#c44536" for x in m["expectancy_pct"]]
        plt.barh(m["strategy"], m["expectancy_pct"], color=colors)
        plt.axvline(0, color="#333333", linewidth=1)
        plt.title("Panic reversal expectancy by strategy")
        plt.xlabel("Average net return per trade (%)")
        plt.tight_layout()
        plt.savefig(chart_dir / "expectancy_by_strategy.png", dpi=160)
        plt.close()

        plt.figure(figsize=(9, 6))
        plt.scatter(metrics["trades"], metrics["profit_factor"], s=90, c=metrics["expectancy_pct"], cmap="viridis")
        for _, row in metrics.iterrows():
            plt.annotate(row["strategy"].replace("_", "\n"), (row["trades"], row["profit_factor"]), fontsize=7, alpha=0.85)
        plt.axhline(1, color="#444444", linewidth=1)
        plt.colorbar(label="Expectancy %")
        plt.title("Profit factor vs sample size")
        plt.xlabel("Trades")
        plt.ylabel("Profit factor")
        plt.tight_layout()
        plt.savefig(chart_dir / "pf_vs_sample_size.png", dpi=160)
        plt.close()

    if not trade_log.empty:
        top_names = metrics.sort_values("expectancy_pct", ascending=False).head(5)["strategy"].tolist()
        plt.figure(figsize=(12, 6))
        for name in top_names:
            part = trade_log[trade_log["strategy"] == name].sort_values("entry_date")
            eq = (1 + part["net_return"] / 10).cumprod()
            plt.plot(pd.to_datetime(part["entry_date"]), eq, label=name)
        plt.title("Top panic reversal equity curves, 10% capital per trade proxy")
        plt.ylabel("Equity multiple")
        plt.legend(fontsize=8)
        plt.tight_layout()
        plt.savefig(chart_dir / "top_equity_curves.png", dpi=160)
        plt.close()

        plt.figure(figsize=(10, 6))
        sample = trade_log.sample(min(len(trade_log), 4000), random_state=7)
        plt.scatter(sample["ret3_signal"] * 100, sample["net_return"] * 100, s=12, alpha=0.35, color="#33658a")
        plt.axhline(0, color="#333333", linewidth=1)
        plt.axvline(-10, color="#f26419", linestyle="--", linewidth=1)
        plt.title("Event anatomy: 3-day panic depth vs trade return")
        plt.xlabel("Signal 3-day return (%)")
        plt.ylabel("Net trade return (%)")
        plt.tight_layout()
        plt.savefig(chart_dir / "panic_depth_vs_return.png", dpi=160)
        plt.close()

    if not yearly.empty:
        pivot = yearly.pivot_table(index="strategy", columns="year", values="expectancy_pct", aggfunc="mean")
        plt.figure(figsize=(12, 6))
        plt.imshow(pivot.fillna(0), aspect="auto", cmap="RdYlGn", vmin=-3, vmax=3)
        plt.colorbar(label="Expectancy %")
        plt.xticks(range(len(pivot.columns)), pivot.columns, rotation=45)
        plt.yticks(range(len(pivot.index)), pivot.index)
        plt.title("Yearly expectancy heatmap")
        plt.tight_layout()
        plt.savefig(chart_dir / "yearly_expectancy_heatmap.png", dpi=160)
        plt.close()

    if not anatomy.empty:
        pivot = anatomy.pivot_table(index="strategy", columns="panic_depth_bucket", values="expectancy_pct", aggfunc="mean")
        plt.figure(figsize=(12, 6))
        plt.imshow(pivot.fillna(0), aspect="auto", cmap="RdYlGn", vmin=-3, vmax=3)
        plt.colorbar(label="Expectancy %")
        plt.xticks(range(len(pivot.columns)), pivot.columns, rotation=35, ha="right")
        plt.yticks(range(len(pivot.index)), pivot.index)
        plt.title("Expectancy by panic-depth bucket")
        plt.tight_layout()
        plt.savefig(chart_dir / "expectancy_by_panic_depth.png", dpi=160)
        plt.close()


def write_report(
    out_dir: Path,
    metrics: pd.DataFrame,
    splits: pd.DataFrame,
    yearly: pd.DataFrame,
    exit_stats: pd.DataFrame,
    anatomy: pd.DataFrame,
    scorecard: pd.DataFrame,
    cost_metrics: pd.DataFrame,
    strategies: list[PanicStrategy],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    report = out_dir / "final_report.md"
    best = metrics.sort_values("expectancy_pct", ascending=False).head(5)
    promoted = scorecard[scorecard["verdict"] == "promote_to_paper"]
    watched = scorecard[scorecard["verdict"] == "watch"]

    lines: list[str] = []
    lines.append("# Panic Reversal Strategy Lab")
    lines.append("")
    lines.append(f"Data window: {start.date()} to {end.date()}. Entries are next-session opens after a daily signal.")
    lines.append("")
    lines.append("## Strategy thesis")
    lines.append("")
    lines.append(
        "This lab translates the Lance Brightstein panic trade into systematic rules: broad or symbol-level forced liquidation, "
        "large multi-day decline, volatility expansion, reclaim/off-low confirmation, defined low-of-panic risk, and an exit into "
        "mean reversion or a prior-daily-low trail."
    )
    lines.append("")
    lines.append("## Tested variants")
    lines.append("")
    for s in strategies:
        lines.append(f"- `{s.name}`: {s.thesis}")
    lines.append("")
    lines.append("## Headline metrics, base cost model")
    lines.append("")
    lines.append(best.to_markdown(index=False))
    lines.append("")
    lines.append("## Robustness scorecard")
    lines.append("")
    lines.append(scorecard.to_markdown(index=False))
    lines.append("")
    lines.append("## Chronological split read")
    lines.append("")
    split_view = splits.pivot_table(index="strategy", columns="split", values="expectancy_pct", aggfunc="first").reset_index()
    lines.append(split_view.to_markdown(index=False))
    lines.append("")
    lines.append("## Cost sensitivity")
    lines.append("")
    cost_view = cost_metrics.pivot_table(index="strategy", columns="cost_scenario", values="expectancy_pct", aggfunc="first").reset_index()
    lines.append(cost_view.to_markdown(index=False))
    lines.append("")
    lines.append("## Exit behavior")
    lines.append("")
    lines.append(exit_stats.sort_values(["strategy", "expectancy_pct"], ascending=[True, False]).head(40).to_markdown(index=False))
    lines.append("")
    lines.append("## Panic-depth anatomy")
    lines.append("")
    lines.append(anatomy.sort_values(["strategy", "panic_depth_bucket"]).head(80).to_markdown(index=False))
    lines.append("")
    lines.append("## Pro-trader interpretation")
    lines.append("")
    if not promoted.empty:
        lines.append("Promote candidates:")
        for _, row in promoted.iterrows():
            lines.append(f"- `{row['strategy']}` passed the strict scorecard and deserves paper routing.")
    elif not watched.empty:
        lines.append("No variant cleared the strict production gate. Watch-list candidates:")
        for _, row in watched.head(4).iterrows():
            lines.append(f"- `{row['strategy']}`: worth iterating because its score was materially better than the rest.")
    else:
        lines.append("No variant cleared the paper-trading gate. Treat the raw daily-bar rules as research, not an executable production strategy.")
    lines.append("")
    lines.append(
        "The key production upgrade is intraday confirmation. Lance's described trade was not a blind next-day buy; it used a failed breakdown, "
        "reclaim of lows, trend break, and higher-low confirmation while liquidity was stressed. Daily bars can approximate the setup but cannot "
        "prove the exact timing edge."
    )
    lines.append("")
    lines.append("## Recommended next algorithm")
    lines.append("")
    lines.append("1. Require broad panic: index/breadth collapse, volatility expansion, and multi-asset stress if available.")
    lines.append("2. Build a liquid watch list: index futures/ETFs plus the largest, cleanest single names.")
    lines.append("3. Wait for failed breakdown: new low attempt fails, price reclaims the low, and a higher low forms.")
    lines.append("4. Enter on reclaim/break of short intraday downtrend, not on the first falling print.")
    lines.append("5. Initial stop at panic low; after a full retrace of the panic leg, scale out heavily.")
    lines.append("6. Trail the remaining core with prior daily lows.")
    lines.append("")
    lines.append("## Generated visuals")
    lines.append("")
    lines.append("- `charts/expectancy_by_strategy.png`")
    lines.append("- `charts/pf_vs_sample_size.png`")
    lines.append("- `charts/top_equity_curves.png`")
    lines.append("- `charts/panic_depth_vs_return.png`")
    lines.append("- `charts/yearly_expectancy_heatmap.png`")
    lines.append("- `charts/expectancy_by_panic_depth.png`")
    report.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    daily = build_daily_cache(Path(args.parquet_dir), out_dir / "daily_bars_cache.parquet", refresh=args.refresh_cache)
    data = add_panic_features(daily)
    data = data.dropna(subset=["trade_date", "open", "high", "low", "close"])
    start = pd.to_datetime(data["trade_date"]).min()
    end = pd.to_datetime(data["trade_date"]).max()
    strategies = make_strategies()

    all_trades = []
    all_cost_trades = []
    cost_metrics_rows = []
    for strategy in strategies:
        print(f"Backtesting {strategy.name}")
        trades = backtest_strategy(data, strategy, **COST_SCENARIOS["base"])
        all_trades.append(trades)
        for scenario, costs in COST_SCENARIOS.items():
            scenario_trades = backtest_strategy(data, strategy, **costs)
            if not scenario_trades.empty:
                scenario_trades["cost_scenario"] = scenario
            all_cost_trades.append(scenario_trades)
            m = metrics_for_trades(scenario_trades, strategy.name)
            m["cost_scenario"] = scenario
            cost_metrics_rows.append(m)

    trade_log = pd.concat([t for t in all_trades if not t.empty], ignore_index=True) if any(not t.empty for t in all_trades) else pd.DataFrame()
    cost_trade_log = pd.concat([t for t in all_cost_trades if not t.empty], ignore_index=True) if any(not t.empty for t in all_cost_trades) else pd.DataFrame()
    metrics = pd.DataFrame([metrics_for_trades(t, s.name) for t, s in zip(all_trades, strategies)]).sort_values("expectancy_pct", ascending=False)
    cost_metrics = pd.DataFrame(cost_metrics_rows)
    splits = split_metrics(trade_log, start, end) if not trade_log.empty else pd.DataFrame()
    yearly = yearly_metrics(trade_log) if not trade_log.empty else pd.DataFrame()
    exits = exit_metrics(trade_log)
    anatomy = event_anatomy(trade_log)
    symbols = symbol_contribution(trade_log)
    scorecard = robustness_score(metrics[metrics["trades"] > 0], splits, yearly) if not trade_log.empty else pd.DataFrame()

    trade_log.to_csv(out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    cost_metrics.to_csv(out_dir / "cost_scenario_metrics.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    exits.to_csv(out_dir / "exit_metrics.csv", index=False)
    anatomy.to_csv(out_dir / "panic_depth_anatomy.csv", index=False)
    symbols.to_csv(out_dir / "symbol_contribution.csv", index=False)
    scorecard.to_csv(out_dir / "robustness_scorecard.csv", index=False)
    save_charts(out_dir, trade_log, metrics, yearly, anatomy)
    write_report(out_dir, metrics, splits, yearly, exits, anatomy, scorecard, cost_metrics, strategies, start, end)

    print(f"Wrote panic reversal lab to {out_dir}")
    print(metrics.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest panic/capitulation reversal variants on local candle parquet history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Example:
              python scripts/panic_reversal_strategy_lab.py
            """
        ),
    )
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
