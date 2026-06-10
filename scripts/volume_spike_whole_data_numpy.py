from __future__ import annotations

import argparse
import gc
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

import volume_spike_3x3_parquet_20day as lab


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_OUT_DIR = ROOT / "docs" / "volume_spike_3x3_parquet_20day_whole_data"
DEFAULT_START_DATE = "2021-01-01"
DEFAULT_END_DATE = "2026-05-30"

T0 = time.perf_counter()


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def monthly_file_key(path: Path) -> str | None:
    match = re.match(r"candles_(\d{6})\.parquet$", path.name)
    return match.group(1) if match else None


def monthly_files(parquet_dir: Path, glob: str, start: np.datetime64, end: np.datetime64) -> list[Path]:
    paths: list[Path] = []
    start_month = str(start)[:7].replace("-", "")
    end_month = str(end)[:7].replace("-", "")
    for path in sorted(parquet_dir.glob(glob)):
        key = monthly_file_key(path)
        if key is None:
            continue
        if start_month <= key <= end_month:
            paths.append(path)
    return paths


def read_chunk(paths: list[Path], end: np.datetime64, target_symbols: pd.Index | None) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame(columns=lab.COLS)
    df = pd.read_parquet([str(path) for path in paths], columns=lab.COLS)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[df["date"] <= str(end)]
    df = df[df["bucket"].between(1, int(lab.BUCKET_COUNT))]
    if target_symbols is not None and target_symbols.size > 0:
        df = df[df["symbol"].isin(target_symbols)]
    return df


def simulate_for_candidate_rows(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    cand_day: np.ndarray,
    cand_symbol: np.ndarray,
    cand_entry_idx: np.ndarray,
    hold_minutes: int,
    stop_pct: float,
    target_pct: float,
    round_trip_cost_pct: float,
) -> dict[str, np.ndarray]:
    n = cand_day.size
    out: dict[str, np.ndarray] = {
        f"net_h{hold_minutes}": np.full(n, np.nan, dtype=np.float32),
        f"gross_h{hold_minutes}": np.full(n, np.nan, dtype=np.float32),
        f"exit_idx_h{hold_minutes}": np.full(n, -1, dtype=np.int32),
        f"exit_offset_h{hold_minutes}": np.full(n, -1, dtype=np.int16),
        f"exit_price_h{hold_minutes}": np.full(n, np.nan, dtype=np.float32),
        f"target_first_h{hold_minutes}": np.zeros(n, dtype=bool),
        f"stop_first_h{hold_minutes}": np.zeros(n, dtype=bool),
        f"timeout_h{hold_minutes}": np.zeros(n, dtype=bool),
        f"exit_type_h{hold_minutes}": np.full(n, "", dtype=object),
    }
    safe = np.flatnonzero(cand_entry_idx + int(hold_minutes) <= min(lab.BUCKET_COUNT, lab.LATEST_EXIT_BUCKET)).astype(np.int32)
    if safe.size == 0:
        return out

    offsets = np.arange(int(hold_minutes), dtype=np.int32)
    path_idx = cand_entry_idx[safe].reshape(-1, 1) + offsets.reshape(1, -1)
    entry = o[cand_day[safe], cand_symbol[safe], cand_entry_idx[safe]].astype(np.float32)
    path_open = o[cand_day[safe].reshape(-1, 1), cand_symbol[safe].reshape(-1, 1), path_idx].astype(np.float32)
    path_high = h[cand_day[safe].reshape(-1, 1), cand_symbol[safe].reshape(-1, 1), path_idx].astype(np.float32)
    path_low = l[cand_day[safe].reshape(-1, 1), cand_symbol[safe].reshape(-1, 1), path_idx].astype(np.float32)
    path_close = c[cand_day[safe].reshape(-1, 1), cand_symbol[safe].reshape(-1, 1), path_idx].astype(np.float32)
    valid = (
        np.isfinite(entry)
        & (entry > 0.0)
        & np.isfinite(path_open).all(axis=1)
        & np.isfinite(path_high).all(axis=1)
        & np.isfinite(path_low).all(axis=1)
        & np.isfinite(path_close).all(axis=1)
    )
    if not valid.any():
        return out

    rows = safe[valid]
    entry = entry[valid].astype(np.float32)
    path_open = path_open[valid].astype(np.float32)
    path_high = path_high[valid].astype(np.float32)
    path_low = path_low[valid].astype(np.float32)
    path_close = path_close[valid].astype(np.float32)

    stop_price = entry * (1.0 + np.float32(stop_pct) / 100.0)
    target_price = entry * (1.0 - np.float32(target_pct) / 100.0)
    stop_hit = path_high >= stop_price.reshape(-1, 1)
    target_hit = path_low <= target_price.reshape(-1, 1)
    stop_any = stop_hit.any(axis=1)
    target_any = target_hit.any(axis=1)
    stop_first_idx = np.where(stop_any, np.argmax(stop_hit, axis=1), int(hold_minutes) + 1).astype(np.int16)
    target_first_idx = np.where(target_any, np.argmax(target_hit, axis=1), int(hold_minutes) + 1).astype(np.int16)
    stop_first = stop_any & (stop_first_idx <= target_first_idx)
    target_first = target_any & (target_first_idx < stop_first_idx)
    timeout = ~(stop_first | target_first)
    exit_offset = np.where(stop_first, stop_first_idx, np.where(target_first, target_first_idx, int(hold_minutes) - 1)).astype(np.int32)
    row_idx = np.arange(path_open.shape[0], dtype=np.int32)
    exit_open = path_open[row_idx, exit_offset].astype(np.float32)
    exit_close = path_close[row_idx, exit_offset].astype(np.float32)
    stop_gap = stop_first & (exit_open >= stop_price)
    exit_price = np.where(stop_gap, exit_open, np.where(stop_first, stop_price, np.where(target_first, target_price, exit_close))).astype(np.float32)
    gross = ((entry - exit_price) / entry * 100.0).astype(np.float32)
    net = (gross - np.float32(round_trip_cost_pct)).astype(np.float32)
    exit_type = np.where(stop_gap, "SL_GAP", np.where(stop_first, "SL", np.where(target_first, "TARGET", "TIME"))).astype(object)

    out[f"net_h{hold_minutes}"][rows] = net
    out[f"gross_h{hold_minutes}"][rows] = gross
    out[f"exit_idx_h{hold_minutes}"][rows] = cand_entry_idx[rows] + exit_offset
    out[f"exit_offset_h{hold_minutes}"][rows] = exit_offset.astype(np.int16)
    out[f"exit_price_h{hold_minutes}"][rows] = exit_price
    out[f"target_first_h{hold_minutes}"][rows] = target_first
    out[f"stop_first_h{hold_minutes}"][rows] = stop_first
    out[f"timeout_h{hold_minutes}"][rows] = timeout
    out[f"exit_type_h{hold_minutes}"][rows] = exit_type
    return out


