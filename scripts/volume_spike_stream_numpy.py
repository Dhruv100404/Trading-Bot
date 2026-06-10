from __future__ import annotations

import argparse
import gc
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import volume_spike_3x3_parquet_20day as lab
import volume_spike_whole_data_numpy as whole


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_OUT_DIR = ROOT / "docs" / "volume_spike_3x3_parquet_20day_whole_data_fast"
DEFAULT_START_DATE = "2021-01-01"
DEFAULT_END_DATE = "2026-05-30"
PRICE_BUCKETS = int(lab.LATEST_EXIT_BUCKET)
VOLUME_BUCKETS = int(lab.FEATURE_BUCKET_END_REQUESTED)

T0 = time.perf_counter()


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def monthly_file_key(path: Path) -> str | None:
    match = re.match(r"candles_(\d{6})\.parquet$", path.name)
    return match.group(1) if match else None


def monthly_files(parquet_dir: Path, glob: str, start: np.datetime64, end: np.datetime64) -> list[Path]:
    start_month = str(start)[:7].replace("-", "")
    end_month = str(end)[:7].replace("-", "")
    paths: list[Path] = []
    for path in sorted(parquet_dir.glob(glob)):
        key = monthly_file_key(path)
        if key is not None and start_month <= key <= end_month:
            paths.append(path)
    return paths


