#!/usr/bin/env python3
"""Focused validation for a +5% target with a maximum 10-session hold.

The candidate signals come from the existing short-hold result/volume research.
This script changes only the exit model and evaluates fixed stops without using
future prices to rank entries. Same-day target/stop ambiguity is resolved in
favour of the stop (the conservative assumption).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/app") if Path("/app/docs").exists() else Path(__file__).resolve().parents[1]
TRADE_LOG = ROOT / "docs/swing_short_hold_review/trade_log.csv"
DAILY_BARS = ROOT / "docs/complex_strategy_tuning_lab/daily_bars_cache.parquet"
OUTPUT_DIR = ROOT / "docs/five_percent_swing_review"

TARGET_PCT = 5.0
MAX_HOLD_SESSIONS = 10
ROUND_TRIP_COST_PCT = 0.26
STOP_GRID = (2.0, 2.5, 3.0, 4.0, 5.0)

SPLITS = {
    "in_sample": (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-03-28")),
    "validation": (pd.Timestamp("2024-03-29"), pd.Timestamp("2025-04-27")),
    "out_of_sample": (pd.Timestamp("2025-04-28"), pd.Timestamp("2026-05-28")),
}


def load_inputs() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    signals = pd.read_csv(
        TRADE_LOG,
        usecols=[
            "strategy",
            "symbol",
            "signal_date",
            "entry_date",
            "entry",
            "relvol50",
            "close_location",
            "rs60_rank",
            "market_breadth200",
            "rank_score",
            "result_titles",
        ],
        parse_dates=["signal_date", "entry_date"],
    )
    signals = signals[
        signals["strategy"].str.endswith("_h10")
        & ~signals["strategy"].str.contains("no_result", regex=False)
    ].drop_duplicates(["strategy", "symbol", "entry_date"])

    symbols = set(signals["symbol"].dropna().astype(str))
    bars = pd.read_parquet(
        DAILY_BARS,
        columns=["symbol", "trade_date", "open", "high", "low", "close"],
    )
    bars = bars[bars["symbol"].isin(symbols)].copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars = bars.sort_values(["symbol", "trade_date"])
    grouped = {
        symbol: frame.reset_index(drop=True)
        for symbol, frame in bars.groupby("symbol", sort=False)
    }
    return signals, grouped


def simulate_one(
    signal: object,
    bars: pd.DataFrame,
    stop_pct: float,
) -> dict[str, object] | None:
    dates = bars["trade_date"].to_numpy(dtype="datetime64[ns]")
    entry_date = np.datetime64(pd.Timestamp(signal.entry_date), "ns")
    start = int(np.searchsorted(dates, entry_date))
    if start >= len(bars) or dates[start] != entry_date:
        return None

    entry = float(signal.entry)
    if not np.isfinite(entry) or entry <= 0:
        entry = float(bars.iloc[start]["open"])
    target = entry * (1.0 + TARGET_PCT / 100.0)
    stop = entry * (1.0 - stop_pct / 100.0)
    end = min(start + MAX_HOLD_SESSIONS, len(bars))

    exit_price = float(bars.iloc[end - 1]["close"])
    exit_reason = "TIME"
    exit_idx = end - 1
    for idx in range(start, end):
        bar = bars.iloc[idx]
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])

        if open_price <= stop:
            exit_price, exit_reason, exit_idx = open_price, "STOP_GAP", idx
            break
        if open_price >= target:
            exit_price, exit_reason, exit_idx = target, "TARGET_GAP", idx
            break

        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            exit_price, exit_reason, exit_idx = stop, "STOP_SAME_DAY", idx
            break
        if hit_stop:
            exit_price, exit_reason, exit_idx = stop, "STOP", idx
            break
        if hit_target:
            exit_price, exit_reason, exit_idx = target, "TARGET", idx
            break

    gross_return = exit_price / entry - 1.0
    net_return = gross_return - ROUND_TRIP_COST_PCT / 100.0
    return {
        "strategy": signal.strategy,
        "stop_pct": stop_pct,
        "symbol": signal.symbol,
        "signal_date": pd.Timestamp(signal.signal_date),
        "entry_date": pd.Timestamp(signal.entry_date),
        "exit_date": pd.Timestamp(bars.iloc[exit_idx]["trade_date"]),
        "entry": entry,
        "target": target,
        "stop": stop,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "hold_sessions": exit_idx - start + 1,
        "gross_return": gross_return,
        "net_return": net_return,
        "relvol50": float(signal.relvol50),
        "close_location": float(signal.close_location),
        "rs60_rank": float(signal.rs60_rank),
        "market_breadth200": float(signal.market_breadth200),
        "rank_score": float(signal.rank_score),
        "result_titles": "" if pd.isna(signal.result_titles) else str(signal.result_titles),
    }


def simulate_grid(
    signals: pd.DataFrame,
    grouped_bars: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for stop_pct in STOP_GRID:
        for signal in signals.itertuples(index=False):
            bars = grouped_bars.get(str(signal.symbol))
            if bars is None:
                continue
            trade = simulate_one(signal, bars, stop_pct)
            if trade is not None:
                rows.append(trade)
    return pd.DataFrame(rows)


def metric_row(trades: pd.DataFrame) -> dict[str, float | int]:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "target_hit_pct": 0.0,
            "stop_exit_pct": 0.0,
            "time_exit_pct": 0.0,
            "expectancy_pct": 0.0,
            "median_return_pct": 0.0,
            "profit_factor": 0.0,
            "avg_hold_sessions": 0.0,
            "max_drawdown_pct": 0.0,
        }
    ordered = trades.sort_values(["exit_date", "symbol"])
    returns = ordered["net_return"].astype(float)
    gains = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    # Ten equal slots: each trade contributes one tenth of portfolio capital.
    equity = (1.0 + returns / 10.0).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    reasons = ordered["exit_reason"].astype(str)
    return {
        "trades": int(len(ordered)),
        "win_rate_pct": round(float((returns > 0).mean() * 100.0), 2),
        "target_hit_pct": round(float(reasons.str.startswith("TARGET").mean() * 100.0), 2),
        "stop_exit_pct": round(float(reasons.str.startswith("STOP").mean() * 100.0), 2),
        "time_exit_pct": round(float((reasons == "TIME").mean() * 100.0), 2),
        "expectancy_pct": round(float(returns.mean() * 100.0), 3),
        "median_return_pct": round(float(returns.median() * 100.0), 3),
        "profit_factor": round(gains / losses, 3) if losses > 0 else float("inf"),
        "avg_hold_sessions": round(float(ordered["hold_sessions"].mean()), 2),
        "max_drawdown_pct": round(float(drawdown.min() * 100.0), 3),
    }


def build_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (strategy, stop_pct), group in trades.groupby(["strategy", "stop_pct"]):
        rows.append(
            {
                "strategy": strategy,
                "stop_pct": stop_pct,
                "split": "full",
                **metric_row(group),
            }
        )
        for split, (start, end) in SPLITS.items():
            part = group[group["entry_date"].between(start, end)]
            rows.append(
                {
                    "strategy": strategy,
                    "stop_pct": stop_pct,
                    "split": split,
                    **metric_row(part),
                }
            )
    return pd.DataFrame(rows)


def select_without_oos(metrics: pd.DataFrame) -> tuple[str, float, pd.DataFrame]:
    train = metrics[metrics["split"].isin(["in_sample", "validation"])].copy()
    wide = train.pivot(index=["strategy", "stop_pct"], columns="split")
    candidates = pd.DataFrame(index=wide.index).reset_index()
    candidates["in_sample_expectancy_pct"] = wide["expectancy_pct"]["in_sample"].to_numpy()
    candidates["validation_expectancy_pct"] = wide["expectancy_pct"]["validation"].to_numpy()
    candidates["validation_profit_factor"] = wide["profit_factor"]["validation"].to_numpy()
    candidates["validation_trades"] = wide["trades"]["validation"].to_numpy()
    candidates = candidates[
        (candidates["in_sample_expectancy_pct"] > 0)
        & (candidates["validation_expectancy_pct"] > 0)
        & (candidates["validation_profit_factor"] > 1)
        & (candidates["validation_trades"] >= 20)
    ].copy()
    if candidates.empty:
        raise RuntimeError("No configuration passed the training/validation guardrails")
    candidates["selection_score"] = candidates[
        ["in_sample_expectancy_pct", "validation_expectancy_pct"]
    ].min(axis=1) + 0.1 * np.log(candidates["validation_profit_factor"].clip(lower=1.0))
    candidates = candidates.sort_values(
        ["selection_score", "validation_trades"], ascending=[False, False]
    )
    best = candidates.iloc[0]
    return str(best["strategy"]), float(best["stop_pct"]), candidates


def select_audit_survivor(metrics: pd.DataFrame) -> tuple[str, float]:
    split_rows = metrics[metrics["split"].isin(SPLITS)].copy()
    wide = split_rows.pivot(index=["strategy", "stop_pct"], columns="split")
    candidates = pd.DataFrame(index=wide.index).reset_index()
    for split in SPLITS:
        candidates[f"{split}_expectancy_pct"] = wide["expectancy_pct"][split].to_numpy()
    candidates["oos_profit_factor"] = wide["profit_factor"]["out_of_sample"].to_numpy()
    candidates["oos_trades"] = wide["trades"]["out_of_sample"].to_numpy()
    expectancy_columns = [f"{split}_expectancy_pct" for split in SPLITS]
    candidates = candidates[
        (candidates[expectancy_columns] > 0).all(axis=1)
        & (candidates["oos_profit_factor"] >= 1.2)
        & (candidates["oos_trades"] >= 20)
    ].copy()
    if candidates.empty:
        raise RuntimeError("No configuration remained positive across all temporal splits")
    candidates["worst_split_expectancy_pct"] = candidates[expectancy_columns].min(axis=1)
    best = candidates.sort_values(
        ["worst_split_expectancy_pct", "oos_profit_factor"], ascending=[False, False]
    ).iloc[0]
    return str(best["strategy"]), float(best["stop_pct"])


def write_report(
    metrics: pd.DataFrame,
    candidates: pd.DataFrame,
    selected_strategy: str,
    selected_stop: float,
    audit_strategy: str,
    audit_stop: float,
    audit_trades: pd.DataFrame,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(OUTPUT_DIR / "parameter_split_metrics.csv", index=False)
    candidates.to_csv(OUTPUT_DIR / "training_validation_ranking.csv", index=False)
    audit_trades.to_csv(OUTPUT_DIR / "paper_candidate_trade_log.csv", index=False)

    selected_metrics = metrics[
        (metrics["strategy"] == selected_strategy)
        & (metrics["stop_pct"] == selected_stop)
    ].copy()
    selected_oos = selected_metrics[selected_metrics["split"] == "out_of_sample"].iloc[0]
    audit_metrics = metrics[
        (metrics["strategy"] == audit_strategy)
        & (metrics["stop_pct"] == audit_stop)
    ].copy()
    audit_oos = audit_metrics[audit_metrics["split"] == "out_of_sample"].iloc[0]
    recent = audit_trades.sort_values("entry_date").tail(10)[
        [
            "symbol",
            "signal_date",
            "entry_date",
            "exit_date",
            "entry",
            "exit_reason",
            "net_return",
            "relvol50",
            "result_titles",
        ]
    ].copy()
    recent["net_return_pct"] = (recent.pop("net_return") * 100.0).round(2)

    evidence = {
        "status": "paper_only",
        "target_pct": TARGET_PCT,
        "stop_pct": audit_stop,
        "max_hold_sessions": MAX_HOLD_SESSIONS,
        "strategy": audit_strategy,
        "metrics": audit_metrics.to_dict(orient="records"),
        "note": "This configuration was identified after the temporal audit and needs a new prospective paper sample.",
    }
    (OUTPUT_DIR / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    report = f"""# Five Percent / Ten Session Swing Review

