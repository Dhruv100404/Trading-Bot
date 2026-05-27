from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from quant_research_pipeline import DEFAULT_OUT_DIR as QUANT_OUT_DIR  # noqa: E402
from quant_research_pipeline import add_features, load_daily_bars  # noqa: E402


DEFAULT_OUT_DIR = ROOT / "docs" / "multi_derive_outputs"


def _future_rolling(series: pd.Series, window: int, op: str) -> pd.Series:
    shifted = series.shift(-1).iloc[::-1]
    roller = shifted.rolling(window, min_periods=1)
    if op == "max":
        out = roller.max()
    elif op == "min":
        out = roller.min()
    else:
        raise ValueError(f"unsupported future rolling op: {op}")
    return out.iloc[::-1]


def add_forward_labels(df: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    out = df.sort_values(["symbol", "trade_date"]).copy()
    g = out.groupby("symbol", group_keys=False)
    for horizon in horizons:
        out[f"future_close_{horizon}"] = g["close"].shift(-horizon)
        out[f"fwd_ret_{horizon}d_pct"] = (out[f"future_close_{horizon}"] / out["close"] - 1.0) * 100.0
        future_high = g["high"].transform(lambda s, h=horizon: _future_rolling(s, h, "max"))
        future_low = g["low"].transform(lambda s, h=horizon: _future_rolling(s, h, "min"))
        out[f"mfe_{horizon}d_pct"] = (future_high / out["close"] - 1.0) * 100.0
        out[f"mae_{horizon}d_pct"] = (future_low / out["close"] - 1.0) * 100.0
        out[f"hit_2pct_{horizon}d"] = out[f"mfe_{horizon}d_pct"] >= 2.0
        out[f"hit_4pct_{horizon}d"] = out[f"mfe_{horizon}d_pct"] >= 4.0
        out[f"drawdown_3pct_{horizon}d"] = out[f"mae_{horizon}d_pct"] <= -3.0
    return out


def add_multi_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["symbol", "trade_date"]).copy()
    g = out.groupby("symbol", group_keys=False)

    out["dist_sma20_atr"] = (out["close"] - out["sma20"]) / out["atr14"].replace(0, np.nan)
    out["dist_sma50_pct"] = (out["close"] / out["sma50"].replace(0, np.nan) - 1.0) * 100.0
    out["dist_52w_high_pct"] = (out["prior_high252"] / out["close"].replace(0, np.nan) - 1.0) * 100.0
    out["range_position_52w"] = (
        (out["close"] - out["prior_low252"]) / (out["prior_high252"] - out["prior_low252"]).replace(0, np.nan)
    ).clip(0, 1)
    out["volume_dryup_ratio"] = out["relvol"] / g["relvol"].transform(
        lambda s: s.rolling(60, min_periods=30).median()
    ).replace(0, np.nan)
    out["atr_pct_rank_252"] = g["atr_pct"].transform(
        lambda s: s.rolling(252, min_periods=80).rank(pct=True)
    )
    out["relvol_rank_60"] = g["relvol"].transform(
        lambda s: s.rolling(60, min_periods=30).rank(pct=True)
    )
    out["ret20_rank_252"] = g["ret20"].transform(
        lambda s: s.rolling(252, min_periods=80).rank(pct=True)
    )

    out["trend_quality_score"] = (
        (out["close"] > out["sma200"]).astype(float) * 20
        + (out["ema20"] > out["ema50"]).astype(float) * 18
        + (out["ema50"] > out["ema200"]).astype(float) * 18
        + out["rs60_rank"].fillna(0).clip(0, 1) * 22
        + out["rs120_rank"].fillna(0).clip(0, 1) * 22
    )
    out["pullback_quality_score"] = (
        out["trend_quality_score"] * 0.45
        + (out["rsi14"].between(35, 58)).astype(float) * 18
        + (out["dist_ema20_atr"].between(-1.8, 0.8)).astype(float) * 16
        + (out["relvol"].between(0.45, 1.25)).astype(float) * 12
        + out["market_breadth200"].fillna(0).clip(0, 1) * 9
    )
    out["stretch_reversal_score"] = (
        (out["close"] > out["sma200"]).astype(float) * 16
        + (-out["dist_ema20_atr"]).clip(0, 4).fillna(0) * 12
        + (40 - out["rsi14"]).clip(0, 25).fillna(0) * 1.2
        + out["close_location"].fillna(0).clip(0, 1) * 18
        + out["market_breadth200"].fillna(0).clip(0, 1) * 14
        + out["rs120_rank"].fillna(0).clip(0, 1) * 10
        - out["gap_pct"].abs().fillna(0).clip(0, 8) * 2
    )
    out["breakout_quality_score"] = (
        (out["close"] > out["prior_high20"]).astype(float) * 22
        + (out["close"] > out["prior_high55"]).astype(float) * 14
        + out["rs60_rank"].fillna(0).clip(0, 1) * 22
        + out["close_location"].fillna(0).clip(0, 1) * 15
        + out["relvol"].fillna(0).clip(0, 3) * 8
        + out["market_breadth200"].fillna(0).clip(0, 1) * 11
        - out["gap_pct"].abs().fillna(0).clip(0, 8) * 2
    )
    out["compression_score"] = (
        (out["bb_width"] < out["bb_width_q20"] * 1.15).astype(float) * 26
        + (out["range_pct"] <= out["range7_min"] * 1.10).astype(float) * 18
        + (out["close"] > out["prior_high20"]).astype(float) * 18
        + out["rs60_rank"].fillna(0).clip(0, 1) * 18
        + out["relvol"].fillna(0).clip(0, 2) * 8
        - (out["atr_pct"] * 100).fillna(0).clip(0, 10) * 1.5
    )
    out["risk_penalty_score"] = (
        out["gap_pct"].abs().fillna(0).clip(0, 10) * 3
        + (out["atr_pct"] * 100).fillna(0).clip(0, 12) * 2
        + (out["volatility_regime"].eq("high")).astype(float) * 18
        + (out["market_breadth200"].fillna(0) < 0.25).astype(float) * 18
    )
    out["composite_alpha_score"] = (
        out[["pullback_quality_score", "stretch_reversal_score", "breakout_quality_score", "compression_score"]]
        .max(axis=1)
        - out["risk_penalty_score"] * 0.45
    ).clip(0, 100)

    setup_scores = out[
        ["pullback_quality_score", "stretch_reversal_score", "breakout_quality_score", "compression_score"]
    ].copy()
    setup_scores.columns = ["rs_pullback", "atr_stretch_reversal", "breakout", "compression_breakout"]
    out["derived_setup_family"] = setup_scores.idxmax(axis=1)
    out.loc[out["composite_alpha_score"] < 45, "derived_setup_family"] = "no_trade"
    return out


