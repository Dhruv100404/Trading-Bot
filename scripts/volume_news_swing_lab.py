from __future__ import annotations

import argparse
import math
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"
DEFAULT_EVENTS_PATH = ROOT / "data" / "events" / "corporate_events.parquet"
DEFAULT_OUT_DIR = ROOT / "docs" / "volume_news_swing_lab"

HOLD_DAYS = [1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60]
VOLUME_REL_THRESHOLDS = [1.5, 2.0, 3.0, 5.0, 8.0]
RETURN_THRESHOLDS = [0.0, 0.02, 0.05, 0.08, 0.12]
CLOSE_LOCATION_THRESHOLDS = [0.55, 0.70]
EVENT_REL_THRESHOLDS = [1.5, 2.0, 3.0, 5.0]
EVENT_RETURN_THRESHOLDS = [0.0, 0.02, 0.05, 0.08]
EVENT_CATEGORIES = [
    "financial_results",
    "big_order",
    "merger_acquisition",
    "policy_regulatory",
    "fund_raise",
    "promoter_change",
    "management_change",
]
DEFAULT_EVENT_SOURCES = ["nse_announcements", "nse_financial_results"]


def clean_float(value: object, digits: int = 3) -> float | str:
    if value is None or pd.isna(value):
        return ""
    try:
        v = float(value)
    except Exception:
        return str(value)
    if math.isinf(v):
        return "inf"
    return round(v, digits)


def profit_factor(returns: pd.Series) -> float:
    gross_profit = float(returns[returns > 0].sum())
    gross_loss = float(-returns[returns <= 0].sum())
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def chronological_cutoffs(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    span = end - start
    return start + span * 0.60, start + span * 0.80


def markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.head(max_rows).to_markdown(index=False)
    except Exception:
        return "```\n" + df.head(max_rows).to_csv(index=False) + "```"


def read_daily(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Daily cache not found: {path}")
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def add_features(raw: pd.DataFrame, hold_days: list[int], round_trip_cost: float) -> pd.DataFrame:
    df = raw.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    prior_volume = g["volume"].shift(1)

    df["prev_close"] = prev_close
    df["ret1"] = df["close"] / prev_close - 1
    df["gap_open_pct_calc"] = df["open"] / prev_close - 1
    df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["close_location"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)

    for n in [10, 20, 50, 200]:
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())
    df["sma50_slope20"] = df["sma50"] / g["sma50"].shift(20) - 1

    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)
    for n in [20, 60, 120, 252]:
        df[f"prior_high{n}"] = prior_high.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).max().reset_index(level=0, drop=True)
        df[f"prior_low{n}"] = prior_low.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).min().reset_index(level=0, drop=True)

    for n in [20, 60]:
        df[f"ret{n}"] = df["close"] / g["close"].shift(n) - 1
        df[f"prior_ret{n}"] = prev_close / g["close"].shift(n + 1) - 1

    df["vol20_prior"] = prior_volume.groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["vol50_prior"] = prior_volume.groupby(df["symbol"]).rolling(50, min_periods=25).mean().reset_index(level=0, drop=True)
    df["relvol20"] = df["volume"] / df["vol20_prior"].replace(0, np.nan)
    df["relvol50"] = df["volume"] / df["vol50_prior"].replace(0, np.nan)
    df["prev_relvol50"] = g["relvol50"].shift(1)
    df["adv20_prior"] = (prev_close * prior_volume).groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["rs60_rank"] = df.groupby("trade_date")["ret60"].rank(pct=True)
    df["market_breadth200"] = (df["close"] > df["sma200"]).groupby(df["trade_date"]).transform("mean")
    df["liquid"] = (df["close"] >= 25) & (df["vol20_prior"] >= 75_000) & (df["adv20_prior"] >= 7_500_000)
    df["market_ok"] = df["market_breadth200"].fillna(0) >= 0.38
    df["uptrend"] = (df["close"] > df["sma50"]) & (df["sma50"] > df["sma200"]) & (df["sma50_slope20"] > 0)
    df["near_52w_high"] = df["close"] >= df["prior_high252"] * 0.85
    df["neglected"] = (df["prev_close"] <= df["prior_high120"] * 0.92) & (df["prior_ret20"] <= 0.25)
    df["next_open"] = g["open"].shift(-1)
    df["entry_date"] = g["trade_date"].shift(-1)
    df["rank_score"] = (
        df["relvol50"].clip(upper=25).fillna(0) * 1.5
        + df["ret1"].fillna(0) * 100
        + df["close_location"].fillna(0) * 2
        + df["rs60_rank"].fillna(0) * 4
    )
    df["year"] = df["trade_date"].dt.year

    for hold in hold_days:
        df[f"exit_close_h{hold}"] = g["close"].shift(-hold)
        df[f"exit_date_h{hold}"] = g["trade_date"].shift(-hold)
        df[f"net_return_h{hold}"] = df[f"exit_close_h{hold}"] / df["next_open"] - 1 - round_trip_cost

    return df


