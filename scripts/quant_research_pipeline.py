from __future__ import annotations

import argparse
import json
import math
import os
import sys
import textwrap
import time
import urllib.request
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except Exception:  # pragma: no cover
    pq = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_OUT_DIR = ROOT / "docs" / "quant_research_outputs"
CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    family: str
    entry_rule: str
    stop_atr: float
    target_atr: float
    max_hold_days: int
    signal_fn: Callable[[pd.DataFrame], pd.Series]
    thesis: str = ""
    required_columns: tuple[str, ...] = ()
    timeframe: str = "daily"
    direction: str = "long"
    invalidation: str = ""
    execution_caveat: str = ""


COST_SCENARIOS = {
    "optimistic": {"cost_bps_side": 4.0, "slippage_bps_side": 3.0},
    "base": {"cost_bps_side": 8.0, "slippage_bps_side": 5.0},
    "stress": {"cost_bps_side": 8.0, "slippage_bps_side": 15.0},
}


def clickhouse_json(sql: str, timeout: int = 240) -> dict:
    req = urllib.request.Request(CLICKHOUSE_URL, data=sql.encode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def clickhouse_df(sql: str, timeout: int = 300) -> pd.DataFrame:
    req = urllib.request.Request(CLICKHOUSE_URL, data=(sql + "\nFORMAT CSVWithNames").encode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
    return pd.read_csv(StringIO(content))


def inspect_parquet_files(parquet_dir: Path) -> pd.DataFrame:
    rows = []
    files = sorted(parquet_dir.rglob("*.parquet"))
    for path in files:
        info = {
            "path": str(path.relative_to(parquet_dir)),
            "bytes": path.stat().st_size,
            "schema": "",
            "num_rows": None,
            "row_groups": None,
            "kind": classify_file(path.relative_to(parquet_dir).as_posix()),
            "error": None,
        }
        if pq is None:
            info["error"] = "pyarrow unavailable"
        else:
            try:
                pf = pq.ParquetFile(path)
                info["num_rows"] = pf.metadata.num_rows
                info["row_groups"] = pf.metadata.num_row_groups
                info["schema"] = "; ".join(f"{field.name}:{field.type}" for field in pf.schema_arrow)
            except Exception as exc:
                info["error"] = str(exc)
        rows.append(info)
    return pd.DataFrame(rows)


def classify_file(relative: str) -> str:
    if relative.startswith("parquet_of_index/"):
        return "index_intraday"
    if relative.startswith("daily/"):
        return "daily_snapshot"
    name = Path(relative).name
    if name.startswith("candles_20") and name.endswith(".parquet"):
        return "stock_intraday_monthly"
    if name.endswith("_full.parquet"):
        return "stock_intraday_duplicate_bundle"
    return "unknown"


def root_monthly_files() -> list[Path]:
    return sorted(DEFAULT_PARQUET_DIR.glob("candles_20*.parquet"))


def ch_file_path(path: Path) -> str:
    return path.relative_to(DEFAULT_PARQUET_DIR).as_posix().replace("'", "''")


def dataset_quality_summary() -> dict:
    totals: dict[str, object] = {
        "rows": 0,
        "symbols": 0,
        "min_date": None,
        "max_date": None,
        "min_bucket": None,
        "max_bucket": None,
        "missing_symbol_rows": 0,
        "missing_date_rows": 0,
        "missing_ohlc_rows": 0,
        "nonpositive_ohlc_rows": 0,
        "bad_ohlc_rows": 0,
        "missing_volume_rows": 0,
        "zero_volume_rows": 0,
        "duplicate_symbol_date_bucket_rows": 0,
    }
    symbols: set[str] = set()
    for path in root_monthly_files():
        rel = ch_file_path(path)
        print(f"Quality scan {rel}")
        sql = f"""
        WITH src AS (
            SELECT *
            FROM file('parquets/{rel}', Parquet)
        )
        SELECT
            count() AS rows,
            groupUniqArray(symbol) AS symbols_arr,
            min(toDate(date)) AS min_date,
            max(toDate(date)) AS max_date,
            min(bucket) AS min_bucket,
            max(bucket) AS max_bucket,
            countIf(symbol IS NULL OR symbol = '') AS missing_symbol_rows,
            countIf(date IS NULL OR date = '') AS missing_date_rows,
            countIf(open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL) AS missing_ohlc_rows,
            countIf(open <= 0 OR high <= 0 OR low <= 0 OR close <= 0) AS nonpositive_ohlc_rows,
            countIf(high < low OR high < open OR high < close OR low > open OR low > close) AS bad_ohlc_rows,
            countIf(volume IS NULL) AS missing_volume_rows,
            countIf(volume = 0) AS zero_volume_rows,
            count() - uniqExact(symbol, date, bucket) AS duplicate_symbol_date_bucket_rows
        FROM src
        FORMAT JSON
        """
        row = clickhouse_json(sql, timeout=180)["data"][0]
        totals["rows"] += int(row["rows"])
        symbols.update(s for s in row.get("symbols_arr", []) if s)
        for key in [
            "missing_symbol_rows", "missing_date_rows", "missing_ohlc_rows",
            "nonpositive_ohlc_rows", "bad_ohlc_rows", "missing_volume_rows",
            "zero_volume_rows", "duplicate_symbol_date_bucket_rows",
        ]:
            totals[key] += int(row[key])
        for key, fn in [("min_date", min), ("max_date", max), ("min_bucket", min), ("max_bucket", max)]:
            val = row[key]
            if val is None:
                continue
            totals[key] = val if totals[key] is None else fn(totals[key], val)
    totals["symbols"] = len(symbols)
    return totals


def dataset_quality_summary_single_query() -> dict:
    sql = """
    WITH src AS (
        SELECT *
        FROM file('parquets/candles_20*.parquet', Parquet)
    )
    SELECT
        count() AS rows,
        uniqExact(symbol) AS symbols,
        min(toDate(date)) AS min_date,
        max(toDate(date)) AS max_date,
        min(bucket) AS min_bucket,
        max(bucket) AS max_bucket,
        countIf(symbol IS NULL OR symbol = '') AS missing_symbol_rows,
        countIf(date IS NULL OR date = '') AS missing_date_rows,
        countIf(open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL) AS missing_ohlc_rows,
        countIf(open <= 0 OR high <= 0 OR low <= 0 OR close <= 0) AS nonpositive_ohlc_rows,
        countIf(high < low OR high < open OR high < close OR low > open OR low > close) AS bad_ohlc_rows,
        countIf(volume IS NULL) AS missing_volume_rows,
        countIf(volume = 0) AS zero_volume_rows,
        count() - uniqExact(symbol, date, bucket) AS duplicate_symbol_date_bucket_rows
    FROM src
    FORMAT JSON
    """
    return clickhouse_json(sql, timeout=300)["data"][0]


def write_dataset_map(out_dir: Path, parquet_dir: Path, schema_df: pd.DataFrame, quality: dict) -> None:
    kind_counts = (
        schema_df.groupby("kind", dropna=False)
        .agg(files=("path", "count"), rows=("num_rows", "sum"), bytes=("bytes", "sum"))
        .reset_index()
        .sort_values("files", ascending=False)
        if not schema_df.empty
        else pd.DataFrame()
    )
    schema_examples = (
        schema_df[["kind", "schema"]]
        .drop_duplicates()
        .head(20)
        .to_dict("records")
        if not schema_df.empty
        else []
    )
    kind_table = kind_counts.to_markdown(index=False) if not kind_counts.empty else "No parquet files found."
    schema_lines = "\n".join(f"- `{row['kind']}`: `{row['schema']}`" for row in schema_examples)
    text = f"""# Dataset Map

Generated: {pd.Timestamp.now()}

## Files Scanned

Parquet directory: `{parquet_dir}`

{kind_table}

## Main Research Dataset

- File family: `candles_20*.parquet`
- Rows: {quality.get('rows')}
- Symbols: {quality.get('symbols')}
- Date range: {quality.get('min_date')} to {quality.get('max_date')}
- Bucket range: {quality.get('min_bucket')} to {quality.get('max_bucket')}
- Asset class inference: cash equities / stocks, because the files are symbol-level OHLCV candles. Confirm exchange and universe construction before live use.
- Timezone status: inferred local exchange session from `date` plus `bucket`; no explicit timezone column was detected.
- Timestamp mapping: `date` plus intraday `bucket`.
- OHLCV mapping: `open`, `high`, `low`, `close`, `volume`.
- Extra columns: `buy_ratio`, `cum_volume`, `vwap`, `vol_rate` when present.
- Bid/ask status: no bid or ask columns detected.
- Adjusted/raw status: unknown from schema alone. Treat corporate action adjustment as a risk until verified.
- Shorting realism: disabled by default for research promotion. Any short result should be labeled hypothetical unless borrow/shortability and product type are known.
- Survivorship risk: likely, unless the parquet universe is proven point-in-time.

## Schema Examples

{schema_lines}

## Data Quality Snapshot

- Missing symbol rows: {quality.get('missing_symbol_rows')}
- Missing date rows: {quality.get('missing_date_rows')}
- Missing OHLC rows: {quality.get('missing_ohlc_rows')}
- Non-positive OHLC rows: {quality.get('nonpositive_ohlc_rows')}
- Bad OHLC relationship rows: {quality.get('bad_ohlc_rows')}
- Missing volume rows: {quality.get('missing_volume_rows')}
- Zero-volume rows: {quality.get('zero_volume_rows')}
- Duplicate symbol/date/bucket rows: {quality.get('duplicate_symbol_date_bucket_rows')}
"""
    (out_dir / "dataset_map.md").write_text(text, encoding="utf-8")


def write_data_quality_report(out_dir: Path, quality: dict) -> None:
    rows = int(quality.get("rows") or 0)
    zero_volume = int(quality.get("zero_volume_rows") or 0)
    bad_ohlc = int(quality.get("bad_ohlc_rows") or 0)
    zero_pct = zero_volume / rows * 100 if rows else 0
    bad_pct = bad_ohlc / rows * 100 if rows else 0
    text = f"""# Data Quality Report

Generated: {pd.Timestamp.now()}

## Summary

- Total rows scanned: {rows}
- Symbols: {quality.get('symbols')}
- Date range: {quality.get('min_date')} to {quality.get('max_date')}
- Intraday buckets: {quality.get('min_bucket')} to {quality.get('max_bucket')}

## Issues

| Issue | Rows | Percent of rows |
|---|---:|---:|
| Missing symbol | {quality.get('missing_symbol_rows')} | {(int(quality.get('missing_symbol_rows') or 0) / rows * 100 if rows else 0):.6f}% |
| Missing date | {quality.get('missing_date_rows')} | {(int(quality.get('missing_date_rows') or 0) / rows * 100 if rows else 0):.6f}% |
| Missing OHLC | {quality.get('missing_ohlc_rows')} | {(int(quality.get('missing_ohlc_rows') or 0) / rows * 100 if rows else 0):.6f}% |
| Non-positive OHLC | {quality.get('nonpositive_ohlc_rows')} | {(int(quality.get('nonpositive_ohlc_rows') or 0) / rows * 100 if rows else 0):.6f}% |
| Bad OHLC relationship | {bad_ohlc} | {bad_pct:.6f}% |
| Missing volume | {quality.get('missing_volume_rows')} | {(int(quality.get('missing_volume_rows') or 0) / rows * 100 if rows else 0):.6f}% |
| Zero volume | {zero_volume} | {zero_pct:.6f}% |
| Duplicate symbol/date/bucket | {quality.get('duplicate_symbol_date_bucket_rows')} | {(int(quality.get('duplicate_symbol_date_bucket_rows') or 0) / rows * 100 if rows else 0):.6f}% |

## Research Impact

- Zero-volume buckets must be handled carefully in opening-volume and relative-volume filters.
- With no bid/ask or tick data, stop/target sequencing must remain conservative.
- The adjusted-vs-raw status is not proven by schema; avoid corporate-action-sensitive conclusions until verified.
- If a strategy only works on symbols or periods with suspicious data quality, reject it.
"""
    (out_dir / "data_quality_report.md").write_text(text, encoding="utf-8")


def load_daily_bars(cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)

    frames = []
    for path in root_monthly_files():
        rel = ch_file_path(path)
        print(f"Daily aggregate {rel}")
        sql = f"""
    WITH intraday AS (
        SELECT *
        FROM file('parquets/{rel}', Parquet)
        WHERE symbol IS NOT NULL
          AND date IS NOT NULL
          AND open > 0 AND high > 0 AND low > 0 AND close > 0
          AND high >= greatest(open, close)
          AND low <= least(open, close)
    ),
    daily AS (
        SELECT
            symbol,
            toDate(date) AS trade_date,
            toFloat64(argMin(open, bucket)) AS open,
            toFloat64(max(high)) AS high,
            toFloat64(min(low)) AS low,
            toFloat64(argMax(close, bucket)) AS close,
            toFloat64(argMin(day_open, bucket)) AS day_open,
            toFloat64(argMin(gap_pct, bucket)) AS gap_pct,
            toUInt64(sum(volume)) AS volume,
            toFloat64(argMax(vwap, bucket)) AS close_vwap,
            toFloat64(avgIf(vol_rate, vol_rate IS NOT NULL)) AS avg_vol_rate,
            toUInt16(countDistinct(bucket)) AS buckets
        FROM intraday
        GROUP BY symbol, trade_date
    )
    SELECT *
    FROM daily
    WHERE buckets >= 300
    ORDER BY symbol, trade_date
    """
        frames.append(clickhouse_df(sql, timeout=240))
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["symbol", "trade_date"]).drop_duplicates(["symbol", "trade_date"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["ret1"] = df["close"] / prev_close - 1
    df["atr14"] = tr.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    for n in [5, 10, 20, 50, 100, 200]:
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())
    for n in [20, 50, 100]:
        df[f"ema{n}"] = g["close"].transform(lambda s, n=n: s.ewm(span=n, adjust=False, min_periods=max(5, n // 2)).mean())
    delta = g["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    avg_loss = loss.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))
    df["rsi2"] = 100 - (100 / (1 + (
        gain.groupby(df["symbol"]).rolling(2, min_periods=2).mean().reset_index(level=0, drop=True)
        / loss.groupby(df["symbol"]).rolling(2, min_periods=2).mean().reset_index(level=0, drop=True).replace(0, np.nan)
    )))
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    df["relvol"] = df["volume"] / df["vol20"].replace(0, np.nan)
    df["range_pct"] = (df["high"] - df["low"]) / df["close"]
    df["range7_min"] = g["range_pct"].transform(lambda s: s.rolling(7, min_periods=7).min())
    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)
    df["prior_high20"] = prior_high.groupby(df["symbol"]).rolling(20, min_periods=10).max().reset_index(level=0, drop=True)
    df["prior_low20"] = prior_low.groupby(df["symbol"]).rolling(20, min_periods=10).min().reset_index(level=0, drop=True)
    df["prior_high55"] = prior_high.groupby(df["symbol"]).rolling(55, min_periods=30).max().reset_index(level=0, drop=True)
    df["prior_low55"] = prior_low.groupby(df["symbol"]).rolling(55, min_periods=30).min().reset_index(level=0, drop=True)
    df["bb_mid20"] = df["sma20"]
    df["bb_std20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=20).std())
    df["bb_upper"] = df["bb_mid20"] + 2 * df["bb_std20"]
    df["bb_lower"] = df["bb_mid20"] - 2 * df["bb_std20"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid20"]
    df["bb_width_q20"] = g["bb_width"].transform(lambda s: s.rolling(120, min_periods=60).quantile(0.2))
    df["next_open"] = g["open"].shift(-1)
    df["year"] = df["trade_date"].dt.year
    df["dow"] = df["trade_date"].dt.day_name()
    return df


def make_strategies() -> list[StrategySpec]:
    return [
        StrategySpec(
            "trend_pullback_ema20",
            "Trend-following pullback",
            "Close above EMA50/EMA100, shallow pullback to EMA20, RSI14 38-58, relvol > 0.7.",
            1.4,
            2.6,
            10,
            lambda d: (d.close > d.ema50) & (d.ema50 > d.ema100) & (d.close <= d.ema20 * 1.015) & (d.close >= d.ema20 * 0.97) & d.rsi14.between(38, 58) & (d.relvol > 0.7),
        ),
        StrategySpec(
            "donchian55_volume_breakout",
            "Breakout",
            "Close breaks prior 55-day high, above EMA100, relative volume > 1.4.",
            1.8,
            3.6,
            15,
            lambda d: (d.close > d.prior_high55) & (d.close > d.ema100) & (d.relvol > 1.4),
        ),
        StrategySpec(
            "bb_squeeze_breakout",
            "Volatility compression breakout",
            "Bollinger width in bottom rolling quintile, close above upper band, relvol > 1.2.",
            1.5,
            3.0,
            12,
            lambda d: (d.bb_width < d.bb_width_q20) & (d.close > d.bb_upper) & (d.relvol > 1.2) & (d.close > d.ema50),
        ),
        StrategySpec(
            "rsi2_uptrend_reversion",
            "Mean reversion",
            "EMA50 uptrend, RSI2 below 5, close above SMA200.",
            1.2,
            1.8,
            5,
            lambda d: (d.close > d.sma200) & (d.close > d.ema50) & (d.rsi2 < 5),
        ),
        StrategySpec(
            "gap_down_reversal",
            "Gap reversal",
            "Gap down worse than -3%, bullish day close, close above VWAP, relvol > 1.1.",
            1.3,
            2.4,
            6,
            lambda d: (d.gap_pct < -3) & (d.close > d.open) & (d.close > d.close_vwap) & (d.relvol > 1.1),
        ),
        StrategySpec(
            "low_volume_pullback_continuation",
            "Volume pullback continuation",
            "EMA50 uptrend, close near SMA20, volume below 70% of 20-day average, positive 20-day structure.",
            1.2,
            2.2,
            8,
            lambda d: (d.close > d.ema50) & (d.ema20 > d.ema50) & (d.close <= d.sma20 * 1.01) & (d.close >= d.sma20 * 0.97) & (d.relvol < 0.7),
        ),
        StrategySpec(
            "nr7_breakout_close",
            "Narrow range breakout",
            "Narrowest 7-day range, close above prior 20-day high, relvol > 1.0.",
            1.3,
            2.6,
            8,
            lambda d: (d.range_pct <= d.range7_min * 1.001) & (d.close > d.prior_high20) & (d.relvol > 1.0),
        ),
        StrategySpec(
            "previous20_high_breakout",
            "Breakout",
            "Close above prior 20-day high, close above EMA50, relvol > 1.25.",
            1.4,
            2.5,
            10,
            lambda d: (d.close > d.prior_high20) & (d.close > d.ema50) & (d.relvol > 1.25),
        ),
        StrategySpec(
            "atr_stretch_reversal",
            "Mean reversion",
            "Price closes >2.5 ATR below EMA20 while above SMA200, RSI14 < 35.",
            1.4,
            2.0,
            7,
            lambda d: (d.close > d.sma200) & ((d.ema20 - d.close) > 2.5 * d.atr14) & (d.rsi14 < 35),
        ),
        StrategySpec(
            "rare_confluence_quality_breakout",
            "Confluence breakout",
            "Close breaks 55D high, EMA20>EMA50>EMA100, relvol>1.8, gap between -1% and +2%.",
            1.6,
            3.2,
            12,
            lambda d: (d.close > d.prior_high55) & (d.ema20 > d.ema50) & (d.ema50 > d.ema100) & (d.relvol > 1.8) & d.gap_pct.between(-1, 2),
        ),
    ]


def backtest_strategy(df: pd.DataFrame, spec: StrategySpec, max_trades_per_day: int = 20, cost_bps_side: float = 8, slippage_bps_side: float = 5) -> pd.DataFrame:
    d = df.copy()
    signal = spec.signal_fn(d).fillna(False)
    candidates = d.loc[signal & d["next_open"].notna() & d["atr14"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates["score"] = candidates["relvol"].fillna(0) + (candidates["close"] / candidates["ema50"].replace(0, np.nan)).fillna(0)
    candidates = candidates.sort_values(["trade_date", "score"], ascending=[True, False])
    candidates = candidates.groupby("trade_date", group_keys=False).head(max_trades_per_day)
    by_symbol = {sym: sdf.reset_index(drop=True) for sym, sdf in d.groupby("symbol", sort=False)}
    trades = []
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    for row in candidates.itertuples(index=False):
        sdf = by_symbol[row.symbol]
        idx_arr = np.flatnonzero(sdf["trade_date"].values == np.datetime64(row.trade_date))
        if len(idx_arr) == 0:
            continue
        signal_idx = int(idx_arr[0])
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue
        entry_date = sdf.at[entry_idx, "trade_date"]
        entry = float(sdf.at[entry_idx, "open"])
        atr = float(row.atr14)
        if entry <= 0 or atr <= 0:
            continue
        stop = entry - spec.stop_atr * atr
        target = entry + spec.target_atr * atr
        exit_price = None
        exit_date = None
        exit_reason = "time"
        hold = 0
        for j in range(entry_idx, min(entry_idx + spec.max_hold_days, len(sdf))):
            hold = j - entry_idx + 1
            low = float(sdf.at[j, "low"])
            high = float(sdf.at[j, "high"])
            close = float(sdf.at[j, "close"])
            exit_date = sdf.at[j, "trade_date"]
            if low <= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if high >= target:
                exit_price = target
                exit_reason = "target"
                break
            exit_price = close
        if exit_price is None:
            continue
        gross_ret = exit_price / entry - 1
        net_ret = gross_ret - round_cost
        trades.append({
            "strategy": spec.name,
            "family": spec.family,
            "symbol": row.symbol,
            "signal_date": row.trade_date,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry": entry,
            "exit": exit_price,
            "stop": stop,
            "target": target,
            "exit_reason": exit_reason,
            "hold_days": hold,
            "gross_return": gross_ret,
            "net_return": net_ret,
            "year": pd.Timestamp(entry_date).year,
        })
    return pd.DataFrame(trades)


def streaks(wins: pd.Series) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for w in wins.astype(bool):
        if w:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def metrics_for_trades(trades: pd.DataFrame, label: str) -> dict:
    if trades.empty:
        return {"strategy": label, "trades": 0}
    r = trades["net_return"]
    wins = r > 0
    gross_profit = r[r > 0].sum()
    gross_loss = -r[r <= 0].sum()
    pf = gross_profit / gross_loss if gross_loss > 0 else math.inf
    eq = (1 + r / 20).cumprod()  # equal-weighted max 20 concurrent slots proxy
    dd = eq / eq.cummax() - 1
    span_days = max((trades["entry_date"].max() - trades["entry_date"].min()).days, 1)
    best_w, best_l = streaks(wins)
    downside = r[r < 0].std(ddof=0)
    return {
        "strategy": label,
        "trades": int(len(trades)),
        "trades_per_week": round(len(trades) / (span_days / 7), 3),
        "trades_per_month": round(len(trades) / (span_days / 30.4375), 3),
        "win_rate": round(float(wins.mean() * 100), 2),
        "loss_rate": round(float((~wins).mean() * 100), 2),
        "profit_factor": round(float(pf), 3) if math.isfinite(pf) else "inf",
        "total_return_proxy_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "max_drawdown_proxy_pct": round(float(dd.min() * 100), 2),
        "avg_win_pct": round(float(r[wins].mean() * 100), 3) if wins.any() else 0,
        "avg_loss_pct": round(float(r[~wins].mean() * 100), 3) if (~wins).any() else 0,
        "expectancy_pct": round(float(r.mean() * 100), 3),
        "sharpe_trade": round(float(r.mean() / r.std(ddof=0) * math.sqrt(252)), 3) if r.std(ddof=0) > 0 else 0,
        "sortino_trade": round(float(r.mean() / downside * math.sqrt(252)), 3) if downside and downside > 0 else 0,
        "longest_win_streak": best_w,
        "longest_loss_streak": best_l,
        "avg_hold_days": round(float(trades["hold_days"].mean()), 2),
        "best_year": int(trades.groupby("year")["net_return"].mean().idxmax()),
        "worst_year": int(trades.groupby("year")["net_return"].mean().idxmin()),
    }


def chronological_cutoffs(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    span = end - start
    return start + span * 0.60, start + span * 0.80


def split_metrics(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    if trades.empty:
        return []
    cut1, cut2 = chronological_cutoffs(start, end)
    out = []
    ranges = [
        ("in_sample", start, cut1, trades["entry_date"] < cut1),
        ("validation", cut1, cut2, (trades["entry_date"] >= cut1) & (trades["entry_date"] < cut2)),
        ("out_of_sample", cut2, end, trades["entry_date"] >= cut2),
    ]
    for label, range_start, range_end, mask in ranges:
        m = metrics_for_trades(trades.loc[mask], label)
        m["range_start"] = str(pd.Timestamp(range_start).date())
        m["range_end"] = str(pd.Timestamp(range_end).date())
        out.append(m)
    return out


def year_by_year_metrics(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    out = []
    for year, part in trades.groupby("year"):
        m = metrics_for_trades(part, f"year_{year}")
        m["year"] = int(year)
        out.append(m)
    return out


def walk_forward_metrics(
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    train_months: int = 12,
    validate_months: int = 3,
    test_months: int = 3,
    step_months: int = 3,
) -> list[dict]:
    if trades.empty:
        return []
    out = []
    cursor = pd.Timestamp(start).normalize()
    window_id = 1
    while True:
        train_start = cursor
        train_end = train_start + pd.DateOffset(months=train_months)
        validate_end = train_end + pd.DateOffset(months=validate_months)
        test_end = validate_end + pd.DateOffset(months=test_months)
        if test_end > end:
            break
        windows = [
            ("train", train_start, train_end),
            ("validate", train_end, validate_end),
            ("test", validate_end, test_end),
        ]
        for segment, seg_start, seg_end in windows:
            part = trades[(trades["entry_date"] >= seg_start) & (trades["entry_date"] < seg_end)]
            m = metrics_for_trades(part, f"wf_{window_id}_{segment}")
            m["window_id"] = window_id
            m["segment"] = segment
            m["range_start"] = str(seg_start.date())
            m["range_end"] = str(seg_end.date())
            out.append(m)
        cursor = cursor + pd.DateOffset(months=step_months)
        window_id += 1
    return out


def instrument_contribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for strategy, part in trades.groupby("strategy"):
        total = part["net_return"].sum()
        grouped = (
            part.groupby("symbol")
            .agg(trades=("net_return", "size"), net_return_sum=("net_return", "sum"), avg_net_return=("net_return", "mean"))
            .reset_index()
            .sort_values("net_return_sum", ascending=False)
        )
        grouped.insert(0, "strategy", strategy)
        grouped["contribution_pct"] = grouped["net_return_sum"] / total * 100 if total else 0.0
        rows.append(grouped)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def parameter_sensitivity(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    rows = []
    for stop_mult in [0.85, 1.0, 1.15]:
        for target_mult in [0.85, 1.0, 1.15]:
            varied = StrategySpec(
                f"{spec.name}_s{stop_mult}_t{target_mult}",
                spec.family,
                spec.entry_rule,
                spec.stop_atr * stop_mult,
                spec.target_atr * target_mult,
                spec.max_hold_days,
                spec.signal_fn,
            )
            m = metrics_for_trades(backtest_strategy(df, varied), varied.name)
            rows.append(m)
    return pd.DataFrame(rows)


def market_behavior(df: pd.DataFrame) -> dict:
    daily = df.copy()
    out = {
        "rows_daily": int(len(daily)),
        "symbols": int(daily["symbol"].nunique()),
        "start": str(daily["trade_date"].min().date()),
        "end": str(daily["trade_date"].max().date()),
        "avg_abs_gap_pct": round(float(daily["gap_pct"].abs().mean()), 3),
        "median_daily_range_pct": round(float((daily["range_pct"] * 100).median()), 3),
        "mean_daily_return_pct": round(float(daily["ret1"].mean() * 100), 4),
        "positive_day_pct": round(float((daily["ret1"] > 0).mean() * 100), 2),
    }
    out["by_year"] = (
        daily.groupby("year")
        .agg(symbols=("symbol", "nunique"), avg_ret=("ret1", "mean"), med_range=("range_pct", "median"), avg_gap=("gap_pct", "mean"))
        .reset_index()
        .to_dict("records")
    )
    out["by_dow"] = (
        daily.groupby("dow")
        .agg(avg_ret=("ret1", "mean"), positive_pct=("ret1", lambda s: (s > 0).mean() * 100), rows=("ret1", "size"))
        .reset_index()
        .to_dict("records")
    )
    return out


def rare_perfect_candidates(all_trades: pd.DataFrame, min_trades: int = 12) -> pd.DataFrame:
    if all_trades.empty:
        return pd.DataFrame()
    rows = []
    for (strategy, year), t in all_trades.groupby(["strategy", "year"]):
        if len(t) >= min_trades and (t["net_return"] > 0).all():
            rows.append(metrics_for_trades(t, f"{strategy}_year_{year}"))
    for strategy, t in all_trades.groupby("strategy"):
        if len(t) >= min_trades and (t["net_return"] > 0).all():
            rows.append(metrics_for_trades(t, strategy))
    return pd.DataFrame(rows)


def metric_float(row: pd.Series, key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key, default)
        if val == "inf":
            return math.inf
        return float(val)
    except Exception:
        return default


def assess_strategies(metrics: pd.DataFrame, cost_metrics: pd.DataFrame, split_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if metrics.empty:
        return pd.DataFrame(rows)
    for row in metrics.to_dict("records"):
        strategy = row["strategy"]
        reasons = []
        trades = int(row.get("trades") or 0)
        pf = metric_float(pd.Series(row), "profit_factor")
        expectancy = metric_float(pd.Series(row), "expectancy_pct")
        if trades < 100:
            reasons.append("fewer than 100 base-cost trades")
        if pf < 1.25:
            reasons.append("base-cost profit factor below 1.25")
        if expectancy <= 0:
            reasons.append("base-cost expectancy is not positive")
        stress = cost_metrics[(cost_metrics["strategy"] == strategy) & (cost_metrics["cost_scenario"] == "stress")]
        if stress.empty or metric_float(stress.iloc[0], "profit_factor") < 1.05:
            reasons.append("stress-cost profit factor below 1.05")
        oos = split_df[(split_df["parent_strategy"] == strategy) & (split_df["strategy"] == "out_of_sample")]
        if oos.empty or metric_float(oos.iloc[0], "profit_factor") < 1.0:
            reasons.append("out-of-sample profit factor below 1.0")
        label = "watchlist" if not reasons else "reject"
        rows.append({
            "strategy": strategy,
            "label": label,
            "reasons": "; ".join(reasons) if reasons else "No hard-fail in summary gates; still requires manual review, walk-forward inspection, and paper trading.",
        })
    return pd.DataFrame(rows)


def write_rejected_strategies(out_dir: Path, assessments: pd.DataFrame) -> None:
    if assessments.empty:
        text = "# Rejected Strategies\n\nNo strategy metrics were generated.\n"
    else:
        lines = ["# Rejected Strategies", ""]
        for row in assessments.itertuples(index=False):
            lines.append(f"## {row.strategy}")
            lines.append("")
            lines.append(f"- Label: {row.label}")
            lines.append(f"- Reason: {row.reasons}")
            lines.append("")
        text = "\n".join(lines)
    (out_dir / "rejected_strategies.md").write_text(text, encoding="utf-8")


def write_report(
    out_dir: Path,
    schema_df: pd.DataFrame,
    quality: dict,
    behavior: dict,
    metrics: pd.DataFrame,
    cost_metrics: pd.DataFrame,
    split_df: pd.DataFrame,
    year_df: pd.DataFrame,
    walk_forward_df: pd.DataFrame,
    sensitivity_df: pd.DataFrame,
    contribution_df: pd.DataFrame,
    rare: pd.DataFrame,
    assessments: pd.DataFrame,
    strategies: list[StrategySpec],
) -> None:
    top = metrics.sort_values(["expectancy_pct", "profit_factor"], ascending=[False, False]).head(10) if not metrics.empty else metrics
    watchlist = assessments[assessments["label"] == "watchlist"]["strategy"].tolist() if not assessments.empty else []
    best = metrics[metrics["strategy"].isin(watchlist)]
    best = best.sort_values(["expectancy_pct", "profit_factor"], ascending=[False, False]).head(5) if not best.empty else best
    if best.empty:
        best_text = "No robust strategy was found under the tested assumptions."
    else:
        best_text = best.to_markdown(index=False)
    rules = "\n".join(
        f"- `{s.name}` ({s.family}): {s.entry_rule} Entry next session open. Stop {s.stop_atr} ATR, target {s.target_atr} ATR, max hold {s.max_hold_days} sessions."
        for s in strategies
    )
    rejected_summary = assessments.to_markdown(index=False) if not assessments.empty else "No strategy assessments generated."
    cost_summary = cost_metrics.to_markdown(index=False) if not cost_metrics.empty else "No cost scenario metrics generated."
    split_summary = split_df.to_markdown(index=False) if not split_df.empty else "No chronological split metrics generated."
    year_summary = year_df.to_markdown(index=False) if not year_df.empty else "No year-by-year metrics generated."
    wf_summary = walk_forward_df[walk_forward_df["segment"].eq("test")].to_markdown(index=False) if not walk_forward_df.empty else "No walk-forward metrics generated."
    sensitivity_summary = sensitivity_df.head(30).to_markdown(index=False) if not sensitivity_df.empty else "No parameter sensitivity metrics generated."
    contribution_summary = contribution_df.head(30).to_markdown(index=False) if not contribution_df.empty else "No instrument contribution metrics generated."
    report = f"""# Quant Research Report

Generated: {pd.Timestamp.now()}

## A. Dataset Summary
- Inspected parquet files: {len(schema_df)}
- Root monthly stock dataset: {quality.get('rows')} intraday rows, {quality.get('symbols')} symbols.
- Date range: {quality.get('min_date')} to {quality.get('max_date')}.
- Intraday buckets: {quality.get('min_bucket')} to {quality.get('max_bucket')}; this is bucketed intraday OHLCV data, not tick data.
- Strategy research used root monthly `candles_20*.parquet` files. Duplicate bundle files and index files were schema-inspected but excluded from stock strategy backtests to avoid duplicate observations and mixed instruments.

## B. Data Quality Issues
- Missing OHLC rows: {quality.get('missing_ohlc_rows')}
- Non-positive OHLC rows: {quality.get('nonpositive_ohlc_rows')}
- Bad OHLC relationship rows: {quality.get('bad_ohlc_rows')}
- Zero-volume rows: {quality.get('zero_volume_rows')}
- Duplicate symbol/date/bucket rows estimate: {quality.get('duplicate_symbol_date_bucket_rows')}
- Daily research bars require at least 300 buckets per symbol/day to avoid partial sessions.

## C. Assumptions and Known Limitations
- Timezone is inferred from session buckets and is not explicitly stored in the parquet schema.
- This is bucketed OHLCV data, not tick data; same-bucket entry/exit sequencing is not valid.
- Bid/ask is unavailable, so spread and slippage are modeled, not observed.
- Adjusted-vs-raw price status is not proven by schema.
- Shorting is disabled for promotion unless a strategy is explicitly labeled hypothetical.
- Survivorship bias is possible unless the parquet universe is proven point-in-time.

## D. Market Behavior Analysis
- Daily bars analyzed: {behavior.get('rows_daily')}; symbols: {behavior.get('symbols')}.
- Mean daily return: {behavior.get('mean_daily_return_pct')}%.
- Positive day rate: {behavior.get('positive_day_pct')}%.
- Median daily range: {behavior.get('median_daily_range_pct')}%.
- Average absolute gap: {behavior.get('avg_abs_gap_pct')}%.
- Data supports swing, breakout, gap/reversal, volatility-compression, and low-frequency confluence research. It is less suitable for tick microstructure without separate bid/ask data.

## E. Benchmarks
Benchmarks are not fully implemented in this runner yet. Add equal-weighted universe and index parquet benchmarks before promoting any strategy.

## F. Source-Inspired Strategy Hypotheses Tested
Trend-following, pullback continuation, Donchian/channel breakout, Bollinger squeeze breakout, RSI mean reversion, gap reversal, low-volume pullback, narrow-range breakout, ATR stretch reversal, and rare confluence breakout.

## G. Failed Strategies And Why They Failed
{rejected_summary}

## H. Top Strategy Ideas Tested
{top.to_markdown(index=False) if not top.empty else 'No trades generated.'}

## I. Top Validated Strategies, If Any
{best_text}

## J. Exact Rules For Tested Strategies
{rules}

## K. Backtest Metrics By Cost Scenario
{cost_summary}

## L. In-Sample, Validation, And Out-Of-Sample Results
{split_summary}

## M. Year-By-Year Results
{year_summary}

## N. Walk-Forward Results
{wf_summary}

## O. Parameter Sensitivity
{sensitivity_summary}

## P. Instrument And Year Contribution Concentration
{contribution_summary}

## Q. Rare 100% Historical Candidates, If Any
{rare.to_markdown(index=False) if not rare.empty else 'No strategy variant in this simple search produced a sufficiently sampled all-winning full-period candidate. Any tiny one-off perfect runs are deliberately not promoted.'}

## R. Why Rare Perfect Candidates May Fail Live
Perfect historical results can be coincidence, data artifact, survivorship bias, stale fills, same-day execution assumptions, corporate actions, or parameter overfit. They are observations, not guarantees.

## S. Risk Management Rules
- Cap capital per strategy and per symbol.
- Avoid oversized orders in low-volume names.
- Use hard stops and max holding periods.
- Stop trading a setup if live slippage materially exceeds assumptions.
- Monitor year/regime degradation.

## T. Position Sizing Suggestion
Start paper trading with equal-risk sizing: risk 0.25%-0.50% of capital per trade, max 5-10 open positions, and reduce size for names with wide ATR or weak liquidity.

## U. Live Trading Warnings
Backtests do not guarantee future results. This run uses daily bars aggregated from intraday candles; real fills, gaps through stops, taxes, auction prints, impact, and broker/API failures can change outcomes.

## V. Paper-Trading Plan
Paper trade only the top robust setups for at least 4-8 weeks. Log signal time, expected entry, actual fill, slippage, stop/target behavior, and rejected trades. Re-run this pipeline after adding each new month.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def save_charts(out_dir: Path, trade_log: pd.DataFrame, metrics_df: pd.DataFrame, year_df: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    if not metrics_df.empty:
        m = metrics_df.sort_values("expectancy_pct", ascending=True)
        plt.figure(figsize=(10, 5))
        plt.barh(m["strategy"], m["expectancy_pct"])
        plt.axvline(0, color="black", linewidth=0.8)
        plt.title("Strategy Expectancy After Fees and Slippage")
        plt.xlabel("Expectancy per trade (%)")
        plt.tight_layout()
        plt.savefig(chart_dir / "strategy_expectancy.png", dpi=150)
        plt.close()

    if not year_df.empty:
        piv = year_df.pivot_table(index="year", columns="parent_strategy", values="expectancy_pct", aggfunc="first")
        plt.figure(figsize=(12, 6))
        for col in piv.columns:
            plt.plot(piv.index, piv[col], marker="o", label=col)
        plt.axhline(0, color="black", linewidth=0.8)
        plt.title("Yearly Expectancy by Strategy")
        plt.ylabel("Expectancy per trade (%)")
        plt.legend(fontsize=7, ncol=2)
        plt.tight_layout()
        plt.savefig(chart_dir / "yearly_expectancy.png", dpi=150)
        plt.close()

    if not trade_log.empty:
        top_names = metrics_df.sort_values("expectancy_pct", ascending=False).head(4)["strategy"].tolist()
        plt.figure(figsize=(11, 6))
        for name in top_names:
            t = trade_log[trade_log["strategy"] == name].sort_values("entry_date")
            if t.empty:
                continue
            eq = (1 + t["net_return"] / 20).cumprod()
            plt.plot(pd.to_datetime(t["entry_date"]), eq, label=name)
        plt.title("Proxy Equity Curves, Equal-Weighted Slots")
        plt.ylabel("Growth of 1.0")
        plt.legend(fontsize=8)
        plt.tight_layout()
        plt.savefig(chart_dir / "proxy_equity_curves.png", dpi=150)
        plt.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Inspecting parquet files under {args.parquet_dir}")
    schema_df = inspect_parquet_files(args.parquet_dir)
    schema_df.to_csv(args.out_dir / "schema_inventory.csv", index=False)

    print("Running ClickHouse data quality scan")
    quality = dataset_quality_summary()
    (args.out_dir / "data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    write_dataset_map(args.out_dir, args.parquet_dir, schema_df, quality)
    write_data_quality_report(args.out_dir, quality)

    print("Loading daily bars")
    daily = load_daily_bars(args.out_dir / "daily_bars_cache.parquet", refresh=args.refresh_cache)
    daily = add_features(daily)
    behavior = market_behavior(daily)
    (args.out_dir / "market_behavior.json").write_text(json.dumps(behavior, indent=2, default=str), encoding="utf-8")
    research_start = pd.Timestamp(daily["trade_date"].min())
    research_end = pd.Timestamp(daily["trade_date"].max())

    strategies = make_strategies()
    base_trade_frames = []
    cost_trade_frames = []
    metrics = []
    cost_metrics = []
    split_rows = []
    year_rows = []
    walk_forward_rows = []
    sensitivity_rows = []
    for spec in strategies:
        print(f"Backtesting {spec.name}")
        base_trades = pd.DataFrame()
        for scenario, cfg in COST_SCENARIOS.items():
            trades = backtest_strategy(
                daily,
                spec,
                cost_bps_side=cfg["cost_bps_side"],
                slippage_bps_side=cfg["slippage_bps_side"],
            )
            if not trades.empty:
                trades = trades.copy()
                trades["cost_scenario"] = scenario
                trades["cost_bps_side"] = cfg["cost_bps_side"]
                trades["slippage_bps_side"] = cfg["slippage_bps_side"]
                cost_trade_frames.append(trades)
            m = metrics_for_trades(trades, spec.name)
            m["cost_scenario"] = scenario
            m["cost_bps_side"] = cfg["cost_bps_side"]
            m["slippage_bps_side"] = cfg["slippage_bps_side"]
            cost_metrics.append(m)
            if scenario == "base":
                base_trades = trades

        if not base_trades.empty:
            base_trade_frames.append(base_trades)
        metrics.append(metrics_for_trades(base_trades, spec.name))
        for m in split_metrics(base_trades, research_start, research_end):
            m["parent_strategy"] = spec.name
            split_rows.append(m)
        for m in year_by_year_metrics(base_trades):
            m["parent_strategy"] = spec.name
            year_rows.append(m)
        for m in walk_forward_metrics(base_trades, research_start, research_end):
            m["parent_strategy"] = spec.name
            walk_forward_rows.append(m)
        sens = parameter_sensitivity(daily, spec)
        sens["parent_strategy"] = spec.name
        sensitivity_rows.append(sens)

    trade_log = pd.concat(base_trade_frames, ignore_index=True) if base_trade_frames else pd.DataFrame()
    cost_trade_log = pd.concat(cost_trade_frames, ignore_index=True) if cost_trade_frames else pd.DataFrame()
    metrics_df = pd.DataFrame(metrics)
    cost_metrics_df = pd.DataFrame(cost_metrics)
    split_df = pd.DataFrame(split_rows)
    year_df = pd.DataFrame(year_rows)
    walk_forward_df = pd.DataFrame(walk_forward_rows)
    sensitivity_df = pd.concat(sensitivity_rows, ignore_index=True) if sensitivity_rows else pd.DataFrame()
    contribution_df = instrument_contribution(trade_log)
    rare = rare_perfect_candidates(trade_log)
    assessments = assess_strategies(metrics_df, cost_metrics_df, split_df)

    trade_log.to_csv(args.out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(args.out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics_df.to_csv(args.out_dir / "strategy_metrics.csv", index=False)
    cost_metrics_df.to_csv(args.out_dir / "cost_scenario_metrics.csv", index=False)
    split_df.to_csv(args.out_dir / "split_metrics.csv", index=False)
    split_df.to_csv(args.out_dir / "validation_metrics.csv", index=False)
    year_df.to_csv(args.out_dir / "year_by_year.csv", index=False)
    walk_forward_df.to_csv(args.out_dir / "walk_forward.csv", index=False)
    sensitivity_df.to_csv(args.out_dir / "parameter_sensitivity.csv", index=False)
    contribution_df.to_csv(args.out_dir / "instrument_contribution.csv", index=False)
    rare.to_csv(args.out_dir / "rare_perfect_candidates.csv", index=False)
    write_rejected_strategies(args.out_dir, assessments)
    write_report(
        args.out_dir,
        schema_df,
        quality,
        behavior,
        metrics_df,
        cost_metrics_df,
        split_df,
        year_df,
        walk_forward_df,
        sensitivity_df,
        contribution_df,
        rare,
        assessments,
        strategies,
    )
    save_charts(args.out_dir, trade_log, metrics_df, year_df)

    print(f"Done. Outputs saved to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
