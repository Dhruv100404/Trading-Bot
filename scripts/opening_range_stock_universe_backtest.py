from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from opening_range_intraday_backtest import ORBConfig, backtest_day


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_VOLUME_GROUPS = ROOT / "data" / "volume_groups.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "opening_range_stock_universe_lab"


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def load_symbols(volume_groups_path: Path, groups: list[str]) -> set[str]:
    data = json.loads(volume_groups_path.read_text(encoding="utf-8"))
    volume_groups = data.get("volume_groups", {})
    selected: set[str] = set()
    for group_name, symbols in volume_groups.items():
        if any(group_name.upper().startswith(group.upper()) for group in groups):
            selected.update(str(symbol).upper() for symbol in symbols)
    if not selected:
        raise ValueError(f"No symbols matched groups {groups} in {volume_groups_path}")
    return selected


def stock_configs(core_only: bool = False) -> list[ORBConfig]:
    core = [
        ORBConfig(name="stock_orb15_touch_rr2_all_days", rr=2.0, trigger_model="touch"),
        ORBConfig(name="stock_orb15_touch_rr2_no_tue", rr=2.0, trigger_model="touch", skip_tuesday=True),
    ]
    if core_only:
        return core
    return [
        *core,
        ORBConfig(name="stock_orb15_touch_rr3_all_days", rr=3.0, trigger_model="touch"),
        ORBConfig(name="stock_orb15_close5_next_open_rr2_all_days", rr=2.0, trigger_model="close5_next_open"),
        ORBConfig(name="stock_orb15_close5_next_open_rr2_no_tue", rr=2.0, trigger_model="close5_next_open", skip_tuesday=True),
    ]


def clean_month(path: Path, symbols: set[str], min_price: float) -> pd.DataFrame:
    columns = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume"]
    df = pq.read_table(path, columns=columns).to_pandas()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df = df[df["symbol"].isin(symbols)]
    df["bucket"] = pd.to_numeric(df["bucket"], errors="coerce").fillna(0).astype(int)
    df = df[(df["bucket"] >= 1) & (df["bucket"] <= 375)].copy()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close"])
    df = df[(df["open"] >= min_price) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df[df["high"] >= df[["open", "close"]].max(axis=1)]
    df = df[df["low"] <= df[["open", "close"]].min(axis=1)]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values(["symbol", "date", "bucket"]).drop_duplicates(["symbol", "date", "bucket"], keep="last")


def metric_block(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
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
        "trade_days": int(trades[["date"]].drop_duplicates().shape[0]),
        "symbols": int(trades["symbol"].nunique()),
        "win_rate": round(float((r > 0).mean() * 100), 2),
        "expectancy_r": round(float(r.mean()), 3),
        "median_r": round(float(r.median()), 3),
        "trade_sharpe": round(float(r.mean() / std * math.sqrt(252)) if std > 0 else 0.0, 3),
        "target_rate": round(float((trades["exit_reason"] == "TP").mean() * 100), 2),
        "stop_rate": round(float(trades["exit_reason"].isin(["SL", "ambiguous_stop_first"]).mean() * 100), 2),
        "avg_hold_minutes": round(float(trades["hold_minutes"].mean()), 2),
        "avg_or_range_pct": round(float(trades["or_range_pct"].mean()), 3),
    }


def daily_portfolio_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"daily_sharpe": 0.0, "max_drawdown_r": 0.0, "avg_daily_r": 0.0, "avg_trades_per_trade_day": 0.0}
    daily = trades.groupby("date", sort=True)["r_multiple"].sum()
    std = float(daily.std(ddof=0))
    equity = daily.cumsum()
    dd = equity - equity.cummax()
    return {
        "daily_sharpe": round(float(daily.mean() / std * math.sqrt(252)) if std > 0 else 0.0, 3),
        "max_drawdown_r": round(float(dd.min()), 2),
        "avg_daily_r": round(float(daily.mean()), 3),
        "avg_trades_per_trade_day": round(float(trades.groupby("date").size().mean()), 2),
    }


def summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    split_rows = []
    yearly_rows = []
    symbol_rows = []
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    trades = trades.copy()
    trades["date_ts"] = pd.to_datetime(trades["date"])
    start, end = trades["date_ts"].min(), trades["date_ts"].max()
    span = end - start
    cut1 = start + span * 0.60
    cut2 = start + span * 0.80
    for config, part in trades.groupby("config", sort=True):
        summary_rows.append({"config": config, **metric_block(part), **daily_portfolio_metrics(part)})
        for label, mask in [
            ("train", part["date_ts"] < cut1),
            ("validation", (part["date_ts"] >= cut1) & (part["date_ts"] < cut2)),
            ("out_of_sample", part["date_ts"] >= cut2),
        ]:
            split_part = part.loc[mask]
            split_rows.append({"config": config, "split": label, **metric_block(split_part), **daily_portfolio_metrics(split_part)})
        p = part.copy()
        p["year"] = p["date_ts"].dt.year
        for year, yp in p.groupby("year", sort=True):
            yearly_rows.append({
                "config": config,
                "year": int(year),
                **metric_block(yp),
                "total_r": round(float(yp["r_multiple"].sum()), 2),
            })
        for symbol, sp in part.groupby("symbol", sort=True):
            if len(sp) >= 40:
                symbol_rows.append({"config": config, "symbol": symbol, **metric_block(sp), "total_r": round(float(sp["r_multiple"].sum()), 2)})
    summary = pd.DataFrame(summary_rows).sort_values(["daily_sharpe", "expectancy_r"], ascending=False)
    splits = pd.DataFrame(split_rows)
    yearly = pd.DataFrame(yearly_rows)
    symbols = pd.DataFrame(symbol_rows).sort_values(["config", "expectancy_r"], ascending=[True, False])
    return summary, splits, yearly, symbols


def write_report(out_dir: Path, summary: pd.DataFrame, splits: pd.DataFrame, yearly: pd.DataFrame, symbols: pd.DataFrame, groups: list[str], min_price: float) -> None:
    lines = [
        "# Opening Range Stock Universe Backtest",
        "",
        f"Universe: `{', '.join(groups)}` volume groups, minimum price Rs {min_price:g}.",
        "",
        "This applies the same first-15-minute opening range breakout to liquid stocks. Results are signal-quality R multiples, not a fully position-sized portfolio.",
        "",
        "## Summary",
        "",
        summary.to_markdown(index=False) if not summary.empty else "No trades.",
        "",
        "## Walk-forward Splits",
        "",
        splits.to_markdown(index=False) if not splits.empty else "No split rows.",
        "",
        "## Yearly",
        "",
        yearly.to_markdown(index=False) if not yearly.empty else "No yearly rows.",
        "",
        "## Top Symbols By Config",
        "",
    ]
    if not symbols.empty:
        top = symbols.groupby("config", group_keys=False).head(15)
        lines.append(top.to_markdown(index=False))
    else:
        lines.append("No symbol rows.")
    lines.extend([
        "",
        "## Bottom Symbols By Config",
        "",
    ])
    if not symbols.empty:
        bottom = symbols.sort_values(["config", "expectancy_r"], ascending=[True, True]).groupby("config", group_keys=False).head(15)
        lines.append(bottom.to_markdown(index=False))
    else:
        lines.append("No symbol rows.")
    lines.extend([
        "",
        "## Practical Read",
        "",
        "Stocks need an additional selection layer. Running every liquid breakout mechanically can create too many simultaneous trades and too much execution load.",
    ])
    (out_dir / "final_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = [part.strip() for part in args.volume_groups.split(",") if part.strip()]
    symbols = load_symbols(Path(args.volume_groups_file), groups)
    configs = stock_configs(core_only=args.core_only)
    all_trades: list[dict] = []
    files = monthly_files(Path(args.parquet_dir))
    for file_idx, path in enumerate(files, start=1):
        print(f"{file_idx}/{len(files)} {path.name}", flush=True)
        month = clean_month(path, symbols, args.min_price)
        if month.empty:
            continue
        for (symbol, date), day in month.groupby(["symbol", "date"], sort=True):
            if day["bucket"].nunique() < 300:
                continue
            for cfg in configs:
                all_trades.extend(backtest_day(str(symbol), date, day, cfg))
    trades = pd.DataFrame(all_trades)
    if not trades.empty:
        trades = trades.sort_values(["config", "date", "symbol", "trade_number"])
    summary, splits, yearly, symbol_summary = summarize(trades)
    trades.to_csv(out_dir / "trade_log.csv", index=False)
    summary.to_csv(out_dir / "summary.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    symbol_summary.to_csv(out_dir / "symbol_summary.csv", index=False)
    write_report(out_dir, summary, splits, yearly, symbol_summary, groups, args.min_price)
    print("Best rows")
    print(summary.head(10).to_string(index=False) if not summary.empty else "No trades")
    print(f"Wrote stock universe lab to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest first-15-minute ORB on liquid stock minute parquet data.")
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR))
    parser.add_argument("--volume-groups-file", default=str(DEFAULT_VOLUME_GROUPS))
    parser.add_argument("--volume-groups", default="MEGA,LARGE")
    parser.add_argument("--min-price", type=float, default=50.0)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--core-only", action="store_true", help="Run only the core 1:2 touch-breakout variants.")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
