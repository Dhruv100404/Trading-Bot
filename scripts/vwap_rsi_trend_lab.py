from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_CACHE_DIR = ROOT / "parquets" / "cache" / "vwap_rsi_5m_v1"
DEFAULT_OUT_DIR = ROOT / "docs" / "vwap_rsi_trend_lab"
DEFAULT_VOLUME_GROUPS = ROOT / "data" / "volume_groups.json"

CACHE_VERSION = "vwap_rsi_5m_v1"
SESSION_OPEN_MINUTE = 9 * 60 + 15
SOURCE_BUCKETS = 375
BAR_MINUTES = 5
BAR_COUNT = SOURCE_BUCKETS // BAR_MINUTES
SOURCE_COLUMNS = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume"]
ARRAY_NAMES = ["open", "high", "low", "close", "volume", "vwap", "rsi14", "relvol20", "valid"]

T0 = time.perf_counter()


@dataclass(frozen=True)
class CacheBundle:
    cache_dir: Path
    manifest: dict[str, Any]
    dates: np.ndarray
    symbols: np.ndarray
    arrays: dict[str, np.ndarray]

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]


@dataclass(frozen=True)
class SignalSpec:
    trigger: str
    rsi_edge: float
    confirm_pct: float
    max_vwap_dist_pct: float
    rvol_min: float

    @property
    def name(self) -> str:
        return (
            f"{self.trigger}_rsi{self.rsi_edge:g}_confirm{self.confirm_pct:g}"
            f"_dist{self.max_vwap_dist_pct:g}_rvol{self.rvol_min:g}"
        )


