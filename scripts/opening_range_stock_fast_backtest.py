from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_VOLUME_GROUPS = ROOT / "data" / "volume_groups.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "opening_range_stock_universe_lab"
DEFAULT_STRICT_OUT_DIR = ROOT / "docs" / "opening_range_stock_nolookahead_lab"


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def bucket_label(bucket: int) -> str:
    minutes = 9 * 60 + 15 + int(bucket) - 1
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load_symbols(path: Path, groups: list[str]) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    volume_groups = data.get("volume_groups", {})
    selected: set[str] = set()
    for group_name, symbols in volume_groups.items():
        if any(group_name.upper().startswith(group.upper()) for group in groups):
            selected.update(str(symbol).upper() for symbol in symbols)
    if not selected:
        raise ValueError(f"No symbols matched groups {groups}")
    return selected


def read_month(path: Path, symbols: set[str], min_price: float) -> pd.DataFrame:
    columns = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume"]
    try:
        table = pq.read_table(
            path,
            columns=columns,
            filters=[("symbol", "in", sorted(symbols)), ("bucket", ">=", 1), ("bucket", "<=", 375)],
        )
    except Exception:
        table = pq.read_table(path, columns=columns)
    df = table.to_pandas()
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df = df[df["symbol"].isin(symbols)]
    df["bucket"] = pd.to_numeric(df["bucket"], errors="coerce").fillna(0).astype(np.int16)
    df = df[(df["bucket"] >= 1) & (df["bucket"] <= 375)]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close"])
    df = df[(df["open"] >= min_price) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df[df["high"] >= df[["open", "close"]].max(axis=1)]
    df = df[df["low"] <= df[["open", "close"]].min(axis=1)]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values(["symbol", "date", "bucket"]).drop_duplicates(["symbol", "date", "bucket"], keep="last")


def opening_ranges(df: pd.DataFrame) -> pd.DataFrame:
    opening = df[(df["bucket"] >= 1) & (df["bucket"] <= 15)]
    if opening.empty:
        return pd.DataFrame()
    ranges = (
        opening.groupby(["symbol", "date"], sort=False)
        .agg(
            or_high=("high", "max"),
            or_low=("low", "min"),
            or_open=("open", "first"),
            opening_buckets=("bucket", "nunique"),
            opening_volume=("volume", "sum"),
        )
        .reset_index()
    )
    ranges = ranges[(ranges["opening_buckets"] >= 15) & (ranges["or_high"] > ranges["or_low"]) & (ranges["or_open"] > 0)]
    ranges["or_width"] = ranges["or_high"] - ranges["or_low"]
    ranges["or_range_pct"] = ranges["or_width"] / ranges["or_open"] * 100
    return ranges


def first_touch_entries(df: pd.DataFrame, ranges: pd.DataFrame, rr: float, min_range_pct: float, max_range_pct: float) -> pd.DataFrame:
    ranges = ranges[(ranges["or_range_pct"] >= min_range_pct) & (ranges["or_range_pct"] <= max_range_pct)].copy()
    if ranges.empty:
        return pd.DataFrame()
    scan = df[(df["bucket"] >= 16) & (df["bucket"] <= 45)].merge(ranges, on=["symbol", "date"], how="inner")
    if scan.empty:
        return pd.DataFrame()
    scan["break_high"] = scan["high"] >= scan["or_high"]
    scan["break_low"] = scan["low"] <= scan["or_low"]
    scan = scan[scan["break_high"] ^ scan["break_low"]].copy()
    if scan.empty:
        return pd.DataFrame()
    scan = scan.sort_values(["symbol", "date", "bucket"]).drop_duplicates(["symbol", "date"], keep="first")
    scan["direction"] = np.where(scan["break_high"], "long", "short")
    scan["entry_price"] = np.where(scan["direction"] == "long", scan["or_high"], scan["or_low"])
    scan["stop"] = np.where(scan["direction"] == "long", scan["or_low"], scan["or_high"])
    scan["risk_points"] = np.where(scan["direction"] == "long", scan["entry_price"] - scan["stop"], scan["stop"] - scan["entry_price"])
    scan = scan[scan["risk_points"] > 0].copy()
    scan["target"] = np.where(
        scan["direction"] == "long",
        scan["entry_price"] + rr * scan["risk_points"],
        scan["entry_price"] - rr * scan["risk_points"],
    )
    scan = scan.rename(columns={"bucket": "entry_bucket"})
    keep = [
        "symbol", "date", "direction", "entry_bucket", "entry_price", "stop", "target", "risk_points",
        "or_high", "or_low", "or_width", "or_range_pct", "opening_volume",
    ]
    scan = scan[keep].reset_index(drop=True)
    scan["trade_id"] = np.arange(len(scan), dtype=np.int64)
    return scan


def close5_next_open_entries(df: pd.DataFrame, ranges: pd.DataFrame, rr: float, min_range_pct: float, max_range_pct: float) -> pd.DataFrame:
    ranges = ranges[(ranges["or_range_pct"] >= min_range_pct) & (ranges["or_range_pct"] <= max_range_pct)].copy()
    if ranges.empty:
        return pd.DataFrame()

    scan = df[(df["bucket"] >= 16) & (df["bucket"] <= 40)].merge(ranges, on=["symbol", "date"], how="inner")
    if scan.empty:
        return pd.DataFrame()
    scan["confirm_group"] = ((scan["bucket"] - 16) // 5).astype(np.int8)
    bars = (
        scan.sort_values(["symbol", "date", "bucket"])
        .groupby(["symbol", "date", "confirm_group"], sort=False)
        .agg(
            end_bucket=("bucket", "max"),
            bar_close=("close", "last"),
            bar_buckets=("bucket", "nunique"),
            or_high=("or_high", "first"),
            or_low=("or_low", "first"),
            or_width=("or_width", "first"),
            or_range_pct=("or_range_pct", "first"),
            opening_volume=("opening_volume", "first"),
        )
        .reset_index()
    )
    bars = bars[bars["bar_buckets"] >= 5].copy()
    if bars.empty:
        return pd.DataFrame()

    bars["break_high"] = bars["bar_close"] > bars["or_high"]
    bars["break_low"] = bars["bar_close"] < bars["or_low"]
    bars = bars[bars["break_high"] ^ bars["break_low"]].copy()
    if bars.empty:
        return pd.DataFrame()
    bars = bars.sort_values(["symbol", "date", "end_bucket"]).drop_duplicates(["symbol", "date"], keep="first")
    bars["direction"] = np.where(bars["break_high"], "long", "short")
    bars["entry_bucket"] = bars["end_bucket"].astype(np.int16) + 1

    next_open = df[["symbol", "date", "bucket", "open"]].rename(columns={"bucket": "entry_bucket", "open": "entry_price"})
    entries = bars.merge(next_open, on=["symbol", "date", "entry_bucket"], how="inner")
    if entries.empty:
        return pd.DataFrame()
    entries["stop"] = np.where(entries["direction"] == "long", entries["or_low"], entries["or_high"])
    entries["risk_points"] = np.where(
        entries["direction"] == "long",
        entries["entry_price"] - entries["stop"],
        entries["stop"] - entries["entry_price"],
    )
    entries = entries[entries["risk_points"] > 0].copy()
    if entries.empty:
        return pd.DataFrame()
    entries["target"] = np.where(
        entries["direction"] == "long",
        entries["entry_price"] + rr * entries["risk_points"],
        entries["entry_price"] - rr * entries["risk_points"],
    )
    keep = [
        "symbol", "date", "direction", "entry_bucket", "entry_price", "stop", "target", "risk_points",
        "or_high", "or_low", "or_width", "or_range_pct", "opening_volume",
    ]
    entries = entries[keep].reset_index(drop=True)
    entries["trade_id"] = np.arange(len(entries), dtype=np.int64)
    return entries


def exits_for_entries(df: pd.DataFrame, entries: pd.DataFrame, rr: float, include_entry_bucket: bool = False) -> pd.DataFrame:
    if entries.empty:
        return entries
    post_cols = ["symbol", "date", "bucket", "high", "low", "close"]
    post = df[post_cols].merge(
        entries[["trade_id", "symbol", "date", "direction", "entry_bucket", "entry_price", "stop", "target", "risk_points"]],
        on=["symbol", "date"],
        how="inner",
    )
    if include_entry_bucket:
        post = post[post["bucket"] >= post["entry_bucket"]].copy()
    else:
        post = post[post["bucket"] > post["entry_bucket"]].copy()
    if post.empty:
        return entries

    is_long = post["direction"] == "long"
    post["hit_stop"] = np.where(is_long, post["low"] <= post["stop"], post["high"] >= post["stop"])
    post["hit_target"] = np.where(is_long, post["high"] >= post["target"], post["low"] <= post["target"])
    events = post[post["hit_stop"] | post["hit_target"]].sort_values(["trade_id", "bucket"]).drop_duplicates("trade_id")

    if not events.empty:
        events["exit_reason"] = np.where(events["hit_stop"], "SL", "TP")
        events["exit_price"] = np.where(events["hit_stop"], events["stop"], events["target"])
        events["r_multiple"] = np.where(events["hit_stop"], -1.0, rr)
        event_exits = events[["trade_id", "bucket", "exit_price", "exit_reason", "r_multiple"]].rename(columns={"bucket": "exit_bucket"})
    else:
        event_exits = pd.DataFrame(columns=["trade_id", "exit_bucket", "exit_price", "exit_reason", "r_multiple"])

    missing_ids = set(entries["trade_id"]) - set(event_exits["trade_id"])
    if missing_ids:
        eod = (
            post[post["trade_id"].isin(missing_ids)]
            .sort_values(["trade_id", "bucket"])
            .groupby("trade_id", sort=False)
            .tail(1)
            .copy()
        )
        eod["exit_reason"] = "EOD"
        eod["exit_price"] = eod["close"]
        eod["r_multiple"] = np.where(
            eod["direction"] == "long",
            (eod["exit_price"] - eod["entry_price"]) / eod["risk_points"],
            (eod["entry_price"] - eod["exit_price"]) / eod["risk_points"],
        )
        eod_exits = eod[["trade_id", "bucket", "exit_price", "exit_reason", "r_multiple"]].rename(columns={"bucket": "exit_bucket"})
        event_exits = pd.concat([event_exits, eod_exits], ignore_index=True)

    out = entries.merge(event_exits, on="trade_id", how="left")
    out["entry_time"] = out["entry_bucket"].map(bucket_label)
    out["exit_time"] = out["exit_bucket"].map(bucket_label)
    out["hold_minutes"] = out["exit_bucket"].astype(int) - out["entry_bucket"].astype(int) + 1
    out["weekday"] = pd.to_datetime(out["date"]).dt.day_name()
    out["trade_number"] = 1
    for col in ["entry_price", "stop", "target", "risk_points", "or_high", "or_low", "or_width", "or_range_pct", "exit_price", "r_multiple"]:
        out[col] = out[col].astype(float).round(4)
    return out


def month_trades(path: Path, symbols: set[str], args: argparse.Namespace) -> pd.DataFrame:
    df = read_month(path, symbols, args.min_price)
    if df.empty:
        return pd.DataFrame()
    ranges = opening_ranges(df)
    if args.trigger_model == "close5_next_open":
        entries = close5_next_open_entries(df, ranges, rr=args.rr, min_range_pct=args.min_range_pct, max_range_pct=args.max_range_pct)
    else:
        entries = first_touch_entries(df, ranges, rr=args.rr, min_range_pct=args.min_range_pct, max_range_pct=args.max_range_pct)
    trades = exits_for_entries(df, entries, rr=args.rr, include_entry_bucket=args.include_entry_bucket)
    if trades.empty:
        return trades
    trades.insert(0, "config", f"stock_fast_orb15_{args.trigger_model}_rr{args.rr:g}_all_days")
    return trades


def metric_block(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "symbols": 0,
            "trade_days": 0,
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "median_r": 0.0,
            "trade_sharpe": 0.0,
            "target_rate": 0.0,
            "stop_rate": 0.0,
            "avg_hold_minutes": 0.0,
            "avg_or_range_pct": 0.0,
        }
    r = trades["r_multiple"].astype(float)
    std = float(r.std(ddof=0))
    return {
        "trades": int(len(trades)),
        "symbols": int(trades["symbol"].nunique()),
        "trade_days": int(trades["date"].nunique()),
        "win_rate": round(float((r > 0).mean() * 100), 2),
        "expectancy_r": round(float(r.mean()), 3),
        "median_r": round(float(r.median()), 3),
        "trade_sharpe": round(float(r.mean() / std * math.sqrt(252)) if std > 0 else 0.0, 3),
        "target_rate": round(float((trades["exit_reason"] == "TP").mean() * 100), 2),
        "stop_rate": round(float((trades["exit_reason"] == "SL").mean() * 100), 2),
        "avg_hold_minutes": round(float(trades["hold_minutes"].mean()), 2),
        "avg_or_range_pct": round(float(trades["or_range_pct"].mean()), 3),
    }


def daily_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"daily_sharpe": 0.0, "max_drawdown_r": 0.0, "avg_daily_r": 0.0, "avg_trades_per_day": 0.0}
    daily = trades.groupby("date", sort=True)["r_multiple"].sum()
    std = float(daily.std(ddof=0))
    equity = daily.cumsum()
    dd = equity - equity.cummax()
    return {
        "daily_sharpe": round(float(daily.mean() / std * math.sqrt(252)) if std > 0 else 0.0, 3),
        "max_drawdown_r": round(float(dd.min()), 2),
        "avg_daily_r": round(float(daily.mean()), 3),
        "avg_trades_per_day": round(float(trades.groupby("date").size().mean()), 2),
    }


def summarize(trades: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    split_rows = []
    weekday_rows = []
    yearly_rows = []
    symbol_rows = []
    base_config = f"stock_fast_orb15_{args.trigger_model}_rr{args.rr:g}"
    variants = [(f"{base_config}_all_days", trades)]
    variants.append((f"{base_config}_no_tue", trades[trades["weekday"] != "Tuesday"].copy()))
    for config, part in variants:
        p = part.copy()
        p["config"] = config
        rows.append({"config": config, **metric_block(p), **daily_metrics(p)})
        dates = pd.to_datetime(p["date"])
        if not p.empty:
            start, end = dates.min(), dates.max()
            span = end - start
            cut1 = start + span * 0.60
            cut2 = start + span * 0.80
            for label, mask in [
                ("train", dates < cut1),
                ("validation", (dates >= cut1) & (dates < cut2)),
                ("out_of_sample", dates >= cut2),
            ]:
                sp = p.loc[mask]
                split_rows.append({"config": config, "split": label, **metric_block(sp), **daily_metrics(sp)})
            for weekday, wp in p.groupby("weekday", sort=False):
                weekday_rows.append({"config": config, "weekday": weekday, **metric_block(wp)})
            y = p.copy()
            y["year"] = pd.to_datetime(y["date"]).dt.year
            for year, yp in y.groupby("year", sort=True):
                yearly_rows.append({"config": config, "year": int(year), **metric_block(yp), "total_r": round(float(yp["r_multiple"].sum()), 2)})
            for symbol, sym_part in p.groupby("symbol", sort=True):
                if len(sym_part) >= 50:
                    symbol_rows.append({"config": config, "symbol": symbol, **metric_block(sym_part), "total_r": round(float(sym_part["r_multiple"].sum()), 2)})
    summary = pd.DataFrame(rows).sort_values(["daily_sharpe", "expectancy_r"], ascending=False)
    splits = pd.DataFrame(split_rows)
    weekdays = pd.DataFrame(weekday_rows)
    if not weekdays.empty:
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        weekdays["weekday"] = pd.Categorical(weekdays["weekday"], categories=order, ordered=True)
        weekdays = weekdays.sort_values(["config", "weekday"])
    yearly = pd.DataFrame(yearly_rows)
    symbols = pd.DataFrame(symbol_rows)
    if not symbols.empty:
        symbols = symbols.sort_values(["config", "expectancy_r"], ascending=[True, False])
    return summary, splits, weekdays, yearly, symbols


def write_report(out_dir: Path, summary: pd.DataFrame, splits: pd.DataFrame, weekdays: pd.DataFrame, yearly: pd.DataFrame, symbols: pd.DataFrame, args: argparse.Namespace) -> None:
    if args.trigger_model == "close5_next_open":
        trigger_note = "Strict no-lookahead test: first 15-minute high/low, completed 5-minute close outside the range, entry on the next minute open, stop at opposite opening-range side, target 1:2."
        ambiguity_note = "Entry-minute target/stop checks are skipped; later same-minute target/stop ambiguity is treated as stop first."
    else:
        trigger_note = "Fast core test of the video rule on liquid stocks: first 15-minute high/low, first touch breakout before 10:00, stop at opposite opening-range side, target 1:2."
        ambiguity_note = "Touch entries use minute OHLC high/low to infer an intrabar breakout, so this model is optimistic/ambiguous and should not be treated as the final no-lookahead result."
    lines = [
        "# Opening Range Stock Universe Fast Backtest",
        "",
        f"Universe: `{args.volume_groups}` from `{args.volume_groups_file}`, minimum price Rs {args.min_price:g}.",
        "",
        trigger_note,
        "",
        "Modeling notes:",
        "",
        "- This is stock price movement in R multiples, not option premium P&L.",
        "- Only the first breakout trade per stock per day is tested in this fast stock-universe pass.",
        f"- {ambiguity_note}",
        "- For strict mode, confirmation groups are buckets 16-40, so the latest valid next-open entry is bucket 41, around 09:55 IST.",
        "- Daily portfolio Sharpe sums every triggered stock signal per day, so it measures raw universe signal quality rather than a capped-position trading book.",
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
        "## Top Symbols",
        "",
        symbols.groupby("config", group_keys=False).head(20).to_markdown(index=False) if not symbols.empty else "No symbol rows.",
        "",
        "## Bottom Symbols",
        "",
        symbols.sort_values(["config", "expectancy_r"], ascending=[True, True]).groupby("config", group_keys=False).head(20).to_markdown(index=False) if not symbols.empty else "No symbol rows.",
    ]
    (out_dir / "final_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols(Path(args.volume_groups_file), [part.strip() for part in args.volume_groups.split(",") if part.strip()])
    files = monthly_files(Path(args.parquet_dir))
    if args.max_files:
        files = files[: args.max_files]
    trade_frames = []
    for idx, path in enumerate(files, start=1):
        print(f"{idx}/{len(files)} {path.name}", flush=True)
        trades = month_trades(path, symbols, args)
        if not trades.empty:
            trade_frames.append(trades)
    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    if not all_trades.empty:
        all_trades = all_trades.sort_values(["date", "symbol", "entry_bucket"])
    summary, splits, weekdays, yearly, symbols_summary = summarize(all_trades, args)
    all_trades.to_csv(out_dir / "trade_log.csv", index=False)
    summary.to_csv(out_dir / "summary.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    weekdays.to_csv(out_dir / "weekday_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    symbols_summary.to_csv(out_dir / "symbol_summary.csv", index=False)
    write_report(out_dir, summary, splits, weekdays, yearly, symbols_summary, args)
    print("Best rows")
    print(summary.to_string(index=False) if not summary.empty else "No trades")
    print(f"Wrote fast stock ORB lab to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast vectorized stock-universe ORB backtest.")
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR))
    parser.add_argument("--volume-groups-file", default=str(DEFAULT_VOLUME_GROUPS))
    parser.add_argument("--volume-groups", default="MEGA,LARGE")
    parser.add_argument("--min-price", type=float, default=50.0)
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--min-range-pct", type=float, default=0.0)
    parser.add_argument("--max-range-pct", type=float, default=10.0)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--trigger-model", choices=["touch", "close5_next_open"], default="close5_next_open")
    parser.add_argument("--include-entry-bucket", action="store_true", help="Include the entry minute in exit checks; leave off for strict no-lookahead mode.")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    if args.out_dir is None:
        args.out_dir = str(DEFAULT_STRICT_OUT_DIR if args.trigger_model == "close5_next_open" and not args.include_entry_bucket else DEFAULT_OUT_DIR)
    return args


if __name__ == "__main__":
    run(parse_args())
