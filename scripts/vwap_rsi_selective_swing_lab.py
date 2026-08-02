from __future__ import annotations

import argparse
import itertools
import math
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import vwap_rsi_trend_lab as base


sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / "parquets" / "cache" / "vwap_rsi_5m_v1"
DEFAULT_OUT_DIR = ROOT / "docs" / "vwap_rsi_selective_swing_lab"
T0 = time.perf_counter()


@dataclass(frozen=True)
class FilterSpec:
    window_start: str
    window_end: str
    direction: str
    vwap_slope_bars: int
    min_vwap_slope_pct: float
    rsi_slope_bars: int
    min_rsi_slope: float
    min_session_move_pct: float
    breakout_lookback: int
    min_breakout_pct: float
    max_signal_range_pct: float
    max_body_pct: float
    max_trades_per_day: int

    @property
    def name(self) -> str:
        return (
            f"{self.window_start}-{self.window_end}_{self.direction}"
            f"_vs{self.vwap_slope_bars}_{self.min_vwap_slope_pct:g}"
            f"_rs{self.rsi_slope_bars}_{self.min_rsi_slope:g}"
            f"_sm{self.min_session_move_pct:g}"
            f"_bo{self.breakout_lookback}_{self.min_breakout_pct:g}"
            f"_rng{self.max_signal_range_pct:g}_body{self.max_body_pct:g}"
            f"_top{self.max_trades_per_day}"
        )


@dataclass(frozen=True)
class ExitSpec:
    max_hold_bars: int
    failure_exit: str
    min_failure_hold_bars: int

    @property
    def name(self) -> str:
        hold = "eod" if self.max_hold_bars <= 0 else f"hold{self.max_hold_bars}"
        fail = self.failure_exit
        if fail == "none":
            return hold
        return f"{hold}_{fail}_fail{self.min_failure_hold_bars}"


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def parse_windows(raw: str) -> list[tuple[str, str]]:
    windows: list[tuple[str, str]] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError(f"Invalid window {part!r}. Use HH:MM-HH:MM.")
        start, end = [piece.strip() for piece in part.split("-", 1)]
        windows.append((start, end))
    if not windows:
        raise ValueError("At least one entry window is required.")
    return windows


def make_filter_specs(args: argparse.Namespace) -> list[FilterSpec]:
    allowed_directions = {"both", "long", "short"}
    directions = base.parse_csv_strings(args.directions)
    unknown = sorted(set(directions) - allowed_directions)
    if unknown:
        raise ValueError(f"Unsupported directions: {unknown}. Allowed: both,long,short")
    return [
        FilterSpec(
            window_start=start,
            window_end=end,
            direction=direction,
            vwap_slope_bars=int(vwap_slope_bars),
            min_vwap_slope_pct=float(min_vwap_slope_pct),
            rsi_slope_bars=int(rsi_slope_bars),
            min_rsi_slope=float(min_rsi_slope),
            min_session_move_pct=float(min_session_move_pct),
            breakout_lookback=int(breakout_lookback),
            min_breakout_pct=float(min_breakout_pct),
            max_signal_range_pct=float(max_signal_range_pct),
            max_body_pct=float(max_body_pct),
            max_trades_per_day=int(max_trades_per_day),
        )
        for (start, end),
        direction,
        vwap_slope_bars,
        min_vwap_slope_pct,
        rsi_slope_bars,
        min_rsi_slope,
        min_session_move_pct,
        breakout_lookback,
        min_breakout_pct,
        max_signal_range_pct,
        max_body_pct,
        max_trades_per_day in itertools.product(
            parse_windows(args.entry_windows),
            directions,
            base.parse_csv_ints(args.vwap_slope_bars),
            base.parse_csv_floats(args.min_vwap_slope_pcts),
            base.parse_csv_ints(args.rsi_slope_bars),
            base.parse_csv_floats(args.min_rsi_slope_values),
            base.parse_csv_floats(args.min_session_move_pcts),
            base.parse_csv_ints(args.breakout_lookbacks),
            base.parse_csv_floats(args.min_breakout_pcts),
            base.parse_csv_floats(args.max_signal_range_pcts),
            base.parse_csv_floats(args.max_body_pcts),
            base.parse_csv_ints(args.max_trades_per_day_values),
        )
    ]


