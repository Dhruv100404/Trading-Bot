from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_VOLUME_GROUPS = ROOT / "data" / "volume_groups.json"
DEFAULT_CACHE_DIR = ROOT / "parquets" / "cache" / "vwap_bounce_lab"
DEFAULT_OUT_DIR = ROOT / "docs" / "vwap_bounce_lab"


@dataclass(frozen=True)
class SignalParams:
    touch_tolerance_pct: float
    min_departure_pct: float
    confirm_pct: float
    min_close_location: float
    min_risk_pct: float
    max_risk_pct: float
    entry_cutoff_bucket: int


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def month_id(path: Path) -> str:
    return path.stem.replace("candles_", "")


def parse_csv_floats(value: str) -> list[float]:
    vals = [float(part.strip()) for part in value.split(",") if part.strip()]
    if not vals:
        raise ValueError("Expected at least one numeric value.")
    return vals


def parse_timeframes(value: str) -> list[str]:
    allowed = {"1m", "3m", "5m", "15m", "1d"}
    frames = [part.strip().lower() for part in value.split(",") if part.strip()]
    unknown = sorted(set(frames) - allowed)
    if unknown:
        raise ValueError(f"Unsupported timeframes: {unknown}. Allowed: {sorted(allowed)}")
    return frames


def timeframe_minutes(timeframe: str) -> int | None:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe == "1d":
        return None
    raise ValueError(f"Unsupported timeframe {timeframe}")


def bucket_label(bucket: int | float | None) -> str:
    if bucket is None or pd.isna(bucket):
        return ""
    minutes = 9 * 60 + 15 + int(bucket) - 1
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load_symbols(volume_groups_path: Path, groups: Iterable[str], explicit_symbols: str | None = None) -> set[str]:
    if explicit_symbols:
        symbols = {part.strip().upper() for part in explicit_symbols.split(",") if part.strip()}
        if not symbols:
            raise ValueError("--symbols was provided but no symbols were parsed.")
        return symbols

    data = json.loads(volume_groups_path.read_text(encoding="utf-8"))
    volume_groups = data.get("volume_groups", {})
    selected: set[str] = set()
    group_prefixes = [group.upper() for group in groups]
    for group_name, symbols in volume_groups.items():
        if any(group_name.upper().startswith(prefix) for prefix in group_prefixes):
            selected.update(str(symbol).upper() for symbol in symbols)
    if not selected:
        raise ValueError(f"No symbols matched groups {list(groups)} in {volume_groups_path}")
    return selected


def cache_key(symbols: set[str], min_price: float, min_day_buckets: int) -> str:
    raw = "|".join(sorted(symbols)) + f"|min_price={min_price:g}|min_day_buckets={min_day_buckets}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def cache_path(cache_root: Path, key: str, timeframe: str, month_path: Path) -> Path:
    return cache_root / key / f"tf_{timeframe}" / month_path.name


def cache_is_fresh(path: Path, source: Path) -> bool:
    return path.exists() and path.stat().st_mtime >= source.stat().st_mtime


def clean_raw_month(path: Path, symbols: set[str], min_price: float, min_day_buckets: int) -> pd.DataFrame:
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
    df = df[df["symbol"].isin(symbols)].copy()
    df["bucket"] = pd.to_numeric(df["bucket"], errors="coerce").fillna(0).astype(np.int16)
    df = df[(df["bucket"] >= 1) & (df["bucket"] <= 375)].copy()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close", "volume"])
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df[(df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1))]
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values(["symbol", "date", "bucket"]).drop_duplicates(["symbol", "date", "bucket"], keep="last")
    grouped = df.groupby(["symbol", "date"], sort=False)
    df["day_open_clean"] = grouped["open"].transform("first")
    df["day_buckets_clean"] = grouped["bucket"].transform("nunique")
    df = df[(df["day_open_clean"] >= min_price) & (df["day_buckets_clean"] >= min_day_buckets)].copy()
    if df.empty:
        return df

    compute_session_vwap(df)
    return df.drop(columns=["day_open_clean", "day_buckets_clean"])


def compute_session_vwap(df: pd.DataFrame) -> None:
    typical = (
        df["high"].to_numpy(dtype=np.float64)
        + df["low"].to_numpy(dtype=np.float64)
        + df["close"].to_numpy(dtype=np.float64)
    ) / 3.0
    volume = df["volume"].to_numpy(dtype=np.float64)
    df["_pv"] = typical * volume
    grouped = df.groupby(["symbol", "date"], sort=False)
    cum_pv = grouped["_pv"].cumsum().to_numpy(dtype=np.float64)
    cum_volume = grouped["volume"].cumsum().to_numpy(dtype=np.float64)
    vwap = np.divide(cum_pv, cum_volume, out=np.full(len(df), np.nan, dtype=np.float64), where=cum_volume > 0)
    df["vwap"] = vwap.astype(np.float32)
    df.drop(columns=["_pv"], inplace=True)