def extract_candidates_for_target(
    df: pd.DataFrame,
    target_start: np.datetime64,
    target_end: np.datetime64,
    args: argparse.Namespace,
    search_specs: list[dict[str, float | int | str]],
    holds: list[int],
) -> tuple[pd.DataFrame, np.ndarray]:
    if df.empty:
        return pd.DataFrame(), np.array([], dtype="datetime64[D]")

    dates_index = pd.Index(np.sort(df["date"].unique()), name="date")
    symbols_index = pd.Index(np.sort(df["symbol"].unique()), name="symbol")
    if dates_index.empty or symbols_index.empty:
        return pd.DataFrame(), np.array([], dtype="datetime64[D]")

    full_index = pd.MultiIndex.from_product((dates_index, symbols_index), names=["date", "symbol"])
    buckets = np.arange(1, int(lab.BUCKET_COUNT) + 1, dtype=np.int32)
    wide = df.pivot_table(index=["date", "symbol"], columns="bucket", values=["open", "high", "low", "close", "volume"], aggfunc="first").reindex(full_index)
    day_count = dates_index.size
    symbol_count = symbols_index.size
    o = wide["open"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, int(lab.BUCKET_COUNT))
    h = wide["high"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, int(lab.BUCKET_COUNT))
    l = wide["low"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, int(lab.BUCKET_COUNT))
    c = wide["close"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, int(lab.BUCKET_COUNT))
    v = wide["volume"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, int(lab.BUCKET_COUNT))

    prev_close = np.concatenate((np.full((1, symbol_count), np.nan, dtype=np.float32), c[:-1, :, -1].astype(np.float32)), axis=0)
    gap = lab.safe_pct(o[:, :, 0].astype(np.float32), prev_close).astype(np.float32)
    dates_np = dates_index.to_numpy(dtype="datetime64[D]")
    target_dates = dates_np[(dates_np >= target_start) & (dates_np <= target_end)]

    price_valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0.0) & (h > 0.0) & (l > 0.0) & (c > 0.0)
    volume_valid = price_valid & np.isfinite(v) & (v >= 0.0)
    volume_clean = np.where(volume_valid, v, 0.0).astype(np.float32)
    cum_volume = np.cumsum(volume_clean, axis=2, dtype=np.float32)
    high_so_far = np.maximum.accumulate(np.where(price_valid, h, -np.inf).astype(np.float32), axis=2)

    feature_bucket = np.arange(lab.FEATURE_BUCKET_START, lab.FEATURE_BUCKET_END_REQUESTED + 1, dtype=np.int32)
    feature_idx = feature_bucket - 1
    entry_idx = feature_idx + 1
    feature_valid = (
        price_valid[:, :, feature_idx]
        & price_valid[:, :, feature_idx - 6]
        & price_valid[:, :, feature_idx - 15]
        & price_valid[:, :, feature_idx - 20]
        & np.isfinite(gap).reshape(day_count, symbol_count, 1)
    )
    entry_open_valid = np.isfinite(o[:, :, entry_idx]) & (o[:, :, entry_idx] > 0.0)
    bar_volume = volume_clean[:, :, feature_idx]
    prev5_volume = (cum_volume[:, :, feature_idx - 1] - cum_volume[:, :, feature_idx - 6]).astype(np.float32)
    vol20 = (cum_volume[:, :, feature_idx] - cum_volume[:, :, feature_idx - 20]).astype(np.float32)
    cum_vol = cum_volume[:, :, feature_idx].astype(np.float32)
    bar_mean, bar_count = lab.previous_n_day_mean(bar_volume, feature_valid)
    vol20_mean, vol20_count = lab.previous_n_day_mean(vol20, feature_valid)
    cum_mean, cum_count = lab.previous_n_day_mean(cum_vol, feature_valid)
    bar_rvol = lab.finite_divide(bar_volume, bar_mean)
    vol20_rvol = lab.finite_divide(vol20, vol20_mean)
    cum_rvol = lab.finite_divide(cum_vol, cum_mean)
    volume_accel = lab.finite_divide(bar_volume * 5.0, prev5_volume)
    close_t = c[:, :, feature_idx].astype(np.float32)
    mom15 = lab.safe_pct(close_t, c[:, :, feature_idx - 15]).astype(np.float32)
    drop_from_day_high = lab.safe_pct(high_so_far[:, :, feature_idx].astype(np.float32), close_t).astype(np.float32)
    gap_3d = np.broadcast_to(gap.reshape(day_count, symbol_count, 1), close_t.shape).astype(np.float32)
    trade_day_mask = (dates_np >= target_start) & (dates_np <= target_end)
    base_valid = (
        feature_valid
        & entry_open_valid
        & trade_day_mask.reshape(day_count, 1, 1)
        & (bar_count >= lab.MIN_PRIOR_DAYS)
        & (vol20_count >= lab.MIN_PRIOR_DAYS)
        & (cum_count >= lab.MIN_PRIOR_DAYS)
    )

    min_bar = min(float(spec["bar_rvol_min"]) for spec in search_specs)
    min_vol20 = min(float(spec["vol20_rvol_min"]) for spec in search_specs)
    min_cum = min(float(spec["cum_rvol_min"]) for spec in search_specs)
    max_accel = max(float(spec["volume_accel_max"]) for spec in search_specs)
    min_drop = min(float(spec["drop_from_high_min"]) for spec in search_specs)
    min_gap = min(float(spec["gap_up_min"]) for spec in search_specs)
    min_mom = min(float(spec["mom15_min"]) for spec in search_specs)
    max_mom = max(float(spec["mom15_max"]) for spec in search_specs)
    broad_candidate = (
        base_valid
        & np.isfinite(bar_rvol)
        & np.isfinite(vol20_rvol)
        & np.isfinite(cum_rvol)
        & np.isfinite(volume_accel)
        & np.isfinite(drop_from_day_high)
        & np.isfinite(mom15)
        & np.isfinite(gap_3d)
        & (bar_rvol >= min_bar)
        & (vol20_rvol >= min_vol20)
        & (cum_rvol >= min_cum)
        & (volume_accel < max_accel)
        & (drop_from_day_high >= min_drop)
        & (gap_3d >= min_gap)
        & (mom15 >= min_mom)
        & (mom15 <= max_mom)
    )

    cand_day, cand_symbol, cand_local = np.nonzero(broad_candidate)
    if cand_day.size == 0:
        return pd.DataFrame(), target_dates

    cand_entry_idx = (feature_idx[cand_local] + 1).astype(np.int32)
    out = pd.DataFrame(
        {
            "date": dates_np[cand_day].astype(str),
            "symbol": symbols_index.to_numpy(dtype=object)[cand_symbol],
            "day_local": cand_day.astype(np.int32),
            "symbol_local": cand_symbol.astype(np.int32),
            "signal_idx": feature_idx[cand_local].astype(np.int16),
            "entry_idx": cand_entry_idx.astype(np.int16),
            "signal_bucket": (feature_idx[cand_local] + 1).astype(np.int16),
            "entry_bucket": (cand_entry_idx + 1).astype(np.int16),
            "entry_price": o[cand_day, cand_symbol, cand_entry_idx].astype(np.float32),
            "gap_pct": gap[cand_day, cand_symbol].astype(np.float32),
            "bar_rvol": bar_rvol[cand_day, cand_symbol, cand_local].astype(np.float32),
            "vol20_rvol": vol20_rvol[cand_day, cand_symbol, cand_local].astype(np.float32),
            "cum_rvol": cum_rvol[cand_day, cand_symbol, cand_local].astype(np.float32),
            "volume_accel": volume_accel[cand_day, cand_symbol, cand_local].astype(np.float32),
            "mom15_pct": mom15[cand_day, cand_symbol, cand_local].astype(np.float32),
            "drop_from_day_high_pct": drop_from_day_high[cand_day, cand_symbol, cand_local].astype(np.float32),
        }
    )
    for hold in holds:
        sim = simulate_for_candidate_rows(
            o,
            h,
            l,
            c,
            cand_day.astype(np.int32),
            cand_symbol.astype(np.int32),
            cand_entry_idx.astype(np.int32),
            int(hold),
            float(args.stop_pct),
            float(args.target_pct),
            float(args.round_trip_cost_pct),
        )
        for key, value in sim.items():
            out[key] = value
    return out, target_dates


