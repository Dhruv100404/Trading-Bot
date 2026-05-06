from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from quant_research_pipeline import (
    COST_SCENARIOS,
    DEFAULT_OUT_DIR,
    DEFAULT_PARQUET_DIR,
    StrategySpec,
    add_features,
    backtest_strategy,
    chronological_cutoffs,
    instrument_contribution,
    load_daily_bars,
    metrics_for_trades,
    parameter_sensitivity,
    split_metrics,
    walk_forward_metrics,
    year_by_year_metrics,
)


SECOND_PASS_OUT_DIR = DEFAULT_OUT_DIR / "second_pass_swing"


def add_second_pass_features(raw_daily: pd.DataFrame) -> pd.DataFrame:
    df = add_features(raw_daily)
    df = df.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)

    for n in [5, 20, 60, 120]:
        df[f"ret{n}"] = df["close"] / g["close"].shift(n) - 1

    for col in ["sma20", "sma50", "sma100", "sma200", "ema20", "ema50", "ema100"]:
        df[f"{col}_slope20"] = df[col] / g[col].shift(20) - 1

    df["atr_pct"] = df["atr14"] / df["close"].replace(0, np.nan)
    df["atr_pct_med60"] = g["atr_pct"].transform(lambda s: s.rolling(60, min_periods=30).median())
    df["atr_pct_q80_252"] = g["atr_pct"].transform(lambda s: s.rolling(252, min_periods=126).quantile(0.80))
    df["range_pct_med20"] = g["range_pct"].transform(lambda s: s.rolling(20, min_periods=10).median())
    df["range_pct_q30_120"] = g["range_pct"].transform(lambda s: s.rolling(120, min_periods=60).quantile(0.30))
    df["ema20_dist_atr"] = (df["close"] - df["ema20"]) / df["atr14"].replace(0, np.nan)
    df["close_loc"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["dollar_vol20"] = df["close"] * df["vol20"]
    df["near_55d_high"] = df["close"] / df["prior_high55"].replace(0, np.nan) - 1

    df["above_sma50"] = (df["close"] > df["sma50"]).astype(float)
    df["above_sma200"] = (df["close"] > df["sma200"]).astype(float)
    market = (
        df.groupby("trade_date")
        .agg(
            mkt_ret1=("ret1", "mean"),
            mkt_breadth_sma50=("above_sma50", "mean"),
            mkt_breadth_sma200=("above_sma200", "mean"),
            mkt_median_range_pct=("range_pct", "median"),
        )
        .sort_index()
    )
    market["mkt_ret20"] = (1 + market["mkt_ret1"].fillna(0)).rolling(20, min_periods=10).apply(np.prod, raw=True) - 1
    market["mkt_ret60"] = (1 + market["mkt_ret1"].fillna(0)).rolling(60, min_periods=30).apply(np.prod, raw=True) - 1
    market["mkt_range_q80_252"] = market["mkt_median_range_pct"].rolling(252, min_periods=126).quantile(0.80)

    return df.merge(market.reset_index(), on="trade_date", how="left")


def liquidity_filter(d: pd.DataFrame) -> pd.Series:
    return (d["close"] >= 50) & (d["vol20"] >= 100_000) & (d["volume"] > 0)


def atr_base(d: pd.DataFrame) -> pd.Series:
    return (d["close"] > d["sma200"]) & ((d["ema20"] - d["close"]) > 2.5 * d["atr14"]) & (d["rsi14"] < 35)


def nr7_base(d: pd.DataFrame) -> pd.Series:
    return (d["range_pct"] <= d["range7_min"] * 1.001) & (d["close"] > d["prior_high20"]) & (d["relvol"] > 1.0)


def make_second_pass_strategies() -> list[StrategySpec]:
    return [
        StrategySpec(
            "atr_stretch_market_breadth",
            "ATR stretch regime filter",
            "ATR stretch reversal only when market breadth above SMA50 is at least 45%.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & (d["mkt_breadth_sma50"] >= 0.45),
        ),
        StrategySpec(
            "atr_stretch_market_momentum",
            "ATR stretch regime filter",
            "ATR stretch reversal only when equal-weight market 20-day return is not worse than -2%.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & (d["mkt_ret20"] > -0.02),
        ),
        StrategySpec(
            "atr_stretch_slope_quality",
            "ATR stretch trend-quality filter",
            "ATR stretch reversal only when SMA200 and EMA50 slopes are positive.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & (d["sma200_slope20"] > 0) & (d["ema50_slope20"] > 0),
        ),
        StrategySpec(
            "atr_stretch_volatility_guard",
            "ATR stretch volatility filter",
            "ATR stretch reversal only when symbol ATR percentage is below its rolling 80th percentile.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & (d["atr_pct"] < d["atr_pct_q80_252"]),
        ),
        StrategySpec(
            "atr_stretch_liquid_only",
            "ATR stretch liquidity filter",
            "ATR stretch reversal only in liquid names with price >= 50 and 20-day volume >= 100k.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & liquidity_filter(d),
        ),
        StrategySpec(
            "atr_stretch_no_crash_gap",
            "ATR stretch gap/participation filter",
            "ATR stretch reversal excluding large gap-down and abnormal volume shock days.",
            1.4,
            2.0,
            7,
            lambda d: atr_base(d) & (d["gap_pct"] > -5) & d["relvol"].between(0.5, 3.0),
        ),
        StrategySpec(
            "atr_stretch_quality_combo",
            "ATR stretch combined quality filter",
            "ATR stretch reversal with market breadth, non-crash momentum, liquidity, trend slope, and volatility guard.",
            1.4,
            2.0,
            7,
            lambda d: (
                atr_base(d)
                & (d["mkt_breadth_sma50"] >= 0.45)
                & (d["mkt_ret20"] > -0.02)
                & (d["sma200_slope20"] > 0)
                & (d["atr_pct"] < d["atr_pct_q80_252"])
                & liquidity_filter(d)
                & d["relvol"].between(0.5, 3.0)
            ),
        ),
        StrategySpec(
            "nr7_liquid_only",
            "NR7 liquidity filter",
            "NR7 breakout only in liquid names with price >= 50 and 20-day volume >= 100k.",
            1.3,
            2.6,
            8,
            lambda d: nr7_base(d) & liquidity_filter(d),
        ),
        StrategySpec(
            "nr7_trend_quality",
            "NR7 trend-quality filter",
            "NR7 breakout only with EMA20 > EMA50 > EMA100, above SMA200, and positive SMA200 slope.",
            1.3,
            2.6,
            8,
            lambda d: (
                nr7_base(d)
                & (d["ema20"] > d["ema50"])
                & (d["ema50"] > d["ema100"])
                & (d["close"] > d["sma200"])
                & (d["sma200_slope20"] > 0)
            ),
        ),
        StrategySpec(
            "nr7_market_breadth",
            "NR7 regime filter",
            "NR7 breakout only when market breadth above SMA50 is at least 50% and market 20-day return is positive.",
            1.3,
            2.6,
            8,
            lambda d: nr7_base(d) & (d["mkt_breadth_sma50"] >= 0.50) & (d["mkt_ret20"] > 0),
        ),
        StrategySpec(
            "nr7_not_extended",
            "NR7 extension filter",
            "NR7 breakout only when close is not more than 2 ATR above EMA20.",
            1.3,
            2.6,
            8,
            lambda d: nr7_base(d) & (d["ema20_dist_atr"] < 2.0),
        ),
        StrategySpec(
            "nr7_compression_quality",
            "NR7 compression filter",
            "NR7 breakout only when range is in the lower 30% of its rolling 120-day range distribution.",
            1.3,
            2.6,
            8,
            lambda d: nr7_base(d) & (d["range_pct"] <= d["range_pct_q30_120"]),
        ),
        StrategySpec(
            "nr7_55d_breakout",
            "NR7 stronger breakout filter",
            "NR7 breakout only when the daily close also clears the prior 55-day high.",
            1.3,
            2.6,
            8,
            lambda d: nr7_base(d) & (d["close"] > d["prior_high55"]),
        ),
        StrategySpec(
            "nr7_quality_combo",
            "NR7 combined quality filter",
            "NR7 breakout with trend quality, liquidity, market breadth, and no major EMA20 extension.",
            1.3,
            2.6,
            8,
            lambda d: (
                nr7_base(d)
                & liquidity_filter(d)
                & (d["ema20"] > d["ema50"])
                & (d["ema50"] > d["ema100"])
                & (d["close"] > d["sma200"])
                & (d["sma200_slope20"] > 0)
                & (d["mkt_breadth_sma50"] >= 0.45)
                & (d["ema20_dist_atr"] < 2.0)
            ),
        ),
    ]


def metric_float(row: pd.Series, key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key, default)
        if val == "inf":
            return math.inf
        return float(val)
    except Exception:
        return default


def assess_second_pass(metrics: pd.DataFrame, cost_metrics: pd.DataFrame, split_df: pd.DataFrame, walk_forward_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in metrics.to_dict("records"):
        strategy = row["strategy"]
        reasons = []
        if int(row.get("trades") or 0) < 100:
            reasons.append("fewer than 100 base-cost trades")
        if metric_float(pd.Series(row), "profit_factor") < 1.25:
            reasons.append("base-cost profit factor below 1.25")
        if metric_float(pd.Series(row), "expectancy_pct") <= 0:
            reasons.append("base-cost expectancy is not positive")

        stress = cost_metrics[(cost_metrics["strategy"] == strategy) & (cost_metrics["cost_scenario"] == "stress")]
        if stress.empty or metric_float(stress.iloc[0], "profit_factor") < 1.05:
            reasons.append("stress-cost profit factor below 1.05")

        for split_name, threshold in [("validation", 1.0), ("out_of_sample", 1.0)]:
            part = split_df[(split_df["parent_strategy"] == strategy) & (split_df["strategy"] == split_name)]
            if part.empty or metric_float(part.iloc[0], "profit_factor") < threshold:
                reasons.append(f"{split_name} profit factor below {threshold}")

        wf_test = walk_forward_df[(walk_forward_df["parent_strategy"] == strategy) & (walk_forward_df["segment"] == "test")]
        positive_windows = int((wf_test["expectancy_pct"] > 0).sum()) if not wf_test.empty else 0
        if positive_windows < 10:
            reasons.append("fewer than 10 positive walk-forward test windows")

        label = "watchlist" if not reasons else "reject"
        rows.append({
            "strategy": strategy,
            "label": label,
            "positive_walk_forward_test_windows": positive_windows,
            "reasons": "; ".join(reasons) if reasons else "Passed summary gates; still paper-test only until live slippage and signal timing are verified.",
        })
    return pd.DataFrame(rows)


def summarize_walk_forward(walk_forward_df: pd.DataFrame) -> pd.DataFrame:
    test = walk_forward_df[walk_forward_df["segment"] == "test"]
    if test.empty:
        return pd.DataFrame()
    return (
        test.groupby("parent_strategy")
        .agg(
            test_windows=("profit_factor", "size"),
            pf_mean=("profit_factor", "mean"),
            pf_min=("profit_factor", "min"),
            positive_windows=("expectancy_pct", lambda s: int((s > 0).sum())),
            avg_expectancy_pct=("expectancy_pct", "mean"),
        )
        .reset_index()
        .sort_values(["positive_windows", "pf_mean"], ascending=False)
    )


def write_rejection_report(out_dir: Path, assessments: pd.DataFrame) -> None:
    lines = ["# Second-Pass Swing Rejections", ""]
    for row in assessments.itertuples(index=False):
        lines.append(f"## {row.strategy}")
        lines.append("")
        lines.append(f"- Label: {row.label}")
        lines.append(f"- Positive walk-forward test windows: {row.positive_walk_forward_test_windows}")
        lines.append(f"- Reason: {row.reasons}")
        lines.append("")
    (out_dir / "rejected_strategies.md").write_text("\n".join(lines), encoding="utf-8")


def write_final_report(
    out_dir: Path,
    metrics: pd.DataFrame,
    cost_metrics: pd.DataFrame,
    split_df: pd.DataFrame,
    year_df: pd.DataFrame,
    walk_forward_df: pd.DataFrame,
    sensitivity_df: pd.DataFrame,
    assessments: pd.DataFrame,
) -> None:
    top = metrics.sort_values(["profit_factor", "expectancy_pct"], ascending=False)
    watchlist = assessments[assessments["label"] == "watchlist"]["strategy"].tolist()
    validated = top[top["strategy"].isin(watchlist)]
    wf_summary = summarize_walk_forward(walk_forward_df)
    final_label = (
        validated.to_markdown(index=False)
        if not validated.empty
        else "No robust second-pass swing strategy was found under the tested assumptions."
    )
    report = f"""# Second-Pass Swing Research Report

Generated: {pd.Timestamp.now()}

## Scope

This pass tested only predeclared swing refinements for the two prior watchlist families:

- ATR stretch reversal with market regime, trend slope, volatility, liquidity, and crash-gap guards.
- NR7 breakout with liquidity, trend quality, market breadth, extension, compression, and stronger-breakout guards.

No intraday entry/exit logic was used. Signals are generated after session close and entered next session open.

## Verdict

{final_label}

## Rejection Summary

{assessments.to_markdown(index=False) if not assessments.empty else 'No assessments generated.'}

## Base-Cost Metrics

{top.to_markdown(index=False) if not top.empty else 'No trades generated.'}

## Cost Scenario Metrics

{cost_metrics.to_markdown(index=False) if not cost_metrics.empty else 'No cost scenario metrics generated.'}

## 60/20/20 Split Metrics

{split_df.to_markdown(index=False) if not split_df.empty else 'No split metrics generated.'}

## Year-By-Year Metrics

{year_df.to_markdown(index=False) if not year_df.empty else 'No year-by-year metrics generated.'}

## Walk-Forward Test Summary

{wf_summary.to_markdown(index=False) if not wf_summary.empty else 'No walk-forward metrics generated.'}

## Parameter Sensitivity

{sensitivity_df.to_markdown(index=False) if not sensitivity_df.empty else 'No parameter sensitivity metrics generated.'}

## Live Trading Note

Any watchlist result is still paper-test only. This dataset has no bid/ask, no tick sequencing, unknown adjusted-vs-raw status, and possible survivorship bias.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def run_second_pass(daily: pd.DataFrame, out_dir: Path, top_sensitivity_count: int = 3) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(daily["trade_date"].min())
    end = pd.Timestamp(daily["trade_date"].max())
    strategies = make_second_pass_strategies()

    base_trade_frames = []
    cost_trade_frames = []
    metrics_rows = []
    cost_metrics_rows = []
    split_rows = []
    year_rows = []
    walk_forward_rows = []

    for spec in strategies:
        print(f"Second-pass backtest {spec.name}")
        base_trades = pd.DataFrame()
        for scenario, cfg in COST_SCENARIOS.items():
            trades = backtest_strategy(
                daily,
                spec,
                cost_bps_side=cfg["cost_bps_side"],
                slippage_bps_side=cfg["slippage_bps_side"],
            )
            if not trades.empty:
                trades = trades.copy()
                trades["cost_scenario"] = scenario
                trades["cost_bps_side"] = cfg["cost_bps_side"]
                trades["slippage_bps_side"] = cfg["slippage_bps_side"]
                cost_trade_frames.append(trades)
            m = metrics_for_trades(trades, spec.name)
            m["cost_scenario"] = scenario
            m["cost_bps_side"] = cfg["cost_bps_side"]
            m["slippage_bps_side"] = cfg["slippage_bps_side"]
            cost_metrics_rows.append(m)
            if scenario == "base":
                base_trades = trades

        if not base_trades.empty:
            base_trade_frames.append(base_trades)
        metrics_rows.append(metrics_for_trades(base_trades, spec.name))
        for m in split_metrics(base_trades, start, end):
            m["parent_strategy"] = spec.name
            split_rows.append(m)
        for m in year_by_year_metrics(base_trades):
            m["parent_strategy"] = spec.name
            year_rows.append(m)
        for m in walk_forward_metrics(base_trades, start, end):
            m["parent_strategy"] = spec.name
            walk_forward_rows.append(m)

    trade_log = pd.concat(base_trade_frames, ignore_index=True) if base_trade_frames else pd.DataFrame()
    cost_trade_log = pd.concat(cost_trade_frames, ignore_index=True) if cost_trade_frames else pd.DataFrame()
    metrics = pd.DataFrame(metrics_rows)
    cost_metrics = pd.DataFrame(cost_metrics_rows)
    split_df = pd.DataFrame(split_rows)
    year_df = pd.DataFrame(year_rows)
    walk_forward_df = pd.DataFrame(walk_forward_rows)
    contribution = instrument_contribution(trade_log)
    assessments = assess_second_pass(metrics, cost_metrics, split_df, walk_forward_df)

    sensitivity_frames = []
    for name in metrics.sort_values(["profit_factor", "expectancy_pct"], ascending=False)["strategy"].head(top_sensitivity_count):
        spec = next(s for s in strategies if s.name == name)
        sens = parameter_sensitivity(daily, spec)
        sens["parent_strategy"] = spec.name
        sensitivity_frames.append(sens)
    sensitivity = pd.concat(sensitivity_frames, ignore_index=True) if sensitivity_frames else pd.DataFrame()

    trade_log.to_csv(out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    cost_metrics.to_csv(out_dir / "cost_scenario_metrics.csv", index=False)
    split_df.to_csv(out_dir / "split_metrics.csv", index=False)
    year_df.to_csv(out_dir / "year_by_year.csv", index=False)
    walk_forward_df.to_csv(out_dir / "walk_forward.csv", index=False)
    contribution.to_csv(out_dir / "instrument_contribution.csv", index=False)
    sensitivity.to_csv(out_dir / "parameter_sensitivity.csv", index=False)
    assessments.to_csv(out_dir / "strategy_assessments.csv", index=False)
    write_rejection_report(out_dir, assessments)
    write_final_report(out_dir, metrics, cost_metrics, split_df, year_df, walk_forward_df, sensitivity, assessments)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--out-dir", type=Path, default=SECOND_PASS_OUT_DIR)
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_OUT_DIR / "daily_bars_cache.parquet")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--top-sensitivity-count", type=int, default=3)
    args = parser.parse_args()

    print("Loading daily bars")
    raw_daily = load_daily_bars(args.daily_cache, refresh=args.refresh_cache)
    print("Building second-pass features")
    daily = add_second_pass_features(raw_daily)
    run_second_pass(daily, args.out_dir, top_sensitivity_count=args.top_sensitivity_count)
    print(f"Done. Second-pass outputs saved to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
