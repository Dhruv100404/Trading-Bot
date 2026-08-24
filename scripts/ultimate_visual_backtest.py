from __future__ import annotations

import argparse
import html
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_VOLUME_GROUPS_PATH = ROOT / "data" / "volume_groups.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "ultimate_visual_backtest"

CAPITAL = 50_000.0
LEVERAGE = 5.0
TOP_N = 5
CAP_MULT = 3.0
ENTRY_BUCKET = 1
EXIT_BUCKET = 45
CR1_MIN = 1.5
WICK_MAX = 0.2
PRICE_MAX = 500.0
RF_ANNUAL = 0.065
TRADING_DAYS_PER_YEAR = 252

COLS = [
    "symbol",
    "date",
    "gap_pct",
    "day_open",
    "bucket",
    "open",
    "high",
    "low",
    "close",
    "vwap",
    "buy_ratio",
]

T0 = time.perf_counter()


@dataclass(frozen=True, slots=True)
class BacktestResult:
    target_pct: float
    stop_pct: float
    metrics: dict[str, float | int | str]
    daily: pd.DataFrame
    monthly: pd.DataFrame
    trades: pd.DataFrame
    drawdown: pd.DataFrame
    losing_streaks: pd.DataFrame


def log(message: str) -> None:
    print(f"[{time.perf_counter() - T0:0.1f}s] {message}", flush=True)


def parse_float_list(raw: str) -> list[float]:
    values = [float(part.strip()) for part in str(raw).split(",") if part.strip()]
    if not values:
        raise ValueError("Expected at least one comma-separated float.")
    return values


def month_key(path: Path) -> str | None:
    stem = path.stem
    if not stem.startswith("candles_"):
        return None
    key = stem.replace("candles_", "", 1)
    return key if len(key) == 6 and key.isdigit() else None


def parquet_paths(parquet_dir: Path, start_month: str, end_month: str) -> list[Path]:
    paths: list[Path] = []
    max_month = "999999" if end_month.lower() in {"", "auto", "latest"} else end_month
    for path in sorted(parquet_dir.glob("candles_20*.parquet")):
        key = month_key(path)
        if key is not None and start_month <= key <= max_month:
            paths.append(path)
    return paths


def load_volume_group(path: Path, group_name: str) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data.get("volume_groups", {})
    if group_name not in groups:
        available = ", ".join(sorted(groups))
        raise ValueError(f"Unknown volume group '{group_name}'. Available: {available}")
    return {str(symbol) for symbol in groups[group_name]}


def minute_label(bucket: int) -> str:
    total_minutes = 9 * 60 + 15 + int(bucket)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def stack_bucket_arrays(piv: pd.DataFrame, prefix: str, buckets: list[int]) -> np.ndarray:
    arrays = []
    row_count = len(piv)
    for bucket in buckets:
        col = f"{prefix}_b{bucket}"
        if col in piv.columns:
            arrays.append(piv[col].to_numpy(np.float32, copy=False))
        else:
            arrays.append(np.full(row_count, np.nan, dtype=np.float32))
    return np.stack(arrays, axis=1).astype(np.float32, copy=False)


def first_true(mask: np.ndarray) -> np.ndarray:
    width = mask.shape[1]
    found = mask.any(axis=1)
    idx = np.argmax(mask, axis=1).astype(np.int32)
    idx[~found] = width
    return idx


