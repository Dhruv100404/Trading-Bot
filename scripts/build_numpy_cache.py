from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_CACHE_DIR = ROOT / "parquets" / "cache" / "swing_numpy_v1"
CACHE_VERSION = "swing_numpy_v1"

RAW_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "day_open",
    "gap_pct",
    "close_vwap",
    "avg_vol_rate",
    "avg_buy_ratio",
]

FEATURE_COLUMNS = [
    "prev_close",
    "next_open",
    "ret1",
    "ret3",
    "ret5",
    "ret10",
    "ret20",
    "ret60",
    "ret120",
    "sma5",
    "sma10",
    "sma20",
    "sma50",
    "sma100",
    "sma200",
    "ema10",
    "ema20",
    "ema50",
    "ema200",
    "atr14",
    "rsi10",
    "rsi14",
    "avg_volume20",
    "relvol20",
    "prior_high20",
    "prior_high55",
    "prior_high252",
    "prior_low20",
    "prior_low55",
    "high_52w",
    "low_52w",
    "close_location",
    "range_pct",
    "range_atr",
    "recovery_from_low_pct",
]

ARRAY_COLUMNS = [*RAW_COLUMNS, "buckets", *FEATURE_COLUMNS]
SOURCE_COLUMNS = [
    "date",
    "symbol",
    "bucket",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "day_open",
    "gap_pct",
    "vwap",
    "vol_rate",
    "buy_ratio",
]


@dataclass
class NumpyCache:
    cache_dir: Path
    manifest: dict[str, Any]
    dates: np.ndarray
    symbols: np.ndarray
    arrays: dict[str, np.ndarray]

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]

    def names(self) -> list[str]:
        return sorted(self.arrays)


def monthly_key(path: Path) -> str | None:
    match = re.match(r"candles_(\d{6})\.parquet$", path.name)
    return match.group(1) if match else None


def resolve_monthly_files(
    parquet_dir: Path,
    parquet_glob: str,
    start_date: str | None,
    end_date: str | None,
) -> list[Path]:
    start_month = start_date[:7].replace("-", "") if start_date else None
    end_month = end_date[:7].replace("-", "") if end_date else None
    files: list[Path] = []
    for path in sorted(parquet_dir.glob(parquet_glob)):
        key = monthly_key(path)
        if key is None:
            continue
        if start_month and key < start_month:
            continue
        if end_month and key > end_month:
            continue
        files.append(path)
    return files


def load_symbol_filter(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {str(item).upper() for item in data}
    if isinstance(data, dict):
        symbols: set[str] = set()
        if isinstance(data.get("symbols"), list):
            symbols.update(str(item).upper() for item in data["symbols"])
        if isinstance(data.get("volume_groups"), dict):
            for group_symbols in data["volume_groups"].values():
                symbols.update(str(item).upper() for item in group_symbols)
        return symbols
    raise ValueError(f"Unsupported symbols file format: {path}")


def symbols_hash(symbols: set[str] | None) -> str:
    if not symbols:
        return "all"
    joined = "\n".join(sorted(symbols))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def source_fingerprint(
    files: list[Path],
    start_date: str | None,
    end_date: str | None,
    symbols: set[str] | None,
    min_buckets: int,
) -> dict[str, Any]:
    source_files = []
    for path in files:
        stat = path.stat()
        source_files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return {
        "cache_version": CACHE_VERSION,
        "start_date": start_date,
        "end_date": end_date,
        "min_buckets": min_buckets,
        "symbols_hash": symbols_hash(symbols),
        "source_files": source_files,
    }


def manifest_matches(cache_dir: Path, fingerprint: dict[str, Any]) -> bool:
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if manifest.get("fingerprint") != fingerprint:
        return False
    arrays = manifest.get("arrays", {})
    return all((cache_dir / f"{name}.npy").exists() for name in arrays) and (cache_dir / "dates.npy").exists() and (cache_dir / "symbols.npy").exists()


def aggregate_month(path: Path, symbols: set[str] | None, start_date: str | None, end_date: str | None, min_buckets: int) -> pd.DataFrame:
    schema_names = set(pq.read_schema(path).names)
    columns = [col for col in SOURCE_COLUMNS if col in schema_names]
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
    df = df.dropna(subset=["date", "symbol", "bucket", "open", "high", "low", "close"])
    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    if df.empty:
        return pd.DataFrame()

    for col in ["bucket", "open", "high", "low", "close", "volume", "day_open", "gap_pct", "vwap", "vol_rate", "buy_ratio"]:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[
        (df["bucket"].between(1, 375))
        & (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["high"] >= df[["open", "close"]].max(axis=1))
        & (df["low"] <= df[["open", "close"]].min(axis=1))
    ].copy()
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(["symbol", "date", "bucket"])
    grouped = df.groupby(["symbol", "date"], sort=False)
    daily = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        day_open=("day_open", "first"),
        gap_pct=("gap_pct", "first"),
        close_vwap=("vwap", "last"),
        avg_vol_rate=("vol_rate", "mean"),
        avg_buy_ratio=("buy_ratio", "mean"),
        buckets=("bucket", "nunique"),
    ).reset_index()
    daily = daily[daily["buckets"] >= min_buckets].copy()
    daily["day_open"] = daily["day_open"].fillna(daily["open"])
    return daily


