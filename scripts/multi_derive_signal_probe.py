from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN_DIR = ROOT / "docs" / "multi_derive_outputs"


def evaluate_slice(
    df: pd.DataFrame,
    *,
    setup: str,
    regime: str,
    min_score: float,
    top_n: int,
) -> dict[str, object]:
    part = df[df["rank_score"] >= min_score].copy()
    if setup != "all":
        part = part[part["derived_setup_family"].eq(setup)]
    if regime != "all":
        part = part[part["regime_label"].eq(regime)]
    if part.empty:
        return {
            "setup": setup,
            "regime": regime,
            "min_rank_score": min_score,
            "top_n_per_day": top_n,
            "days": 0,
            "signals": 0,
            "avg_fwd_ret_10d_pct": 0.0,
            "median_fwd_ret_10d_pct": 0.0,
            "hit_2pct_10d": 0.0,
            "hit_4pct_10d": 0.0,
            "drawdown_3pct_10d": 0.0,
            "avg_mfe_10d_pct": 0.0,
            "avg_mae_10d_pct": 0.0,
            "quality_score": -999.0,
        }
    picked = (
        part.sort_values(["trade_date", "rank_score"], ascending=[True, False])
        .groupby("trade_date", group_keys=False)
        .head(top_n)
    )
    picked = picked[picked["fwd_ret_10d_pct"].notna()].copy()
    if picked.empty:
        return {
            "setup": setup,
            "regime": regime,
            "min_rank_score": min_score,
            "top_n_per_day": top_n,
            "days": 0,
            "signals": 0,
            "avg_fwd_ret_10d_pct": 0.0,
            "median_fwd_ret_10d_pct": 0.0,
            "hit_2pct_10d": 0.0,
            "hit_4pct_10d": 0.0,
            "drawdown_3pct_10d": 0.0,
            "avg_mfe_10d_pct": 0.0,
            "avg_mae_10d_pct": 0.0,
            "quality_score": -999.0,
        }
    avg_ret = float(picked["fwd_ret_10d_pct"].mean())
    hit2 = float(picked["hit_2pct_10d"].mean())
    hit4 = float(picked["hit_4pct_10d"].mean())
    dd3 = float(picked["drawdown_3pct_10d"].mean())
    avg_mae = float(picked["mae_10d_pct"].mean())
    quality = avg_ret + hit2 * 2.0 + hit4 * 1.5 - dd3 * 1.5 + avg_mae * 0.10
    return {
        "setup": setup,
        "regime": regime,
        "min_rank_score": min_score,
        "top_n_per_day": top_n,
        "days": int(picked["trade_date"].nunique()),
        "signals": int(len(picked)),
        "avg_fwd_ret_10d_pct": round(avg_ret, 4),
        "median_fwd_ret_10d_pct": round(float(picked["fwd_ret_10d_pct"].median()), 4),
        "hit_2pct_10d": round(hit2, 4),
        "hit_4pct_10d": round(hit4, 4),
        "drawdown_3pct_10d": round(dd3, 4),
        "avg_mfe_10d_pct": round(float(picked["mfe_10d_pct"].mean()), 4),
        "avg_mae_10d_pct": round(avg_mae, 4),
        "quality_score": round(quality, 4),
    }


def run(args: argparse.Namespace) -> int:
    input_path = args.input
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading candidate matrix from {input_path}")
    df = pd.read_csv(input_path, parse_dates=["trade_date"])
    setups = ["all"] + sorted(df["derived_setup_family"].dropna().unique().tolist())
    regimes = ["all"] + sorted(df["regime_label"].dropna().unique().tolist())
    rows = []
    for setup in setups:
        for regime in regimes:
            for min_score in args.min_scores:
                for top_n in args.top_ns:
                    rows.append(
                        evaluate_slice(
                            df,
                            setup=setup,
                            regime=regime,
                            min_score=min_score,
                            top_n=top_n,
                        )
                    )
    results = pd.DataFrame(rows)
    results = results[results["signals"] >= args.min_signals].sort_values(
        ["quality_score", "avg_fwd_ret_10d_pct", "signals"],
        ascending=[False, False, False],
    )
    results.to_csv(out_dir / "signal_probe_results.csv", index=False)
    top = results.head(30).copy()
    report = f"""# Multi-Derive Signal Probe

Generated: {pd.Timestamp.now()}

Input: `{input_path}`

This is a proxy research probe over the multi-derived candidate matrix. It is not a portfolio backtest. It searches setup/regime/rank-score/top-N combinations using 10-day forward labels.

## Top Combinations

{top.to_markdown(index=False) if not top.empty else "No combinations passed the minimum signal count."}

## Next Step

Turn the top few combinations into proper `StrategySpec` modules and run the full backtester with costs, stops, targets, max positions, OOS, and walk-forward validation.
"""
    (out_dir / "signal_probe_report.md").write_text(report, encoding="utf-8")
    print(f"Done. Probe outputs saved to {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe multi-derived candidates for promising setup/regime slices.")
    parser.add_argument("--input", type=Path, default=DEFAULT_IN_DIR / "candidate_matrix.csv")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_IN_DIR)
    parser.add_argument("--min-scores", type=float, nargs="+", default=[62, 70, 78, 85, 92])
    parser.add_argument("--top-ns", type=int, nargs="+", default=[3, 5, 10, 20])
    parser.add_argument("--min-signals", type=int, default=100)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