def build_market_regime(df: pd.DataFrame) -> pd.DataFrame:
    daily = (
        df.groupby("trade_date")
        .agg(
            symbols=("symbol", "nunique"),
            avg_ret1=("ret1", "mean"),
            median_ret20=("ret20", "median"),
            breadth50=("market_breadth50", "mean"),
            breadth200=("market_breadth200", "mean"),
            high_vol_share=("volatility_regime", lambda s: s.eq("high").mean()),
            trending_share=("trend_regime", lambda s: s.eq("trending").mean()),
            avg_gap_pct=("gap_pct", "mean"),
            median_atr_pct=("atr_pct", "median"),
            top_rs_count=("rs60_rank", lambda s: (s >= 0.80).sum()),
        )
        .reset_index()
        .sort_values("trade_date")
    )
    daily["breadth200_slope5"] = daily["breadth200"] - daily["breadth200"].rolling(5, min_periods=3).mean()
    daily["breadth200_slope20"] = daily["breadth200"] - daily["breadth200"].rolling(20, min_periods=10).mean()
    daily["market_stress_score"] = (
        (1 - daily["breadth200"]).clip(0, 1) * 45
        + daily["high_vol_share"].clip(0, 1) * 30
        + (-daily["breadth200_slope5"]).clip(0, 0.20) * 125
    ).clip(0, 100)
    daily["regime_label"] = np.select(
        [
            (daily["breadth200"] >= 0.55) & (daily["breadth200_slope20"] >= -0.03) & (daily["high_vol_share"] < 0.25),
            (daily["breadth200"].between(0.38, 0.55)) & (daily["market_stress_score"] < 55),
            (daily["breadth200"].between(0.25, 0.45)) & (daily["breadth200_slope5"] < -0.02),
            (daily["breadth200"] < 0.25) | (daily["market_stress_score"] >= 70),
        ],
        ["risk_on", "mixed", "distribution", "panic"],
        default="sideways",
    )
    daily["trade_budget_multiplier"] = np.select(
        [
            daily["regime_label"].eq("risk_on"),
            daily["regime_label"].eq("mixed"),
            daily["regime_label"].eq("sideways"),
            daily["regime_label"].eq("distribution"),
            daily["regime_label"].eq("panic"),
        ],
        [1.0, 0.75, 0.6, 0.35, 0.0],
        default=0.5,
    )
    return daily


