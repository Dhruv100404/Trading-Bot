#!/usr/bin/env python3
"""Research an actionable NSE event + volume strategy.

The study is deliberately separated into in-sample/validation/OOS periods.
Configuration selection uses only in-sample and validation metrics.  The OOS
slice is disclosed afterward.  Daily-bar ambiguity is resolved against the
strategy (stop before target), and all entries occur at the next session open.

This script refuses to run when the normalized event parquet predates the NSE
timestamp-parser fix.  That guard prevents the former ISO/day-first date swap
from entering the backtest.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import volume_news_swing_lab as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY = ROOT / "docs" / "complex_strategy_tuning_lab" / "daily_bars_cache.parquet"
DEFAULT_EVENTS = ROOT / "data" / "events" / "corporate_events.parquet"
DEFAULT_OUT = ROOT / "docs" / "news_action_strategy"
EVENT_PARSER = ROOT / "scripts" / "fetch_corporate_events.py"

TARGET_PCT = 5.0
MAX_HOLD_SESSIONS = 10
ROUND_TRIP_COST_PCT = 0.26
STOP_GRID = (2.5, 3.0, 4.0, 5.0)
MAX_NEW_PER_DAY = 5
MAX_OPEN_POSITIONS = 10

EVENT_SOURCES = [
    "nse_announcements",
    "nse_financial_results",
    "nse_integrated_financials",
]
EVENT_CATEGORIES = [
    "financial_results",
    "big_order",
    "merger_acquisition",
    "policy_regulatory",
    "fund_raise",
    "promoter_change",
    "management_change",
]

SPLITS = {
    "in_sample": (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-03-28")),
    "validation": (pd.Timestamp("2024-03-29"), pd.Timestamp("2025-04-27")),
    "out_of_sample": (pd.Timestamp("2025-04-28"), pd.Timestamp("2026-05-28")),
}


@dataclass(frozen=True)
class Config:
    config_id: str
    strategy_kind: str
    event_group: str
    context: str
    relvol_min: float | None
    ret1_min: float | None
    close_location_min: float | None


def check_event_provenance(events_path: Path, allow_stale: bool) -> None:
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    if not EVENT_PARSER.exists():
        raise FileNotFoundError(EVENT_PARSER)
    parser_text = EVENT_PARSER.read_text(encoding="utf-8")
    fixed_parser = "parse_exchange_timestamp" in parser_text and "dayfirst=not is_iso" in parser_text
    rebuilt_after_fix = events_path.stat().st_mtime >= EVENT_PARSER.stat().st_mtime
    if not allow_stale and (not fixed_parser or not rebuilt_after_fix):
        raise RuntimeError(
            "Event data provenance guard failed. corporate_events.parquet must be rebuilt "
            "after the ISO-aware parse_exchange_timestamp fix."
        )


def event_group_mask(df: pd.DataFrame, group: str) -> pd.Series:
    categories = df["event_categories"].fillna("").astype(str)
    if group == "any_catalyst":
        return df["has_event"].fillna(False)
    if group == "financial_results":
        return categories.str.contains(r"(?:^|\|)financial_results(?:\||$)", regex=True)
    if group == "business_catalyst":
        return categories.str.contains(
            r"(?:^|\|)(?:big_order|merger_acquisition|policy_regulatory|fund_raise)(?:\||$)",
            regex=True,
        )
    if group == "governance_change":
        return categories.str.contains(
            r"(?:^|\|)(?:promoter_change|management_change)(?:\||$)",
            regex=True,
        )
    if group == "none":
        return pd.Series(True, index=df.index)
    raise KeyError(group)


def make_configs() -> list[Config]:
    configs: list[Config] = []
    for group in ["any_catalyst", "financial_results", "business_catalyst", "governance_change"]:
        configs.append(
            Config(
                config_id=f"event_only__{group}__liquid_market",
                strategy_kind="event_only_baseline",
                event_group=group,
                context="liquid_market",
                relvol_min=None,
                ret1_min=None,
                close_location_min=None,
            )
        )

    for group in ["any_catalyst", "financial_results", "business_catalyst", "governance_change"]:
        for context in ["liquid_market", "leader_uptrend", "near_52w_high"]:
            for relvol_min in [2.0, 3.0, 5.0, 8.0]:
                for ret1_min in [0.0, 0.02, 0.05]:
                    config_id = (
                        f"event_volume__{group}__{context}__rv{relvol_min:g}"
                        f"__ret{ret1_min * 100:g}__cl70"
                    )
                    configs.append(
                        Config(
                            config_id=config_id,
                            strategy_kind="event_plus_volume",
                            event_group=group,
                            context=context,
                            relvol_min=relvol_min,
                            ret1_min=ret1_min,
                            close_location_min=0.70,
                        )
                    )

    for context in ["leader_uptrend", "near_52w_high"]:
        configs.append(
            Config(
                config_id=f"volume_only__{context}__rv8__ret0__cl70",
                strategy_kind="volume_only_baseline",
                event_group="none",
                context=context,
                relvol_min=8.0,
                ret1_min=0.0,
                close_location_min=0.70,
            )
        )
    return configs


def prepare_data(
    daily_path: Path,
    events_path: Path,
) -> tuple[pd.DataFrame, dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    raw_daily = base.read_daily(daily_path)
    daily = base.add_features(raw_daily, [MAX_HOLD_SESSIONS], ROUND_TRIP_COST_PCT / 100.0)
    daily["local_idx"] = daily.groupby("symbol", sort=False).cumcount().astype("int32")

    all_events = pd.read_parquet(events_path).copy()
    all_events["symbol"] = all_events["symbol"].fillna("").astype(str).str.upper().str.strip()
    all_events["event_time"] = pd.to_datetime(all_events["event_time"], errors="coerce")
    all_events["event_date"] = pd.to_datetime(all_events["event_date"], errors="coerce")
    eligible = all_events[
        all_events["symbol"].ne("")
        & all_events["event_date"].notna()
        & all_events["source"].isin(EVENT_SOURCES)
        & all_events["event_category"].isin(EVENT_CATEGORIES)
        & all_events["is_catalyst"].fillna(False)
    ].copy()
    eligible = eligible.drop_duplicates(["source", "source_event_id"], keep="last")
    max_price_date = pd.Timestamp(daily["trade_date"].max())
    post_price_rows = int((eligible["event_date"] > max_price_date).sum())
    events = eligible[eligible["event_date"] <= max_price_date + pd.Timedelta(days=7)].copy()
    mapped_raw = base.map_events_to_trade_dates(events, daily)

    # Integrated Filing-Financials, the legacy structured-results endpoint and
    # announcement text can all represent the same result.  A strategy gets one
    # result signal per symbol/effective trading day, not three duplicated votes.
    financial = mapped_raw[mapped_raw["event_category"].eq("financial_results")].copy()
    source_priority = {
        "nse_integrated_financials": 0,
        "nse_announcements": 1,
        "nse_financial_results": 2,
    }
    financial["source_priority"] = financial["source"].map(source_priority).fillna(9)
    financial = financial.sort_values(
        ["symbol", "trade_date", "source_priority", "event_time"]
    ).drop_duplicates(["symbol", "trade_date", "event_category"], keep="first")
    mapped = pd.concat(
        [mapped_raw[~mapped_raw["event_category"].eq("financial_results")], financial],
        ignore_index=True,
    )
    event_days = base.aggregate_event_days(mapped)
    joined = daily.merge(event_days, on=["symbol", "trade_date"], how="left")
    joined["has_event"] = joined["event_count"].notna()
    joined["event_count"] = joined["event_count"].fillna(0).astype("int32")
    joined["event_categories"] = joined["event_categories"].fillna("")
    joined["event_sources"] = joined["event_sources"].fillna("")
    joined["max_catalyst_score"] = joined["max_catalyst_score"].fillna(0).astype("float32")
    joined["sample_titles"] = joined["sample_titles"].fillna("")
    joined["sample_summaries"] = joined["sample_summaries"].fillna("")
    joined["signal_rank"] = (
        joined["relvol50"].clip(upper=25).fillna(0) * 1.5
        + joined["ret1"].fillna(0) * 100
        + joined["close_location"].fillna(0) * 2
        + joined["rs60_rank"].fillna(0) * 4
        + joined["max_catalyst_score"] / 50
    )

    bars: dict[str, dict[str, np.ndarray]] = {}
    for symbol, part in joined.groupby("symbol", sort=False):
        bars[str(symbol)] = {
            "dates": part["trade_date"].to_numpy(dtype="datetime64[ns]"),
            "open": part["open"].to_numpy(dtype=float),
            "high": part["high"].to_numpy(dtype=float),
            "low": part["low"].to_numpy(dtype=float),
            "close": part["close"].to_numpy(dtype=float),
        }

    coverage: dict[str, Any] = {
        "price_rows": int(len(joined)),
        "price_symbols": int(joined["symbol"].nunique()),
        "price_min_date": str(pd.Timestamp(joined["trade_date"].min()).date()),
        "price_max_date": str(pd.Timestamp(joined["trade_date"].max()).date()),
        "eligible_catalyst_filings_all_dates": int(len(eligible)),
        "eligible_catalyst_filings_through_price_cutoff": int(
            (eligible["event_date"] <= max_price_date).sum()
        ),
        "post_price_cutoff_filings_unevaluated": post_price_rows,
        "mapped_filing_rows_before_semantic_dedupe": int(len(mapped_raw)),
        "mapped_filing_rows_after_semantic_dedupe": int(len(mapped)),
        "mapped_symbol_event_days": int(len(event_days)),
        "mapped_symbols": int(event_days["symbol"].nunique()) if not event_days.empty else 0,
    }
    for group in ["financial_results", "business_catalyst", "governance_change"]:
        coverage[f"{group}_event_days"] = int(event_group_mask(joined, group).sum())
    return joined, bars, coverage


def context_mask(df: pd.DataFrame, context: str) -> pd.Series:
    masks = base.context_masks(df)
    return masks[context].fillna(False)


def config_mask(df: pd.DataFrame, config: Config) -> pd.Series:
    mask = context_mask(df, config.context)
    if config.event_group != "none":
        mask &= event_group_mask(df, config.event_group)
    if config.relvol_min is None:
        return mask.fillna(False)
    fresh = df["relvol50"].ge(config.relvol_min) & (
        df["prev_relvol50"].isna() | df["prev_relvol50"].lt(min(config.relvol_min, 1.5))
    )
    mask &= fresh
    mask &= df["ret1"].ge(float(config.ret1_min))
    mask &= df["close_location"].ge(float(config.close_location_min))
    mask &= df["close"].gt(df["open"])
    mask &= df["next_open"].notna()
    return mask.fillna(False)


def simulate_one(
    row: Any,
    stop_pct: float,
    bars: dict[str, dict[str, np.ndarray]],
) -> dict[str, Any] | None:
    symbol_bars = bars.get(str(row.symbol))
    if symbol_bars is None:
        return None
    start = int(row.local_idx) + 1
    if start >= len(symbol_bars["dates"]):
        return None
    entry = float(symbol_bars["open"][start])
    if not np.isfinite(entry) or entry <= 0:
        return None
    target = entry * (1.0 + TARGET_PCT / 100.0)
    stop = entry * (1.0 - stop_pct / 100.0)
    end = min(start + MAX_HOLD_SESSIONS, len(symbol_bars["dates"]))
    exit_idx = end - 1
    exit_price = float(symbol_bars["close"][exit_idx])
    exit_reason = "TIME"
    for idx in range(start, end):
        open_price = float(symbol_bars["open"][idx])
        high = float(symbol_bars["high"][idx])
        low = float(symbol_bars["low"][idx])
        if open_price <= stop:
            exit_idx, exit_price, exit_reason = idx, open_price, "STOP_GAP"
            break
        if open_price >= target:
            exit_idx, exit_price, exit_reason = idx, target, "TARGET_GAP"
            break
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            exit_idx, exit_price, exit_reason = idx, stop, "STOP_SAME_DAY"
            break
        if hit_stop:
            exit_idx, exit_price, exit_reason = idx, stop, "STOP"
            break
        if hit_target:
            exit_idx, exit_price, exit_reason = idx, target, "TARGET"
            break
    net_return = exit_price / entry - 1.0 - ROUND_TRIP_COST_PCT / 100.0
    return {
        "signal_id": int(row.Index),
        "symbol": str(row.symbol),
        "signal_date": pd.Timestamp(row.trade_date),
        "entry_date": pd.Timestamp(symbol_bars["dates"][start]),
        "exit_date": pd.Timestamp(symbol_bars["dates"][exit_idx]),
        "entry": entry,
        "target": target,
        "stop": stop,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "hold_sessions": exit_idx - start + 1,
        "net_return": net_return,
        "stop_pct": stop_pct,
        "signal_rank": float(row.signal_rank),
        "relvol50": float(row.relvol50) if pd.notna(row.relvol50) else np.nan,
        "ret1": float(row.ret1) if pd.notna(row.ret1) else np.nan,
        "close_location": float(row.close_location) if pd.notna(row.close_location) else np.nan,
        "rs60_rank": float(row.rs60_rank) if pd.notna(row.rs60_rank) else np.nan,
        "market_breadth200": float(row.market_breadth200) if pd.notna(row.market_breadth200) else np.nan,
        "event_categories": str(row.event_categories),
        "event_sources": str(row.event_sources),
        "event_count": int(row.event_count),
        "max_catalyst_score": float(row.max_catalyst_score),
        "event_titles": str(row.sample_titles),
        "event_summaries": str(row.sample_summaries),
    }


def select_portfolio(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    ordered = trades.sort_values(
        ["entry_date", "signal_rank", "symbol"], ascending=[True, False, True]
    )
    selected: list[int] = []
    symbol_exit: dict[str, pd.Timestamp] = {}
    active: list[pd.Timestamp] = []
    day_count: dict[pd.Timestamp, int] = {}
    for idx, row in ordered.iterrows():
        entry_date = pd.Timestamp(row["entry_date"])
        active = [exit_date for exit_date in active if exit_date >= entry_date]
        day = entry_date.normalize()
        if day_count.get(day, 0) >= MAX_NEW_PER_DAY:
            continue
        prior_exit = symbol_exit.get(str(row["symbol"]))
        if prior_exit is not None and prior_exit >= entry_date:
            continue
        if len(active) >= MAX_OPEN_POSITIONS:
            continue
        exit_date = pd.Timestamp(row["exit_date"])
        selected.append(idx)
        active.append(exit_date)
        symbol_exit[str(row["symbol"])] = exit_date
        day_count[day] = day_count.get(day, 0) + 1
    return ordered.loc[selected].reset_index(drop=True)


def metric_row(trades: pd.DataFrame) -> dict[str, Any]:
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
            "max_drawdown_10_slot_proxy_pct": 0.0,
        }
    ordered = trades.sort_values(["exit_date", "symbol"])
    returns = ordered["net_return"].astype(float)
    gains = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    equity = (1.0 + returns / MAX_OPEN_POSITIONS).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    reasons = ordered["exit_reason"].astype(str)
    return {
        "trades": int(len(ordered)),
        "win_rate_pct": round(float((returns > 0).mean() * 100), 2),
        "target_hit_pct": round(float(reasons.str.startswith("TARGET").mean() * 100), 2),
        "stop_exit_pct": round(float(reasons.str.startswith("STOP").mean() * 100), 2),
        "time_exit_pct": round(float((reasons == "TIME").mean() * 100), 2),
        "expectancy_pct": round(float(returns.mean() * 100), 3),
        "median_return_pct": round(float(returns.median() * 100), 3),
        "profit_factor": round(gains / losses, 3) if losses > 0 else float("inf"),
        "avg_hold_sessions": round(float(ordered["hold_sessions"].mean()), 2),
        "max_drawdown_10_slot_proxy_pct": round(float(drawdown.min() * 100), 3),
    }


def build_metrics(
    joined: pd.DataFrame,
    bars: dict[str, dict[str, np.ndarray]],
    configs: list[Config],
) -> tuple[pd.DataFrame, dict[tuple[str, float], pd.DataFrame]]:
    signal_ids: dict[str, np.ndarray] = {}
    all_ids: set[int] = set()
    for config in configs:
        ids = joined.index[config_mask(joined, config)].to_numpy(dtype=int)
        signal_ids[config.config_id] = ids
        all_ids.update(ids.tolist())

    signal_frame = joined.loc[sorted(all_ids)]
    simulated_rows: list[dict[str, Any]] = []
    for stop_pct in STOP_GRID:
        for row in signal_frame.itertuples(index=True):
            trade = simulate_one(row, stop_pct, bars)
            if trade is not None:
                simulated_rows.append(trade)
    simulated = pd.DataFrame(simulated_rows)
    by_stop = {
        stop: part.set_index("signal_id", drop=False)
        for stop, part in simulated.groupby("stop_pct", sort=False)
    }

    metric_rows: list[dict[str, Any]] = []
    selected_cache: dict[tuple[str, float], pd.DataFrame] = {}
    config_lookup = {config.config_id: config for config in configs}
    for config_id, ids in signal_ids.items():
        config = config_lookup[config_id]
        for stop_pct in STOP_GRID:
            table = by_stop[stop_pct]
            present = np.intersect1d(ids, table.index.to_numpy(dtype=int), assume_unique=False)
            candidates = table.loc[present].copy() if len(present) else table.iloc[0:0].copy()
            selected = select_portfolio(candidates)
            selected_cache[(config_id, stop_pct)] = selected
            base_row = {
                "config_id": config_id,
                "strategy_kind": config.strategy_kind,
                "event_group": config.event_group,
                "context": config.context,
                "relvol_min": config.relvol_min,
                "ret1_min_pct": None if config.ret1_min is None else config.ret1_min * 100,
                "close_location_min": config.close_location_min,
                "stop_pct": stop_pct,
            }
            metric_rows.append({**base_row, "split": "full", **metric_row(selected)})
            for split, (start, end) in SPLITS.items():
                part = selected[selected["entry_date"].between(start, end)]
                metric_rows.append({**base_row, "split": split, **metric_row(part)})
    return pd.DataFrame(metric_rows), selected_cache


def rank_without_oos(metrics: pd.DataFrame) -> pd.DataFrame:
    event_metrics = metrics[
        (metrics["strategy_kind"] == "event_plus_volume")
        & metrics["split"].isin(["in_sample", "validation"])
    ]
    wide = event_metrics.pivot(index=["config_id", "stop_pct"], columns="split")
    ranked = pd.DataFrame(index=wide.index).reset_index()
    for split in ["in_sample", "validation"]:
        for metric in ["trades", "expectancy_pct", "profit_factor", "target_hit_pct"]:
            ranked[f"{split}_{metric}"] = wide[metric][split].to_numpy()
    ranked = ranked[
        (ranked["in_sample_trades"] >= 30)
        & (ranked["validation_trades"] >= 10)
        & (ranked["in_sample_expectancy_pct"] > 0)
        & (ranked["validation_expectancy_pct"] > 0)
        & (ranked["in_sample_profit_factor"] > 1)
        & (ranked["validation_profit_factor"] > 1)
    ].copy()
    if ranked.empty:
        raise RuntimeError("No event-volume configuration passed in-sample/validation guardrails")
    ranked["selection_score"] = (
        ranked[["in_sample_expectancy_pct", "validation_expectancy_pct"]].min(axis=1)
        + 0.08
        * np.log(
            ranked[["in_sample_profit_factor", "validation_profit_factor"]]
            .min(axis=1)
            .clip(lower=1.0)
        )
        + 0.01 * np.log1p(ranked["validation_trades"])
    )
    return ranked.sort_values(
        ["selection_score", "validation_trades"], ascending=[False, False]
    ).reset_index(drop=True)


def audit_survivor(metrics: pd.DataFrame) -> tuple[str, float] | None:
    rows = metrics[
        (metrics["strategy_kind"] == "event_plus_volume") & metrics["split"].isin(SPLITS)
    ]
    wide = rows.pivot(index=["config_id", "stop_pct"], columns="split")
    candidates = pd.DataFrame(index=wide.index).reset_index()
    for split in SPLITS:
        candidates[f"{split}_expectancy_pct"] = wide["expectancy_pct"][split].to_numpy()
    candidates["oos_trades"] = wide["trades"]["out_of_sample"].to_numpy()
    candidates["oos_profit_factor"] = wide["profit_factor"]["out_of_sample"].to_numpy()
    expectation_cols = [f"{split}_expectancy_pct" for split in SPLITS]
    candidates = candidates[
        (candidates[expectation_cols] > 0).all(axis=1)
        & (candidates["oos_trades"] >= 10)
        & (candidates["oos_profit_factor"] >= 1.2)
    ].copy()
    if candidates.empty:
        return None
    candidates["worst_split"] = candidates[expectation_cols].min(axis=1)
    best = candidates.sort_values(["worst_split", "oos_trades"], ascending=[False, False]).iloc[0]
    return str(best["config_id"]), float(best["stop_pct"])


def clickhouse_json_rows(url: str, query: str) -> list[dict[str, Any]]:
    request = Request(url, data=(query + " FORMAT JSONEachRow").encode("utf-8"), method="POST")
    with urlopen(request, timeout=8) as response:
        text = response.read().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def api_json(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def measure_live_rows(
    deals: list[dict[str, Any]],
    linked_news: list[dict[str, Any]],
    price_symbols: set[str],
    max_price_date: pd.Timestamp,
    source: str,
) -> dict[str, Any]:
    deal_dates = pd.to_datetime(
        [row.get("event_date") or row.get("deal_date") for row in deals], errors="coerce"
    )
    news_dates = pd.to_datetime(
        [row.get("event_time") or row.get("published_at") for row in linked_news], errors="coerce"
    )
    deal_symbols = pd.Series([str(row.get("symbol") or "").upper() for row in deals])
    news_symbols = pd.Series([str(row.get("symbol") or "").upper() for row in linked_news])
    deal_linkable = deal_dates.notna() & (deal_dates <= max_price_date) & deal_symbols.isin(price_symbols)
    news_linkable = news_dates.notna() & (news_dates <= max_price_date) & news_symbols.isin(price_symbols)
    return {
        "status": "measured",
        "measurement_source": source,
        "large_deal_rows": int(len(deals)),
        "large_deal_distinct_dates": int(pd.Series(deal_dates.dropna().date).nunique()),
        "large_deal_min_date": str(deal_dates.min().date()) if deal_dates.notna().any() else None,
        "large_deal_max_date": str(deal_dates.max().date()) if deal_dates.notna().any() else None,
        "large_deal_rows_linkable_to_price_history": int(deal_linkable.sum()),
        "linked_news_rows": int(len(linked_news)),
        "linked_news_rows_with_timestamp": int(news_dates.notna().sum()),
        "linked_news_distinct_dates": int(pd.Series(news_dates.dropna().date).nunique()),
        "linked_news_rows_linkable_to_price_history": int(news_linkable.sum()),
        "backtest_eligible": bool(deal_linkable.sum() >= 100 or news_linkable.sum() >= 100),
    }


def live_history_coverage(
    clickhouse_url: str,
    app_url: str,
    price_symbols: set[str],
    max_price_date: pd.Timestamp,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "unavailable"}
    try:
        deals = clickhouse_json_rows(
            clickhouse_url,
            "SELECT symbol, toString(deal_date) AS event_date FROM trading.nse_large_deals FINAL",
        )
        linked_news = clickhouse_json_rows(
            clickhouse_url,
            "SELECT m.symbol AS symbol, toString(a.published_at) AS event_time "
            "FROM (SELECT * FROM trading.news_articles FINAL) AS a "
            "INNER JOIN (SELECT * FROM trading.news_mentions FINAL) AS m "
            "ON a.article_id = m.article_id",
        )
        result = measure_live_rows(
            deals, linked_news, price_symbols, max_price_date, "clickhouse_http"
        )
    except Exception as exc:
        try:
            deal_payload = api_json(f"{app_url.rstrip('/')}/api/nse/large-deals?limit=500")
            news_payload = api_json(f"{app_url.rstrip('/')}/api/news?limit=500")
            deals = list(deal_payload.get("deals", []))
            linked_news = [
                row
                for row in news_payload.get("news", [])
                if str(row.get("symbol") or "").strip()
            ]
            result = measure_live_rows(
                deals, linked_news, price_symbols, max_price_date, "app_api_fallback"
            )
            result["clickhouse_http_error"] = str(exc)
        except Exception as fallback_exc:
            result["error"] = str(exc)
            result["app_api_fallback_error"] = str(fallback_exc)
    return result


def split_records(metrics: pd.DataFrame, config_id: str, stop_pct: float) -> list[dict[str, Any]]:
    rows = metrics[
        (metrics["config_id"] == config_id) & (metrics["stop_pct"] == stop_pct)
    ].copy()
    return json.loads(rows.to_json(orient="records"))


def exact_rules(config: Config, stop_pct: float) -> list[str]:
    rules = [
        f"Event group: {config.event_group}; only catalyst-classified NSE filings.",
        "After-close filings are shifted to the next trading session before confirmation.",
        f"Context: {config.context}; price >= INR 25, prior 20-session volume >= 75k, "
        "prior traded value >= INR 7.5m, and market breadth >= 38%.",
    ]
    if config.relvol_min is not None:
        rules.extend(
            [
                f"Fresh volume spike: signal-day volume >= {config.relvol_min:g}x the prior "
                "50-session mean and previous-day relative volume < 1.5x.",
                f"Signal candle return >= {float(config.ret1_min) * 100:g}%, close above open, "
                f"and close-location >= {float(config.close_location_min):.2f}.",
            ]
        )
    rules.extend(
        [
            "Enter at the next session open; no signal-close fill assumption.",
            f"Gross target +{TARGET_PCT:.1f}%, fixed stop -{stop_pct:.1f}%, forced exit after "
            f"{MAX_HOLD_SESSIONS} sessions, {ROUND_TRIP_COST_PCT:.2f}% round-trip costs.",
            "If daily OHLC touches stop and target on the same session, assume the stop occurred first.",
            f"Portfolio limits: {MAX_OPEN_POSITIONS} positions, {MAX_NEW_PER_DAY} new entries/day, "
            "and no overlapping trade in the same symbol.",
        ]
    )
    return rules


def write_outputs(
    out_dir: Path,
    joined: pd.DataFrame,
    coverage: dict[str, Any],
    live_coverage: dict[str, Any],
    configs: list[Config],
    metrics: pd.DataFrame,
    ranked: pd.DataFrame,
    selected_cache: dict[tuple[str, float], pd.DataFrame],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_id = str(ranked.iloc[0]["config_id"])
    selected_stop = float(ranked.iloc[0]["stop_pct"])
    selected_trades = selected_cache[(selected_id, selected_stop)].copy()
    selected_trades["target_hit"] = selected_trades["exit_reason"].astype(str).str.startswith("TARGET")
    selected_trades["net_return_pct"] = (selected_trades["net_return"] * 100).round(3)
    selected_trades["strategy"] = selected_id
    selected_trades["outcome"] = selected_trades["exit_reason"]
    selected_trades["event_category"] = selected_trades["event_categories"]
    selected_trades["event_title"] = selected_trades["event_titles"]
    selected_trades["split"] = "outside_split"
    for split, (start, end) in SPLITS.items():
        selected_trades.loc[selected_trades["entry_date"].between(start, end), "split"] = split
    predictions = selected_trades.rename(
        columns={
            "entry": "entry_price",
            "target": "target_price",
            "stop": "stop_price",
            "exit": "exit_price",
        }
    )[
        [
            "strategy",
            "symbol",
            "signal_date",
            "entry_date",
            "exit_date",
            "entry_price",
            "target_price",
            "stop_price",
            "exit_price",
            "outcome",
            "target_hit",
            "hold_sessions",
            "net_return_pct",
            "split",
            "relvol50",
            "event_category",
            "event_title",
        ]
    ].sort_values(["signal_date", "symbol"], ascending=[False, True])
    predictions.to_csv(out_dir / "predictions.csv", index=False)
    predictions[predictions["target_hit"]].to_csv(
        out_dir / "successful_predictions.csv", index=False
    )
    metrics.to_csv(out_dir / "parameter_split_metrics.csv", index=False)
    ranked.to_csv(out_dir / "training_validation_ranking.csv", index=False)

    config_lookup = {config.config_id: config for config in configs}
    selected_config = config_lookup[selected_id]
    selected_metrics = split_records(metrics, selected_id, selected_stop)
    selected_oos = next(row for row in selected_metrics if row["split"] == "out_of_sample")
    clean_oos_pass = (
        int(selected_oos["trades"]) >= 20
        and float(selected_oos["expectancy_pct"]) > 0
        and float(selected_oos["profit_factor"]) > 1
    )
    metrics_by_split = {row["split"]: row for row in selected_metrics}
    status = "paper_only_candidate" if clean_oos_pass else "rejected_or_insufficient_oos"
    reason = (
        "Held-out results remained positive, but this is not a live recommendation: the sample is "
        "small, the configuration came from a grid, and it needs a new prospective paper sample."
        if clean_oos_pass
        else "The preselected rule did not retain enough positive held-out evidence."
    )

    survivor = audit_survivor(metrics)
    survivor_payload: dict[str, Any] | None = None
    if survivor is not None:
        survivor_id, survivor_stop = survivor
        survivor_payload = {
            "config_id": survivor_id,
            "stop_pct": survivor_stop,
            "status": "post_oos_hypothesis_only",
            "metrics": split_records(metrics, survivor_id, survivor_stop),
            "rules": exact_rules(config_lookup[survivor_id], survivor_stop),
        }

    baseline_rows = metrics[
        metrics["strategy_kind"].isin(["event_only_baseline", "volume_only_baseline"])
        & metrics["split"].isin(["full", "out_of_sample"])
    ].sort_values(["strategy_kind", "split", "expectancy_pct"], ascending=[True, True, False])
    baseline_summary = json.loads(baseline_rows.to_json(orient="records"))

    evidence = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "recommended": False,
        "status": status,
        "reason": reason,
        "metrics": metrics_by_split,
        "research_design": {
            "selection": "configuration and stop selected using in-sample + validation only",
            "oos_use": "out_of_sample disclosed only after selection",
            "splits": {
                key: {"start": str(start.date()), "end": str(end.date())}
                for key, (start, end) in SPLITS.items()
            },
            "multiple_testing_warning": "The grid creates selection risk even with a held-out OOS slice.",
        },
        "objective": {
            "target_pct_gross": TARGET_PCT,
            "max_hold_sessions": MAX_HOLD_SESSIONS,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "tested_stops_pct": list(STOP_GRID),
        },
        "data_coverage": coverage,
        "live_news_and_large_deal_history": live_coverage,
        "selected_without_oos": {
            "config_id": selected_id,
            "stop_pct": selected_stop,
            "rules": exact_rules(selected_config, selected_stop),
            "metrics": selected_metrics,
            "metrics_by_split": metrics_by_split,
            "clean_oos_pass": clean_oos_pass,
            "verdict": (
                "paper_only_candidate; requires new prospective sample"
                if clean_oos_pass
                else "not approved; OOS was negative or too small"
            ),
        },
        "post_oos_audit_survivor": survivor_payload,
        "baseline_metrics": baseline_summary,
        "limitations": [
            "Current RSS news and NSE large-deal tables do not contain a multi-year dated history; "
            "they are excluded from model selection unless coverage says otherwise.",
            "Corporate-event categories are keyword classifications, not a reading of earnings quality.",
            "Daily OHLC cannot identify true intraday target/stop order; stop-first is assumed.",
            "The latest price bar bounds the study and cannot authorize a newer live entry.",
        ],
    }
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    coverage_rows = [
        {"metric": key, "value": value}
        for key, value in {**coverage, **{f"live_{k}": v for k, v in live_coverage.items()}}.items()
    ]
    pd.DataFrame(coverage_rows).to_csv(out_dir / "coverage.csv", index=False)

    selected_table = metrics[
        (metrics["config_id"] == selected_id) & (metrics["stop_pct"] == selected_stop)
    ]
    top_rank = ranked.head(10)
    report = f"""# News/Event To Action Strategy Audit

