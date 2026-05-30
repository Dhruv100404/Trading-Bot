from __future__ import annotations

import argparse
import itertools
import json
import math
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ma_strategy_lab import add_ma_features, build_daily_cache
from panic_reversal_strategy_lab import add_panic_features


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_OUT_DIR = ROOT / "docs" / "complex_strategy_tuning_lab"
SHARED_DAILY_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"

ROUND_TRIP_COST = 0.0026


@dataclass(frozen=True)
class MATune:
    name: str
    lookback: int
    trail_ma: int
    rs_min: float
    relvol_min: float
    breadth_min: float
    close_loc_min: float
    max_hold: int
    partial_day: int
    partial_fraction: float
    max_per_day: int
    stop_atr_mult: float


@dataclass(frozen=True)
class PanicTune:
    name: str
    ret3_max: float
    range_atr_min: float
    close_loc_min: float
    recovery_min: float
    broad_only: bool
    entry_model: str
    target_model: str
    max_hold: int
    partial_fraction: float
    max_per_day: int
    stop_buffer_atr: float


@dataclass(frozen=True)
class SymbolArrays:
    trade_date: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    sma10: np.ndarray
    sma20: np.ndarray


def pct(x: float | int | None) -> float:
    if x is None or pd.isna(x):
        return 0.0
    return float(x) * 100.0


def metric_block(trades: pd.DataFrame, label: str) -> dict:
    if trades.empty:
        return {
            "label": label,
            "trades": 0,
            "expectancy_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "tail_5pct": 0.0,
            "avg_hold": 0.0,
        }
    r = trades["net_return"].astype(float)
    wins = r > 0
    gross_profit = r[r > 0].sum()
    gross_loss = -r[r <= 0].sum()
    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
    equity = (1 + r / 10).cumprod()
    dd = equity / equity.cummax() - 1
    return {
        "label": label,
        "trades": int(len(trades)),
        "expectancy_pct": round(pct(r.mean()), 3),
        "median_return_pct": round(pct(r.median()), 3),
        "win_rate": round(pct(wins.mean()), 2),
        "profit_factor": round(float(pf), 3),
        "max_drawdown_pct": round(pct(dd.min()), 2),
        "tail_5pct": round(pct(r.quantile(0.05)), 3),
        "avg_hold": round(float(trades["hold_days"].mean()), 2),
    }