def all_group_symbols(path: Path) -> pd.Index:
    data = json.loads(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for group_symbols in data.get("volume_groups", {}).values():
        symbols.update(str(symbol) for symbol in group_symbols)
    return pd.Index(sorted(symbols), name="symbol")


def load_symbols(args: argparse.Namespace) -> pd.Index:
    if args.all_symbols:
        return all_group_symbols(Path(args.volume_groups_path))
    return lab.load_target_symbols()


def read_month_arrays(path: Path, symbols: pd.Index) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    table = pq.read_table(str(path), columns=lab.COLS)
    df = table.to_pandas()
    if df.empty:
        empty_dates = np.array([], dtype="datetime64[D]")
        empty_shape = (0, len(symbols), PRICE_BUCKETS)
        empty_v_shape = (0, len(symbols), VOLUME_BUCKETS)
        return (
            empty_dates,
            np.full(empty_shape, np.nan, dtype=np.float32),
            np.full(empty_shape, np.nan, dtype=np.float32),
            np.full(empty_shape, np.nan, dtype=np.float32),
            np.full(empty_shape, np.nan, dtype=np.float32),
            np.zeros(empty_v_shape, dtype=np.float32),
            np.full((0, len(symbols)), np.nan, dtype=np.float32),
        )

    dates_str = np.array(sorted(df["date"].astype(str).unique()), dtype=object)
    day_codes = pd.Categorical(df["date"].astype(str), categories=dates_str, ordered=True).codes.astype(np.int32)
    symbol_codes = pd.Categorical(df["symbol"], categories=symbols, ordered=True).codes.astype(np.int32)
    bucket = df["bucket"].to_numpy(np.int32, copy=False)
    keep = (day_codes >= 0) & (symbol_codes >= 0) & (bucket >= 1) & (bucket <= int(lab.BUCKET_COUNT))
    if not keep.all():
        df = df.loc[keep]
        day_codes = day_codes[keep]
        symbol_codes = symbol_codes[keep]
        bucket = bucket[keep]

    day_count = dates_str.size
    symbol_count = len(symbols)
    shape = (day_count, symbol_count, PRICE_BUCKETS)
    o = np.full(shape, np.nan, dtype=np.float32)
    h = np.full(shape, np.nan, dtype=np.float32)
    l = np.full(shape, np.nan, dtype=np.float32)
    c = np.full(shape, np.nan, dtype=np.float32)
    v = np.zeros((day_count, symbol_count, VOLUME_BUCKETS), dtype=np.float32)
    close_eod = np.full((day_count, symbol_count), np.nan, dtype=np.float32)

    price_mask = bucket <= PRICE_BUCKETS
    if price_mask.any():
        d = day_codes[price_mask]
        s = symbol_codes[price_mask]
        b = bucket[price_mask] - 1
        o[d, s, b] = df["open"].to_numpy(np.float32, copy=False)[price_mask]
        h[d, s, b] = df["high"].to_numpy(np.float32, copy=False)[price_mask]
        l[d, s, b] = df["low"].to_numpy(np.float32, copy=False)[price_mask]
        c[d, s, b] = df["close"].to_numpy(np.float32, copy=False)[price_mask]

    volume_mask = bucket <= VOLUME_BUCKETS
    if volume_mask.any():
        v[day_codes[volume_mask], symbol_codes[volume_mask], bucket[volume_mask] - 1] = df["volume"].to_numpy(np.float32, copy=False)[volume_mask]

    eod_mask = bucket == int(lab.BUCKET_COUNT)
    if eod_mask.any():
        close_eod[day_codes[eod_mask], symbol_codes[eod_mask]] = df["close"].to_numpy(np.float32, copy=False)[eod_mask]

    dates = dates_str.astype("datetime64[D]")
    del df, table
    return dates, o, h, l, c, v, close_eod


def simulate_day_candidates(
    o_day: np.ndarray,
    h_day: np.ndarray,
    l_day: np.ndarray,
    c_day: np.ndarray,
    cand_symbol: np.ndarray,
    cand_entry_idx: np.ndarray,
    holds: list[int],
    stop_pct: float,
    target_pct: float,
    round_trip_cost_pct: float,
) -> dict[str, np.ndarray]:
    n = cand_symbol.size
    out: dict[str, np.ndarray] = {}
    for hold in holds:
        out[f"net_h{hold}"] = np.full(n, np.nan, dtype=np.float32)
        out[f"gross_h{hold}"] = np.full(n, np.nan, dtype=np.float32)
        out[f"exit_idx_h{hold}"] = np.full(n, -1, dtype=np.int32)
        out[f"exit_offset_h{hold}"] = np.full(n, -1, dtype=np.int16)
        out[f"exit_price_h{hold}"] = np.full(n, np.nan, dtype=np.float32)
        out[f"target_first_h{hold}"] = np.zeros(n, dtype=bool)
        out[f"stop_first_h{hold}"] = np.zeros(n, dtype=bool)
        out[f"timeout_h{hold}"] = np.zeros(n, dtype=bool)
        out[f"exit_type_h{hold}"] = np.full(n, "", dtype=object)

    for hold in holds:
        safe = np.flatnonzero(cand_entry_idx + int(hold) <= min(lab.BUCKET_COUNT, lab.LATEST_EXIT_BUCKET)).astype(np.int32)
        if safe.size == 0:
            continue
        offsets = np.arange(int(hold), dtype=np.int32)
        path_idx = cand_entry_idx[safe].reshape(-1, 1) + offsets.reshape(1, -1)
        symbols = cand_symbol[safe]
        entry = o_day[symbols, cand_entry_idx[safe]].astype(np.float32)
        path_open = o_day[symbols.reshape(-1, 1), path_idx].astype(np.float32)
        path_high = h_day[symbols.reshape(-1, 1), path_idx].astype(np.float32)
        path_low = l_day[symbols.reshape(-1, 1), path_idx].astype(np.float32)
        path_close = c_day[symbols.reshape(-1, 1), path_idx].astype(np.float32)
        valid = (
            np.isfinite(entry)
            & (entry > 0.0)
            & np.isfinite(path_open).all(axis=1)
            & np.isfinite(path_high).all(axis=1)
            & np.isfinite(path_low).all(axis=1)
            & np.isfinite(path_close).all(axis=1)
        )
        if not valid.any():
            continue
        rows = safe[valid]
        entry = entry[valid]
        path_open = path_open[valid]
        path_high = path_high[valid]
        path_low = path_low[valid]
        path_close = path_close[valid]
        stop_price = entry * (1.0 + np.float32(stop_pct) / 100.0)
        target_price = entry * (1.0 - np.float32(target_pct) / 100.0)
        stop_hit = path_high >= stop_price.reshape(-1, 1)
        target_hit = path_low <= target_price.reshape(-1, 1)
        stop_any = stop_hit.any(axis=1)
        target_any = target_hit.any(axis=1)
        stop_first_idx = np.where(stop_any, np.argmax(stop_hit, axis=1), int(hold) + 1).astype(np.int16)
        target_first_idx = np.where(target_any, np.argmax(target_hit, axis=1), int(hold) + 1).astype(np.int16)
        stop_first = stop_any & (stop_first_idx <= target_first_idx)
        target_first = target_any & (target_first_idx < stop_first_idx)
        timeout = ~(stop_first | target_first)
        exit_offset = np.where(stop_first, stop_first_idx, np.where(target_first, target_first_idx, int(hold) - 1)).astype(np.int32)
        row_idx = np.arange(path_open.shape[0], dtype=np.int32)
        exit_open = path_open[row_idx, exit_offset]
        exit_close = path_close[row_idx, exit_offset]
        stop_gap = stop_first & (exit_open >= stop_price)
        exit_price = np.where(stop_gap, exit_open, np.where(stop_first, stop_price, np.where(target_first, target_price, exit_close))).astype(np.float32)
        gross = ((entry - exit_price) / entry * 100.0).astype(np.float32)
        net = (gross - np.float32(round_trip_cost_pct)).astype(np.float32)
        exit_type = np.where(stop_gap, "SL_GAP", np.where(stop_first, "SL", np.where(target_first, "TARGET", "TIME"))).astype(object)

        out[f"net_h{hold}"][rows] = net
        out[f"gross_h{hold}"][rows] = gross
        out[f"exit_idx_h{hold}"][rows] = cand_entry_idx[rows] + exit_offset
        out[f"exit_offset_h{hold}"][rows] = exit_offset.astype(np.int16)
        out[f"exit_price_h{hold}"][rows] = exit_price
        out[f"target_first_h{hold}"][rows] = target_first
        out[f"stop_first_h{hold}"][rows] = stop_first
        out[f"timeout_h{hold}"][rows] = timeout
        out[f"exit_type_h{hold}"][rows] = exit_type
    return out


def extract_month_candidates(
    path: Path,
    symbols: pd.Index,
    prev_close_state: np.ndarray,
    roll_state: dict[str, np.ndarray],
    args: argparse.Namespace,
    broad_specs: list[dict[str, float | int | str]],
    holds: list[int],
    trade_start: np.datetime64,
    trade_end: np.datetime64,
    processed_days: int,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, int]:
    dates, o, h, l, c, v, close_eod = read_month_arrays(path, symbols)
    if dates.size == 0:
        return pd.DataFrame(), dates, prev_close_state, processed_days

    price_valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0.0) & (h > 0.0) & (l > 0.0) & (c > 0.0)
    price_valid_volume = price_valid[:, :, :VOLUME_BUCKETS]
    volume_clean = np.where(price_valid_volume & np.isfinite(v) & (v >= 0.0), v, 0.0).astype(np.float32)
    cum_volume = np.cumsum(volume_clean, axis=2, dtype=np.float32)
    high_so_far = np.maximum.accumulate(np.where(price_valid_volume, h[:, :, :VOLUME_BUCKETS], -np.inf).astype(np.float32), axis=2)

    feature_bucket = np.arange(lab.FEATURE_BUCKET_START, lab.FEATURE_BUCKET_END_REQUESTED + 1, dtype=np.int32)
    feature_idx = feature_bucket - 1
    entry_idx = feature_idx + 1
    feature_valid = (
        price_valid[:, :, feature_idx]
        & price_valid[:, :, feature_idx - 6]
        & price_valid[:, :, feature_idx - 15]
        & price_valid[:, :, feature_idx - 20]
    )
    entry_open_valid = np.isfinite(o[:, :, entry_idx]) & (o[:, :, entry_idx] > 0.0)
    bar_volume = volume_clean[:, :, feature_idx]
    prev5_volume = (cum_volume[:, :, feature_idx - 1] - cum_volume[:, :, feature_idx - 6]).astype(np.float32)
    vol20 = (cum_volume[:, :, feature_idx] - cum_volume[:, :, feature_idx - 20]).astype(np.float32)
    cum_vol = cum_volume[:, :, feature_idx].astype(np.float32)
    volume_accel = lab.finite_divide(bar_volume * 5.0, prev5_volume)
    close_t = c[:, :, feature_idx].astype(np.float32)
    mom15 = lab.safe_pct(close_t, c[:, :, feature_idx - 15]).astype(np.float32)
    drop_from_day_high = lab.safe_pct(high_so_far[:, :, feature_idx].astype(np.float32), close_t).astype(np.float32)

    min_bar = min(float(spec["bar_rvol_min"]) for spec in broad_specs)
    min_vol20 = min(float(spec["vol20_rvol_min"]) for spec in broad_specs)
    min_cum = min(float(spec["cum_rvol_min"]) for spec in broad_specs)
    max_accel = max(float(spec["volume_accel_max"]) for spec in broad_specs)
    min_drop = min(float(spec["drop_from_high_min"]) for spec in broad_specs)
    min_gap = min(float(spec["gap_up_min"]) for spec in broad_specs)
    min_mom = min(float(spec["mom15_min"]) for spec in broad_specs)
    max_mom = max(float(spec["mom15_max"]) for spec in broad_specs)

    frames: list[pd.DataFrame] = []
    symbol_values = symbols.to_numpy(dtype=object)
    hist_slot_count = int(lab.LOOKBACK_DAYS)
    bar_sum = roll_state["bar_sum"]
    vol20_sum = roll_state["vol20_sum"]
    cum_sum = roll_state["cum_sum"]
    valid_count = roll_state["valid_count"]
    bar_hist = roll_state["bar_hist"]
    vol20_hist = roll_state["vol20_hist"]
    cum_hist = roll_state["cum_hist"]
    valid_hist = roll_state["valid_hist"]

    for day_pos, date in enumerate(dates):
        current_day_id = processed_days
        prev_close = prev_close_state if day_pos == 0 else close_eod[day_pos - 1]
        gap = lab.safe_pct(o[day_pos, :, 0], prev_close).astype(np.float32)
        gap_2d = gap.reshape(-1, 1)
        day_feature_valid = feature_valid[day_pos] & np.isfinite(gap_2d)
        count = valid_count.astype(np.float32)
        bar_mean = lab.finite_divide(bar_sum, count)
        vol20_mean = lab.finite_divide(vol20_sum, count)
        cum_mean = lab.finite_divide(cum_sum, count)
        bar_rvol = lab.finite_divide(bar_volume[day_pos], bar_mean)
        vol20_rvol = lab.finite_divide(vol20[day_pos], vol20_mean)
        cum_rvol = lab.finite_divide(cum_vol[day_pos], cum_mean)
        trade_day = trade_start <= date <= trade_end
        base_valid = (
            trade_day
            & day_feature_valid
            & entry_open_valid[day_pos]
            & (valid_count >= int(lab.MIN_PRIOR_DAYS))
        )
        broad = (
            base_valid
            & np.isfinite(bar_rvol)
            & np.isfinite(vol20_rvol)
            & np.isfinite(cum_rvol)
            & np.isfinite(volume_accel[day_pos])
            & np.isfinite(drop_from_day_high[day_pos])
            & np.isfinite(mom15[day_pos])
            & (bar_rvol >= min_bar)
            & (vol20_rvol >= min_vol20)
            & (cum_rvol >= min_cum)
            & (volume_accel[day_pos] < max_accel)
            & (drop_from_day_high[day_pos] >= min_drop)
            & (gap_2d >= min_gap)
            & (mom15[day_pos] >= min_mom)
            & (mom15[day_pos] <= max_mom)
        )
        cand_symbol, cand_local = np.nonzero(broad)
        if cand_symbol.size:
            cand_entry_idx = (feature_idx[cand_local] + 1).astype(np.int32)
            order = np.lexsort((cand_entry_idx, cand_symbol))
            cand_symbol = cand_symbol[order].astype(np.int32)
            cand_local = cand_local[order].astype(np.int32)
            cand_entry_idx = cand_entry_idx[order].astype(np.int32)
            out = pd.DataFrame(
                {
                    "date": np.full(cand_symbol.size, str(date), dtype=object),
                    "symbol": symbol_values[cand_symbol],
                    "day_local": np.full(cand_symbol.size, current_day_id, dtype=np.int32),
                    "symbol_local": cand_symbol.astype(np.int32),
                    "signal_idx": feature_idx[cand_local].astype(np.int16),
                    "entry_idx": cand_entry_idx.astype(np.int16),
                    "signal_bucket": (feature_idx[cand_local] + 1).astype(np.int16),
                    "entry_bucket": (cand_entry_idx + 1).astype(np.int16),
                    "entry_price": o[day_pos, cand_symbol, cand_entry_idx].astype(np.float32),
                    "gap_pct": gap[cand_symbol].astype(np.float32),
                    "bar_rvol": bar_rvol[cand_symbol, cand_local].astype(np.float32),
                    "vol20_rvol": vol20_rvol[cand_symbol, cand_local].astype(np.float32),
                    "cum_rvol": cum_rvol[cand_symbol, cand_local].astype(np.float32),
                    "volume_accel": volume_accel[day_pos, cand_symbol, cand_local].astype(np.float32),
                    "mom15_pct": mom15[day_pos, cand_symbol, cand_local].astype(np.float32),
                    "drop_from_day_high_pct": drop_from_day_high[day_pos, cand_symbol, cand_local].astype(np.float32),
                }
            )
            sim = simulate_day_candidates(
                o[day_pos],
                h[day_pos],
                l[day_pos],
                c[day_pos],
                cand_symbol,
                cand_entry_idx,
                holds,
                float(args.stop_pct),
                float(args.target_pct),
                float(args.round_trip_cost_pct),
            )
            for key, value in sim.items():
                out[key] = value
            frames.append(out)

        slot = current_day_id % hist_slot_count
        bar_sum -= bar_hist[slot]
        vol20_sum -= vol20_hist[slot]
        cum_sum -= cum_hist[slot]
        valid_count -= valid_hist[slot]
        valid_today = day_feature_valid.astype(np.float32)
        bar_hist[slot] = np.where(day_feature_valid, bar_volume[day_pos], 0.0).astype(np.float32)
        vol20_hist[slot] = np.where(day_feature_valid, vol20[day_pos], 0.0).astype(np.float32)
        cum_hist[slot] = np.where(day_feature_valid, cum_vol[day_pos], 0.0).astype(np.float32)
        valid_hist[slot] = valid_today
        bar_sum += bar_hist[slot]
        vol20_sum += vol20_hist[slot]
        cum_sum += cum_hist[slot]
        valid_count += valid_hist[slot]
        processed_days += 1

    prev_close_state = close_eod[-1].astype(np.float32)
    del o, h, l, c, v, close_eod, price_valid, volume_clean, cum_volume, high_so_far
    gc.collect()
    if not frames:
        return pd.DataFrame(), dates, prev_close_state, processed_days
    return pd.concat(frames, ignore_index=True), dates, prev_close_state, processed_days