Generated: {datetime.now().astimezone().isoformat()}

## Outcome

Selected without looking at OOS: `{selected_id}`, stop **-{selected_stop:.1f}%**, target
**+{TARGET_PCT:.1f}%**, maximum **{MAX_HOLD_SESSIONS} sessions**.  Clean OOS verdict:
**{'PASS FOR PAPER TESTING' if clean_oos_pass else 'NOT APPROVED'}**.

This is an action rule, not a headline prediction: a catalyst must be confirmed by a fresh
volume spike and strong price acceptance before the next-session entry.

## Selected temporal metrics

{base.markdown_table(selected_table, 20)}

## Exact entry and exit rules

""" + "\n".join(f"{i}. {rule}" for i, rule in enumerate(exact_rules(selected_config, selected_stop), 1)) + f"""

## Data coverage

- Price: {coverage['price_rows']:,} rows, {coverage['price_symbols']:,} symbols,
  {coverage['price_min_date']} to {coverage['price_max_date']}.
- Eligible catalyst filings through the price cutoff:
  {coverage['eligible_catalyst_filings_through_price_cutoff']:,}; mapped raw filings before semantic
  dedupe: {coverage['mapped_filing_rows_before_semantic_dedupe']:,}; mapped rows after dedupe:
  {coverage['mapped_filing_rows_after_semantic_dedupe']:,}; aggregated symbol-event days:
  {coverage['mapped_symbol_event_days']:,}. Post-cutoff filings left unevaluated:
  {coverage['post_price_cutoff_filings_unevaluated']:,}.