@dataclass(frozen=True)
class StopSpec:
    mode: str
    value: float

    @property
    def name(self) -> str:
        if self.mode == "swing":
            return f"swing{int(self.value)}"
        return f"pct{self.value:g}"


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def parse_csv_floats(raw: str) -> list[float]:
    values = [float(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError(f"Expected at least one numeric value in {raw!r}")
    return values


def parse_csv_ints(raw: str) -> list[int]:
    values = [int(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError(f"Expected at least one integer value in {raw!r}")
    return values


def parse_csv_strings(raw: str) -> list[str]:
    return [part.strip().lower() for part in str(raw).split(",") if part.strip()]


def parse_time_to_bar(value: str, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value).strip())
    if not match:
        raise ValueError(f"Invalid time {value!r}. Use HH:MM.")
    minute = int(match.group(1)) * 60 + int(match.group(2))
    offset = minute - SESSION_OPEN_MINUTE
    if offset < 0:
        return 0
    return int(np.clip(offset // BAR_MINUTES, 0, BAR_COUNT - 1))


def bar_label(bar_idx: int | np.ndarray) -> str | np.ndarray:
    arr = np.asarray(bar_idx, dtype=np.int32)
    minutes = SESSION_OPEN_MINUTE + arr * BAR_MINUTES
    hours = np.char.zfill((minutes // 60).astype(str), 2)
    mins = np.char.zfill((minutes % 60).astype(str), 2)
    labels = np.char.add(np.char.add(hours, ":"), mins)
    if np.ndim(bar_idx) == 0:
        return str(labels.item())
    return labels


def month_key(path: Path) -> str | None:
    match = re.match(r"candles_(\d{6})\.parquet$", path.name)
    return match.group(1) if match else None


def resolve_monthly_files(parquet_dir: Path, parquet_glob: str, start_date: str | None, end_date: str | None) -> list[Path]:
    start_month = start_date[:7].replace("-", "") if start_date else None
    end_month = end_date[:7].replace("-", "") if end_date else None
    files: list[Path] = []
    for path in sorted(parquet_dir.glob(parquet_glob)):
        key = month_key(path)
        if key is None:
            continue
        if start_month and key < start_month:
            continue
        if end_month and key > end_month:
            continue
        files.append(path)
    return files


def load_volume_group_symbols(path: Path, groups: list[str]) -> set[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    volume_groups = raw.get("volume_groups", {})
    selected: set[str] = set()
    prefixes = [group.upper() for group in groups]
    for group_name, symbols in volume_groups.items():
        upper_name = str(group_name).upper()
        if any(upper_name.startswith(prefix) for prefix in prefixes):
            selected.update(str(symbol).upper() for symbol in symbols)
    if not selected:
        raise ValueError(f"No symbols matched groups {groups} in {path}")
    return selected


def resolve_symbols(args: argparse.Namespace) -> set[str] | None:
    if args.all_symbols:
        return None
    if args.symbols:
        symbols = {part.strip().upper() for part in str(args.symbols).split(",") if part.strip()}
        if not symbols:
            raise ValueError("--symbols was supplied but no symbols were parsed.")
        return symbols
    groups = [part.strip() for part in str(args.volume_groups).split(",") if part.strip()]
    return load_volume_group_symbols(Path(args.volume_groups_file), groups)


def symbols_hash(symbols: set[str] | None) -> str:
    if symbols is None:
        return "all"
    return hashlib.sha256("\n".join(sorted(symbols)).encode("utf-8")).hexdigest()


def source_fingerprint(
    files: list[Path],
    start_date: str | None,
    end_date: str | None,
    symbols: set[str] | None,
    min_day_bars: int,
) -> dict[str, Any]:
    source_files = []
    for path in files:
        stat = path.stat()
        source_files.append({"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return {
        "cache_version": CACHE_VERSION,
        "bar_minutes": BAR_MINUTES,
        "start_date": start_date,
        "end_date": end_date,
        "symbols_hash": symbols_hash(symbols),
        "min_day_bars": int(min_day_bars),
        "source_files": source_files,
    }


def cache_is_fresh(cache_dir: Path, fingerprint: dict[str, Any]) -> bool:
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if manifest.get("fingerprint") != fingerprint:
        return False
    required = ["dates.npy", "symbols.npy", *[f"{name}.npy" for name in ARRAY_NAMES]]
    return all((cache_dir / name).exists() for name in required)


def read_month(path: Path, symbols: set[str] | None, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    schema_names = set(pq.read_schema(path).names)
    columns = [col for col in SOURCE_COLUMNS if col in schema_names]
    filters = None
    if symbols:
        filters = [("symbol", "in", sorted(symbols))]
    try:
        table = pq.read_table(path, columns=columns, filters=filters)
    except Exception:
        table = pq.read_table(path, columns=columns)
    df = table.to_pandas()
    if df.empty:
        return pd.DataFrame()

    df["symbol"] = df["symbol"].astype(str).str.upper()
    if symbols:
        df = df[df["symbol"].isin(symbols)]
    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    if df.empty:
        return pd.DataFrame()

    for col in ["bucket", "open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close", "volume"])
    df = df[
        (df["bucket"].between(1, SOURCE_BUCKETS))
        & (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["volume"] >= 0)
        & (df["high"] >= df[["open", "close"]].max(axis=1))
        & (df["low"] <= df[["open", "close"]].min(axis=1))
    ].copy()
    if df.empty:
        return pd.DataFrame()

    df["bucket"] = df["bucket"].astype(np.int16)
    df["bar_idx"] = ((df["bucket"].astype(np.int32) - 1) // BAR_MINUTES).astype(np.int16)
    df = df.sort_values(["symbol", "date", "bucket"], kind="mergesort")
    grouped = df.groupby(["symbol", "date", "bar_idx"], sort=False)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        minutes=("bucket", "nunique"),
    ).reset_index()
    bars = bars[bars["minutes"] >= max(1, BAR_MINUTES - 1)].copy()
    return bars


def compute_session_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, valid: np.ndarray) -> np.ndarray:
    typical = ((high + low + close) / 3.0).astype(np.float32)
    clean_volume = np.where(valid & np.isfinite(volume) & (volume > 0.0), volume, 0.0).astype(np.float32)
    pv = np.where(valid, typical * clean_volume, 0.0).astype(np.float32)
    cum_pv = np.cumsum(pv, axis=2, dtype=np.float32)
    cum_volume = np.cumsum(clean_volume, axis=2, dtype=np.float32)
    out = np.full_like(close, np.nan, dtype=np.float32)
    np.divide(cum_pv, cum_volume, out=out, where=cum_volume > 0.0)
    return out


def compute_rsi14(close: np.ndarray, valid: np.ndarray, period: int = 14) -> np.ndarray:
    rsi = np.full_like(close, np.nan, dtype=np.float32)
    if close.shape[2] <= period:
        return rsi

    delta = close[:, :, 1:] - close[:, :, :-1]
    delta_valid = valid[:, :, 1:] & valid[:, :, :-1] & np.isfinite(delta)
    gains = np.where(delta_valid & (delta > 0.0), delta, 0.0).astype(np.float32)
    losses = np.where(delta_valid & (delta < 0.0), -delta, 0.0).astype(np.float32)

    first_valid = delta_valid[:, :, :period].sum(axis=2) == period
    avg_gain = np.full(close.shape[:2], np.nan, dtype=np.float32)
    avg_loss = np.full(close.shape[:2], np.nan, dtype=np.float32)
    avg_gain[first_valid] = gains[:, :, :period].sum(axis=2)[first_valid] / np.float32(period)
    avg_loss[first_valid] = losses[:, :, :period].sum(axis=2)[first_valid] / np.float32(period)

    def write_rsi(bar: int, gain_avg: np.ndarray, loss_avg: np.ndarray) -> None:
        rs = np.divide(gain_avg, loss_avg, out=np.full_like(gain_avg, np.nan), where=loss_avg != 0.0)
        values = 100.0 - (100.0 / (1.0 + rs))
        values = np.where((loss_avg == 0.0) & np.isfinite(gain_avg), 100.0, values)
        values = np.where((gain_avg == 0.0) & (loss_avg == 0.0), 50.0, values)
        rsi[:, :, bar] = values.astype(np.float32)

    write_rsi(period, avg_gain, avg_loss)
    for bar in range(period + 1, close.shape[2]):
        idx = bar - 1
        ok = delta_valid[:, :, idx] & np.isfinite(avg_gain) & np.isfinite(avg_loss)
        avg_gain = np.where(ok, (avg_gain * (period - 1) + gains[:, :, idx]) / period, np.nan).astype(np.float32)
        avg_loss = np.where(ok, (avg_loss * (period - 1) + losses[:, :, idx]) / period, np.nan).astype(np.float32)
        write_rsi(bar, avg_gain, avg_loss)
    return rsi


def compute_relvol20(volume: np.ndarray, valid: np.ndarray, lookback: int = 20, min_prior: int = 10) -> np.ndarray:
    clean = np.where(valid & np.isfinite(volume), volume, 0.0).astype(np.float32)
    counts = (valid & np.isfinite(volume)).astype(np.float32)
    sum_cs = np.cumsum(clean, axis=0, dtype=np.float32)
    count_cs = np.cumsum(counts, axis=0, dtype=np.float32)
    zero = np.zeros((1, volume.shape[1], volume.shape[2]), dtype=np.float32)
    sum_pad = np.concatenate((zero, sum_cs), axis=0)
    count_pad = np.concatenate((zero, count_cs), axis=0)
    day_idx = np.arange(volume.shape[0], dtype=np.int32)
    start_idx = np.maximum(day_idx - lookback, 0).astype(np.int32)
    prior_sum = sum_pad[day_idx] - sum_pad[start_idx]
    prior_count = count_pad[day_idx] - count_pad[start_idx]
    prior_mean = np.divide(prior_sum, prior_count, out=np.full_like(prior_sum, np.nan), where=prior_count >= min_prior)
    return np.divide(volume, prior_mean, out=np.full_like(volume, np.nan, dtype=np.float32), where=prior_mean > 0.0).astype(np.float32)


def write_cache_arrays(cache_dir: Path, bars: pd.DataFrame, fingerprint: dict[str, Any], started: float) -> None:
    dates = np.array(sorted(bars["date"].dt.strftime("%Y-%m-%d").unique()), dtype="datetime64[D]")
    symbols = np.array(sorted(bars["symbol"].astype(str).unique()), dtype=str)
    date_labels = dates.astype(str)
    day_codes = pd.Categorical(bars["date"].dt.strftime("%Y-%m-%d"), categories=date_labels, ordered=True).codes.astype(np.int32)
    symbol_codes = pd.Categorical(bars["symbol"].astype(str), categories=symbols, ordered=True).codes.astype(np.int32)
    bar_codes = bars["bar_idx"].to_numpy(np.int32, copy=False)
    valid_rows = (day_codes >= 0) & (symbol_codes >= 0) & (bar_codes >= 0) & (bar_codes < BAR_COUNT)
    bars = bars.loc[valid_rows].reset_index(drop=True)
    day_codes = day_codes[valid_rows]
    symbol_codes = symbol_codes[valid_rows]
    bar_codes = bar_codes[valid_rows]
    shape = (dates.size, symbols.size, BAR_COUNT)

    arrays: dict[str, np.ndarray] = {
        "open": np.full(shape, np.nan, dtype=np.float32),
        "high": np.full(shape, np.nan, dtype=np.float32),
        "low": np.full(shape, np.nan, dtype=np.float32),
        "close": np.full(shape, np.nan, dtype=np.float32),
        "volume": np.zeros(shape, dtype=np.float32),
    }
    for col in ["open", "high", "low", "close", "volume"]:
        arrays[col][day_codes, symbol_codes, bar_codes] = bars[col].to_numpy(np.float32, copy=False)

    valid = (
        np.isfinite(arrays["open"])
        & np.isfinite(arrays["high"])
        & np.isfinite(arrays["low"])
        & np.isfinite(arrays["close"])
        & (arrays["open"] > 0.0)
        & (arrays["high"] > 0.0)
        & (arrays["low"] > 0.0)
        & (arrays["close"] > 0.0)
    )
    arrays["valid"] = valid.astype(bool)
    arrays["vwap"] = compute_session_vwap(arrays["high"], arrays["low"], arrays["close"], arrays["volume"], valid)
    arrays["rsi14"] = compute_rsi14(arrays["close"], valid)
    arrays["relvol20"] = compute_relvol20(arrays["volume"], valid)

    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(cache_dir / "dates.npy", dates)
    np.save(cache_dir / "symbols.npy", symbols)
    array_specs: dict[str, dict[str, Any]] = {}
    for name in ARRAY_NAMES:
        arr = arrays[name]
        np.save(cache_dir / f"{name}.npy", arr)
        array_specs[name] = {"file": f"{name}.npy", "dtype": str(arr.dtype), "shape": list(arr.shape)}

    manifest = {
        "cache_version": CACHE_VERSION,
        "created_at": pd.Timestamp.utcnow().isoformat(),
        "build_seconds": round(time.perf_counter() - started, 3),
        "date_count": int(dates.size),
        "symbol_count": int(symbols.size),
        "bar_count": int(BAR_COUNT),
        "row_count_5m": int(len(bars)),
        "date_min": str(dates.min()) if dates.size else "",
        "date_max": str(dates.max()) if dates.size else "",
        "fingerprint": fingerprint,
        "arrays": array_specs,
    }
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def atomic_replace(tmp_dir: Path, final_dir: Path) -> None:
    old_dir = final_dir.with_name(f"{final_dir.name}.old.{os.getpid()}")
    if old_dir.exists():
        shutil.rmtree(old_dir)
    if final_dir.exists():
        final_dir.rename(old_dir)
    tmp_dir.rename(final_dir)
    if old_dir.exists():
        shutil.rmtree(old_dir)


def build_cache(args: argparse.Namespace) -> Path:
    started = time.perf_counter()
    parquet_dir = Path(args.parquet_dir)
    cache_dir = Path(args.cache_dir)
    symbols = resolve_symbols(args)
    files = resolve_monthly_files(parquet_dir, args.parquet_glob, args.start_date, args.end_date)
    if not files:
        raise FileNotFoundError(f"No monthly parquet files matched {parquet_dir / args.parquet_glob}")
    fingerprint = source_fingerprint(files, args.start_date, args.end_date, symbols, args.min_day_bars)
    if not args.refresh_cache and cache_is_fresh(cache_dir, fingerprint):
        log(f"Cache already fresh: {cache_dir}")
        return cache_dir

    tmp_dir = cache_dir.with_name(f"{cache_dir.name}.tmp.{os.getpid()}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    frames: list[pd.DataFrame] = []
    try:
        for idx, path in enumerate(files, start=1):
            log(f"{idx}/{len(files)} aggregate {path.name}")
            bars = read_month(path, symbols, args.start_date, args.end_date)
            if not bars.empty:
                frames.append(bars)
        if not frames:
            raise RuntimeError("No 5-minute bars were produced for the selected data.")
        all_bars = pd.concat(frames, ignore_index=True)
        all_bars = all_bars.sort_values(["symbol", "date", "bar_idx"], kind="mergesort")
        all_bars = all_bars.drop_duplicates(["symbol", "date", "bar_idx"], keep="last")
        day_bar_counts = all_bars.groupby(["symbol", "date"], sort=False)["bar_idx"].transform("nunique")
        all_bars = all_bars[day_bar_counts >= int(args.min_day_bars)].copy()
        if all_bars.empty:
            raise RuntimeError(f"No symbol-days survived --min-day-bars {args.min_day_bars}.")
        log(
            "5m rows: "
            f"{len(all_bars):,} | symbols: {all_bars['symbol'].nunique():,} | dates: {all_bars['date'].nunique():,}"
        )
        log("Computing VWAP, RSI, and relative-volume arrays")
        write_cache_arrays(tmp_dir, all_bars, fingerprint, started)
        atomic_replace(tmp_dir, cache_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise
    log(f"Wrote cache: {cache_dir}")
    return cache_dir


def load_cache(cache_dir: Path, mmap: bool = True) -> CacheBundle:
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Cache manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mmap_mode = "r" if mmap else None
    arrays = {
        name: np.load(cache_dir / manifest["arrays"][name]["file"], mmap_mode=mmap_mode)
        for name in ARRAY_NAMES
    }
    return CacheBundle(
        cache_dir=cache_dir,
        manifest=manifest,
        dates=np.load(cache_dir / "dates.npy", mmap_mode=mmap_mode),
        symbols=np.load(cache_dir / "symbols.npy", mmap_mode=mmap_mode),
        arrays=arrays,
    )


def finite_divide(num: np.ndarray, den: np.ndarray, default: float = 0.0) -> np.ndarray:
    out = np.full(np.broadcast_shapes(num.shape, den.shape), default, dtype=np.float32)
    valid = np.isfinite(num) & np.isfinite(den) & (den != 0.0)
    np.divide(num, den, out=out, where=valid)
    return out


def rolling_swing_levels(low: np.ndarray, high: np.ndarray, valid: np.ndarray, lookbacks: list[int]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    safe_low = np.where(valid, low, np.nan).astype(np.float32)
    safe_high = np.where(valid, high, np.nan).astype(np.float32)
    levels: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lookback in sorted(set(int(v) for v in lookbacks)):
        swing_low = np.full_like(low, np.nan, dtype=np.float32)
        swing_high = np.full_like(high, np.nan, dtype=np.float32)
        for bar in range(BAR_COUNT):
            start = max(0, bar - lookback + 1)
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                swing_low[:, :, bar] = np.nanmin(safe_low[:, :, start : bar + 1], axis=2)
                swing_high[:, :, bar] = np.nanmax(safe_high[:, :, start : bar + 1], axis=2)
        levels[lookback] = (swing_low, swing_high)
    return levels


def make_signal_specs(args: argparse.Namespace) -> list[SignalSpec]:
    triggers = parse_csv_strings(args.triggers)
    allowed = {"cross", "sustain", "pullback"}
    unknown = sorted(set(triggers) - allowed)
    if unknown:
        raise ValueError(f"Unsupported triggers: {unknown}. Allowed: {sorted(allowed)}")
    return [
        SignalSpec(trigger, rsi_edge, confirm, max_dist, rvol)
        for trigger, rsi_edge, confirm, max_dist, rvol in itertools.product(
            triggers,
            parse_csv_floats(args.rsi_edges),
            parse_csv_floats(args.confirm_pcts),
            parse_csv_floats(args.max_vwap_dist_pcts),
            parse_csv_floats(args.rvol_mins),
        )
    ]


def make_stop_specs(args: argparse.Namespace) -> list[StopSpec]:
    modes = parse_csv_strings(args.stop_modes)
    specs: list[StopSpec] = []
    if "swing" in modes:
        specs.extend(StopSpec("swing", float(value)) for value in parse_csv_ints(args.swing_lookbacks))
    if "pct" in modes:
        specs.extend(StopSpec("pct", float(value)) for value in parse_csv_floats(args.fixed_stop_pcts))
    unknown = sorted(set(modes) - {"swing", "pct"})
    if unknown:
        raise ValueError(f"Unsupported stop modes: {unknown}. Allowed: swing,pct")
    if not specs:
        raise ValueError("At least one stop spec is required.")
    return specs


def date_mask(dates: np.ndarray, start_date: str | None, end_date: str | None) -> np.ndarray:
    mask = np.ones(dates.shape[0], dtype=bool)
    if start_date:
        mask &= dates >= np.datetime64(start_date)
    if end_date:
        mask &= dates <= np.datetime64(end_date)
    return mask


def split_masks(dates: np.ndarray, trade_mask: np.ndarray) -> dict[str, np.ndarray]:
    active = dates[trade_mask]
    if active.size == 0:
        empty = np.zeros_like(trade_mask, dtype=bool)
        return {"train": empty, "validation": empty, "out_of_sample": empty}
    start = pd.Timestamp(str(active.min()))
    end = pd.Timestamp(str(active.max()))
    span = end - start
    cut1 = np.datetime64((start + span * 0.60).date())
    cut2 = np.datetime64((start + span * 0.80).date())
    return {
        "train": trade_mask & (dates < cut1),
        "validation": trade_mask & (dates >= cut1) & (dates < cut2),
        "out_of_sample": trade_mask & (dates >= cut2),
    }


def candidate_rows(cache: CacheBundle, spec: SignalSpec, args: argparse.Namespace) -> dict[str, np.ndarray]:
    o = cache["open"]
    h = cache["high"]
    l = cache["low"]
    c = cache["close"]
    vwap = cache["vwap"]
    rsi = cache["rsi14"]
    relvol = cache["relvol20"]
    valid = cache["valid"].astype(bool)

    signal_start = max(1, parse_time_to_bar(args.entry_start, 0))
    signal_end = min(BAR_COUNT - 2, parse_time_to_bar(args.entry_end, BAR_COUNT - 2))
    if signal_start > signal_end:
        raise ValueError("--entry-start must be before --entry-end")
    bars = np.arange(signal_start, signal_end + 1, dtype=np.int32)
    prev = bars - 1
    date_ok = date_mask(cache.dates, args.start_date, args.end_date)

    base = (
        date_ok.reshape(-1, 1, 1)
        & valid[:, :, bars]
        & valid[:, :, prev]
        & valid[:, :, bars + 1]
        & np.isfinite(vwap[:, :, bars])
        & np.isfinite(vwap[:, :, prev])
        & np.isfinite(rsi[:, :, bars])
    )
    if spec.rvol_min > 0.0:
        base &= np.isfinite(relvol[:, :, bars]) & (relvol[:, :, bars] >= np.float32(spec.rvol_min))

    confirm = np.float32(spec.confirm_pct / 100.0)
    touch = np.float32(args.pullback_touch_pct / 100.0)
    max_dist = np.float32(spec.max_vwap_dist_pct)
    close_now = c[:, :, bars]
    open_now = o[:, :, bars]
    low_now = l[:, :, bars]
    high_now = h[:, :, bars]
    vwap_now = vwap[:, :, bars]
    close_prev = c[:, :, prev]
    vwap_prev = vwap[:, :, prev]
    vwap_dist = np.abs((close_now / vwap_now - 1.0) * 100.0).astype(np.float32)
    base &= np.isfinite(vwap_dist) & (vwap_dist <= max_dist)

    if args.require_directional:
        bull_candle = close_now > open_now
        bear_candle = close_now < open_now
    else:
        bull_candle = np.ones_like(base, dtype=bool)
        bear_candle = np.ones_like(base, dtype=bool)

    long_bias = close_now >= vwap_now * (1.0 + confirm)
    short_bias = close_now <= vwap_now * (1.0 - confirm)
    long_rsi = rsi[:, :, bars] >= np.float32(50.0 + spec.rsi_edge)
    short_rsi = rsi[:, :, bars] <= np.float32(50.0 - spec.rsi_edge)

    if spec.trigger == "cross":
        long_trigger = (close_prev <= vwap_prev) & long_bias
        short_trigger = (close_prev >= vwap_prev) & short_bias
    elif spec.trigger == "sustain":
        long_trigger = (close_prev >= vwap_prev * (1.0 + confirm)) & long_bias
        short_trigger = (close_prev <= vwap_prev * (1.0 - confirm)) & short_bias
    elif spec.trigger == "pullback":
        long_trigger = (close_prev >= vwap_prev * (1.0 + confirm)) & (low_now <= vwap_now * (1.0 + touch)) & long_bias
        short_trigger = (close_prev <= vwap_prev * (1.0 - confirm)) & (high_now >= vwap_now * (1.0 - touch)) & short_bias
    else:
        raise ValueError(f"Unexpected trigger: {spec.trigger}")

    long_mask = base & bull_candle & long_rsi & long_trigger
    short_mask = base & bear_candle & short_rsi & short_trigger
    long_day, long_symbol, long_local = np.nonzero(long_mask)
    short_day, short_symbol, short_local = np.nonzero(short_mask)
    if long_day.size + short_day.size == 0:
        empty_i = np.array([], dtype=np.int32)
        return {
            "day": empty_i,
            "symbol": empty_i,
            "signal_idx": empty_i,
            "entry_idx": empty_i,
            "direction": empty_i,
        }

    day = np.concatenate((long_day, short_day)).astype(np.int32)
    symbol = np.concatenate((long_symbol, short_symbol)).astype(np.int32)
    signal_idx = np.concatenate((bars[long_local], bars[short_local])).astype(np.int32)
    direction = np.concatenate((np.ones(long_day.size, dtype=np.int8), -np.ones(short_day.size, dtype=np.int8))).astype(np.int8)
    order = np.lexsort((direction, signal_idx, symbol, day))
    day = day[order]
    symbol = symbol[order]
    signal_idx = signal_idx[order]
    direction = direction[order]

    if args.one_trade_per_symbol_day and day.size:
        keys = day.astype(np.int64) * np.int64(cache.symbols.shape[0]) + symbol.astype(np.int64)
        first = np.unique(keys, return_index=True)[1]
        day = day[first]
        symbol = symbol[first]
        signal_idx = signal_idx[first]
        direction = direction[first]

    return {
        "day": day.astype(np.int32),
        "symbol": symbol.astype(np.int32),
        "signal_idx": signal_idx.astype(np.int32),
        "entry_idx": (signal_idx + 1).astype(np.int32),
        "direction": direction.astype(np.int8),
    }


def compute_stop_and_risk(
    cache: CacheBundle,
    candidates: dict[str, np.ndarray],
    stop_spec: StopSpec,
    swing_levels: dict[int, tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    day = candidates["day"]
    symbol = candidates["symbol"]
    signal_idx = candidates["signal_idx"]
    entry_idx = candidates["entry_idx"]
    direction = candidates["direction"]
    entry = cache["open"][day, symbol, entry_idx].astype(np.float32)
    if stop_spec.mode == "swing":
        swing_low, swing_high = swing_levels[int(stop_spec.value)]
        stop = np.where(direction > 0, swing_low[day, symbol, signal_idx], swing_high[day, symbol, signal_idx]).astype(np.float32)
    else:
        pct = np.float32(stop_spec.value / 100.0)
        stop = np.where(direction > 0, entry * (1.0 - pct), entry * (1.0 + pct)).astype(np.float32)
    risk_points = np.where(direction > 0, entry - stop, stop - entry).astype(np.float32)
    risk_pct = (risk_points / entry * 100.0).astype(np.float32)
    return stop, risk_pct


def simulate_exits(
    cache: CacheBundle,
    candidates: dict[str, np.ndarray],
    stop: np.ndarray,
    risk_pct: np.ndarray,
    rr: float,
    args: argparse.Namespace,
    return_trades: bool = False,
) -> dict[str, np.ndarray]:
    day = candidates["day"].astype(np.int32)
    symbol = candidates["symbol"].astype(np.int32)
    signal_idx = candidates["signal_idx"].astype(np.int32)
    entry_idx = candidates["entry_idx"].astype(np.int32)
    direction = candidates["direction"].astype(np.int8)
    entry = cache["open"][day, symbol, entry_idx].astype(np.float32)

    risk_ok = (
        np.isfinite(entry)
        & (entry > 0.0)
        & np.isfinite(stop)
        & np.isfinite(risk_pct)
        & (risk_pct >= np.float32(args.min_risk_pct))
        & (risk_pct <= np.float32(args.max_risk_pct))
    )
    if not risk_ok.any():
        return empty_sim(return_trades)

    day = day[risk_ok]
    symbol = symbol[risk_ok]
    signal_idx = signal_idx[risk_ok]
    entry_idx = entry_idx[risk_ok]
    direction = direction[risk_ok]
    entry = entry[risk_ok]
    stop = stop[risk_ok].astype(np.float32)
    risk_pct = risk_pct[risk_ok].astype(np.float32)

    n = day.size
    all_day: list[np.ndarray] = []
    all_symbol: list[np.ndarray] = []
    all_signal_idx: list[np.ndarray] = []
    all_entry_idx: list[np.ndarray] = []
    all_exit_idx: list[np.ndarray] = []
    all_direction: list[np.ndarray] = []
    all_entry: list[np.ndarray] = []
    all_stop: list[np.ndarray] = []
    all_target: list[np.ndarray] = []
    all_exit: list[np.ndarray] = []
    all_gross: list[np.ndarray] = []
    all_net: list[np.ndarray] = []
    all_r: list[np.ndarray] = []
    all_risk: list[np.ndarray] = []
    all_exit_type: list[np.ndarray] = []
    all_hold_bars: list[np.ndarray] = []

    high = cache["high"]
    low = cache["low"]
    open_ = cache["open"]
    close = cache["close"]
    valid = cache["valid"].astype(bool)
    offsets = np.arange(BAR_COUNT, dtype=np.int32)
    cost = np.float32(args.round_trip_cost_pct)

    for start in range(0, n, int(args.sim_chunk_size)):
        end = min(n, start + int(args.sim_chunk_size))
        d = day[start:end]
        s = symbol[start:end]
        sig = signal_idx[start:end]
        ent_idx = entry_idx[start:end]
        direc = direction[start:end]
        ent = entry[start:end]
        stp = stop[start:end]
        risk = risk_pct[start:end]
        risk_points = ent * risk / 100.0
        tgt = np.where(direc > 0, ent + risk_points * np.float32(rr), ent - risk_points * np.float32(rr)).astype(np.float32)

        path_idx = ent_idx.reshape(-1, 1) + offsets.reshape(1, -1)
        path_valid_idx = path_idx < BAR_COUNT
        clipped = np.minimum(path_idx, BAR_COUNT - 1)
        ph = high[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pl = low[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        po = open_[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pc = close[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pv = valid[d.reshape(-1, 1), s.reshape(-1, 1), clipped] & path_valid_idx

        long_rows = direc > 0
        stop_hit = np.where(long_rows.reshape(-1, 1), pl <= stp.reshape(-1, 1), ph >= stp.reshape(-1, 1)) & pv
        target_hit = np.where(long_rows.reshape(-1, 1), ph >= tgt.reshape(-1, 1), pl <= tgt.reshape(-1, 1)) & pv
        any_stop = stop_hit.any(axis=1)
        any_target = target_hit.any(axis=1)
        no_hit_value = BAR_COUNT + 1
        first_stop = np.where(any_stop, np.argmax(stop_hit, axis=1), no_hit_value).astype(np.int32)
        first_target = np.where(any_target, np.argmax(target_hit, axis=1), no_hit_value).astype(np.int32)
        stop_first = any_stop & (first_stop <= first_target)
        target_first = any_target & (first_target < first_stop)
        any_valid = pv.any(axis=1)
        last_valid = np.where(any_valid, pv.shape[1] - 1 - np.argmax(pv[:, ::-1], axis=1), 0).astype(np.int32)
        exit_offset = np.where(stop_first, first_stop, np.where(target_first, first_target, last_valid)).astype(np.int32)
        row_idx = np.arange(d.size, dtype=np.int32)
        exit_open = po[row_idx, exit_offset].astype(np.float32)
        exit_close = pc[row_idx, exit_offset].astype(np.float32)

        long_stop_gap = long_rows & stop_first & (exit_open <= stp)
        short_stop_gap = (~long_rows) & stop_first & (exit_open >= stp)
        long_target_gap = long_rows & target_first & (exit_open >= tgt)
        short_target_gap = (~long_rows) & target_first & (exit_open <= tgt)
        stop_gap = long_stop_gap | short_stop_gap
        target_gap = long_target_gap | short_target_gap
        exit_price = np.where(
            stop_first,
            np.where(stop_gap, exit_open, stp),
            np.where(target_first, np.where(target_gap, exit_open, tgt), exit_close),
        ).astype(np.float32)
        gross = np.where(long_rows, (exit_price - ent) / ent * 100.0, (ent - exit_price) / ent * 100.0).astype(np.float32)
        net = (gross - cost).astype(np.float32)
        r_mult = (net / risk).astype(np.float32)
        exit_type = np.where(stop_gap, 3, np.where(stop_first, 2, np.where(target_first, 1, 0))).astype(np.int8)
        exit_idx = (ent_idx + exit_offset).astype(np.int32)

        keep = any_valid & np.isfinite(net)
        if not keep.any():
            continue
        all_day.append(d[keep])
        all_symbol.append(s[keep])
        all_signal_idx.append(sig[keep])
        all_entry_idx.append(ent_idx[keep])
        all_exit_idx.append(exit_idx[keep])
        all_direction.append(direc[keep])
        all_entry.append(ent[keep])
        all_stop.append(stp[keep])
        all_target.append(tgt[keep])
        all_exit.append(exit_price[keep])
        all_gross.append(gross[keep])
        all_net.append(net[keep])
        all_r.append(r_mult[keep])
        all_risk.append(risk[keep])
        all_exit_type.append(exit_type[keep])
        all_hold_bars.append((exit_offset[keep] + 1).astype(np.int16))

    if not all_net:
        return empty_sim(return_trades)

    out = {
        "day": np.concatenate(all_day).astype(np.int32),
        "symbol": np.concatenate(all_symbol).astype(np.int32),
        "net_pct": np.concatenate(all_net).astype(np.float32),
        "gross_pct": np.concatenate(all_gross).astype(np.float32),
        "r_multiple": np.concatenate(all_r).astype(np.float32),
        "risk_pct": np.concatenate(all_risk).astype(np.float32),
        "exit_type": np.concatenate(all_exit_type).astype(np.int8),
        "hold_bars": np.concatenate(all_hold_bars).astype(np.int16),
    }
    if return_trades:
        out.update(
            {
                "signal_idx": np.concatenate(all_signal_idx).astype(np.int32),
                "entry_idx": np.concatenate(all_entry_idx).astype(np.int32),
                "exit_idx": np.concatenate(all_exit_idx).astype(np.int32),
                "direction": np.concatenate(all_direction).astype(np.int8),
                "entry_price": np.concatenate(all_entry).astype(np.float32),
                "stop_price": np.concatenate(all_stop).astype(np.float32),
                "target_price": np.concatenate(all_target).astype(np.float32),
                "exit_price": np.concatenate(all_exit).astype(np.float32),
            }
        )
    return out


def empty_sim(return_trades: bool = False) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {
        "day": np.array([], dtype=np.int32),
        "symbol": np.array([], dtype=np.int32),
        "net_pct": np.array([], dtype=np.float32),
        "gross_pct": np.array([], dtype=np.float32),
        "r_multiple": np.array([], dtype=np.float32),
        "risk_pct": np.array([], dtype=np.float32),
        "exit_type": np.array([], dtype=np.int8),
        "hold_bars": np.array([], dtype=np.int16),
    }
    if return_trades:
        out.update(
            {
                "signal_idx": np.array([], dtype=np.int32),
                "entry_idx": np.array([], dtype=np.int32),
                "exit_idx": np.array([], dtype=np.int32),
                "direction": np.array([], dtype=np.int8),
                "entry_price": np.array([], dtype=np.float32),
                "stop_price": np.array([], dtype=np.float32),
                "target_price": np.array([], dtype=np.float32),
                "exit_price": np.array([], dtype=np.float32),
            }
        )
    return out


def metric_block(sim: dict[str, np.ndarray], day_count: int, mask: np.ndarray) -> dict[str, float]:
    selected = mask[sim["day"]] if sim["day"].size else np.array([], dtype=bool)
    net = sim["net_pct"][selected].astype(np.float32)
    r_mult = sim["r_multiple"][selected].astype(np.float32)
    risk = sim["risk_pct"][selected].astype(np.float32)
    days = sim["day"][selected].astype(np.int32)
    exit_type = sim["exit_type"][selected].astype(np.int8)
    hold_bars = sim["hold_bars"][selected].astype(np.float32)
    trading_days = int(mask.sum())
    if trading_days == 0 or net.size == 0:
        return {
            "trading_days": float(trading_days),
            "trades": 0.0,
            "trades_per_day": 0.0,
            "avg_net_pct": 0.0,
            "median_net_pct": 0.0,
            "win_pct": 0.0,
            "expectancy_r": 0.0,
            "total_net_pct": 0.0,
            "daily_sharpe": 0.0,
            "max_drawdown_sum_pct": 0.0,
            "target_pct": 0.0,
            "stop_pct": 0.0,
            "eod_pct": 0.0,
            "avg_risk_pct": 0.0,
            "avg_hold_minutes": 0.0,
        }
    day_net = np.bincount(days, weights=net, minlength=day_count).astype(np.float32)
    values = day_net[mask].astype(np.float32)
    mean_day = float(values.mean()) if values.size else 0.0
    std_day = float(values.std(ddof=0)) if values.size else 0.0
    curve = np.cumsum(values, dtype=np.float32)
    drawdown = curve - np.maximum.accumulate(curve)
    return {
        "trading_days": float(trading_days),
        "trades": float(net.size),
        "trades_per_day": float(net.size / trading_days) if trading_days else 0.0,
        "avg_net_pct": float(net.mean()),
        "median_net_pct": float(np.median(net)),
        "win_pct": float((net > 0.0).mean() * 100.0),
        "expectancy_r": float(r_mult.mean()) if r_mult.size else 0.0,
        "total_net_pct": float(net.sum()),
        "daily_sharpe": float(mean_day / std_day * math.sqrt(252.0)) if std_day > 0.0 else 0.0,
        "max_drawdown_sum_pct": float(drawdown.min()) if drawdown.size else 0.0,
        "target_pct": float((exit_type == 1).mean() * 100.0) if exit_type.size else 0.0,
        "stop_pct": float(np.isin(exit_type, [2, 3]).mean() * 100.0) if exit_type.size else 0.0,
        "eod_pct": float((exit_type == 0).mean() * 100.0) if exit_type.size else 0.0,
        "avg_risk_pct": float(risk.mean()) if risk.size else 0.0,
        "avg_hold_minutes": float(hold_bars.mean() * BAR_MINUTES) if hold_bars.size else 0.0,
    }


def score_row(row: dict[str, float], min_trades: int) -> float:
    validation_avg = float(row["validation_avg_net_pct"])
    oos_avg = float(row["out_of_sample_avg_net_pct"])
    full_avg = float(row["full_avg_net_pct"])
    validation_sharpe = float(np.clip(row["validation_daily_sharpe"], -8.0, 8.0))
    oos_sharpe = float(np.clip(row["out_of_sample_daily_sharpe"], -8.0, 8.0))
    sample_bonus = min(float(row["full_trades"]), 2000.0) / 80.0
    sample_penalty = max(0.0, float(min_trades) - float(row["out_of_sample_trades"])) * 0.08
    negative_penalty = 0.0
    if validation_avg <= 0.0:
        negative_penalty += 20.0 + abs(validation_avg) * 30.0
    if oos_avg <= 0.0:
        negative_penalty += 25.0 + abs(oos_avg) * 35.0
    dd_penalty = abs(min(float(row["out_of_sample_max_drawdown_sum_pct"]), 0.0)) * 0.12
    return (
        validation_sharpe * 5.0
        + oos_sharpe * 6.0
        + validation_avg * 25.0
        + oos_avg * 32.0
        + full_avg * 10.0
        + sample_bonus
        - sample_penalty
        - negative_penalty
        - dd_penalty
    )


def summarize_config(
    sim: dict[str, np.ndarray],
    dates: np.ndarray,
    full_mask: np.ndarray,
    min_trades: int,
) -> tuple[dict[str, float], list[dict[str, float]], list[dict[str, float]]]:
    day_count = dates.shape[0]
    row: dict[str, float] = {}
    full_stats = metric_block(sim, day_count, full_mask)
    for key, value in full_stats.items():
        row[f"full_{key}"] = value

    split_rows: list[dict[str, float]] = []
    for split, mask in split_masks(dates, full_mask).items():
        stats = metric_block(sim, day_count, mask)
        for key, value in stats.items():
            row[f"{split}_{key}"] = value
        split_rows.append({"split": split, **stats})

    yearly_rows: list[dict[str, float]] = []
    years = sorted({int(str(date)[:4]) for date in dates[full_mask]})
    for year in years:
        mask = full_mask & np.char.startswith(dates.astype(str), str(year))
        yearly_rows.append({"year": float(year), **metric_block(sim, day_count, mask)})

    row["score"] = score_row(row, min_trades)
    return row, split_rows, yearly_rows


def attach_config(base: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config)
    out.update(base)
    return out


def trade_frame(
    cache: CacheBundle,
    sim: dict[str, np.ndarray],
    config: dict[str, Any],
    limit: int,
) -> pd.DataFrame:
    if sim["day"].size == 0:
        return pd.DataFrame()
    order = np.lexsort((sim["symbol"], sim["entry_idx"], sim["day"]))
    if limit > 0:
        order = order[:limit]
    day = sim["day"][order].astype(np.int32)
    symbol = sim["symbol"][order].astype(np.int32)
    exit_reason = np.array(["EOD", "TARGET", "SL", "SL_GAP"], dtype=object)
    frame = pd.DataFrame(
        {
            **config,
            "date": cache.dates[day].astype(str),
            "symbol": cache.symbols[symbol].astype(str),
            "direction": np.where(sim["direction"][order] > 0, "LONG", "SHORT"),
            "signal_time": bar_label(sim["signal_idx"][order]),
            "entry_time": bar_label(sim["entry_idx"][order]),
            "exit_time": bar_label(sim["exit_idx"][order]),
            "entry_price": sim["entry_price"][order],
            "stop_price": sim["stop_price"][order],
            "target_price": sim["target_price"][order],
            "exit_price": sim["exit_price"][order],
            "exit_reason": exit_reason[sim["exit_type"][order]],
            "risk_pct": sim["risk_pct"][order],
            "gross_pct": sim["gross_pct"][order],
            "net_pct": sim["net_pct"][order],
            "r_multiple": sim["r_multiple"][order],
            "hold_minutes": sim["hold_bars"][order].astype(np.int32) * BAR_MINUTES,
            "signal_rsi14": cache["rsi14"][day, symbol, sim["signal_idx"][order]],
            "signal_relvol20": cache["relvol20"][day, symbol, sim["signal_idx"][order]],
            "signal_close_vwap_dist_pct": (
                cache["close"][day, symbol, sim["signal_idx"][order]]
                / cache["vwap"][day, symbol, sim["signal_idx"][order]]
                - 1.0
            )
            * 100.0,
        }
    )
    for col in [
        "entry_price",
        "stop_price",
        "target_price",
        "exit_price",
        "risk_pct",
        "gross_pct",
        "net_pct",
        "r_multiple",
        "signal_rsi14",
        "signal_relvol20",
        "signal_close_vwap_dist_pct",
    ]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").round(4)
    return frame


def best_daily_curve(cache: CacheBundle, sim: dict[str, np.ndarray], full_mask: np.ndarray) -> pd.DataFrame:
    if sim["day"].size == 0:
        return pd.DataFrame()
    day_net = np.bincount(sim["day"], weights=sim["net_pct"], minlength=cache.dates.shape[0]).astype(np.float32)
    counts = np.bincount(sim["day"], minlength=cache.dates.shape[0]).astype(np.int32)
    dates = cache.dates[full_mask].astype(str)
    values = day_net[full_mask].astype(np.float32)
    out = pd.DataFrame(
        {
            "date": dates,
            "trades": counts[full_mask],
            "day_net_pct": values,
            "cum_net_pct": np.cumsum(values, dtype=np.float32),
        }
    )
    out["running_peak_pct"] = out["cum_net_pct"].cummax()
    out["drawdown_pct"] = out["cum_net_pct"] - out["running_peak_pct"]
    return out


def markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.head(max_rows).to_markdown(index=False)
    except Exception:
        return "```\n" + df.head(max_rows).to_csv(index=False) + "```"


def write_report(
    out_dir: Path,
    cache: CacheBundle,
    summary: pd.DataFrame,
    split_metrics: pd.DataFrame,
    yearly_metrics: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    volume_compare = (
        summary.groupby("rvol_min", as_index=False)
        .agg(
            configs=("config_id", "count"),
            best_score=("score", "max"),
            median_score=("score", "median"),
            best_full_avg_net_pct=("full_avg_net_pct", "max"),
            best_oos_avg_net_pct=("out_of_sample_avg_net_pct", "max"),
            best_oos_daily_sharpe=("out_of_sample_daily_sharpe", "max"),
        )
        .sort_values("best_score", ascending=False)
        if not summary.empty
        else pd.DataFrame()
    )
    stop_compare = (
        summary.groupby(["stop_name", "rr"], as_index=False)
        .agg(
            configs=("config_id", "count"),
            best_score=("score", "max"),
            best_oos_avg_net_pct=("out_of_sample_avg_net_pct", "max"),
            best_oos_daily_sharpe=("out_of_sample_daily_sharpe", "max"),
            median_full_trades=("full_trades", "median"),
        )
        .sort_values("best_score", ascending=False)
        if not summary.empty
        else pd.DataFrame()
    )
    top_cols = [
        "config_id",
        "trigger",
        "stop_name",
        "rr",
        "rsi_edge",
        "confirm_pct",
        "max_vwap_dist_pct",
        "rvol_min",
        "score",
        "full_trades",
        "full_avg_net_pct",
        "full_win_pct",
        "full_daily_sharpe",
        "out_of_sample_trades",
        "out_of_sample_avg_net_pct",
        "out_of_sample_daily_sharpe",
        "out_of_sample_max_drawdown_sum_pct",
    ]
    report = [
        "# VWAP + RSI 5-Minute Trend Lab",
        "",
        f"Cache: `{cache.cache_dir}`.",
        f"Data: `{cache.manifest.get('date_min')}` to `{cache.manifest.get('date_max')}`, "
        f"{cache.dates.shape[0]:,} dates, {cache.symbols.shape[0]:,} symbols, {BAR_COUNT} five-minute bars per day.",
        f"Trade window: `{args.entry_start}` to `{args.entry_end}`. Round-trip cost: `{args.round_trip_cost_pct:g}%`.",
        "",
        "## Strategy Translation",
        "",
        "- Source PDF rule: use 5-minute VWAP plus RSI(14) trend confirmation.",
        "- Long: candle confirms above session VWAP and RSI is above 50 plus the tested edge.",
        "- Short: candle confirms below session VWAP and RSI is below 50 minus the tested edge.",
        "- Entry: next 5-minute bar open after the signal candle closes.",
        "- Stops: tested recent swing high/low lookbacks and fixed percent stops.",
        "- Targets: tested fixed risk-reward multiples. Same-bar target/stop ambiguity is counted as stop first.",
        "- Volume check: `rvol_min` is the signal bar volume divided by its previous-20-trading-day average for the same 5-minute slot.",
        "",
        "## Best Configs",
        "",
        markdown_table(summary[[col for col in top_cols if col in summary.columns]], max_rows=20),
        "",
        "## Volume Filter Read",
        "",
        markdown_table(volume_compare, max_rows=20),
        "",
        "## Stop/Target Read",
        "",
        markdown_table(stop_compare, max_rows=30),
        "",
        "## Walk-Forward Splits",
        "",
        markdown_table(split_metrics.head(80), max_rows=80),
        "",
        "## Yearly Top Rows",
        "",
        markdown_table(yearly_metrics.head(80), max_rows=80),
        "",
        "## Practical Read",
        "",
        "Prefer rows that keep positive validation and out-of-sample expectancy, not just the best full-period total. "
        "If the best rows only work without a volume filter, treat the volume filter as optional rather than mandatory.",
    ]
    (out_dir / "final_report.md").write_text("\n".join(report), encoding="utf-8")


def run_backtest(args: argparse.Namespace) -> None:
    cache_dir = build_cache(args) if args.build_cache else Path(args.cache_dir)
    cache = load_cache(cache_dir, mmap=True)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    full_mask = date_mask(cache.dates, args.start_date, args.end_date)
    if not full_mask.any():
        raise ValueError("No cache dates match the requested date range.")

    signal_specs = make_signal_specs(args)
    stop_specs = make_stop_specs(args)
    rr_values = parse_csv_floats(args.rr_values)
    swing_lookbacks = sorted({int(spec.value) for spec in stop_specs if spec.mode == "swing"})
    log(f"Loaded cache: {cache.dates.shape[0]:,} dates x {cache.symbols.shape[0]:,} symbols")
    log(f"Grid: {len(signal_specs):,} signal specs x {len(stop_specs):,} stops x {len(rr_values):,} RR values")
    swing_levels = rolling_swing_levels(cache["low"], cache["high"], cache["valid"].astype(bool), swing_lookbacks) if swing_lookbacks else {}

    summary_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    config_index: dict[int, tuple[SignalSpec, StopSpec, float]] = {}
    config_id = 0

    for sig_idx, signal_spec in enumerate(signal_specs, start=1):
        log(f"Signals {sig_idx:,}/{len(signal_specs):,}: {signal_spec.name}")
        candidates = candidate_rows(cache, signal_spec, args)
        if candidates["day"].size == 0:
            continue
        log(f"  candidates: {candidates['day'].size:,}")
        for stop_spec in stop_specs:
            stop, risk_pct = compute_stop_and_risk(cache, candidates, stop_spec, swing_levels)
            for rr in rr_values:
                config_id += 1
                config = {
                    "config_id": config_id,
                    "signal_name": signal_spec.name,
                    "trigger": signal_spec.trigger,
                    "rsi_edge": signal_spec.rsi_edge,
                    "confirm_pct": signal_spec.confirm_pct,
                    "max_vwap_dist_pct": signal_spec.max_vwap_dist_pct,
                    "rvol_min": signal_spec.rvol_min,
                    "stop_name": stop_spec.name,
                    "stop_mode": stop_spec.mode,
                    "stop_value": stop_spec.value,
                    "rr": float(rr),
                }
                sim = simulate_exits(cache, candidates, stop, risk_pct, rr, args, return_trades=False)
                if sim["day"].size < int(args.min_trades):
                    continue
                metrics, splits, yearly = summarize_config(sim, cache.dates, full_mask, int(args.min_trades))
                summary_rows.append(attach_config(metrics, config))
                split_rows.extend(attach_config({"split": item.pop("split"), **item}, config) for item in splits)
                yearly_rows.extend(attach_config({"year": int(item.pop("year")), **item}, config) for item in yearly)
                config_index[config_id] = (signal_spec, stop_spec, float(rr))

    summary = pd.DataFrame(summary_rows)
    split_metrics = pd.DataFrame(split_rows)
    yearly_metrics = pd.DataFrame(yearly_rows)
    if summary.empty:
        summary.to_csv(out_dir / "summary.csv", index=False)
        split_metrics.to_csv(out_dir / "split_metrics.csv", index=False)
        yearly_metrics.to_csv(out_dir / "yearly_metrics.csv", index=False)
        write_report(out_dir, cache, summary, split_metrics, yearly_metrics, args)
        log("No configs reached --min-trades.")
        return

    summary = summary.sort_values(["score", "out_of_sample_avg_net_pct", "full_daily_sharpe"], ascending=False).reset_index(drop=True)
    split_metrics = split_metrics.sort_values(["config_id", "split"]).reset_index(drop=True)
    yearly_metrics = yearly_metrics.sort_values(["config_id", "year"]).reset_index(drop=True)

    float_cols = summary.select_dtypes(include=["float32", "float64"]).columns
    summary[float_cols] = summary[float_cols].round(4)
    for frame in [split_metrics, yearly_metrics]:
        fcols = frame.select_dtypes(include=["float32", "float64"]).columns
        frame[fcols] = frame[fcols].round(4)

    summary.to_csv(out_dir / "summary.csv", index=False)
    split_metrics.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly_metrics.to_csv(out_dir / "yearly_metrics.csv", index=False)

    volume_compare = summary.groupby("rvol_min", as_index=False).agg(
        configs=("config_id", "count"),
        best_score=("score", "max"),
        median_score=("score", "median"),
        best_full_avg_net_pct=("full_avg_net_pct", "max"),
        best_oos_avg_net_pct=("out_of_sample_avg_net_pct", "max"),
        best_oos_daily_sharpe=("out_of_sample_daily_sharpe", "max"),
    ).sort_values("best_score", ascending=False)
    volume_compare.to_csv(out_dir / "volume_filter_metrics.csv", index=False)

    stop_target = summary.groupby(["stop_name", "rr"], as_index=False).agg(
        configs=("config_id", "count"),
        best_score=("score", "max"),
        best_oos_avg_net_pct=("out_of_sample_avg_net_pct", "max"),
        best_oos_daily_sharpe=("out_of_sample_daily_sharpe", "max"),
        median_full_trades=("full_trades", "median"),
    ).sort_values("best_score", ascending=False)
    stop_target.to_csv(out_dir / "stop_target_metrics.csv", index=False)

    top_ids = [int(value) for value in summary["config_id"].head(int(args.save_top_configs)).tolist()]
    trade_frames: list[pd.DataFrame] = []
    for top_id in top_ids:
        signal_spec, stop_spec, rr = config_index[top_id]
        candidates = candidate_rows(cache, signal_spec, args)
        stop, risk_pct = compute_stop_and_risk(cache, candidates, stop_spec, swing_levels)
        sim = simulate_exits(cache, candidates, stop, risk_pct, rr, args, return_trades=True)
        config = summary.loc[summary["config_id"].eq(top_id)].iloc[0][
            [
                "config_id",
                "trigger",
                "rsi_edge",
                "confirm_pct",
                "max_vwap_dist_pct",
                "rvol_min",
                "stop_name",
                "stop_mode",
                "stop_value",
                "rr",
            ]
        ].to_dict()
        trade_frames.append(trade_frame(cache, sim, config, int(args.top_trade_limit)))
        if top_id == int(summary.iloc[0]["config_id"]):
            best_daily_curve(cache, sim, full_mask).to_csv(out_dir / "best_equity_curve.csv", index=False)

    top_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    top_trades.to_csv(out_dir / "top_trade_log.csv", index=False)
    write_report(out_dir, cache, summary, split_metrics, yearly_metrics, args)

    log("Best rows")
    print(summary.head(12).to_string(index=False), flush=True)
    log(f"Wrote outputs: {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a 5-minute NumPy cache and backtest the VWAP + RSI trend strategy.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--parquet-glob", default="candles_20*.parquet")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--volume-groups-file", type=Path, default=DEFAULT_VOLUME_GROUPS)
    parser.add_argument("--volume-groups", default="MEGA,LARGE")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--all-symbols", action="store_true")
    parser.add_argument("--min-day-bars", type=int, default=70)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--build-cache", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--list-cache", action="store_true")

    parser.add_argument("--triggers", default="cross,sustain,pullback")
    parser.add_argument("--rsi-edges", default="0,5")
    parser.add_argument("--confirm-pcts", default="0,0.05")
    parser.add_argument("--max-vwap-dist-pcts", default="0.75,1.5")
    parser.add_argument("--rvol-mins", default="0,1.5")
    parser.add_argument("--pullback-touch-pct", type=float, default=0.10)
    parser.add_argument("--require-directional", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--entry-start", default="09:15")
    parser.add_argument("--entry-end", default="14:55")
    parser.add_argument("--one-trade-per-symbol-day", action=argparse.BooleanOptionalAction, default=True)

    parser.add_argument("--stop-modes", default="swing,pct")
    parser.add_argument("--swing-lookbacks", default="3,5,8")
    parser.add_argument("--fixed-stop-pcts", default="0.5,0.75,1")
    parser.add_argument("--rr-values", default="1,1.5,2,2.5,3")
    parser.add_argument("--min-risk-pct", type=float, default=0.05)
    parser.add_argument("--max-risk-pct", type=float, default=3.0)
    parser.add_argument("--round-trip-cost-pct", type=float, default=0.10)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--sim-chunk-size", type=int, default=100000)
    parser.add_argument("--save-top-configs", type=int, default=5)
    parser.add_argument("--top-trade-limit", type=int, default=50000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_cache:
        cache = load_cache(Path(args.cache_dir), mmap=True)
        print(f"Cache: {cache.cache_dir}")
        print(f"Dates: {cache.manifest.get('date_min')} to {cache.manifest.get('date_max')} ({cache.dates.shape[0]:,})")
        print(f"Symbols: {cache.symbols.shape[0]:,}")
        for name in ARRAY_NAMES:
            arr = cache[name]
            print(f"{name}: shape={arr.shape} dtype={arr.dtype}")
        return
    run_backtest(args)


if __name__ == "__main__":
    main()