def empty_roll_state(symbol_count: int, feature_count: int) -> dict[str, np.ndarray]:
    shape = (symbol_count, feature_count)
    hist_shape = (int(lab.LOOKBACK_DAYS), symbol_count, feature_count)
    return {
        "bar_sum": np.zeros(shape, dtype=np.float32),
        "vol20_sum": np.zeros(shape, dtype=np.float32),
        "cum_sum": np.zeros(shape, dtype=np.float32),
        "valid_count": np.zeros(shape, dtype=np.float32),
        "bar_hist": np.zeros(hist_shape, dtype=np.float32),
        "vol20_hist": np.zeros(hist_shape, dtype=np.float32),
        "cum_hist": np.zeros(hist_shape, dtype=np.float32),
        "valid_hist": np.zeros(hist_shape, dtype=np.float32),
    }


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = out_dir / "candidate_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir = Path(args.parquet_dir).resolve()
    trade_start = np.datetime64(args.trade_start_date)
    trade_end = np.datetime64(args.trade_end_date)
    files = monthly_files(parquet_dir, args.parquet_glob, trade_start, trade_end)
    if not files:
        raise FileNotFoundError(f"No monthly parquet files matched {parquet_dir / args.parquet_glob}")

    symbols = load_symbols(args)
    specs = lab.pattern_grid_specs(args) if args.search_grid else []
    baseline_spec = whole.baseline_spec_from_args(args)
    broad_specs = [*(specs or [baseline_spec]), baseline_spec]
    holds = sorted({int(spec["hold_minutes"]) for spec in broad_specs})

    if not args.resume_candidates:
        for old in chunks_dir.glob("*"):
            if old.is_file():
                old.unlink()

    roll_state = empty_roll_state(len(symbols), lab.FEATURE_BUCKET_END_REQUESTED - lab.FEATURE_BUCKET_START + 1)
    prev_close_state = np.full(len(symbols), np.nan, dtype=np.float32)
    processed_days = 0
    candidate_files: list[Path] = []
    trading_dates: list[np.ndarray] = []
    for idx, path in enumerate(files, start=1):
        key = monthly_file_key(path) or path.stem
        candidate_path = chunks_dir / f"candidates_{key}.parquet"
        dates_path = chunks_dir / f"dates_{key}.csv"
        if args.resume_candidates and candidate_path.exists() and dates_path.exists():
            log(f"{key}: resuming candidates")
            dates = pd.read_csv(dates_path)["date"].to_numpy(dtype="datetime64[D]")
            if dates.size:
                trading_dates.append(dates)
            if candidate_path.exists():
                candidate_files.append(candidate_path)
            continue
        log(f"{key}: reading file {idx}/{len(files)}")
        candidates, dates, prev_close_state, processed_days = extract_month_candidates(
            path,
            symbols,
            prev_close_state,
            roll_state,
            args,
            broad_specs,
            holds,
            trade_start,
            trade_end,
            processed_days,
        )
        log(f"{key}: candidates {len(candidates):,}")
        pd.DataFrame({"date": dates.astype(str)}).to_csv(dates_path, index=False)
        if dates.size:
            trading_dates.append(dates)
        if not candidates.empty:
            candidates.to_parquet(candidate_path, index=False)
            candidate_files.append(candidate_path)

    if not candidate_files:
        raise RuntimeError("No candidate rows were produced.")

    table = pd.concat((pd.read_parquet(path) for path in candidate_files), ignore_index=True)
    table = table.sort_values(["date", "symbol", "entry_idx"], kind="mergesort").reset_index(drop=True)
    dates_np = np.unique(np.concatenate(trading_dates)).astype("datetime64[D]")
    day_lookup = {str(date): idx for idx, date in enumerate(dates_np.astype(str))}
    symbol_codes, symbol_uniques = pd.factorize(table["symbol"], sort=True)
    day_idx = table["date"].map(day_lookup).astype(np.int32).to_numpy()
    symbol_idx = symbol_codes.astype(np.int32)
    trade_day_mask = (dates_np >= trade_start) & (dates_np <= trade_end)
    table.to_parquet(out_dir / "broad_candidate_rows.parquet", index=False)
    log(f"Combined candidates {len(table):,} | days {dates_np.size:,} | symbols {len(symbol_uniques):,}")

    baseline_row, baseline_rows = whole.evaluate_spec(table, baseline_spec, day_idx, symbol_idx, dates_np, trade_day_mask, int(args.min_trades))
    baseline_tradebook = whole.tradebook_from_rows(table, baseline_rows, int(baseline_spec["hold_minutes"]))
    baseline_daily = whole.daily_from_tradebook(baseline_tradebook, dates_np)
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
    whole.save_chart_set(out_dir, baseline_daily, "")
    baseline_diag = lab.write_diagnostics("", baseline_tradebook, baseline_daily, out_dir)
    live_section = ""
    if args.live_signals:
        lab.HOLD_MINUTES = int(baseline_spec["hold_minutes"])
        lab.STOP_PCT = np.float32(args.stop_pct)
        lab.TARGET_PCT = np.float32(args.target_pct)
        live_date = str(args.live_date) if args.live_date else (str(dates_np[trade_day_mask].max()) if trade_day_mask.any() else "")
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
                log(f"Grid {idx:,}/{len(specs):,}")
            row, selected_rows = whole.evaluate_spec(table, spec, day_idx, symbol_idx, dates_np, trade_day_mask, int(args.min_trades))
            rows.append(row)
            if float(row["score"]) > best_score and float(row["full_trades"]) >= float(args.min_trades):
                best_score = float(row["score"])
                best_rows = selected_rows
                best_spec = spec
        pattern_results = pd.DataFrame(rows).sort_values(["score", "out_of_sample_avg_net_pct", "validation_avg_net_pct"], ascending=False).reset_index(drop=True)
        pattern_results.to_csv(out_dir / "pattern_grid.csv", index=False)
        lab.plot_pattern_search(pattern_results, out_dir)
        if best_spec is not None:
            best_hold = int(best_spec["hold_minutes"])
            best_tradebook = whole.tradebook_from_rows(table, best_rows, best_hold, str(best_spec["name"]))
            best_daily = whole.daily_from_tradebook(best_tradebook, dates_np)
            best_tradebook.to_csv(out_dir / "best_pattern_tradebook.csv", index=False)
            best_daily.to_csv(out_dir / "best_pattern_daily_summary.csv", index=False)
            whole.save_chart_set(out_dir, best_daily, "best_pattern_")
            best_diag = lab.write_diagnostics("best_pattern", best_tradebook, best_daily, out_dir)
            log(f"Best pattern {best_spec['name']} | trades {len(best_tradebook):,} | score {best_score:0.2f}")

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
- Broad candidate rows extracted in a one-pass monthly stream: {len(table):,}