def first_candidate_indices(mask: np.ndarray, day_idx: np.ndarray, symbol_idx: np.ndarray) -> np.ndarray:
    selected = np.flatnonzero(mask)
    if selected.size == 0:
        return selected.astype(np.int32)
    keys = day_idx[selected].astype(np.int64) * (np.int64(symbol_idx.max()) + 1) + symbol_idx[selected].astype(np.int64)
    first = np.unique(keys, return_index=True)[1].astype(np.int32)
    return selected[first].astype(np.int32)


def evaluate_spec(
    table: pd.DataFrame,
    spec: dict[str, float | int | str],
    day_idx: np.ndarray,
    symbol_idx: np.ndarray,
    dates_np: np.ndarray,
    trade_day_mask: np.ndarray,
    min_trades: int,
) -> tuple[dict[str, float | int | str], np.ndarray]:
    hold = int(spec["hold_minutes"])
    net_col = f"net_h{hold}"
    net_all = table[net_col].to_numpy(np.float32)
    entry_idx = table["entry_idx"].to_numpy(np.int32)
    mask = (
        np.isfinite(net_all)
        & (table["bar_rvol"].to_numpy(np.float32) >= float(spec["bar_rvol_min"]))
        & (table["vol20_rvol"].to_numpy(np.float32) >= float(spec["vol20_rvol_min"]))
        & (table["cum_rvol"].to_numpy(np.float32) >= float(spec["cum_rvol_min"]))
        & (table["volume_accel"].to_numpy(np.float32) < float(spec["volume_accel_max"]))
        & (table["drop_from_day_high_pct"].to_numpy(np.float32) >= float(spec["drop_from_high_min"]))
        & (table["gap_pct"].to_numpy(np.float32) >= float(spec["gap_up_min"]))
        & (table["mom15_pct"].to_numpy(np.float32) >= float(spec["mom15_min"]))
        & (table["mom15_pct"].to_numpy(np.float32) <= float(spec["mom15_max"]))
        & (entry_idx + hold <= min(lab.BUCKET_COUNT, lab.LATEST_EXIT_BUCKET))
    )
    rows = first_candidate_indices(mask, day_idx, symbol_idx)
    row: dict[str, float | int | str] = {**spec}
    if rows.size:
        row.update(lab.summarize_pattern(net_all[rows], day_idx[rows], dates_np, dates_np.size, trade_day_mask, min_trades))
    else:
        empty = np.array([], dtype=np.float32)
        row.update(lab.summarize_pattern(empty, np.array([], dtype=np.int32), dates_np, dates_np.size, trade_day_mask, min_trades))
    return row, rows


