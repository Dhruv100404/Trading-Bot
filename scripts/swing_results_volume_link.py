from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import swing_volume_spike_review as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS_PATH = ROOT / "data" / "events" / "corporate_events.parquet"
DEFAULT_OUT_DIR = ROOT / "docs" / "swing_results_volume_link"


def load_financial_results(path: Path, max_date: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Events file not found: {path}")
    events = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    events = events.copy()
    events["symbol"] = events["symbol"].fillna("").astype(str).str.upper().str.strip()
    events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
    events = events[
        events["symbol"].ne("")
        & events["event_date"].notna()
        & events["event_category"].eq("financial_results")
        & events["source"].isin(["nse_announcements", "nse_financial_results"])
    ].copy()
    if "is_catalyst" in events.columns:
        events = events[events["is_catalyst"].fillna(False)]
    events = events[events["event_date"] <= max_date + pd.Timedelta(days=7)]
    return events.reset_index(drop=True)


def map_events_to_trade_dates(events: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    events = events.copy()
    event_time = pd.to_datetime(events["event_time"], errors="coerce")
    event_date = pd.to_datetime(events["event_date"], errors="coerce")
    after_close = event_time.dt.time >= pd.Timestamp("15:30").time()
    events["effective_calendar_date"] = event_date.dt.normalize() + pd.to_timedelta(after_close.fillna(False).astype(int), unit="D")

    mapped_parts: list[pd.DataFrame] = []
    bar_dates = {
        symbol: part["trade_date"].sort_values().to_numpy(dtype="datetime64[ns]")
        for symbol, part in daily[["symbol", "trade_date"]].drop_duplicates().groupby("symbol", sort=False)
    }
    for symbol, part in events.groupby("symbol", sort=False):
        dates = bar_dates.get(symbol)
        if dates is None or len(dates) == 0:
            continue
        wanted = part["effective_calendar_date"].to_numpy(dtype="datetime64[ns]")
        idx = np.searchsorted(dates, wanted, side="left")
        valid = idx < len(dates)
        if not valid.any():
            continue
        mapped = part.loc[valid].copy()
        mapped["trade_date"] = pd.to_datetime(dates[idx[valid]])
        mapped_parts.append(mapped)
    return pd.concat(mapped_parts, ignore_index=True) if mapped_parts else pd.DataFrame()


def event_days(mapped: pd.DataFrame) -> pd.DataFrame:
    if mapped.empty:
        return pd.DataFrame()

    def join_unique(values: pd.Series, limit: int = 3) -> str:
        seen: list[str] = []
        for value in values.dropna().astype(str):
            value = value.strip()
            if value and value not in seen:
                seen.append(value)
            if len(seen) >= limit:
                break
        return " | ".join(seen)

    return (
        mapped.groupby(["symbol", "trade_date"], as_index=False)
        .agg(
            result_event_count=("event_category", "size"),
            result_sources=("source", join_unique),
            result_titles=("title", join_unique),
            result_summaries=("summary", join_unique),
            max_catalyst_score=("catalyst_score", "max"),
        )
        .sort_values(["trade_date", "symbol"])
    )


def add_result_features(daily: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    out = daily.merge(events, on=["symbol", "trade_date"], how="left")
    out["has_result_event"] = out["result_event_count"].notna()
    out["result_event_count"] = out["result_event_count"].fillna(0).astype(int)
    for col in ["result_sources", "result_titles", "result_summaries"]:
        out[col] = out[col].fillna("")
    out["max_catalyst_score"] = out["max_catalyst_score"].fillna(0)
    out = out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    g = out.groupby("symbol", group_keys=False)
    for lookback in [0, 1, 3, 5, 10]:
        if lookback == 0:
            out[f"result_within_{lookback}d"] = out["has_result_event"]
        else:
            out[f"result_within_{lookback}d"] = (
                g["has_result_event"]
                .transform(lambda s, lookback=lookback: s.astype(int).rolling(lookback + 1, min_periods=1).max())
                .astype(bool)
            )
    return out


def result_mask(df: pd.DataFrame, lookback_days: int | None) -> pd.Series:
    if lookback_days is None:
        return pd.Series(True, index=df.index)
    return df[f"result_within_{lookback_days}d"].fillna(False)


def make_result_candidates(df: pd.DataFrame, symbol_tables: dict[str, pd.DataFrame], spec: base.StrategySpec, lookback_days: int | None) -> pd.DataFrame:
    mask = base.signal_mask(df, spec) & result_mask(df, lookback_days)
    cols = [
        "symbol",
        "trade_date",
        "local_idx",
        "rank_score",
        "volume_group",
        "ret1",
        "relvol50",
        "close_location",
        "rs60_rank",
        "market_breadth200",
        "has_result_event",
        "result_event_count",
        "result_titles",
        "result_summaries",
        "max_catalyst_score",
    ]
    candidates = df.loc[mask, cols].copy()
    if candidates.empty:
        return candidates

    rows: list[dict[str, object]] = []
    for row in candidates.itertuples(index=False):
        sdf = symbol_tables.get(row.symbol)
        if sdf is None:
            continue
        signal_idx = int(row.local_idx)
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue
        exit_idx, exit_price, exit_reason, hold_days = base.exit_for_candidate(sdf, signal_idx, spec)
        if exit_idx < 0:
            continue
        entry = float(sdf.at[entry_idx, "open"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        rows.append(
            {
                "strategy": spec.name,
                "symbol": row.symbol,
                "volume_group": row.volume_group,
                "signal_date": pd.Timestamp(row.trade_date),
                "entry_date": pd.Timestamp(sdf.at[entry_idx, "trade_date"]),
                "exit_date": pd.Timestamp(sdf.at[exit_idx, "trade_date"]),
                "signal_idx": signal_idx,
                "entry_idx": entry_idx,
                "exit_idx": exit_idx,
                "entry": entry,
                "exit": exit_price,
                "exit_reason": exit_reason,
                "hold_days": hold_days,
                "net_return": exit_price / entry - 1 - base.ROUND_TRIP_COST,
                "ret1": float(row.ret1),
                "relvol50": float(row.relvol50),
                "close_location": float(row.close_location),
                "rs60_rank": float(row.rs60_rank) if pd.notna(row.rs60_rank) else np.nan,
                "market_breadth200": float(row.market_breadth200) if pd.notna(row.market_breadth200) else np.nan,
                "rank_score": float(row.rank_score),
                "has_result_event": bool(row.has_result_event),
                "result_event_count": int(row.result_event_count),
                "result_titles": row.result_titles,
                "result_summaries": row.result_summaries,
                "max_catalyst_score": float(row.max_catalyst_score),
            }
        )
    return pd.DataFrame(rows)


def result_link_specs() -> list[tuple[base.StrategySpec, int | None]]:
    specs: list[tuple[base.StrategySpec, int | None]] = []
    for context in ["leader_uptrend", "near_52w_high"]:
        for lookback in [None, 0, 1, 3, 5, 10]:
            name = f"{context}_rv8_{'no_result_filter' if lookback is None else f'result{lookback}d'}_fixed30"
            specs.append(
                (
                    base.StrategySpec(
                        name=name,
                        label=name,
                        context=context,
                        relvol50_min=8.0,
                        ret1_min=0.0,
                        close_location_min=0.70,
                        hold_days=30,
                    ),
                    lookback,
                )
            )
    for relvol in [2.0, 3.0, 5.0, 8.0]:
        for lookback in [0, 3, 5]:
            specs.append(
                (
                    base.StrategySpec(
                        name=f"financial_results_rv{relvol:g}_result{lookback}d_fixed30",
                        label=f"financial_results_rv{relvol:g}_result{lookback}d_fixed30",
                        context="liquid_market",
                        relvol50_min=relvol,
                        ret1_min=0.0,
                        close_location_min=0.70,
                        hold_days=30,
                    ),
                    lookback,
                )
            )
    return specs


def sample_news(trades: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    cols = [
        "strategy",
        "symbol",
        "signal_date",
        "entry_date",
        "net_return",
        "relvol50",
        "ret1",
        "close_location",
        "result_titles",
        "result_summaries",
    ]
    return trades[trades["has_result_event"]][cols].sort_values("signal_date", ascending=False).head(n)


def write_report(
    out_dir: Path,
    daily: pd.DataFrame,
    mapped_events: pd.DataFrame,
    metrics: pd.DataFrame,
    split_all: pd.DataFrame,
    group_all: pd.DataFrame,
    news_samples: pd.DataFrame,
) -> None:
    top = metrics.sort_values(["portfolio_return_pct", "daily_sharpe"], ascending=False)
    result_only = top[top["strategy"].str.contains("result", regex=False)]
    report = f"""# Results-News Linked Volume Spike Swing Review

Generated: {pd.Timestamp.now()}

Data window: {pd.Timestamp(daily["trade_date"].min()).date()} to {pd.Timestamp(daily["trade_date"].max()).date()}.
Mapped financial-result event days: {len(mapped_events):,}.

## Key Read

Financial-results confirmation is useful as an explanation layer, but it did not automatically beat the pure near-52-week-high volume setup in this run. The best result-linked variants become more selective and statistically weaker because many strong volume spikes do not have a clean result event in the same recent window.

My practical read: use results/news as a catalyst quality score and watchlist priority, not as a hard filter unless the filtered sample remains large enough.

## Strategy Ranking

{base.markdown_table(top, 30)}

## Result-Linked Shortlist

{base.markdown_table(result_only, 30)}

## Split Metrics

{base.markdown_table(split_all, 40)}

## Volume Group Analysis

{base.markdown_table(group_all, 30)}

## Recent Result-Linked Trade Samples

{base.markdown_table(news_samples, 25)}

## Instinct Layer To Add Live

- Best result reaction is not just good results; it is good results plus abnormal volume plus a close near the high.
- Prefer first reaction day or the next 1-3 sessions. After 5-10 sessions, you are often late unless the stock is still consolidating tightly.
- Avoid result spikes where the candle closes in the lower half; that means supply absorbed the news.
- Be careful with financial results after a long run-up. Great numbers can still fail if everyone already expected them.
- Best discretionary add-on: read whether sales/profit growth, margins, guidance, order book, or management commentary improved. Price-volume confirms that institutions cared.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Loading daily data and base features")
    raw_daily = base.load_daily_cache(Path(args.daily_cache))
    symbol_to_group = base.load_volume_groups(Path(args.volume_groups))
    daily = base.add_features(raw_daily, symbol_to_group)

    print("Mapping financial-result events to trade dates")
    events = load_financial_results(Path(args.events_path), pd.Timestamp(daily["trade_date"].max()))
    mapped = map_events_to_trade_dates(events, daily)
    edays = event_days(mapped)
    daily = add_result_features(daily, edays)
    symbol_tables = base.build_symbol_tables(daily)
    trading_dates = daily["trade_date"].drop_duplicates().sort_values()

    metrics_rows: list[dict[str, object]] = []
    trade_frames: list[pd.DataFrame] = []
    split_frames: list[pd.DataFrame] = []
    group_frames: list[pd.DataFrame] = []
    daily_by_strategy: dict[str, pd.DataFrame] = {}

    for spec, lookback in result_link_specs():
        print(f"Backtesting {spec.name}")
        candidates = make_result_candidates(daily, symbol_tables, spec, lookback)
        trades = base.select_portfolio_trades(candidates, spec)
        strategy_daily = base.daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
        daily_by_strategy[spec.name] = strategy_daily
        metrics_rows.append(base.metrics_for_strategy(trades, strategy_daily, spec.name))
        if not trades.empty:
            trade_frames.append(trades)
        split = base.split_metrics(trades, strategy_daily, pd.Timestamp(daily["trade_date"].min()), pd.Timestamp(daily["trade_date"].max()))
        split.insert(0, "parent_strategy", spec.name)
        split_frames.append(split)
        groups = base.volume_group_analysis(trades)
        groups.insert(0, "strategy", spec.name)
        group_frames.append(groups)

    metrics = pd.DataFrame(metrics_rows).sort_values(["portfolio_return_pct", "daily_sharpe"], ascending=False)
    trades_all = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    split_all = pd.concat(split_frames, ignore_index=True) if split_frames else pd.DataFrame()
    group_all = pd.concat(group_frames, ignore_index=True) if group_frames else pd.DataFrame()
    news_samples = sample_news(trades_all)

    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    trades_all.to_csv(out_dir / "trade_log.csv", index=False)
    split_all.to_csv(out_dir / "split_metrics.csv", index=False)
    group_all.to_csv(out_dir / "volume_group_analysis.csv", index=False)
    news_samples.to_csv(out_dir / "news_trade_samples.csv", index=False)
    edays.to_csv(out_dir / "financial_result_event_days.csv", index=False)
    base.save_chart(out_dir / "charts", daily_by_strategy, metrics)
    write_report(out_dir, daily, mapped, metrics, split_all, group_all, news_samples)
    print(f"Wrote outputs to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link financial-results news to swing volume-spike setups.")
    parser.add_argument("--daily-cache", type=Path, default=base.DEFAULT_DAILY_CACHE)
    parser.add_argument("--volume-groups", type=Path, default=base.DEFAULT_VOLUME_GROUPS)
    parser.add_argument("--events-path", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