def build_candidate_matrix(df: pd.DataFrame, regime: pd.DataFrame, min_score: float) -> pd.DataFrame:
    cols = [
        "symbol",
        "trade_date",
        "close",
        "next_open",
        "derived_setup_family",
        "composite_alpha_score",
        "trend_quality_score",
        "pullback_quality_score",
        "stretch_reversal_score",
        "breakout_quality_score",
        "compression_score",
        "risk_penalty_score",
        "rs60_rank",
        "rs120_rank",
        "market_breadth200",
        "trend_regime",
        "volatility_regime",
        "gap_pct",
        "relvol",
        "atr_pct",
        "close_location",
        "fwd_ret_10d_pct",
        "mfe_10d_pct",
        "mae_10d_pct",
        "hit_2pct_10d",
        "hit_4pct_10d",
        "drawdown_3pct_10d",
    ]
    candidates = df.loc[
        (df["liquid_research"])
        & (df["derived_setup_family"].ne("no_trade"))
        & (df["composite_alpha_score"] >= min_score)
        & df["next_open"].notna(),
        cols,
    ].copy()
    candidates = candidates.merge(
        regime[["trade_date", "regime_label", "market_stress_score", "trade_budget_multiplier"]],
        on="trade_date",
        how="left",
    )
    candidates["rank_score"] = (
        candidates["composite_alpha_score"]
        + candidates["rs60_rank"].fillna(0) * 8
        + candidates["trade_budget_multiplier"].fillna(0.5) * 6
        - candidates["market_stress_score"].fillna(50) * 0.08
    )
    return candidates.sort_values(["trade_date", "rank_score"], ascending=[True, False])