- Live news/deal coverage: `{json.dumps(live_coverage, sort_keys=True)}`.

## Top in-sample/validation configurations

{base.markdown_table(top_rank, 10)}

## Interpretation

- `predictions.csv` contains every historical signal and its realized outcome for the selected rule.
- `successful_predictions.csv` contains only rows that reached the +5% gross target before stop/time.
- The current news/deal feed is not used as fake history.  If it has fewer than 100 linkable dated
  observations, its proper role is ranking/confirmation, not a claimed backtested edge.
- Any configuration found after examining OOS is explicitly labelled post-OOS and is a new
  hypothesis, not independent proof.

Historical research is not a live trade recommendation.  Paper-test the exact frozen rule on new data.
"""
    (out_dir / "report.md").write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--clickhouse-url",
        default=os.getenv("CLICKHOUSE_HTTP_URL", "http://localhost:8123"),
    )
    parser.add_argument(
        "--app-url",
        default=os.getenv("TRADER_APP_URL", "http://localhost:3000"),
        help="App/API URL used when ClickHouse is not published to the host.",
    )
    parser.add_argument(
        "--allow-stale-event-data",
        action="store_true",
        help="Bypass the parser/parquet mtime provenance guard (not recommended).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    check_event_provenance(args.events, args.allow_stale_event_data)
    joined, bars, coverage = prepare_data(args.daily, args.events)
    configs = make_configs()
    metrics, selected_cache = build_metrics(joined, bars, configs)
    ranked = rank_without_oos(metrics)
    live_coverage = live_history_coverage(
        args.clickhouse_url,
        args.app_url,
        set(joined["symbol"].astype(str)),
        pd.Timestamp(joined["trade_date"].max()),
    )
    write_outputs(
        args.out_dir,
        joined,
        coverage,
        live_coverage,
        configs,
        metrics,
        ranked,
        selected_cache,
    )
    selected = ranked.iloc[0]
    print(f"selected={selected['config_id']} stop={selected['stop_pct']}%")
    print(f"outputs={args.out_dir}")


if __name__ == "__main__":
    main()
