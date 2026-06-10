from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_CACHE = ROOT / "docs" / "complex_strategy_tuning_lab" / "daily_bars_cache.parquet"
DEFAULT_OUT_DIR = ROOT / "docs" / "king_supertrend_lab"
ROUND_TRIP_COST = 0.0026


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    family: str
    max_new_per_week: int
    max_hold_weeks: int
    trigger_window_weeks: int = 1


SUPER_TREND = StrategyConfig(
    name="weekly_supertrend_10_3",
    family="Supertrend 10-3 Weekly",
    max_new_per_week=20,
    max_hold_weeks=156,
)

KING_CANDLE = StrategyConfig(
    name="king_candle_supertrend_breakout",
    family="King Candle Supertrend Breakout",
    max_new_per_week=12,
    max_hold_weeks=156,
    trigger_window_weeks=4,
)

KING_CANDLE_QUALITY = StrategyConfig(
    name="king_candle_quality_breakout",
    family="King Candle Quality Breakout",
    max_new_per_week=8,
    max_hold_weeks=156,
    trigger_window_weeks=4,
)


def load_daily(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    required = ["symbol", "trade_date", "open", "high", "low", "close", "volume"]
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise RuntimeError(f"Daily cache is missing required columns: {missing}")
    df = df[required + [c for c in ["buckets", "gap_pct"] if c in df.columns]].copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.dropna(subset=required)
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df[df["high"] >= df[["open", "close"]].max(axis=1)]
    df = df[df["low"] <= df[["open", "close"]].min(axis=1)]
    return df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def volume_summary(df: pd.DataFrame) -> dict[str, object]:
    return {
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "from_date": df["trade_date"].min().strftime("%Y-%m-%d"),
        "to_date": df["trade_date"].max().strftime("%Y-%m-%d"),
        "missing_volume_rows": int(df["volume"].isna().sum()),
        "nonpositive_volume_rows": int((df["volume"] <= 0).sum()),
        "volume_coverage_pct": round(100 * float((df["volume"] > 0).mean()), 4),
    }


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.copy()
    df["week_end"] = df["trade_date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    grouped = df.groupby(["symbol", "week_end"], sort=False)
    weekly = grouped.agg(
        start_date=("trade_date", "first"),
        end_date=("trade_date", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        days=("trade_date", "nunique"),
    ).reset_index()
    weekly = weekly[weekly["days"] >= 3]
    return weekly.sort_values(["symbol", "week_end"]).reset_index(drop=True)


def add_supertrend(group: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    g = group.copy()
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            g["high"] - g["low"],
            (g["high"] - prev_close).abs(),
            (g["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()
    hl2 = (g["high"] + g["low"]) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.full(len(g), np.nan)
    final_lower = np.full(len(g), np.nan)
    supertrend = np.full(len(g), np.nan)
    direction = np.zeros(len(g), dtype=np.int8)

    close = g["close"].to_numpy(dtype=float)
    bu = basic_upper.to_numpy(dtype=float)
    bl = basic_lower.to_numpy(dtype=float)

    for i in range(len(g)):
        if not math.isfinite(bu[i]) or not math.isfinite(bl[i]):
            continue
        if i == 0 or not math.isfinite(final_upper[i - 1]):
            final_upper[i] = bu[i]
            final_lower[i] = bl[i]
            direction[i] = 1 if close[i] >= hl2.iloc[i] else -1
            supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
            continue

        prev_fu = final_upper[i - 1]
        prev_fl = final_lower[i - 1]
        prev_close_i = close[i - 1]
        final_upper[i] = bu[i] if bu[i] < prev_fu or prev_close_i > prev_fu else prev_fu
        final_lower[i] = bl[i] if bl[i] > prev_fl or prev_close_i < prev_fl else prev_fl

        prev_st = supertrend[i - 1]
        if prev_st == prev_fu:
            if close[i] <= final_upper[i]:
                direction[i] = -1
                supertrend[i] = final_upper[i]
            else:
                direction[i] = 1
                supertrend[i] = final_lower[i]
        else:
            if close[i] >= final_lower[i]:
                direction[i] = 1
                supertrend[i] = final_lower[i]
            else:
                direction[i] = -1
                supertrend[i] = final_upper[i]

    g["atr10"] = atr
    g["supertrend"] = supertrend
    g["st_dir"] = direction
    return g


def add_features(weekly: pd.DataFrame) -> pd.DataFrame:
    df = weekly.sort_values(["symbol", "week_end"]).copy()
    df = df.groupby("symbol", group_keys=False).apply(add_supertrend).reset_index(drop=True)
    g = df.groupby("symbol", group_keys=False)

    for col in ["high", "close", "low"]:
        df[f"ema50_{col}"] = g[col].transform(lambda s: s.ewm(span=50, adjust=False, min_periods=20).mean())

    prev_high = g["high"].shift(1)
    prev_close = g["close"].shift(1)
    df["prior_high20w"] = prev_high.groupby(df["symbol"]).rolling(20, min_periods=8).max().reset_index(level=0, drop=True)
    df["prior_high52w"] = prev_high.groupby(df["symbol"]).rolling(52, min_periods=26).max().reset_index(level=0, drop=True)
    df["vol20w"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=8).mean())
    df["adv20w"] = g.apply(lambda x: (x["close"] * x["volume"]).rolling(20, min_periods=8).mean()).reset_index(level=0, drop=True)
    df["relvol"] = df["volume"] / df["vol20w"].replace(0, np.nan)
    df["ret13w"] = df["close"] / g["close"].shift(13) - 1
    df["rs13w_rank"] = df.groupby("week_end")["ret13w"].rank(pct=True)
    df["market_breadth_ema50"] = (df["close"] > df["ema50_close"]).groupby(df["week_end"]).transform("mean")

    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    df["body_ratio"] = body / candle_range
    df["close_location"] = (df["close"] - df["low"]) / candle_range
    df["open_location"] = (df["open"] - df["low"]) / candle_range
    df["range_atr"] = (df["high"] - df["low"]) / df["atr10"].replace(0, np.nan)
    df["green_candle"] = df["close"] > df["open"]
    df["st_flip_up"] = (df["st_dir"] == 1) & (g["st_dir"].shift(1) == -1)
    df["st_positive"] = df["st_dir"] == 1
    df["st_flip_down"] = (df["st_dir"] == -1) & (g["st_dir"].shift(1) == 1)
    df["near_ema50_band"] = (
        (df["low"] <= df["ema50_high"] * 1.02)
        & (df["close"] >= df["ema50_low"] * 0.98)
        & df["ema50_close"].notna()
    )
    df["liquid"] = (df["close"] >= 50) & (df["vol20w"] >= 500_000) & (df["adv20w"] >= 100_000_000)
    df["rank_score"] = (
        df["rs13w_rank"].fillna(0) * 5
        + df["relvol"].fillna(0).clip(upper=5) * 1.2
        + df["body_ratio"].fillna(0) * 2
        + df["close_location"].fillna(0) * 2
        + df["range_atr"].fillna(0).clip(upper=4) * 0.8
        + df["market_breadth_ema50"].fillna(0)
    )
    return df


def supertrend_candidates(features: pd.DataFrame) -> pd.DataFrame:
    mask = (
        features["liquid"]
        & features["st_flip_up"]
        & (features["market_breadth_ema50"] >= 0.35)
        & (features["rs13w_rank"] >= 0.50)
        & (features["relvol"] >= 0.75)
    )
    return features.loc[mask].copy()


def king_candle_candidates(
    features: pd.DataFrame,
    *,
    body_min: float = 0.65,
    relvol_min: float = 1.20,
    rs_min: float = 0.55,
    range_atr_min: float = 1.15,
) -> pd.DataFrame:
    mask = (
        features["liquid"]
        & features["st_positive"]
        & features["green_candle"]
        & (features["body_ratio"] >= body_min)
        & (features["close_location"] >= 0.78)
        & (features["open_location"] <= 0.35)
        & (features["range_atr"] >= range_atr_min)
        & (features["relvol"] >= relvol_min)
        & (features["high"] >= features["prior_high20w"] * 1.001)
        & (features["rs13w_rank"] >= rs_min)
        & (features["market_breadth_ema50"] >= 0.35)
        & (features["near_ema50_band"] | features["st_flip_up"] | (features["close"] >= features["prior_high52w"] * 0.95))
    )
    return features.loc[mask].copy()


def cap_candidates(candidates: pd.DataFrame, max_new_per_week: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    return (
        candidates.sort_values(["week_end", "rank_score", "relvol"], ascending=[True, False, False])
        .groupby("week_end", group_keys=False)
        .head(max_new_per_week)
        .copy()
    )


def exit_trade(
    rows: pd.DataFrame,
    entry_idx: int,
    entry: float,
    max_hold_weeks: int,
    *,
    allow_entry_week_targets: bool = True,
) -> dict[str, object]:
    target1 = entry * 1.40
    target2 = target1 * 1.40
    remaining = 1.0
    weighted_return = 0.0
    hit_t1 = False
    hit_t2 = False
    exit_reason = "data_end"
    exit_idx = min(len(rows) - 1, entry_idx + max_hold_weeks)
    exit_price = float(rows.iloc[exit_idx]["close"])
    exit_date = rows.iloc[exit_idx]["end_date"]

    for j in range(entry_idx, min(len(rows), entry_idx + max_hold_weeks + 1)):
        row = rows.iloc[j]
        high = float(row["high"])

        can_take_target = allow_entry_week_targets or j > entry_idx
        if can_take_target and not hit_t1 and high >= target1:
            weighted_return += 0.40 * (target1 / entry - 1)
            remaining -= 0.40
            hit_t1 = True
        if can_take_target and not hit_t2 and high >= target2:
            weighted_return += 0.40 * (target2 / entry - 1)
            remaining -= 0.40
            hit_t2 = True

        if int(row["st_dir"]) == -1 and j > entry_idx:
            if j + 1 < len(rows):
                exit_idx = j + 1
                exit_row = rows.iloc[exit_idx]
                exit_price = float(exit_row["open"])
                exit_date = exit_row["start_date"]
            else:
                exit_idx = j
                exit_price = float(row["close"])
                exit_date = row["end_date"]
            exit_reason = "weekly_supertrend_negative"
            break
    else:
        if exit_idx >= entry_idx + max_hold_weeks:
            exit_reason = "max_hold"

    if remaining > 0:
        weighted_return += remaining * (exit_price / entry - 1)

    return {
        "exit_idx": int(exit_idx),
        "exit_date": exit_date,
        "exit_price": exit_price,
        "hit_target1": hit_t1,
        "hit_target2": hit_t2,
        "exit_reason": exit_reason,
        "gross_return": weighted_return,
        "net_return": weighted_return - ROUND_TRIP_COST,
    }


def backtest_candidates(features: pd.DataFrame, candidates: pd.DataFrame, config: StrategyConfig, mode: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()

    rows_by_symbol = {symbol: grp.reset_index(drop=True) for symbol, grp in features.groupby("symbol", sort=False)}
    index_lookup = {
        (symbol, week_end): idx
        for symbol, grp in rows_by_symbol.items()
        for idx, week_end in enumerate(grp["week_end"])
    }

    trades: list[dict[str, object]] = []
    blocked_until: dict[str, int] = {}
    capped = cap_candidates(candidates, config.max_new_per_week)
    capped = capped.sort_values(["week_end", "rank_score"], ascending=[True, False])

    for signal in capped.itertuples(index=False):
        symbol = signal.symbol
        rows = rows_by_symbol[symbol]
        signal_idx = index_lookup.get((symbol, signal.week_end))
        if signal_idx is None:
            continue
        if signal_idx < blocked_until.get(symbol, -1):
            continue

        trigger_price = np.nan
        entry_idx = signal_idx + 1
        if mode == "king":
            trigger_price = float(signal.high) * 1.001
            found = None
            for j in range(signal_idx + 1, min(len(rows), signal_idx + config.trigger_window_weeks + 1)):
                if j > signal_idx + 1 and int(rows.iloc[j - 1]["st_dir"]) != 1:
                    break
                if float(rows.iloc[j]["high"]) >= trigger_price:
                    found = j
                    break
            if found is None:
                continue
            entry_idx = found
            entry = max(trigger_price, float(rows.iloc[entry_idx]["open"]))
        else:
            if entry_idx >= len(rows):
                continue
            entry = float(rows.iloc[entry_idx]["open"])

        if entry_idx >= len(rows) or entry <= 0 or not math.isfinite(entry):
            continue

        result = exit_trade(
            rows,
            entry_idx,
            entry,
            config.max_hold_weeks,
            allow_entry_week_targets=mode != "king",
        )
        blocked_until[symbol] = int(result["exit_idx"]) + 1
        entry_row = rows.iloc[entry_idx]
        trades.append(
            {
                "strategy": config.name,
                "family": config.family,
                "symbol": symbol,
                "signal_week": signal.week_end,
                "signal_end_date": signal.end_date,
                "entry_date": entry_row["start_date"],
                "exit_date": result["exit_date"],
                "entry": entry,
                "exit": result["exit_price"],
                "gross_return": result["gross_return"],
                "net_return": result["net_return"],
                "hold_weeks": int(result["exit_idx"]) - entry_idx + 1,
                "exit_reason": result["exit_reason"],
                "hit_target1": result["hit_target1"],
                "hit_target2": result["hit_target2"],
                "rank_score": float(signal.rank_score),
                "relvol": float(signal.relvol),
                "rs13w_rank": float(signal.rs13w_rank),
                "body_ratio": float(signal.body_ratio),
                "close_location": float(signal.close_location),
                "range_atr": float(signal.range_atr),
                "trigger_price": trigger_price,
            }
        )
    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> dict[str, object]:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct_points": 0.0,
            "avg_hold_weeks": 0.0,
            "target1_rate": 0.0,
            "target2_rate": 0.0,
        }
    returns = trades["net_return"].astype(float)
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    equity = returns.cumsum()
    drawdown = equity - equity.cummax()
    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    return {
        "trades": int(len(trades)),
        "win_rate": round(100 * float((returns > 0).mean()), 2),
        "avg_return_pct": round(100 * float(returns.mean()), 3),
        "median_return_pct": round(100 * float(returns.median()), 3),
        "profit_factor": round(float(gross_profit / gross_loss), 3) if gross_loss > 0 else 999.0,
        "max_drawdown_pct_points": round(100 * float(drawdown.min()), 3),
        "avg_hold_weeks": round(float(trades["hold_weeks"].mean()), 2),
        "target1_rate": round(100 * float(trades["hit_target1"].mean()), 2),
        "target2_rate": round(100 * float(trades["hit_target2"].mean()), 2),
    }


def yearly(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    out = trades.copy()
    out["year"] = pd.to_datetime(out["entry_date"]).dt.year
    grouped = out.groupby("year")
    return grouped.agg(
        trades=("symbol", "count"),
        win_rate=("net_return", lambda s: round(100 * float((s > 0).mean()), 2)),
        avg_return_pct=("net_return", lambda s: round(100 * float(s.mean()), 3)),
        return_proxy_pct=("net_return", lambda s: round(100 * float(s.sum()), 3)),
    ).reset_index()


def latest_candidates(features: pd.DataFrame, candidates: pd.DataFrame, limit: int = 25) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    cutoff = features["week_end"].max() - pd.Timedelta(days=70)
    cols = [
        "symbol",
        "week_end",
        "end_date",
        "close",
        "high",
        "supertrend",
        "rank_score",
        "relvol",
        "rs13w_rank",
        "body_ratio",
        "close_location",
        "range_atr",
    ]
    return (
        candidates[candidates["week_end"] >= cutoff]
        .sort_values(["week_end", "rank_score"], ascending=[False, False])
        .head(limit)[cols]
        .copy()
    )


def save_chart(out_dir: Path, trade_sets: dict[str, pd.DataFrame]) -> None:
    if plt is None:
        return
    plt.figure(figsize=(11, 6))
    for name, trades in trade_sets.items():
        if trades.empty:
            continue
        ordered = trades.sort_values("exit_date")
        plt.plot(pd.to_datetime(ordered["exit_date"]), ordered["net_return"].cumsum() * 100, label=name)
    plt.title("King Candle vs Weekly Supertrend equity proxy")
    plt.ylabel("Cumulative net return, percentage points")
    plt.xlabel("Exit date")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "equity_proxy.png", dpi=150)
    plt.close()


def write_report(
    out_dir: Path,
    data_stats: dict[str, object],
    weekly: pd.DataFrame,
    summaries: dict[str, dict[str, object]],
    yearly_tables: dict[str, pd.DataFrame],
    latest_tables: dict[str, pd.DataFrame],
) -> None:
    lines = [
        "# King Candle and Weekly Supertrend Lab",
        "",
        "This is a first explicit backtest of the pasted Vijay Khant style rules on local NSE OHLCV history.",
        "",
        "## Data availability",
        "",
        f"- Daily rows: {data_stats['rows']:,}",
        f"- Symbols: {data_stats['symbols']:,}",
        f"- Date range: {data_stats['from_date']} to {data_stats['to_date']}",
        f"- Missing volume rows: {data_stats['missing_volume_rows']:,}",
        f"- Non-positive volume rows: {data_stats['nonpositive_volume_rows']:,}",
        f"- Positive-volume coverage: {data_stats['volume_coverage_pct']}%",
        f"- Weekly rows after aggregation: {len(weekly):,}",
        "",
        "## Rule interpretation",
        "",
        "### Weekly Supertrend 10-3",
        "",
        "- Aggregate daily OHLCV into weekly candles.",
        "- Compute Supertrend with ATR period 10 and multiplier 3.",
        "- Buy next week open after the weekly Supertrend flips positive.",
        "- Require liquidity, relative strength, basic market breadth, and usable volume.",
        "- Sell 40% at +40%, another 40% at another +40% from that level (+96% from entry), and trail the final 20% until weekly Supertrend turns negative.",
        "",
        "### King Candle Supertrend Breakout",
        "",
        "- Weekly bullish candle: green body, large body/range, close near high, open near low.",
        "- Requires range expansion, volume expansion, relative strength, Supertrend positive, and a 20-week high breakout.",
        "- Entry is a stop-style trigger above the King candle high within the next 4 weeks.",
        "- The trigger window only uses Supertrend states from completed weeks; targets are not credited inside the same weekly candle that triggered the entry.",
        "- Uses the same 40/40/20 profit booking and weekly Supertrend trailing exit.",
        "",
        "### King Candle Quality Breakout",
        "",
        "- Same King Candle idea, but only the cleaner candles from the sweep are allowed.",
        "- Requires body/range >= 0.85, relative volume >= 1.20, RS13 rank >= 0.85, and range/ATR >= 1.15.",
        "- This is the stricter version to prefer for watchlists because it held up better outside the 2021-2023 bull run.",
        "",
        "## Summary",
        "",
        "| Strategy | Trades | Win % | Avg % | Median % | PF | Max DD pct-pts | Avg hold wks | T1 hit | T2 hit |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, summary in summaries.items():
        lines.append(
            f"| {name} | {summary['trades']:,} | {summary['win_rate']:.2f} | {summary['avg_return_pct']:.3f} | "
            f"{summary['median_return_pct']:.3f} | {summary['profit_factor']:.3f} | {summary['max_drawdown_pct_points']:.3f} | "
            f"{summary['avg_hold_weeks']:.2f} | {summary['target1_rate']:.2f} | {summary['target2_rate']:.2f} |"
        )

    for name, table in yearly_tables.items():
        lines.extend(["", f"## {name} yearly", ""])
        if table.empty:
            lines.append("No trades.")
        else:
            lines.append(table.to_markdown(index=False))

    for name, table in latest_tables.items():
        lines.extend(["", f"## Recent {name} candidates", ""])
        if table.empty:
            lines.append("No recent candidates.")
        else:
            printable = table.copy()
            printable["week_end"] = pd.to_datetime(printable["week_end"]).dt.strftime("%Y-%m-%d")
            printable["end_date"] = pd.to_datetime(printable["end_date"]).dt.strftime("%Y-%m-%d")
            for col in ["close", "high", "supertrend", "rank_score", "relvol", "rs13w_rank", "body_ratio", "close_location", "range_atr"]:
                printable[col] = printable[col].astype(float).round(3)
            lines.append(printable.to_markdown(index=False))

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a daily-to-weekly OHLCV backtest. It cannot verify intraday order sequence inside a weekly candle.",
            "- Volume is available and used as weekly summed volume plus 20-week relative volume.",
            "- Fundamental/news confirmation from the video is not included yet; this test is price/volume only.",
            "- Open trades are force-closed at the final available candle for measurement.",
        ]
    )
    (out_dir / "final_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    daily = load_daily(Path(args.daily_cache))
    stats = volume_summary(daily)
    weekly = add_features(to_weekly(daily))

    st_candidates = supertrend_candidates(weekly)
    king_candidates = king_candle_candidates(weekly)
    quality_candidates = king_candle_candidates(
        weekly,
        body_min=0.85,
        relvol_min=1.20,
        rs_min=0.85,
        range_atr_min=1.15,
    )
    st_trades = backtest_candidates(weekly, st_candidates, SUPER_TREND, mode="supertrend")
    king_trades = backtest_candidates(weekly, king_candidates, KING_CANDLE, mode="king")
    quality_trades = backtest_candidates(weekly, quality_candidates, KING_CANDLE_QUALITY, mode="king")

    st_trades.to_csv(out_dir / "weekly_supertrend_trades.csv", index=False)
    king_trades.to_csv(out_dir / "king_candle_trades.csv", index=False)
    quality_trades.to_csv(out_dir / "king_candle_quality_trades.csv", index=False)
    st_candidates.to_csv(out_dir / "weekly_supertrend_candidates.csv", index=False)
    king_candidates.to_csv(out_dir / "king_candle_candidates.csv", index=False)
    quality_candidates.to_csv(out_dir / "king_candle_quality_candidates.csv", index=False)

    summaries = {
        "Weekly Supertrend 10-3": summarize(st_trades),
        "King Candle Supertrend Breakout": summarize(king_trades),
        "King Candle Quality Breakout": summarize(quality_trades),
    }
    yearly_tables = {
        "Weekly Supertrend 10-3": yearly(st_trades),
        "King Candle Supertrend Breakout": yearly(king_trades),
        "King Candle Quality Breakout": yearly(quality_trades),
    }
    latest_tables = {
        "Weekly Supertrend 10-3": latest_candidates(weekly, st_candidates),
        "King Candle Supertrend Breakout": latest_candidates(weekly, king_candidates),
        "King Candle Quality Breakout": latest_candidates(weekly, quality_candidates),
    }
    for name, table in yearly_tables.items():
        table.to_csv(out_dir / f"{name.lower().replace(' ', '_').replace('-', '')}_yearly.csv", index=False)
    for name, table in latest_tables.items():
        table.to_csv(out_dir / f"{name.lower().replace(' ', '_').replace('-', '')}_latest_candidates.csv", index=False)

    save_chart(out_dir, summaries and {
        "Weekly Supertrend 10-3": st_trades,
        "King Candle": king_trades,
        "King Candle Quality": quality_trades,
    })
    write_report(out_dir, stats, weekly, summaries, yearly_tables, latest_tables)
    print("Data:", stats)
    print("Weekly Supertrend:", summaries["Weekly Supertrend 10-3"])
    print("King Candle:", summaries["King Candle Supertrend Breakout"])
    print("King Candle Quality:", summaries["King Candle Quality Breakout"])
    print(f"Wrote {out_dir / 'final_report.md'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest weekly Supertrend and King Candle rules.")
    parser.add_argument("--daily-cache", default=str(DEFAULT_DAILY_CACHE))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
