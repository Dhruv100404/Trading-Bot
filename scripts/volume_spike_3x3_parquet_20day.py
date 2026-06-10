from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "parquets"
OUT_DIR = ROOT / "docs" / "volume_spike_3x3_parquet_20day"
VOLUME_GROUPS_PATH = ROOT / "data" / "volume_groups.json"
PARQUET_GLOB = "candles_2026*.parquet"
# Set True to bypass volume_groups.json and test every symbol present in parquet.
USE_ALL_PARQUET_SYMBOLS = True
TRADE_START_DATE = np.datetime64("2026-05-01")
TRADE_END_DATE = np.datetime64("2026-05-30")

BUCKET_COUNT = 375
LATEST_EXIT_BUCKET = 361
FEATURE_BUCKET_START = 285
FEATURE_BUCKET_END_REQUESTED = 314
HOLD_MINUTES = 30
if HOLD_MINUTES < 1:
    raise ValueError("HOLD_MINUTES must be a positive integer.")
FEATURE_BUCKET_END = FEATURE_BUCKET_END_REQUESTED
LOOKBACK_DAYS = np.int16(20)
MIN_PRIOR_DAYS = np.int16(10)
STOP_PCT = np.float32(3.0)
TARGET_PCT = np.float32(3.0)
ROUND_TRIP_COST_PCT = np.float32(0.10)

BAR_RVOL_MIN = np.float32(8.0)
VOL20_RVOL_MIN = np.float32(3.0)
CUM_RVOL_MIN = np.float32(8.0)
VOLUME_ACCEL_MAX = np.float32(3.0)
DROP_FROM_HIGH_MIN = np.float32(3.0)
GAP_UP_MIN = np.float32(0.5)
MOM15_MIN = np.float32(-0.5)
MOM15_MAX = np.float32(0.0)

SEARCH_HOLDS = "10,15,20,30,45,60"
SEARCH_BAR_RVOL = "6,8,10"
SEARCH_VOL20_RVOL = "2,3,5"
SEARCH_CUM_RVOL = "6,8,10"
SEARCH_VOLUME_ACCEL_MAX = "2,3"
SEARCH_DROP_FROM_HIGH = "2,3,4"
SEARCH_GAP_UP_MIN = "0.5,1"
SEARCH_MOM15_WINDOWS = "-2:0,-1:0,-0.5:0,0:1.5"

