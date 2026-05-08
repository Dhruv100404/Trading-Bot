from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from quant_research_pipeline import (
    COST_SCENARIOS,
    DEFAULT_OUT_DIR,
    add_features,
    instrument_contribution,
    load_daily_bars,
    metrics_for_trades,
    split_metrics,
    walk_forward_metrics,
    year_by_year_metrics,
)


OUT_DIR = DEFAULT_OUT_DIR / "bamboo_mtf_breakout"


@dataclass(frozen=True)
class BambooSpec:
    name: str
    description: str
    risk_multiple: float
    max_hold_days: int
    signal_fn: object


def add_bamboo_features(raw_daily: pd.DataFrame) -> pd.DataFrame:
    df = add_features(raw_daily)
    df = df.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)

    for n in [120, 252, 504, 756]:
        df[f"prior_high{n}"] = prior_high.groupby(df["symbol"]).rolling(n, min_periods=max(60, n // 2)).max().reset_index(level=0, drop=True)
        df[f"prior_low{n}"] = prior_low.groupby(df["symbol"]).rolling(n, min_periods=max(60, n // 2)).min().reset_index(level=0, drop=True)

    range_span = (df["prior_high252"] - df["prior_low252"]).replace(0, np.nan)
    df["range_position_52w"] = (df["close"] - df["prior_low252"]) / range_span
    df["close_loc"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["ema20_dist_atr"] = (df["close"] - df["ema20"]) / df["atr14"].replace(0, np.nan)

    df["base_breakout_52w"] = df["close"] > df["prior_high252"]
    df["base_breakout_2y"] = df["close"] > df["prior_high504"]
    df["base_breakout_3y"] = df["close"] > df["prior_high756"]
    for col in ["base_breakout_52w", "base_breakout_2y", "base_breakout_3y"]:
        df[f"{col}_recent20"] = (
            df[col]
            .astype(float)
            .groupby(df["symbol"])
            .rolling(20, min_periods=1)
            .max()
            .reset_index(level=0, drop=True)
            .fillna(0)
            .astype(bool)
        )

    df["trend_ok"] = (df["close"] > df["sma50"]) & (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"] * 0.98)
    df["liquid_ok"] = (df["close"] >= 50) & (df["vol20"] >= 100_000) & (df["volume"] > 0)
    df["daily_breakout_20"] = df["close"] > df["prior_high20"]
    df["daily_breakout_55"] = df["close"] > df["prior_high55"]
    df["volume_ok"] = df["relvol"] >= 1.1
    df["strong_close_ok"] = df["close_loc"] >= 0.65
    df["range_leader_ok"] = df["range_position_52w"] >= 0.75
    df["gap_ok"] = df["gap_pct"].fillna(0).between(-3, 3)
    df["no_chase_ok"] = df["ema20_dist_atr"] <= 2.5
    return df


def common_signal(d: pd.DataFrame) -> pd.Series:
    return (
        d["trend_ok"]
        & d["liquid_ok"]
        & d["range_leader_ok"]
        & d["daily_breakout_20"]
        & d["volume_ok"]
        & d["strong_close_ok"]
        & d["gap_ok"]
        & d["no_chase_ok"]
    )


def make_bamboo_specs() -> list[BambooSpec]:
    return [
        BambooSpec(
            "bamboo_52w_daily_breakout_2r",
            "52-week leadership plus daily prior-20-day breakout, candle-low stop, 2R target.",
            2.0,
            15,
            lambda d: common_signal(d) & d["base_breakout_52w_recent20"],
        ),
        BambooSpec(
            "bamboo_52w_daily_breakout_3r",
            "52-week leadership plus daily prior-20-day breakout, candle-low stop, 3R target.",
            3.0,
            20,
            lambda d: common_signal(d) & d["base_breakout_52w_recent20"],
        ),
        BambooSpec(
            "bamboo_2y_base_breakout_2r",
            "Recent 2-year base breakout plus daily prior-20-day breakout, candle-low stop, 2R target.",
            2.0,
            15,
            lambda d: common_signal(d) & d["base_breakout_2y_recent20"],
        ),
        BambooSpec(
            "bamboo_2y_base_breakout_3r",
            "Recent 2-year base breakout plus daily prior-20-day breakout, candle-low stop, 3R target.",
            3.0,
            20,
            lambda d: common_signal(d) & d["base_breakout_2y_recent20"],
        ),
        BambooSpec(
            "bamboo_3y_base_breakout_2r",
            "Recent 3-year base breakout plus daily prior-20-day breakout, candle-low stop, 2R target.",
            2.0,
            15,
            lambda d: common_signal(d) & d["base_breakout_3y_recent20"],
        ),
        BambooSpec(
            "bamboo_55d_continuation_no_chase_2r",
            "Recent 52-week breakout, stronger prior-55-day daily continuation, under 2 ATR above EMA20, 2R target.",
            2.0,
            15,
            lambda d: common_signal(d) & d["base_breakout_52w_recent20"] & d["daily_breakout_55"] & (d["ema20_dist_atr"] <= 2.0),
        ),
    ]


def backtest_bamboo(
    df: pd.DataFrame,
    spec: BambooSpec,
    max_trades_per_day: int = 10,
    cost_bps_side: float = 8,
    slippage_bps_side: float = 5,
) -> pd.DataFrame:
    signal = spec.signal_fn(df).fillna(False)
    candidates = df.loc[signal & df["next_open"].notna() & df["atr14"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()

    candidates["rank_score"] = (
        candidates["relvol"].fillna(0)
        + candidates["range_position_52w"].fillna(0)
        + candidates["close_loc"].fillna(0)
        - candidates["ema20_dist_atr"].fillna(0).clip(lower=0) * 0.10
    )
    candidates = candidates.sort_values(["trade_date", "rank_score"], ascending=[True, False])
    candidates = candidates.groupby("trade_date", group_keys=False).head(max_trades_per_day)

    by_symbol = {sym: sdf.reset_index(drop=True) for sym, sdf in df.groupby("symbol", sort=False)}
    last_exit_by_symbol: dict[str, pd.Timestamp] = {}
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    trades = []

    for row in candidates.itertuples(index=False):
        sdf = by_symbol[row.symbol]
        idx_arr = np.flatnonzero(sdf["trade_date"].values == np.datetime64(row.trade_date))
        if len(idx_arr) == 0:
            continue
        signal_idx = int(idx_arr[0])
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue

        entry_date = pd.Timestamp(sdf.at[entry_idx, "trade_date"])
        if row.symbol in last_exit_by_symbol and entry_date <= last_exit_by_symbol[row.symbol]:
            continue

        entry = float(sdf.at[entry_idx, "open"])
        stop = float(row.low)
        if entry <= 0 or stop <= 0 or stop >= entry:
            continue
        risk = entry - stop
        if risk / entry > 0.12:
            continue

        target = entry + spec.risk_multiple * risk
        exit_price = None
        exit_date = None
        exit_reason = "time"
        hold = 0
        for j in range(entry_idx, min(entry_idx + spec.max_hold_days, len(sdf))):
            hold = j - entry_idx + 1
            low = float(sdf.at[j, "low"])
            high = float(sdf.at[j, "high"])
            close = float(sdf.at[j, "close"])
            exit_date = pd.Timestamp(sdf.at[j, "trade_date"])
            if low <= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if high >= target:
                exit_price = target
                exit_reason = "target"
                break
            exit_price = close

        if exit_price is None or exit_date is None:
            continue

        gross_ret = exit_price / entry - 1
        net_ret = gross_ret - round_cost
        trades.append(
            {
                "strategy": spec.name,
                "family": "Bamboo MTF Breakout",
                "symbol": row.symbol,
                "signal_date": pd.Timestamp(row.trade_date),
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry": entry,
                "exit": exit_price,
                "stop": stop,
                "target": target,
                "risk_multiple": spec.risk_multiple,
                "exit_reason": exit_reason,
                "hold_days": hold,
                "gross_return": gross_ret,
                "net_return": net_ret,
                "year": entry_date.year,
                "signal_close": float(row.close),
                "trigger_high": float(row.prior_high20),
                "relvol": float(row.relvol),
                "range_position_52w": float(row.range_position_52w),
                "ema20_dist_atr": float(row.ema20_dist_atr),
            }
        )
        last_exit_by_symbol[row.symbol] = exit_date

    return pd.DataFrame(trades)


def latest_signal_rows(df: pd.DataFrame, specs: list[BambooSpec], top_n: int = 4) -> tuple[pd.DataFrame, pd.DataFrame]:
    latest_date = pd.Timestamp(df["trade_date"].max())
    latest = df[df["trade_date"].eq(latest_date)].copy()
    rows = []

    for spec in specs:
        signal = spec.signal_fn(latest).fillna(False)
        part = latest.loc[signal].copy()
        if part.empty:
            continue
        part["strategy"] = spec.name
        part["risk_multiple"] = spec.risk_multiple
        part["signal_date"] = latest_date
        part["planned_entry"] = "next_open_or_live_confirmation"
        part["stop"] = part["low"]
        part["risk_pct_vs_close"] = (part["close"] - part["stop"]) / part["close"] * 100
        part["target_from_close"] = part["close"] + spec.risk_multiple * (part["close"] - part["stop"])
        part["rank_score"] = (
            part["relvol"].fillna(0)
            + part["range_position_52w"].fillna(0)
            + part["close_loc"].fillna(0)
            - part["risk_pct_vs_close"].fillna(0) * 0.03
        )
        rows.append(
            part[
                [
                    "strategy",
                    "symbol",
                    "signal_date",
                    "planned_entry",
                    "close",
                    "stop",
                    "target_from_close",
                    "risk_multiple",
                    "risk_pct_vs_close",
                    "relvol",
                    "range_position_52w",
                    "ema20_dist_atr",
                    "prior_high20",
                    "prior_high55",
                    "gap_pct",
                    "close_loc",
                    "rank_score",
                ]
            ]
        )

    if not rows:
        empty = pd.DataFrame()
        return empty, empty

    all_signals = pd.concat(rows, ignore_index=True).sort_values(["strategy", "rank_score"], ascending=[True, False])
    top_unique = (
        all_signals.sort_values(["rank_score", "relvol", "range_position_52w"], ascending=False)
        .drop_duplicates("symbol")
        .head(top_n)
        .reset_index(drop=True)
    )
    return all_signals.reset_index(drop=True), top_unique


def assessment(metrics: pd.DataFrame, cost_metrics: pd.DataFrame, split_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in metrics.to_dict("records"):
        strategy = row["strategy"]
        trades = int(row.get("trades") or 0)
        reasons = []
        label = "watchlist"
        pf = row.get("profit_factor", 0)
        pf_num = float(pf) if pf != "inf" else math.inf
        base_expectancy = float(row.get("expectancy_pct") or 0)
        stress = cost_metrics[(cost_metrics["strategy"] == strategy) & (cost_metrics["cost_scenario"] == "stress")]
        stress_pf = float(stress.iloc[0].get("profit_factor") or 0) if not stress.empty and stress.iloc[0].get("profit_factor") != "inf" else math.inf
        split = split_df[split_df["parent_strategy"] == strategy]
        validation = split[split["strategy"].eq("validation")]
        out_sample = split[split["strategy"].eq("out_of_sample")]

        if trades < 100:
            reasons.append("fewer than 100 base-cost trades")
        if pf_num < 1.25:
            reasons.append("base-cost profit factor below 1.25")
        if stress_pf < 1.05:
            reasons.append("stress-cost profit factor below 1.05")
        if base_expectancy <= 0:
            reasons.append("base-cost expectancy is not positive")
        if not validation.empty and float(validation.iloc[0].get("expectancy_pct") or 0) <= 0:
            reasons.append("validation expectancy is not positive")
        if not out_sample.empty and float(out_sample.iloc[0].get("expectancy_pct") or 0) <= 0:
            reasons.append("out-of-sample expectancy is not positive")
        if reasons:
            label = "reject"
        rows.append({"strategy": strategy, "label": label, "reasons": "; ".join(reasons) if reasons else "Passed summary gates; paper-test before any live use."})
    return pd.DataFrame(rows)


def write_report(out_dir: Path, metrics: pd.DataFrame, cost_metrics: pd.DataFrame, split_df: pd.DataFrame, year_df: pd.DataFrame, wf_df: pd.DataFrame, contribution: pd.DataFrame, assessments: pd.DataFrame, specs: list[BambooSpec]) -> None:
    rules = "\n".join(
        f"- `{spec.name}`: {spec.description} Entry next session open. Stop is signal candle low. Target {spec.risk_multiple}R. Max hold {spec.max_hold_days} sessions."
        for spec in specs
    )
    watch = assessments[assessments["label"].eq("watchlist")]
    verdict = "No Bamboo MTF variant passed the summary gates." if watch.empty else watch.to_markdown(index=False)
    report = f"""# Bamboo MTF Breakout Backtest

Generated: {pd.Timestamp.now()}

## Verdict

{verdict}

## Strategy Rules

{rules}

## Base-Cost Metrics

{metrics.to_markdown(index=False) if not metrics.empty else "No trades generated."}

## Cost Scenario Metrics

{cost_metrics.to_markdown(index=False) if not cost_metrics.empty else "No cost metrics generated."}

## In-Sample, Validation, Out-Of-Sample

{split_df.to_markdown(index=False) if not split_df.empty else "No split metrics generated."}

## Year By Year

{year_df.to_markdown(index=False) if not year_df.empty else "No yearly metrics generated."}

## Walk Forward Test Windows

{wf_df[wf_df["segment"].eq("test")].to_markdown(index=False) if not wf_df.empty else "No walk-forward metrics generated."}

## Top Symbol Contribution

{contribution.head(40).to_markdown(index=False) if not contribution.empty else "No contribution data generated."}

## Assessment

{assessments.to_markdown(index=False) if not assessments.empty else "No assessment generated."}

## Caveats

- This is a daily-bar backtest using next-session open entries.
- Stop and target sequencing is conservative: if stop and target both appear inside the same daily candle, stop wins.
- The higher-timeframe breakout is approximated with 52-week, 2-year, and 3-year daily resistance proxies.
- The daily swing high is approximated with prior 20-day or 55-day highs to avoid lookahead.
- Keep any passing variant paper-test only until forward slippage and signal timing are verified.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--max-trades-per-day", type=int, default=10)
    parser.add_argument("--top-latest", type=int, default=4)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    daily = load_daily_bars(DEFAULT_OUT_DIR / "daily_bars_cache.parquet", refresh=args.refresh_cache)
    daily = add_bamboo_features(daily)
    research_start = pd.Timestamp(daily["trade_date"].min())
    research_end = pd.Timestamp(daily["trade_date"].max())
    specs = make_bamboo_specs()
    latest_signals, top_latest = latest_signal_rows(daily, specs, top_n=args.top_latest)

    base_trade_frames = []
    cost_trade_frames = []
    metrics_rows = []
    cost_metric_rows = []
    split_rows = []
    year_rows = []
    wf_rows = []

    for spec in specs:
        print(f"Backtesting {spec.name}")
        base_trades = pd.DataFrame()
        for scenario, cfg in COST_SCENARIOS.items():
            trades = backtest_bamboo(
                daily,
                spec,
                max_trades_per_day=args.max_trades_per_day,
                cost_bps_side=cfg["cost_bps_side"],
                slippage_bps_side=cfg["slippage_bps_side"],
            )
            if not trades.empty:
                trades = trades.copy()
                trades["cost_scenario"] = scenario
                trades["cost_bps_side"] = cfg["cost_bps_side"]
                trades["slippage_bps_side"] = cfg["slippage_bps_side"]
                cost_trade_frames.append(trades)
            row = metrics_for_trades(trades, spec.name)
            row["cost_scenario"] = scenario
            row["cost_bps_side"] = cfg["cost_bps_side"]
            row["slippage_bps_side"] = cfg["slippage_bps_side"]
            cost_metric_rows.append(row)
            if scenario == "base":
                base_trades = trades

        if not base_trades.empty:
            base_trade_frames.append(base_trades)
        metrics_rows.append(metrics_for_trades(base_trades, spec.name))
        for row in split_metrics(base_trades, research_start, research_end):
            row["parent_strategy"] = spec.name
            split_rows.append(row)
        for row in year_by_year_metrics(base_trades):
            row["parent_strategy"] = spec.name
            year_rows.append(row)
        for row in walk_forward_metrics(base_trades, research_start, research_end):
            row["parent_strategy"] = spec.name
            wf_rows.append(row)

    trade_log = pd.concat(base_trade_frames, ignore_index=True) if base_trade_frames else pd.DataFrame()
    cost_trade_log = pd.concat(cost_trade_frames, ignore_index=True) if cost_trade_frames else pd.DataFrame()
    metrics_df = pd.DataFrame(metrics_rows)
    cost_metrics_df = pd.DataFrame(cost_metric_rows)
    split_df = pd.DataFrame(split_rows)
    year_df = pd.DataFrame(year_rows)
    wf_df = pd.DataFrame(wf_rows)
    contribution_df = instrument_contribution(trade_log)
    assessments = assessment(metrics_df, cost_metrics_df, split_df)

    trade_log.to_csv(args.out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(args.out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics_df.to_csv(args.out_dir / "strategy_metrics.csv", index=False)
    cost_metrics_df.to_csv(args.out_dir / "cost_scenario_metrics.csv", index=False)
    split_df.to_csv(args.out_dir / "split_metrics.csv", index=False)
    year_df.to_csv(args.out_dir / "year_by_year.csv", index=False)
    wf_df.to_csv(args.out_dir / "walk_forward.csv", index=False)
    contribution_df.to_csv(args.out_dir / "instrument_contribution.csv", index=False)
    assessments.to_csv(args.out_dir / "strategy_assessments.csv", index=False)
    latest_signals.to_csv(args.out_dir / "latest_signals.csv", index=False)
    top_latest.to_csv(args.out_dir / "top_latest_signals.csv", index=False)
    write_report(args.out_dir, metrics_df, cost_metrics_df, split_df, year_df, wf_df, contribution_df, assessments, specs)
    print(f"Done. Outputs saved to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