def select_top_per_day(candidates: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    ranked = candidates.sort_values(["date", "score", "symbol"], ascending=[True, False, True], kind="mergesort")
    return ranked.groupby("date", sort=False).head(top_n).reset_index(drop=True)


def load_month_candidates(path: Path, symbols: set[str], args: argparse.Namespace) -> pd.DataFrame:
    data = pd.read_parquet(path, columns=COLS)
    if data.empty:
        return pd.DataFrame()

    data = data[(data["bucket"] <= int(args.exit_bucket)) & data["symbol"].isin(symbols)].copy()
    if data.empty:
        return pd.DataFrame()

    data["date"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "gap_pct", "day_open", "vwap", "buy_ratio"]:
        data[column] = data[column].astype(np.float32)

    base = data.groupby(["symbol", "date"], sort=False).agg(
        gap_pct=("gap_pct", "first"),
        day_open=("day_open", "first"),
    )
    piv = base.reset_index()
    pivot_values = ["open", "high", "low", "close", "vwap", "buy_ratio"]
    for value in pivot_values:
        wide = data.pivot_table(
            index=["symbol", "date"],
            columns="bucket",
            values=value,
            aggfunc="first",
        )
        wide.columns = [f"{value}_b{int(col)}" for col in wide.columns]
        piv = piv.merge(wide.reset_index(), on=["symbol", "date"], how="left")

    buckets = list(range(1, int(args.exit_bucket) + 1))
    open_arr = stack_bucket_arrays(piv, "open", buckets)
    high_arr = stack_bucket_arrays(piv, "high", buckets)
    low_arr = stack_bucket_arrays(piv, "low", buckets)
    close_arr = stack_bucket_arrays(piv, "close", buckets)
    vwap_arr = stack_bucket_arrays(piv, "vwap", buckets)
    buy_ratio_arr = stack_bucket_arrays(piv, "buy_ratio", buckets)

    b1_open = open_arr[:, 0]
    b1_high = high_arr[:, 0]
    b1_low = low_arr[:, 0]
    b1_close = close_arr[:, 0]
    b1_vwap = vwap_arr[:, 0]
    b1_buy_ratio = buy_ratio_arr[:, 0]
    gap = piv["gap_pct"].to_numpy(np.float32, copy=False)

    with np.errstate(divide="ignore", invalid="ignore"):
        candle_return = ((b1_close - b1_open) / b1_open * 100.0).astype(np.float32)
        candle_range = ((b1_high - b1_low) / b1_open * 100.0).astype(np.float32)
        upper_wick = ((b1_high - np.maximum(b1_open, b1_close)) / b1_open * 100.0).astype(np.float32)
        upper_wick_ratio = np.where(candle_range > 0, upper_wick / candle_range, 0.0).astype(np.float32)
        body_ratio = np.where(candle_range > 0, np.abs(candle_return) / candle_range, 0.0).astype(np.float32)

    green = (b1_close > b1_open) & (b1_open > 0.0) & np.isfinite(b1_close)
    above_vwap = (b1_close > b1_vwap) & (b1_vwap > 0.0) & np.isfinite(b1_vwap)
    mask = (
        green
        & (candle_return > float(args.cr1_min))
        & (upper_wick_ratio < float(args.wick_max))
        & (b1_close < float(args.price_max))
        & (b1_close > 0.0)
        & (candle_range >= 0.01)
        & np.isfinite(b1_close)
    )
    if not mask.any():
        return pd.DataFrame()

    score = np.zeros(len(piv), dtype=np.float32)
    score += np.where(candle_return > 3.0, 4.0, np.where(candle_return > 2.0, 3.0, np.where(candle_return > 1.5, 2.0, 0.0)))
    score += np.where(gap > 2.0, 2.0, np.where(gap > 1.0, 1.5, np.where(gap > 0.5, 1.0, np.where(gap > 0.0, 0.5, 0.0))))
    score += np.where(b1_buy_ratio > 0.80, 1.5, np.where(b1_buy_ratio > 0.65, 1.0, np.where(b1_buy_ratio > 0.50, 0.5, 0.0)))
    score += np.where(above_vwap, 0.5, 0.0)
    score += np.where(body_ratio > 0.8, 0.5, 0.0)

    selected_idx = np.flatnonzero(mask)
    candidates = pd.DataFrame(
        {
            "date": piv.loc[selected_idx, "date"].to_numpy(dtype=object),
            "symbol": piv.loc[selected_idx, "symbol"].to_numpy(dtype=object),
            "entry_price": b1_close[selected_idx],
            "gap_pct": gap[selected_idx],
            "cr1_pct": candle_return[selected_idx],
            "upper_wick_ratio": upper_wick_ratio[selected_idx],
            "body_ratio": body_ratio[selected_idx],
            "buy_ratio": b1_buy_ratio[selected_idx],
            "above_vwap": above_vwap[selected_idx],
            "score": score[selected_idx],
        }
    )

    top = select_top_per_day(candidates, int(args.top_n))
    if top.empty:
        return top

    original_lookup = pd.Series(selected_idx, index=pd.MultiIndex.from_frame(candidates[["date", "symbol"]]))
    top_keys = pd.MultiIndex.from_frame(top[["date", "symbol"]])
    original_idx = original_lookup.loc[top_keys].to_numpy(np.int32)
    future_data: dict[str, np.ndarray] = {}
    future_buckets = list(range(int(args.entry_bucket) + 1, int(args.exit_bucket) + 1))
    for bucket in future_buckets:
        arr_idx = bucket - 1
        future_data[f"open_b{bucket}"] = open_arr[original_idx, arr_idx]
        future_data[f"high_b{bucket}"] = high_arr[original_idx, arr_idx]
        future_data[f"low_b{bucket}"] = low_arr[original_idx, arr_idx]
        future_data[f"close_b{bucket}"] = close_arr[original_idx, arr_idx]

    return pd.concat([top.reset_index(drop=True), pd.DataFrame(future_data)], axis=1)


def load_selected_trades(paths: list[Path], symbols: set[str], args: argparse.Namespace) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for idx, path in enumerate(paths, start=1):
        log(f"Loading {path.name} ({idx}/{len(paths)})")
        month_candidates = load_month_candidates(path, symbols, args)
        if not month_candidates.empty:
            parts.append(month_candidates)
            log(f"  selected {len(month_candidates):,} top-ranked trades")
        else:
            log("  no selected trades")
    if not parts:
        return pd.DataFrame()
    selected = pd.concat(parts, ignore_index=True)
    selected = selected.sort_values(["date", "score", "symbol"], ascending=[True, False, True], kind="mergesort")
    return selected.reset_index(drop=True)


def simulate_trades(selected: pd.DataFrame, target_pct: float, stop_pct: float, args: argparse.Namespace) -> pd.DataFrame:
    future_buckets = list(range(int(args.entry_bucket) + 1, int(args.exit_bucket) + 1))
    entry = selected["entry_price"].to_numpy(np.float32, copy=False)
    path_open = selected[[f"open_b{bucket}" for bucket in future_buckets]].to_numpy(np.float32, copy=False)
    path_high = selected[[f"high_b{bucket}" for bucket in future_buckets]].to_numpy(np.float32, copy=False)
    path_low = selected[[f"low_b{bucket}" for bucket in future_buckets]].to_numpy(np.float32, copy=False)
    path_close = selected[[f"close_b{bucket}" for bucket in future_buckets]].to_numpy(np.float32, copy=False)

    target_price = entry * (1.0 - np.float32(target_pct) / 100.0)
    stop_price = entry * (1.0 + np.float32(stop_pct) / 100.0)
    target_hit = path_low <= target_price.reshape(-1, 1)
    stop_hit = path_high >= stop_price.reshape(-1, 1)
    target_idx = first_true(target_hit)
    stop_idx = first_true(stop_hit)
    width = len(future_buckets)

    stop_first = (stop_idx <= target_idx) & (stop_idx < width)
    target_first = (target_idx < stop_idx) & (target_idx < width)
    timeout = ~(stop_first | target_first)
    exit_idx = np.where(stop_first, stop_idx, np.where(target_first, target_idx, width - 1)).astype(np.int32)
    row_idx = np.arange(len(selected), dtype=np.int32)
    exit_open = path_open[row_idx, exit_idx]
    exit_close = path_close[row_idx, exit_idx]

    stop_gap = stop_first & (exit_open >= stop_price)
    exit_price = np.where(
        stop_gap,
        exit_open,
        np.where(stop_first, stop_price, np.where(target_first, target_price, exit_close)),
    ).astype(np.float32)
    gross_pct = ((entry - exit_price) / entry * 100.0).astype(np.float32)
    net_pct = (gross_pct - np.float32(args.cost_pct)).astype(np.float32)
    exit_type = np.where(stop_gap, "SL_GAP", np.where(stop_first, "SL", np.where(target_first, "TARGET", "TIME")))
    exit_bucket = np.array(future_buckets, dtype=np.int32)[exit_idx]

    trades = selected[
        [
            "date",
            "symbol",
            "entry_price",
            "gap_pct",
            "cr1_pct",
            "upper_wick_ratio",
            "buy_ratio",
            "score",
        ]
    ].copy()
    trades["target_pct"] = float(target_pct)
    trades["stop_pct"] = float(stop_pct)
    trades["exit_bucket"] = exit_bucket
    trades["exit_time"] = [minute_label(bucket) for bucket in exit_bucket]
    trades["exit_price"] = exit_price
    trades["exit_type"] = exit_type
    trades["gross_pct"] = gross_pct
    trades["net_pct"] = net_pct
    return trades


def max_streak(flags: np.ndarray) -> int:
    best = 0
    current = 0
    for flag in flags:
        if bool(flag):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def losing_streaks(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []
    current: list[int] = []
    pnl = daily["daily_rs"].to_numpy(np.float64, copy=False)
    dates = daily["date"].astype(str).to_numpy(dtype=object)
    for idx, value in enumerate(pnl):
        if value < 0:
            current.append(idx)
        elif current:
            streak_values = pnl[current]
            rows.append(
                {
                    "start": dates[current[0]],
                    "end": dates[current[-1]],
                    "days": len(current),
                    "loss_rs": float(streak_values.sum()),
                    "loss_pct": float(daily.iloc[current]["daily_roc_pct"].sum()),
                }
            )
            current = []
    if current:
        streak_values = pnl[current]
        rows.append(
            {
                "start": dates[current[0]],
                "end": dates[current[-1]],
                "days": len(current),
                "loss_rs": float(streak_values.sum()),
                "loss_pct": float(daily.iloc[current]["daily_roc_pct"].sum()),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["start", "end", "days", "loss_rs", "loss_pct"])
    return pd.DataFrame(rows).sort_values(["loss_rs", "days"], ascending=[True, False]).reset_index(drop=True)


def daily_from_trades(trades: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    grouped = trades.groupby("date", sort=True)
    rows: list[dict[str, float | int | str]] = []
    total_margin = float(args.capital) * float(args.leverage)
    base_position = total_margin / float(args.top_n)
    max_position = base_position * float(args.cap_mult)
    cumulative_rs = 0.0

    for date, group in grouped:
        trade_count = len(group)
        position_size = min(total_margin / trade_count, max_position)
        daily_rs = float((group["net_pct"].to_numpy(np.float64) / 100.0 * position_size).sum())
        cumulative_rs += daily_rs
        rows.append(
            {
                "date": str(date),
                "trades": trade_count,
                "wins": int((group["net_pct"] > 0).sum()),
                "losses": int((group["net_pct"] < 0).sum()),
                "daily_rs": daily_rs,
                "daily_roc_pct": daily_rs / float(args.capital) * 100.0,
                "equity": float(args.capital) + cumulative_rs,
            }
        )

    daily = pd.DataFrame(rows)
    if daily.empty:
        return daily
    daily["date_dt"] = pd.to_datetime(daily["date"])
    peak = daily["equity"].cummax()
    daily["drawdown_rs"] = peak - daily["equity"]
    daily["drawdown_pct"] = np.where(peak > 0, daily["drawdown_rs"] / peak * 100.0, 0.0)
    return daily


def summarize(trades: pd.DataFrame, daily: pd.DataFrame, target_pct: float, stop_pct: float, args: argparse.Namespace) -> tuple[dict[str, float | int | str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if trades.empty or daily.empty:
        metrics: dict[str, float | int | str] = {
            "target_pct": target_pct,
            "stop_pct": stop_pct,
            "trades": 0,
            "total_return_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
        }
        return metrics, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    net = trades["net_pct"].to_numpy(np.float64, copy=False)
    wins = net[net > 0]
    losses = net[net < 0]
    equity = daily["equity"].to_numpy(np.float64, copy=False)
    daily_rs = daily["daily_rs"].to_numpy(np.float64, copy=False)
    prev_equity = np.roll(equity, 1)
    prev_equity[0] = float(args.capital)
    daily_return = np.where(prev_equity > 0, daily_rs / prev_equity, 0.0)
    excess = daily_return - RF_ANNUAL / TRADING_DAYS_PER_YEAR
    std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    sharpe = float(excess.mean() / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else 0.0
    downside = daily_return[daily_return < 0]
    downside_std = float(np.sqrt((downside**2).mean())) if len(downside) else 0.0
    sortino = float(daily_return.mean() / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR)) if downside_std > 0 else 0.0
    total_return_pct = float((equity[-1] - float(args.capital)) / float(args.capital) * 100.0)
    max_drawdown_pct = float(daily["drawdown_pct"].max())
    max_drawdown_rs = float(daily["drawdown_rs"].max())
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0
    years = max(len(daily) / TRADING_DAYS_PER_YEAR, 1 / TRADING_DAYS_PER_YEAR)
    cagr = float((equity[-1] / float(args.capital)) ** (1.0 / years) - 1.0) if equity[-1] > 0 else -1.0
    recovery = total_return_pct / max_drawdown_pct if max_drawdown_pct > 0 else 999.0

    monthly = (
        daily.assign(month=daily["date_dt"].dt.strftime("%Y-%m"))
        .groupby("month", as_index=False)
        .agg(
            monthly_rs=("daily_rs", "sum"),
            monthly_roc_pct=("daily_roc_pct", "sum"),
            trading_days=("date", "count"),
            trades=("trades", "sum"),
        )
    )
    monthly["status"] = np.where(monthly["monthly_rs"] > 0, "GREEN", np.where(monthly["monthly_rs"] < 0, "RED", "FLAT"))

    drawdown = daily[["date", "equity", "drawdown_rs", "drawdown_pct"]].copy()
    streaks = losing_streaks(daily)
    max_dd_idx = int(daily["drawdown_pct"].idxmax())
    metrics = {
        "target_pct": float(target_pct),
        "stop_pct": float(stop_pct),
        "trades": int(len(trades)),
        "trading_days": int(len(daily)),
        "start_date": str(daily["date"].iloc[0]),
        "end_date": str(daily["date"].iloc[-1]),
        "ending_equity": float(equity[-1]),
        "total_return_pct": total_return_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "profit_factor": float(profit_factor),
        "win_rate_pct": float((net > 0).mean() * 100.0),
        "avg_win_pct": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(losses.mean()) if len(losses) else 0.0,
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_rs": max_drawdown_rs,
        "max_drawdown_date": str(daily["date"].iloc[max_dd_idx]),
        "cagr_pct": cagr * 100.0,
        "recovery_factor": float(recovery),
        "profitable_months": int((monthly["monthly_rs"] > 0).sum()),
        "red_months": int((monthly["monthly_rs"] < 0).sum()),
        "active_months": int(len(monthly)),
        "best_month_pct": float(monthly["monthly_roc_pct"].max()),
        "worst_month_pct": float(monthly["monthly_roc_pct"].min()),
        "max_losing_days": max_streak((daily["daily_rs"] < 0).to_numpy(bool)),
        "max_winning_days": max_streak((daily["daily_rs"] > 0).to_numpy(bool)),
        "target_exits": int((trades["exit_type"] == "TARGET").sum()),
        "stop_exits": int(trades["exit_type"].isin(["SL", "SL_GAP"]).sum()),
        "time_exits": int((trades["exit_type"] == "TIME").sum()),
    }
    return metrics, monthly, drawdown, streaks


def run_combo(selected: pd.DataFrame, target_pct: float, stop_pct: float, args: argparse.Namespace) -> BacktestResult:
    trades = simulate_trades(selected, target_pct, stop_pct, args)
    daily = daily_from_trades(trades, args)
    metrics, monthly, drawdown, streaks = summarize(trades, daily, target_pct, stop_pct, args)
    return BacktestResult(target_pct, stop_pct, metrics, daily, monthly, trades, drawdown, streaks)


def choose_best(results: list[BacktestResult]) -> BacktestResult:
    def score(result: BacktestResult) -> tuple[float, float, float]:
        metrics = result.metrics
        return (
            float(metrics.get("sharpe", 0.0)),
            float(metrics.get("total_return_pct", 0.0)),
            -float(metrics.get("max_drawdown_pct", 0.0)),
        )

    return max(results, key=score)


def plot_heatmap(ax: plt.Axes, summary: pd.DataFrame, value_col: str, title: str, cmap: str) -> None:
    pivot = summary.pivot(index="stop_pct", columns="target_pct", values=value_col).sort_index(ascending=True)
    values = pivot.to_numpy(float)
    im = ax.imshow(values, cmap=cmap, aspect="auto")
    ax.set_title(title, fontweight="bold")
    ax.set_xlabel("Target %")
    ax.set_ylabel("Stop %")
    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_xticklabels([f"{value:g}" for value in pivot.columns])
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_yticklabels([f"{value:g}" for value in pivot.index])
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            text_color = "white" if abs(values[row, col]) > np.nanmax(np.abs(values)) * 0.55 else "black"
            ax.text(col, row, f"{values[row, col]:.1f}", ha="center", va="center", fontsize=8, color=text_color)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)


def save_dashboard(best: BacktestResult, summary: pd.DataFrame, out_dir: Path) -> Path:
    fig = plt.figure(figsize=(18, 14), constrained_layout=True)
    gs = fig.add_gridspec(3, 2)
    ax_equity = fig.add_subplot(gs[0, 0])
    ax_dd = fig.add_subplot(gs[1, 0])
    ax_monthly = fig.add_subplot(gs[2, 0])
    ax_heat = fig.add_subplot(gs[0, 1])
    ax_streak = fig.add_subplot(gs[1, 1])
    ax_text = fig.add_subplot(gs[2, 1])

    daily = best.daily
    monthly = best.monthly
    metrics = best.metrics

    ax_equity.plot(daily["date_dt"], daily["equity"], color="#146C94", linewidth=2.4)
    ax_equity.fill_between(daily["date_dt"], daily["equity"], float(CAPITAL), color="#AFD3E2", alpha=0.35)
    ax_equity.set_title(f"Equity Curve: TP {best.target_pct:g}% / SL {best.stop_pct:g}%", fontweight="bold")
    ax_equity.set_ylabel("Equity (Rs)")
    ax_equity.grid(True, alpha=0.25)

    ax_dd.fill_between(daily["date_dt"], -daily["drawdown_pct"], 0, color="#C0392B", alpha=0.35)
    ax_dd.plot(daily["date_dt"], -daily["drawdown_pct"], color="#922B21", linewidth=1.4)
    ax_dd.set_title("Drawdown Curve", fontweight="bold")
    ax_dd.set_ylabel("Drawdown %")
    ax_dd.grid(True, alpha=0.25)

    colors = np.where(monthly["monthly_roc_pct"] >= 0, "#287D3C", "#B42318")
    ax_monthly.bar(monthly["month"], monthly["monthly_roc_pct"], color=colors)
    ax_monthly.axhline(0, color="#333333", linewidth=0.8)
    ax_monthly.set_title("Monthly ROC %", fontweight="bold")
    ax_monthly.set_ylabel("ROC %")
    ax_monthly.tick_params(axis="x", labelrotation=70, labelsize=8)
    ax_monthly.grid(True, axis="y", alpha=0.2)

    plot_heatmap(ax_heat, summary, "sharpe", "Target/SL Sharpe Heatmap", "RdYlGn")

    streaks = best.losing_streaks.head(8).copy()
    if streaks.empty:
        ax_streak.text(0.5, 0.5, "No losing streaks", ha="center", va="center")
        ax_streak.axis("off")
    else:
        labels = streaks["start"] + " to " + streaks["end"] + " (" + streaks["days"].astype(str) + "d)"
        ax_streak.barh(labels[::-1], streaks["loss_pct"].iloc[::-1], color="#B42318")
        ax_streak.set_title("Top Losing Streaks by ROC", fontweight="bold")
        ax_streak.set_xlabel("Streak ROC %")
        ax_streak.grid(True, axis="x", alpha=0.2)

    exit_counts = best.trades["exit_type"].value_counts()
    text = "\n".join(
        [
            "Best combo by Sharpe",
            f"TP / SL: {best.target_pct:g}% / {best.stop_pct:g}%",
            f"Trades: {int(metrics['trades']):,}",
            f"Return: {float(metrics['total_return_pct']):+.1f}%",
            f"Ending equity: Rs {float(metrics['ending_equity']):,.0f}",
            f"Sharpe: {float(metrics['sharpe']):.2f}",
            f"Sortino: {float(metrics['sortino']):.2f}",
            f"Max DD: {float(metrics['max_drawdown_pct']):.2f}%",
            f"Profit factor: {float(metrics['profit_factor']):.2f}",
            f"Profitable months: {int(metrics['profitable_months'])}/{int(metrics['active_months'])}",
            f"Max losing days: {int(metrics['max_losing_days'])}",
            "",
            "Exit mix",
            *(f"{name}: {count:,}" for name, count in exit_counts.items()),
        ]
    )
    ax_text.text(0.02, 0.98, text, ha="left", va="top", fontsize=13, family="monospace")
    ax_text.axis("off")

    fig.suptitle("Ultimate Strategy Visual Backtest", fontsize=20, fontweight="bold")
    path = out_dir / "dashboard.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def save_heatmaps(summary: pd.DataFrame, out_dir: Path) -> tuple[Path, Path, Path]:
    paths: list[Path] = []
    specs = [
        ("total_return_pct", "Total Return %", "RdYlGn", "target_sl_total_return.png"),
        ("max_drawdown_pct", "Max Drawdown %", "YlOrRd", "target_sl_drawdown.png"),
        ("profit_factor", "Profit Factor", "RdYlGn", "target_sl_profit_factor.png"),
    ]
    for value_col, title, cmap, filename in specs:
        fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
        plot_heatmap(ax, summary, value_col, title, cmap)
        path = out_dir / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path)
    return paths[0], paths[1], paths[2]


def save_monthly(best: BacktestResult, out_dir: Path) -> Path:
    monthly = best.monthly.copy()
    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    colors = np.where(monthly["monthly_roc_pct"] >= 0, "#287D3C", "#B42318")
    ax.bar(monthly["month"], monthly["monthly_roc_pct"], color=colors)
    ax.axhline(0, color="#222222", linewidth=0.8)
    ax.set_title("Profitable and Losing Months", fontweight="bold")
    ax.set_ylabel("Monthly ROC %")
    ax.tick_params(axis="x", labelrotation=70)
    ax.grid(True, axis="y", alpha=0.2)
    path = out_dir / "monthly_returns.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def format_metrics_cards(metrics: dict[str, float | int | str]) -> str:
    cards = [
        ("Total Return", f"{float(metrics['total_return_pct']):+.1f}%"),
        ("Ending Equity", f"Rs {float(metrics['ending_equity']):,.0f}"),
        ("Sharpe", f"{float(metrics['sharpe']):.2f}"),
        ("Max Drawdown", f"{float(metrics['max_drawdown_pct']):.2f}%"),
        ("Profit Factor", f"{float(metrics['profit_factor']):.2f}"),
        ("Win Rate", f"{float(metrics['win_rate_pct']):.1f}%"),
        ("Profitable Months", f"{int(metrics['profitable_months'])}/{int(metrics['active_months'])}"),
        ("Max Losing Days", f"{int(metrics['max_losing_days'])}"),
    ]
    return "\n".join(f"<div class='card'><span>{name}</span><strong>{value}</strong></div>" for name, value in cards)


def df_to_html_table(df: pd.DataFrame, columns: list[str], rows: int = 12) -> str:
    if df.empty:
        return "<p>No rows.</p>"
    subset = df.loc[:, columns].head(rows).copy()
    return subset.to_html(index=False, classes="table", border=0, justify="center", escape=True)


def save_report(
    best: BacktestResult,
    summary: pd.DataFrame,
    dashboard_path: Path,
    heatmap_paths: tuple[Path, Path, Path],
    monthly_path: Path,
    out_dir: Path,
    args: argparse.Namespace,
) -> Path:
    top_sharpe = summary.sort_values(["sharpe", "total_return_pct"], ascending=False)
    top_return = summary.sort_values(["total_return_pct", "sharpe"], ascending=False)
    metrics = best.metrics
    plain_read = [
        f"Best Sharpe combo is TP {best.target_pct:g}% / SL {best.stop_pct:g}%.",
        f"Equity ended at Rs {float(metrics['ending_equity']):,.0f}, total return {float(metrics['total_return_pct']):+.1f}%.",
        f"Max drawdown was {float(metrics['max_drawdown_pct']):.2f}% on {html.escape(str(metrics['max_drawdown_date']))}.",
        f"Green months were {int(metrics['profitable_months'])} out of {int(metrics['active_months'])}.",
        f"The longest losing-day streak was {int(metrics['max_losing_days'])} active trading days.",
    ]
    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ultimate Strategy Visual Backtest</title>
<style>
body {{ font-family: Inter, Arial, sans-serif; margin: 28px; color: #1d2939; background: #f7f8fa; }}
h1, h2 {{ color: #101828; }}
.grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ background: white; border: 1px solid #e4e7ec; border-radius: 8px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(16,24,40,.06); }}
.card span {{ display:block; color:#667085; font-size:13px; }}
.card strong {{ display:block; font-size:24px; margin-top:6px; }}
.panel {{ background:white; border:1px solid #e4e7ec; border-radius:8px; padding:18px; margin:18px 0; }}
img {{ max-width: 100%; border: 1px solid #e4e7ec; border-radius: 8px; background: white; }}
.table {{ border-collapse: collapse; width: 100%; background: white; }}
.table th, .table td {{ padding: 8px 10px; border-bottom: 1px solid #eaecf0; font-size: 13px; text-align: right; }}
.table th:first-child, .table td:first-child {{ text-align: left; }}
li {{ margin: 6px 0; }}
code {{ background:#eef2f6; padding:2px 5px; border-radius:4px; }}
</style>
</head>
<body>
<h1>Ultimate Strategy Visual Backtest</h1>
<p>
MID stocks, b1 green reversal short, CR1 &gt; {float(args.cr1_min):g}%, upper wick ratio &lt; {float(args.wick_max):g},
price &lt; Rs {float(args.price_max):g}, top {int(args.top_n)} picks/day, entry bucket {int(args.entry_bucket)}
({minute_label(int(args.entry_bucket))}), force exit bucket {int(args.exit_bucket)} ({minute_label(int(args.exit_bucket))}).
</p>
<div class="grid">
{format_metrics_cards(metrics)}
</div>
<div class="panel">
<h2>Visual Read</h2>
<ul>
{''.join(f'<li>{html.escape(item)}</li>' for item in plain_read)}
</ul>
</div>
<div class="panel">
<h2>Main Dashboard</h2>
<img src="{dashboard_path.name}" alt="Ultimate strategy dashboard">
</div>
<div class="panel">
<h2>Target / Stop-Loss Sweep</h2>
<p>Higher is better for return, Sharpe, and profit factor. Lower is better for drawdown.</p>
<img src="{heatmap_paths[0].name}" alt="Target SL total return heatmap">
<br><br>
<img src="{heatmap_paths[1].name}" alt="Target SL drawdown heatmap">
<br><br>
<img src="{heatmap_paths[2].name}" alt="Target SL profit factor heatmap">
</div>
<div class="panel">
<h2>Monthly Shape</h2>
<img src="{monthly_path.name}" alt="Monthly returns">
</div>
<div class="panel">
<h2>Top Combos by Sharpe</h2>
{df_to_html_table(top_sharpe, ["target_pct", "stop_pct", "total_return_pct", "sharpe", "max_drawdown_pct", "profit_factor", "profitable_months", "red_months"], 15)}
</div>
<div class="panel">
<h2>Top Combos by Return</h2>
{df_to_html_table(top_return, ["target_pct", "stop_pct", "total_return_pct", "sharpe", "max_drawdown_pct", "profit_factor", "profitable_months", "red_months"], 15)}
</div>
<div class="panel">
<h2>Top Losing Streaks</h2>
{df_to_html_table(best.losing_streaks, ["start", "end", "days", "loss_pct", "loss_rs"], 10)}
</div>
<p>Generated from local parquet files in <code>{html.escape(str(args.parquet_dir))}</code>.</p>
</body>
</html>
"""
    path = out_dir / "report.html"
    path.write_text(html_text, encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visual target/SL sweep for the ultimate intraday short strategy.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--volume-groups-path", type=Path, default=DEFAULT_VOLUME_GROUPS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--volume-group", default="MID (1-10cr/day)")
    parser.add_argument("--start-month", default="202301")
    parser.add_argument("--end-month", default="auto")
    parser.add_argument("--targets", default="1,1.5,2,2.5,3,4,5")
    parser.add_argument("--stops", default="0.2,0.3,0.5,0.75,1,1.5")
    parser.add_argument("--capital", type=float, default=CAPITAL)
    parser.add_argument("--leverage", type=float, default=LEVERAGE)
    parser.add_argument("--top-n", type=int, default=TOP_N)
    parser.add_argument("--cap-mult", type=float, default=CAP_MULT)
    parser.add_argument("--entry-bucket", type=int, default=ENTRY_BUCKET)
    parser.add_argument("--exit-bucket", type=int, default=EXIT_BUCKET)
    parser.add_argument("--cr1-min", type=float, default=CR1_MIN)
    parser.add_argument("--wick-max", type=float, default=WICK_MAX)
    parser.add_argument("--price-max", type=float, default=PRICE_MAX)
    parser.add_argument("--cost-pct", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.parquet_dir = Path(args.parquet_dir)
    args.out_dir = Path(args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    targets = parse_float_list(args.targets)
    stops = parse_float_list(args.stops)
    paths = parquet_paths(args.parquet_dir, str(args.start_month), str(args.end_month))
    if not paths:
        raise RuntimeError("No parquet files matched the requested month range.")

    symbols = load_volume_group(Path(args.volume_groups_path), str(args.volume_group))
    log(f"Universe: {args.volume_group} | {len(symbols):,} symbols")
    log(f"Months: {month_key(paths[0])} to {month_key(paths[-1])} | files {len(paths):,}")
    log(f"Sweeping targets {targets} x stops {stops}")

    selected = load_selected_trades(paths, symbols, args)
    if selected.empty:
        raise RuntimeError("No selected trades were produced.")
    selected_path = args.out_dir / "selected_trades.parquet"
    selected.to_parquet(selected_path, index=False)
    log(f"Selected trade candidates: {len(selected):,}")

    results: list[BacktestResult] = []
    for target in targets:
        for stop in stops:
            result = run_combo(selected, target, stop, args)
            results.append(result)
            log(
                "TP "
                f"{target:g}% SL {stop:g}% | "
                f"return {float(result.metrics['total_return_pct']):+.1f}% | "
                f"Sharpe {float(result.metrics['sharpe']):.2f} | "
                f"MDD {float(result.metrics['max_drawdown_pct']):.2f}%"
            )

    summary = pd.DataFrame([result.metrics for result in results]).sort_values(["sharpe", "total_return_pct"], ascending=False)
    best = choose_best(results)

    best.trades.to_parquet(args.out_dir / "best_tradebook.parquet", index=False)
    best.daily.to_json(args.out_dir / "best_daily.json", orient="records", indent=2)
    best.monthly.to_json(args.out_dir / "best_monthly.json", orient="records", indent=2)
    best.losing_streaks.to_json(args.out_dir / "best_losing_streaks.json", orient="records", indent=2)
    summary.to_json(args.out_dir / "target_sl_summary.json", orient="records", indent=2)

    dashboard_path = save_dashboard(best, summary, args.out_dir)
    heatmap_paths = save_heatmaps(summary, args.out_dir)
    monthly_path = save_monthly(best, args.out_dir)
    report_path = save_report(best, summary, dashboard_path, heatmap_paths, monthly_path, args.out_dir, args)

    log(f"Best Sharpe: TP {best.target_pct:g}% / SL {best.stop_pct:g}%")
    log(f"Wrote dashboard: {dashboard_path}")
    log(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
