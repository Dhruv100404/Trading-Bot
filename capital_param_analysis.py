"""
Capital and parameter sensitivity analysis for the intraday gap strategies.

This script:
1. Loads the local parquet history from ./parquets
2. Replays SELL and BUY gap strategies across a compact parameter grid
3. Scores each configuration on total return, Sharpe, drawdown, win rate, and monthly consistency
4. Writes monthly return tables for multiple starting-capital scenarios

Outputs land in ./analysis_capital_params
"""

from __future__ import annotations

import gc
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
PARQUET_DIR = BASE_DIR / "parquets"
VOLUME_GROUPS_PATH = BASE_DIR / "volume_groups.json"
OUT_DIR = BASE_DIR / "analysis_capital_params"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CAPITAL_SCENARIOS = [50_000, 100_000, 200_000, 500_000, 1_000_000]
LEVERAGE = 5.0
TOP_N = 15
ENTRY_BKT = 1
CAP_MULT = 2.0
COST_PCT = 0.15
RF_ANNUAL = 0.065

TP_LIST = [2.0, 3.0, 4.0]
SL_LIST = [0.4, 0.5, 0.75]
EXIT_LIST = [30, 45, 60]
GAP_ABS_LIST = [1.0, 1.5, 2.0]
PRICE_MAX_LIST = [750, 1000, 1500]
TOP_REPORT_COUNT = 6

TIMER_START = time.perf_counter()