def tradebook_from_rows(table: pd.DataFrame, rows: np.ndarray, hold: int, pattern_name: str | None = None) -> pd.DataFrame:
    if rows.size == 0:
        return pd.DataFrame()
    part = table.iloc[rows].copy()
    out = pd.DataFrame(
        {
            "date": part["date"],
            "symbol": part["symbol"],
            "direction": "SHORT",
            "signal_bucket": part["signal_bucket"].astype(np.int16),
            "signal_time": lab.minute_labels(part["signal_idx"].to_numpy(np.int32)),
            "entry_bucket": part["entry_bucket"].astype(np.int16),
            "entry_time": lab.minute_labels(part["entry_idx"].to_numpy(np.int32)),
            "entry_price": part["entry_price"].astype(np.float32),
            "exit_bucket": part[f"exit_idx_h{hold}"].astype(np.int32) + 1,
            "exit_time": lab.minute_labels(part[f"exit_idx_h{hold}"].to_numpy(np.int32)),
            "exit_price": part[f"exit_price_h{hold}"].astype(np.float32),
            "exit_type": part[f"exit_type_h{hold}"],
            "hold_minutes": part[f"exit_offset_h{hold}"].astype(np.int16),
            "gross_pct": part[f"gross_h{hold}"].astype(np.float32),
            "net_pct": part[f"net_h{hold}"].astype(np.float32),
            "gap_pct": part["gap_pct"].astype(np.float32),
            "bar_rvol": part["bar_rvol"].astype(np.float32),
            "vol20_rvol": part["vol20_rvol"].astype(np.float32),
            "cum_rvol": part["cum_rvol"].astype(np.float32),
            "volume_accel": part["volume_accel"].astype(np.float32),
            "mom15_pct": part["mom15_pct"].astype(np.float32),
            "drop_from_day_high_pct": part["drop_from_day_high_pct"].astype(np.float32),
        }
    )
    if pattern_name is not None:
        out.insert(0, "pattern_name", pattern_name)
    return out.sort_values(["date", "entry_bucket", "symbol"], kind="mergesort").reset_index(drop=True)