def add_features(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.sort_values(["symbol", "date"]).copy()
    g = daily.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    daily["prev_close"] = prev_close
    daily["next_open"] = g["open"].shift(-1)
    daily["ret1"] = daily["close"] / prev_close - 1.0

    for n in [3, 5, 10, 20, 60, 120]:
        daily[f"ret{n}"] = daily["close"] / g["close"].shift(n) - 1.0

    tr = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["atr14"] = tr.groupby(daily["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)

    for n in [5, 10, 20, 50, 100, 200]:
        daily[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(3, n // 2)).mean())
    for n in [10, 20, 50, 200]:
        daily[f"ema{n}"] = g["close"].transform(lambda s, n=n: s.ewm(span=n, adjust=False, min_periods=max(3, n // 2)).mean())

    delta = g["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for n in [10, 14]:
        avg_gain = gain.groupby(daily["symbol"]).rolling(n, min_periods=max(5, n // 2)).mean().reset_index(level=0, drop=True)
        avg_loss = loss.groupby(daily["symbol"]).rolling(n, min_periods=max(5, n // 2)).mean().reset_index(level=0, drop=True)
        rs = avg_gain / avg_loss.replace(0, np.nan)
        daily[f"rsi{n}"] = 100.0 - (100.0 / (1.0 + rs))

    daily["avg_volume20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    daily["relvol20"] = daily["volume"] / daily["avg_volume20"].replace(0, np.nan)

    for n in [20, 55, 252]:
        daily[f"prior_high{n}"] = g["high"].transform(lambda s, n=n: s.shift(1).rolling(n, min_periods=max(5, n // 2)).max())
    for n in [20, 55]:
        daily[f"prior_low{n}"] = g["low"].transform(lambda s, n=n: s.shift(1).rolling(n, min_periods=max(5, n // 2)).min())

    daily["high_52w"] = g["high"].transform(lambda s: s.rolling(252, min_periods=126).max())
    daily["low_52w"] = g["low"].transform(lambda s: s.rolling(252, min_periods=126).min())
    daily["close_location"] = (daily["close"] - daily["low"]) / (daily["high"] - daily["low"]).replace(0, np.nan)
    daily["range_pct"] = (daily["high"] - daily["low"]) / daily["close"].replace(0, np.nan)
    daily["range_atr"] = (daily["high"] - daily["low"]) / daily["atr14"].replace(0, np.nan)
    daily["recovery_from_low_pct"] = daily["close"] / daily["low"].replace(0, np.nan) - 1.0
    daily["gap_pct"] = daily["gap_pct"].fillna((daily["open"] / prev_close - 1.0) * 100.0)
    return daily


def matrix_from_column(daily: pd.DataFrame, day_codes: np.ndarray, symbol_codes: np.ndarray, shape: tuple[int, int], col: str) -> np.ndarray:
    if col == "buckets":
        out = np.zeros(shape, dtype=np.uint16)
        out[day_codes, symbol_codes] = daily[col].fillna(0).to_numpy(np.uint16, copy=False)
        return out
    out = np.full(shape, np.nan, dtype=np.float32)
    out[day_codes, symbol_codes] = daily[col].to_numpy(np.float32, copy=False)
    return out


def write_arrays(cache_dir: Path, daily: pd.DataFrame, fingerprint: dict[str, Any], started: float) -> None:
    dates = np.array(sorted(daily["date"].dt.strftime("%Y-%m-%d").unique()), dtype="datetime64[D]")
    symbols = np.array(sorted(daily["symbol"].unique()), dtype=str)
    date_labels = dates.astype(str)
    day_codes = pd.Categorical(daily["date"].dt.strftime("%Y-%m-%d"), categories=date_labels, ordered=True).codes.astype(np.int32)
    symbol_codes = pd.Categorical(daily["symbol"], categories=symbols, ordered=True).codes.astype(np.int32)
    valid = (day_codes >= 0) & (symbol_codes >= 0)
    daily = daily.loc[valid].reset_index(drop=True)
    day_codes = day_codes[valid]
    symbol_codes = symbol_codes[valid]
    shape = (dates.size, symbols.size)

    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(cache_dir / "dates.npy", dates)
    np.save(cache_dir / "symbols.npy", symbols)

    arrays: dict[str, dict[str, Any]] = {}
    for col in ARRAY_COLUMNS:
        arr = matrix_from_column(daily, day_codes, symbol_codes, shape, col)
        np.save(cache_dir / f"{col}.npy", arr)
        arrays[col] = {
            "file": f"{col}.npy",
            "dtype": str(arr.dtype),
            "shape": list(arr.shape),
        }

    manifest = {
        "cache_version": CACHE_VERSION,
        "created_at": pd.Timestamp.utcnow().isoformat(),
        "build_seconds": round(time.perf_counter() - started, 3),
        "fingerprint": fingerprint,
        "date_count": int(dates.size),
        "symbol_count": int(symbols.size),
        "row_count": int(len(daily)),
        "date_min": str(dates.min()) if dates.size else "",
        "date_max": str(dates.max()) if dates.size else "",
        "arrays": arrays,
    }
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def atomic_replace_dir(tmp_dir: Path, final_dir: Path) -> None:
    old_dir = final_dir.with_name(f"{final_dir.name}.old.{os.getpid()}")
    if old_dir.exists():
        shutil.rmtree(old_dir)
    if final_dir.exists():
        final_dir.rename(old_dir)
    tmp_dir.rename(final_dir)
    if old_dir.exists():
        shutil.rmtree(old_dir)


def build_numpy_cache(
    parquet_dir: Path = DEFAULT_PARQUET_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    parquet_glob: str = "candles_20*.parquet",
    start_date: str | None = None,
    end_date: str | None = None,
    symbols_file: Path | None = None,
    refresh: bool = False,
    min_buckets: int = 300,
) -> Path:
    started = time.perf_counter()
    symbols = load_symbol_filter(symbols_file)
    files = resolve_monthly_files(parquet_dir, parquet_glob, start_date, end_date)
    if not files:
        raise FileNotFoundError(f"No monthly parquet files matched {parquet_dir / parquet_glob}")

    fingerprint = source_fingerprint(files, start_date, end_date, symbols, min_buckets)
    if not refresh and manifest_matches(cache_dir, fingerprint):
        print(f"Cache already fresh: {cache_dir}")
        return cache_dir

    tmp_dir = cache_dir.with_name(f"{cache_dir.name}.tmp.{os.getpid()}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    frames: list[pd.DataFrame] = []
    try:
        for idx, path in enumerate(files, start=1):
            print(f"{idx}/{len(files)} aggregate {path.name}", flush=True)
            month = aggregate_month(path, symbols, start_date, end_date, min_buckets)
            if not month.empty:
                frames.append(month)
        if not frames:
            raise RuntimeError("No daily rows were produced for the selected files/filter.")

        daily = pd.concat(frames, ignore_index=True)
        daily = daily.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)
        print(f"Daily rows: {len(daily):,} | symbols: {daily['symbol'].nunique():,} | dates: {daily['date'].nunique():,}")
        print("Adding swing features", flush=True)
        daily = add_features(daily)
        print("Writing .npy arrays", flush=True)
        write_arrays(tmp_dir, daily, fingerprint, started)
        atomic_replace_dir(tmp_dir, cache_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise

    print(f"Wrote cache: {cache_dir}")
    return cache_dir


def load_numpy_cache(cache_dir: Path = DEFAULT_CACHE_DIR, mmap: bool = True) -> NumpyCache:
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Cache manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mmap_mode = "r" if mmap else None
    arrays = {
        name: np.load(cache_dir / spec["file"], mmap_mode=mmap_mode)
        for name, spec in manifest.get("arrays", {}).items()
    }
    return NumpyCache(
        cache_dir=cache_dir,
        manifest=manifest,
        dates=np.load(cache_dir / "dates.npy", mmap_mode=mmap_mode),
        symbols=np.load(cache_dir / "symbols.npy", mmap_mode=mmap_mode),
        arrays=arrays,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reusable NumPy cache for swing strategy scripts.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--parquet-glob", default="candles_20*.parquet")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--symbols-file", type=Path, default=None)
    parser.add_argument("--min-buckets", type=int, default=300)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--list-arrays", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_arrays:
        cache = load_numpy_cache(args.cache_dir)
        print(f"Cache: {cache.cache_dir}")
        print(f"Dates: {cache.manifest.get('date_min')} to {cache.manifest.get('date_max')} ({cache.dates.shape[0]:,})")
        print(f"Symbols: {cache.symbols.shape[0]:,}")
        for name in cache.names():
            arr = cache[name]
            print(f"{name}: shape={arr.shape} dtype={arr.dtype}")
        return
    build_numpy_cache(
        parquet_dir=args.parquet_dir,
        cache_dir=args.cache_dir,
        parquet_glob=args.parquet_glob,
        start_date=args.start_date,
        end_date=args.end_date,
        symbols_file=args.symbols_file,
        refresh=args.refresh,
        min_buckets=args.min_buckets,
    )


if __name__ == "__main__":
    main()