def factor_edge_table(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    rows = []
    base = df.loc[df["liquid_research"] & df["fwd_ret_10d_pct"].notna()].copy()
    for feature in features:
        series = base[feature].replace([np.inf, -np.inf], np.nan)
        valid = base.loc[series.notna()].copy()
        if valid.empty or valid[feature].nunique(dropna=True) < 5:
            continue
        try:
            valid["_bucket"] = pd.qcut(valid[feature], q=5, duplicates="drop")
        except ValueError:
            continue
        grouped = (
            valid.groupby("_bucket", observed=True)
            .agg(
                rows=("symbol", "size"),
                avg_feature=(feature, "mean"),
                avg_fwd_ret_10d_pct=("fwd_ret_10d_pct", "mean"),
                median_fwd_ret_10d_pct=("fwd_ret_10d_pct", "median"),
                hit_2pct_10d=("hit_2pct_10d", "mean"),
                hit_4pct_10d=("hit_4pct_10d", "mean"),
                drawdown_3pct_10d=("drawdown_3pct_10d", "mean"),
                avg_mfe_10d_pct=("mfe_10d_pct", "mean"),
                avg_mae_10d_pct=("mae_10d_pct", "mean"),
            )
            .reset_index()
        )
        grouped.insert(0, "feature", feature)
        grouped["_bucket"] = grouped["_bucket"].astype(str)
        rows.append(grouped.rename(columns={"_bucket": "bucket"}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def summarize_setup_edges(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    return (
        candidates.groupby(["derived_setup_family", "regime_label"], dropna=False)
        .agg(
            rows=("symbol", "size"),
            avg_rank_score=("rank_score", "mean"),
            avg_fwd_ret_10d_pct=("fwd_ret_10d_pct", "mean"),
            hit_2pct_10d=("hit_2pct_10d", "mean"),
            hit_4pct_10d=("hit_4pct_10d", "mean"),
            drawdown_3pct_10d=("drawdown_3pct_10d", "mean"),
            avg_mfe_10d_pct=("mfe_10d_pct", "mean"),
            avg_mae_10d_pct=("mae_10d_pct", "mean"),
        )
        .reset_index()
        .sort_values(["avg_fwd_ret_10d_pct", "hit_2pct_10d"], ascending=[False, False])
    )


def latest_shortlist(candidates: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    latest_date = candidates["trade_date"].max()
    latest = candidates[candidates["trade_date"].eq(latest_date)].copy()
    return latest.sort_values("rank_score", ascending=False).head(top_n)


def write_report(
    out_dir: Path,
    df: pd.DataFrame,
    regime: pd.DataFrame,
    candidates: pd.DataFrame,
    setup_edges: pd.DataFrame,
    latest: pd.DataFrame,
    factor_edges: pd.DataFrame,
) -> None:
    latest_date = df["trade_date"].max().date()
    regime_tail = regime.tail(10).copy()
    for col in ["breadth50", "breadth200", "high_vol_share", "market_stress_score", "trade_budget_multiplier"]:
        regime_tail[col] = regime_tail[col].astype(float).round(3)
    setup_show = setup_edges.head(20).copy()
    for col in setup_show.select_dtypes(include=[float]).columns:
        setup_show[col] = setup_show[col].round(3)
    latest_show = latest.copy()
    for col in latest_show.select_dtypes(include=[float]).columns:
        latest_show[col] = latest_show[col].round(3)

    best_factor = pd.DataFrame()
    if not factor_edges.empty:
        best_factor = factor_edges.sort_values("avg_fwd_ret_10d_pct", ascending=False).head(20).copy()
        for col in best_factor.select_dtypes(include=[float]).columns:
            best_factor[col] = best_factor[col].round(3)

    text = f"""# Multi-Derive Research Pipeline Report

Generated: {pd.Timestamp.now()}

## Dataset

- Rows: {len(df):,}
- Symbols: {df["symbol"].nunique():,}
- Date range: {df["trade_date"].min().date()} to {latest_date}
- Candidate rows above threshold: {len(candidates):,}

## What This Pipeline Derives

1. Enriched daily feature matrix from parquet-derived OHLCV.
2. Forward labels: 5/10/20 day return, MFE, MAE, target-hit and drawdown flags.
3. Market regime table with breadth slope, high-volatility share, stress score, and trade budget multiplier.
4. Setup scores for pullback, ATR stretch reversal, breakout, and compression breakout.
5. Candidate matrix with rank scores and future outcome labels for research.
6. Factor edge table that shows which derived features actually had forward edge.

## Latest Regime

{regime_tail.to_markdown(index=False)}

## Latest Candidate Shortlist

{latest_show.to_markdown(index=False) if not latest_show.empty else "No latest candidates passed the threshold."}

## Best Setup/Regime Buckets

{setup_show.to_markdown(index=False) if not setup_show.empty else "No setup edge rows generated."}

## Strongest Factor Buckets

{best_factor.to_markdown(index=False) if not best_factor.empty else "No factor edge rows generated."}

## How To Use

- Use `candidate_matrix.csv` for strategy discovery.
- Use `factor_edge_table.csv` to see which derived signals are worth turning into strategy code.
- Use `market_regime_daily.csv` as the governor input before any strategy fires.
- Use `latest_candidates.csv` as a research shortlist, not live execution.
"""
    (out_dir / "pipeline_report.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading daily cache from {args.daily_cache}")
    raw = load_daily_bars(args.daily_cache, refresh=args.refresh_cache)
    print("Adding base research features")
    df = add_features(raw)
    print("Adding multi-derived features and forward labels")
    df = add_multi_derived_features(df)
    df = add_forward_labels(df)
    print("Building market regime and candidates")
    regime = build_market_regime(df)
    candidates = build_candidate_matrix(df, regime, min_score=args.min_score)
    setup_edges = summarize_setup_edges(candidates)
    latest = latest_shortlist(candidates, args.top_latest)
    factor_edges = factor_edge_table(
        df,
        [
            "composite_alpha_score",
            "trend_quality_score",
            "pullback_quality_score",
            "stretch_reversal_score",
            "breakout_quality_score",
            "compression_score",
            "risk_penalty_score",
            "rs60_rank",
            "rs120_rank",
            "relvol",
            "atr_pct",
            "market_breadth200",
            "close_location",
            "dist_ema20_atr",
            "gap_pct",
            "volume_dryup_ratio",
        ],
    )

    feature_cols = [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "next_open",
        "liquid_research",
        "trend_regime",
        "volatility_regime",
        "market_breadth50",
        "market_breadth200",
        "rs60_rank",
        "rs120_rank",
        "relvol",
        "atr14",
        "atr_pct",
        "rsi14",
        "dist_ema20_atr",
        "close_location",
        "gap_pct",
        "trend_quality_score",
        "pullback_quality_score",
        "stretch_reversal_score",
        "breakout_quality_score",
        "compression_score",
        "risk_penalty_score",
        "composite_alpha_score",
        "derived_setup_family",
        "fwd_ret_5d_pct",
        "fwd_ret_10d_pct",
        "fwd_ret_20d_pct",
        "mfe_10d_pct",
        "mae_10d_pct",
        "hit_2pct_10d",
        "drawdown_3pct_10d",
    ]
    df[feature_cols].to_parquet(args.out_dir / "feature_matrix.parquet", index=False)
    regime.to_csv(args.out_dir / "market_regime_daily.csv", index=False)
    candidates.to_csv(args.out_dir / "candidate_matrix.csv", index=False)
    setup_edges.to_csv(args.out_dir / "setup_regime_edges.csv", index=False)
    factor_edges.to_csv(args.out_dir / "factor_edge_table.csv", index=False)
    latest.to_csv(args.out_dir / "latest_candidates.csv", index=False)
    write_report(args.out_dir, df, regime, candidates, setup_edges, latest, factor_edges)

    manifest = {
        "generated_at": str(pd.Timestamp.now()),
        "daily_cache": str(args.daily_cache),
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "from_date": str(df["trade_date"].min().date()),
        "to_date": str(df["trade_date"].max().date()),
        "candidate_rows": int(len(candidates)),
        "latest_candidate_rows": int(len(latest)),
        "min_score": float(args.min_score),
        "outputs": [
            "feature_matrix.parquet",
            "market_regime_daily.csv",
            "candidate_matrix.csv",
            "setup_regime_edges.csv",
            "factor_edge_table.csv",
            "latest_candidates.csv",
            "pipeline_report.md",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done. Multi-derived outputs saved to {args.out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build multi-derived research datasets from cached parquet daily bars.")
    parser.add_argument("--daily-cache", type=Path, default=QUANT_OUT_DIR / "daily_bars_cache.parquet")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--min-score", type=float, default=62.0)
    parser.add_argument("--top-latest", type=int, default=40)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