def daily_from_tradebook(tradebook: pd.DataFrame, dates_np: np.ndarray) -> pd.DataFrame:
    date_to_idx = {str(date): idx for idx, date in enumerate(dates_np.astype(str))}
    day_idx = tradebook["date"].map(date_to_idx).fillna(-1).astype(np.int32).to_numpy()
    valid = day_idx >= 0
    net = tradebook.loc[valid, "net_pct"].to_numpy(np.float32)
    wins = net > 0.0
    counts = np.bincount(day_idx[valid], minlength=dates_np.size).astype(np.float32)
    day_net = np.bincount(day_idx[valid], weights=net, minlength=dates_np.size).astype(np.float32)
    day_wins = np.bincount(day_idx[valid], weights=wins.astype(np.float32), minlength=dates_np.size).astype(np.float32)
    return lab.daily_summary(day_net, counts, day_wins, dates_np, np.ones(dates_np.size, dtype=bool))


def save_chart_set(out_dir: Path, daily: pd.DataFrame, prefix: str) -> None:
    lab.plot_curve(daily, out_dir / f"{prefix}equity_curve.png")
    lab.plot_daily_bars(daily, out_dir / f"{prefix}daily_net.png")


def baseline_spec_from_args(args: argparse.Namespace) -> dict[str, float | int | str]:
    if args.preset == "candidate60":
        return {
            "name": "candidate60_rv8_v202_cum10_acc2_drop2_gap0.5_m0_1.5_h60",
            "bar_rvol_min": 8.0,
            "vol20_rvol_min": 2.0,
            "cum_rvol_min": 10.0,
            "volume_accel_max": 2.0,
            "drop_from_high_min": 2.0,
            "gap_up_min": 0.5,
            "mom15_min": 0.0,
            "mom15_max": 1.5,
            "hold_minutes": 60,
            "stop_pct": float(args.stop_pct),
            "target_pct": float(args.target_pct),
        }
    return {
        "name": "baseline_rv8_v203_cum8_acc3_drop3_gap0.5_m-0.5_0_h30",
        "bar_rvol_min": float(lab.BAR_RVOL_MIN),
        "vol20_rvol_min": float(lab.VOL20_RVOL_MIN),
        "cum_rvol_min": float(lab.CUM_RVOL_MIN),
        "volume_accel_max": float(lab.VOLUME_ACCEL_MAX),
        "drop_from_high_min": float(lab.DROP_FROM_HIGH_MIN),
        "gap_up_min": float(lab.GAP_UP_MIN),
        "mom15_min": float(lab.MOM15_MIN),
        "mom15_max": float(lab.MOM15_MAX),
        "hold_minutes": int(args.hold_minutes),
        "stop_pct": float(args.stop_pct),
        "target_pct": float(args.target_pct),
    }


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir = Path(args.parquet_dir).resolve()
    start = np.datetime64(args.trade_start_date)
    end = np.datetime64(args.trade_end_date)
    files = monthly_files(parquet_dir, args.parquet_glob, start, end)
    if not files:
        raise FileNotFoundError(f"No monthly parquet files matched {parquet_dir / args.parquet_glob}")

    target_symbols = None if args.all_symbols else lab.load_target_symbols()
    specs = lab.pattern_grid_specs(args) if args.search_grid else []
    baseline_spec = baseline_spec_from_args(args)
    search_specs = specs or [baseline_spec]
    broad_specs = [*search_specs, baseline_spec]
    holds = sorted({int(spec["hold_minutes"]) for spec in broad_specs})

    chunks_dir = out_dir / "candidate_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    if not args.resume_candidates:
        for old_file in chunks_dir.glob("candidates_*.parquet"):
            old_file.unlink()
        for old_file in chunks_dir.glob("dates_*.csv"):
            old_file.unlink()
        for old_file in chunks_dir.glob("empty_*.marker"):
            old_file.unlink()

    candidate_files: list[Path] = []
    trading_dates: list[np.ndarray] = []
    chunk_months = max(1, int(args.chunk_months))
    warmup_files = max(0, int(args.warmup_files))
    for start_idx in range(0, len(files), chunk_months):
        target_paths = files[start_idx : start_idx + chunk_months]
        context_paths = files[max(0, start_idx - warmup_files) : start_idx] + target_paths
        first_key = monthly_file_key(target_paths[0])
        last_key = monthly_file_key(target_paths[-1])
        if first_key is None or last_key is None:
            continue
        target_start = max(start, np.datetime64(f"{first_key[:4]}-{first_key[4:]}-01"))
        target_end = min(end, np.datetime64(pd.Period(f"{last_key[:4]}-{last_key[4:]}", freq="M").end_time.date()))
        chunk_id = f"{first_key}_{last_key}"
        candidate_path = chunks_dir / f"candidates_{chunk_id}.parquet"
        dates_path = chunks_dir / f"dates_{chunk_id}.csv"
        empty_marker = chunks_dir / f"empty_{chunk_id}.marker"
        if args.resume_candidates and dates_path.exists() and (candidate_path.exists() or empty_marker.exists()):
            log(f"Chunk {first_key}-{last_key}: resuming saved candidates")
            dates = pd.read_csv(dates_path)["date"].to_numpy(dtype="datetime64[D]")
            if dates.size:
                trading_dates.append(dates)
            if candidate_path.exists():
                candidate_files.append(candidate_path)
            continue

        log(f"Chunk {first_key}-{last_key}: reading {len(context_paths)} files")
        df = read_chunk(context_paths, end, target_symbols)
        log(f"Chunk {first_key}-{last_key}: rows {len(df):,}, symbols {df['symbol'].nunique() if not df.empty else 0:,}")
        candidates, dates = extract_candidates_for_target(df, target_start, target_end, args, broad_specs, holds)
        log(f"Chunk {first_key}-{last_key}: broad candidates {len(candidates):,}")
        if not candidates.empty:
            candidates.to_parquet(candidate_path, index=False)
            candidate_files.append(candidate_path)
            if empty_marker.exists():
                empty_marker.unlink()
        else:
            empty_marker.write_text("", encoding="utf-8")
        if dates.size:
            trading_dates.append(dates)
            pd.DataFrame({"date": dates.astype(str)}).to_csv(dates_path, index=False)
        else:
            pd.DataFrame({"date": []}).to_csv(dates_path, index=False)
        del df, candidates
        gc.collect()

    if not candidate_files:
        raise RuntimeError("No candidate rows were produced for the requested whole-data run.")
    table = pd.concat((pd.read_parquet(path) for path in candidate_files), ignore_index=True)
    table = table.sort_values(["date", "symbol", "entry_idx"], kind="mergesort").reset_index(drop=True)
    dates_np = np.unique(np.concatenate(trading_dates)).astype("datetime64[D]")
    day_lookup = {str(date): idx for idx, date in enumerate(dates_np.astype(str))}
    symbol_codes, symbol_uniques = pd.factorize(table["symbol"], sort=True)
    day_idx = table["date"].map(day_lookup).astype(np.int32).to_numpy()
    symbol_idx = symbol_codes.astype(np.int32)
    trade_day_mask = np.ones(dates_np.size, dtype=bool)

    log(f"Combined broad candidates: {len(table):,} | trading days: {dates_np.size:,} | symbols: {len(symbol_uniques):,}")
    table.to_parquet(out_dir / "broad_candidate_rows.parquet", index=False)

    baseline_row, baseline_rows = evaluate_spec(table, baseline_spec, day_idx, symbol_idx, dates_np, trade_day_mask, int(args.min_trades))
    baseline_tradebook = tradebook_from_rows(table, baseline_rows, int(baseline_spec["hold_minutes"]))
    baseline_daily = daily_from_tradebook(baseline_tradebook, dates_np)
    summary = lab.metric_summary(
        baseline_tradebook["net_pct"].to_numpy(np.float32),
        np.bincount(day_idx[baseline_rows], weights=baseline_tradebook["net_pct"].to_numpy(np.float32), minlength=dates_np.size).astype(np.float32),
        np.bincount(day_idx[baseline_rows], minlength=dates_np.size).astype(np.float32),
        table.iloc[baseline_rows][f"target_first_h{int(baseline_spec['hold_minutes'])}"].to_numpy(bool),
        table.iloc[baseline_rows][f"stop_first_h{int(baseline_spec['hold_minutes'])}"].to_numpy(bool),
        table.iloc[baseline_rows][f"timeout_h{int(baseline_spec['hold_minutes'])}"].to_numpy(bool),
        trade_day_mask,
    )

    baseline_tradebook.to_csv(out_dir / "tradebook.csv", index=False)
    baseline_daily.to_csv(out_dir / "daily_summary.csv", index=False)
    summary.to_csv(out_dir / "summary.csv", index=False)
    save_chart_set(out_dir, baseline_daily, "")
    baseline_diag = lab.write_diagnostics("", baseline_tradebook, baseline_daily, out_dir)
    live_section = ""
    if args.live_signals:
        lab.HOLD_MINUTES = int(baseline_spec["hold_minutes"])
        lab.STOP_PCT = np.float32(args.stop_pct)
        lab.TARGET_PCT = np.float32(args.target_pct)
        live_date = str(args.live_date) if args.live_date else (str(dates_np.max()) if dates_np.size else "")
        live_signals = lab.live_signal_sheet(baseline_tradebook, live_date, str(args.preset))
        live_signals.to_csv(out_dir / "live_signals.csv", index=False)
        live_section = f"""
## Live Signals

- Signal date: `{live_date}`
- Rows: {live_signals.shape[0]:,}
- File: `live_signals.csv`

{lab.markdown_table(live_signals, max_rows=50)}
"""

    pattern_results = pd.DataFrame()
    best_tradebook = pd.DataFrame()
    best_daily = pd.DataFrame()
    best_diag = ""
    if args.search_grid:
        rows: list[dict[str, float | int | str]] = []
        best_score = -np.inf
        best_rows = np.array([], dtype=np.int32)
        best_spec: dict[str, float | int | str] | None = None
        for idx, spec in enumerate(specs, start=1):
            if idx == 1 or idx % 500 == 0 or idx == len(specs):
                log(f"Global pattern grid {idx:,}/{len(specs):,}")
            row, selected_rows = evaluate_spec(table, spec, day_idx, symbol_idx, dates_np, trade_day_mask, int(args.min_trades))
            rows.append(row)
            if float(row["score"]) > best_score and float(row["full_trades"]) >= float(args.min_trades):
                best_score = float(row["score"])
                best_rows = selected_rows
                best_spec = spec
        pattern_results = pd.DataFrame(rows).sort_values(["score", "out_of_sample_avg_net_pct", "validation_avg_net_pct"], ascending=False).reset_index(drop=True)
        pattern_results.to_csv(out_dir / "pattern_grid.csv", index=False)
        lab.plot_pattern_search(pattern_results, out_dir)
        if best_spec is None and not pattern_results.empty:
            best_spec = pattern_results.iloc[0].to_dict()
            _, best_rows = evaluate_spec(table, best_spec, day_idx, symbol_idx, dates_np, trade_day_mask, int(args.min_trades))
        if best_spec is not None:
            best_hold = int(best_spec["hold_minutes"])
            best_tradebook = tradebook_from_rows(table, best_rows, best_hold, str(best_spec["name"]))
            best_daily = daily_from_tradebook(best_tradebook, dates_np)
            best_tradebook.to_csv(out_dir / "best_pattern_tradebook.csv", index=False)
            best_daily.to_csv(out_dir / "best_pattern_daily_summary.csv", index=False)
            save_chart_set(out_dir, best_daily, "best_pattern_")
            best_diag = lab.write_diagnostics("best_pattern", best_tradebook, best_daily, out_dir)
            log(f"Best global pattern: {best_spec['name']} | trades {len(best_tradebook):,} | score {float(pattern_results.iloc[0]['score']):0.2f}")

    top_cols = [
        "name",
        "score",
        "hold_minutes",
        "bar_rvol_min",
        "vol20_rvol_min",
        "cum_rvol_min",
        "volume_accel_max",
        "drop_from_high_min",
        "gap_up_min",
        "mom15_min",
        "mom15_max",
        "full_trades",
        "full_avg_net_pct",
        "full_win_pct",
        "validation_avg_net_pct",
        "out_of_sample_avg_net_pct",
        "out_of_sample_trades",
        "out_of_sample_daily_sharpe",
        "out_of_sample_max_drawdown_sum_pct",
    ]
    if not pattern_results.empty:
        pattern_section = f"""
## Fast Pattern Search

- Grid combos evaluated globally: {pattern_results.shape[0]:,}
- Broad candidate rows extracted chunk-by-chunk: {len(table):,}
- Search holds: `{args.search_holds}` minutes.
- First valid signal per symbol/day is selected after each threshold mask.

### Top Patterns

{lab.markdown_table(pattern_results[top_cols], max_rows=25)}

### Best Pattern Daily

{best_daily.to_string(index=False) if not best_daily.empty else "No best pattern daily rows."}

{best_diag}
"""
    else:
        pattern_section = "\n## Fast Pattern Search\n\nNot run. Add `--search-grid`.\n"

    report = f"""# Whole-Data Parquet Volume-Spike Backtest

- Source parquet glob: `{parquet_dir / args.parquet_glob}`
- Strategy preset: `{args.preset}`
- Trade dates: `{args.trade_start_date}` to `{args.trade_end_date}`
- Universe: `{"all parquet symbols" if args.all_symbols else "volume_groups MEGA/LARGE"}`
- Monthly files used: {len(files):,}
- Chunk months: {chunk_months}, warmup files per chunk: {warmup_files}
- Candidate chunk files: {len(candidate_files):,}
- Combined broad candidate rows: {len(table):,}
- Trading days: {dates_np.size:,}
- Symbols seen in candidates: {len(symbol_uniques):,}
- Baseline rows: {len(baseline_tradebook):,}

## Baseline Summary

{summary.to_string(index=False)}

## Baseline Daily

{baseline_daily.tail(40).to_string(index=False)}

{baseline_diag}

{live_section}

{pattern_section}

## Files

- `broad_candidate_rows.parquet`
- `candidate_chunks/`
- `summary.csv`
- `daily_summary.csv`
- `tradebook.csv`
- `equity_curve.csv`
- `drawdown_details.csv`
- `monthly_analysis.csv`
- `quarterly_analysis.csv`
- `cost_sensitivity.csv`
- `robustness_summary.csv`
- `live_signals.csv` when `--live-signals` is used.
- `pattern_grid.csv`
- `best_pattern_tradebook.csv`
- `best_pattern_daily_summary.csv`
"""
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    log(f"Wrote {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunked whole-data NumPy search for the intraday volume-spike setup.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--parquet-glob", default="candles_20*.parquet")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--trade-start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--trade-end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--all-symbols", action="store_true", help="Use every symbol present in parquet. Default uses volume_groups MEGA/LARGE for speed.")
    parser.add_argument("--chunk-months", type=int, default=3)
    parser.add_argument("--warmup-files", type=int, default=2)
    parser.add_argument("--resume-candidates", action="store_true", help="Reuse candidate chunk files already written in the output directory.")
    parser.add_argument(
        "--preset",
        choices=["baseline", "candidate60"],
        default="baseline",
        help="Baseline rule preset to evaluate from the broad candidate table.",
    )
    parser.add_argument("--hold-minutes", type=int, default=int(lab.HOLD_MINUTES))
    parser.add_argument("--stop-pct", type=float, default=float(lab.STOP_PCT))
    parser.add_argument("--target-pct", type=float, default=float(lab.TARGET_PCT))
    parser.add_argument("--round-trip-cost-pct", type=float, default=float(lab.ROUND_TRIP_COST_PCT))
    parser.add_argument("--live-signals", action="store_true", help="Write latest-session signal sheet for the configured baseline/preset rule.")
    parser.add_argument("--live-date", default=None, help="Signal date to export. Defaults to latest available trade date in the run.")
    parser.add_argument("--search-grid", action="store_true")
    parser.add_argument("--search-holds", default=lab.SEARCH_HOLDS)
    parser.add_argument("--search-bar-rvol", default=lab.SEARCH_BAR_RVOL)
    parser.add_argument("--search-vol20-rvol", default=lab.SEARCH_VOL20_RVOL)
    parser.add_argument("--search-cum-rvol", default=lab.SEARCH_CUM_RVOL)
    parser.add_argument("--search-volume-accel-max", default=lab.SEARCH_VOLUME_ACCEL_MAX)
    parser.add_argument("--search-drop-from-high", default=lab.SEARCH_DROP_FROM_HIGH)
    parser.add_argument("--search-gap-up-min", default=lab.SEARCH_GAP_UP_MIN)
    parser.add_argument("--search-mom15-windows", default=lab.SEARCH_MOM15_WINDOWS)
    parser.add_argument("--grid-limit", type=int, default=0)
    parser.add_argument("--min-trades", type=int, default=250)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
