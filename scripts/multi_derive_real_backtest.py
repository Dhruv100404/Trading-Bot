from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from quant_research_pipeline import COST_SCENARIOS, metrics_for_trades, split_metrics, walk_forward_metrics, year_by_year_metrics  # noqa: E402


DEFAULT_IN_DIR = ROOT / "docs" / "multi_derive_outputs"
DEFAULT_OUT_DIR = ROOT / "docs" / "multi_derive_outputs" / "real_backtest_atr_stretch_sideways"


def select_candidates(
    candidates: pd.DataFrame,
    *,
    setup: str,
    regime: str,
    min_rank_score: float,
    top_n_per_day: int,
) -> pd.DataFrame:
    selected = candidates[candidates["rank_score"] >= min_rank_score].copy()
    if setup != "all":
        selected = selected[selected["derived_setup_family"].eq(setup)]
    if regime != "all":
        selected = selected[selected["regime_label"].eq(regime)]
    selected = selected.sort_values(["trade_date", "rank_score"], ascending=[True, False])
    selected = selected.groupby("trade_date", group_keys=False).head(top_n_per_day)
    return selected.reset_index(drop=True)


def backtest_selected(
    features: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    strategy_name: str,
    stop_atr: float,
    target_atr: float,
    max_hold_days: int,
    cost_bps_side: float,
    slippage_bps_side: float,
    skip_same_symbol_overlap: bool,
) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()

    features = features.sort_values(["symbol", "trade_date"]).copy()
    by_symbol = {
        symbol: part.reset_index(drop=True)
        for symbol, part in features.groupby("symbol", sort=False)
    }
    next_allowed_signal_date: dict[str, pd.Timestamp] = {}
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    trades = []

    for row in selected.sort_values(["trade_date", "rank_score"], ascending=[True, False]).itertuples(index=False):
        symbol = row.symbol
        signal_date = pd.Timestamp(row.trade_date)
        if skip_same_symbol_overlap and signal_date < next_allowed_signal_date.get(symbol, pd.Timestamp.min):
            continue
        sdf = by_symbol.get(symbol)
        if sdf is None or sdf.empty:
            continue
        idx_arr = np.flatnonzero(sdf["trade_date"].values == np.datetime64(signal_date))
        if len(idx_arr) == 0:
            continue
        signal_idx = int(idx_arr[0])
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue

        entry_date = pd.Timestamp(sdf.at[entry_idx, "trade_date"])
        entry = float(sdf.at[entry_idx, "open"])
        atr = float(sdf.at[signal_idx, "atr14"])
        if not math.isfinite(entry) or not math.isfinite(atr) or entry <= 0 or atr <= 0:
            continue

        stop = entry - stop_atr * atr
        target = entry + target_atr * atr
        exit_price = None
        exit_date = None
        exit_reason = "time"
        hold = 0

        for j in range(entry_idx, min(entry_idx + max_hold_days, len(sdf))):
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

        gross_return = exit_price / entry - 1.0
        net_return = gross_return - round_cost
        if skip_same_symbol_overlap:
            next_allowed_signal_date[symbol] = exit_date

        trades.append(
            {
                "strategy": strategy_name,
                "family": getattr(row, "derived_setup_family", ""),
                "symbol": symbol,
                "signal_date": signal_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry": entry,
                "exit": exit_price,
                "stop": stop,
                "target": target,
                "exit_reason": exit_reason,
                "hold_days": hold,
                "gross_return": gross_return,
                "net_return": net_return,
                "year": entry_date.year,
                "rank_score": float(row.rank_score),
                "composite_alpha_score": float(row.composite_alpha_score),
                "regime_label": getattr(row, "regime_label", ""),
                "market_stress_score": float(getattr(row, "market_stress_score", 0.0)),
                "cost_bps_side": cost_bps_side,
                "slippage_bps_side": slippage_bps_side,
            }
        )

    return pd.DataFrame(trades)


def symbol_contribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby("symbol")
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: float((s > 0).mean() * 100)),
            avg_net_return_pct=("net_return", lambda s: float(s.mean() * 100)),
            total_net_return_pct=("net_return", lambda s: float(s.sum() * 100)),
        )
        .reset_index()
        .sort_values("total_net_return_pct", ascending=False)
    )


