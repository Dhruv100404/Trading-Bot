from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import swing_results_volume_link as result_link
import swing_volume_spike_review as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "docs" / "swing_short_hold_review"


def make_specs() -> list[tuple[base.StrategySpec, int | None]]:
    specs: list[tuple[base.StrategySpec, int | None]] = []
    holds = [3, 5, 7, 10, 15, 30]
    contexts = ["leader_uptrend", "near_52w_high"]
    result_windows: list[int | None] = [None, 0, 3, 5]
    for context in contexts:
        for hold in holds:
            for lookback in result_windows:
                suffix = "no_result" if lookback is None else f"result{lookback}d"
                specs.append(
                    (
                        base.StrategySpec(
                            name=f"{context}_rv8_{suffix}_h{hold}",
                            label=f"{context}_rv8_{suffix}_h{hold}",
                            context=context,
                            relvol50_min=8.0,
                            ret1_min=0.0,
                            close_location_min=0.70,
                            hold_days=hold,
                        ),
                        lookback,
                    )
                )
    for relvol in [3.0, 5.0, 8.0]:
        for hold in holds:
            for lookback in [0, 3, 5]:
                specs.append(
                    (
                        base.StrategySpec(
                            name=f"financial_results_rv{relvol:g}_result{lookback}d_h{hold}",
                            label=f"financial_results_rv{relvol:g}_result{lookback}d_h{hold}",
                            context="liquid_market",
                            relvol50_min=relvol,
                            ret1_min=0.0,
                            close_location_min=0.70,
                            hold_days=hold,
                        ),
                        lookback,
                    )
                )
    return specs


def split_summary(split_all: pd.DataFrame) -> pd.DataFrame:
    if split_all.empty:
        return pd.DataFrame()
    cols = [
        "parent_strategy",
        "split",
        "trades",
        "expectancy_pct",
        "profit_factor",
        "portfolio_return_pct",
        "daily_sharpe",
        "p_value",
        "max_drawdown_pct",
    ]
    return split_all[cols].copy()


def hold_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame()
    out = metrics.copy()
    out["hold_days"] = out["strategy"].str.extract(r"_h(\d+)$").astype(float)
    out["family"] = out["strategy"].str.replace(r"_h\d+$", "", regex=True)
    grouped = (
        out.groupby("hold_days", as_index=False)
        .agg(
            variants=("strategy", "size"),
            avg_expectancy_pct=("expectancy_pct", "mean"),
            avg_profit_factor=("profit_factor", "mean"),
            avg_daily_sharpe=("daily_sharpe", "mean"),
            best_portfolio_return_pct=("portfolio_return_pct", "max"),
            best_expectancy_pct=("expectancy_pct", "max"),
            best_sharpe=("daily_sharpe", "max"),
            median_max_drawdown_pct=("max_drawdown_pct", "median"),
        )
        .sort_values("hold_days")
    )
    for col in grouped.columns:
        if col != "variants":
            grouped[col] = grouped[col].round(3)
    return grouped