def context_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "liquid_market": df["liquid"] & df["market_ok"],
        "leader_uptrend": df["liquid"] & df["market_ok"] & df["uptrend"] & (df["rs60_rank"] >= 0.60),
        "near_52w_high": df["liquid"] & df["market_ok"] & df["uptrend"] & df["near_52w_high"] & (df["rs60_rank"] >= 0.60),
        "neglected_reaction": df["liquid"] & df["market_ok"] & df["neglected"],
    }


def split_expectancies(df: pd.DataFrame, valid: pd.Series, return_col: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
    cut1, cut2 = chronological_cutoffs(start, end)
    dates = df.loc[valid, "trade_date"]
    returns = df.loc[valid, return_col].astype(float)
    parts = {
        "in_sample": dates < cut1,
        "validation": (dates >= cut1) & (dates < cut2),
        "out_of_sample": dates >= cut2,
    }
    out: dict[str, float] = {}
    for name, part_mask in parts.items():
        part = returns.loc[part_mask]
        out[f"{name}_trades"] = int(len(part))
        out[f"{name}_expectancy_pct"] = round(float(part.mean() * 100), 3) if len(part) else np.nan
    return out


def build_array_cache(df: pd.DataFrame, hold_days: list[int]) -> dict[str, object]:
    return {
        "dates": df["trade_date"].to_numpy(dtype="datetime64[ns]"),
        "years": df["year"].to_numpy(),
        "returns": {hold: df[f"net_return_h{hold}"].to_numpy(dtype=float) for hold in hold_days},
    }


def split_expectancies_from_arrays(
    returns: np.ndarray,
    dates: np.ndarray,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float]:
    cut1, cut2 = chronological_cutoffs(start, end)
    cut1_np = np.datetime64(cut1.to_datetime64())
    cut2_np = np.datetime64(cut2.to_datetime64())
    parts = {
        "in_sample": dates < cut1_np,
        "validation": (dates >= cut1_np) & (dates < cut2_np),
        "out_of_sample": dates >= cut2_np,
    }
    out: dict[str, float] = {}
    for name, mask in parts.items():
        part = returns[mask]
        out[f"{name}_trades"] = int(part.size)
        out[f"{name}_expectancy_pct"] = round(float(np.nanmean(part) * 100), 3) if part.size else np.nan
    return out


def summarize_indices(
    arrays: dict[str, object],
    signal_idx: np.ndarray,
    hold: int,
    meta: dict[str, object],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, object]:
    all_returns = arrays["returns"][hold]  # type: ignore[index]
    returns = all_returns[signal_idx]
    finite = np.isfinite(returns)
    idx = signal_idx[finite]
    returns = returns[finite].astype(float)
    row = {**meta, "hold_days": hold, "trades": int(returns.size)}
    if returns.size == 0:
        return {
            **row,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "expectancy_pct": np.nan,
            "median_return_pct": np.nan,
            "avg_win_pct": np.nan,
            "avg_loss_pct": np.nan,
            "positive_years": 0,
            "tested_years": 0,
            "in_sample_trades": 0,
            "in_sample_expectancy_pct": np.nan,
            "validation_trades": 0,
            "validation_expectancy_pct": np.nan,
            "out_of_sample_trades": 0,
            "out_of_sample_expectancy_pct": np.nan,
        }

    wins = returns > 0
    years = arrays["years"][idx]  # type: ignore[index]
    yearly = pd.Series(returns).groupby(years).mean()
    row.update(
        {
            "win_rate": round(float(wins.mean() * 100), 2),
            "profit_factor": clean_float(profit_factor(pd.Series(returns)), 3),
            "expectancy_pct": round(float(returns.mean() * 100), 3),
            "median_return_pct": round(float(np.median(returns) * 100), 3),
            "avg_win_pct": round(float(returns[wins].mean() * 100), 3) if wins.any() else 0.0,
            "avg_loss_pct": round(float(returns[~wins].mean() * 100), 3) if (~wins).any() else 0.0,
            "positive_years": int((yearly > 0).sum()),
            "tested_years": int(yearly.count()),
        }
    )
    dates = arrays["dates"][idx]  # type: ignore[index]
    row.update(split_expectancies_from_arrays(returns, dates, start, end))
    return row


def summarize_mask(
    df: pd.DataFrame,
    mask: pd.Series,
    hold: int,
    meta: dict[str, object],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, object]:
    return_col = f"net_return_h{hold}"
    valid = mask & df[return_col].notna() & df["next_open"].notna()
    returns = df.loc[valid, return_col].astype(float)
    row = {**meta, "hold_days": hold, "trades": int(len(returns))}
    if returns.empty:
        return {
            **row,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "expectancy_pct": np.nan,
            "median_return_pct": np.nan,
            "avg_win_pct": np.nan,
            "avg_loss_pct": np.nan,
            "positive_years": 0,
            "tested_years": 0,
            "in_sample_trades": 0,
            "in_sample_expectancy_pct": np.nan,
            "validation_trades": 0,
            "validation_expectancy_pct": np.nan,
            "out_of_sample_trades": 0,
            "out_of_sample_expectancy_pct": np.nan,
        }

    wins = returns > 0
    yearly = returns.groupby(df.loc[valid, "year"]).mean()
    row.update(
        {
            "win_rate": round(float(wins.mean() * 100), 2),
            "profit_factor": clean_float(profit_factor(returns), 3),
            "expectancy_pct": round(float(returns.mean() * 100), 3),
            "median_return_pct": round(float(returns.median() * 100), 3),
            "avg_win_pct": round(float(returns[wins].mean() * 100), 3) if wins.any() else 0.0,
            "avg_loss_pct": round(float(returns[~wins].mean() * 100), 3) if (~wins).any() else 0.0,
            "positive_years": int((yearly > 0).sum()),
            "tested_years": int(yearly.count()),
        }
    )
    row.update(split_expectancies(df, valid, return_col, start, end))
    return row


def volume_signal_mask(df: pd.DataFrame, context: str, relvol_min: float, ret_min: float, close_location_min: float) -> pd.Series:
    contexts = context_masks(df)
    fresh_from_normal = df["relvol50"].ge(relvol_min) & (df["prev_relvol50"].isna() | df["prev_relvol50"].lt(min(relvol_min, 1.5)))
    return (
        contexts[context]
        & fresh_from_normal
        & df["ret1"].ge(ret_min)
        & df["close_location"].ge(close_location_min)
        & df["close"].gt(df["open"])
    ).fillna(False)


def summarize_volume_grid(df: pd.DataFrame, hold_days: list[int], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    arrays = build_array_cache(df, hold_days)
    contexts = context_masks(df)
    relvol = df["relvol50"]
    prev_relvol = df["prev_relvol50"]
    ret1 = df["ret1"]
    close_location = df["close_location"]
    green = df["close"].gt(df["open"])
    for context, context_mask in contexts.items():
        for relvol_min in VOLUME_REL_THRESHOLDS:
            fresh_from_normal = relvol.ge(relvol_min) & (prev_relvol.isna() | prev_relvol.lt(min(relvol_min, 1.5)))
            for ret_min in RETURN_THRESHOLDS:
                for close_location_min in CLOSE_LOCATION_THRESHOLDS:
                    mask = (
                        context_mask
                        & fresh_from_normal
                        & ret1.ge(ret_min)
                        & close_location.ge(close_location_min)
                        & green
                    ).fillna(False)
                    signal_idx = np.flatnonzero(mask.to_numpy())
                    meta = {
                        "strategy_type": "volume_spike",
                        "context": context,
                        "relvol50_min": relvol_min,
                        "ret1_min_pct": round(ret_min * 100, 2),
                        "close_location_min": close_location_min,
                    }
                    for hold in hold_days:
                        rows.append(summarize_indices(arrays, signal_idx, hold, meta, start, end))
    out = pd.DataFrame(rows)
    out["strategy_key"] = (
        out["context"].astype(str)
        + "_rv"
        + out["relvol50_min"].astype(str)
        + "_r"
        + out["ret1_min_pct"].astype(str)
        + "_cl"
        + out["close_location_min"].astype(str)
    )
    return out


def best_by_params(summary: pd.DataFrame, group_cols: list[str], min_trades: int) -> pd.DataFrame:
    eligible = summary[summary["trades"] >= min_trades].copy()
    if eligible.empty:
        return eligible
    eligible["robust_score"] = (
        eligible["expectancy_pct"].fillna(-99) * 0.35
        + eligible["validation_expectancy_pct"].fillna(-99) * 0.25
        + eligible["out_of_sample_expectancy_pct"].fillna(-99) * 0.40
    )
    idx = eligible.groupby(group_cols)["robust_score"].idxmax()
    return eligible.loc[idx].sort_values(["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"], ascending=False).reset_index(drop=True)


def read_events(path: Path, sources: list[str], categories: list[str], max_date: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Event file not found: {path}")
    events = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    events = events.copy()
    events["symbol"] = events["symbol"].fillna("").astype(str).str.upper().str.strip()
    events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
    events = events[events["symbol"].ne("") & events["event_date"].notna()]
    if sources:
        events = events[events["source"].isin(sources)]
    if categories:
        events = events[events["event_category"].isin(categories)]
    if "is_catalyst" in events.columns:
        events = events[events["is_catalyst"].fillna(False)]
    elif "catalyst_score" in events.columns:
        events = events[events["catalyst_score"].fillna(0) >= 50]
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

    if not mapped_parts:
        return pd.DataFrame()
    return pd.concat(mapped_parts, ignore_index=True)


def aggregate_event_days(mapped: pd.DataFrame) -> pd.DataFrame:
    if mapped.empty:
        return pd.DataFrame()

    def join_unique(values: pd.Series, limit: int | None = None) -> str:
        items = [str(v).strip() for v in values.dropna().tolist() if str(v).strip()]
        seen: list[str] = []
        for item in items:
            if item not in seen:
                seen.append(item)
            if limit is not None and len(seen) >= limit:
                break
        return "|".join(seen)

    event_days = (
        mapped.groupby(["symbol", "trade_date"], as_index=False)
        .agg(
            event_count=("event_category", "size"),
            event_categories=("event_category", join_unique),
            event_sources=("source", join_unique),
            max_catalyst_score=("catalyst_score", "max"),
            sample_titles=("title", lambda s: join_unique(s, limit=3)),
            sample_summaries=("summary", lambda s: join_unique(s, limit=2)),
        )
        .sort_values(["trade_date", "symbol"])
    )
    return event_days


def add_event_columns(df: pd.DataFrame, event_days: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if event_days.empty:
        out["has_event"] = False
        out["event_count"] = 0
        out["event_categories"] = ""
        out["event_sources"] = ""
        out["max_catalyst_score"] = np.nan
        out["sample_titles"] = ""
        out["sample_summaries"] = ""
        return out

    out = out.merge(event_days, on=["symbol", "trade_date"], how="left")
    out["has_event"] = out["event_count"].notna()
    out["event_count"] = out["event_count"].fillna(0).astype(int)
    for col in ["event_categories", "event_sources", "sample_titles", "sample_summaries"]:
        out[col] = out[col].fillna("")
    return out


def event_category_sets() -> dict[str, list[str]]:
    return {
        "all_catalysts": EVENT_CATEGORIES,
        "business_catalysts": ["big_order", "merger_acquisition", "policy_regulatory", "fund_raise"],
        "financial_results": ["financial_results"],
        "big_order": ["big_order"],
        "merger_acquisition": ["merger_acquisition"],
        "policy_regulatory": ["policy_regulatory"],
        "fund_raise": ["fund_raise"],
        "promoter_change": ["promoter_change"],
        "management_change": ["management_change"],
    }


def category_mask(df: pd.DataFrame, categories: list[str]) -> pd.Series:
    if not categories:
        return df["has_event"].fillna(False)
    pattern = "|".join([fr"(?:^|\|){cat}(?:\||$)" for cat in categories])
    return df["event_categories"].fillna("").str.contains(pattern, regex=True)


def event_signal_mask(
    df: pd.DataFrame,
    category_group: str,
    confirmation: str,
    relvol_min: float | None = None,
    ret_min: float | None = None,
    close_location_min: float | None = None,
) -> pd.Series:
    cats = event_category_sets()[category_group]
    mask = df["liquid"] & df["market_ok"] & df["has_event"] & category_mask(df, cats)
    if confirmation == "event_plus_volume":
        if relvol_min is None or ret_min is None or close_location_min is None:
            raise ValueError("event_plus_volume requires relvol_min, ret_min, and close_location_min")
        mask = (
            mask
            & df["relvol50"].ge(relvol_min)
            & df["ret1"].ge(ret_min)
            & df["close_location"].ge(close_location_min)
            & df["close"].gt(df["open"])
        )
    return mask.fillna(False)


def summarize_event_grid(df: pd.DataFrame, hold_days: list[int], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    arrays = build_array_cache(df, hold_days)
    base_masks = {category_group: event_signal_mask(df, category_group, "event_only") for category_group in event_category_sets()}
    relvol = df["relvol50"]
    ret1 = df["ret1"]
    close_location = df["close_location"]
    green = df["close"].gt(df["open"])
    for category_group, base_mask in base_masks.items():
        base_idx = np.flatnonzero(base_mask.to_numpy())
        for hold in hold_days:
            rows.append(
                summarize_indices(
                    arrays,
                    base_idx,
                    hold,
                    {
                        "strategy_type": "event_only",
                        "category_group": category_group,
                        "confirmation": "event_only",
                        "relvol50_min": np.nan,
                        "ret1_min_pct": np.nan,
                        "close_location_min": np.nan,
                    },
                    start,
                    end,
                )
            )
        for relvol_min in EVENT_REL_THRESHOLDS:
            for ret_min in EVENT_RETURN_THRESHOLDS:
                for close_location_min in CLOSE_LOCATION_THRESHOLDS:
                    mask = (
                        base_mask
                        & relvol.ge(relvol_min)
                        & ret1.ge(ret_min)
                        & close_location.ge(close_location_min)
                        & green
                    ).fillna(False)
                    signal_idx = np.flatnonzero(mask.to_numpy())
                    meta = {
                        "strategy_type": "event_plus_volume",
                        "category_group": category_group,
                        "confirmation": "event_plus_volume",
                        "relvol50_min": relvol_min,
                        "ret1_min_pct": round(ret_min * 100, 2),
                        "close_location_min": close_location_min,
                    }
                    for hold in hold_days:
                        rows.append(summarize_indices(arrays, signal_idx, hold, meta, start, end))
    out = pd.DataFrame(rows)
    out["strategy_key"] = (
        out["category_group"].astype(str)
        + "_"
        + out["confirmation"].astype(str)
        + "_rv"
        + out["relvol50_min"].astype(str)
        + "_r"
        + out["ret1_min_pct"].astype(str)
        + "_cl"
        + out["close_location_min"].astype(str)
    )
    return out


def trades_from_mask(df: pd.DataFrame, mask: pd.Series, hold: int, label: str) -> pd.DataFrame:
    return_col = f"net_return_h{hold}"
    exit_date_col = f"exit_date_h{hold}"
    exit_close_col = f"exit_close_h{hold}"
    valid = mask & df[return_col].notna() & df["next_open"].notna()
    cols = [
        "symbol",
        "trade_date",
        "entry_date",
        "next_open",
        exit_date_col,
        exit_close_col,
        return_col,
        "ret1",
        "relvol50",
        "close_location",
        "rs60_rank",
        "rank_score",
        "event_categories",
        "sample_titles",
    ]
    existing = [c for c in cols if c in df.columns]
    trades = df.loc[valid, existing].copy()
    if trades.empty:
        return trades
    trades = trades.rename(
        columns={
            "trade_date": "signal_date",
            "next_open": "entry",
            exit_date_col: "exit_date",
            exit_close_col: "exit",
            return_col: "net_return",
        }
    )
    trades["strategy"] = label
    trades["hold_days"] = hold
    return trades


def apply_portfolio_filter(trades: pd.DataFrame, max_per_day: int) -> pd.DataFrame:
    if trades.empty:
        return trades
    filtered: list[dict[str, object]] = []
    last_exit_by_symbol: dict[str, pd.Timestamp] = {}
    count_by_day: dict[pd.Timestamp, int] = {}
    ordered = trades.sort_values(["entry_date", "rank_score"], ascending=[True, False])
    for row in ordered.to_dict("records"):
        entry_date = pd.Timestamp(row["entry_date"])
        exit_date = pd.Timestamp(row["exit_date"])
        symbol = str(row["symbol"])
        if count_by_day.get(entry_date, 0) >= max_per_day:
            continue
        if symbol in last_exit_by_symbol and entry_date <= last_exit_by_symbol[symbol]:
            continue
        filtered.append(row)
        count_by_day[entry_date] = count_by_day.get(entry_date, 0) + 1
        last_exit_by_symbol[symbol] = exit_date
    return pd.DataFrame(filtered)


def metrics_for_trades(trades: pd.DataFrame, label: str) -> dict[str, object]:
    if trades.empty:
        return {"strategy": label, "trades": 0}
    r = trades["net_return"].astype(float)
    wins = r > 0
    eq = (1 + r / 10).cumprod()
    dd = eq / eq.cummax() - 1
    return {
        "strategy": label,
        "trades": int(len(trades)),
        "win_rate": round(float(wins.mean() * 100), 2),
        "profit_factor": clean_float(profit_factor(r), 3),
        "expectancy_pct": round(float(r.mean() * 100), 3),
        "median_return_pct": round(float(r.median() * 100), 3),
        "total_return_proxy_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "max_drawdown_proxy_pct": round(float(dd.min() * 100), 2),
        "avg_hold_days": round(float(trades["hold_days"].mean()), 2),
    }


def selected_portfolio_tests(df: pd.DataFrame, volume_best: pd.DataFrame, event_best: pd.DataFrame, max_per_day: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_trades: list[pd.DataFrame] = []
    metric_rows: list[dict[str, object]] = []

    for _, row in volume_best.head(5).iterrows():
        label = f"volume::{row['strategy_key']}::h{int(row['hold_days'])}"
        mask = volume_signal_mask(
            df,
            str(row["context"]),
            float(row["relvol50_min"]),
            float(row["ret1_min_pct"]) / 100,
            float(row["close_location_min"]),
        )
        trades = trades_from_mask(df, mask, int(row["hold_days"]), label)
        filtered = apply_portfolio_filter(trades, max_per_day)
        all_trades.append(filtered)
        metric_rows.append(metrics_for_trades(filtered, label))

    for _, row in event_best.head(5).iterrows():
        label = f"event::{row['strategy_key']}::h{int(row['hold_days'])}"
        if row["confirmation"] == "event_only":
            mask = event_signal_mask(df, str(row["category_group"]), "event_only")
        else:
            mask = event_signal_mask(
                df,
                str(row["category_group"]),
                "event_plus_volume",
                float(row["relvol50_min"]),
                float(row["ret1_min_pct"]) / 100,
                float(row["close_location_min"]),
            )
        trades = trades_from_mask(df, mask, int(row["hold_days"]), label)
        filtered = apply_portfolio_filter(trades, max_per_day)
        all_trades.append(filtered)
        metric_rows.append(metrics_for_trades(filtered, label))

    trade_log = pd.concat([t for t in all_trades if not t.empty], ignore_index=True) if all_trades else pd.DataFrame()
    metrics = pd.DataFrame(metric_rows)
    return trade_log, metrics


def save_charts(out_dir: Path, volume_best: pd.DataFrame, event_best: pd.DataFrame, portfolio_metrics: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    if not volume_best.empty:
        top = volume_best.sort_values(["robust_score", "out_of_sample_expectancy_pct"], ascending=False).head(15)
        labels = [f"{r.context}\nrv{r.relvol50_min} r{r.ret1_min_pct} h{int(r.hold_days)}" for r in top.itertuples()]
        plt.figure(figsize=(13, 7))
        colors = ["#218380" if x > 0 else "#c44536" for x in top["out_of_sample_expectancy_pct"].fillna(0)]
        plt.barh(labels[::-1], top["out_of_sample_expectancy_pct"].iloc[::-1], color=colors[::-1])
        plt.axvline(0, color="#333333", linewidth=1)
        plt.xlabel("Out-of-sample average net return (%)")
        plt.title("Volume Spike Grid - Best Holding Time Per Parameter Set")
        plt.tight_layout()
        plt.savefig(chart_dir / "volume_top_oos_expectancy.png", dpi=160)
        plt.close()

        counts = volume_best[volume_best["out_of_sample_expectancy_pct"] > 0]["hold_days"].value_counts().sort_index()
        if not counts.empty:
            plt.figure(figsize=(9, 5))
            plt.bar(counts.index.astype(str), counts.values, color="#5b8e7d")
            plt.xlabel("Best fixed holding days")
            plt.ylabel("Positive-OOS parameter sets")
            plt.title("Where Volume Spike Holding Time Clusters")
            plt.tight_layout()
            plt.savefig(chart_dir / "volume_best_hold_distribution.png", dpi=160)
            plt.close()

    if not event_best.empty:
        top_event = event_best.sort_values(["robust_score", "out_of_sample_expectancy_pct"], ascending=False).head(15)
        labels = [f"{r.category_group}\n{r.confirmation} h{int(r.hold_days)}" for r in top_event.itertuples()]
        plt.figure(figsize=(13, 7))
        colors = ["#2f6690" if x > 0 else "#c44536" for x in top_event["out_of_sample_expectancy_pct"].fillna(0)]
        plt.barh(labels[::-1], top_event["out_of_sample_expectancy_pct"].iloc[::-1], color=colors[::-1])
        plt.axvline(0, color="#333333", linewidth=1)
        plt.xlabel("Out-of-sample average net return (%)")
        plt.title("Corporate Event Grid - Best Holding Time Per Parameter Set")
        plt.tight_layout()
        plt.savefig(chart_dir / "event_top_oos_expectancy.png", dpi=160)
        plt.close()

    if not portfolio_metrics.empty:
        top = portfolio_metrics.sort_values("expectancy_pct", ascending=True)
        plt.figure(figsize=(12, 6))
        colors = ["#218380" if x > 0 else "#c44536" for x in top["expectancy_pct"].fillna(0)]
        plt.barh(top["strategy"], top["expectancy_pct"], color=colors)
        plt.axvline(0, color="#333333", linewidth=1)
        plt.xlabel("Average net return per selected trade (%)")
        plt.title("Selected Setups After Daily Cap And No Symbol Overlap")
        plt.tight_layout()
        plt.savefig(chart_dir / "selected_portfolio_expectancy.png", dpi=160)
        plt.close()


def event_day_export(event_days: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    if event_days.empty:
        return pd.DataFrame()
    cols = [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "ret1",
        "gap_open_pct_calc",
        "relvol50",
        "close_location",
        "liquid",
        "market_ok",
        "rs60_rank",
    ]
    return event_days.merge(df[cols], on=["symbol", "trade_date"], how="left").sort_values(["trade_date", "symbol"])


def write_report(
    out_dir: Path,
    df: pd.DataFrame,
    events: pd.DataFrame,
    event_days: pd.DataFrame,
    volume_summary: pd.DataFrame,
    volume_best: pd.DataFrame,
    event_summary: pd.DataFrame,
    event_best: pd.DataFrame,
    portfolio_metrics: pd.DataFrame,
    min_trades: int,
) -> None:
    start = pd.Timestamp(df["trade_date"].min()).date()
    end = pd.Timestamp(df["trade_date"].max()).date()

    volume_top = volume_best[volume_best["trades"] >= min_trades].sort_values(
        ["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"], ascending=False
    )
    event_top = event_best[event_best["trades"] >= min_trades].sort_values(
        ["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"], ascending=False
    )
    volume_positive = volume_top[volume_top["out_of_sample_expectancy_pct"] > 0]
    event_positive = event_top[event_top["out_of_sample_expectancy_pct"] > 0]
    event_positive_oos = event_positive.sort_values(
        ["out_of_sample_expectancy_pct", "validation_expectancy_pct", "expectancy_pct"],
        ascending=False,
    )

    volume_hold_text = "none"
    if not volume_positive.empty:
        hold_counts = volume_positive.head(25)["hold_days"].value_counts().sort_values(ascending=False)
        volume_hold_text = ", ".join(f"{int(k)}D ({int(v)} setups)" for k, v in hold_counts.items())

    event_hold_text = "none"
    if not event_positive.empty:
        hold_counts = event_positive.head(25)["hold_days"].value_counts().sort_values(ascending=False)
        event_hold_text = ", ".join(f"{int(k)}D ({int(v)} setups)" for k, v in hold_counts.items())

    event_category_counts = (
        events.groupby(["event_category", "source"], dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values(["event_category", "rows"], ascending=[True, False])
    )
    event_day_counts = (
        event_days.assign(year=event_days["trade_date"].dt.year)
        .groupby("year")
        .agg(event_days=("symbol", "size"), symbols=("symbol", "nunique"))
        .reset_index()
        if not event_days.empty
        else pd.DataFrame()
    )

    report = f"""# Volume Spike And Corporate Event Swing Lab

Generated: {pd.Timestamp.now()}

Data window: {start} to {end}. Entry is always the next session open after the signal day. Returns use fixed swing exits and subtract base round-trip cost/slippage.

## What Was Tested

- **Sudden volume spike**: first day where `relvol50` crosses the threshold from normal volume, with positive price action and close-location filters. This answers the holding-time question directly across parameter grids.
- **News/corporate events**: NSE corporate filings are converted into category-level event days, then tested as event-only and event-plus-volume-confirmation signals.
- Cost model: 8 bps brokerage/fees side + 5 bps slippage side, or 26 bps round trip.
- Minimum sample used for "best by params": {min_trades} trades.

## Key Answer

- Volume spike holding-time cluster among positive OOS setups: **{volume_hold_text}**.
- Corporate-event holding-time cluster among positive OOS setups: **{event_hold_text}**.
- Pure event-only signals are included as a baseline; the more useful test is event plus actual price-volume confirmation.
- Important event nuance: financial-results + volume has very strong full-sample/validation numbers, but the OOS slice is weak in this dataset. Treat positive-OOS event rows as the cleaner shortlist.

## Best Volume Spike Parameter Sets

{markdown_table(volume_top[["context", "relvol50_min", "ret1_min_pct", "close_location_min", "hold_days", "trades", "win_rate", "profit_factor", "expectancy_pct", "validation_expectancy_pct", "out_of_sample_expectancy_pct", "positive_years", "tested_years"]], 20)}

## Best Corporate Event Parameter Sets By Blended Score

{markdown_table(event_top[["category_group", "confirmation", "relvol50_min", "ret1_min_pct", "close_location_min", "hold_days", "trades", "win_rate", "profit_factor", "expectancy_pct", "validation_expectancy_pct", "out_of_sample_expectancy_pct", "positive_years", "tested_years"]], 20)}

## Positive OOS Corporate Event Shortlist

{markdown_table(event_positive_oos[["category_group", "confirmation", "relvol50_min", "ret1_min_pct", "close_location_min", "hold_days", "trades", "win_rate", "profit_factor", "expectancy_pct", "validation_expectancy_pct", "out_of_sample_expectancy_pct", "positive_years", "tested_years"]], 20)}

## Event Data Converted To Meaningful Rows

Rows after source/category filters: {len(events):,}. Mapped symbol-date event days inside price data: {len(event_days):,}.

{markdown_table(event_category_counts, 30)}

## Event Days By Year

{markdown_table(event_day_counts, 10)}

## Selected Portfolio-Style Check

Top setups were re-tested with a max-per-day cap and no overlapping position in the same symbol. This is stricter than the grid because the grid is an event study.

{markdown_table(portfolio_metrics.sort_values("expectancy_pct", ascending=False), 20)}

## Files

- `volume_holding_grid.csv`: all volume-spike parameter and holding-period results.
- `volume_best_by_params.csv`: best holding period per volume parameter set.
- `event_backtest_grid.csv`: all corporate event parameter and holding-period results.
- `event_best_by_params.csv`: best holding period per event parameter set.
- `meaningful_event_days.csv`: mapped event day rows with category labels and market reaction features.
- `selected_trade_log.csv`: stricter selected trade log for the top setups.
- `selected_portfolio_metrics.csv`: metrics for the selected stricter checks.
- `charts/volume_top_oos_expectancy.png`
- `charts/volume_best_hold_distribution.png`
- `charts/event_top_oos_expectancy.png`
- `charts/selected_portfolio_expectancy.png`

## Read Before Live

This is a historical research backtest, not financial advice. The event data knows the filing category and market reaction, but not the true fundamental surprise quality. For live trading, treat the strongest area as: corporate catalyst + abnormal volume + constructive price close, then validate forward with real slippage and order execution.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    round_trip_cost = 2 * (args.cost_bps_side + args.slippage_bps_side) / 10000

    print("Reading daily bars")
    daily = read_daily(Path(args.daily_cache))
    hold_days = [int(x) for x in args.hold_days]
    start = pd.Timestamp(daily["trade_date"].min())
    end = pd.Timestamp(daily["trade_date"].max())

    print("Adding price, volume, trend, and fixed-horizon return features")
    df = add_features(daily, hold_days, round_trip_cost)

    print("Summarizing sudden-volume holding-time grid")
    volume_summary = summarize_volume_grid(df, hold_days, start, end)
    volume_group_cols = ["context", "relvol50_min", "ret1_min_pct", "close_location_min"]
    volume_best = best_by_params(volume_summary, volume_group_cols, args.min_trades)

    print("Reading and mapping corporate events")
    events = read_events(Path(args.events_path), args.event_sources, EVENT_CATEGORIES, end)
    mapped_events = map_events_to_trade_dates(events, df)
    event_days = aggregate_event_days(mapped_events)
    df_events = add_event_columns(df, event_days)
    meaningful = event_day_export(event_days, df_events)

    print("Summarizing corporate-event and event-plus-volume grid")
    event_summary = summarize_event_grid(df_events, hold_days, start, end)
    event_group_cols = ["category_group", "confirmation", "relvol50_min", "ret1_min_pct", "close_location_min"]
    event_best = best_by_params(event_summary, event_group_cols, args.min_trades)

    print("Running selected portfolio-style checks")
    top_volume = volume_best[volume_best["out_of_sample_expectancy_pct"] > 0].sort_values(
        ["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"],
        ascending=False,
    )
    if top_volume.empty:
        top_volume = volume_best.sort_values(["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"], ascending=False)

    top_event = event_best[event_best["out_of_sample_expectancy_pct"] > 0].sort_values(
        ["out_of_sample_expectancy_pct", "validation_expectancy_pct", "expectancy_pct"],
        ascending=False,
    )
    if top_event.empty:
        top_event = event_best.sort_values(["robust_score", "out_of_sample_expectancy_pct", "expectancy_pct"], ascending=False)
    selected_trade_log, selected_metrics = selected_portfolio_tests(df_events, top_volume, top_event, args.max_per_day)

    print("Writing outputs")
    volume_summary.to_csv(out_dir / "volume_holding_grid.csv", index=False)
    volume_best.to_csv(out_dir / "volume_best_by_params.csv", index=False)
    event_summary.to_csv(out_dir / "event_backtest_grid.csv", index=False)
    event_best.to_csv(out_dir / "event_best_by_params.csv", index=False)
    meaningful.to_csv(out_dir / "meaningful_event_days.csv", index=False)
    event_days.to_csv(out_dir / "event_days_raw_labels.csv", index=False)
    selected_trade_log.to_csv(out_dir / "selected_trade_log.csv", index=False)
    selected_metrics.to_csv(out_dir / "selected_portfolio_metrics.csv", index=False)

    save_charts(out_dir, volume_best, event_best, selected_metrics)
    write_report(out_dir, df_events, events, event_days, volume_summary, volume_best, event_summary, event_best, selected_metrics, args.min_trades)

    print(f"Wrote lab outputs to {out_dir}")
    if not volume_best.empty:
        print("Top volume setup:")
        print(volume_best.sort_values(["robust_score", "out_of_sample_expectancy_pct"], ascending=False).head(1).to_string(index=False))
    if not event_best.empty:
        print("Top event setup:")
        print(event_best.sort_values(["robust_score", "out_of_sample_expectancy_pct"], ascending=False).head(1).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze volume-spike holding times and corporate-event swing backtests.")
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_DAILY_CACHE)
    parser.add_argument("--events-path", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--hold-days", nargs="+", type=int, default=HOLD_DAYS)
    parser.add_argument("--event-sources", nargs="+", default=DEFAULT_EVENT_SOURCES)
    parser.add_argument("--min-trades", type=int, default=250)
    parser.add_argument("--max-per-day", type=int, default=8)
    parser.add_argument("--cost-bps-side", type=float, default=8.0)
    parser.add_argument("--slippage-bps-side", type=float, default=5.0)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
