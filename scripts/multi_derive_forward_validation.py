from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from multi_derive_real_backtest import backtest_selected, exit_summary, select_candidates, symbol_contribution  # noqa: E402
from quant_research_pipeline import COST_SCENARIOS, metrics_for_trades, year_by_year_metrics  # noqa: E402


DEFAULT_IN_DIR = ROOT / "docs" / "multi_derive_outputs"
DEFAULT_OUT_DIR = DEFAULT_IN_DIR / "forward_validation"


def proxy_quality(picked: pd.DataFrame) -> dict[str, float]:
    avg_ret = float(picked["fwd_ret_10d_pct"].mean())
    hit2 = float(picked["hit_2pct_10d"].mean())
    hit4 = float(picked["hit_4pct_10d"].mean())
    dd3 = float(picked["drawdown_3pct_10d"].mean())
    avg_mae = float(picked["mae_10d_pct"].mean())
    quality = avg_ret + hit2 * 2.0 + hit4 * 1.5 - dd3 * 1.5 + avg_mae * 0.10
    return {
        "avg_fwd_ret_10d_pct": avg_ret,
        "hit_2pct_10d": hit2,
        "hit_4pct_10d": hit4,
        "drawdown_3pct_10d": dd3,
        "avg_mfe_10d_pct": float(picked["mfe_10d_pct"].mean()),
        "avg_mae_10d_pct": avg_mae,
        "quality_score": quality,
    }


def search_train_config(args: argparse.Namespace, candidates: pd.DataFrame) -> pd.DataFrame:
    setups = ["all"] + sorted(candidates["derived_setup_family"].dropna().unique().tolist())
    regimes = ["all"] + sorted(candidates["regime_label"].dropna().unique().tolist())
    rows = []
    for setup in setups:
        for regime in regimes:
            for min_score in args.min_scores:
                for top_n in args.top_ns:
                    picked = select_candidates(
                        candidates,
                        setup=setup,
                        regime=regime,
                        min_rank_score=min_score,
                        top_n_per_day=top_n,
                    )
                    picked = picked[picked["fwd_ret_10d_pct"].notna()].copy()
                    if len(picked) < args.min_train_signals:
                        continue
                    stats = proxy_quality(picked)
                    rows.append(
                        {
                            "setup": setup,
                            "regime": regime,
                            "min_rank_score": min_score,
                            "top_n_per_day": top_n,
                            "train_days": int(picked["trade_date"].nunique()),
                            "train_signals": int(len(picked)),
                            **stats,
                        }
                    )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["quality_score", "avg_fwd_ret_10d_pct", "train_signals"],
        ascending=[False, False, False],
    )


def segment_candidates(candidates: pd.DataFrame, args: argparse.Namespace) -> dict[str, pd.DataFrame]:
    train_end = pd.Timestamp(args.train_end)
    validation_start = pd.Timestamp(args.validation_start)
    forward_start = pd.Timestamp(args.forward_start)
    return {
        "train": candidates[candidates["trade_date"].le(train_end)].copy(),
        "validation": candidates[
            candidates["trade_date"].ge(validation_start) & candidates["trade_date"].lt(forward_start)
        ].copy(),
        "forward": candidates[candidates["trade_date"].ge(forward_start)].copy(),
    }