def make_exit_specs(args: argparse.Namespace) -> list[ExitSpec]:
    allowed = {"none", "vwap", "rsi", "vwap_rsi"}
    failure_exits = base.parse_csv_strings(args.failure_exits)
    unknown = sorted(set(failure_exits) - allowed)
    if unknown:
        raise ValueError(f"Unsupported failure exits: {unknown}. Allowed: none,vwap,rsi,vwap_rsi")
    return [
        ExitSpec(
            max_hold_bars=int(max_hold_bars),
            failure_exit=failure_exit,
            min_failure_hold_bars=int(args.min_failure_hold_bars),
        )
        for max_hold_bars, failure_exit in itertools.product(
            base.parse_csv_ints(args.max_hold_bars_values),
            failure_exits,
        )
    ]


def rolling_prior_extreme(values: np.ndarray, valid: np.ndarray, lookback: int, mode: str) -> np.ndarray:
    out = np.full_like(values, np.nan, dtype=np.float32)
    if lookback <= 0:
        return out
    safe = np.where(valid, values, np.nan).astype(np.float32)
    for bar in range(values.shape[2]):
        start = max(0, bar - int(lookback))
        if start >= bar:
            continue
        chunk = safe[:, :, start:bar]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            if mode == "high":
                out[:, :, bar] = np.nanmax(chunk, axis=2)
            else:
                out[:, :, bar] = np.nanmin(chunk, axis=2)
    return out


def select_top_by_day(day: np.ndarray, score: np.ndarray, top_n: int) -> np.ndarray:
    if day.size == 0 or top_n <= 0:
        return np.arange(day.size, dtype=np.int32)
    order = np.lexsort((-score.astype(np.float64), day.astype(np.int64)))
    sorted_day = day[order]
    keep_parts: list[np.ndarray] = []
    starts = np.r_[0, np.flatnonzero(sorted_day[1:] != sorted_day[:-1]) + 1]
    ends = np.r_[starts[1:], sorted_day.size]
    for start, end in zip(starts, ends):
        keep_parts.append(order[start : min(end, start + int(top_n))])
    if not keep_parts:
        return np.array([], dtype=np.int32)
    return np.sort(np.concatenate(keep_parts).astype(np.int32))


def base_candidate_features(cache: base.CacheBundle, signal_spec: base.SignalSpec, args: argparse.Namespace) -> dict[str, np.ndarray]:
    candidates = base.candidate_rows(cache, signal_spec, args)
    day = candidates["day"]
    if day.size == 0:
        return {**candidates}

    symbol = candidates["symbol"]
    sig = candidates["signal_idx"]
    direction = candidates["direction"]
    o = cache["open"]
    h = cache["high"]
    l = cache["low"]
    c = cache["close"]
    vwap = cache["vwap"]
    rsi = cache["rsi14"]
    relvol = cache["relvol20"]

    signal_open = o[day, symbol, sig].astype(np.float32)
    signal_high = h[day, symbol, sig].astype(np.float32)
    signal_low = l[day, symbol, sig].astype(np.float32)
    signal_close = c[day, symbol, sig].astype(np.float32)
    signal_vwap = vwap[day, symbol, sig].astype(np.float32)
    signal_rsi = rsi[day, symbol, sig].astype(np.float32)
    signal_relvol = relvol[day, symbol, sig].astype(np.float32)
    session_open = o[day, symbol, 0].astype(np.float32)

    body_pct = (np.abs(signal_close - signal_open) / signal_close * 100.0).astype(np.float32)
    range_pct = ((signal_high - signal_low) / signal_close * 100.0).astype(np.float32)
    vwap_dist_pct = (np.abs(signal_close / signal_vwap - 1.0) * 100.0).astype(np.float32)
    session_move_pct = (direction * ((signal_close / session_open - 1.0) * 100.0)).astype(np.float32)

    return {
        **candidates,
        "signal_open": signal_open,
        "signal_high": signal_high,
        "signal_low": signal_low,
        "signal_close": signal_close,
        "signal_vwap": signal_vwap,
        "signal_rsi": signal_rsi,
        "signal_relvol20": signal_relvol,
        "body_pct": body_pct,
        "range_pct": range_pct,
        "vwap_dist_pct": vwap_dist_pct,
        "session_move_pct": session_move_pct,
    }