def aggregate_timeframe(raw: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    minutes = timeframe_minutes(timeframe)
    if raw.empty:
        return pd.DataFrame()

    if minutes is None:
        grouped = raw.groupby(["symbol", "date"], sort=False)
        daily = grouped.agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            vwap=("vwap", "last"),
            start_bucket=("bucket", "min"),
            end_bucket=("bucket", "max"),
            minutes_in_bar=("bucket", "nunique"),
        ).reset_index()
        daily["timeframe"] = "1d"
        daily["bar_seq"] = daily.groupby("symbol", sort=False).cumcount().astype(np.int32)
        return daily

    cols = ["symbol", "date", "bucket", "open", "high", "low", "close", "volume", "vwap"]
    frame = raw[cols].copy()
    frame["bar_group"] = ((frame["bucket"].astype(np.int32) - 1) // minutes).astype(np.int16)
    grouped = frame.groupby(["symbol", "date", "bar_group"], sort=False)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        vwap=("vwap", "last"),
        start_bucket=("bucket", "min"),
        end_bucket=("bucket", "max"),
        minutes_in_bar=("bucket", "nunique"),
    ).reset_index()
    bars["timeframe"] = timeframe
    bars["bar_seq"] = bars.groupby(["symbol", "date"], sort=False).cumcount().astype(np.int16)
    return bars.drop(columns=["bar_group"])


def ensure_month_caches(
    month_path: Path,
    symbols: set[str],
    timeframes: list[str],
    args: argparse.Namespace,
    key: str,
) -> dict[str, pd.DataFrame]:
    cache_root = Path(args.cache_dir)
    requested_paths = {tf: cache_path(cache_root, key, tf, month_path) for tf in timeframes}
    can_read_all = not args.refresh_cache and all(cache_is_fresh(path, month_path) for path in requested_paths.values())
    if can_read_all:
        return {tf: pd.read_parquet(path) for tf, path in requested_paths.items()}

    missing = [
        tf
        for tf, path in requested_paths.items()
        if args.refresh_cache or not cache_is_fresh(path, month_path)
    ]
    raw = clean_raw_month(month_path, symbols, args.min_price, args.min_day_buckets)
    out: dict[str, pd.DataFrame] = {}

    for tf in timeframes:
        path = requested_paths[tf]
        if tf in missing:
            bars = aggregate_timeframe(raw, tf)
            path.parent.mkdir(parents=True, exist_ok=True)
            bars.to_parquet(path, index=False)
            out[tf] = bars
        else:
            out[tf] = pd.read_parquet(path)
    return out