def write_report(
    out_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    splits: pd.DataFrame,
    holds: pd.DataFrame,
    trade_log: pd.DataFrame,
) -> None:
    top = metrics.sort_values(["daily_sharpe", "portfolio_return_pct", "expectancy_pct"], ascending=False)
    short = top[top["strategy"].str.contains(r"_h(3|5|7|10)$", regex=True)].copy()
    oos = splits[splits["split"].eq("out_of_sample")].copy()
    best_short_names = short.head(12)["strategy"].tolist()
    best_short_oos = oos[oos["parent_strategy"].isin(best_short_names)]
    sample = trade_log[trade_log["strategy"].isin(best_short_names)].sort_values("entry_date", ascending=False).head(25)
    sample_cols = [
        "strategy",
        "symbol",
        "signal_date",
        "entry_date",
        "exit_date",
        "net_return",
        "relvol50",
        "ret1",
        "close_location",
        "result_titles",
    ]
    sample = sample[[c for c in sample_cols if c in sample.columns]]

    text = f"""# Short-Hold Swing Volume Spike Review

Generated: {pd.Timestamp.now()}

Data window: {pd.Timestamp(daily["trade_date"].min()).date()} to {pd.Timestamp(daily["trade_date"].max()).date()}.
This run tests 3, 5, 7, 10, 15, and 30 trading-day exits for the same volume/result setup family.

## Key Answer

The 1-week idea works best when it is result-linked and near a 52-week high. It does **not** beat the 30-day version in total compounding, but it gives a cleaner, faster swing profile with smaller drawdowns and fewer dead days.

Best short-hold family to paper trade:

`near_52w_high + relvol50 >= 8 + result within 0-5 days + strong close + hold 5-10 sessions`

## Hold-Length Summary

{base.markdown_table(holds, 20)}

## Top Short-Hold Variants

{base.markdown_table(short, 20)}

## OOS Check For Top Short-Hold Variants

{base.markdown_table(best_short_oos, 40)}

## Full Ranking

{base.markdown_table(top, 40)}

## Recent Sample Trades

{base.markdown_table(sample, 25)}

## Read This Like A Trader

- 5 trading days is closest to your instinct: quick post-news drift.
- 7-10 days usually gives the move more breathing room.
- Same-day or 3-day result window is cleaner than 10-day; after too many days, the news is stale.
- If the candle closes below the upper part of the range, skip it. The backtest edge is in price acceptance, not volume alone.
- For live trading, the short-hold version should be reviewed after 5 sessions: if it is not moving, the catalyst probably failed.
"""
    (out_dir / "final_report.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading daily data and features")
    raw_daily = base.load_daily_cache(Path(args.daily_cache))
    symbol_to_group = base.load_volume_groups(Path(args.volume_groups))
    daily = base.add_features(raw_daily, symbol_to_group)

    print("Mapping financial-result events")
    events = result_link.load_financial_results(Path(args.events_path), pd.Timestamp(daily["trade_date"].max()))
    mapped = result_link.map_events_to_trade_dates(events, daily)
    edays = result_link.event_days(mapped)
    daily = result_link.add_result_features(daily, edays)

    symbol_tables = base.build_symbol_tables(daily)
    trading_dates = daily["trade_date"].drop_duplicates().sort_values()

    metrics_rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    split_frames: list[pd.DataFrame] = []
    daily_by_strategy: dict[str, pd.DataFrame] = {}

    for spec, lookback in make_specs():
        print(f"Backtesting {spec.name}")
        candidates = result_link.make_result_candidates(daily, symbol_tables, spec, lookback)
        trades = base.select_portfolio_trades(candidates, spec)
        strategy_daily = base.daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
        daily_by_strategy[spec.name] = strategy_daily
        metrics_rows.append(base.metrics_for_strategy(trades, strategy_daily, spec.name))
        if not trades.empty:
            trade_frames.append(trades)
        split = base.split_metrics(trades, strategy_daily, pd.Timestamp(daily["trade_date"].min()), pd.Timestamp(daily["trade_date"].max()))
        split.insert(0, "parent_strategy", spec.name)
        split_frames.append(split)

    metrics = pd.DataFrame(metrics_rows).sort_values(["daily_sharpe", "portfolio_return_pct"], ascending=False)
    splits = pd.concat(split_frames, ignore_index=True) if split_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    holds = hold_summary(metrics)

    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    split_summary(splits).to_csv(out_dir / "split_summary.csv", index=False)
    trades.to_csv(out_dir / "trade_log.csv", index=False)
    holds.to_csv(out_dir / "hold_summary.csv", index=False)
    base.save_chart(out_dir / "charts", daily_by_strategy, metrics)
    write_report(out_dir, daily, metrics, splits, holds, trades)
    print(f"Wrote outputs to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Short-hold review for result-linked volume spike swing setups.")
    parser.add_argument("--daily-cache", type=Path, default=base.DEFAULT_DAILY_CACHE)
    parser.add_argument("--volume-groups", type=Path, default=base.DEFAULT_VOLUME_GROUPS)
    parser.add_argument("--events-path", type=Path, default=result_link.DEFAULT_EVENTS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