Data window: 2021-01-01 to 2026-05-28. Entry is the next session open after a signal.

## Fixed objective

- Gross target: **+{TARGET_PCT:.1f}%**
- Maximum holding period: **{MAX_HOLD_SESSIONS} trading sessions**
- Round-trip fees/slippage: **{ROUND_TRIP_COST_PCT:.2f}%**
- Same-day target and stop: **stop first** (conservative daily-bar assumption)
- Candidate family: result/catalyst + abnormal volume + near-52-week-high or leader context

## Selected without looking at OOS

- Signal rule: `{selected_strategy}`
- Fixed stop: **-{selected_stop:.1f}%**
- The rule/stop was selected using in-sample and validation metrics only. OOS was revealed afterward.

## Temporal split results

{selected_metrics.to_markdown(index=False)}

## Unseen OOS result

- Trades: **{int(selected_oos['trades'])}**
- Target hit rate: **{selected_oos['target_hit_pct']:.2f}%**
- Net win rate: **{selected_oos['win_rate_pct']:.2f}%**
- Net expectancy per signal: **{selected_oos['expectancy_pct']:.3f}%**
- Profit factor: **{selected_oos['profit_factor']:.3f}**
- 10-slot drawdown proxy: **{selected_oos['max_drawdown_pct']:.3f}%**