### Top Patterns

{lab.markdown_table(pattern_results[top_cols], max_rows=25)}

### Best Pattern Daily

{best_daily.to_string(index=False) if not best_daily.empty else "No best pattern daily rows."}

{best_diag}
"""
    else:
        pattern_section = "\n## Fast Pattern Search\n\nNot run. Add `--search-grid`.\n"

    report = f"""# Whole-Data One-Pass NumPy Volume-Spike Backtest

- Source parquet glob: `{parquet_dir / args.parquet_glob}`
- Strategy preset: `{args.preset}`
- Trade dates: `{args.trade_start_date}` to `{args.trade_end_date}`
- Universe: `{"all volume_groups symbols" if args.all_symbols else "volume_groups MEGA/LARGE"}`
- Monthly files used: {len(files):,}
- Candidate chunk files: {len(candidate_files):,}
- Combined broad candidate rows: {len(table):,}
- Trading days: {dates_np.size:,}
- Symbols in candidate table: {len(symbol_uniques):,}
- Baseline rows: {len(baseline_tradebook):,}

## Baseline Summary

{summary.to_string(index=False)}

## Baseline Daily

{baseline_daily.tail(40).to_string(index=False)}

{baseline_diag}

{live_section}

{pattern_section}

## Files

- `candidate_chunks/`
- `broad_candidate_rows.parquet`
- `summary.csv`
- `daily_summary.csv`
- `tradebook.csv`
- `live_signals.csv` when `--live-signals` is used.
- `pattern_grid.csv`
- `best_pattern_tradebook.csv`
- `best_pattern_daily_summary.csv`
"""
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    log(f"Wrote {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-pass monthly NumPy stream for whole-data volume-spike search.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--parquet-glob", default="candles_20*.parquet")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--volume-groups-path", type=Path, default=lab.VOLUME_GROUPS_PATH)
    parser.add_argument("--trade-start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--trade-end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--all-symbols", action="store_true")
    parser.add_argument(
        "--preset",
        choices=["baseline", "candidate60"],
        default="baseline",
        help="Baseline rule preset to evaluate from the streamed candidate table.",
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
    parser.add_argument("--resume-candidates", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