def apply_filter_spec(
    cache: base.CacheBundle,
    features: dict[str, np.ndarray],
    filter_spec: FilterSpec,
    prior_levels: dict[int, tuple[np.ndarray, np.ndarray]],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    day = features["day"].astype(np.int32)
    if day.size == 0:
        empty = {key: value for key, value in features.items() if key in {"day", "symbol", "signal_idx", "entry_idx", "direction"}}
        return empty, {}

    symbol = features["symbol"].astype(np.int32)
    sig = features["signal_idx"].astype(np.int32)
    direction = features["direction"].astype(np.int8)
    start_bar = base.parse_time_to_bar(filter_spec.window_start, 0)
    end_bar = base.parse_time_to_bar(filter_spec.window_end, base.BAR_COUNT - 2)

    vwap_prev_idx = np.maximum(sig - int(filter_spec.vwap_slope_bars), 0).astype(np.int32)
    rsi_prev_idx = np.maximum(sig - int(filter_spec.rsi_slope_bars), 0).astype(np.int32)
    vwap_now = cache["vwap"][day, symbol, sig].astype(np.float32)
    vwap_prev = cache["vwap"][day, symbol, vwap_prev_idx].astype(np.float32)
    rsi_now = cache["rsi14"][day, symbol, sig].astype(np.float32)
    rsi_prev = cache["rsi14"][day, symbol, rsi_prev_idx].astype(np.float32)
    vwap_slope_pct = (direction * ((vwap_now / vwap_prev - 1.0) * 100.0)).astype(np.float32)
    rsi_slope = (direction * (rsi_now - rsi_prev)).astype(np.float32)

    if filter_spec.breakout_lookback > 0:
        prior_high, prior_low = prior_levels[int(filter_spec.breakout_lookback)]
        ph = prior_high[day, symbol, sig].astype(np.float32)
        pl = prior_low[day, symbol, sig].astype(np.float32)
        long_break = (features["signal_close"] / ph - 1.0) * 100.0
        short_break = (pl / features["signal_close"] - 1.0) * 100.0
        breakout_pct = np.where(direction > 0, long_break, short_break).astype(np.float32)
    else:
        breakout_pct = np.zeros(day.size, dtype=np.float32)

    mask = (
        (sig >= start_bar)
        & (sig <= end_bar)
        & np.isfinite(vwap_slope_pct)
        & np.isfinite(rsi_slope)
        & np.isfinite(features["signal_relvol20"])
        & np.isfinite(features["body_pct"])
        & np.isfinite(features["range_pct"])
        & np.isfinite(features["session_move_pct"])
        & np.isfinite(breakout_pct)
        & (sig >= int(filter_spec.vwap_slope_bars))
        & (sig >= int(filter_spec.rsi_slope_bars))
        & (vwap_slope_pct >= np.float32(filter_spec.min_vwap_slope_pct))
        & (rsi_slope >= np.float32(filter_spec.min_rsi_slope))
        & (features["session_move_pct"] >= np.float32(filter_spec.min_session_move_pct))
        & (features["range_pct"] <= np.float32(filter_spec.max_signal_range_pct))
        & (features["body_pct"] <= np.float32(filter_spec.max_body_pct))
        & (breakout_pct >= np.float32(filter_spec.min_breakout_pct))
    )
    if filter_spec.direction == "long":
        mask &= direction > 0
    elif filter_spec.direction == "short":
        mask &= direction < 0

    idx = np.flatnonzero(mask).astype(np.int32)
    if idx.size == 0:
        empty_i = np.array([], dtype=np.int32)
        return {
            "day": empty_i,
            "symbol": empty_i,
            "signal_idx": empty_i,
            "entry_idx": empty_i,
            "direction": empty_i.astype(np.int8),
        }, {}

    signal_score = (
        features["signal_relvol20"][idx] * 0.5
        + np.maximum(vwap_slope_pct[idx], 0.0) * 8.0
        + np.maximum(features["session_move_pct"][idx], 0.0) * 1.5
        + np.maximum(rsi_slope[idx], 0.0) * 0.08
        + np.maximum(breakout_pct[idx], 0.0) * 3.0
        - features["range_pct"][idx] * 0.15
    ).astype(np.float32)
    top_idx = select_top_by_day(day[idx], signal_score, int(filter_spec.max_trades_per_day))
    selected = idx[top_idx]

    candidates = {
        "day": day[selected].astype(np.int32),
        "symbol": symbol[selected].astype(np.int32),
        "signal_idx": sig[selected].astype(np.int32),
        "entry_idx": features["entry_idx"][selected].astype(np.int32),
        "direction": direction[selected].astype(np.int8),
    }
    selected_features = {
        "signal_score": signal_score[top_idx].astype(np.float32),
        "vwap_slope_pct": vwap_slope_pct[selected].astype(np.float32),
        "rsi_slope": rsi_slope[selected].astype(np.float32),
        "breakout_pct": breakout_pct[selected].astype(np.float32),
        "session_move_pct": features["session_move_pct"][selected].astype(np.float32),
        "range_pct": features["range_pct"][selected].astype(np.float32),
        "body_pct": features["body_pct"][selected].astype(np.float32),
        "signal_relvol20": features["signal_relvol20"][selected].astype(np.float32),
        "vwap_dist_pct": features["vwap_dist_pct"][selected].astype(np.float32),
    }
    return candidates, selected_features


def simulate_exits_selective(
    cache: base.CacheBundle,
    candidates: dict[str, np.ndarray],
    stop: np.ndarray,
    risk_pct: np.ndarray,
    rr: float,
    exit_spec: ExitSpec,
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
        return base.empty_sim(return_trades)

    day = day[risk_ok]
    symbol = symbol[risk_ok]
    signal_idx = signal_idx[risk_ok]
    entry_idx = entry_idx[risk_ok]
    direction = direction[risk_ok]
    entry = entry[risk_ok]
    stop = stop[risk_ok].astype(np.float32)
    risk_pct = risk_pct[risk_ok].astype(np.float32)

    high = cache["high"]
    low = cache["low"]
    open_ = cache["open"]
    close = cache["close"]
    vwap = cache["vwap"]
    rsi = cache["rsi14"]
    valid = cache["valid"].astype(bool)
    offsets = np.arange(base.BAR_COUNT, dtype=np.int32)
    cost = np.float32(args.round_trip_cost_pct)
    no_hit_value = base.BAR_COUNT + 1

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

    for start in range(0, day.size, int(args.sim_chunk_size)):
        end = min(day.size, start + int(args.sim_chunk_size))
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
        path_valid_idx = path_idx < base.BAR_COUNT
        clipped = np.minimum(path_idx, base.BAR_COUNT - 1)
        ph = high[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pl = low[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        po = open_[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pc = close[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pvwap = vwap[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        prsi = rsi[d.reshape(-1, 1), s.reshape(-1, 1), clipped].astype(np.float32)
        pv = valid[d.reshape(-1, 1), s.reshape(-1, 1), clipped] & path_valid_idx

        long_rows = direc > 0
        stop_hit = np.where(long_rows.reshape(-1, 1), pl <= stp.reshape(-1, 1), ph >= stp.reshape(-1, 1)) & pv
        target_hit = np.where(long_rows.reshape(-1, 1), ph >= tgt.reshape(-1, 1), pl <= tgt.reshape(-1, 1)) & pv
        any_stop = stop_hit.any(axis=1)
        any_target = target_hit.any(axis=1)
        first_stop = np.where(any_stop, np.argmax(stop_hit, axis=1), no_hit_value).astype(np.int32)
        first_target = np.where(any_target, np.argmax(target_hit, axis=1), no_hit_value).astype(np.int32)
        stop_first = any_stop & (first_stop <= first_target)
        target_first = any_target & (first_target < first_stop)
        any_valid = pv.any(axis=1)
        last_valid = np.where(any_valid, pv.shape[1] - 1 - np.argmax(pv[:, ::-1], axis=1), 0).astype(np.int32)

        exit_offset = np.where(stop_first, first_stop, np.where(target_first, first_target, last_valid)).astype(np.int32)
        exit_type = np.where(stop_first, 2, np.where(target_first, 1, 0)).astype(np.int8)

        if exit_spec.max_hold_bars > 0:
            hold_offset = np.minimum(np.int32(exit_spec.max_hold_bars - 1), last_valid).astype(np.int32)
            use_hold = any_valid & ((hold_offset < exit_offset) | ((exit_type == 0) & (hold_offset == exit_offset)))
            exit_offset = np.where(use_hold, hold_offset, exit_offset).astype(np.int32)
            exit_type = np.where(use_hold, 4, exit_type).astype(np.int8)

        if exit_spec.failure_exit != "none":
            min_fail = max(0, int(exit_spec.min_failure_hold_bars))
            hold_mask = offsets.reshape(1, -1) >= min_fail
            vwap_fail = np.where(long_rows.reshape(-1, 1), pc < pvwap, pc > pvwap)
            rsi_fail = np.where(long_rows.reshape(-1, 1), prsi < 50.0, prsi > 50.0)
            if exit_spec.failure_exit == "vwap":
                fail_signal = vwap_fail
            elif exit_spec.failure_exit == "rsi":
                fail_signal = rsi_fail
            else:
                fail_signal = vwap_fail | rsi_fail
            fail_signal = fail_signal & pv & hold_mask & np.isfinite(pc) & np.isfinite(pvwap) & np.isfinite(prsi)
            any_fail = fail_signal.any(axis=1)
            first_fail_signal = np.where(any_fail, np.argmax(fail_signal, axis=1), no_hit_value).astype(np.int32)
            fail_exit_offset = (first_fail_signal + 1).astype(np.int32)
            fail_valid = any_fail & (fail_exit_offset <= last_valid)
            use_fail = fail_valid & (
                (fail_exit_offset < exit_offset)
                | ((exit_type == 0) & (fail_exit_offset == exit_offset))
                | ((exit_type == 4) & (fail_exit_offset <= exit_offset))
            )
            exit_offset = np.where(use_fail, fail_exit_offset, exit_offset).astype(np.int32)
            exit_type = np.where(use_fail, 5, exit_type).astype(np.int8)

        row_idx = np.arange(d.size, dtype=np.int32)
        exit_open = po[row_idx, exit_offset].astype(np.float32)
        exit_close = pc[row_idx, exit_offset].astype(np.float32)
        stop_exit = np.isin(exit_type, [2, 3])
        target_exit = exit_type == 1

        long_stop_gap = long_rows & stop_exit & (exit_open <= stp)
        short_stop_gap = (~long_rows) & stop_exit & (exit_open >= stp)
        long_target_gap = long_rows & target_exit & (exit_open >= tgt)
        short_target_gap = (~long_rows) & target_exit & (exit_open <= tgt)
        stop_gap = long_stop_gap | short_stop_gap
        target_gap = long_target_gap | short_target_gap
        exit_type = np.where(stop_gap, 3, exit_type).astype(np.int8)
        exit_price = np.where(
            np.isin(exit_type, [2, 3]),
            np.where(stop_gap, exit_open, stp),
            np.where(
                target_exit,
                np.where(target_gap, exit_open, tgt),
                np.where(exit_type == 5, exit_open, exit_close),
            ),
        ).astype(np.float32)
        gross = np.where(long_rows, (exit_price - ent) / ent * 100.0, (ent - exit_price) / ent * 100.0).astype(np.float32)
        net = (gross - cost).astype(np.float32)
        r_mult = (net / risk).astype(np.float32)
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
        return base.empty_sim(return_trades)

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


def trade_frame_selective(
    cache: base.CacheBundle,
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
    exit_reason = np.array(["EOD", "TARGET", "SL", "SL_GAP", "TIME", "FAILURE"], dtype=object)
    frame = pd.DataFrame(
        {
            **config,
            "date": cache.dates[day].astype(str),
            "symbol": cache.symbols[symbol].astype(str),
            "direction": np.where(sim["direction"][order] > 0, "LONG", "SHORT"),
            "signal_time": base.bar_label(sim["signal_idx"][order]),
            "entry_time": base.bar_label(sim["entry_idx"][order]),
            "exit_time": base.bar_label(sim["exit_idx"][order]),
            "entry_price": sim["entry_price"][order],
            "stop_price": sim["stop_price"][order],
            "target_price": sim["target_price"][order],
            "exit_price": sim["exit_price"][order],
            "exit_reason": exit_reason[np.clip(sim["exit_type"][order], 0, exit_reason.size - 1)],
            "risk_pct": sim["risk_pct"][order],
            "gross_pct": sim["gross_pct"][order],
            "net_pct": sim["net_pct"][order],
            "r_multiple": sim["r_multiple"][order],
            "hold_minutes": sim["hold_bars"][order].astype(np.int32) * base.BAR_MINUTES,
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


def plot_equity(daily: pd.DataFrame, out_path: Path, title: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(pd.to_datetime(daily["date"]), daily["cum_net_pct"], label="cum net")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_title(title)
    ax.set_ylabel("cumulative net, summed trade %")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def daily_curve(cache: base.CacheBundle, sim: dict[str, np.ndarray], full_mask: np.ndarray) -> pd.DataFrame:
    day_net = np.bincount(sim["day"], weights=sim["net_pct"], minlength=cache.dates.shape[0]).astype(np.float32)
    counts = np.bincount(sim["day"], minlength=cache.dates.shape[0]).astype(np.int32)
    wins = np.bincount(sim["day"], weights=(sim["net_pct"] > 0.0).astype(np.float32), minlength=cache.dates.shape[0]).astype(np.float32)
    dates = cache.dates[full_mask].astype(str)
    values = day_net[full_mask]
    count_values = counts[full_mask]
    win_values = wins[full_mask]
    return pd.DataFrame(
        {
            "date": dates,
            "trades": count_values,
            "wins": win_values.astype(np.int32),
            "win_pct": np.divide(win_values, count_values, out=np.zeros_like(win_values, dtype=np.float32), where=count_values > 0) * 100.0,
            "day_net_pct": values,
            "cum_net_pct": np.cumsum(values, dtype=np.float32),
        }
    )


def add_feature_stats(row: dict[str, Any], feats: dict[str, np.ndarray]) -> dict[str, Any]:
    for key, values in feats.items():
        if values.size:
            row[f"avg_{key}"] = float(np.nanmean(values))
    return row


def write_report(out_dir: Path, summary: pd.DataFrame, split_metrics: pd.DataFrame, args: argparse.Namespace) -> None:
    top_cols = [
        "config_id",
        "signal_name",
        "filter_name",
        "trigger",
        "direction_filter",
        "entry_window",
        "max_trades_per_day",
        "stop_name",
        "exit_name",
        "rr",
        "full_trades",
        "full_trades_per_day",
        "full_avg_net_pct",
        "full_win_pct",
        "full_daily_sharpe",
        "full_max_drawdown_sum_pct",
        "validation_avg_net_pct",
        "out_of_sample_avg_net_pct",
        "out_of_sample_trades",
        "out_of_sample_daily_sharpe",
        "out_of_sample_max_drawdown_sum_pct",
        "score",
    ]
    lines = [
        "# Selective VWAP + RSI Swing Lab",
        "",
        f"Cache: `{args.cache_dir}`",
        f"Trade dates: `{args.start_date or 'cache-start'}` to `{args.end_date or 'cache-end'}`",
        f"Round-trip cost: `{args.round_trip_cost_pct:g}%`",
        "",
        "## Why This Lab Exists",
        "",
        "The first broad PDF translation over-traded the rule. This version keeps the same no-lookahead VWAP/RSI idea but adds filters that are stated or implied in the PDF: trending market, high volume, breakout/pullback quality, no oversized chase candle, and only the cleanest few signals per day.",
        "",
        "## Top Results",
        "",
        base.markdown_table(summary[[col for col in top_cols if col in summary.columns]].head(25), max_rows=25) if not summary.empty else "No configs passed filters.",
        "",
        "## Walk-Forward Splits",
        "",
        base.markdown_table(split_metrics.head(60), max_rows=60) if not split_metrics.empty else "No split rows.",
    ]
    (out_dir / "final_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    cache = base.load_cache(Path(args.cache_dir), mmap=True)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    full_mask = base.date_mask(cache.dates, args.start_date, args.end_date)
    signal_specs = base.make_signal_specs(args)
    filter_specs = make_filter_specs(args)
    stop_specs = base.make_stop_specs(args)
    exit_specs = make_exit_specs(args)
    rr_values = base.parse_csv_floats(args.rr_values)
    swing_lookbacks = sorted({int(spec.value) for spec in stop_specs if spec.mode == "swing"})
    breakout_lookbacks = sorted({spec.breakout_lookback for spec in filter_specs if spec.breakout_lookback > 0})
    log(f"Loaded cache: {cache.dates.shape[0]:,} dates x {cache.symbols.shape[0]:,} symbols")
    log(
        f"Grid: {len(signal_specs):,} signals x {len(filter_specs):,} filters x "
        f"{len(stop_specs):,} stops x {len(exit_specs):,} exits x {len(rr_values):,} RR"
    )

    valid = cache["valid"].astype(bool)
    swing_levels = base.rolling_swing_levels(cache["low"], cache["high"], valid, swing_lookbacks) if swing_lookbacks else {}
    prior_levels = {
        lookback: (
            rolling_prior_extreme(cache["high"], valid, lookback, "high"),
            rolling_prior_extreme(cache["low"], valid, lookback, "low"),
        )
        for lookback in breakout_lookbacks
    }

    summary_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    config_index: dict[int, tuple[base.SignalSpec, FilterSpec, base.StopSpec, ExitSpec, float]] = {}
    best_payload: tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]] | None = None
    best_score = -np.inf
    config_id = 0

    for sig_num, signal_spec in enumerate(signal_specs, start=1):
        log(f"Signal {sig_num:,}/{len(signal_specs):,}: {signal_spec.name}")
        features = base_candidate_features(cache, signal_spec, args)
        log(f"  raw candidates: {features.get('day', np.array([], dtype=np.int32)).size:,}")
        for filt_num, filter_spec in enumerate(filter_specs, start=1):
            candidates, selected_features = apply_filter_spec(cache, features, filter_spec, prior_levels)
            if candidates["day"].size < int(args.min_trades):
                continue
            if filt_num == 1 or filt_num % 100 == 0:
                log(f"  filter {filt_num:,}/{len(filter_specs):,}: {candidates['day'].size:,} selected")
            for stop_spec in stop_specs:
                stop, risk_pct = base.compute_stop_and_risk(cache, candidates, stop_spec, swing_levels)
                for exit_spec in exit_specs:
                    for rr in rr_values:
                        config_id += 1
                        sim = simulate_exits_selective(cache, candidates, stop, risk_pct, rr, exit_spec, args, return_trades=False)
                        if sim["day"].size < int(args.min_trades):
                            continue
                        metrics, splits, yearly = base.summarize_config(sim, cache.dates, full_mask, int(args.min_trades))
                        config = {
                            "config_id": config_id,
                            "signal_name": signal_spec.name,
                            "filter_name": filter_spec.name,
                            "trigger": signal_spec.trigger,
                            "rsi_edge": signal_spec.rsi_edge,
                            "confirm_pct": signal_spec.confirm_pct,
                            "max_vwap_dist_pct": signal_spec.max_vwap_dist_pct,
                            "rvol_min": signal_spec.rvol_min,
                            "entry_window": f"{filter_spec.window_start}-{filter_spec.window_end}",
                            "direction_filter": filter_spec.direction,
                            "vwap_slope_bars": filter_spec.vwap_slope_bars,
                            "min_vwap_slope_pct": filter_spec.min_vwap_slope_pct,
                            "rsi_slope_bars": filter_spec.rsi_slope_bars,
                            "min_rsi_slope": filter_spec.min_rsi_slope,
                            "min_session_move_pct": filter_spec.min_session_move_pct,
                            "breakout_lookback": filter_spec.breakout_lookback,
                            "min_breakout_pct": filter_spec.min_breakout_pct,
                            "max_signal_range_pct": filter_spec.max_signal_range_pct,
                            "max_body_pct": filter_spec.max_body_pct,
                            "max_trades_per_day": filter_spec.max_trades_per_day,
                            "stop_name": stop_spec.name,
                            "stop_mode": stop_spec.mode,
                            "stop_value": stop_spec.value,
                            "exit_name": exit_spec.name,
                            "max_hold_bars": exit_spec.max_hold_bars,
                            "failure_exit": exit_spec.failure_exit,
                            "min_failure_hold_bars": exit_spec.min_failure_hold_bars,
                            "rr": float(rr),
                        }
                        row = base.attach_config(metrics, config)
                        row = add_feature_stats(row, selected_features)
                        summary_rows.append(row)
                        split_rows.extend(base.attach_config({"split": item.pop("split"), **item}, config) for item in splits)
                        yearly_rows.extend(base.attach_config({"year": int(item.pop("year")), **item}, config) for item in yearly)
                        config_index[config_id] = (signal_spec, filter_spec, stop_spec, exit_spec, float(rr))
                        if float(metrics["score"]) > best_score:
                            best_score = float(metrics["score"])
                            best_payload = (candidates, selected_features, config)

    summary = pd.DataFrame(summary_rows)
    split_metrics = pd.DataFrame(split_rows)
    yearly_metrics = pd.DataFrame(yearly_rows)
    if summary.empty:
        summary.to_csv(out_dir / "summary.csv", index=False)
        split_metrics.to_csv(out_dir / "split_metrics.csv", index=False)
        yearly_metrics.to_csv(out_dir / "yearly_metrics.csv", index=False)
        write_report(out_dir, summary, split_metrics, args)
        log("No configs reached --min-trades.")
        return

    summary = summary.sort_values(["score", "out_of_sample_avg_net_pct", "validation_avg_net_pct"], ascending=False).reset_index(drop=True)
    split_metrics = split_metrics.sort_values(["config_id", "split"]).reset_index(drop=True)
    yearly_metrics = yearly_metrics.sort_values(["config_id", "year"]).reset_index(drop=True)
    for frame in [summary, split_metrics, yearly_metrics]:
        fcols = frame.select_dtypes(include=["float32", "float64"]).columns
        frame[fcols] = frame[fcols].round(5)
    summary.to_csv(out_dir / "summary.csv", index=False)
    split_metrics.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly_metrics.to_csv(out_dir / "yearly_metrics.csv", index=False)

    best_config_id = int(summary.iloc[0]["config_id"])
    signal_spec, filter_spec, stop_spec, exit_spec, rr = config_index[best_config_id]
    features = base_candidate_features(cache, signal_spec, args)
    candidates, selected_features = apply_filter_spec(cache, features, filter_spec, prior_levels)
    stop, risk_pct = base.compute_stop_and_risk(cache, candidates, stop_spec, swing_levels)
    best_sim = simulate_exits_selective(cache, candidates, stop, risk_pct, rr, exit_spec, args, return_trades=True)
    best_tradebook = trade_frame_selective(cache, best_sim, summary.iloc[0].to_dict(), int(args.top_trade_limit))
    best_tradebook.to_csv(out_dir / "best_tradebook.csv", index=False)
    daily = daily_curve(cache, best_sim, full_mask)
    daily.to_csv(out_dir / "best_daily_curve.csv", index=False)
    plot_equity(daily, out_dir / "best_equity_curve.png", f"Selective VWAP+RSI best config {best_config_id}")
    write_report(out_dir, summary, split_metrics, args)
    log("Best rows")
    print(summary.head(15).to_string(index=False), flush=True)
    log(f"Wrote outputs: {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Selective no-lookahead VWAP + RSI swing backtest using the 5-minute cache.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--triggers", default="cross,pullback")
    parser.add_argument("--rsi-edges", default="5,10")
    parser.add_argument("--confirm-pcts", default="0")
    parser.add_argument("--max-vwap-dist-pcts", default="1.5")
    parser.add_argument("--rvol-mins", default="1.5,3")
    parser.add_argument("--pullback-touch-pct", type=float, default=0.10)
    parser.add_argument("--require-directional", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--one-trade-per-symbol-day", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--entry-start", default="09:20")
    parser.add_argument("--entry-end", default="14:55")
    parser.add_argument("--entry-windows", default="09:20-11:00,13:30-14:30")
    parser.add_argument("--directions", default="both,long,short")
    parser.add_argument("--vwap-slope-bars", default="3,6")
    parser.add_argument("--min-vwap-slope-pcts", default="0.05,0.1")
    parser.add_argument("--rsi-slope-bars", default="3")
    parser.add_argument("--min-rsi-slope-values", default="0,2")
    parser.add_argument("--min-session-move-pcts", default="0.5")
    parser.add_argument("--breakout-lookbacks", default="6")
    parser.add_argument("--min-breakout-pcts", default="0")
    parser.add_argument("--max-signal-range-pcts", default="1.25")
    parser.add_argument("--max-body-pcts", default="1")
    parser.add_argument("--max-trades-per-day-values", default="1,3,5")
    parser.add_argument("--stop-modes", default="swing")
    parser.add_argument("--swing-lookbacks", default="5,8")
    parser.add_argument("--fixed-stop-pcts", default="0.5,0.75,1")
    parser.add_argument("--max-hold-bars-values", default="0")
    parser.add_argument("--failure-exits", default="none")
    parser.add_argument("--min-failure-hold-bars", type=int, default=3)
    parser.add_argument("--rr-values", default="1.5,2,3")
    parser.add_argument("--min-risk-pct", type=float, default=0.05)
    parser.add_argument("--max-risk-pct", type=float, default=3.0)
    parser.add_argument("--round-trip-cost-pct", type=float, default=0.10)
    parser.add_argument("--min-trades", type=int, default=250)
    parser.add_argument("--sim-chunk-size", type=int, default=100000)
    parser.add_argument("--top-trade-limit", type=int, default=200000)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