The preselected model was nearly flat in OOS, so it is **not approved**.

## Paper candidate that survived the audit

- Signal rule: `{audit_strategy}`
- Stop / target / hold: **-{audit_stop:.1f}% / +{TARGET_PCT:.1f}% / {MAX_HOLD_SESSIONS} sessions**
- This was identified after examining all temporal splits. It is a new research hypothesis, not an independent OOS winner.

{audit_metrics.to_markdown(index=False)}

Audit OOS: {int(audit_oos['trades'])} trades, {audit_oos['target_hit_pct']:.2f}% target hits, {audit_oos['expectancy_pct']:.3f}% net expectancy, {audit_oos['profit_factor']:.3f} profit factor.

## Entry checklist represented by the selected signal family

1. A financial-result/corporate catalyst occurred within the encoded result window.
2. Signal-day relative volume versus 50 sessions meets the encoded `rv` threshold.
3. Price closes strongly in its daily range and is in the selected leadership/52-week-high context.
4. Enter only on the next session; never use signal-day close as the assumed fill.
5. Place the fixed stop immediately, take profit at +5%, and exit at session 10 regardless.
6. Use at most ten equal-risk slots and avoid opening overlapping positions in the same symbol.

## Recent historical examples (not current recommendations)

{recent.to_markdown(index=False)}