def add_signal_columns(bars: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    out = bars.sort_values(group_cols + ["bar_seq"]).copy()
    grouped = out.groupby(group_cols, sort=False)
    out["prev_close"] = grouped["close"].shift(1)
    out["prev_vwap"] = grouped["vwap"].shift(1)
    out["next_open"] = grouped["open"].shift(-1)
    out["next_date"] = grouped["date"].shift(-1) if "date" in out.columns else out["date"]
    out["next_start_bucket"] = grouped["start_bucket"].shift(-1)
    out["next_bar_seq"] = grouped["bar_seq"].shift(-1)
    return out


def find_intraday_entries(bars: pd.DataFrame, timeframe: str, params: SignalParams) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()

    work = add_signal_columns(bars, ["symbol", "date"])
    touch = params.touch_tolerance_pct / 100.0
    departure = params.min_departure_pct / 100.0
    confirm = params.confirm_pct / 100.0

    candle_range = (work["high"] - work["low"]).replace(0, np.nan)
    close_location = (work["close"] - work["low"]) / candle_range
    valid = (
        (work["vwap"] > 0)
        & (work["prev_vwap"] > 0)
        & work["next_open"].notna()
        & work["next_bar_seq"].notna()
        & (work["next_start_bucket"] <= params.entry_cutoff_bucket)
    )
    long_signal = (
        valid
        & (work["prev_close"] >= work["prev_vwap"] * (1.0 + departure))
        & (work["low"] <= work["vwap"] * (1.0 + touch))
        & (work["close"] >= work["vwap"] * (1.0 + confirm))
        & (work["close"] > work["open"])
        & (close_location >= params.min_close_location)
    )
    short_signal = (
        valid
        & (work["prev_close"] <= work["prev_vwap"] * (1.0 - departure))
        & (work["high"] >= work["vwap"] * (1.0 - touch))
        & (work["close"] <= work["vwap"] * (1.0 - confirm))
        & (work["close"] < work["open"])
        & (close_location <= (1.0 - params.min_close_location))
    )

    signals = work[long_signal | short_signal].copy()
    if signals.empty:
        return pd.DataFrame()

    signals["direction"] = np.where(long_signal.loc[signals.index], "long", "short")
    signals = signals.sort_values(["symbol", "date", "bar_seq"]).drop_duplicates(["symbol", "date"], keep="first")
    signals["entry_price"] = signals["next_open"].astype(float)
    signals["stop"] = np.where(signals["direction"] == "long", signals["low"], signals["high"]).astype(float)
    signals["risk_points"] = np.where(
        signals["direction"] == "long",
        signals["entry_price"] - signals["stop"],
        signals["stop"] - signals["entry_price"],
    )
    signals["risk_pct"] = signals["risk_points"] / signals["entry_price"] * 100.0
    signals = signals[
        (signals["entry_price"] > 0)
        & (signals["risk_points"] > 0)
        & signals["risk_pct"].between(params.min_risk_pct, params.max_risk_pct)
    ].copy()
    if signals.empty:
        return pd.DataFrame()

    entries = pd.DataFrame(
        {
            "timeframe": timeframe,
            "symbol": signals["symbol"].astype(str),
            "signal_date": signals["date"].astype(str),
            "entry_date": signals["date"].astype(str),
            "direction": signals["direction"].astype(str),
            "signal_bar_seq": signals["bar_seq"].astype(int),
            "entry_bar_seq": signals["next_bar_seq"].astype(int),
            "signal_bucket": signals["end_bucket"].astype(int),
            "entry_bucket": signals["next_start_bucket"].astype(int),
            "signal_time": signals["end_bucket"].map(bucket_label),
            "entry_time": signals["next_start_bucket"].map(bucket_label),
            "entry_price": signals["entry_price"].astype(float),
            "stop": signals["stop"].astype(float),
            "risk_points": signals["risk_points"].astype(float),
            "risk_pct": signals["risk_pct"].astype(float),
            "signal_vwap": signals["vwap"].astype(float),
            "prev_vwap_dist_pct": (signals["prev_close"] / signals["prev_vwap"] - 1.0).astype(float) * 100.0,
            "signal_close_vwap_dist_pct": (signals["close"] / signals["vwap"] - 1.0).astype(float) * 100.0,
        }
    ).reset_index(drop=True)
    entries["trade_id"] = np.arange(len(entries), dtype=np.int64)
    return entries


def find_daily_entries(bars: pd.DataFrame, params: SignalParams) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()

    work = bars.sort_values(["symbol", "date"]).copy()
    work["bar_seq"] = work.groupby("symbol", sort=False).cumcount().astype(np.int32)
    work = add_signal_columns(work, ["symbol"])
    touch = params.touch_tolerance_pct / 100.0
    departure = params.min_departure_pct / 100.0
    confirm = params.confirm_pct / 100.0

    candle_range = (work["high"] - work["low"]).replace(0, np.nan)
    close_location = (work["close"] - work["low"]) / candle_range
    valid = (work["vwap"] > 0) & (work["prev_vwap"] > 0) & work["next_open"].notna() & work["next_bar_seq"].notna()
    long_signal = (
        valid
        & (work["prev_close"] >= work["prev_vwap"] * (1.0 + departure))
        & (work["low"] <= work["vwap"] * (1.0 + touch))
        & (work["close"] >= work["vwap"] * (1.0 + confirm))
        & (work["close"] > work["open"])
        & (close_location >= params.min_close_location)
    )
    short_signal = (
        valid
        & (work["prev_close"] <= work["prev_vwap"] * (1.0 - departure))
        & (work["high"] >= work["vwap"] * (1.0 - touch))
        & (work["close"] <= work["vwap"] * (1.0 - confirm))
        & (work["close"] < work["open"])
        & (close_location <= (1.0 - params.min_close_location))
    )

    signals = work[long_signal | short_signal].copy()
    if signals.empty:
        return pd.DataFrame()

    signals["direction"] = np.where(long_signal.loc[signals.index], "long", "short")
    signals["entry_price"] = signals["next_open"].astype(float)
    signals["stop"] = np.where(signals["direction"] == "long", signals["low"], signals["high"]).astype(float)
    signals["risk_points"] = np.where(
        signals["direction"] == "long",
        signals["entry_price"] - signals["stop"],
        signals["stop"] - signals["entry_price"],
    )
    signals["risk_pct"] = signals["risk_points"] / signals["entry_price"] * 100.0
    signals = signals[
        (signals["entry_price"] > 0)
        & (signals["risk_points"] > 0)
        & signals["risk_pct"].between(params.min_risk_pct, params.max_risk_pct)
    ].copy()
    if signals.empty:
        return pd.DataFrame()

    entries = pd.DataFrame(
        {
            "timeframe": "1d",
            "symbol": signals["symbol"].astype(str),
            "signal_date": signals["date"].astype(str),
            "entry_date": signals["next_date"].astype(str),
            "direction": signals["direction"].astype(str),
            "signal_bar_seq": signals["bar_seq"].astype(int),
            "entry_bar_seq": signals["next_bar_seq"].astype(int),
            "signal_bucket": np.nan,
            "entry_bucket": np.nan,
            "signal_time": "",
            "entry_time": "",
            "entry_price": signals["entry_price"].astype(float),
            "stop": signals["stop"].astype(float),
            "risk_points": signals["risk_points"].astype(float),
            "risk_pct": signals["risk_pct"].astype(float),
            "signal_vwap": signals["vwap"].astype(float),
            "prev_vwap_dist_pct": (signals["prev_close"] / signals["prev_vwap"] - 1.0).astype(float) * 100.0,
            "signal_close_vwap_dist_pct": (signals["close"] / signals["vwap"] - 1.0).astype(float) * 100.0,
        }
    ).reset_index(drop=True)
    entries["trade_id"] = np.arange(len(entries), dtype=np.int64)
    return entries


def build_intraday_day_index(bars: pd.DataFrame) -> dict[tuple[str, str], dict[str, np.ndarray]]:
    by_day: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    for key, part in bars.sort_values(["symbol", "date", "bar_seq"]).groupby(["symbol", "date"], sort=False):
        by_day[(str(key[0]), str(key[1]))] = {
            "high": part["high"].to_numpy(dtype=np.float64),
            "low": part["low"].to_numpy(dtype=np.float64),
            "close": part["close"].to_numpy(dtype=np.float64),
            "bar_seq": part["bar_seq"].to_numpy(dtype=np.int32),
            "end_bucket": part["end_bucket"].to_numpy(dtype=np.int16),
        }
    return by_day


def intraday_exits(day_index: dict[tuple[str, str], dict[str, np.ndarray]], entries: pd.DataFrame, rr: float) -> pd.DataFrame:
    if entries.empty:
        return entries
    entries = entries.copy()
    entries["target"] = np.where(
        entries["direction"] == "long",
        entries["entry_price"] + rr * entries["risk_points"],
        entries["entry_price"] - rr * entries["risk_points"],
    )

    exit_rows: list[dict] = []
    for row in entries.itertuples(index=False):
        day = day_index.get((str(row.symbol), str(row.entry_date)))
        if day is None:
            continue
        start = int(row.entry_bar_seq)
        if start >= len(day["high"]):
            continue

        exit_idx = len(day["high"]) - 1
        exit_reason = "EOD"
        exit_price = float(day["close"][exit_idx])
        r_multiple = (
            (exit_price - row.entry_price) / row.risk_points
            if row.direction == "long"
            else (row.entry_price - exit_price) / row.risk_points
        )
        if row.direction == "long":
            stop_hits = day["low"][start:] <= row.stop
            target_hits = day["high"][start:] >= row.target
        else:
            stop_hits = day["high"][start:] >= row.stop
            target_hits = day["low"][start:] <= row.target
        hits = stop_hits | target_hits
        if hits.any():
            rel_idx = int(np.argmax(hits))
            exit_idx = start + rel_idx
            hit_stop = bool(stop_hits[rel_idx])
            hit_target = bool(target_hits[rel_idx])
            if hit_stop and hit_target:
                exit_reason = "ambiguous_stop_first"
                exit_price = row.stop
                r_multiple = -1.0
            elif hit_stop:
                exit_reason = "SL"
                exit_price = row.stop
                r_multiple = -1.0
            else:
                exit_reason = "TP"
                exit_price = row.target
                r_multiple = rr
        exit_rows.append(
            {
                "trade_id": int(row.trade_id),
                "exit_bar_seq": int(day["bar_seq"][exit_idx]),
                "exit_bucket": int(day["end_bucket"][exit_idx]),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "r_multiple": r_multiple,
            }
        )

    exit_df = pd.DataFrame(exit_rows)
    if exit_df.empty:
        return pd.DataFrame()
    out = entries.merge(exit_df, on="trade_id", how="inner")
    out["rr"] = rr
    out["config"] = out["timeframe"] + "_rr" + f"{rr:g}"
    out["exit_date"] = out["entry_date"]
    out["exit_time"] = out["exit_bucket"].map(bucket_label)
    out["hold_bars"] = out["exit_bar_seq"].astype(int) - out["entry_bar_seq"].astype(int) + 1
    out["hold_minutes"] = out["exit_bucket"].astype(int) - out["entry_bucket"].astype(int) + 1
    out["weekday"] = pd.to_datetime(out["entry_date"]).dt.day_name()
    return round_trade_columns(out)


def daily_exits(bars: pd.DataFrame, entries: pd.DataFrame, rr: float, max_hold_days: int) -> pd.DataFrame:
    if entries.empty:
        return entries

    bars = bars.sort_values(["symbol", "date"]).copy()
    bars["bar_seq"] = bars.groupby("symbol", sort=False).cumcount().astype(np.int32)
    entries = entries.copy()
    entries["target"] = np.where(
        entries["direction"] == "long",
        entries["entry_price"] + rr * entries["risk_points"],
        entries["entry_price"] - rr * entries["risk_points"],
    )

    by_symbol = {
        symbol: part.reset_index(drop=True)
        for symbol, part in bars.groupby("symbol", sort=False)
    }
    rows: list[dict] = []
    for row in entries.itertuples(index=False):
        symbol_bars = by_symbol.get(row.symbol)
        if symbol_bars is None:
            continue
        start = int(row.entry_bar_seq)
        if start >= len(symbol_bars):
            continue
        end = min(len(symbol_bars) - 1, start + max_hold_days - 1)
        exit_idx = end
        exit_reason = "max_hold"
        exit_price = float(symbol_bars.at[end, "close"])
        r_multiple = (
            (exit_price - row.entry_price) / row.risk_points
            if row.direction == "long"
            else (row.entry_price - exit_price) / row.risk_points
        )
        for idx in range(start, end + 1):
            high = float(symbol_bars.at[idx, "high"])
            low = float(symbol_bars.at[idx, "low"])
            if row.direction == "long":
                hit_stop = low <= row.stop
                hit_target = high >= row.target
            else:
                hit_stop = high >= row.stop
                hit_target = low <= row.target
            if hit_stop or hit_target:
                exit_idx = idx
                if hit_stop and hit_target:
                    exit_reason = "ambiguous_stop_first"
                    exit_price = row.stop
                    r_multiple = -1.0
                elif hit_stop:
                    exit_reason = "SL"
                    exit_price = row.stop
                    r_multiple = -1.0
                else:
                    exit_reason = "TP"
                    exit_price = row.target
                    r_multiple = rr
                break
        rows.append(
            {
                "trade_id": int(row.trade_id),
                "exit_bar_seq": int(exit_idx),
                "exit_date": str(symbol_bars.at[exit_idx, "date"]),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "r_multiple": r_multiple,
            }
        )

    exit_df = pd.DataFrame(rows)
    if exit_df.empty:
        return pd.DataFrame()
    out = entries.merge(exit_df, on="trade_id", how="inner")
    out["rr"] = rr
    out["config"] = out["timeframe"] + "_rr" + f"{rr:g}"
    out["exit_time"] = ""
    out["exit_bucket"] = np.nan
    out["hold_bars"] = out["exit_bar_seq"].astype(int) - out["entry_bar_seq"].astype(int) + 1
    out["hold_minutes"] = np.nan
    out["weekday"] = pd.to_datetime(out["entry_date"]).dt.day_name()
    return round_trade_columns(out)


def round_trade_columns(trades: pd.DataFrame) -> pd.DataFrame:
    for col in [
        "entry_price",
        "stop",
        "target",
        "risk_points",
        "risk_pct",
        "signal_vwap",
        "prev_vwap_dist_pct",
        "signal_close_vwap_dist_pct",
        "exit_price",
        "r_multiple",
    ]:
        if col in trades.columns:
            trades[col] = trades[col].astype(float).round(4)
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
            "total_r": 0.0,
            "trade_sharpe": 0.0,
            "daily_sharpe": 0.0,
            "max_drawdown_r": 0.0,
            "target_rate": 0.0,
            "stop_rate": 0.0,
            "avg_hold_bars": 0.0,
            "avg_hold_minutes": 0.0,
            "avg_risk_pct": 0.0,
        }

    r = trades["r_multiple"].astype(float)
    trade_std = float(r.std(ddof=0))
    daily = trades.groupby("entry_date", sort=True)["r_multiple"].sum()
    daily_std = float(daily.std(ddof=0))
    equity = daily.cumsum()
    dd = equity - equity.cummax()
    hold_minutes = pd.to_numeric(trades.get("hold_minutes"), errors="coerce")
    return {
        "trades": int(len(trades)),
        "symbols": int(trades["symbol"].nunique()),
        "trade_days": int(trades["entry_date"].nunique()),
        "win_rate": round(float((r > 0).mean() * 100.0), 2),
        "expectancy_r": round(float(r.mean()), 3),
        "median_r": round(float(r.median()), 3),
        "total_r": round(float(r.sum()), 2),
        "trade_sharpe": round(float(r.mean() / trade_std * math.sqrt(252)) if trade_std > 0 else 0.0, 3),
        "daily_sharpe": round(float(daily.mean() / daily_std * math.sqrt(252)) if daily_std > 0 else 0.0, 3),
        "max_drawdown_r": round(float(dd.min()), 2),
        "target_rate": round(float((trades["exit_reason"] == "TP").mean() * 100.0), 2),
        "stop_rate": round(float(trades["exit_reason"].isin(["SL", "ambiguous_stop_first"]).mean() * 100.0), 2),
        "avg_hold_bars": round(float(trades["hold_bars"].mean()), 2),
        "avg_hold_minutes": round(float(hold_minutes.mean()) if hold_minutes.notna().any() else 0.0, 2),
        "avg_risk_pct": round(float(trades["risk_pct"].mean()), 3),
    }


def summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    summary_rows = []
    direction_rows = []
    split_rows = []
    yearly_rows = []
    symbol_rows = []
    dates = pd.to_datetime(trades["entry_date"])
    start, end = dates.min(), dates.max()
    span = end - start
    cut1 = start + span * 0.60
    cut2 = start + span * 0.80

    for (timeframe, rr), part in trades.groupby(["timeframe", "rr"], sort=True):
        summary_rows.append({"timeframe": timeframe, "rr": rr, "config": f"{timeframe}_rr{rr:g}", **metric_block(part)})
        for direction, dp in part.groupby("direction", sort=True):
            direction_rows.append({"timeframe": timeframe, "rr": rr, "direction": direction, **metric_block(dp)})
        part_dates = pd.to_datetime(part["entry_date"])
        for label, mask in [
            ("train", part_dates < cut1),
            ("validation", (part_dates >= cut1) & (part_dates < cut2)),
            ("out_of_sample", part_dates >= cut2),
        ]:
            split_rows.append({"timeframe": timeframe, "rr": rr, "split": label, **metric_block(part.loc[mask])})
        yp = part.copy()
        yp["year"] = pd.to_datetime(yp["entry_date"]).dt.year
        for year, year_part in yp.groupby("year", sort=True):
            yearly_rows.append({"timeframe": timeframe, "rr": rr, "year": int(year), **metric_block(year_part)})
        for symbol, sp in part.groupby("symbol", sort=True):
            if len(sp) >= 20:
                symbol_rows.append({"timeframe": timeframe, "rr": rr, "symbol": symbol, **metric_block(sp)})

    summary = pd.DataFrame(summary_rows).sort_values(["daily_sharpe", "expectancy_r"], ascending=False)
    directions = pd.DataFrame(direction_rows).sort_values(["timeframe", "rr", "direction"])
    splits = pd.DataFrame(split_rows).sort_values(["timeframe", "rr", "split"])
    yearly = pd.DataFrame(yearly_rows).sort_values(["timeframe", "rr", "year"])
    symbols = pd.DataFrame(symbol_rows)
    if not symbols.empty:
        symbols = symbols.sort_values(["timeframe", "rr", "expectancy_r"], ascending=[True, True, False])
    return summary, directions, splits, yearly, symbols