def split_cutoffs(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    start = pd.to_datetime(df["trade_date"]).min()
    end = pd.to_datetime(df["trade_date"]).max()
    span = end - start
    return start, start + span * 0.60, start + span * 0.80, end


def score_from_splits(full: dict, train: dict, validation: dict, oos: dict) -> float:
    sample_bonus = min(full["trades"], 400) / 8
    val_exp = validation["expectancy_pct"]
    oos_exp = oos["expectancy_pct"]
    train_pf = min(float(train["profit_factor"]), 8.0)
    validation_pf = min(float(validation["profit_factor"]), 8.0)
    oos_pf = min(float(oos["profit_factor"]), 8.0)
    oos_sample_penalty = max(0, 30 - int(oos["trades"])) * 1.2
    return (
        validation_pf * 18
        + oos_pf * 12
        + train_pf * 6
        + val_exp * 9
        + min(oos_exp, 4.0) * 8
        + full["expectancy_pct"] * 4
        + sample_bonus
        - abs(min(validation["max_drawdown_pct"], 0)) * 0.8
        - abs(min(oos["max_drawdown_pct"], 0)) * 0.35
        + max(full["tail_5pct"], -12) * 0.4
        - oos_sample_penalty
    )


def add_split_columns(trades: pd.DataFrame, start: pd.Timestamp, cut1: pd.Timestamp, cut2: pd.Timestamp) -> pd.DataFrame:
    out = trades.copy()
    entry_dates = pd.to_datetime(out["entry_date"])
    out["split"] = np.select(
        [entry_dates < cut1, (entry_dates >= cut1) & (entry_dates < cut2), entry_dates >= cut2],
        ["train", "validation", "out_of_sample"],
        default="unknown",
    )
    return out


def evaluate_trades(trades: pd.DataFrame, params: dict, family: str, start: pd.Timestamp, cut1: pd.Timestamp, cut2: pd.Timestamp) -> dict:
    if trades.empty:
        full = metric_block(trades, "full")
        train = metric_block(trades, "train")
        validation = metric_block(trades, "validation")
        oos = metric_block(trades, "out_of_sample")
    else:
        split = add_split_columns(trades, start, cut1, cut2)
        full = metric_block(split, "full")
        train = metric_block(split[split["split"] == "train"], "train")
        validation = metric_block(split[split["split"] == "validation"], "validation")
        oos = metric_block(split[split["split"] == "out_of_sample"], "out_of_sample")
    row = {
        "family": family,
        **params,
        "score": round(score_from_splits(full, train, validation, oos), 3),
    }
    for prefix, block in [("full", full), ("train", train), ("validation", validation), ("oos", oos)]:
        for key, value in block.items():
            if key != "label":
                row[f"{prefix}_{key}"] = value
    return row


def candidate_rank(c: pd.DataFrame) -> pd.DataFrame:
    out = c.copy()
    out["rank_score"] = (
        out["rs60_rank"].fillna(0) * 6
        + out["relvol"].fillna(0).clip(upper=5) * 1.3
        + out["close_location"].fillna(0) * 1.5
        + out["breakout_pct"].fillna(0).clip(upper=0.10) * 8
        + out["adv20"].rank(pct=True).fillna(0)
    )
    return out.sort_values(["trade_date", "rank_score"], ascending=[True, False])


def prepare_ma_optimizer_frame(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values(["symbol", "trade_date"]).copy()
    d["sym_pos"] = d.groupby("symbol").cumcount().astype(np.int32)
    g = d.groupby("symbol", group_keys=False)
    for col in [
        "liquid",
        "sma10",
        "sma20",
        "sma50",
        "sma20_slope5",
        "sma50_slope5",
        "rs60_rank",
        "market_breadth200",
        "atr14",
        "close",
    ]:
        d[f"prev_{col}"] = g[col].shift(1)
    return d


def prepare_panic_optimizer_frame(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values(["symbol", "trade_date"]).copy()
    d["sym_pos"] = d.groupby("symbol").cumcount().astype(np.int32)
    return d


def symbol_map(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {symbol: sdf.reset_index(drop=True) for symbol, sdf in df.groupby("symbol", sort=False)}


def numpy_symbol_map(df: pd.DataFrame) -> dict[str, SymbolArrays]:
    stores: dict[str, SymbolArrays] = {}
    for symbol, sdf in df.groupby("symbol", sort=False):
        stores[symbol] = SymbolArrays(
            trade_date=sdf["trade_date"].to_numpy(),
            open=sdf["open"].to_numpy(dtype=np.float64),
            high=sdf["high"].to_numpy(dtype=np.float64),
            low=sdf["low"].to_numpy(dtype=np.float64),
            close=sdf["close"].to_numpy(dtype=np.float64),
            sma10=sdf["sma10"].to_numpy(dtype=np.float64) if "sma10" in sdf else np.full(len(sdf), np.nan),
            sma20=sdf["sma20"].to_numpy(dtype=np.float64) if "sma20" in sdf else np.full(len(sdf), np.nan),
        )
    return stores


def backtest_ma_breakout(df: pd.DataFrame, tune: MATune, by_symbol: dict[str, SymbolArrays] | None = None) -> pd.DataFrame:
    d = df
    if "prev_liquid" not in d.columns:
        d = prepare_ma_optimizer_frame(d)

    high_col = f"prior_high{tune.lookback}"
    trigger = d[high_col] * 1.001
    trend_ok = (
        d["prev_liquid"].fillna(False)
        & (d["prev_market_breadth200"] >= tune.breadth_min)
        & (d["prev_sma10"] > d["prev_sma20"])
        & (d["prev_sma20"] > d["prev_sma50"])
        & (d["prev_sma20_slope5"] > -0.002)
        & (d["prev_sma50_slope5"] > -0.002)
        & (d["prev_rs60_rank"] >= tune.rs_min)
    )
    mask = (
        trend_ok
        & d[high_col].notna()
        & (d["high"] >= trigger)
        & (d["close_location"] >= tune.close_loc_min)
        & (d["relvol"] >= tune.relvol_min)
        & (d["close"] >= trigger * 0.985)
        & (d["atr14"].notna())
    )
    candidates = d.loc[mask].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates["entry_trigger"] = trigger.loc[candidates.index]
    candidates["breakout_pct"] = candidates["close"] / candidates[high_col] - 1
    candidates = candidate_rank(candidates).groupby("trade_date", group_keys=False).head(tune.max_per_day)

    by_symbol = by_symbol or numpy_symbol_map(d)
    trades: list[dict] = []

    for signal in candidates.itertuples(index=False):
        store = by_symbol[signal.symbol]
        entry_idx = int(signal.sym_pos)
        entry = float(signal.entry_trigger)
        if entry <= 0 or not math.isfinite(entry):
            continue
        prev_atr = float(signal.prev_atr14)
        atr = prev_atr if math.isfinite(prev_atr) else float(signal.atr14)
        stop = min(float(signal.low), entry - tune.stop_atr_mult * atr)
        if stop <= 0 or stop >= entry:
            continue

        remaining = 1.0
        weighted_return = 0.0
        partial_done = False
        exit_date = signal.trade_date
        exit_reason = "time"
        hold = 1
        lows = store.low
        closes = store.close
        trail_values = store.sma10 if tune.trail_ma == 10 else store.sma20
        dates = store.trade_date

        if float(signal.close) <= stop:
            weighted_return = float(signal.close) / entry - 1
            remaining = 0.0
            exit_reason = "entry_day_failed"
        for j in range(entry_idx + 1, min(entry_idx + tune.max_hold, len(closes))):
            if remaining <= 0:
                break
            hold = j - entry_idx + 1
            exit_date = dates[j]
            low = float(lows[j])
            close = float(closes[j])
            trail = float(trail_values[j])

            if low <= stop:
                weighted_return += remaining * (stop / entry - 1)
                remaining = 0.0
                exit_reason = "initial_or_breakeven_stop"
                break

            if not partial_done and hold >= tune.partial_day and close > entry:
                frac = min(remaining, tune.partial_fraction)
                weighted_return += frac * (close / entry - 1)
                remaining -= frac
                partial_done = True
                stop = max(stop, entry)

            if hold >= max(tune.partial_day, 3) and math.isfinite(trail) and close < trail:
                weighted_return += remaining * (close / entry - 1)
                remaining = 0.0
                exit_reason = f"close_below_sma{tune.trail_ma}"
                break

        if remaining > 0:
            last_idx = min(entry_idx + tune.max_hold - 1, len(closes) - 1)
            exit_date = dates[last_idx]
            weighted_return += remaining * (float(closes[last_idx]) / entry - 1)
            exit_reason = "max_hold"

        trades.append({
            "strategy": "tuned_ma_breakout",
            "symbol": signal.symbol,
            "signal_date": signal.trade_date,
            "entry_date": signal.trade_date,
            "exit_date": exit_date,
            "entry": entry,
            "stop": stop,
            "exit_reason": exit_reason,
            "hold_days": hold,
            "gross_return": weighted_return,
            "net_return": weighted_return - ROUND_TRIP_COST,
            "rank_score": signal.rank_score,
            "lookback": tune.lookback,
            "trail_ma": tune.trail_ma,
        })
    return pd.DataFrame(trades)


def broad_panic_mask(d: pd.DataFrame) -> pd.Series:
    return (d["market_avg_ret1"] <= -0.010) | (d["market_down_3pct"] >= 0.22) | (d["market_down_5pct_3d"] >= 0.34)


def panic_rank(c: pd.DataFrame) -> pd.DataFrame:
    out = c.copy()
    out["panic_depth"] = (-out["ret3"].fillna(0)).clip(lower=0)
    out["rank_score"] = (
        out["panic_depth"] * 18
        + out["recovery_from_low_pct"].fillna(0) * 28
        + out["range_atr"].fillna(0).clip(upper=5) * 0.9
        + out["close_location"].fillna(0) * 2.3
        + out["relvol"].fillna(0).clip(upper=5) * 0.6
        + out["adv20"].rank(pct=True).fillna(0)
    )
    return out.sort_values(["trade_date", "rank_score"], ascending=[True, False])


def panic_entry_price(low: float, high: float, close: float, tune: PanicTune, next_open: float | None) -> tuple[float | None, int, str]:
    if tune.entry_model == "next_open":
        if next_open is None or not math.isfinite(next_open):
            return None, 1, "next_open"
        return float(next_open), 1, "next_open"
    if tune.entry_model == "close":
        return close, 0, "signal_close"
    reclaim_fraction = 0.25 if tune.entry_model == "reclaim25" else 0.40
    entry = low + reclaim_fraction * (high - low)
    if close < entry:
        return None, 0, "unconfirmed_reclaim"
    return float(entry), 0, tune.entry_model


def panic_target(sma20: float, pre_panic_close3: float, signal_low: float, tune: PanicTune, entry: float) -> float | None:
    if tune.target_model == "sma20":
        value = float(sma20)
        return value if math.isfinite(value) and value > entry else None
    if tune.target_model == "pre3":
        value = float(pre_panic_close3)
        return value if math.isfinite(value) and value > entry else None
    if tune.target_model == "half_retrace":
        pre = float(pre_panic_close3)
        value = signal_low + 0.5 * (pre - signal_low) if math.isfinite(pre) else np.nan
        return value if math.isfinite(value) and value > entry else None
    return None


def backtest_panic(df: pd.DataFrame, tune: PanicTune, by_symbol: dict[str, SymbolArrays] | None = None) -> pd.DataFrame:
    d = df
    liquid = d["mega_liquid"] if tune.broad_only else d["liquid"]
    panic = broad_panic_mask(d) if tune.broad_only else pd.Series(True, index=d.index)
    mask = (
        liquid
        & panic
        & (d["ret3"] <= tune.ret3_max)
        & (d["range_atr"] >= tune.range_atr_min)
        & (d["close_location"] >= tune.close_loc_min)
        & (d["recovery_from_low_pct"] >= tune.recovery_min)
        & (d["atr14"].notna())
    )
    candidates = d.loc[mask].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates = panic_rank(candidates).groupby("trade_date", group_keys=False).head(tune.max_per_day)
    by_symbol = by_symbol or numpy_symbol_map(d)
    trades: list[dict] = []

    for signal in candidates.itertuples(index=False):
        store = by_symbol[signal.symbol]
        signal_idx = int(signal.sym_pos)
        opens = store.open
        highs = store.high
        lows = store.low
        closes = store.close
        sma20 = store.sma20
        dates = store.trade_date
        next_open = float(opens[signal_idx + 1]) if signal_idx + 1 < len(opens) else None
        signal_low = float(signal.low)
        entry, start_offset, entry_reason = panic_entry_price(signal_low, float(signal.high), float(signal.close), tune, next_open)
        if entry is None or entry <= 0:
            continue
        atr = float(signal.atr14)
        stop = signal_low - tune.stop_buffer_atr * atr
        if stop <= 0 or stop >= entry:
            continue

        remaining = 1.0
        weighted_return = 0.0
        partial_done = False
        exit_date = signal.trade_date
        exit_reason = "time"
        hold = 1
        entry_idx = signal_idx + start_offset
        if entry_idx >= len(closes):
            continue
        pre_panic_close3 = float(signal.pre_panic_close3)

        for j in range(entry_idx + 1, min(entry_idx + tune.max_hold, len(closes))):
            if remaining <= 0:
                break
            hold = j - entry_idx + 1
            exit_date = dates[j]
            low = float(lows[j])
            high = float(highs[j])
            close = float(closes[j])

            if low <= stop:
                weighted_return += remaining * (stop / entry - 1)
                remaining = 0.0
                exit_reason = "panic_low_stop"
                break

            target = panic_target(float(sma20[j]), pre_panic_close3, signal_low, tune, entry)
            if target is not None and high >= target and not partial_done:
                frac = min(remaining, tune.partial_fraction)
                weighted_return += frac * (target / entry - 1)
                remaining -= frac
                partial_done = True
                stop = max(stop, entry)
                exit_reason = f"partial_{tune.target_model}"

            trail = float(lows[j - 1])
            if partial_done and low <= trail:
                weighted_return += remaining * (trail / entry - 1)
                remaining = 0.0
                exit_reason = "prior_daily_low_trail"
                break

            if not partial_done and hold >= max(3, tune.max_hold // 2) and close > entry:
                frac = min(remaining, tune.partial_fraction / 2)
                weighted_return += frac * (close / entry - 1)
                remaining -= frac
                partial_done = True
                stop = max(stop, entry)

        if remaining > 0:
            last_idx = min(entry_idx + tune.max_hold - 1, len(closes) - 1)
            exit_date = dates[last_idx]
            weighted_return += remaining * (float(closes[last_idx]) / entry - 1)
            exit_reason = "max_hold"

        trades.append({
            "strategy": "tuned_panic_reversal",
            "symbol": signal.symbol,
            "signal_date": signal.trade_date,
            "entry_date": dates[entry_idx],
            "exit_date": exit_date,
            "entry": entry,
            "stop": stop,
            "exit_reason": exit_reason,
            "entry_reason": entry_reason,
            "hold_days": hold,
            "gross_return": weighted_return,
            "net_return": weighted_return - ROUND_TRIP_COST,
            "rank_score": signal.rank_score,
            "ret3_signal": signal.ret3,
            "range_atr": signal.range_atr,
            "close_location": signal.close_location,
            "recovery_from_low_pct": signal.recovery_from_low_pct,
        })
    return pd.DataFrame(trades)


def ma_grid(limit: int | None = None) -> list[MATune]:
    rows: list[MATune] = []
    for values in itertools.product(
        [20, 55],
        [10, 20],
        [0.58, 0.68, 0.78],
        [0.8, 1.05, 1.3],
        [0.28, 0.38, 0.48],
        [0.45, 0.58],
        [45, 75],
        [3, 5],
        [0.33, 0.50],
        [1.0, 1.4],
    ):
        lookback, trail, rs, relvol, breadth, close_loc, max_hold, partial_day, partial_fraction, stop_atr = values
        rows.append(MATune(
            name=f"ma_l{lookback}_t{trail}_rs{rs}_rv{relvol}_b{breadth}_cl{close_loc}_h{max_hold}_p{partial_day}",
            lookback=lookback,
            trail_ma=trail,
            rs_min=rs,
            relvol_min=relvol,
            breadth_min=breadth,
            close_loc_min=close_loc,
            max_hold=max_hold,
            partial_day=partial_day,
            partial_fraction=partial_fraction,
            max_per_day=6,
            stop_atr_mult=stop_atr,
        ))
    if limit and len(rows) > limit:
        indices = np.linspace(0, len(rows) - 1, limit, dtype=int)
        return [rows[int(i)] for i in indices]
    return rows


def panic_grid(limit: int | None = None) -> list[PanicTune]:
    rows: list[PanicTune] = []
    for values in itertools.product(
        [-0.06, -0.08, -0.10],
        [1.1, 1.35, 1.6],
        [0.40, 0.52, 0.64],
        [0.012, 0.022, 0.035],
        [True, False],
        ["reclaim25", "reclaim40", "next_open"],
        ["pre3", "half_retrace", "sma20"],
        [8, 12],
        [0.60, 0.75],
        [0.0, 0.15],
    ):
        ret3, range_atr, close_loc, recovery, broad_only, entry_model, target_model, max_hold, partial_fraction, stop_buffer = values
        rows.append(PanicTune(
            name=f"panic_r{abs(ret3)}_ra{range_atr}_cl{close_loc}_rec{recovery}_{'broad' if broad_only else 'sym'}_{entry_model}_{target_model}_h{max_hold}",
            ret3_max=ret3,
            range_atr_min=range_atr,
            close_loc_min=close_loc,
            recovery_min=recovery,
            broad_only=broad_only,
            entry_model=entry_model,
            target_model=target_model,
            max_hold=max_hold,
            partial_fraction=partial_fraction,
            max_per_day=12 if broad_only else 8,
            stop_buffer_atr=stop_buffer,
        ))
    if limit and len(rows) > limit:
        indices = np.linspace(0, len(rows) - 1, limit, dtype=int)
        return [rows[int(i)] for i in indices]
    return rows


def run_grid(df: pd.DataFrame, family: str, tunes: list, backtest_fn, out_dir: Path, prefix: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    start, cut1, cut2, end = split_cutoffs(df)
    result_rows: list[dict] = []
    trade_logs: list[pd.DataFrame] = []

    for idx, tune in enumerate(tunes, start=1):
        if idx == 1 or idx % 50 == 0:
            print(f"{prefix}: {idx}/{len(tunes)}")
        trades = backtest_fn(df, tune)
        params = asdict(tune)
        row = evaluate_trades(trades, params, family, start, cut1, cut2)
        result_rows.append(row)
        if not trades.empty:
            trades = add_split_columns(trades, start, cut1, cut2)
            trades["param_name"] = tune.name
            trade_logs.append(trades)

    results = pd.DataFrame(result_rows).sort_values("score", ascending=False)
    all_trades = pd.concat(trade_logs, ignore_index=True) if trade_logs else pd.DataFrame()
    results.to_csv(out_dir / f"{prefix}_tuned_results.csv", index=False)
    all_trades.to_csv(out_dir / f"{prefix}_all_candidate_trades.csv", index=False)
    if not results.empty and not all_trades.empty:
        best_name = results.iloc[0]["name"]
        best_trades = all_trades[all_trades["param_name"] == best_name].copy()
        best_trades.to_csv(out_dir / f"{prefix}_best_trades.csv", index=False)
    return results, all_trades


def save_charts(out_dir: Path, ma_results: pd.DataFrame, panic_results: pd.DataFrame, ma_trades: pd.DataFrame, panic_trades: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    for name, results in [("ma", ma_results), ("panic", panic_results)]:
        top = results.sort_values("score", ascending=False).head(20).sort_values("score")
        plt.figure(figsize=(12, 7))
        plt.barh(top["name"], top["score"], color="#1b998b")
        plt.title(f"Top tuned {name} parameter sets")
        plt.xlabel("Walk-forward score")
        plt.tight_layout()
        plt.savefig(chart_dir / f"{name}_top_parameter_scores.png", dpi=160)
        plt.close()

        plt.figure(figsize=(9, 6))
        plt.scatter(results["validation_expectancy_pct"], results["oos_expectancy_pct"], s=22, alpha=0.45, color="#33658a")
        plt.axhline(0, color="#333333", linewidth=1)
        plt.axvline(0, color="#333333", linewidth=1)
        plt.title(f"{name.upper()} validation vs out-of-sample expectancy")
        plt.xlabel("Validation expectancy %")
        plt.ylabel("OOS expectancy %")
        plt.tight_layout()
        plt.savefig(chart_dir / f"{name}_validation_vs_oos.png", dpi=160)
        plt.close()

    for name, results, trades in [("ma", ma_results, ma_trades), ("panic", panic_results, panic_trades)]:
        if results.empty or trades.empty:
            continue
        best_names = results.head(5)["name"].tolist()
        plt.figure(figsize=(12, 6))
        for param in best_names:
            part = trades[trades["param_name"] == param].sort_values("entry_date")
            if part.empty:
                continue
            equity = (1 + part["net_return"] / 10).cumprod()
            plt.plot(pd.to_datetime(part["entry_date"]), equity, label=param[:42])
        plt.title(f"Top tuned {name} equity curves, 10% capital proxy")
        plt.ylabel("Equity multiple")
        plt.legend(fontsize=7)
        plt.tight_layout()
        plt.savefig(chart_dir / f"{name}_top_equity_curves.png", dpi=160)
        plt.close()


def period_return_tables(best_trades: pd.DataFrame, prefix: str, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if best_trades.empty:
        monthly = pd.DataFrame(columns=["strategy_family", "year", "month", "month_label", "trades", "win_rate", "avg_return_pct", "return_proxy_pct"])
        yearly = pd.DataFrame(columns=["strategy_family", "year", "trades", "win_rate", "avg_return_pct", "return_proxy_pct"])
        monthly.to_csv(out_dir / f"{prefix}_monthly_returns.csv", index=False)
        yearly.to_csv(out_dir / f"{prefix}_yearly_returns.csv", index=False)
        return monthly, yearly

    part = best_trades.copy()
    part["entry_date"] = pd.to_datetime(part["entry_date"])
    part["year"] = part["entry_date"].dt.year
    part["month"] = part["entry_date"].dt.month
    part["month_label"] = part["entry_date"].dt.strftime("%Y-%m")

    monthly = (
        part.groupby(["year", "month", "month_label"], sort=True)
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
            avg_return_pct=("net_return", lambda s: round(float(s.mean() * 100), 3)),
            return_proxy_pct=("net_return", lambda s: round(float((s / 10).sum() * 100), 3)),
        )
        .reset_index()
    )
    monthly.insert(0, "strategy_family", prefix)
    yearly = (
        part.groupby(["year"], sort=True)
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
            avg_return_pct=("net_return", lambda s: round(float(s.mean() * 100), 3)),
            return_proxy_pct=("net_return", lambda s: round(float((s / 10).sum() * 100), 3)),
        )
        .reset_index()
    )
    yearly.insert(0, "strategy_family", prefix)
    monthly.to_csv(out_dir / f"{prefix}_monthly_returns.csv", index=False)
    yearly.to_csv(out_dir / f"{prefix}_yearly_returns.csv", index=False)
    return monthly, yearly


def save_period_charts(out_dir: Path, ma_monthly: pd.DataFrame, ma_yearly: pd.DataFrame, panic_monthly: pd.DataFrame, panic_yearly: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    for prefix, monthly, yearly in [
        ("ma", ma_monthly, ma_yearly),
        ("panic", panic_monthly, panic_yearly),
    ]:
        if not yearly.empty:
            plt.figure(figsize=(10, 5))
            colors = ["#1b998b" if value >= 0 else "#c44536" for value in yearly["return_proxy_pct"]]
            plt.bar(yearly["year"].astype(str), yearly["return_proxy_pct"], color=colors)
            plt.axhline(0, color="#333333", linewidth=1)
            plt.title(f"{prefix.upper()} best model yearly return proxy")
            plt.ylabel("Return proxy %")
            plt.tight_layout()
            plt.savefig(chart_dir / f"{prefix}_best_yearly_returns.png", dpi=160)
            plt.close()

        if not monthly.empty:
            pivot = monthly.pivot_table(index="year", columns="month", values="return_proxy_pct", aggfunc="sum").fillna(0)
            plt.figure(figsize=(12, 5))
            plt.imshow(pivot, aspect="auto", cmap="RdYlGn", vmin=-12, vmax=12)
            plt.colorbar(label="Return proxy %")
            plt.xticks(range(len(pivot.columns)), [str(int(month)) for month in pivot.columns])
            plt.yticks(range(len(pivot.index)), [str(int(year)) for year in pivot.index])
            plt.title(f"{prefix.upper()} best model monthly return heatmap")
            plt.xlabel("Month")
            plt.ylabel("Year")
            plt.tight_layout()
            plt.savefig(chart_dir / f"{prefix}_best_monthly_heatmap.png", dpi=160)
            plt.close()


def ma_tune_from_row(row: pd.Series) -> MATune:
    return MATune(
        name=str(row["name"]),
        lookback=int(row["lookback"]),
        trail_ma=int(row["trail_ma"]),
        rs_min=float(row["rs_min"]),
        relvol_min=float(row["relvol_min"]),
        breadth_min=float(row["breadth_min"]),
        close_loc_min=float(row["close_loc_min"]),
        max_hold=int(row["max_hold"]),
        partial_day=int(row["partial_day"]),
        partial_fraction=float(row["partial_fraction"]),
        max_per_day=int(row["max_per_day"]),
        stop_atr_mult=float(row["stop_atr_mult"]),
    )


def panic_tune_from_row(row: pd.Series) -> PanicTune:
    return PanicTune(
        name=str(row["name"]),
        ret3_max=float(row["ret3_max"]),
        range_atr_min=float(row["range_atr_min"]),
        close_loc_min=float(row["close_loc_min"]),
        recovery_min=float(row["recovery_min"]),
        broad_only=bool(row["broad_only"]),
        entry_model=str(row["entry_model"]),
        target_model=str(row["target_model"]),
        max_hold=int(row["max_hold"]),
        partial_fraction=float(row["partial_fraction"]),
        max_per_day=int(row["max_per_day"]),
        stop_buffer_atr=float(row["stop_buffer_atr"]),
    )


def latest_ma_predictions(df: pd.DataFrame, tune: MATune, recent_dates: int = 20, limit: int = 40) -> pd.DataFrame:
    high_col = f"prior_high{tune.lookback}"
    trigger = df[high_col] * 1.001
    trend_ok = (
        df["prev_liquid"].fillna(False)
        & (df["prev_market_breadth200"] >= tune.breadth_min)
        & (df["prev_sma10"] > df["prev_sma20"])
        & (df["prev_sma20"] > df["prev_sma50"])
        & (df["prev_sma20_slope5"] > -0.002)
        & (df["prev_sma50_slope5"] > -0.002)
        & (df["prev_rs60_rank"] >= tune.rs_min)
    )
    mask = (
        trend_ok
        & df[high_col].notna()
        & (df["high"] >= trigger)
        & (df["close_location"] >= tune.close_loc_min)
        & (df["relvol"] >= tune.relvol_min)
        & (df["close"] >= trigger * 0.985)
        & df["atr14"].notna()
    )
    candidates = df.loc[mask].copy()
    if candidates.empty:
        return pd.DataFrame()
    dates = sorted(candidates["trade_date"].dropna().unique())[-recent_dates:]
    candidates = candidates[candidates["trade_date"].isin(dates)].copy()
    candidates["entry"] = trigger.loc[candidates.index]
    candidates["breakout_pct"] = candidates["close"] / candidates[high_col] - 1
    candidates["risk_per_share"] = (candidates["entry"] - np.minimum(candidates["low"], candidates["entry"] - tune.stop_atr_mult * candidates["atr14"])).clip(lower=0.01)
    candidates["stop"] = candidates["entry"] - candidates["risk_per_share"]
    candidates["target"] = candidates["entry"] + 2.5 * candidates["risk_per_share"]
    ranked = candidate_rank(candidates).groupby("trade_date", group_keys=False).head(tune.max_per_day)
    ranked = ranked.sort_values(["trade_date", "rank_score"], ascending=[False, False]).head(limit)
    ranked["trade_date"] = pd.to_datetime(ranked["trade_date"])
    return pd.DataFrame({
        "strategy_family": "moving_average_breakout",
        "model": tune.name,
        "signal_date": ranked["trade_date"].dt.strftime("%Y-%m-%d"),
        "symbol": ranked["symbol"],
        "direction": "long",
        "entry": ranked["entry"].round(2),
        "stop": ranked["stop"].round(2),
        "target": ranked["target"].round(2),
        "score": ranked["rank_score"].round(3),
        "close": ranked["close"].round(2),
        "reason": "20/55D breakout trigger, MA trend alignment, relative strength, volume expansion",
    })


def latest_panic_predictions(df: pd.DataFrame, tune: PanicTune, recent_dates: int = 20, limit: int = 40) -> pd.DataFrame:
    liquid = df["mega_liquid"] if tune.broad_only else df["liquid"]
    panic = broad_panic_mask(df) if tune.broad_only else pd.Series(True, index=df.index)
    mask = (
        liquid
        & panic
        & (df["ret3"] <= tune.ret3_max)
        & (df["range_atr"] >= tune.range_atr_min)
        & (df["close_location"] >= tune.close_loc_min)
        & (df["recovery_from_low_pct"] >= tune.recovery_min)
        & df["atr14"].notna()
    )
    candidates = df.loc[mask].copy()
    if candidates.empty:
        return pd.DataFrame()
    dates = sorted(candidates["trade_date"].dropna().unique())[-recent_dates:]
    candidates = candidates[candidates["trade_date"].isin(dates)].copy()
    candidates = panic_rank(candidates).groupby("trade_date", group_keys=False).head(tune.max_per_day)
    entries = []
    targets = []
    stops = []
    for row in candidates.itertuples(index=False):
        entry, _, _ = panic_entry_price(float(row.low), float(row.high), float(row.close), tune, None)
        entry = entry if entry and math.isfinite(entry) else float(row.close)
        target = panic_target(float(row.sma20), float(row.pre_panic_close3), float(row.low), tune, entry)
        if target is None:
            target = entry + 2.0 * max(entry - float(row.low), 0.01)
        entries.append(entry)
        targets.append(target)
        stops.append(float(row.low) - tune.stop_buffer_atr * float(row.atr14))
    candidates["entry"] = entries
    candidates["target"] = targets
    candidates["stop"] = stops
    candidates = candidates.sort_values(["trade_date", "rank_score"], ascending=[False, False]).head(limit)
    candidates["trade_date"] = pd.to_datetime(candidates["trade_date"])
    return pd.DataFrame({
        "strategy_family": "panic_reversal",
        "model": tune.name,
        "signal_date": candidates["trade_date"].dt.strftime("%Y-%m-%d"),
        "symbol": candidates["symbol"],
        "direction": "long",
        "entry": candidates["entry"].round(2),
        "stop": candidates["stop"].round(2),
        "target": candidates["target"].round(2),
        "score": candidates["rank_score"].round(3),
        "close": candidates["close"].round(2),
        "reason": "capitulation selloff, reclaim/off-low confirmation, volatility expansion",
    })


def write_signal_predictions(out_dir: Path, ma_df: pd.DataFrame, panic_df: pd.DataFrame, ma_results: pd.DataFrame, panic_results: pd.DataFrame) -> pd.DataFrame:
    predictions = []
    if not ma_results.empty:
        predictions.append(latest_ma_predictions(ma_df, ma_tune_from_row(ma_results.iloc[0])))
    if not panic_results.empty:
        predictions.append(latest_panic_predictions(panic_df, panic_tune_from_row(panic_results.iloc[0])))
    all_predictions = pd.concat([item for item in predictions if not item.empty], ignore_index=True) if predictions else pd.DataFrame()
    if not all_predictions.empty:
        all_predictions = all_predictions.sort_values(["signal_date", "score"], ascending=[False, False])
        latest_date = all_predictions["signal_date"].max()
        current = all_predictions[all_predictions["signal_date"] == latest_date].copy()
    else:
        current = pd.DataFrame()
    all_predictions.to_csv(out_dir / "latest_signal_predictions.csv", index=False)
    current.to_csv(out_dir / "current_signal_predictions.csv", index=False)
    return all_predictions


def write_engine_payload(
    out_dir: Path,
    ma_results: pd.DataFrame,
    panic_results: pd.DataFrame,
    ma_monthly: pd.DataFrame,
    ma_yearly: pd.DataFrame,
    panic_monthly: pd.DataFrame,
    panic_yearly: pd.DataFrame,
    predictions: pd.DataFrame,
) -> None:
    chart_dir = out_dir / "charts"
    payload = {
        "updated_at": pd.Timestamp.utcnow().isoformat(),
        "output_dir": str(out_dir),
        "best": {
            "ma": ma_results.head(1).to_dict(orient="records"),
            "panic": panic_results.head(1).to_dict(orient="records"),
        },
        "scorecards": {
            "ma": ma_results.head(20).to_dict(orient="records"),
            "panic": panic_results.head(20).to_dict(orient="records"),
        },
        "period_returns": {
            "ma_monthly": ma_monthly.tail(36).to_dict(orient="records"),
            "ma_yearly": ma_yearly.to_dict(orient="records"),
            "panic_monthly": panic_monthly.tail(36).to_dict(orient="records"),
            "panic_yearly": panic_yearly.to_dict(orient="records"),
        },
        "predictions": predictions.head(80).to_dict(orient="records") if not predictions.empty else [],
        "charts": {
            "ma_equity": str(chart_dir / "ma_top_equity_curves.png"),
            "ma_monthly_heatmap": str(chart_dir / "ma_best_monthly_heatmap.png"),
            "ma_yearly": str(chart_dir / "ma_best_yearly_returns.png"),
            "panic_equity": str(chart_dir / "panic_top_equity_curves.png"),
            "panic_monthly_heatmap": str(chart_dir / "panic_best_monthly_heatmap.png"),
            "panic_yearly": str(chart_dir / "panic_best_yearly_returns.png"),
            "ma_validation_vs_oos": str(chart_dir / "ma_validation_vs_oos.png"),
            "panic_validation_vs_oos": str(chart_dir / "panic_validation_vs_oos.png"),
        },
        "files": {
            "report": str(out_dir / "final_report.md"),
            "combined_scorecard": str(out_dir / "combined_top_scorecard.csv"),
            "ma_monthly": str(out_dir / "ma_monthly_returns.csv"),
            "ma_yearly": str(out_dir / "ma_yearly_returns.csv"),
            "panic_monthly": str(out_dir / "panic_monthly_returns.csv"),
            "panic_yearly": str(out_dir / "panic_yearly_returns.csv"),
            "predictions": str(out_dir / "latest_signal_predictions.csv"),
            "current_predictions": str(out_dir / "current_signal_predictions.csv"),
        },
    }
    (out_dir / "engine_payload.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_report(
    out_dir: Path,
    ma_results: pd.DataFrame,
    panic_results: pd.DataFrame,
    ma_monthly: pd.DataFrame,
    ma_yearly: pd.DataFrame,
    panic_monthly: pd.DataFrame,
    panic_yearly: pd.DataFrame,
    predictions: pd.DataFrame,
) -> None:
    best_ma = ma_results.iloc[0].to_dict()
    best_panic = panic_results.iloc[0].to_dict()
    report = out_dir / "final_report.md"
    lines = [
        "# Complex Strategy Tuning Lab",
        "",
        "This is the second-pass optimizer for the two discretionary playbooks:",
        "",
        "- Qullamaggie/Lance moving-average trend breakout.",
        "- Lance panic/capitulation reversal.",
        "",
        "The first pass used daily next-open approximations. This pass tunes the actual Python strategy logic: entry model, filters, partial exits, breakeven behavior, and trail style.",
        "",
        "Speed note: the hot simulation loop uses precomputed NumPy arrays per symbol. Pandas is only used for feature engineering, ranking, and reporting.",
        "Scoring note: profit factor is capped inside the optimizer score and small out-of-sample windows are penalized so tiny no-loss samples do not dominate the leaderboard.",
        "",
        "## Why real-life winners can fail naive backtests",
        "",
        "1. Real entries are intraday; next-open backtests often buy too late.",
        "2. Discretionary traders wait for confirmation: failed low, reclaim, ORH break, higher low, or volume confirmation.",
        "3. The best trades are rare and clustered in specific regimes.",
        "4. The local data is NSE stock history, not Nikkei futures, Nasdaq futures, Apple, Nvidia, or yen carry panic data.",
        "5. Daily OHLC cannot prove exact event order inside the candle.",
        "",
        "## Best tuned MA breakout",
        "",
        pd.DataFrame([best_ma]).to_markdown(index=False),
        "",
        "## Best tuned panic reversal",
        "",
        pd.DataFrame([best_panic]).to_markdown(index=False),
        "",
        "## Top 10 MA parameter sets",
        "",
        ma_results.head(10).to_markdown(index=False),
        "",
        "## Top 10 panic parameter sets",
        "",
        panic_results.head(10).to_markdown(index=False),
        "",
        "## Moving-average monthly return proxy",
        "",
        ma_monthly.tail(18).to_markdown(index=False) if not ma_monthly.empty else "No MA monthly rows.",
        "",
        "## Moving-average yearly return proxy",
        "",
        ma_yearly.to_markdown(index=False) if not ma_yearly.empty else "No MA yearly rows.",
        "",
        "## Panic reversal monthly return proxy",
        "",
        panic_monthly.tail(18).to_markdown(index=False) if not panic_monthly.empty else "No panic monthly rows.",
        "",
        "## Panic reversal yearly return proxy",
        "",
        panic_yearly.to_markdown(index=False) if not panic_yearly.empty else "No panic yearly rows.",
        "",
        "## Latest model signal predictions",
        "",
        predictions.head(30).to_markdown(index=False) if not predictions.empty else "No fresh signals in the latest lookback window.",
        "",
        "## Production read",
        "",
        "The tuned backtests should be treated as a research engine, not a magic JSON config. The useful output is the parameter behavior: which filters survive validation, which exits carry the edge, and where out-of-sample breaks.",
        "",
        "For production, the next upgrade is an intraday event simulator that reads minute buckets only for selected candidate days and verifies sequence: low made first, reclaim triggered, higher low held, then entry fired.",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "daily_bars_cache.parquet"
    if SHARED_DAILY_CACHE.exists() and not args.refresh_cache:
        daily = pd.read_parquet(SHARED_DAILY_CACHE)
        if not cache.exists():
            daily.to_parquet(cache, index=False)
    else:
        daily = build_daily_cache(Path(args.parquet_dir), cache, refresh=args.refresh_cache)
    daily = daily.dropna(subset=["trade_date", "open", "high", "low", "close"]).copy()

    print("Building MA features")
    ma_df = prepare_ma_optimizer_frame(add_ma_features(daily))
    ma_by_symbol = numpy_symbol_map(ma_df)
    print("Building panic features")
    panic_df = prepare_panic_optimizer_frame(add_panic_features(daily))
    panic_by_symbol = numpy_symbol_map(panic_df)

    ma_tunes = ma_grid(args.max_ma_grid)
    panic_tunes = panic_grid(args.max_panic_grid)
    print(f"Running {len(ma_tunes)} MA parameter sets")
    ma_results, ma_trades = run_grid(
        ma_df,
        "moving_average_breakout",
        ma_tunes,
        lambda frame, tune: backtest_ma_breakout(frame, tune, ma_by_symbol),
        out_dir,
        "ma",
    )
    print(f"Running {len(panic_tunes)} panic parameter sets")
    panic_results, panic_trades = run_grid(
        panic_df,
        "panic_reversal",
        panic_tunes,
        lambda frame, tune: backtest_panic(frame, tune, panic_by_symbol),
        out_dir,
        "panic",
    )
    ma_best_name = str(ma_results.iloc[0]["name"]) if not ma_results.empty else ""
    panic_best_name = str(panic_results.iloc[0]["name"]) if not panic_results.empty else ""
    ma_best_trades = ma_trades[ma_trades["param_name"] == ma_best_name].copy() if ma_best_name and not ma_trades.empty else pd.DataFrame()
    panic_best_trades = panic_trades[panic_trades["param_name"] == panic_best_name].copy() if panic_best_name and not panic_trades.empty else pd.DataFrame()

    ma_monthly, ma_yearly = period_return_tables(ma_best_trades, "ma", out_dir)
    panic_monthly, panic_yearly = period_return_tables(panic_best_trades, "panic", out_dir)
    predictions = write_signal_predictions(out_dir, ma_df, panic_df, ma_results, panic_results)

    save_charts(out_dir, ma_results, panic_results, ma_trades, panic_trades)
    save_period_charts(out_dir, ma_monthly, ma_yearly, panic_monthly, panic_yearly)
    write_report(out_dir, ma_results, panic_results, ma_monthly, ma_yearly, panic_monthly, panic_yearly, predictions)

    combined = pd.concat(
        [
            ma_results.head(25).assign(strategy_family="moving_average_breakout"),
            panic_results.head(25).assign(strategy_family="panic_reversal"),
        ],
        ignore_index=True,
    ).sort_values("score", ascending=False)
    combined.to_csv(out_dir / "combined_top_scorecard.csv", index=False)
    write_engine_payload(
        out_dir,
        ma_results,
        panic_results,
        ma_monthly,
        ma_yearly,
        panic_monthly,
        panic_yearly,
        predictions,
    )

    print("Best MA")
    print(ma_results.head(5).to_string(index=False))
    print("Best panic")
    print(panic_results.head(5).to_string(index=False))
    print(f"Wrote complex tuning lab to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tune and retest the MA breakout and panic reversal strategies with richer Python logic.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Example:
              python scripts/complex_strategy_optimizer.py --max-ma-grid 300 --max-panic-grid 500
            """
        ),
    )
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--max-ma-grid", type=int, default=260)
    parser.add_argument("--max-panic-grid", type=int, default=420)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