## Limitations

- The latest cached daily bar in this focused study is 2026-05-28, so it cannot by itself authorize a July entry.
- The candidate families were researched previously; comparing stops and variants still creates multiple-testing risk.
- Daily OHLC cannot reveal intraday order when both stop and target trade, so this test assumes the stop happened first.
- Corporate actions, execution gaps, taxes, liquidity, and live feed quality can make forward results worse.
- Paper trade the exact rule before any live order.
"""
    (OUTPUT_DIR / "report.md").write_text(report, encoding="utf-8")


def main() -> None:
    signals, grouped_bars = load_inputs()
    trades = simulate_grid(signals, grouped_bars)
    metrics = build_metrics(trades)
    selected_strategy, selected_stop, candidates = select_without_oos(metrics)
    audit_strategy, audit_stop = select_audit_survivor(metrics)
    audit_trades = trades[
        (trades["strategy"] == audit_strategy)
        & (trades["stop_pct"] == audit_stop)
    ].copy()
    write_report(
        metrics,
        candidates,
        selected_strategy,
        selected_stop,
        audit_strategy,
        audit_stop,
        audit_trades,
    )
    selected = metrics[
        (metrics["strategy"] == selected_strategy)
        & (metrics["stop_pct"] == selected_stop)
    ]
    print(f"selected={selected_strategy} stop={selected_stop:.1f}%")
    print(selected.to_string(index=False))
    print(f"audit_survivor={audit_strategy} stop={audit_stop:.1f}% (paper only)")


if __name__ == "__main__":
    main()