def write_report(
    out_dir: Path,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    directions: pd.DataFrame,
    splits: pd.DataFrame,
    yearly: pd.DataFrame,
    symbols: pd.DataFrame,
    args: argparse.Namespace,
    selected_symbols: set[str],
    cache_key_value: str,
    tested_months: list[str],
) -> None:
    top_symbols = (
        symbols.groupby(["timeframe", "rr"], group_keys=False).head(10).to_markdown(index=False)
        if not symbols.empty
        else "No symbol rows."
    )
    bottom_symbols = (
        symbols.sort_values(["timeframe", "rr", "expectancy_r"], ascending=[True, True, True])
        .groupby(["timeframe", "rr"], group_keys=False)
        .head(10)
        .to_markdown(index=False)
        if not symbols.empty
        else "No symbol rows."
    )
    best = summary.head(10).to_markdown(index=False) if not summary.empty else "No trades."
    report = [
        "# VWAP Bounce Reversal Lab",
        "",
        f"Universe: {len(selected_symbols)} symbols from volume groups `{args.volume_groups}`.",
        f"Months tested: `{', '.join(tested_months)}`.",
        f"Timeframes: `{args.timeframes}`. Risk-reward ratios: `{args.rr}`.",
        f"Cache key: `{cache_key_value}` under `{Path(args.cache_dir) / cache_key_value}`.",
        "",
        "## Signal Definition",
        "",
        "- VWAP is computed locally as cumulative session typical-price times volume divided by cumulative volume; the parquet `vwap` column is not trusted because local samples are zero-filled.",
        "- Long setup: previous bar closes above VWAP, current bar tags/comes close to VWAP, then closes bullish back above VWAP.",
        "- Short setup: previous bar closes below VWAP, current bar tags/comes close to VWAP, then closes bearish back below VWAP.",
        "- Entry is next bar open. Stop is the signal bar low for long and signal bar high for short. Same-bar target/stop ambiguity is counted as stop first.",
        "- Intraday tests exit by the session close if target/stop does not hit. Daily tests enter next trading day and exit on target/stop or max hold.",
        "- Results are raw signal-quality R multiples, not a capped position-sizing portfolio.",
        "",
        "## Parameters",
        "",
        pd.DataFrame(
            [
                {
                    "touch_tolerance_pct": args.touch_tolerance_pct,
                    "min_departure_pct": args.min_departure_pct,
                    "confirm_pct": args.confirm_pct,
                    "min_close_location": args.min_close_location,
                    "min_risk_pct": args.min_risk_pct,
                    "max_risk_pct": args.max_risk_pct,
                    "entry_cutoff_bucket": args.entry_cutoff_bucket,
                    "entry_cutoff_time": bucket_label(args.entry_cutoff_bucket),
                    "max_hold_days": args.max_hold_days,
                    "min_price": args.min_price,
                    "min_day_buckets": args.min_day_buckets,
                }
            ]
        ).to_markdown(index=False),
        "",
        "## Best Rows",
        "",
        best,
        "",
        "## Summary",
        "",
        summary.to_markdown(index=False) if not summary.empty else "No trades.",
        "",
        "## Direction Breakdown",
        "",
        directions.to_markdown(index=False) if not directions.empty else "No direction rows.",
        "",
        "## Walk-forward Splits",
        "",
        splits.to_markdown(index=False) if not splits.empty else "No split rows.",
        "",
        "## Yearly",
        "",
        yearly.to_markdown(index=False) if not yearly.empty else "No yearly rows.",
        "",
        "## Top Symbols",
        "",
        top_symbols,
        "",
        "## Bottom Symbols",
        "",
        bottom_symbols,
        "",
        "## Practical Read",
        "",
        "Promote only rows that hold up in validation and out-of-sample, and check direction separately. Short rows are hypothetical unless the instrument and broker route allow clean intraday shorting.",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "final_report.md").write_text("\n".join(report), encoding="utf-8")
    if not trades.empty:
        trades.head(5000).to_csv(out_dir / "trade_log_head.csv", index=False)


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timeframes = parse_timeframes(args.timeframes)
    rr_values = parse_csv_floats(args.rr)
    groups = [part.strip() for part in args.volume_groups.split(",") if part.strip()]
    symbols = load_symbols(Path(args.volume_groups_file), groups, args.symbols)
    key = cache_key(symbols, args.min_price, args.min_day_buckets)
    params = SignalParams(
        touch_tolerance_pct=args.touch_tolerance_pct,
        min_departure_pct=args.min_departure_pct,
        confirm_pct=args.confirm_pct,
        min_close_location=args.min_close_location,
        min_risk_pct=args.min_risk_pct,
        max_risk_pct=args.max_risk_pct,
        entry_cutoff_bucket=args.entry_cutoff_bucket,
    )

    files = monthly_files(Path(args.parquet_dir))
    if args.from_month:
        files = [path for path in files if month_id(path) >= args.from_month]
    if args.to_month:
        files = [path for path in files if month_id(path) <= args.to_month]
    if args.last_files:
        files = files[-args.last_files :]
    if args.max_files:
        files = files[: args.max_files]
    if not files:
        raise FileNotFoundError(f"No candles_20*.parquet files found in {args.parquet_dir}")

    intraday_frames = [tf for tf in timeframes if tf != "1d"]
    want_daily = "1d" in timeframes
    trade_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []

    for idx, month_path in enumerate(files, start=1):
        print(f"{idx}/{len(files)} {month_path.name}", flush=True)
        month_timeframes = [*intraday_frames, *(["1d"] if want_daily else [])]
        month_bars = ensure_month_caches(month_path, symbols, month_timeframes, args, key)
        for timeframe in intraday_frames:
            bars = month_bars.get(timeframe, pd.DataFrame())
            entries = find_intraday_entries(bars, timeframe, params)
            if entries.empty:
                continue
            day_index = build_intraday_day_index(bars)
            for rr in rr_values:
                trades = intraday_exits(day_index, entries, rr)
                if not trades.empty:
                    trade_frames.append(trades)
        if want_daily:
            daily = month_bars.get("1d", pd.DataFrame())
            if not daily.empty:
                daily_frames.append(daily)

    if want_daily and daily_frames:
        daily_bars = pd.concat(daily_frames, ignore_index=True).sort_values(["symbol", "date"])
        daily_bars = daily_bars.drop_duplicates(["symbol", "date"], keep="last")
        daily_bars["bar_seq"] = daily_bars.groupby("symbol", sort=False).cumcount().astype(np.int32)
        entries = find_daily_entries(daily_bars, params)
        for rr in rr_values:
            trades = daily_exits(daily_bars, entries, rr, args.max_hold_days)
            if not trades.empty:
                trade_frames.append(trades)

    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    if not all_trades.empty:
        all_trades = all_trades.sort_values(["timeframe", "rr", "entry_date", "symbol", "entry_bar_seq"])
    summary, directions, splits, yearly, symbol_summary = summarize(all_trades)

    all_trades.to_csv(out_dir / "trade_log.csv", index=False)
    summary.to_csv(out_dir / "summary.csv", index=False)
    directions.to_csv(out_dir / "direction_metrics.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    symbol_summary.to_csv(out_dir / "symbol_summary.csv", index=False)
    write_report(
        out_dir,
        all_trades,
        summary,
        directions,
        splits,
        yearly,
        symbol_summary,
        args,
        symbols,
        key,
        [month_id(path) for path in files],
    )

    print("Best rows")
    print(summary.head(10).to_string(index=False) if not summary.empty else "No trades")
    print(f"Wrote VWAP bounce lab to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest VWAP bounce/reversal entries across intraday and daily timeframes.")
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR))
    parser.add_argument("--volume-groups-file", default=str(DEFAULT_VOLUME_GROUPS))
    parser.add_argument("--volume-groups", default="MEGA,LARGE")
    parser.add_argument("--symbols", default=None, help="Optional comma-separated symbols. Overrides --volume-groups.")
    parser.add_argument("--timeframes", default="1m,3m,5m,15m,1d")
    parser.add_argument("--rr", default="1,1.5,2,3", help="Comma-separated risk-reward ratios.")
    parser.add_argument("--min-price", type=float, default=50.0)
    parser.add_argument("--min-day-buckets", type=int, default=300)
    parser.add_argument("--touch-tolerance-pct", type=float, default=0.10)
    parser.add_argument("--min-departure-pct", type=float, default=0.10)
    parser.add_argument("--confirm-pct", type=float, default=0.03)
    parser.add_argument("--min-close-location", type=float, default=0.60)
    parser.add_argument("--min-risk-pct", type=float, default=0.03)
    parser.add_argument("--max-risk-pct", type=float, default=4.0)
    parser.add_argument("--entry-cutoff-bucket", type=int, default=345)
    parser.add_argument("--max-hold-days", type=int, default=10)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--max-files", type=int, default=0, help="Limit monthly files for quick smoke tests.")
    parser.add_argument("--last-files", type=int, default=0, help="Use the most recent N monthly parquet files.")
    parser.add_argument("--from-month", default=None, help="Start month filter in YYYYMM format, e.g. 202501.")
    parser.add_argument("--to-month", default=None, help="End month filter in YYYYMM format, e.g. 202605.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
