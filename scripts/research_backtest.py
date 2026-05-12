from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from quant_research_pipeline import (  # noqa: E402
    COST_SCENARIOS,
    DEFAULT_OUT_DIR as QUANT_OUT_DIR,
    StrategySpec,
    add_features,
    assess_strategies,
    backtest_strategy,
    instrument_contribution,
    load_daily_bars,
    metrics_for_trades,
    parameter_sensitivity,
    rare_perfect_candidates,
    save_charts,
    split_metrics,
    walk_forward_metrics,
    year_by_year_metrics,
)


DEFAULT_STRATEGY_DIR = ROOT / "research" / "strategies"
DEFAULT_OUT_DIR = ROOT / "docs" / "python_research_outputs"


def discover_strategy_files(strategy_dir: Path) -> list[Path]:
    if not strategy_dir.exists():
        return []
    return sorted(
        path
        for path in strategy_dir.glob("*.py")
        if not path.name.startswith("_") and path.name not in {"__init__.py", "template_strategy.py"}
    )


def load_strategy_module(path: Path):
    module_name = f"research_strategy_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import strategy module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def strategies_from_module(path: Path) -> list[StrategySpec]:
    module = load_strategy_module(path)
    if hasattr(module, "make_strategies"):
        strategies = module.make_strategies()
    else:
        strategies = getattr(module, "STRATEGIES", [])
    out = []
    for strategy in strategies:
        if not isinstance(strategy, StrategySpec):
            raise TypeError(f"{path} exported a non-StrategySpec object: {strategy!r}")
        out.append(strategy)
    return out


def load_research_strategies(strategy_dir: Path, selected: set[str] | None = None) -> list[StrategySpec]:
    strategies: list[StrategySpec] = []
    for path in discover_strategy_files(strategy_dir):
        for strategy in strategies_from_module(path):
            if selected and strategy.name not in selected:
                continue
            strategies.append(strategy)
    seen: set[str] = set()
    unique = []
    for strategy in strategies:
        if strategy.name in seen:
            raise ValueError(f"Duplicate strategy name: {strategy.name}")
        seen.add(strategy.name)
        unique.append(strategy)
    return unique


def write_research_report(
    out_dir: Path,
    strategies: list[StrategySpec],
    metrics_df: pd.DataFrame,
    cost_metrics_df: pd.DataFrame,
    split_df: pd.DataFrame,
    year_df: pd.DataFrame,
    walk_forward_df: pd.DataFrame,
    assessments: pd.DataFrame,
) -> None:
    top = metrics_df.sort_values(["expectancy_pct", "profit_factor"], ascending=[False, False]) if not metrics_df.empty else metrics_df
    watchlist = assessments[assessments["label"].eq("watchlist")] if not assessments.empty else assessments
    strategy_rules = "\n".join(
        f"- `{s.name}` ({s.family}): {s.entry_rule} Stop {s.stop_atr} ATR, target {s.target_atr} ATR, max hold {s.max_hold_days} sessions."
        for s in strategies
    )
    text = f"""# Python Research Backtest Report

Generated: {pd.Timestamp.now()}

This report comes from the Python research lane. It does not alter app/live behavior.

## Strategy Verdicts
{assessments.to_markdown(index=False) if not assessments.empty else "No assessments generated."}

## Top Metrics
{top.to_markdown(index=False) if not top.empty else "No trades generated."}

## Watchlist Candidates
{watchlist.to_markdown(index=False) if not watchlist.empty else "No strategy passed the current summary gates."}

## Rules Tested
{strategy_rules}

## Cost Scenarios
{cost_metrics_df.to_markdown(index=False) if not cost_metrics_df.empty else "No cost metrics generated."}

## Chronological Splits
{split_df.to_markdown(index=False) if not split_df.empty else "No split metrics generated."}

## Year By Year
{year_df.to_markdown(index=False) if not year_df.empty else "No yearly metrics generated."}

## Walk Forward Tests
{walk_forward_df[walk_forward_df["segment"].eq("test")].to_markdown(index=False) if not walk_forward_df.empty else "No walk-forward metrics generated."}

## Promotion Rule
Only copy a strategy into app/live wiring after it passes costs, OOS, year-by-year behavior, walk-forward windows, and paper trading.
"""
    (out_dir / "final_report.md").write_text(text, encoding="utf-8")


