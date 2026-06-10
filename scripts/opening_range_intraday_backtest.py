from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_DIR = ROOT / "parquets" / "parquet_of_index"
DEFAULT_OUT_DIR = ROOT / "docs" / "opening_range_intraday_lab"
DEFAULT_STRICT_OUT_DIR = ROOT / "docs" / "opening_range_intraday_nolookahead_lab"


@dataclass(frozen=True)
class ORBConfig:
    name: str
    opening_range_buckets: int = 15
    entry_cutoff_bucket: int = 45  # last minute before 10:00 IST
    rr: float = 2.0
    max_trades_per_day: int = 2
    skip_tuesday: bool = False
    trigger_model: str = "touch"  # touch | close5_next_open
    min_range_pct: float = 0.0
    max_range_pct: float = 10.0


def bucket_label(bucket: int) -> str:
    minutes = 9 * 60 + 15 + int(bucket) - 1
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def index_files(index_dir: Path, symbols: Iterable[str] | None = None) -> list[Path]:
    if symbols:
        paths: list[Path] = []
        for symbol in symbols:
            paths.extend((index_dir / symbol).glob("*.parquet"))
        return sorted(paths)
    return sorted(index_dir.glob("*/*.parquet"))


def load_index_data(index_dir: Path, symbols: Iterable[str] | None = None) -> pd.DataFrame:
    paths = index_files(index_dir, symbols)
    if not paths:
        raise FileNotFoundError(f"No index parquet files found under {index_dir}")
    frames = []
    columns = ["date", "symbol", "bucket", "open", "high", "low", "close"]
    for path in paths:
        frames.append(pd.read_parquet(path, columns=columns))
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["bucket"] = df["bucket"].astype(int)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close"])
    df = df[(df["bucket"] >= 1) & (df["bucket"] <= 375)].copy()
    df = df.sort_values(["symbol", "date", "bucket"])
    df = df.drop_duplicates(["symbol", "date", "bucket"], keep="last")
    return df


def simulate_exit(day: pd.DataFrame, start_pos: int, direction: str, entry: float, stop: float, target: float, risk: float) -> dict:
    rows = day.iloc[start_pos:]
    for row in rows.itertuples(index=False):
        hit_stop = row.low <= stop if direction == "long" else row.high >= stop
        hit_target = row.high >= target if direction == "long" else row.low <= target
        if hit_stop and hit_target:
            return {
                "exit_bucket": int(row.bucket),
                "exit_time": bucket_label(int(row.bucket)),
                "exit_price": stop,
                "exit_reason": "ambiguous_stop_first",
                "r_multiple": -1.0,
            }
        if hit_stop:
            return {
                "exit_bucket": int(row.bucket),
                "exit_time": bucket_label(int(row.bucket)),
                "exit_price": stop,
                "exit_reason": "SL",
                "r_multiple": -1.0,
            }
        if hit_target:
            return {
                "exit_bucket": int(row.bucket),
                "exit_time": bucket_label(int(row.bucket)),
                "exit_price": target,
                "exit_reason": "TP",
                "r_multiple": float((target - entry) / risk if direction == "long" else (entry - target) / risk),
            }
    last = day.iloc[-1]
    exit_price = float(last["close"])
    r_multiple = (exit_price - entry) / risk if direction == "long" else (entry - exit_price) / risk
    return {
        "exit_bucket": int(last["bucket"]),
        "exit_time": bucket_label(int(last["bucket"])),
        "exit_price": exit_price,
        "exit_reason": "EOD",
        "r_multiple": float(r_multiple),
    }


def next_signal_touch(day: pd.DataFrame, start_pos: int, cfg: ORBConfig, or_high: float, or_low: float) -> tuple[int, str, float] | None:
    for pos in range(start_pos, len(day)):
        row = day.iloc[pos]
        bucket = int(row["bucket"])
        if bucket > cfg.entry_cutoff_bucket:
            return None
        broke_high = float(row["high"]) >= or_high
        broke_low = float(row["low"]) <= or_low
        if broke_high and broke_low:
            continue
        if broke_high:
            return pos, "long", or_high
        if broke_low:
            return pos, "short", or_low
    return None