COLS = [
    "date",
    "symbol",
    "bucket",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

T0 = time.perf_counter()


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def finite_divide(num: np.ndarray, den: np.ndarray, default: float = np.nan) -> np.ndarray:
    output = np.full(np.broadcast_shapes(num.shape, den.shape), default, dtype=np.float32)
    valid = np.isfinite(num) & np.isfinite(den) & (den != 0.0)
    np.divide(num, den, out=output, where=valid)
    return output.astype(np.float32, copy=False)


def safe_pct(now: np.ndarray, before: np.ndarray) -> np.ndarray:
    return ((finite_divide(now, before) - 1.0) * 100.0).astype(np.float32)


def minute_labels(indices: np.ndarray) -> np.ndarray:
    total_minutes = (np.int32(9 * 60 + 15) + indices.astype(np.int32)).astype(np.int32)
    hours = np.char.zfill((total_minutes // 60).astype(str), 2)
    minutes = np.char.zfill((total_minutes % 60).astype(str), 2)
    return np.char.add(np.char.add(hours, ":"), minutes)


def previous_n_day_mean(values: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cleaned = np.where(valid, values, 0.0).astype(np.float32)
    counts = valid.astype(np.float32)
    sum_cs = np.cumsum(cleaned, axis=0, dtype=np.float32)
    count_cs = np.cumsum(counts, axis=0, dtype=np.float32)
    zero_plane = np.zeros((1, values.shape[1], values.shape[2]), dtype=np.float32)
    sum_pad = np.concatenate((zero_plane, sum_cs), axis=0)
    count_pad = np.concatenate((zero_plane, count_cs), axis=0)
    day_id = np.arange(values.shape[0], dtype=np.int32)
    start_id = np.maximum(day_id - np.int32(LOOKBACK_DAYS), 0).astype(np.int32)
    prior_sum = (sum_pad[day_id] - sum_pad[start_id]).astype(np.float32)
    prior_count = (count_pad[day_id] - count_pad[start_id]).astype(np.float32)
    return finite_divide(prior_sum, prior_count), prior_count


def parse_float_list(raw: str) -> list[float]:
    return [float(part.strip()) for part in raw.split(",") if part.strip()]


def parse_int_list(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def parse_window_list(raw: str) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"Invalid momentum window '{item}'. Use min:max.")
        lo, hi = item.split(":", 1)
        low = float(lo.strip())
        high = float(hi.strip())
        if high < low:
            raise ValueError(f"Invalid momentum window '{item}'. Max must be >= min.")
        windows.append((low, high))
    if not windows:
        raise ValueError("At least one momentum window is required.")
    return windows


def resolve_parquet_paths(data_dir: Path, raw_globs: str) -> np.ndarray:
    patterns = [part.strip() for part in str(raw_globs).split(",") if part.strip()]
    if not patterns:
        raise ValueError("--parquet-glob must contain at least one pattern.")
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in data_dir.glob(pattern):
            found[str(path.resolve())] = path
    return np.array([found[key] for key in sorted(found)], dtype=object)


def effective_feature_bucket_end(max_hold_minutes: int) -> int:
    if max_hold_minutes < 1:
        raise ValueError("Hold minutes must be a positive integer.")
    max_end = min(BUCKET_COUNT, LATEST_EXIT_BUCKET) - int(max_hold_minutes)
    end = min(FEATURE_BUCKET_END_REQUESTED, max_end)
    if end < FEATURE_BUCKET_START:
        raise ValueError("Hold-time search is too large for the configured feature window and 15:15 exit deadline.")
    return int(end)


def markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.head(max_rows).to_markdown(index=False)
    except Exception:
        return "```\n" + df.head(max_rows).to_csv(index=False) + "```"


def split_day_masks(dates: np.ndarray, trade_mask: np.ndarray) -> dict[str, np.ndarray]:
    active_dates = dates[trade_mask]
    if active_dates.size == 0:
        empty = np.zeros_like(trade_mask, dtype=bool)
        return {"train": empty, "validation": empty, "out_of_sample": empty}
    start = pd.Timestamp(str(active_dates.min()))
    end = pd.Timestamp(str(active_dates.max()))
    span = end - start
    cut1 = np.datetime64((start + span * 0.60).date())
    cut2 = np.datetime64((start + span * 0.80).date())
    return {
        "train": trade_mask & (dates < cut1),
        "validation": trade_mask & (dates >= cut1) & (dates < cut2),
        "out_of_sample": trade_mask & (dates >= cut2),
    }


def period_stats(
    net: np.ndarray,
    day_idx: np.ndarray,
    day_count: int,
    day_mask: np.ndarray,
) -> dict[str, float]:
    if day_mask.sum() == 0:
        return {
            "trading_days": 0.0,
            "trades": 0.0,
            "trades_per_day": 0.0,
            "avg_net_pct": 0.0,
            "win_pct": 0.0,
            "daily_sharpe": 0.0,
            "max_drawdown_sum_pct": 0.0,
        }
    selected = day_mask[day_idx]
    selected_net = net[selected].astype(np.float32)
    selected_days = day_idx[selected].astype(np.int32)
    day_net = np.bincount(selected_days, weights=selected_net, minlength=day_count).astype(np.float32)
    day_values = day_net[day_mask].astype(np.float32)
    trading_days = np.float32(day_values.size)
    trades = np.float32(selected_net.size)
    mean_day = finite_divide(np.array([day_values.sum()], dtype=np.float32), np.array([trading_days], dtype=np.float32), default=0.0)[0]
    sq_mean = finite_divide(np.array([(day_values * day_values).sum()], dtype=np.float32), np.array([trading_days], dtype=np.float32), default=0.0)[0]
    std_day = np.sqrt(np.maximum(sq_mean - mean_day * mean_day, 0.0)).astype(np.float32)
    curve = np.cumsum(day_values, dtype=np.float32)
    drawdown = curve - np.maximum.accumulate(curve) if curve.size else np.array([0.0], dtype=np.float32)
    return {
        "trading_days": float(trading_days),
        "trades": float(trades),
        "trades_per_day": float(finite_divide(np.array([trades], dtype=np.float32), np.array([trading_days], dtype=np.float32), default=0.0)[0]),
        "avg_net_pct": float(finite_divide(np.array([selected_net.sum()], dtype=np.float32), np.array([trades], dtype=np.float32), default=0.0)[0]),
        "win_pct": float(finite_divide(np.array([(selected_net > 0.0).sum() * 100.0], dtype=np.float32), np.array([trades], dtype=np.float32), default=0.0)[0]),
        "daily_sharpe": float(finite_divide(np.array([mean_day * np.sqrt(np.float32(252.0))], dtype=np.float32), np.array([std_day], dtype=np.float32), default=0.0)[0]),
        "max_drawdown_sum_pct": float(drawdown.min()) if drawdown.size else 0.0,
    }


def pattern_score(row: dict[str, float], min_trades: int) -> float:
    validation_sharpe = float(np.clip(row["validation_daily_sharpe"], -8.0, 8.0))
    oos_sharpe = float(np.clip(row["out_of_sample_daily_sharpe"], -8.0, 8.0))
    validation_avg = float(row["validation_avg_net_pct"])
    oos_avg = float(row["out_of_sample_avg_net_pct"])
    full_avg = float(row["full_avg_net_pct"])
    sample_bonus = min(float(row["full_trades"]), 400.0) / 18.0
    oos_sample_penalty = max(0.0, float(min_trades) - float(row["out_of_sample_trades"])) * 1.5
    negative_split_penalty = 0.0
    if validation_avg <= 0.0:
        negative_split_penalty += 55.0 + abs(validation_avg) * 80.0
    if oos_avg <= 0.0:
        negative_split_penalty += 65.0 + abs(oos_avg) * 100.0
    dd_penalty = abs(min(float(row["validation_max_drawdown_sum_pct"]), 0.0)) * 0.4 + abs(min(float(row["out_of_sample_max_drawdown_sum_pct"]), 0.0)) * 0.5
    return (
        validation_sharpe * 8.0
        + oos_sharpe * 8.0
        + min(validation_avg, 2.0) * 28.0
        + min(oos_avg, 2.0) * 34.0
        + full_avg * 12.0
        + sample_bonus
        - dd_penalty
        - oos_sample_penalty
        - negative_split_penalty
    )


def summarize_pattern(
    net: np.ndarray,
    day_idx: np.ndarray,
    dates: np.ndarray,
    day_count: int,
    trade_day_mask: np.ndarray,
    min_trades: int,
) -> dict[str, float]:
    row: dict[str, float] = {}
    full_stats = period_stats(net, day_idx, day_count, trade_day_mask)
    for key, value in full_stats.items():
        row[f"full_{key}"] = value
    for split_name, mask in split_day_masks(dates, trade_day_mask).items():
        stats = period_stats(net, day_idx, day_count, mask)
        for key, value in stats.items():
            row[f"{split_name}_{key}"] = value
    row["score"] = float(pattern_score(row, min_trades))
    return row


def first_symbol_day_positions(
    candidate_mask: np.ndarray,
    cand_day: np.ndarray,
    cand_symbol: np.ndarray,
    cand_entry_idx: np.ndarray,
    symbol_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    selected = np.flatnonzero(candidate_mask)
    if selected.size == 0:
        empty = np.array([], dtype=np.int32)
        return empty, empty, empty
    keys = cand_day[selected].astype(np.int64) * np.int64(symbol_count) + cand_symbol[selected].astype(np.int64)
    first = np.unique(keys, return_index=True)[1].astype(np.int32)
    chosen = selected[first].astype(np.int32)
    return cand_day[chosen].astype(np.int32), cand_symbol[chosen].astype(np.int32), cand_entry_idx[chosen].astype(np.int32)


def simulate_short_positions(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    day_idx: np.ndarray,
    symbol_idx: np.ndarray,
    selected_entry_idx: np.ndarray,
    hold_minutes: int,
    stop_pct: float,
    target_pct: float,
    round_trip_cost_pct: float,
) -> dict[str, np.ndarray]:
    empty_float = np.array([], dtype=np.float32)
    empty_int = np.array([], dtype=np.int32)
    empty_bool = np.array([], dtype=bool)
    empty_obj = np.array([], dtype=object)
    if day_idx.size == 0:
        return {
            "day_idx": empty_int,
            "symbol_idx": empty_int,
            "entry_idx": empty_int,
            "exit_idx": empty_int,
            "exit_offset": empty_int,
            "entry": empty_float,
            "exit_price": empty_float,
            "gross": empty_float,
            "net": empty_float,
            "target_first": empty_bool,
            "stop_first": empty_bool,
            "timeout": empty_bool,
            "exit_type": empty_obj,
        }

    path_offsets = np.arange(int(hold_minutes), dtype=np.int32)
    path_idx = selected_entry_idx.reshape(-1, 1) + path_offsets.reshape(1, -1)
    entry = o[day_idx, symbol_idx, selected_entry_idx].astype(np.float32)
    path_open = o[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_high = h[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_low = l[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_close = c[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_valid = (
        np.isfinite(entry)
        & np.isfinite(path_open).all(axis=1)
        & np.isfinite(path_high).all(axis=1)
        & np.isfinite(path_low).all(axis=1)
        & np.isfinite(path_close).all(axis=1)
        & (entry > 0.0)
    )
    day_idx = day_idx[path_valid].astype(np.int32)
    symbol_idx = symbol_idx[path_valid].astype(np.int32)
    selected_entry_idx = selected_entry_idx[path_valid].astype(np.int32)
    entry = entry[path_valid].astype(np.float32)
    path_open = path_open[path_valid].astype(np.float32)
    path_high = path_high[path_valid].astype(np.float32)
    path_low = path_low[path_valid].astype(np.float32)
    path_close = path_close[path_valid].astype(np.float32)
    if entry.size == 0:
        return simulate_short_positions(o, h, l, c, empty_int, empty_int, empty_int, hold_minutes, stop_pct, target_pct, round_trip_cost_pct)

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
    exit_type = np.where(stop_gap, "SL_GAP", np.where(stop_first, "SL", np.where(target_first, "TARGET", "TIME"))).astype(object)
    exit_idx = (selected_entry_idx + exit_offset).astype(np.int32)
    gross = ((entry - exit_price) / entry * 100.0).astype(np.float32)
    net = (gross - np.float32(round_trip_cost_pct)).astype(np.float32)

    return {
        "day_idx": day_idx,
        "symbol_idx": symbol_idx,
        "entry_idx": selected_entry_idx,
        "exit_idx": exit_idx,
        "exit_offset": exit_offset,
        "entry": entry,
        "exit_price": exit_price,
        "gross": gross,
        "net": net,
        "target_first": target_first,
        "stop_first": stop_first,
        "timeout": timeout,
        "exit_type": exit_type,
    }


def build_tradebook(
    sim: dict[str, np.ndarray],
    dates_np: np.ndarray,
    symbols_index: pd.Index,
    gap: np.ndarray,
    bar_rvol: np.ndarray,
    vol20_rvol: np.ndarray,
    cum_rvol: np.ndarray,
    volume_accel: np.ndarray,
    mom15: np.ndarray,
    drop_from_day_high: np.ndarray,
) -> pd.DataFrame:
    day_idx = sim["day_idx"].astype(np.int32)
    symbol_idx = sim["symbol_idx"].astype(np.int32)
    entry_idx = sim["entry_idx"].astype(np.int32)
    if day_idx.size == 0:
        return pd.DataFrame()
    selected_feature_idx = (entry_idx - 1).astype(np.int32)
    selected_local_idx = (entry_idx - np.int32(FEATURE_BUCKET_START)).astype(np.int32)
    tradebook = pd.DataFrame(
        {
            "date": dates_np[day_idx].astype(str),
            "symbol": symbols_index.to_numpy(dtype=object)[symbol_idx],
            "direction": np.full(day_idx.shape, "SHORT", dtype=object),
            "signal_bucket": (selected_feature_idx + 1).astype(np.int32),
            "signal_time": minute_labels(selected_feature_idx),
            "entry_bucket": (entry_idx + 1).astype(np.int32),
            "entry_time": minute_labels(entry_idx),
            "entry_price": sim["entry"].astype(np.float32),
            "exit_bucket": (sim["exit_idx"].astype(np.int32) + 1).astype(np.int32),
            "exit_time": minute_labels(sim["exit_idx"].astype(np.int32)),
            "exit_price": sim["exit_price"].astype(np.float32),
            "exit_type": sim["exit_type"],
            "hold_minutes": sim["exit_offset"].astype(np.int16),
            "gross_pct": sim["gross"].astype(np.float32),
            "net_pct": sim["net"].astype(np.float32),
            "gap_pct": gap[day_idx, symbol_idx].astype(np.float32),
            "bar_rvol": bar_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "vol20_rvol": vol20_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "cum_rvol": cum_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "volume_accel": volume_accel[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "mom15_pct": mom15[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "drop_from_day_high_pct": drop_from_day_high[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
        }
    )
    return tradebook.sort_values(["date", "entry_bucket", "symbol"], kind="mergesort").reset_index(drop=True)


def daily_summary(day_net: np.ndarray, day_counts: np.ndarray, day_wins: np.ndarray, dates: np.ndarray, trade_mask: np.ndarray) -> pd.DataFrame:
    selected_net = day_net[trade_mask].astype(np.float32)
    selected_counts = day_counts[trade_mask].astype(np.float32)
    selected_wins = day_wins[trade_mask].astype(np.float32)
    selected_dates = dates[trade_mask].astype(str)
    cum_net = np.cumsum(selected_net, dtype=np.float64)
    return pd.DataFrame(
        {
            "date": selected_dates,
            "trades": selected_counts.astype(np.int32),
            "wins": selected_wins.astype(np.int32),
            "win_pct": finite_divide(selected_wins * 100.0, selected_counts, default=0.0),
            "day_net_pct": selected_net,
            "cum_net_pct": cum_net,
        }
    )


def metric_summary(
    net: np.ndarray,
    day_net: np.ndarray,
    day_counts: np.ndarray,
    target_first: np.ndarray,
    stop_first: np.ndarray,
    timeout: np.ndarray,
    trade_mask: np.ndarray,
) -> pd.DataFrame:
    values = np.where(trade_mask, day_net, 0.0).astype(np.float32)
    counts = np.where(trade_mask, day_counts, 0.0).astype(np.float32)
    period_days = np.float32(trade_mask.sum())
    total_net = np.float32(values.sum())
    total_count = np.float32(counts.sum())
    mean_day = finite_divide(np.array([total_net], dtype=np.float32), np.array([period_days], dtype=np.float32), default=0.0)[0]
    sq_mean = finite_divide(np.array([(values * values).sum()], dtype=np.float32), np.array([period_days], dtype=np.float32), default=0.0)[0]
    std_day = np.sqrt(np.maximum(sq_mean - mean_day * mean_day, 0.0)).astype(np.float32)
    curve = np.cumsum(values, dtype=np.float32)
    drawdown = curve - np.maximum.accumulate(curve)
    wins = net > 0.0
    return pd.DataFrame(
        {
            "lookback_days": np.array([LOOKBACK_DAYS], dtype=np.int32),
            "min_prior_days": np.array([MIN_PRIOR_DAYS], dtype=np.int32),
            "trading_days": np.array([period_days], dtype=np.float32),
            "trades": np.array([total_count], dtype=np.float32),
            "trades_per_day": finite_divide(np.array([total_count], dtype=np.float32), np.array([period_days], dtype=np.float32), default=0.0),
            "avg_net_pct": finite_divide(np.array([total_net], dtype=np.float32), np.array([total_count], dtype=np.float32), default=0.0),
            "win_pct": finite_divide(np.array([wins.sum() * 100.0], dtype=np.float32), np.array([net.size], dtype=np.float32), default=0.0),
            "target_first_pct": finite_divide(np.array([target_first.sum() * 100.0], dtype=np.float32), np.array([net.size], dtype=np.float32), default=0.0),
            "stop_first_pct": finite_divide(np.array([stop_first.sum() * 100.0], dtype=np.float32), np.array([net.size], dtype=np.float32), default=0.0),
            "timeout_pct": finite_divide(np.array([timeout.sum() * 100.0], dtype=np.float32), np.array([net.size], dtype=np.float32), default=0.0),
            "daily_sharpe": finite_divide(np.array([mean_day * np.sqrt(np.float32(252.0))], dtype=np.float32), np.array([std_day], dtype=np.float32), default=0.0),
            "max_drawdown_sum_pct": np.array([drawdown.min()], dtype=np.float32),
        }
    )


def t_test(values: np.ndarray) -> dict[str, object]:
    clean = np.asarray(values, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    n = int(clean.size)
    if n < 2:
        return {"n": n, "mean_pct": np.nan, "std_pct": np.nan, "t_stat": np.nan, "p_value": np.nan, "method": "insufficient"}
    mean = float(clean.mean())
    std = float(clean.std(ddof=1))
    if std == 0:
        t_stat = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        p_value = 0.0 if mean != 0 else 1.0
        method = "degenerate"
    else:
        t_stat = mean / (std / math.sqrt(n))
        try:
            from scipy import stats

            p_value = float(stats.t.sf(abs(t_stat), df=n - 1) * 2.0)
            method = "student_t"
        except Exception:
            p_value = float(math.erfc(abs(t_stat) / math.sqrt(2.0)))
            method = "normal_approx"
    return {"n": n, "mean_pct": mean, "std_pct": std, "t_stat": float(t_stat), "p_value": p_value, "method": method}


def equity_curve_frame(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    out = daily.copy()
    out["running_peak_pct"] = out["cum_net_pct"].cummax()
    out["drawdown_pct"] = out["cum_net_pct"] - out["running_peak_pct"]
    return out


def drawdown_details(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    frame = equity_curve_frame(daily)
    frame["date_dt"] = pd.to_datetime(frame["date"])
    drawdown = frame["drawdown_pct"].to_numpy(np.float64)
    trough_idx = int(np.argmin(drawdown))
    curve = frame["cum_net_pct"].to_numpy(np.float64)
    peak_idx = int(np.argmax(curve[: trough_idx + 1]))
    peak_value = float(curve[peak_idx])
    recovered = frame.index[(frame.index > trough_idx) & (curve >= peak_value)]
    recovery_date = frame.loc[int(recovered[0]), "date"] if len(recovered) else ""
    return pd.DataFrame(
        [
            {
                "max_drawdown_sum_pct": float(drawdown[trough_idx]),
                "peak_date": frame.loc[peak_idx, "date"],
                "trough_date": frame.loc[trough_idx, "date"],
                "recovery_date": recovery_date,
                "peak_cum_net_pct": peak_value,
                "trough_cum_net_pct": float(curve[trough_idx]),
                "drawdown_duration_trading_days": int(trough_idx - peak_idx),
            }
        ]
    )


def period_drawdown(day_returns: pd.Series) -> float:
    values = day_returns.fillna(0.0).to_numpy(np.float64)
    if values.size == 0:
        return 0.0
    curve = np.cumsum(values)
    drawdown = curve - np.maximum.accumulate(curve)
    return float(drawdown.min())


def period_summary(tradebook: pd.DataFrame, daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    daily_work = daily.copy()
    daily_work["date_dt"] = pd.to_datetime(daily_work["date"])
    daily_work["period"] = daily_work["date_dt"].dt.to_period(freq).astype(str)
    daily_stats = (
        daily_work.groupby("period")
        .agg(
            trading_days=("date", "size"),
            active_days=("trades", lambda s: int((s > 0).sum())),
            avg_day_net_pct=("day_net_pct", "mean"),
            day_net_std_pct=("day_net_pct", lambda s: float(s.std(ddof=1)) if len(s) > 1 else 0.0),
            daily_net_sum_pct=("day_net_pct", "sum"),
            max_drawdown_sum_pct=("day_net_pct", period_drawdown),
            positive_day_pct=("day_net_pct", lambda s: float((s > 0).mean() * 100.0)),
        )
        .reset_index()
    )

    if tradebook.empty:
        trade_stats = pd.DataFrame({"period": daily_stats["period"]})
    else:
        trade_work = tradebook.copy()
        trade_work["date_dt"] = pd.to_datetime(trade_work["date"])
        trade_work["period"] = trade_work["date_dt"].dt.to_period(freq).astype(str)
        trade_stats = (
            trade_work.groupby("period")
            .agg(
                trades=("net_pct", "size"),
                wins=("net_pct", lambda s: int((s > 0).sum())),
                win_pct=("net_pct", lambda s: float((s > 0).mean() * 100.0)),
                net_sum_pct=("net_pct", "sum"),
                avg_trade_net_pct=("net_pct", "mean"),
                median_trade_net_pct=("net_pct", "median"),
                target_first=("exit_type", lambda s: int((s == "TARGET").sum())),
                stop_first=("exit_type", lambda s: int(s.isin(["SL", "SL_GAP"]).sum())),
                time_exit=("exit_type", lambda s: int((s == "TIME").sum())),
            )
            .reset_index()
        )

    out = daily_stats.merge(trade_stats, on="period", how="left")
    for col in ["trades", "wins", "win_pct", "net_sum_pct", "avg_trade_net_pct", "median_trade_net_pct", "target_first", "stop_first", "time_exit"]:
        if col in out.columns:
            out[col] = out[col].fillna(0.0)
    out["daily_sharpe"] = np.where(
        out["day_net_std_pct"] > 0.0,
        out["avg_day_net_pct"] / out["day_net_std_pct"] * math.sqrt(252.0),
        0.0,
    )
    ordered = [
        "period",
        "trading_days",
        "active_days",
        "trades",
        "wins",
        "win_pct",
        "net_sum_pct",
        "avg_trade_net_pct",
        "median_trade_net_pct",
        "daily_net_sum_pct",
        "avg_day_net_pct",
        "daily_sharpe",
        "max_drawdown_sum_pct",
        "positive_day_pct",
        "target_first",
        "stop_first",
        "time_exit",
    ]
    return out[[col for col in ordered if col in out.columns]]


def weekly_losers(tradebook: pd.DataFrame) -> pd.DataFrame:
    if tradebook.empty:
        return pd.DataFrame()
    work = tradebook.copy()
    work["date_dt"] = pd.to_datetime(work["date"])
    work["week"] = work["date_dt"].dt.to_period("W-FRI")
    return (
        work.groupby("week")
        .agg(
            week_start=("date_dt", lambda s: s.min().date().isoformat()),
            week_end=("date_dt", lambda s: s.max().date().isoformat()),
            trades=("net_pct", "size"),
            wins=("net_pct", lambda s: int((s > 0).sum())),
            win_pct=("net_pct", lambda s: float((s > 0).mean() * 100.0)),
            net_sum_pct=("net_pct", "sum"),
            avg_trade_net_pct=("net_pct", "mean"),
            worst_trade_pct=("net_pct", "min"),
            best_trade_pct=("net_pct", "max"),
        )
        .reset_index(drop=True)
        .sort_values("net_sum_pct", ascending=True)
        .head(10)
    )


def t_test_summary(tradebook: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    trade_values = tradebook["net_pct"].to_numpy(np.float64) if not tradebook.empty else np.array([], dtype=np.float64)
    daily_values = daily["day_net_pct"].to_numpy(np.float64) if not daily.empty else np.array([], dtype=np.float64)
    rows.append({"sample": "trade_net_pct", **t_test(trade_values)})
    rows.append({"sample": "daily_day_net_pct", **t_test(daily_values)})
    return pd.DataFrame(rows)


def cost_sensitivity(tradebook: pd.DataFrame, daily_template: pd.DataFrame) -> pd.DataFrame:
    if tradebook.empty:
        return pd.DataFrame()
    rows = []
    for cost in [0.0, 0.05, 0.10, 0.20, 0.30, 0.50]:
        net = tradebook["gross_pct"].to_numpy(np.float64) - cost
        by_day = pd.DataFrame({"date": tradebook["date"], "net_pct": net}).groupby("date")["net_pct"].sum()
        daily = daily_template[["date"]].copy()
        daily["day_net_pct"] = daily["date"].map(by_day).fillna(0.0).astype(float)
        daily_test = t_test(daily["day_net_pct"].to_numpy(np.float64))
        rows.append(
            {
                "round_trip_cost_pct": cost,
                "trades": int(net.size),
                "win_pct": float((net > 0.0).mean() * 100.0),
                "avg_trade_net_pct": float(net.mean()),
                "net_sum_pct": float(net.sum()),
                "daily_t_stat": daily_test["t_stat"],
                "daily_p_value": daily_test["p_value"],
            }
        )
    return pd.DataFrame(rows)


def robustness_summary(tradebook: pd.DataFrame, daily: pd.DataFrame, monthly: pd.DataFrame, quarterly: pd.DataFrame) -> pd.DataFrame:
    if tradebook.empty or daily.empty:
        return pd.DataFrame()
    date_series = pd.to_datetime(tradebook["date"])
    first_date = date_series.min()
    last_date = date_series.max()
    mid_date = first_date + (last_date - first_date) / 2
    rows = [
        {"check": "positive_days_pct", "value": float((daily["day_net_pct"] > 0.0).mean() * 100.0)},
        {"check": "positive_months_pct", "value": float((monthly["net_sum_pct"] > 0.0).mean() * 100.0) if not monthly.empty else np.nan},
        {"check": "positive_quarters_pct", "value": float((quarterly["net_sum_pct"] > 0.0).mean() * 100.0) if not quarterly.empty else np.nan},
    ]
    for label, mask in [("first_half", date_series <= mid_date), ("second_half", date_series > mid_date)]:
        part = tradebook.loc[mask, "net_pct"].to_numpy(np.float64)
        rows.append(
            {
                "check": label,
                "trades": int(part.size),
                "avg_net_pct": float(part.mean()) if part.size else np.nan,
                "win_pct": float((part > 0.0).mean() * 100.0) if part.size else np.nan,
                "net_sum_pct": float(part.sum()) if part.size else np.nan,
            }
        )
    return pd.DataFrame(rows)


def write_diagnostics(prefix: str, tradebook: pd.DataFrame, daily: pd.DataFrame, out_dir: Path) -> str:
    file_prefix = f"{prefix}_" if prefix else ""
    equity = equity_curve_frame(daily)
    drawdown = drawdown_details(daily)
    monthly = period_summary(tradebook, daily, "M")
    quarterly = period_summary(tradebook, daily, "Q")
    losing_weeks = weekly_losers(tradebook)
    tests = t_test_summary(tradebook, daily)
    costs = cost_sensitivity(tradebook, daily)
    robust = robustness_summary(tradebook, daily, monthly, quarterly)

    equity.to_csv(out_dir / f"{file_prefix}equity_curve.csv", index=False)
    drawdown.to_csv(out_dir / f"{file_prefix}drawdown_details.csv", index=False)
    monthly.to_csv(out_dir / f"{file_prefix}monthly_analysis.csv", index=False)
    quarterly.to_csv(out_dir / f"{file_prefix}quarterly_analysis.csv", index=False)
    losing_weeks.to_csv(out_dir / f"{file_prefix}top_10_losing_weeks.csv", index=False)
    tests.to_csv(out_dir / f"{file_prefix}t_test_summary.csv", index=False)
    costs.to_csv(out_dir / f"{file_prefix}cost_sensitivity.csv", index=False)
    robust.to_csv(out_dir / f"{file_prefix}robustness_summary.csv", index=False)

    title = "Baseline Diagnostics" if not prefix else f"{prefix.replace('_', ' ').title()} Diagnostics"
    return f"""
## {title}

### T-Test

{markdown_table(tests, max_rows=10)}

### Drawdown

{markdown_table(drawdown, max_rows=5)}

### Quarterly Sharpe

{markdown_table(quarterly[["period", "trading_days", "trades", "net_sum_pct", "avg_trade_net_pct", "daily_sharpe", "max_drawdown_sum_pct", "positive_day_pct"]], max_rows=12)}

### Monthly Analysis

{markdown_table(monthly[["period", "trading_days", "trades", "win_pct", "net_sum_pct", "avg_trade_net_pct", "daily_sharpe", "max_drawdown_sum_pct", "positive_day_pct"]], max_rows=24)}

### Top 10 Losing Weeks

{markdown_table(losing_weeks, max_rows=10)}

### Cost Sensitivity

{markdown_table(costs, max_rows=10)}

### Robustness Checks

{markdown_table(robust, max_rows=10)}
"""


def live_signal_sheet(tradebook: pd.DataFrame, live_date: str, preset_name: str) -> pd.DataFrame:
    if tradebook.empty:
        return pd.DataFrame()
    live = tradebook[tradebook["date"].astype(str).eq(str(live_date))].copy()
    if live.empty:
        return live
    live["strategy_preset"] = preset_name
    live["action"] = "SHORT_NEXT_MINUTE_OPEN"
    live["stop_price"] = live["entry_price"].astype(float) * (1.0 + float(STOP_PCT) / 100.0)
    live["target_price"] = live["entry_price"].astype(float) * (1.0 - float(TARGET_PCT) / 100.0)
    live["planned_hold_minutes"] = int(HOLD_MINUTES)
    live["note"] = "Backfilled from parquet; in real time, signal is known after signal minute closes and entry is next minute open."
    cols = [
        "date",
        "strategy_preset",
        "symbol",
        "action",
        "signal_time",
        "entry_time",
        "entry_price",
        "stop_price",
        "target_price",
        "planned_hold_minutes",
        "gap_pct",
        "bar_rvol",
        "vol20_rvol",
        "cum_rvol",
        "volume_accel",
        "mom15_pct",
        "drop_from_day_high_pct",
        "note",
    ]
    return live[[col for col in cols if col in live.columns]]


def load_target_symbols() -> pd.Index:
    groups_raw = json.loads(VOLUME_GROUPS_PATH.read_text(encoding="utf-8")) if VOLUME_GROUPS_PATH.exists() else {"volume_groups": {}}
    groups = groups_raw.get("volume_groups", {})
    mega = pd.Index(groups.get("MEGA (>100cr/day)", ()), dtype=object)
    large = pd.Index(groups.get("LARGE (10-100cr/day)", ()), dtype=object)
    return mega.union(large)


def plot_curve(daily: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(pd.to_datetime(daily["date"]), daily["cum_net_pct"], label="baseline position-book")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_title("Parquet baseline volume-spike 3%/3% short, one position per symbol/day")
    ax.set_ylabel("cumulative net, summed trade %")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_daily_bars(daily: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(daily.shape[0], dtype=np.float32)
    ax.bar(x, daily["day_net_pct"].to_numpy(np.float32), label="daily net")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(daily["date"], rotation=45, ha="right")
    ax.set_title("Daily net by trade date")
    ax.set_ylabel("daily net, summed trade %")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def pattern_grid_specs(args: argparse.Namespace) -> list[dict[str, float | int | str]]:
    specs: list[dict[str, float | int | str]] = []
    for bar, vol20, cum, accel, drop, gap_min, mom, hold in itertools.product(
        parse_float_list(args.search_bar_rvol),
        parse_float_list(args.search_vol20_rvol),
        parse_float_list(args.search_cum_rvol),
        parse_float_list(args.search_volume_accel_max),
        parse_float_list(args.search_drop_from_high),
        parse_float_list(args.search_gap_up_min),
        parse_window_list(args.search_mom15_windows),
        parse_int_list(args.search_holds),
    ):
        mom_min, mom_max = mom
        specs.append(
            {
                "name": f"rv{bar:g}_v20{vol20:g}_cum{cum:g}_acc{accel:g}_drop{drop:g}_gap{gap_min:g}_m{mom_min:g}_{mom_max:g}_h{hold}",
                "bar_rvol_min": float(bar),
                "vol20_rvol_min": float(vol20),
                "cum_rvol_min": float(cum),
                "volume_accel_max": float(accel),
                "drop_from_high_min": float(drop),
                "gap_up_min": float(gap_min),
                "mom15_min": float(mom_min),
                "mom15_max": float(mom_max),
                "hold_minutes": int(hold),
                "stop_pct": float(args.stop_pct),
                "target_pct": float(args.target_pct),
            }
        )
    if args.grid_limit and len(specs) > args.grid_limit:
        picks = np.linspace(0, len(specs) - 1, int(args.grid_limit), dtype=np.int32)
        specs = [specs[int(i)] for i in picks]
    return specs


def plot_pattern_search(results: pd.DataFrame, out_dir: Path) -> None:
    if results.empty:
        return
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    top = results.head(25).sort_values("score")
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.barh(top["name"], top["score"], color="#1b998b")
    ax.set_title("Top volume-spike pattern-search scores")
    ax.set_xlabel("walk-forward score")
    fig.tight_layout()
    fig.savefig(chart_dir / "pattern_top_scores.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(results["validation_avg_net_pct"], results["out_of_sample_avg_net_pct"], s=24, alpha=0.45, color="#33658a")
    ax.axhline(0.0, color="#333333", linewidth=0.9)
    ax.axvline(0.0, color="#333333", linewidth=0.9)
    ax.set_title("Validation vs out-of-sample expectancy")
    ax.set_xlabel("validation avg net %")
    ax.set_ylabel("out-of-sample avg net %")
    fig.tight_layout()
    fig.savefig(chart_dir / "pattern_validation_vs_oos.png", dpi=150)
    plt.close(fig)


def run_pattern_search(
    args: argparse.Namespace,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    dates_np: np.ndarray,
    symbols_index: pd.Index,
    symbol_count: int,
    day_count: int,
    feature_idx: np.ndarray,
    base_valid: np.ndarray,
    trade_day_mask: np.ndarray,
    gap_3d: np.ndarray,
    gap: np.ndarray,
    bar_rvol: np.ndarray,
    vol20_rvol: np.ndarray,
    cum_rvol: np.ndarray,
    volume_accel: np.ndarray,
    mom15: np.ndarray,
    drop_from_day_high: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    specs = pattern_grid_specs(args)
    if not specs:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    min_bar = min(float(spec["bar_rvol_min"]) for spec in specs)
    min_vol20 = min(float(spec["vol20_rvol_min"]) for spec in specs)
    min_cum = min(float(spec["cum_rvol_min"]) for spec in specs)
    max_accel = max(float(spec["volume_accel_max"]) for spec in specs)
    min_drop = min(float(spec["drop_from_high_min"]) for spec in specs)
    min_gap = min(float(spec["gap_up_min"]) for spec in specs)
    min_mom = min(float(spec["mom15_min"]) for spec in specs)
    max_mom = max(float(spec["mom15_max"]) for spec in specs)

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
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    cand_entry_idx = (feature_idx[cand_local] + 1).astype(np.int32)
    order = np.lexsort((cand_entry_idx, cand_symbol, cand_day))
    cand_day = cand_day[order].astype(np.int32)
    cand_symbol = cand_symbol[order].astype(np.int32)
    cand_local = cand_local[order].astype(np.int32)
    cand_entry_idx = cand_entry_idx[order].astype(np.int32)
    cand_bar_rvol = bar_rvol[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_vol20_rvol = vol20_rvol[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_cum_rvol = cum_rvol[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_volume_accel = volume_accel[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_drop = drop_from_day_high[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_gap = gap_3d[cand_day, cand_symbol, cand_local].astype(np.float32)
    cand_mom = mom15[cand_day, cand_symbol, cand_local].astype(np.float32)

    log(f"Pattern search candidate rows: {cand_day.size:,} | grid combos: {len(specs):,}")
    rows: list[dict[str, float | int | str]] = []
    best_score = -np.inf
    best_sim: dict[str, np.ndarray] | None = None
    best_spec: dict[str, float | int | str] | None = None

    for idx, spec in enumerate(specs, start=1):
        if idx == 1 or idx % 500 == 0 or idx == len(specs):
            log(f"Pattern grid {idx:,}/{len(specs):,}")
        candidate_mask = (
            (cand_bar_rvol >= float(spec["bar_rvol_min"]))
            & (cand_vol20_rvol >= float(spec["vol20_rvol_min"]))
            & (cand_cum_rvol >= float(spec["cum_rvol_min"]))
            & (cand_volume_accel < float(spec["volume_accel_max"]))
            & (cand_drop >= float(spec["drop_from_high_min"]))
            & (cand_gap >= float(spec["gap_up_min"]))
            & (cand_mom >= float(spec["mom15_min"]))
            & (cand_mom <= float(spec["mom15_max"]))
            & (cand_entry_idx + int(spec["hold_minutes"]) <= min(BUCKET_COUNT, LATEST_EXIT_BUCKET))
        )
        day_idx, symbol_idx, entry_idx = first_symbol_day_positions(candidate_mask, cand_day, cand_symbol, cand_entry_idx, symbol_count)
        if day_idx.size < max(1, int(args.min_trades) // 3):
            continue
        sim = simulate_short_positions(
            o,
            h,
            l,
            c,
            day_idx,
            symbol_idx,
            entry_idx,
            int(spec["hold_minutes"]),
            float(spec["stop_pct"]),
            float(spec["target_pct"]),
            float(args.round_trip_cost_pct),
        )
        net = sim["net"].astype(np.float32)
        if net.size == 0:
            continue
        metrics = summarize_pattern(net, sim["day_idx"].astype(np.int32), dates_np, day_count, trade_day_mask, int(args.min_trades))
        row = {**spec, **metrics}
        rows.append(row)
        if float(metrics["score"]) > best_score and float(metrics["full_trades"]) >= float(args.min_trades):
            best_score = float(metrics["score"])
            best_sim = sim
            best_spec = spec

    results = pd.DataFrame(rows)
    if results.empty:
        return results, pd.DataFrame(), pd.DataFrame()
    results = results.sort_values(["score", "out_of_sample_avg_net_pct", "validation_avg_net_pct"], ascending=False).reset_index(drop=True)
    results.to_csv(OUT_DIR / "pattern_grid.csv", index=False)
    plot_pattern_search(results, OUT_DIR)

    if best_sim is None or best_spec is None:
        best_spec = results.iloc[0].to_dict()
        best_score = float(best_spec.get("score", 0.0))
        candidate_mask = (
            (cand_bar_rvol >= float(best_spec["bar_rvol_min"]))
            & (cand_vol20_rvol >= float(best_spec["vol20_rvol_min"]))
            & (cand_cum_rvol >= float(best_spec["cum_rvol_min"]))
            & (cand_volume_accel < float(best_spec["volume_accel_max"]))
            & (cand_drop >= float(best_spec["drop_from_high_min"]))
            & (cand_gap >= float(best_spec["gap_up_min"]))
            & (cand_mom >= float(best_spec["mom15_min"]))
            & (cand_mom <= float(best_spec["mom15_max"]))
            & (cand_entry_idx + int(best_spec["hold_minutes"]) <= min(BUCKET_COUNT, LATEST_EXIT_BUCKET))
        )
        day_idx, symbol_idx, entry_idx = first_symbol_day_positions(candidate_mask, cand_day, cand_symbol, cand_entry_idx, symbol_count)
        best_sim = simulate_short_positions(
            o,
            h,
            l,
            c,
            day_idx,
            symbol_idx,
            entry_idx,
            int(best_spec["hold_minutes"]),
            float(best_spec["stop_pct"]),
            float(best_spec["target_pct"]),
            float(args.round_trip_cost_pct),
        )

    best_tradebook = build_tradebook(
        best_sim,
        dates_np,
        symbols_index,
        gap,
        bar_rvol,
        vol20_rvol,
        cum_rvol,
        volume_accel,
        mom15,
        drop_from_day_high,
    )
    if not best_tradebook.empty:
        best_tradebook.insert(0, "pattern_name", str(best_spec["name"]))
    best_tradebook.to_csv(OUT_DIR / "best_pattern_tradebook.csv", index=False)

    best_day_idx = best_sim["day_idx"].astype(np.int32)
    best_net = best_sim["net"].astype(np.float32)
    best_wins = best_net > 0.0
    best_day_net = np.bincount(best_day_idx, weights=best_net, minlength=day_count).astype(np.float32)
    best_day_counts = np.bincount(best_day_idx, minlength=day_count).astype(np.float32)
    best_day_wins = np.bincount(best_day_idx, weights=best_wins.astype(np.float32), minlength=day_count).astype(np.float32)
    best_daily = daily_summary(best_day_net, best_day_counts, best_day_wins, dates_np, trade_day_mask)
    best_daily.to_csv(OUT_DIR / "best_pattern_daily_summary.csv", index=False)
    plot_curve(best_daily, OUT_DIR / "best_pattern_equity_curve.png")
    plot_daily_bars(best_daily, OUT_DIR / "best_pattern_daily_net.png")
    log(f"Best pattern: {best_spec['name']} | score {best_score:0.2f} | trades {best_tradebook.shape[0]:,}")
    return results, best_tradebook, best_daily


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the intraday volume-spike 3%/3% setup directly from candle parquet files.")
    parser.add_argument("--parquet-dir", type=Path, default=DATA_DIR, help="Directory containing candles_YYYYMM.parquet files.")
    parser.add_argument("--parquet-glob", default=PARQUET_GLOB, help="Glob of parquet files to load from --parquet-dir. Comma-separated patterns are allowed.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="Output directory for report, summary, charts, and tradebook.")
    parser.add_argument("--volume-groups-path", type=Path, default=VOLUME_GROUPS_PATH)
    parser.add_argument("--trade-start-date", default=str(TRADE_START_DATE), help="Inclusive trade start date, YYYY-MM-DD.")
    parser.add_argument("--trade-end-date", default=str(TRADE_END_DATE), help="Inclusive trade end date, YYYY-MM-DD.")
    parser.add_argument(
        "--use-volume-groups",
        action="store_true",
        help="Restrict to MEGA/LARGE symbols from volume_groups.json. Default is all symbols present in parquet.",
    )
    parser.add_argument("--hold-minutes", type=int, default=int(HOLD_MINUTES), help="Baseline hold minutes.")
    parser.add_argument("--stop-pct", type=float, default=float(STOP_PCT), help="Short stop percentage for baseline and search.")
    parser.add_argument("--target-pct", type=float, default=float(TARGET_PCT), help="Short target percentage for baseline and search.")
    parser.add_argument("--round-trip-cost-pct", type=float, default=float(ROUND_TRIP_COST_PCT), help="Round-trip cost/slippage in percentage points.")
    parser.add_argument(
        "--preset",
        choices=["baseline", "candidate60"],
        default="baseline",
        help="Rule preset. candidate60 is the stronger sampled-grid candidate from the 2026 study.",
    )
    parser.add_argument("--bar-rvol-min", type=float, default=float(BAR_RVOL_MIN))
    parser.add_argument("--vol20-rvol-min", type=float, default=float(VOL20_RVOL_MIN))
    parser.add_argument("--cum-rvol-min", type=float, default=float(CUM_RVOL_MIN))
    parser.add_argument("--volume-accel-max", type=float, default=float(VOLUME_ACCEL_MAX))
    parser.add_argument("--drop-from-high-min", type=float, default=float(DROP_FROM_HIGH_MIN))
    parser.add_argument("--gap-up-min", type=float, default=float(GAP_UP_MIN))
    parser.add_argument("--mom15-min", type=float, default=float(MOM15_MIN))
    parser.add_argument("--mom15-max", type=float, default=float(MOM15_MAX))
    parser.add_argument("--live-signals", action="store_true", help="Write latest-session signal sheet for the configured baseline/preset rule.")
    parser.add_argument("--live-date", default=None, help="Signal date to export. Defaults to latest available trade date in the run.")
    parser.add_argument("--search-grid", action="store_true", help="Run fast NumPy pattern search after the baseline.")
    parser.add_argument("--search-holds", default=SEARCH_HOLDS, help="Comma-separated hold-minute values for pattern search.")
    parser.add_argument("--search-bar-rvol", default=SEARCH_BAR_RVOL, help="Comma-separated 1-minute relative-volume minimums.")
    parser.add_argument("--search-vol20-rvol", default=SEARCH_VOL20_RVOL, help="Comma-separated 20-minute relative-volume minimums.")
    parser.add_argument("--search-cum-rvol", default=SEARCH_CUM_RVOL, help="Comma-separated cumulative relative-volume minimums.")
    parser.add_argument("--search-volume-accel-max", default=SEARCH_VOLUME_ACCEL_MAX, help="Comma-separated max 1m-vs-previous-5m acceleration values.")
    parser.add_argument("--search-drop-from-high", default=SEARCH_DROP_FROM_HIGH, help="Comma-separated minimum drop-from-day-high percentages.")
    parser.add_argument("--search-gap-up-min", default=SEARCH_GAP_UP_MIN, help="Comma-separated minimum open gap percentages.")
    parser.add_argument("--search-mom15-windows", default=SEARCH_MOM15_WINDOWS, help="Comma-separated momentum windows as min:max in percentage points.")
    parser.add_argument("--grid-limit", type=int, default=0, help="Optional deterministic downsample of pattern combos.")
    parser.add_argument("--min-trades", type=int, default=30, help="Minimum full-sample trades for selecting the best pattern.")
    return parser.parse_args()


def main() -> None:
    global DATA_DIR, OUT_DIR, VOLUME_GROUPS_PATH, PARQUET_GLOB, TRADE_START_DATE, TRADE_END_DATE, USE_ALL_PARQUET_SYMBOLS
    global HOLD_MINUTES, STOP_PCT, TARGET_PCT, ROUND_TRIP_COST_PCT, FEATURE_BUCKET_END
    global BAR_RVOL_MIN, VOL20_RVOL_MIN, CUM_RVOL_MIN, VOLUME_ACCEL_MAX, DROP_FROM_HIGH_MIN, GAP_UP_MIN, MOM15_MIN, MOM15_MAX

    args = parse_args()
    if args.preset == "candidate60":
        args.hold_minutes = 60
        args.bar_rvol_min = 8.0
        args.vol20_rvol_min = 2.0
        args.cum_rvol_min = 10.0
        args.volume_accel_max = 2.0
        args.drop_from_high_min = 2.0
        args.gap_up_min = 0.5
        args.mom15_min = 0.0
        args.mom15_max = 1.5

    DATA_DIR = Path(args.parquet_dir).resolve()
    OUT_DIR = Path(args.out_dir).resolve()
    VOLUME_GROUPS_PATH = Path(args.volume_groups_path).resolve()
    PARQUET_GLOB = str(args.parquet_glob)
    TRADE_START_DATE = np.datetime64(args.trade_start_date)
    TRADE_END_DATE = np.datetime64(args.trade_end_date)
    USE_ALL_PARQUET_SYMBOLS = not bool(args.use_volume_groups)
    HOLD_MINUTES = int(args.hold_minutes)
    STOP_PCT = np.float32(args.stop_pct)
    TARGET_PCT = np.float32(args.target_pct)
    ROUND_TRIP_COST_PCT = np.float32(args.round_trip_cost_pct)
    BAR_RVOL_MIN = np.float32(args.bar_rvol_min)
    VOL20_RVOL_MIN = np.float32(args.vol20_rvol_min)
    CUM_RVOL_MIN = np.float32(args.cum_rvol_min)
    VOLUME_ACCEL_MAX = np.float32(args.volume_accel_max)
    DROP_FROM_HIGH_MIN = np.float32(args.drop_from_high_min)
    GAP_UP_MIN = np.float32(args.gap_up_min)
    MOM15_MIN = np.float32(args.mom15_min)
    MOM15_MAX = np.float32(args.mom15_max)
    search_holds = parse_int_list(args.search_holds) if args.search_grid else []
    max_needed_hold = max([HOLD_MINUTES, *search_holds] if search_holds else [HOLD_MINUTES])
    FEATURE_BUCKET_END = effective_feature_bucket_end(max_needed_hold)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parquet_paths = resolve_parquet_paths(DATA_DIR, PARQUET_GLOB)
    if parquet_paths.size == 0:
        raise FileNotFoundError(f"No parquet files matched {DATA_DIR / PARQUET_GLOB}")

    log(f"Loading parquet files: {parquet_paths.size}")
    df = pd.read_parquet(parquet_paths.astype(str).tolist(), columns=COLS)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[df["date"] <= str(TRADE_END_DATE)]
    df = df[df["bucket"].between(1, int(BUCKET_COUNT))]
    target_symbols = load_target_symbols()
    use_volume_groups = (not USE_ALL_PARQUET_SYMBOLS) and (target_symbols.size > 0)
    df = df[df["symbol"].isin(target_symbols)] if use_volume_groups else df
    universe_name = "volume_groups MEGA/LARGE" if use_volume_groups else "all parquet symbols"
    log(f"Rows after filters: {len(df):,} | universe: {universe_name} | symbols: {df['symbol'].nunique():,} | days: {df['date'].nunique():,}")

    dates_index = pd.Index(np.sort(df["date"].unique()), name="date")
    symbols_index = pd.Index(np.sort(df["symbol"].unique()), name="symbol")
    full_index = pd.MultiIndex.from_product((dates_index, symbols_index), names=["date", "symbol"])
    buckets = np.arange(1, int(BUCKET_COUNT) + 1, dtype=np.int32)

    log("Pivoting OHLCV to dense day x symbol x bucket arrays")
    wide = df.pivot_table(index=["date", "symbol"], columns="bucket", values=["open", "high", "low", "close", "volume"], aggfunc="first").reindex(full_index)
    day_count = dates_index.size
    symbol_count = symbols_index.size
    bucket_count = buckets.size
    o = wide["open"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, bucket_count)
    h = wide["high"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, bucket_count)
    l = wide["low"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, bucket_count)
    c = wide["close"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, bucket_count)
    v = wide["volume"].reindex(columns=buckets).to_numpy(np.float32).reshape(day_count, symbol_count, bucket_count)
    prev_close = np.concatenate((np.full((1, symbol_count), np.nan, dtype=np.float32), c[:-1, :, -1].astype(np.float32)), axis=0)
    gap = safe_pct(o[:, :, 0].astype(np.float32), prev_close).astype(np.float32)
    dates_np = dates_index.to_numpy(dtype="datetime64[D]")

    price_valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0.0) & (h > 0.0) & (l > 0.0) & (c > 0.0)
    volume_valid = price_valid & np.isfinite(v) & (v >= 0.0)
    volume_clean = np.where(volume_valid, v, 0.0).astype(np.float32)
    cum_volume = np.cumsum(volume_clean, axis=2, dtype=np.float32)
    high_so_far = np.maximum.accumulate(np.where(price_valid, h, -np.inf).astype(np.float32), axis=2)

    log("Building 20-day relative-volume signal")
    feature_bucket = np.arange(FEATURE_BUCKET_START, FEATURE_BUCKET_END + 1, dtype=np.int32)
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
    bar_mean, bar_count = previous_n_day_mean(bar_volume, feature_valid)
    vol20_mean, vol20_count = previous_n_day_mean(vol20, feature_valid)
    cum_mean, cum_count = previous_n_day_mean(cum_vol, feature_valid)
    bar_rvol = finite_divide(bar_volume, bar_mean)
    vol20_rvol = finite_divide(vol20, vol20_mean)
    cum_rvol = finite_divide(cum_vol, cum_mean)
    volume_accel = finite_divide(bar_volume * 5.0, prev5_volume)
    close_t = c[:, :, feature_idx].astype(np.float32)
    mom15 = safe_pct(close_t, c[:, :, feature_idx - 15]).astype(np.float32)
    drop_from_day_high = safe_pct(high_so_far[:, :, feature_idx].astype(np.float32), close_t).astype(np.float32)
    gap_3d = np.broadcast_to(gap.reshape(day_count, symbol_count, 1), close_t.shape).astype(np.float32)
    trade_day_mask = (dates_np >= TRADE_START_DATE) & (dates_np <= TRADE_END_DATE)
    base_valid = (
        feature_valid
        & entry_open_valid
        & trade_day_mask.reshape(day_count, 1, 1)
        & (bar_count >= MIN_PRIOR_DAYS)
        & (vol20_count >= MIN_PRIOR_DAYS)
        & (cum_count >= MIN_PRIOR_DAYS)
    )
    signal = (
        base_valid
        & (mom15 <= MOM15_MAX)
        & (mom15 > MOM15_MIN)
        & (bar_rvol >= BAR_RVOL_MIN)
        & (vol20_rvol >= VOL20_RVOL_MIN)
        & (cum_rvol >= CUM_RVOL_MIN)
        & (volume_accel < VOLUME_ACCEL_MAX)
        & (drop_from_day_high >= DROP_FROM_HIGH_MIN)
        & (gap_3d >= GAP_UP_MIN)
    )

    day_idx, symbol_idx, local_idx = np.nonzero(signal)
    raw_signal_count = np.int32(day_idx.size)
    selected_entry_idx = feature_idx[local_idx] + 1
    signal_order = np.lexsort((selected_entry_idx, symbol_idx, day_idx))
    sorted_day_idx = day_idx[signal_order].astype(np.int32)
    sorted_symbol_idx = symbol_idx[signal_order].astype(np.int32)
    sorted_entry_idx = selected_entry_idx[signal_order].astype(np.int32)
    symbol_day_key = (sorted_day_idx.astype(np.int64) * np.int64(symbol_count) + sorted_symbol_idx.astype(np.int64)).astype(np.int64)
    first_symbol_day_idx = np.unique(symbol_day_key, return_index=True)[1].astype(np.int32)
    day_idx = sorted_day_idx[first_symbol_day_idx].astype(np.int32)
    symbol_idx = sorted_symbol_idx[first_symbol_day_idx].astype(np.int32)
    selected_entry_idx = sorted_entry_idx[first_symbol_day_idx].astype(np.int32)
    position_signal_count = np.int32(day_idx.size)
    log(f"Raw signals: {int(raw_signal_count):,} | first symbol/day positions: {int(position_signal_count):,}")
    path_offsets = np.arange(HOLD_MINUTES, dtype=np.int32)
    path_idx = selected_entry_idx.reshape(-1, 1) + path_offsets.reshape(1, -1)
    entry = o[day_idx, symbol_idx, selected_entry_idx].astype(np.float32)
    path_open = o[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_high = h[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_low = l[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_close = c[day_idx.reshape(-1, 1), symbol_idx.reshape(-1, 1), path_idx].astype(np.float32)
    path_valid = np.isfinite(entry) & np.isfinite(path_open).all(axis=1) & np.isfinite(path_high).all(axis=1) & np.isfinite(path_low).all(axis=1) & np.isfinite(path_close).all(axis=1)
    day_idx = day_idx[path_valid].astype(np.int32)
    symbol_idx = symbol_idx[path_valid].astype(np.int32)
    selected_entry_idx = selected_entry_idx[path_valid].astype(np.int32)
    selected_feature_idx = (selected_entry_idx - 1).astype(np.int32)
    selected_local_idx = (selected_entry_idx - np.int32(FEATURE_BUCKET_START)).astype(np.int32)
    entry = entry[path_valid].astype(np.float32)
    path_open = path_open[path_valid].astype(np.float32)
    path_high = path_high[path_valid].astype(np.float32)
    path_low = path_low[path_valid].astype(np.float32)
    path_close = path_close[path_valid].astype(np.float32)

    log("Simulating 3% SL / 3% target exits")
    stop_price = entry * (1.0 + STOP_PCT / 100.0)
    target_price = entry * (1.0 - TARGET_PCT / 100.0)
    stop_hit = path_high >= stop_price.reshape(-1, 1)
    target_hit = path_low <= target_price.reshape(-1, 1)
    stop_any = stop_hit.any(axis=1)
    target_any = target_hit.any(axis=1)
    stop_first_idx = np.where(stop_any, np.argmax(stop_hit, axis=1), HOLD_MINUTES + 1).astype(np.int16)
    target_first_idx = np.where(target_any, np.argmax(target_hit, axis=1), HOLD_MINUTES + 1).astype(np.int16)
    stop_first = stop_any & (stop_first_idx <= target_first_idx)
    target_first = target_any & (target_first_idx < stop_first_idx)
    timeout = ~(stop_first | target_first)
    exit_offset = np.where(stop_first, stop_first_idx, np.where(target_first, target_first_idx, HOLD_MINUTES - 1)).astype(np.int32)
    exit_open = path_open[np.arange(path_open.shape[0], dtype=np.int32), exit_offset].astype(np.float32)
    exit_close = path_close[np.arange(path_close.shape[0], dtype=np.int32), exit_offset].astype(np.float32)
    stop_gap = stop_first & (exit_open >= stop_price)
    exit_price = np.where(stop_gap, exit_open, np.where(stop_first, stop_price, np.where(target_first, target_price, exit_close))).astype(np.float32)
    exit_type = np.where(stop_gap, "SL_GAP", np.where(stop_first, "SL", np.where(target_first, "TARGET", "TIME"))).astype(object)
    exit_idx = (selected_entry_idx + exit_offset).astype(np.int32)
    gross = ((entry - exit_price) / entry * 100.0).astype(np.float32)
    net = (gross - ROUND_TRIP_COST_PCT).astype(np.float32)
    wins = net > 0.0

    day_net = np.bincount(day_idx, weights=net, minlength=day_count).astype(np.float32)
    day_counts = np.bincount(day_idx, minlength=day_count).astype(np.float32)
    day_wins = np.bincount(day_idx, weights=wins.astype(np.float32), minlength=day_count).astype(np.float32)
    summary = metric_summary(net, day_net, day_counts, target_first, stop_first, timeout, trade_day_mask)
    daily_frame = daily_summary(day_net, day_counts, day_wins, dates_np, trade_day_mask)
    tradebook = pd.DataFrame(
        {
            "date": dates_np[day_idx].astype(str),
            "symbol": symbols_index.to_numpy(dtype=object)[symbol_idx],
            "direction": np.full(day_idx.shape, "SHORT", dtype=object),
            "signal_bucket": (selected_feature_idx + 1).astype(np.int32),
            "signal_time": minute_labels(selected_feature_idx),
            "entry_bucket": (selected_entry_idx + 1).astype(np.int32),
            "entry_time": minute_labels(selected_entry_idx),
            "entry_price": entry,
            "exit_bucket": (exit_idx + 1).astype(np.int32),
            "exit_time": minute_labels(exit_idx),
            "exit_price": exit_price,
            "exit_type": exit_type,
            "hold_minutes": exit_offset.astype(np.int16),
            "gross_pct": gross,
            "net_pct": net,
            "gap_pct": gap[day_idx, symbol_idx].astype(np.float32),
            "bar_rvol": bar_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "vol20_rvol": vol20_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "cum_rvol": cum_rvol[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "volume_accel": volume_accel[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "mom15_pct": mom15[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
            "drop_from_day_high_pct": drop_from_day_high[day_idx, symbol_idx, selected_local_idx].astype(np.float32),
        }
    )
    tradebook = tradebook.sort_values(["date", "entry_bucket", "symbol"], kind="mergesort").reset_index(drop=True)

    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    daily_frame.to_csv(OUT_DIR / "daily_summary.csv", index=False)
    tradebook.to_csv(OUT_DIR / "tradebook.csv", index=False)
    plot_curve(daily_frame, OUT_DIR / "equity_curve.png")
    plot_daily_bars(daily_frame, OUT_DIR / "daily_net.png")
    baseline_diagnostics_section = write_diagnostics("", tradebook, daily_frame, OUT_DIR)
    live_section = ""
    if args.live_signals:
        live_date = str(args.live_date) if args.live_date else (str(dates_np[trade_day_mask].max()) if trade_day_mask.any() else "")
        live_signals = live_signal_sheet(tradebook, live_date, args.preset)
        live_signals.to_csv(OUT_DIR / "live_signals.csv", index=False)
        live_section = f"""
## Live Signals

- Signal date: `{live_date}`
- Rows: {live_signals.shape[0]:,}
- File: `live_signals.csv`

{markdown_table(live_signals, max_rows=50)}
"""

    pattern_results = pd.DataFrame()
    best_pattern_tradebook = pd.DataFrame()
    best_pattern_daily = pd.DataFrame()
    if args.search_grid:
        pattern_results, best_pattern_tradebook, best_pattern_daily = run_pattern_search(
            args,
            o,
            h,
            l,
            c,
            dates_np,
            symbols_index,
            symbol_count,
            day_count,
            feature_idx,
            base_valid,
            trade_day_mask,
            gap_3d,
            gap,
            bar_rvol,
            vol20_rvol,
            cum_rvol,
            volume_accel,
            mom15,
            drop_from_day_high,
        )
    best_pattern_diagnostics_section = ""
    if not best_pattern_tradebook.empty and not best_pattern_daily.empty:
        best_pattern_diagnostics_section = write_diagnostics("best_pattern", best_pattern_tradebook, best_pattern_daily, OUT_DIR)

    if not pattern_results.empty:
        top_pattern_columns = [
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
        pattern_section = f"""
## Fast Pattern Search

- Grid combos evaluated: {pattern_results.shape[0]:,}
- Minimum trades for best-pattern selection: {int(args.min_trades):,}
- Search holds: `{args.search_holds}` minutes.
- Search uses the same broad candidate arrays, then masks threshold combos in NumPy and keeps the first signal per symbol/day.
- Score emphasizes validation/OOS daily Sharpe and expectancy, with penalties for OOS sample weakness and drawdown.

### Top Patterns

{markdown_table(pattern_results[top_pattern_columns], max_rows=20)}

### Best Pattern Daily

{best_pattern_daily.to_string(index=False) if not best_pattern_daily.empty else "No best pattern daily rows."}

{best_pattern_diagnostics_section}
"""
    elif args.search_grid:
        pattern_section = """
## Fast Pattern Search

No pattern-search rows survived the configured candidate/min-trade filters.
"""
    else:
        pattern_section = """
## Fast Pattern Search

Not run. Add `--search-grid` to sweep the NumPy pattern grid.
"""

    report = f"""# Parquet 20-Day Volume-Spike Baseline 3/3

- Source parquet glob: `{DATA_DIR / PARQUET_GLOB}`
- Strategy preset: `{args.preset}`
- Trade dates: `{str(TRADE_START_DATE)}` to `{str(TRADE_END_DATE)}`
- `USE_ALL_PARQUET_SYMBOLS`: `{USE_ALL_PARQUET_SYMBOLS}`
- Symbol universe: `{universe_name}`
- Symbols after universe filter: {symbol_count:,}
- Loaded rows are clipped to dates <= trade end before pivoting, so future-month files cannot affect the signal.
- Gap is recomputed from bucket-1 open versus previous trading day's bucket-375 close.
- Position fix: first valid signal per symbol/day only; repeated same-symbol signals are not counted as separate deployable trades.
- Raw signal rows before position fix: {int(raw_signal_count):,}
- Position rows after first-symbol/day fix: {int(position_signal_count):,}
- Relative volume lookback: {int(LOOKBACK_DAYS)} prior trading days, min {int(MIN_PRIOR_DAYS)} samples.
- Requested signal window: bucket {int(FEATURE_BUCKET_START)} to {int(FEATURE_BUCKET_END_REQUESTED)}.
- Effective signal window: bucket {int(FEATURE_BUCKET_START)} to {int(FEATURE_BUCKET_END)}, entry next 1-minute open.
- Latest planned exit bucket: {int(LATEST_EXIT_BUCKET)} (`15:15`).
- Hold-time tuning: change `HOLD_MINUTES` to any positive integer; the latest safe signal bucket is clipped automatically so planned exits stay at or before 15:15.
- Direction: short.
- Baseline filters: 1m rvol >= {float(BAR_RVOL_MIN):.1f}, 20m rvol >= {float(VOL20_RVOL_MIN):.1f}, cumulative day rvol >= {float(CUM_RVOL_MIN):.1f}, gap >= {float(GAP_UP_MIN):.1f}%, volume acceleration < {float(VOLUME_ACCEL_MAX):.1f}, 15m momentum in ({float(MOM15_MIN):.1f}%, {float(MOM15_MAX):.1f}%], drop from day high >= {float(DROP_FROM_HIGH_MIN):.1f}%.
- Exit: {float(STOP_PCT):.1f}% stop / {float(TARGET_PCT):.1f}% target / {int(HOLD_MINUTES)}m time exit.
- Same-candle ambiguity: stop first.
- SL gap-through: exit at candle open when open is beyond stop.
- Round-trip cost: {float(ROUND_TRIP_COST_PCT):.2f}%.
- Tradebook rows: {tradebook.shape[0]:,}

## Summary

{summary.to_string(index=False)}

## Daily

{daily_frame.to_string(index=False)}

{baseline_diagnostics_section}

{live_section}

{pattern_section}

## Files

- `summary.csv`
- `daily_summary.csv`
- `tradebook.csv`
- `equity_curve.csv`
- `drawdown_details.csv`
- `monthly_analysis.csv`
- `quarterly_analysis.csv`
- `top_10_losing_weeks.csv`
- `t_test_summary.csv`
- `cost_sensitivity.csv`
- `robustness_summary.csv`
- `live_signals.csv` when `--live-signals` is used.
- `equity_curve.png`
- `daily_net.png`
- `pattern_grid.csv` when `--search-grid` is used.
- `best_pattern_tradebook.csv` when `--search-grid` is used.
- `best_pattern_daily_summary.csv` when `--search-grid` is used.
- `best_pattern_*analysis.csv` diagnostics when `--search-grid` finds a best pattern.
- `charts/pattern_top_scores.png` when `--search-grid` is used.
- `charts/pattern_validation_vs_oos.png` when `--search-grid` is used.
"""
    (OUT_DIR / "report.md").write_text(report, encoding="utf-8")
    log(f"Wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