def write_promotion_candidates(out_dir: Path, assessments: pd.DataFrame, metrics_df: pd.DataFrame) -> None:
    if assessments.empty or metrics_df.empty:
        candidates = []
    else:
        passed = assessments[assessments["label"].eq("watchlist")][["strategy", "reasons"]]
        candidates = (
            passed.merge(metrics_df, left_on="strategy", right_on="strategy", how="left")
            .to_dict("records")
        )
    (out_dir / "promotion_candidates.json").write_text(
        json.dumps(candidates, indent=2, default=str),
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    selected = set(args.strategy or []) or None
    strategies = load_research_strategies(args.strategy_dir, selected)
    if not strategies:
        wanted = f" matching {sorted(selected)}" if selected else ""
        raise SystemExit(f"No Python research strategies found{wanted} in {args.strategy_dir}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loaded {len(strategies)} Python research strategy spec(s)")
    print(f"Loading daily bars from {args.daily_cache}")
    daily = load_daily_bars(args.daily_cache, refresh=args.refresh_cache)
    daily = add_features(daily)
    research_start = pd.Timestamp(daily["trade_date"].min())
    research_end = pd.Timestamp(daily["trade_date"].max())

    base_trade_frames = []
    cost_trade_frames = []
    metrics = []
    cost_metrics = []
    split_rows = []
    year_rows = []
    walk_forward_rows = []
    sensitivity_rows = []

    for strategy in strategies:
        print(f"Backtesting {strategy.name}")
        base_trades = pd.DataFrame()
        for scenario, cfg in COST_SCENARIOS.items():
            trades = backtest_strategy(
                daily,
                strategy,
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
            scenario_metrics = metrics_for_trades(trades, strategy.name)
            scenario_metrics["cost_scenario"] = scenario
            scenario_metrics["cost_bps_side"] = cfg["cost_bps_side"]
            scenario_metrics["slippage_bps_side"] = cfg["slippage_bps_side"]
            cost_metrics.append(scenario_metrics)
            if scenario == "base":
                base_trades = trades

        if not base_trades.empty:
            base_trade_frames.append(base_trades)
        metrics.append(metrics_for_trades(base_trades, strategy.name))
        for row in split_metrics(base_trades, research_start, research_end):
            row["parent_strategy"] = strategy.name
            split_rows.append(row)
        for row in year_by_year_metrics(base_trades):
            row["parent_strategy"] = strategy.name
            year_rows.append(row)
        for row in walk_forward_metrics(base_trades, research_start, research_end):
            row["parent_strategy"] = strategy.name
            walk_forward_rows.append(row)
        if not args.skip_sensitivity:
            sensitivity = parameter_sensitivity(daily, strategy)
            sensitivity["parent_strategy"] = strategy.name
            sensitivity_rows.append(sensitivity)

    trade_log = pd.concat(base_trade_frames, ignore_index=True) if base_trade_frames else pd.DataFrame()
    cost_trade_log = pd.concat(cost_trade_frames, ignore_index=True) if cost_trade_frames else pd.DataFrame()
    metrics_df = pd.DataFrame(metrics)
    cost_metrics_df = pd.DataFrame(cost_metrics)
    split_df = pd.DataFrame(split_rows)
    year_df = pd.DataFrame(year_rows)
    walk_forward_df = pd.DataFrame(walk_forward_rows)
    sensitivity_df = pd.concat(sensitivity_rows, ignore_index=True) if sensitivity_rows else pd.DataFrame()
    contribution_df = instrument_contribution(trade_log)
    rare_df = rare_perfect_candidates(trade_log)
    assessments = assess_strategies(metrics_df, cost_metrics_df, split_df)

    trade_log.to_csv(args.out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(args.out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics_df.to_csv(args.out_dir / "strategy_metrics.csv", index=False)
    cost_metrics_df.to_csv(args.out_dir / "cost_scenario_metrics.csv", index=False)
    split_df.to_csv(args.out_dir / "split_metrics.csv", index=False)
    year_df.to_csv(args.out_dir / "year_by_year.csv", index=False)
    walk_forward_df.to_csv(args.out_dir / "walk_forward.csv", index=False)
    sensitivity_df.to_csv(args.out_dir / "parameter_sensitivity.csv", index=False)
    contribution_df.to_csv(args.out_dir / "instrument_contribution.csv", index=False)
    rare_df.to_csv(args.out_dir / "rare_perfect_candidates.csv", index=False)
    assessments.to_csv(args.out_dir / "strategy_assessments.csv", index=False)
    write_promotion_candidates(args.out_dir, assessments, metrics_df)
    write_research_report(args.out_dir, strategies, metrics_df, cost_metrics_df, split_df, year_df, walk_forward_df, assessments)
    save_charts(args.out_dir, trade_log, metrics_df, year_df)

    print(f"Done. Python research outputs saved to {args.out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Python strategy modules against the cached research backtester.")
    parser.add_argument("--strategy-dir", type=Path, default=DEFAULT_STRATEGY_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--daily-cache", type=Path, default=QUANT_OUT_DIR / "daily_bars_cache.parquet")
    parser.add_argument("--strategy", action="append", help="Run one strategy name. Can be supplied multiple times.")
    parser.add_argument("--max-trades-per-day", type=int, default=20)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--skip-sensitivity", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