def run_segment_backtests(
    args: argparse.Namespace,
    features: pd.DataFrame,
    segments: dict[str, pd.DataFrame],
    config: dict[str, object],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows = []
    trade_frames = []
    for segment, frame in segments.items():
        selected = select_candidates(
            frame,
            setup=str(config["setup"]),
            regime=str(config["regime"]),
            min_rank_score=float(config["min_rank_score"]),
            top_n_per_day=int(config["top_n_per_day"]),
        )
        for scenario, costs in COST_SCENARIOS.items():
            strategy_name = (
                f"forward_validated_{config['setup']}_{config['regime']}"
                f"_rank{float(config['min_rank_score']):g}_top{int(config['top_n_per_day'])}_{segment}_{scenario}"
            )
            trades = backtest_selected(
                features,
                selected,
                strategy_name=strategy_name,
                stop_atr=args.stop_atr,
                target_atr=args.target_atr,
                max_hold_days=args.max_hold_days,
                cost_bps_side=costs["cost_bps_side"],
                slippage_bps_side=costs["slippage_bps_side"],
                skip_same_symbol_overlap=not args.allow_same_symbol_overlap,
            )
            metric = metrics_for_trades(trades, strategy_name)
            metric["segment"] = segment
            metric["cost_scenario"] = scenario
            metric["selected_signals"] = int(len(selected))
            metric["stop_atr"] = args.stop_atr
            metric["target_atr"] = args.target_atr
            metric["max_hold_days"] = args.max_hold_days
            metric_rows.append(metric)
            if scenario == args.primary_cost_scenario:
                trades = trades.copy()
                trades["validation_segment"] = segment
                trade_frames.append(trades)
    metrics = pd.DataFrame(metric_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return metrics, trades


def write_report(
    out_dir: Path,
    args: argparse.Namespace,
    train_grid: pd.DataFrame,
    config: dict[str, object],
    metrics: pd.DataFrame,
    trades: pd.DataFrame,
) -> None:
    display_grid = train_grid.head(15).copy()
    display_metrics = metrics.copy()
    years = pd.DataFrame()
    exits = pd.DataFrame()
    symbols = pd.DataFrame()
    if not trades.empty:
        year_rows = []
        for segment, frame in trades.groupby("validation_segment"):
            for row in year_by_year_metrics(frame):
                row["validation_segment"] = segment
                year_rows.append(row)
        years = pd.DataFrame(year_rows)
        exits = exit_summary(trades)
        symbols = symbol_contribution(trades)
    for frame in [display_grid, display_metrics, years, exits, symbols]:
        for col in frame.select_dtypes(include=[float]).columns:
            frame[col] = frame[col].round(4)

    text = f"""# Multi-Derive Forward Validation

Generated: {pd.Timestamp.now()}

This is stricter than the first replay. The setup/regime/rank/top-N slice is selected only on the training window using proxy labels, then the frozen slice is replayed on later data with OHLC entry/exit mechanics.

## Windows

- Training selection: through `{args.train_end}`
- Validation replay: `{args.validation_start}` to before `{args.forward_start}`
- Forward replay: from `{args.forward_start}`
- Primary cost scenario: `{args.primary_cost_scenario}`

## Frozen Config

```json
{json.dumps(config, indent=2)}
```

## Training Search Leaders

{display_grid.to_markdown(index=False) if not display_grid.empty else "No training combinations passed the signal threshold."}

## Frozen Real Replay Metrics

{display_metrics.to_markdown(index=False) if not display_metrics.empty else "No replay metrics generated."}

## Year By Year

{years.to_markdown(index=False) if not years.empty else "No yearly rows generated."}

## Exit Summary

{exits.to_markdown(index=False) if not exits.empty else "No exits generated."}

## Top Symbol Contribution

{symbols.head(25).to_markdown(index=False) if not symbols.empty else "No symbol contribution generated."}

## Verdict Discipline

This is still a script-level research backtest, not the Rust engine backtest table. Treat it as promotable only if validation and forward segments survive separately, stress costs remain acceptable, and the rule is then wired into the engine backtest path.
"""
    (out_dir / "forward_validation_report.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading features from {args.features}")
    features = pd.read_parquet(args.features)
    features["trade_date"] = pd.to_datetime(features["trade_date"])
    print(f"Loading candidates from {args.candidates}")
    candidates = pd.read_csv(args.candidates, parse_dates=["trade_date"])

    segments = segment_candidates(candidates, args)
    train_grid = search_train_config(args, segments["train"])
    train_grid.to_csv(args.out_dir / "training_config_search.csv", index=False)
    if train_grid.empty:
        print("No training configuration passed the minimum signal threshold.")
        return 1

    best = train_grid.iloc[0].to_dict()
    config = {
        "setup": best["setup"],
        "regime": best["regime"],
        "min_rank_score": float(best["min_rank_score"]),
        "top_n_per_day": int(best["top_n_per_day"]),
        "selected_from": f"trade_date <= {args.train_end}",
        "selection_quality_score": float(best["quality_score"]),
        "selection_avg_fwd_ret_10d_pct": float(best["avg_fwd_ret_10d_pct"]),
        "selection_train_signals": int(best["train_signals"]),
    }
    (args.out_dir / "frozen_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    metrics, trades = run_segment_backtests(args, features, segments, config)
    metrics.to_csv(args.out_dir / "forward_validation_metrics.csv", index=False)
    trades.to_csv(args.out_dir / "forward_validation_trade_log_primary_cost.csv", index=False)
    write_report(args.out_dir, args, train_grid, config, metrics, trades)

    manifest = {
        "generated_at": str(pd.Timestamp.now()),
        "features": str(args.features),
        "candidates": str(args.candidates),
        "train_end": args.train_end,
        "validation_start": args.validation_start,
        "forward_start": args.forward_start,
        "stop_atr": args.stop_atr,
        "target_atr": args.target_atr,
        "max_hold_days": args.max_hold_days,
        "primary_cost_scenario": args.primary_cost_scenario,
        "outputs": [
            "training_config_search.csv",
            "frozen_config.json",
            "forward_validation_metrics.csv",
            "forward_validation_trade_log_primary_cost.csv",
            "forward_validation_report.md",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done. Forward-validation outputs saved to {args.out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze multi-derived rules on train data and replay later windows.")
    parser.add_argument("--features", type=Path, default=DEFAULT_IN_DIR / "feature_matrix.parquet")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_IN_DIR / "candidate_matrix.csv")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--train-end", default="2023-12-31")
    parser.add_argument("--validation-start", default="2024-01-01")
    parser.add_argument("--forward-start", default="2025-01-01")
    parser.add_argument("--min-scores", type=float, nargs="+", default=[70, 78, 85, 92])
    parser.add_argument("--top-ns", type=int, nargs="+", default=[3, 5, 10])
    parser.add_argument("--min-train-signals", type=int, default=60)
    parser.add_argument("--stop-atr", type=float, default=1.6)
    parser.add_argument("--target-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-days", type=int, default=10)
    parser.add_argument("--allow-same-symbol-overlap", action="store_true")
    parser.add_argument("--primary-cost-scenario", choices=sorted(COST_SCENARIOS), default="base")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