def five_min_groups(day: pd.DataFrame, start_pos: int, cfg: ORBConfig) -> list[tuple[int, int, pd.Series]]:
    rows = day.iloc[start_pos:].copy()
    rows = rows[rows["bucket"] <= cfg.entry_cutoff_bucket]
    if rows.empty:
        return []
    rows["group"] = ((rows["bucket"] - (cfg.opening_range_buckets + 1)) // 5).astype(int)
    groups: list[tuple[int, int, pd.Series]] = []
    for _, part in rows.groupby("group", sort=True):
        if len(part) < 5:
            continue
        start = int(part.index[0])
        end = int(part.index[-1])
        agg = pd.Series({
            "bucket": int(part["bucket"].iloc[-1]),
            "open": float(part["open"].iloc[0]),
            "high": float(part["high"].max()),
            "low": float(part["low"].min()),
            "close": float(part["close"].iloc[-1]),
        })
        groups.append((start, end, agg))
    return groups


def next_signal_close5(day: pd.DataFrame, start_pos: int, cfg: ORBConfig, or_high: float, or_low: float) -> tuple[int, str, float] | None:
    for _, end_pos, bar in five_min_groups(day, start_pos, cfg):
        next_pos = end_pos + 1
        if next_pos >= len(day):
            return None
        next_bucket = int(day.iloc[next_pos]["bucket"])
        if next_bucket > cfg.entry_cutoff_bucket:
            return None
        if float(bar["close"]) > or_high:
            return next_pos, "long", float(day.iloc[next_pos]["open"])
        if float(bar["close"]) < or_low:
            return next_pos, "short", float(day.iloc[next_pos]["open"])
    return None


def backtest_day(symbol: str, date, day: pd.DataFrame, cfg: ORBConfig) -> list[dict]:
    day = day.sort_values("bucket").reset_index(drop=True)
    opening = day[(day["bucket"] >= 1) & (day["bucket"] <= cfg.opening_range_buckets)]
    if len(opening) < cfg.opening_range_buckets:
        return []
    if cfg.skip_tuesday and pd.Timestamp(date).dayofweek == 1:
        return []

    or_high = float(opening["high"].max())
    or_low = float(opening["low"].min())
    or_open = float(opening["open"].iloc[0])
    or_width = or_high - or_low
    if or_width <= 0 or or_open <= 0:
        return []
    range_pct = or_width / or_open * 100
    if range_pct < cfg.min_range_pct or range_pct > cfg.max_range_pct:
        return []

    start_pos_candidates = day.index[day["bucket"] >= cfg.opening_range_buckets + 1].tolist()
    if not start_pos_candidates:
        return []

    trades: list[dict] = []
    start_pos = int(start_pos_candidates[0])
    signal_fn = next_signal_close5 if cfg.trigger_model == "close5_next_open" else next_signal_touch

    while len(trades) < cfg.max_trades_per_day:
        signal = signal_fn(day, start_pos, cfg, or_high, or_low)
        if signal is None:
            break
        entry_pos, direction, entry = signal
        stop = or_low if direction == "long" else or_high
        risk = entry - stop if direction == "long" else stop - entry
        if risk <= 0:
            start_pos = entry_pos + 1
            continue
        target = entry + cfg.rr * risk if direction == "long" else entry - cfg.rr * risk
        exit_start_pos = entry_pos + 1 if cfg.trigger_model == "close5_next_open" else entry_pos
        exit_info = simulate_exit(day, exit_start_pos, direction, entry, stop, target, risk)
        trades.append({
            "config": cfg.name,
            "symbol": symbol,
            "date": str(date),
            "weekday": pd.Timestamp(date).day_name(),
            "direction": direction,
            "trade_number": len(trades) + 1,
            "or_high": round(or_high, 2),
            "or_low": round(or_low, 2),
            "or_width": round(or_width, 2),
            "or_range_pct": round(range_pct, 3),
            "entry_bucket": int(day.iloc[entry_pos]["bucket"]),
            "entry_time": bucket_label(int(day.iloc[entry_pos]["bucket"])),
            "entry_price": round(float(entry), 2),
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "risk_points": round(float(risk), 2),
            **exit_info,
            "hold_minutes": int(exit_info["exit_bucket"]) - int(day.iloc[entry_pos]["bucket"]) + 1,
        })
        start_pos = int(day.index[day["bucket"] > exit_info["exit_bucket"]].min()) if (day["bucket"] > exit_info["exit_bucket"]).any() else len(day)
        if start_pos >= len(day) or int(day.iloc[start_pos]["bucket"]) > cfg.entry_cutoff_bucket:
            break
    return trades


def metric_block(trades: pd.DataFrame, all_days: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "trading_days": int(len(all_days)),
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "median_r": 0.0,
            "daily_sharpe": 0.0,
            "max_drawdown_r": 0.0,
            "avg_hold_minutes": 0.0,
            "avg_or_range_pct": 0.0,
            "target_rate": 0.0,
            "stop_rate": 0.0,
        }

    r = trades["r_multiple"].astype(float)
    wins = r > 0
    daily = trades.groupby(["symbol", "date"], sort=True)["r_multiple"].sum().reset_index()
    all_day_keys = all_days.copy()
    daily = all_day_keys.merge(daily, on=["symbol", "date"], how="left").fillna({"r_multiple": 0.0})
    day_r = daily["r_multiple"].astype(float)
    equity = day_r.cumsum()
    dd = equity - equity.cummax()
    daily_std = float(day_r.std(ddof=0))
    daily_sharpe = float(day_r.mean() / daily_std * math.sqrt(252)) if daily_std > 0 else 0.0
    return {
        "trades": int(len(trades)),
        "trading_days": int(len(all_days)),
        "trade_days": int(trades[["symbol", "date"]].drop_duplicates().shape[0]),
        "win_rate": round(float(wins.mean() * 100), 2),
        "expectancy_r": round(float(r.mean()), 3),
        "median_r": round(float(r.median()), 3),
        "daily_sharpe": round(daily_sharpe, 3),
        "max_drawdown_r": round(float(dd.min()), 2),
        "avg_hold_minutes": round(float(trades["hold_minutes"].mean()), 2),
        "avg_or_range_pct": round(float(trades["or_range_pct"].mean()), 3),
        "target_rate": round(float((trades["exit_reason"] == "TP").mean() * 100), 2),
        "stop_rate": round(float(trades["exit_reason"].isin(["SL", "ambiguous_stop_first"]).mean() * 100), 2),
    }


def split_metrics(trades: pd.DataFrame, all_days: pd.DataFrame) -> pd.DataFrame:
    if all_days.empty:
        return pd.DataFrame()
    dates = pd.to_datetime(all_days["date"])
    start, end = dates.min(), dates.max()
    span = end - start
    cut1 = start + span * 0.60
    cut2 = start + span * 0.80
    rows = []
    for label, mask in [
        ("train", dates < cut1),
        ("validation", (dates >= cut1) & (dates < cut2)),
        ("out_of_sample", dates >= cut2),
    ]:
        day_part = all_days.loc[mask].copy()
        keys = set(zip(day_part["symbol"], day_part["date"]))
        trade_part = trades[trades.apply(lambda row: (row["symbol"], row["date"]) in keys, axis=1)].copy() if not trades.empty else trades
        rows.append({"split": label, **metric_block(trade_part, day_part)})
    return pd.DataFrame(rows)


def weekday_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for weekday, part in trades.groupby("weekday", sort=False):
        rows.append({
            "weekday": weekday,
            "trades": int(len(part)),
            "win_rate": round(float((part["r_multiple"] > 0).mean() * 100), 2),
            "expectancy_r": round(float(part["r_multiple"].mean()), 3),
            "target_rate": round(float((part["exit_reason"] == "TP").mean() * 100), 2),
            "stop_rate": round(float(part["exit_reason"].isin(["SL", "ambiguous_stop_first"]).mean() * 100), 2),
        })
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    out = pd.DataFrame(rows)
    out["weekday"] = pd.Categorical(out["weekday"], categories=order, ordered=True)
    return out.sort_values("weekday").reset_index(drop=True)


def summarize(trades: pd.DataFrame, all_days: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    split_rows = []
    weekday_rows = []
    yearly_rows = []
    for (config, symbol), part in trades.groupby(["config", "symbol"], sort=True):
        day_part = all_days[all_days["symbol"] == symbol].copy()
        summary_rows.append({"config": config, "symbol": symbol, **metric_block(part, day_part)})
        sm = split_metrics(part, day_part)
        sm.insert(0, "symbol", symbol)
        sm.insert(0, "config", config)
        split_rows.append(sm)
        wm = weekday_metrics(part)
        wm.insert(0, "symbol", symbol)
        wm.insert(0, "config", config)
        weekday_rows.append(wm)
        p = part.copy()
        p["year"] = pd.to_datetime(p["date"]).dt.year
        for year, yp in p.groupby("year", sort=True):
            yearly_rows.append({
                "config": config,
                "symbol": symbol,
                "year": int(year),
                "trades": int(len(yp)),
                "win_rate": round(float((yp["r_multiple"] > 0).mean() * 100), 2),
                "expectancy_r": round(float(yp["r_multiple"].mean()), 3),
                "total_r": round(float(yp["r_multiple"].sum()), 2),
            })
    return (
        pd.DataFrame(summary_rows).sort_values(["daily_sharpe", "expectancy_r"], ascending=False),
        pd.concat(split_rows, ignore_index=True) if split_rows else pd.DataFrame(),
        pd.concat(weekday_rows, ignore_index=True) if weekday_rows else pd.DataFrame(),
        pd.DataFrame(yearly_rows),
    )


def save_report(
    out_dir: Path,
    summary: pd.DataFrame,
    splits: pd.DataFrame,
    weekdays: pd.DataFrame,
    yearly: pd.DataFrame,
    configs: list[ORBConfig],
) -> None:
    best = summary.head(1)
    lines = [
        "# Opening Range Intraday Backtest",
        "",
        "Backtest of the video strategy: mark the first 15-minute high/low, then trade the breakout/breakdown before 10:00 with a 1:2 target and the opposite side of the opening range as stop.",
        "",
        "Important modeling notes:",
        "",
        "- Index spot/futures-style points are tested; option premium P&L is not modeled.",
        "- First range uses buckets 1-15, i.e. 09:15-09:29 IST. Earliest trade is bucket 16, i.e. 09:30 IST.",
        "- Entries after bucket 45, i.e. 09:59 IST, are rejected to respect the no-trade-after-10:00 rule.",
        "- `close5_next_open` waits for a completed 5-minute close outside the range, enters on the next minute open, and starts target/stop checks from the following minute.",
        "- `touch` uses minute OHLC high/low to detect an intrabar breakout and is therefore only an optimistic/ambiguous comparison model.",
        "- Same-minute target/stop ambiguity after entry is counted as stop first.",
        "- Performance is reported in R multiples. If you risk 1% per trade, +0.20R means +0.20% before brokerage/slippage.",
        "",
        "## Tested Configurations",
        "",
        pd.DataFrame([cfg.__dict__ for cfg in configs]).to_markdown(index=False),
        "",
        "## Best Result",
        "",
        best.to_markdown(index=False) if not best.empty else "No trades.",
        "",
        "## Summary",
        "",
        summary.to_markdown(index=False) if not summary.empty else "No trades.",
        "",
        "## Walk-forward Splits",
        "",
        splits.to_markdown(index=False) if not splits.empty else "No split rows.",
        "",
        "## Weekday Breakdown",
        "",
        weekdays.to_markdown(index=False) if not weekdays.empty else "No weekday rows.",
        "",
        "## Yearly Breakdown",
        "",
        yearly.to_markdown(index=False) if not yearly.empty else "No yearly rows.",
        "",
        "## Practical Read",
        "",
        "This setup is clean and easy to execute, but the backtest should be treated as a directional index/futures proxy. For option buying, spread, IV, theta decay, and strike selection can materially change the result.",
    ]
    (out_dir / "final_report.md").write_text("\n".join(lines), encoding="utf-8")


def config_grid(strict_only: bool = False) -> list[ORBConfig]:
    configs: list[ORBConfig] = []
    trigger_models = ["close5_next_open"] if strict_only else ["touch", "close5_next_open"]
    for trigger_model in trigger_models:
        for skip_tuesday in [False, True]:
            for rr in [1.5, 2.0, 3.0]:
                configs.append(ORBConfig(
                    name=f"orb15_{trigger_model}_rr{rr:g}_{'no_tue' if skip_tuesday else 'all_days'}",
                    rr=rr,
                    skip_tuesday=skip_tuesday,
                    trigger_model=trigger_model,
                ))
    return configs


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = args.symbols.split(",") if args.symbols else None
    df = load_index_data(Path(args.index_dir), symbols)
    all_days = df.groupby(["symbol", "date"], as_index=False).agg(buckets=("bucket", "nunique"))
    all_days = all_days[all_days["buckets"] >= 300][["symbol", "date"]].copy()
    all_days["date"] = all_days["date"].astype(str)

    configs = config_grid(strict_only=args.strict_only)
    rows: list[dict] = []
    total_groups = df.groupby(["symbol", "date"], sort=True).ngroups
    for cfg in configs:
        print(f"Running {cfg.name}")
        for idx, ((symbol, date), day) in enumerate(df.groupby(["symbol", "date"], sort=True), start=1):
            if idx == 1 or idx % 500 == 0:
                print(f"  {idx}/{total_groups}")
            rows.extend(backtest_day(str(symbol), date, day, cfg))

    trades = pd.DataFrame(rows)
    if not trades.empty:
        trades = trades.sort_values(["config", "symbol", "date", "trade_number"])
    summary, splits, weekdays, yearly = summarize(trades, all_days) if not trades.empty else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    trades.to_csv(out_dir / "trade_log.csv", index=False)
    summary.to_csv(out_dir / "summary.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    weekdays.to_csv(out_dir / "weekday_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    save_report(out_dir, summary, splits, weekdays, yearly, configs)

    print("Best rows")
    print(summary.head(10).to_string(index=False) if not summary.empty else "No trades")
    print(f"Wrote opening range intraday lab to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest first-15-minute opening range breakout on index minute parquet data.")
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--symbols", default="NIFTY_50,BANK_NIFTY", help="Comma-separated index folders to test.")
    parser.add_argument("--strict-only", action="store_true", help="Run only the no-lookahead close5_next_open model.")
    args = parser.parse_args()
    if args.out_dir is None:
        args.out_dir = str(DEFAULT_STRICT_OUT_DIR if args.strict_only else DEFAULT_OUT_DIR)
    return args


if __name__ == "__main__":
    run(parse_args())