def log(message: str) -> None:
    elapsed = time.perf_counter() - TIMER_START
    mins = int(elapsed // 60)
    prefix = f"[{mins:02d}:{elapsed % 60:05.2f}]" if mins else f"[{elapsed:05.2f}s]"
    print(f"{prefix} {message}", flush=True)


def find_month_files() -> list[tuple[str, Path]]:
    month_files: list[tuple[str, Path]] = []
    for path in sorted(PARQUET_DIR.glob("candles_*.parquet")):
        match = re.fullmatch(r"candles_(\d{6})\.parquet", path.name)
        if match:
            month_files.append((match.group(1), path))
    if not month_files:
        raise FileNotFoundError(f"No monthly parquet files found in {PARQUET_DIR}")
    return month_files


def load_target_symbols() -> set[str]:
    with open(VOLUME_GROUPS_PATH, "r", encoding="utf-8") as handle:
        volume_groups = json.load(handle)["volume_groups"]
    return set(volume_groups.get("MEGA (>100cr/day)", [])) | set(
        volume_groups.get("LARGE (10-100cr/day)", [])
    )


def load_market_data(max_exit_bkt: int) -> dict[str, np.ndarray | list[str]]:
    target_symbols = load_target_symbols()
    cols = ["symbol", "date", "gap_pct", "bucket", "open", "high", "low", "close"]

    frames: list[pd.DataFrame] = []
    month_files = find_month_files()
    log(f"Loading {len(month_files)} monthly parquet files...")
    for month, path in month_files:
        frame = pd.read_parquet(path, columns=cols)
        frame = frame[(frame["bucket"] <= max_exit_bkt + 1) & (frame["symbol"].isin(target_symbols))]
        for col in ["gap_pct", "open", "high", "low", "close"]:
            frame[col] = frame[col].astype(np.float32)
        frames.append(frame)
        log(f"  {month}: {len(frame):,} rows")

    df = pd.concat(frames, ignore_index=True)
    del frames
    gc.collect()
    log(
        "Loaded "
        f"{len(df):,} rows | {df['symbol'].nunique()} symbols | {df['date'].nunique()} trading days"
    )

    log("Pivoting into bucket matrices...")
    stock_day = (
        df.groupby(["symbol", "date"])
        .agg(gap_pct=("gap_pct", "first"))
        .reset_index()
    )
    pivot = stock_day.copy()
    for value_name in ["open", "high", "low", "close"]:
        sub = df[["symbol", "date", "bucket", value_name]]
        value_pivot = sub.pivot_table(
            index=["symbol", "date"],
            columns="bucket",
            values=value_name,
            aggfunc="first",
        )
        value_pivot.columns = [f"{value_name}_b{int(col)}" for col in value_pivot.columns]
        pivot = pivot.merge(value_pivot, on=["symbol", "date"], how="left")
        del sub, value_pivot
        gc.collect()
    del df, stock_day
    gc.collect()

    dates = pivot["date"].values.astype(str)
    months = np.array([date[:7] for date in dates])
    unique_dates = np.unique(dates)
    unique_months = np.array(sorted(np.unique(months)))
    day_to_index = {date: idx for idx, date in enumerate(unique_dates)}
    month_to_index = {month: idx for idx, month in enumerate(unique_months)}

    n_rows = len(pivot)
    buckets = list(range(1, max_exit_bkt + 2))
    bucket_to_index = {bucket: idx for idx, bucket in enumerate(buckets)}

    def column_as_matrix(prefix: str, bucket: int) -> np.ndarray:
        column = f"{prefix}_b{bucket}"
        if column in pivot.columns:
            return pivot[column].values.astype(np.float32)
        return np.full(n_rows, np.nan, dtype=np.float32)

    open_matrix = np.stack([column_as_matrix("open", bucket) for bucket in buckets], axis=1)
    high_matrix = np.stack([column_as_matrix("high", bucket) for bucket in buckets], axis=1)
    low_matrix = np.stack([column_as_matrix("low", bucket) for bucket in buckets], axis=1)
    close_matrix = np.stack([column_as_matrix("close", bucket) for bucket in buckets], axis=1)
    gap = pivot["gap_pct"].values.astype(np.float32)

    day_index = np.array([day_to_index[date] for date in dates], dtype=np.int32)
    day_to_month_index = np.array(
        [month_to_index[date[:7]] for date in unique_dates],
        dtype=np.int32,
    )
    price_b1 = close_matrix[:, bucket_to_index[ENTRY_BKT]].copy()
    valid_b1 = (open_matrix[:, bucket_to_index[ENTRY_BKT]] > 0) & ~np.isnan(price_b1)
    non_flat_bucket = (
        np.where(
            open_matrix[:, bucket_to_index[ENTRY_BKT]] > 0,
            (
                high_matrix[:, bucket_to_index[ENTRY_BKT]]
                - low_matrix[:, bucket_to_index[ENTRY_BKT]]
            )
            / open_matrix[:, bucket_to_index[ENTRY_BKT]]
            * 100,
            0,
        )
        >= 0.01
    )

    del pivot
    gc.collect()
    log(
        "Matrix ready "
        f"| {n_rows:,} stock-days | {len(unique_dates)} days | {len(unique_months)} months"
    )

    return {
        "open": open_matrix,
        "high": high_matrix,
        "low": low_matrix,
        "close": close_matrix,
        "gap": gap,
        "dates": unique_dates,
        "months": unique_months.tolist(),
        "day_index": day_index,
        "day_to_month_index": day_to_month_index,
        "price_b1": price_b1,
        "valid_b1": valid_b1,
        "non_flat_bucket": non_flat_bucket,
        "bucket_to_index": bucket_to_index,
    }


def first_true_index(mask: np.ndarray) -> np.ndarray:
    has_true = mask.any(axis=1)
    index = np.argmax(mask, axis=1)
    index[~has_true] = mask.shape[1]
    return index


def calc_metrics(daily_frac: np.ndarray, monthly_frac: np.ndarray) -> dict[str, float]:
    trading_days = len(daily_frac)
    years = trading_days / 252 if trading_days else 0.0
    total_return_pct = float(daily_frac.sum() * 100)

    equity = 1.0 + np.cumsum(daily_frac)
    if len(equity):
        peaks = np.maximum.accumulate(equity)
        drawdown_pct = float(((peaks - equity) / peaks).max() * 100)
        ending_equity = float(equity[-1])
    else:
        drawdown_pct = 0.0
        ending_equity = 1.0

    daily_excess = daily_frac - (RF_ANNUAL / 252)
    daily_std = float(daily_excess.std(ddof=1)) if trading_days > 1 else 0.0
    sharpe = (
        float(daily_excess.mean() / daily_std * math.sqrt(252))
        if daily_std > 0
        else 0.0
    )

    positive_month_rate = float((monthly_frac > 0).mean() * 100) if len(monthly_frac) else 0.0
    best_month_pct = float(monthly_frac.max() * 100) if len(monthly_frac) else 0.0
    worst_month_pct = float(monthly_frac.min() * 100) if len(monthly_frac) else 0.0
    median_month_pct = float(np.median(monthly_frac) * 100) if len(monthly_frac) else 0.0

    if years > 0 and ending_equity > 0:
        cagr_pct = float(((ending_equity ** (1 / years)) - 1) * 100)
    else:
        cagr_pct = 0.0

    return {
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "sharpe": sharpe,
        "max_drawdown_pct": drawdown_pct,
        "positive_month_rate_pct": positive_month_rate,
        "best_month_pct": best_month_pct,
        "worst_month_pct": worst_month_pct,
        "median_month_pct": median_month_pct,
        "months_tested": int(len(monthly_frac)),
        "trading_days": int(trading_days),
    }


def simulate_config(
    data: dict[str, np.ndarray | list[str]],
    *,
    side: str,
    tp: float,
    sl: float,
    exit_bkt: int,
    gap_abs: float,
    price_max: int,
) -> dict[str, object] | None:
    open_matrix = data["open"]
    high_matrix = data["high"]
    low_matrix = data["low"]
    close_matrix = data["close"]
    gap = data["gap"]
    price_b1 = data["price_b1"]
    valid_b1 = data["valid_b1"]
    non_flat_bucket = data["non_flat_bucket"]
    bucket_to_index = data["bucket_to_index"]
    day_index = data["day_index"]
    day_to_month_index = data["day_to_month_index"]
    months = data["months"]

    gap_threshold = gap_abs if side == "sell" else -gap_abs
    if side == "sell":
        mask = (
            (gap > gap_threshold)
            & (price_b1 < price_max)
            & (price_b1 > 0)
            & non_flat_bucket
            & valid_b1
        )
    else:
        mask = (
            (gap <= gap_threshold)
            & (price_b1 < price_max)
            & (price_b1 > 0)
            & non_flat_bucket
            & valid_b1
        )

    entry_idx = bucket_to_index[ENTRY_BKT]
    exit_idx = bucket_to_index[exit_bkt]
    entry_price = close_matrix[:, entry_idx].copy()
    valid = mask & (entry_price > 0) & ~np.isnan(entry_price)
    if int(valid.sum()) < 50:
        return None

    entry_price_valid = entry_price[valid]
    future_start = entry_idx + 1
    future_end = min(exit_idx + 1, close_matrix.shape[1])

    future_open = open_matrix[valid, future_start:future_end]
    future_high = high_matrix[valid, future_start:future_end]
    future_low = low_matrix[valid, future_start:future_end]
    future_close = close_matrix[valid, future_start:future_end]
    future_len = future_high.shape[1]

    if side == "sell":
        tp_hit_mask = future_low <= entry_price_valid[:, None] * (1 - tp / 100)
        sl_hit_mask = future_high >= entry_price_valid[:, None] * (1 + sl / 100)
    else:
        tp_hit_mask = future_high >= entry_price_valid[:, None] * (1 + tp / 100)
        sl_hit_mask = future_low <= entry_price_valid[:, None] * (1 - sl / 100)

    tp_index = first_true_index(tp_hit_mask)
    sl_index = first_true_index(sl_hit_mask)
    sl_hit = ((sl_index < tp_index) | (sl_index == tp_index)) & (sl_index < future_len)
    tp_win = (tp_index < sl_index) & (tp_index < future_len)
    time_exit = ~tp_win & ~sl_hit

    trade_return_pct = np.full(int(valid.sum()), np.nan, dtype=np.float32)
    trade_return_pct[tp_win] = tp

    sl_price = (
        entry_price_valid * (1 + sl / 100)
        if side == "sell"
        else entry_price_valid * (1 - sl / 100)
    )
    sl_indices = np.where(sl_hit)[0]
    for idx in sl_indices:
        hit_idx = sl_index[idx]
        if hit_idx >= future_len:
            trade_return_pct[idx] = -sl
            continue
        hit_open = future_open[idx, hit_idx]
        if np.isnan(hit_open):
            trade_return_pct[idx] = -sl
            continue
        if side == "sell" and hit_open >= sl_price[idx]:
            trade_return_pct[idx] = -((hit_open - entry_price_valid[idx]) / entry_price_valid[idx] * 100)
        elif side == "buy" and hit_open <= sl_price[idx]:
            trade_return_pct[idx] = ((hit_open - entry_price_valid[idx]) / entry_price_valid[idx] * 100)
        else:
            trade_return_pct[idx] = -sl

    if time_exit.any():
        reversed_close = future_close[time_exit][:, ::-1]
        valid_close = ~np.isnan(reversed_close)
        first_valid = np.argmax(valid_close, axis=1)
        has_valid = valid_close.any(axis=1)
        last_close = np.full(int(time_exit.sum()), np.nan, dtype=np.float32)
        last_close[has_valid] = reversed_close[has_valid, first_valid[has_valid]]
        entry_time_exit = entry_price_valid[time_exit]
        if side == "sell":
            trade_return_pct[time_exit] = np.where(
                entry_time_exit > 0,
                (entry_time_exit - last_close) / entry_time_exit * 100,
                np.nan,
            ).astype(np.float32)
        else:
            trade_return_pct[time_exit] = np.where(
                entry_time_exit > 0,
                (last_close - entry_time_exit) / entry_time_exit * 100,
                np.nan,
            ).astype(np.float32)

    trade_return_pct = trade_return_pct - COST_PCT

    valid_indices = np.where(valid)[0]
    selected_day = day_index[valid_indices]
    selected_gap = gap[valid_indices]
    if side == "sell":
        sort_key = selected_day.astype(np.float64) * 1e6 - selected_gap.astype(np.float64)
    else:
        sort_key = selected_day.astype(np.float64) * 1e6 + selected_gap.astype(np.float64)
    order = np.argsort(sort_key)

    sorted_returns = trade_return_pct[order]
    sorted_days = selected_day[order]
    group_start = np.concatenate([[1], (np.diff(sorted_days) != 0).astype(np.int32)])
    group_offsets = np.arange(len(sorted_returns)) - np.repeat(
        np.where(group_start)[0],
        np.diff(np.concatenate([np.where(group_start)[0], [len(sorted_returns)]])),
    )
    chosen = group_offsets < TOP_N
    final_returns_pct = sorted_returns[chosen]
    final_days = sorted_days[chosen]

    trading_day_count = len(data["dates"])
    day_trade_count = np.bincount(final_days, minlength=trading_day_count).astype(np.float32)
    position_multiple = np.zeros(trading_day_count, dtype=np.float32)
    active_days = day_trade_count > 0
    position_multiple[active_days] = np.minimum(
        LEVERAGE / day_trade_count[active_days],
        (LEVERAGE / TOP_N) * CAP_MULT,
    )

    daily_frac = np.zeros(trading_day_count, dtype=np.float32)
    for trade_idx, trade_ret_pct in enumerate(final_returns_pct):
        if np.isnan(trade_ret_pct):
            continue
        day_idx = final_days[trade_idx]
        daily_frac[day_idx] += (trade_ret_pct / 100.0) * position_multiple[day_idx]

    monthly_frac = np.bincount(
        day_to_month_index,
        weights=daily_frac,
        minlength=len(months),
    ).astype(np.float32)
    metrics = calc_metrics(daily_frac, monthly_frac)
    win_rate_pct = (
        float((final_returns_pct > 0).sum() / len(final_returns_pct) * 100)
        if len(final_returns_pct)
        else 0.0
    )

    config_id = (
        f"{side}_gap{gap_abs:g}_tp{tp:g}_sl{sl:g}_exit{exit_bkt}_price{price_max}"
    )
    return {
        "config_id": config_id,
        "side": side,
        "gap_abs": gap_abs,
        "tp_pct": tp,
        "sl_pct": sl,
        "exit_bkt": exit_bkt,
        "price_max": price_max,
        "trades": int(len(final_returns_pct)),
        "active_days": int(active_days.sum()),
        "win_rate_pct": win_rate_pct,
        "daily_frac": daily_frac,
        "monthly_frac": monthly_frac,
        **metrics,
    }


def build_top_monthly_tables(
    result_rows: list[dict[str, object]],
    months: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    top_rows = result_rows[:TOP_REPORT_COUNT]

    monthly_pct_records: list[dict[str, object]] = []
    monthly_capital_records: list[dict[str, object]] = []
    for row in top_rows:
        monthly_frac = row["monthly_frac"]
        for month_idx, month in enumerate(months):
            month_return_pct = float(monthly_frac[month_idx] * 100)
            monthly_pct_records.append(
                {
                    "config_id": row["config_id"],
                    "side": row["side"],
                    "month": month,
                    "month_return_pct": round(month_return_pct, 4),
                }
            )

            for capital in CAPITAL_SCENARIOS:
                month_pnl_rs = float(monthly_frac[month_idx] * capital)
                monthly_capital_records.append(
                    {
                        "config_id": row["config_id"],
                        "side": row["side"],
                        "month": month,
                        "capital_rs": capital,
                        "month_return_pct": round(month_return_pct, 4),
                        "month_pnl_rs": round(month_pnl_rs, 2),
                    }
                )

    monthly_pct_df = pd.DataFrame(monthly_pct_records)
    monthly_capital_df = pd.DataFrame(monthly_capital_records)
    return monthly_pct_df, monthly_capital_df


def make_capital_summary(result_rows: list[dict[str, object]]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in result_rows[:TOP_REPORT_COUNT]:
        for capital in CAPITAL_SCENARIOS:
            total_pnl_rs = float(row["total_return_pct"] / 100 * capital)
            best_month_rs = float(row["best_month_pct"] / 100 * capital)
            worst_month_rs = float(row["worst_month_pct"] / 100 * capital)
            records.append(
                {
                    "config_id": row["config_id"],
                    "side": row["side"],
                    "capital_rs": capital,
                    "total_return_pct": round(float(row["total_return_pct"]), 2),
                    "total_pnl_rs": round(total_pnl_rs, 2),
                    "cagr_pct": round(float(row["cagr_pct"]), 2),
                    "max_drawdown_pct": round(float(row["max_drawdown_pct"]), 2),
                    "best_month_pnl_rs": round(best_month_rs, 2),
                    "worst_month_pnl_rs": round(worst_month_rs, 2),
                }
            )
    return pd.DataFrame(records)


def write_report(
    summary_df: pd.DataFrame,
    capital_df: pd.DataFrame,
    monthly_capital_df: pd.DataFrame,
    months: list[str],
) -> None:
    top_overall = summary_df.head(TOP_REPORT_COUNT).copy()
    top_sell = summary_df[summary_df["side"] == "sell"].head(3)
    top_buy = summary_df[summary_df["side"] == "buy"].head(3)

    best_config_id = top_overall.iloc[0]["config_id"]
    best_capital_rows = capital_df[capital_df["config_id"] == best_config_id]
    best_monthly_rows = monthly_capital_df[
        (monthly_capital_df["config_id"] == best_config_id)
        & (monthly_capital_df["capital_rs"] == 100_000)
    ]

    report_lines = [
        "# Capital and Parameter Sensitivity Report",
        "",
        "## Dataset",
        f"- Months loaded: {months[0]} to {months[-1]} ({len(months)} months)",
        "- Universe: LARGE + MEGA symbols from volume_groups.json",
        f"- Grid size: {len(summary_df)} configurations across BUY and SELL",
        f"- Capital scenarios: {', '.join(f'Rs {value:,}' for value in CAPITAL_SCENARIOS)}",
        "",
        "## Key read",
        "- Returns in Rs scale almost linearly with starting capital because sizing is a fixed multiple of capital.",
        "- The most useful comparison across configs is therefore return %, Sharpe, drawdown, and month-to-month consistency.",
        "",
        "## Top overall configs",
        top_overall[
            [
                "config_id",
                "side",
                "gap_abs",
                "tp_pct",
                "sl_pct",
                "exit_bkt",
                "price_max",
                "total_return_pct",
                "cagr_pct",
                "sharpe",
                "max_drawdown_pct",
                "positive_month_rate_pct",
                "win_rate_pct",
                "trades",
            ]
        ].to_markdown(index=False),
        "",
        "## Best SELL configs",
        top_sell[
            [
                "config_id",
                "total_return_pct",
                "sharpe",
                "max_drawdown_pct",
                "positive_month_rate_pct",
                "win_rate_pct",
            ]
        ].to_markdown(index=False),
        "",
        "## Best BUY configs",
        top_buy[
            [
                "config_id",
                "total_return_pct",
                "sharpe",
                "max_drawdown_pct",
                "positive_month_rate_pct",
                "win_rate_pct",
            ]
        ].to_markdown(index=False),
        "",
        f"## Best config capital view: {best_config_id}",
        best_capital_rows[
            [
                "capital_rs",
                "total_return_pct",
                "total_pnl_rs",
                "cagr_pct",
                "max_drawdown_pct",
                "best_month_pnl_rs",
                "worst_month_pnl_rs",
            ]
        ].to_markdown(index=False),
        "",
        f"## Best config monthly view at Rs 100,000: {best_config_id}",
        best_monthly_rows[["month", "month_return_pct", "month_pnl_rs"]].to_markdown(index=False),
        "",
        "## Files",
        "- config_summary.csv: full parameter-by-parameter scoreboard",
        "- top_configs.csv: best configurations ranked by Sharpe, return, and drawdown",
        "- top_configs_monthly_return_pct.csv: month-level % return for the top configs",
        "- top_configs_monthly_pnl_by_capital.csv: month-level Rs P&L for multiple capital sizes",
        "- top_configs_capital_summary.csv: total P&L and best/worst month in Rs by capital",
        "",
    ]

    (OUT_DIR / "report.md").write_text("\n".join(report_lines), encoding="utf-8")


def main() -> None:
    data = load_market_data(max(EXIT_LIST))
    months = data["months"]

    configs = [
        (side, tp, sl, exit_bkt, gap_abs, price_max)
        for side in ["sell", "buy"]
        for tp in TP_LIST
        for sl in SL_LIST
        for exit_bkt in EXIT_LIST
        for gap_abs in GAP_ABS_LIST
        for price_max in PRICE_MAX_LIST
    ]

    log(f"Running {len(configs)} configurations...")
    result_rows: list[dict[str, object]] = []
    for idx, (side, tp, sl, exit_bkt, gap_abs, price_max) in enumerate(configs, start=1):
        result = simulate_config(
            data,
            side=side,
            tp=tp,
            sl=sl,
            exit_bkt=exit_bkt,
            gap_abs=gap_abs,
            price_max=price_max,
        )
        if result is None:
            continue
        result_rows.append(result)
        if idx % 40 == 0 or idx == len(configs):
            log(f"  Completed {idx}/{len(configs)} configs")

    if not result_rows:
        raise RuntimeError("No valid configurations were produced from the parameter sweep.")

    summary_records = []
    for row in result_rows:
        summary_record = {key: value for key, value in row.items() if key not in {"daily_frac", "monthly_frac"}}
        summary_records.append(summary_record)
    summary_df = pd.DataFrame(summary_records)
    summary_df = summary_df.sort_values(
        by=["sharpe", "total_return_pct", "positive_month_rate_pct", "max_drawdown_pct"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    summary_df.to_csv(OUT_DIR / "config_summary.csv", index=False)
    summary_df.head(20).to_csv(OUT_DIR / "top_configs.csv", index=False)

    ordered_rows = sorted(
        result_rows,
        key=lambda row: (
            row["sharpe"],
            row["total_return_pct"],
            row["positive_month_rate_pct"],
            -row["max_drawdown_pct"],
        ),
        reverse=True,
    )

    monthly_pct_df, monthly_capital_df = build_top_monthly_tables(ordered_rows, months)
    capital_df = make_capital_summary(ordered_rows)

    monthly_pct_pivot = monthly_pct_df.pivot(
        index=["config_id", "side"],
        columns="month",
        values="month_return_pct",
    ).reset_index()

    monthly_pct_pivot.to_csv(OUT_DIR / "top_configs_monthly_return_pct.csv", index=False)
    monthly_capital_df.to_csv(OUT_DIR / "top_configs_monthly_pnl_by_capital.csv", index=False)
    capital_df.to_csv(OUT_DIR / "top_configs_capital_summary.csv", index=False)
    write_report(summary_df, capital_df, monthly_capital_df, months)

    best = summary_df.iloc[0]
    log(
        "Best config: "
        f"{best['config_id']} | return {best['total_return_pct']:.2f}% | "
        f"Sharpe {best['sharpe']:.2f} | MDD {best['max_drawdown_pct']:.2f}%"
    )
    log(f"Saved outputs to {OUT_DIR}")


if __name__ == "__main__":
    main()