def exit_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby("exit_reason")
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: float((s > 0).mean() * 100)),
            avg_net_return_pct=("net_return", lambda s: float(s.mean() * 100)),
            total_net_return_pct=("net_return", lambda s: float(s.sum() * 100)),
            avg_hold_days=("hold_days", "mean"),
        )
        .reset_index()
        .sort_values("total_net_return_pct", ascending=False)
    )


def run_backtest_grid(args: argparse.Namespace, features: pd.DataFrame, candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics_rows = []
    trade_frames = []
    selected = select_candidates(
        candidates,
        setup=args.setup,
        regime=args.regime,
        min_rank_score=args.min_rank_score,
        top_n_per_day=args.top_n_per_day,
    )
    for stop_atr in args.stop_atr:
        for target_atr in args.target_atr:
            for max_hold_days in args.max_hold_days:
                for scenario, costs in COST_SCENARIOS.items():
                    strategy_name = (
                        f"multi_derive_{args.setup}_{args.regime}"
                        f"_rank{args.min_rank_score:g}_top{args.top_n_per_day}"
                        f"_s{stop_atr:g}_t{target_atr:g}_h{max_hold_days}_{scenario}"
                    )
                    trades = backtest_selected(
                        features,
                        selected,
                        strategy_name=strategy_name,
                        stop_atr=stop_atr,
                        target_atr=target_atr,
                        max_hold_days=max_hold_days,
                        cost_bps_side=costs["cost_bps_side"],
                        slippage_bps_side=costs["slippage_bps_side"],
                        skip_same_symbol_overlap=not args.allow_same_symbol_overlap,
                    )
                    m = metrics_for_trades(trades, strategy_name)
                    m["cost_scenario"] = scenario
                    m["stop_atr"] = stop_atr
                    m["target_atr"] = target_atr
                    m["max_hold_days"] = max_hold_days
                    m["selected_signals"] = int(len(selected))
                    metrics_rows.append(m)
                    if scenario == args.primary_cost_scenario:
                        trade_frames.append(trades)
    metrics = pd.DataFrame(metrics_rows)
    primary_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return metrics, primary_trades


def write_report(
    out_dir: Path,
    args: argparse.Namespace,
    metrics: pd.DataFrame,
    primary_trades: pd.DataFrame,
    split_df: pd.DataFrame,
    year_df: pd.DataFrame,
    walk_df: pd.DataFrame,
    exits: pd.DataFrame,
    symbols: pd.DataFrame,
) -> None:
    top = metrics.sort_values(["cost_scenario", "expectancy_pct"], ascending=[True, False]).copy()
    primary_metrics = metrics[metrics["cost_scenario"].eq(args.primary_cost_scenario)].sort_values(
        ["expectancy_pct", "profit_factor"],
        ascending=[False, False],
    )
    for frame in [top, primary_metrics, split_df, year_df, walk_df, exits, symbols]:
        for col in frame.select_dtypes(include=[float]).columns:
            frame[col] = frame[col].round(4)

    text = f"""# Multi-Derive Real Backtest

Generated: {pd.Timestamp.now()}

## Tested Slice

- Setup: `{args.setup}`
- Regime: `{args.regime}`
- Minimum rank score: `{args.min_rank_score}`
- Top N per signal day: `{args.top_n_per_day}`
- Same-symbol overlap allowed: `{args.allow_same_symbol_overlap}`
- Primary cost scenario: `{args.primary_cost_scenario}`

This is a real OHLC path backtest: signal on day D, entry at next session open, ATR stop/target, same-day stop-first sequencing, max hold, round-trip brokerage/slippage cost.

## Metrics By Cost/Exit Config

{top.to_markdown(index=False) if not top.empty else "No metrics generated."}

## Primary Scenario Metrics

{primary_metrics.to_markdown(index=False) if not primary_metrics.empty else "No primary metrics generated."}

## Chronological Splits

{split_df.to_markdown(index=False) if not split_df.empty else "No split metrics generated."}

## Year By Year

{year_df.to_markdown(index=False) if not year_df.empty else "No yearly metrics generated."}

## Walk Forward Test Windows

{walk_df[walk_df["segment"].eq("test")].to_markdown(index=False) if not walk_df.empty else "No walk-forward metrics generated."}

## Exit Summary

{exits.to_markdown(index=False) if not exits.empty else "No exits generated."}

## Top Symbol Contribution

{symbols.head(25).to_markdown(index=False) if not symbols.empty else "No symbol contribution generated."}

## Verdict

Promote only if the primary config survives base/stress costs, out-of-sample split, and walk-forward windows. Strong proxy labels are not enough.
"""
    (out_dir / "real_backtest_report.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading features from {args.features}")
    features = pd.read_parquet(args.features)
    print(f"Loading candidates from {args.candidates}")
    candidates = pd.read_csv(args.candidates, parse_dates=["trade_date"])
    features["trade_date"] = pd.to_datetime(features["trade_date"])

    metrics, primary_trades = run_backtest_grid(args, features, candidates)
    metrics.to_csv(args.out_dir / "real_backtest_metrics.csv", index=False)
    primary_trades.to_csv(args.out_dir / "real_backtest_trade_log_primary_cost.csv", index=False)

    research_start = pd.Timestamp(features["trade_date"].min())
    research_end = pd.Timestamp(features["trade_date"].max())
    if primary_trades.empty:
        split_df = pd.DataFrame()
        year_df = pd.DataFrame()
        walk_df = pd.DataFrame()
        exits = pd.DataFrame()
        symbols = pd.DataFrame()
    else:
        split_rows = []
        year_rows = []
        walk_rows = []
        for strategy, trades in primary_trades.groupby("strategy"):
            for row in split_metrics(trades, research_start, research_end):
                row["parent_strategy"] = strategy
                split_rows.append(row)
            for row in year_by_year_metrics(trades):
                row["parent_strategy"] = strategy
                year_rows.append(row)
            for row in walk_forward_metrics(trades, research_start, research_end):
                row["parent_strategy"] = strategy
                walk_rows.append(row)
        split_df = pd.DataFrame(split_rows)
        year_df = pd.DataFrame(year_rows)
        walk_df = pd.DataFrame(walk_rows)
        exits = exit_summary(primary_trades)
        symbols = symbol_contribution(primary_trades)

    split_df.to_csv(args.out_dir / "real_backtest_split_metrics.csv", index=False)
    year_df.to_csv(args.out_dir / "real_backtest_year_by_year.csv", index=False)
    walk_df.to_csv(args.out_dir / "real_backtest_walk_forward.csv", index=False)
    exits.to_csv(args.out_dir / "real_backtest_exit_summary.csv", index=False)
    symbols.to_csv(args.out_dir / "real_backtest_symbol_contribution.csv", index=False)

    manifest = {
        "generated_at": str(pd.Timestamp.now()),
        "features": str(args.features),
        "candidates": str(args.candidates),
        "setup": args.setup,
        "regime": args.regime,
        "min_rank_score": args.min_rank_score,
        "top_n_per_day": args.top_n_per_day,
        "stop_atr": args.stop_atr,
        "target_atr": args.target_atr,
        "max_hold_days": args.max_hold_days,
        "allow_same_symbol_overlap": args.allow_same_symbol_overlap,
        "primary_cost_scenario": args.primary_cost_scenario,
        "outputs": [
            "real_backtest_metrics.csv",
            "real_backtest_trade_log_primary_cost.csv",
            "real_backtest_split_metrics.csv",
            "real_backtest_year_by_year.csv",
            "real_backtest_walk_forward.csv",
            "real_backtest_exit_summary.csv",
            "real_backtest_symbol_contribution.csv",
            "real_backtest_report.md",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_report(args.out_dir, args, metrics, primary_trades, split_df, year_df, walk_df, exits, symbols)
    print(f"Done. Real backtest outputs saved to {args.out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real OHLC backtest for a multi-derived candidate slice.")
    parser.add_argument("--features", type=Path, default=DEFAULT_IN_DIR / "feature_matrix.parquet")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_IN_DIR / "candidate_matrix.csv")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--setup", default="atr_stretch_reversal")
    parser.add_argument("--regime", default="sideways")
    parser.add_argument("--min-rank-score", type=float, default=92.0)
    parser.add_argument("--top-n-per-day", type=int, default=3)
    parser.add_argument("--stop-atr", type=float, nargs="+", default=[1.2, 1.4, 1.6])
    parser.add_argument("--target-atr", type=float, nargs="+", default=[2.0, 2.5, 3.0])
    parser.add_argument("--max-hold-days", type=int, nargs="+", default=[5, 7, 10])
    parser.add_argument("--allow-same-symbol-overlap", action="store_true")
    parser.add_argument("--primary-cost-scenario", choices=sorted(COST_SCENARIOS), default="base")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
