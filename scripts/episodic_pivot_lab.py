from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except Exception:  # pragma: no cover
    pq = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_DAILY_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"
DEFAULT_OUT_DIR = ROOT / "docs" / "episodic_pivot_lab"
DEFAULT_EVENTS_PATH = ROOT / "data" / "events" / "corporate_catalysts.parquet"


COST_SCENARIOS = {
    "optimistic": {"cost_bps_side": 4.0, "slippage_bps_side": 3.0},
    "base": {"cost_bps_side": 8.0, "slippage_bps_side": 5.0},
    "stress": {"cost_bps_side": 8.0, "slippage_bps_side": 15.0},
}


@dataclass(frozen=True)
class EPVariant:
    name: str
    description: str
    event_ret_min: float
    event_relvol_min: float
    neglected_high_discount: float
    max_pre_event_ret20: float
    max_setup_days: int
    max_setup_range_pct: float
    max_setup_relvol50: float
    ema10_buffer: float
    max_entry_gap: float
    stop_style: str
    exit_style: str
    min_event_gap_pct: float | None = None
    max_event_gap_pct: float | None = None
    min_prior_ret20_pct: float | None = None
    max_event_ret_pct: float | None = None
    min_setup_days: int = 1
    max_hold_days: int = 90
    max_trades_per_day: int = 8


def monthly_files(parquet_dir: Path) -> list[Path]:
    return sorted(parquet_dir.glob("candles_20*.parquet"))


def build_daily_cache(parquet_dir: Path, cache_path: Path, refresh: bool = False) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)
    if pq is None:
        raise RuntimeError("pyarrow is required to aggregate parquet candles locally.")

    frames: list[pd.DataFrame] = []
    columns = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume", "day_open", "gap_pct", "vwap"]
    for path in monthly_files(parquet_dir):
        print(f"Aggregating {path.name}")
        df = pq.read_table(path, columns=columns).to_pandas()
        df = df.dropna(subset=["date", "symbol", "open", "high", "low", "close"])
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
        df = df[(df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1))]
        df = df.sort_values(["symbol", "date", "bucket"])
        grouped = df.groupby(["symbol", "date"], sort=False)
        daily = grouped.agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            day_open=("day_open", "first"),
            gap_pct=("gap_pct", "first"),
            volume=("volume", "sum"),
            close_vwap=("vwap", "last"),
            buckets=("bucket", "nunique"),
        ).reset_index()
        daily = daily[daily["buckets"] >= 300]
        frames.append(daily)

    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = pd.to_datetime(out.pop("date"))
    out = out.sort_values(["symbol", "trade_date"]).drop_duplicates(["symbol", "trade_date"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache_path, index=False)
    return out


def add_ep_features(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = tr.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)
    df["prev_close"] = prev_close
    df["ret1"] = df["close"] / prev_close - 1
    df["gap_open_pct_calc"] = df["open"] / prev_close - 1
    df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["close_location"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)

    for n in [5, 10, 20, 50, 60, 120, 200]:
        df[f"ema{n}"] = g["close"].transform(lambda s, n=n: s.ewm(span=n, adjust=False, min_periods=max(5, n // 2)).mean())
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())

    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)
    for n in [20, 60, 120, 252]:
        df[f"prior_high{n}"] = prior_high.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).max().reset_index(level=0, drop=True)
        df[f"prior_low{n}"] = prior_low.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).min().reset_index(level=0, drop=True)

    for n in [5, 20, 60, 120]:
        df[f"ret{n}"] = df["close"] / g["close"].shift(n) - 1
        df[f"prior_ret{n}"] = prev_close / g["close"].shift(n + 1) - 1

    prior_volume = g["volume"].shift(1)
    df["vol20_prior"] = prior_volume.groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["vol50_prior"] = prior_volume.groupby(df["symbol"]).rolling(50, min_periods=25).mean().reset_index(level=0, drop=True)
    df["relvol20"] = df["volume"] / df["vol20_prior"].replace(0, np.nan)
    df["relvol50"] = df["volume"] / df["vol50_prior"].replace(0, np.nan)
    df["adv20_prior"] = (prev_close * prior_volume).groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["rs60_rank"] = df.groupby("trade_date")["ret60"].rank(pct=True)
    df["market_breadth200"] = (df["close"] > df["sma200"]).groupby(df["trade_date"]).transform("mean")

    df["pre_event_discount_120"] = prev_close / df["prior_high120"].replace(0, np.nan) - 1
    df["pre_event_range120_pct"] = df["prior_high120"] / df["prior_low120"].replace(0, np.nan) - 1
    df["liquid"] = (df["close"] >= 25) & (df["vol20_prior"] >= 75_000) & (df["adv20_prior"] >= 7_500_000)
    df["next_open"] = g["open"].shift(-1)
    df["year"] = df["trade_date"].dt.year
    return df


def make_variants() -> list[EPVariant]:
    return [
        EPVariant(
            name="delayed_ep_strict_4pct_partial3r",
            description=(
                "Strict price-volume proxy: neglected stock, >=12% event close, >=5x prior 50D volume; "
                "buy stop above a tight EMA10 setup candle; 4% stop; sell half at 3R and trail the rest by EMA20."
            ),
            event_ret_min=0.12,
            event_relvol_min=5.0,
            neglected_high_discount=-0.08,
            max_pre_event_ret20=0.20,
            max_setup_days=18,
            max_setup_range_pct=0.075,
            max_setup_relvol50=2.0,
            ema10_buffer=0.035,
            max_entry_gap=0.06,
            stop_style="fixed_4pct",
            exit_style="partial_3r_ema20",
        ),
        EPVariant(
            name="delayed_ep_medium_4pct_partial3r",
            description=(
                "Looser EP proxy: >=8% event close and >=3x prior 50D volume; same delayed buy-stop entry, "
                "4% stop, half out at 3R, remaining shares trail EMA20."
            ),
            event_ret_min=0.08,
            event_relvol_min=3.0,
            neglected_high_discount=-0.04,
            max_pre_event_ret20=0.25,
            max_setup_days=20,
            max_setup_range_pct=0.085,
            max_setup_relvol50=2.5,
            ema10_buffer=0.045,
            max_entry_gap=0.08,
            stop_style="fixed_4pct",
            exit_style="partial_3r_ema20",
        ),
        EPVariant(
            name="delayed_ep_tight_setup_low_stop",
            description=(
                "Uses the transcript's tighter candle-low stop idea. Event >=10%, relvol >=4x; setup must be tight, "
                "entry is a buy stop above setup high, stop is setup low with max 7% risk, exit at 3R/trail."
            ),
            event_ret_min=0.10,
            event_relvol_min=4.0,
            neglected_high_discount=-0.06,
            max_pre_event_ret20=0.22,
            max_setup_days=18,
            max_setup_range_pct=0.065,
            max_setup_relvol50=1.8,
            ema10_buffer=0.035,
            max_entry_gap=0.06,
            stop_style="setup_low_cap7",
            exit_style="partial_3r_ema20",
        ),
        EPVariant(
            name="delayed_ep_office_5r_or_ema20",
            description=(
                "Part-time trader variant: wider candidate set, 4% stop, no early 3R scale-out; exits only at 5R, "
                "close below EMA20, or max hold."
            ),
            event_ret_min=0.08,
            event_relvol_min=3.0,
            neglected_high_discount=-0.04,
            max_pre_event_ret20=0.25,
            max_setup_days=24,
            max_setup_range_pct=0.085,
            max_setup_relvol50=2.5,
            ema10_buffer=0.05,
            max_entry_gap=0.08,
            stop_style="fixed_4pct",
            exit_style="full_5r_ema20",
            max_hold_days=120,
            max_trades_per_day=6,
        ),
        EPVariant(
            name="delayed_ep_low_volume_accumulation",
            description=(
                "Low-volume delayed EP variant inspired by the 3:15 PM accumulation comment: setup candle must have "
                "below-average volume, then buy stop above its high; 4% stop and 3R/EMA20 exit."
            ),
            event_ret_min=0.08,
            event_relvol_min=3.5,
            neglected_high_discount=-0.04,
            max_pre_event_ret20=0.25,
            max_setup_days=24,
            max_setup_range_pct=0.08,
            max_setup_relvol50=1.0,
            ema10_buffer=0.05,
            max_entry_gap=0.08,
            stop_style="fixed_4pct",
            exit_style="partial_3r_ema20",
            max_hold_days=90,
            max_trades_per_day=6,
        ),
        EPVariant(
            name="delayed_ep_prime_low_volume",
            description=(
                "Prime delayed EP slice found from cleaned catalyst data: event gap must be constructive but not stretched, "
                "stock cannot be in a deep 20D slide before the event, event-day move is capped to avoid chase, and setup "
                "must spend 6-12 sessions tightening before the buy-stop trigger."
            ),
            event_ret_min=0.08,
            event_relvol_min=3.5,
            neglected_high_discount=-0.04,
            max_pre_event_ret20=0.25,
            max_setup_days=12,
            max_setup_range_pct=0.08,
            max_setup_relvol50=1.0,
            ema10_buffer=0.05,
            max_entry_gap=0.08,
            stop_style="fixed_4pct",
            exit_style="partial_3r_ema20",
            min_event_gap_pct=0.5,
            max_event_gap_pct=8.0,
            min_prior_ret20_pct=-5.0,
            max_event_ret_pct=15.0,
            min_setup_days=6,
            max_hold_days=90,
            max_trades_per_day=6,
        ),
        EPVariant(
            name="delayed_ep_prime_balanced",
            description=(
                "Less selective prime slice: constructive event gap, pre-event 20D return above -5%, and at least six "
                "sessions of delayed tightening. Keeps larger event candles to reduce overfitting."
            ),
            event_ret_min=0.08,
            event_relvol_min=3.5,
            neglected_high_discount=-0.04,
            max_pre_event_ret20=0.25,
            max_setup_days=16,
            max_setup_range_pct=0.08,
            max_setup_relvol50=1.0,
            ema10_buffer=0.05,
            max_entry_gap=0.08,
            stop_style="fixed_4pct",
            exit_style="partial_3r_ema20",
            min_event_gap_pct=0.5,
            max_event_gap_pct=8.0,
            min_prior_ret20_pct=-5.0,
            min_setup_days=6,
            max_hold_days=90,
            max_trades_per_day=6,
        ),
    ]


def load_catalyst_events(path: Path, sources: list[str], categories: list[str]) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Event file not found: {path}")
    if path.suffix.lower() == ".parquet":
        events = pd.read_parquet(path)
    else:
        events = pd.read_csv(path)
    events = events.copy()
    events["symbol"] = events["symbol"].fillna("").astype(str).str.upper().str.strip()
    events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce").dt.date
    events = events[events["symbol"].ne("") & events["event_date"].notna()]
    if sources:
        events = events[events["source"].isin(sources)]
    if categories:
        events = events[events["event_category"].isin(categories)]
    if "catalyst_score" in events.columns:
        events = events[events["catalyst_score"].fillna(0) >= 50]
    return events


def add_catalyst_flags(df: pd.DataFrame, events: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    out = df.copy()
    if events.empty:
        out["has_catalyst"] = False
        out["catalyst_categories"] = ""
        out["catalyst_titles"] = ""
        out["catalyst_sources"] = ""
        return out

    active: dict[tuple[str, object], dict[str, set[str]]] = {}
    cutoff_time = pd.Timestamp("15:30").time()
    for row in events.itertuples(index=False):
        symbol = str(row.symbol).upper().strip()
        event_date = row.event_date
        event_time = getattr(row, "event_time", pd.NaT)
        if not symbol or pd.isna(event_date):
            continue
        start_offset = 0
        if not pd.isna(event_time) and pd.Timestamp(event_time).time() >= cutoff_time:
            start_offset = 1
        event_dt = pd.Timestamp(event_date).date()
        for offset in range(start_offset, lookback_days + 1):
            active_date = event_dt + timedelta(days=offset)
            key = (symbol, active_date)
            bucket = active.setdefault(key, {"categories": set(), "titles": set(), "sources": set()})
            bucket["categories"].add(str(getattr(row, "event_category", "")))
            title = str(getattr(row, "title", "") or getattr(row, "summary", "") or "")
            if title:
                bucket["titles"].add(title[:160])
            bucket["sources"].add(str(getattr(row, "source", "")))

    keys = list(zip(out["symbol"].astype(str).str.upper(), pd.to_datetime(out["trade_date"]).dt.date))
    flags = [active.get(key) for key in keys]
    out["has_catalyst"] = [flag is not None for flag in flags]
    out["catalyst_categories"] = ["|".join(sorted(flag["categories"])) if flag else "" for flag in flags]
    out["catalyst_titles"] = [" || ".join(sorted(flag["titles"])[:3]) if flag else "" for flag in flags]
    out["catalyst_sources"] = ["|".join(sorted(flag["sources"])) if flag else "" for flag in flags]
    return out


def event_mask(d: pd.DataFrame, variant: EPVariant, require_catalyst: bool = False) -> pd.Series:
    neglected = (
        (d["liquid"])
        & (d["prev_close"] <= d["prior_high120"] * (1 + variant.neglected_high_discount))
        & (d["prior_ret20"] <= variant.max_pre_event_ret20)
        & (d["prior_ret5"] <= 0.18)
        & (d["pre_event_range120_pct"] <= 1.50)
    )
    strong_reaction = (
        (d["ret1"] >= variant.event_ret_min)
        & (d["relvol50"] >= variant.event_relvol_min)
        & (d["close_location"] >= 0.65)
        & (d["close"] > d["open"])
    )
    if variant.min_event_gap_pct is not None:
        strong_reaction = strong_reaction & (d["gap_open_pct_calc"] * 100 >= variant.min_event_gap_pct)
    if variant.max_event_gap_pct is not None:
        strong_reaction = strong_reaction & (d["gap_open_pct_calc"] * 100 <= variant.max_event_gap_pct)
    if variant.min_prior_ret20_pct is not None:
        strong_reaction = strong_reaction & (d["prior_ret20"] * 100 >= variant.min_prior_ret20_pct)
    if variant.max_event_ret_pct is not None:
        strong_reaction = strong_reaction & (d["ret1"] * 100 <= variant.max_event_ret_pct)
    mask = neglected & strong_reaction
    if require_catalyst:
        mask = mask & d.get("has_catalyst", False)
    return mask


def setup_ok(row: pd.Series, event: pd.Series, variant: EPVariant) -> bool:
    if pd.isna(row["ema10"]) or pd.isna(row["relvol50"]):
        return False
    if row["low"] < event["low"] * 0.98:
        return False
    if row["close"] < event["close"] * 0.86:
        return False
    if row["close"] > event["close"] * 1.22:
        return False
    if row["range_pct"] > variant.max_setup_range_pct:
        return False
    if row["range_pct"] > event["range_pct"] * 0.75:
        return False
    if row["relvol50"] > variant.max_setup_relvol50:
        return False
    near_ema10 = row["low"] <= row["ema10"] * (1 + variant.ema10_buffer)
    held_ema10 = row["close"] >= row["ema10"] * 0.97
    if not near_ema10 or not held_ema10:
        return False
    return bool(row["close_location"] >= 0.35)


def stop_price(entry: float, setup: pd.Series, variant: EPVariant) -> float | None:
    if variant.stop_style == "fixed_4pct":
        return entry * 0.96
    if variant.stop_style == "setup_low_cap7":
        stop = float(setup["low"]) * 0.9975
        risk_pct = (entry - stop) / entry
        if risk_pct <= 0 or risk_pct > 0.07:
            return None
        return stop
    raise ValueError(f"Unknown stop_style {variant.stop_style}")


def simulate_exit(
    sdf: pd.DataFrame,
    entry_idx: int,
    entry: float,
    stop: float,
    variant: EPVariant,
    cost_bps_side: float,
    slippage_bps_side: float,
) -> dict | None:
    risk = entry - stop
    if entry <= 0 or risk <= 0:
        return None

    target3 = entry + 3.0 * risk
    target5 = entry + 5.0 * risk
    active_stop = stop
    partial_done = False
    partial_return = 0.0
    exit_price = None
    exit_date = None
    exit_reason = "time"
    hold = 0

    for j in range(entry_idx, min(entry_idx + variant.max_hold_days, len(sdf))):
        hold = j - entry_idx + 1
        bar = sdf.iloc[j]
        low = float(bar["low"])
        high = float(bar["high"])
        close = float(bar["close"])
        ema20 = float(bar["ema20"]) if not pd.isna(bar["ema20"]) else math.nan
        exit_date = pd.Timestamp(bar["trade_date"])

        if low <= active_stop:
            exit_price = active_stop
            exit_reason = "stop_after_partial" if partial_done else "initial_stop"
            break

        if variant.exit_style == "partial_3r_ema20" and not partial_done and high >= target3:
            partial_return = (target3 / entry - 1) * 0.5
            partial_done = True
            active_stop = max(active_stop, entry)
            if low <= active_stop:
                exit_price = active_stop
                exit_reason = "same_day_breakeven_after_3r"
                break

        if variant.exit_style == "full_5r_ema20" and high >= target5:
            exit_price = target5
            exit_reason = "target_5r"
            break

        if hold >= 2 and math.isfinite(ema20) and close < ema20:
            exit_price = close
            exit_reason = "close_below_ema20"
            break

        exit_price = close

    if exit_price is None or exit_date is None:
        return None

    if partial_done:
        gross_return = partial_return + (exit_price / entry - 1) * 0.5
    else:
        gross_return = exit_price / entry - 1
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    net_return = gross_return - round_cost
    return {
        "exit_date": exit_date,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "hold_days": hold,
        "gross_return": gross_return,
        "net_return": net_return,
        "partial_3r_hit": partial_done,
        "r_multiple_net": net_return / (risk / entry),
    }


def candidate_trades_for_variant(
    df: pd.DataFrame,
    variant: EPVariant,
    cost_bps_side: float,
    slippage_bps_side: float,
    trigger_valid_days: int,
    require_catalyst: bool = False,
) -> pd.DataFrame:
    d = df.copy()
    d["is_ep_event"] = event_mask(d, variant, require_catalyst=require_catalyst).fillna(False)
    trades: list[dict] = []

    for symbol, sdf0 in d.groupby("symbol", sort=False):
        sdf = sdf0.reset_index(drop=True)
        event_indices = np.flatnonzero(sdf["is_ep_event"].to_numpy())
        if len(event_indices) == 0:
            continue

        for event_idx in event_indices:
            event = sdf.iloc[event_idx]
            setup_idx = None
            for j in range(event_idx + variant.min_setup_days, min(event_idx + variant.max_setup_days + 1, len(sdf) - 1)):
                row = sdf.iloc[j]
                if setup_ok(row, event, variant):
                    setup_idx = j
                    break
            if setup_idx is None:
                continue

            setup = sdf.iloc[setup_idx]
            trigger = float(setup["high"]) * 1.001
            entry_idx = None
            entry = None
            for k in range(setup_idx + 1, min(setup_idx + trigger_valid_days + 1, len(sdf))):
                bar = sdf.iloc[k]
                if float(bar["high"]) < trigger:
                    continue
                proposed_entry = max(float(bar["open"]), trigger)
                if proposed_entry > trigger * (1 + variant.max_entry_gap):
                    break
                entry_idx = k
                entry = proposed_entry
                break
            if entry_idx is None or entry is None:
                continue

            stop = stop_price(entry, setup, variant)
            if stop is None:
                continue
            risk_pct = (entry - stop) / entry
            if risk_pct <= 0 or risk_pct > 0.09:
                continue

            exit_result = simulate_exit(sdf, entry_idx, entry, stop, variant, cost_bps_side, slippage_bps_side)
            if exit_result is None:
                continue

            event_score = (
                float(event["ret1"]) * 100
                + min(float(event["relvol50"]), 25.0) * 1.4
                + float(event["close_location"]) * 3.0
                - max(float(setup["range_pct"]) * 100, 0) * 0.25
            )
            trades.append(
                {
                    "strategy": variant.name,
                    "family": "Delayed Episodic Pivot",
                    "symbol": symbol,
                    "event_date": pd.Timestamp(event["trade_date"]),
                    "setup_date": pd.Timestamp(setup["trade_date"]),
                    "entry_date": pd.Timestamp(sdf.at[entry_idx, "trade_date"]),
                    "entry": entry,
                    "stop": stop,
                    "risk_pct": risk_pct,
                    "setup_high": float(setup["high"]),
                    "setup_low": float(setup["low"]),
                    "trigger": trigger,
                    "event_return_pct": float(event["ret1"]) * 100,
                    "event_relvol50": float(event["relvol50"]),
                    "event_gap_pct": float(event["gap_open_pct_calc"]) * 100,
                    "event_close_location": float(event["close_location"]),
                    "event_prior_ret20_pct": float(event["prior_ret20"]) * 100,
                    "catalyst_categories": event.get("catalyst_categories", ""),
                    "catalyst_titles": event.get("catalyst_titles", ""),
                    "catalyst_sources": event.get("catalyst_sources", ""),
                    "days_event_to_setup": int(setup_idx - event_idx),
                    "days_setup_to_entry": int(entry_idx - setup_idx),
                    "rank_score": event_score,
                    "year": pd.Timestamp(sdf.at[entry_idx, "trade_date"]).year,
                    **exit_result,
                }
            )

    return pd.DataFrame(trades)


def apply_portfolio_filters(trades: pd.DataFrame, max_trades_per_day: int) -> pd.DataFrame:
    if trades.empty:
        return trades
    filtered: list[dict] = []
    last_exit_by_symbol: dict[str, pd.Timestamp] = {}
    day_count: dict[pd.Timestamp, int] = {}
    ordered = trades.sort_values(["entry_date", "rank_score"], ascending=[True, False])

    for trade in ordered.to_dict("records"):
        entry_date = pd.Timestamp(trade["entry_date"])
        symbol = str(trade["symbol"])
        if day_count.get(entry_date, 0) >= max_trades_per_day:
            continue
        if symbol in last_exit_by_symbol and entry_date <= last_exit_by_symbol[symbol]:
            continue
        filtered.append(trade)
        day_count[entry_date] = day_count.get(entry_date, 0) + 1
        last_exit_by_symbol[symbol] = pd.Timestamp(trade["exit_date"])

    return pd.DataFrame(filtered)


def apply_cost_model(trades: pd.DataFrame, cost_bps_side: float, slippage_bps_side: float) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    out = trades.copy()
    round_cost = 2 * (cost_bps_side + slippage_bps_side) / 10000
    out["net_return"] = out["gross_return"] - round_cost
    out["r_multiple_net"] = out["net_return"] / out["risk_pct"].replace(0, np.nan)
    return out


def streaks(wins: pd.Series) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for win in wins.astype(bool):
        if win:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def metrics_for_trades(trades: pd.DataFrame, label: str) -> dict:
    if trades.empty:
        return {"strategy": label, "trades": 0}
    t = trades.sort_values("entry_date")
    r = t["net_return"]
    wins = r > 0
    gross_profit = r[r > 0].sum()
    gross_loss = -r[r <= 0].sum()
    pf = gross_profit / gross_loss if gross_loss > 0 else math.inf
    eq = (1 + r / 10).cumprod()
    dd = eq / eq.cummax() - 1
    span_days = max((pd.Timestamp(t["entry_date"].max()) - pd.Timestamp(t["entry_date"].min())).days, 1)
    best_w, best_l = streaks(wins)
    downside = r[r < 0].std(ddof=0)
    return {
        "strategy": label,
        "trades": int(len(t)),
        "trades_per_month": round(len(t) / (span_days / 30.4375), 3),
        "win_rate": round(float(wins.mean() * 100), 2),
        "profit_factor": round(float(pf), 3) if math.isfinite(pf) else "inf",
        "expectancy_pct": round(float(r.mean() * 100), 3),
        "median_return_pct": round(float(r.median() * 100), 3),
        "avg_win_pct": round(float(r[wins].mean() * 100), 3) if wins.any() else 0,
        "avg_loss_pct": round(float(r[~wins].mean() * 100), 3) if (~wins).any() else 0,
        "avg_r_multiple": round(float(t["r_multiple_net"].mean()), 3),
        "partial_3r_hit_pct": round(float(t["partial_3r_hit"].mean() * 100), 2) if "partial_3r_hit" in t else 0,
        "total_return_proxy_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "max_drawdown_proxy_pct": round(float(dd.min() * 100), 2),
        "sharpe_trade": round(float(r.mean() / r.std(ddof=0) * math.sqrt(252)), 3) if r.std(ddof=0) > 0 else 0,
        "sortino_trade": round(float(r.mean() / downside * math.sqrt(252)), 3) if downside and downside > 0 else 0,
        "longest_win_streak": best_w,
        "longest_loss_streak": best_l,
        "avg_hold_days": round(float(t["hold_days"].mean()), 2),
    }


def chronological_cutoffs(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    span = end - start
    return start + span * 0.60, start + span * 0.80


def split_metrics(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    cut1, cut2 = chronological_cutoffs(start, end)
    for strategy, part in trades.groupby("strategy"):
        ranges = [
            ("in_sample", start, cut1, part["entry_date"] < cut1),
            ("validation", cut1, cut2, (part["entry_date"] >= cut1) & (part["entry_date"] < cut2)),
            ("out_of_sample", cut2, end, part["entry_date"] >= cut2),
        ]
        for split, lo, hi, mask in ranges:
            row = metrics_for_trades(part.loc[mask], strategy)
            row["split"] = split
            row["range_start"] = str(pd.Timestamp(lo).date())
            row["range_end"] = str(pd.Timestamp(hi).date())
            rows.append(row)
    return pd.DataFrame(rows)


def yearly_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for (strategy, year), part in trades.groupby(["strategy", "year"]):
        row = metrics_for_trades(part, strategy)
        row["year"] = int(year)
        rows.append(row)
    return pd.DataFrame(rows)


def exit_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["strategy", "exit_reason"])
        .agg(
            trades=("net_return", "size"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
            expectancy_pct=("net_return", lambda s: round(float(s.mean() * 100), 3)),
            avg_hold_days=("hold_days", "mean"),
        )
        .reset_index()
    )


def symbol_contribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    return (
        trades.groupby(["strategy", "symbol"])
        .agg(
            trades=("net_return", "size"),
            net_return_sum=("net_return", "sum"),
            avg_net_return=("net_return", "mean"),
            win_rate=("net_return", lambda s: round(float((s > 0).mean() * 100), 2)),
        )
        .reset_index()
        .sort_values(["strategy", "net_return_sum"], ascending=[True, False])
    )


def save_charts(out_dir: Path, trade_log: pd.DataFrame, metrics: pd.DataFrame, yearly: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    if not metrics.empty:
        m = metrics.sort_values("expectancy_pct", ascending=True)
        plt.figure(figsize=(11, 6))
        colors = ["#2a9d8f" if x > 0 else "#c44536" for x in m["expectancy_pct"]]
        plt.barh(m["strategy"], m["expectancy_pct"], color=colors)
        plt.axvline(0, color="#333333", linewidth=1)
        plt.title("Delayed Episodic Pivot Expectancy After Costs")
        plt.xlabel("Average net return per trade (%)")
        plt.tight_layout()
        plt.savefig(chart_dir / "expectancy_by_strategy.png", dpi=160)
        plt.close()

        plt.figure(figsize=(9, 6))
        plt.scatter(metrics["trades"], metrics["profit_factor"].replace("inf", 99).astype(float), s=90, c=metrics["expectancy_pct"], cmap="viridis")
        for _, row in metrics.iterrows():
            plt.annotate(str(row["strategy"]).replace("_", "\n"), (row["trades"], float(row["profit_factor"]) if row["profit_factor"] != "inf" else 99), fontsize=7)
        plt.axhline(1, color="#333333", linewidth=1)
        plt.title("Profit Factor vs Sample Size")
        plt.xlabel("Trades")
        plt.ylabel("Profit factor")
        plt.tight_layout()
        plt.savefig(chart_dir / "pf_vs_sample_size.png", dpi=160)
        plt.close()

    if not trade_log.empty and not metrics.empty:
        top = metrics.sort_values("expectancy_pct", ascending=False).head(5)["strategy"].tolist()
        plt.figure(figsize=(12, 6))
        for name in top:
            part = trade_log[trade_log["strategy"].eq(name)].sort_values("entry_date")
            eq = (1 + part["net_return"] / 10).cumprod()
            plt.plot(pd.to_datetime(part["entry_date"]), eq, label=name)
        plt.title("Top EP Variants - Proxy Equity Curves")
        plt.ylabel("Growth of 1.0, 10 equal slots")
        plt.legend(fontsize=8)
        plt.tight_layout()
        plt.savefig(chart_dir / "top_equity_curves.png", dpi=160)
        plt.close()

    if not yearly.empty:
        pivot = yearly.pivot_table(index="strategy", columns="year", values="expectancy_pct", aggfunc="first").fillna(0)
        plt.figure(figsize=(12, 6))
        plt.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-3, vmax=3)
        plt.colorbar(label="Expectancy %")
        plt.xticks(range(len(pivot.columns)), pivot.columns, rotation=45)
        plt.yticks(range(len(pivot.index)), pivot.index, fontsize=8)
        plt.title("Yearly Expectancy Heatmap")
        plt.tight_layout()
        plt.savefig(chart_dir / "yearly_expectancy_heatmap.png", dpi=160)
        plt.close()


def write_report(
    out_dir: Path,
    variants: list[EPVariant],
    metrics: pd.DataFrame,
    splits: pd.DataFrame,
    yearly: pd.DataFrame,
    exits: pd.DataFrame,
    symbols: pd.DataFrame,
    cost_metrics: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    require_catalyst: bool,
    events_path: Path | None,
) -> None:
    rules = "\n".join(f"- `{v.name}`: {v.description}" for v in variants)
    ranked = metrics.sort_values(["expectancy_pct", "profit_factor"], ascending=[False, False]) if not metrics.empty else metrics
    split_view = (
        splits.pivot_table(index="strategy", columns="split", values="expectancy_pct", aggfunc="first").reset_index()
        if not splits.empty
        else pd.DataFrame()
    )
    cost_view = (
        cost_metrics.pivot_table(index="strategy", columns="cost_scenario", values="expectancy_pct", aggfunc="first").reset_index()
        if not cost_metrics.empty
        else pd.DataFrame()
    )
    positive = ranked[ranked["expectancy_pct"] > 0] if not ranked.empty and "expectancy_pct" in ranked else pd.DataFrame()
    least_bad = ranked.head(3)["strategy"].tolist() if not ranked.empty else []
    verdict = (
        "No variant had positive full-sample expectancy after base costs."
        if positive.empty
        else "Positive full-sample candidates: " + ", ".join(positive.head(3)["strategy"].tolist())
    )
    least_bad_text = ", ".join(least_bad) if least_bad else "none generated trades"

    report = f"""# Delayed Episodic Pivot Strategy Lab

Generated: {pd.Timestamp.now()}

Data window: {start.date()} to {end.date()}. Entries are delayed buy-stop fills after a daily event and setup candle.

Event confirmation: {"ON" if require_catalyst else "OFF"}{f" using `{events_path}`" if require_catalyst and events_path else ""}.

## Transcript Translation

This implements the strategy discussed in the transcript as a testable price-volume proxy:

1. Find a neglected liquid stock: not near its prior 120-day high and not already strongly up before the event.
2. Detect an episodic-pivot event: large one-day advance, strong close, and abnormal relative volume versus prior 50 days.
3. Do not buy the event candle.
4. Wait for a tight delayed setup near EMA10 with lower volume.
5. Place a buy stop above the setup candle high for the next few sessions.
6. Use either a fixed 4% stop or the setup-candle low.
7. Exit with the transcript-style half at 3R plus EMA20 trail, or a slower 5R/EMA20 variant.

## Important Limitation

The price dataset has OHLCV candles but no order-book or point-in-time fundamental surprise data. When event confirmation is ON, the test requires a normalized NSE catalyst row near the EP candle, but it still does not know whether the event was fundamentally good, bad, or surprising enough. It only knows that a qualifying exchange filing existed.

## Verdict

{verdict}

Least negative variants by base-cost expectancy: {least_bad_text}

## Ranked Variants By Base-Cost Expectancy

{ranked.to_markdown(index=False) if not ranked.empty else "No trades generated."}

## Tested Rules

{rules}

## Chronological Split Expectancy

{split_view.to_markdown(index=False) if not split_view.empty else "No split metrics generated."}

## Cost Sensitivity

{cost_view.to_markdown(index=False) if not cost_view.empty else "No cost metrics generated."}

## Year-By-Year Metrics

{yearly.to_markdown(index=False) if not yearly.empty else "No yearly metrics generated."}

## Exit Behavior

{exits.sort_values(["strategy", "trades"], ascending=[True, False]).to_markdown(index=False) if not exits.empty else "No exit metrics generated."}

## Top Symbol Contribution

{symbols.groupby("strategy").head(8).to_markdown(index=False) if not symbols.empty else "No symbol contribution rows."}

## Generated Files

- `trade_log.csv`
- `strategy_metrics.csv`
- `split_metrics.csv`
- `yearly_metrics.csv`
- `exit_metrics.csv`
- `symbol_contribution.csv`
- `cost_scenario_metrics.csv`
- `charts/expectancy_by_strategy.png`
- `charts/pf_vs_sample_size.png`
- `charts/top_equity_curves.png`
- `charts/yearly_expectancy_heatmap.png`

## Next Production Upgrade

Add a catalyst column: earnings date, big order, management change, policy news, or sector shock. The transcript's real edge is not just the candle; it is the surprise plus institutional under-ownership. This script currently tests the candle and volume reaction only.
"""
    (out_dir / "final_report.md").write_text(report, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_daily = build_daily_cache(Path(args.parquet_dir), Path(args.daily_cache), refresh=args.refresh_cache)
    daily = add_ep_features(raw_daily)
    events_path = Path(args.events_path) if args.events_path else None
    if args.require_catalyst:
        if events_path is None:
            raise ValueError("--events-path is required when --require-catalyst is set")
        print(f"Loading catalyst events from {events_path}")
        events = load_catalyst_events(events_path, args.event_sources, args.event_categories)
        print(f"Loaded {len(events):,} catalyst rows after source/category filters")
        daily = add_catalyst_flags(daily, events, lookback_days=args.event_lookback_days)
    start = pd.Timestamp(daily["trade_date"].min())
    end = pd.Timestamp(daily["trade_date"].max())
    variants = make_variants()

    base_frames: list[pd.DataFrame] = []
    cost_frames: list[pd.DataFrame] = []
    metrics_rows: list[dict] = []
    cost_metric_rows: list[dict] = []

    for variant in variants:
        print(f"Backtesting {variant.name}")
        gross_trades = candidate_trades_for_variant(
            daily,
            variant,
            trigger_valid_days=args.trigger_valid_days,
            cost_bps_side=0.0,
            slippage_bps_side=0.0,
            require_catalyst=args.require_catalyst,
        )
        gross_trades = apply_portfolio_filters(gross_trades, variant.max_trades_per_day)
        base_trades = pd.DataFrame()
        for scenario, costs in COST_SCENARIOS.items():
            trades = apply_cost_model(gross_trades, **costs)
            if not trades.empty:
                trades["cost_scenario"] = scenario
                trades["cost_bps_side"] = costs["cost_bps_side"]
                trades["slippage_bps_side"] = costs["slippage_bps_side"]
                cost_frames.append(trades)
            row = metrics_for_trades(trades, variant.name)
            row["cost_scenario"] = scenario
            row["cost_bps_side"] = costs["cost_bps_side"]
            row["slippage_bps_side"] = costs["slippage_bps_side"]
            cost_metric_rows.append(row)
            if scenario == "base":
                base_trades = trades.drop(columns=["cost_scenario", "cost_bps_side", "slippage_bps_side"], errors="ignore")

        if not base_trades.empty:
            base_frames.append(base_trades)
        metrics_rows.append(metrics_for_trades(base_trades, variant.name))

    trade_log = pd.concat(base_frames, ignore_index=True) if base_frames else pd.DataFrame()
    cost_trade_log = pd.concat(cost_frames, ignore_index=True) if cost_frames else pd.DataFrame()
    metrics = pd.DataFrame(metrics_rows)
    cost_metrics = pd.DataFrame(cost_metric_rows)
    splits = split_metrics(trade_log, start, end)
    yearly = yearly_metrics(trade_log)
    exits = exit_metrics(trade_log)
    symbols = symbol_contribution(trade_log)

    trade_log.to_csv(out_dir / "trade_log.csv", index=False)
    cost_trade_log.to_csv(out_dir / "cost_scenario_trade_log.csv", index=False)
    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    cost_metrics.to_csv(out_dir / "cost_scenario_metrics.csv", index=False)
    splits.to_csv(out_dir / "split_metrics.csv", index=False)
    yearly.to_csv(out_dir / "yearly_metrics.csv", index=False)
    exits.to_csv(out_dir / "exit_metrics.csv", index=False)
    symbols.to_csv(out_dir / "symbol_contribution.csv", index=False)
    save_charts(out_dir, trade_log, metrics[metrics["trades"] > 0] if "trades" in metrics else metrics, yearly)
    write_report(
        out_dir,
        variants,
        metrics,
        splits,
        yearly,
        exits,
        symbols,
        cost_metrics,
        start,
        end,
        require_catalyst=args.require_catalyst,
        events_path=events_path,
    )

    print(f"Done. Outputs saved to {out_dir}")
    print(metrics.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest delayed episodic-pivot swing variants on local daily bars.")
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_DAILY_CACHE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--trigger-valid-days", type=int, default=3)
    parser.add_argument("--require-catalyst", action="store_true", help="Require a normalized catalyst event near the EP reaction day.")
    parser.add_argument("--events-path", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--event-lookback-days", type=int, default=3)
    parser.add_argument(
        "--event-sources",
        nargs="+",
        default=["nse_announcements", "nse_financial_results"],
        help="Event sources allowed for catalyst confirmation.",
    )
    parser.add_argument(
        "--event-categories",
        nargs="+",
        default=[
            "financial_results",
            "big_order",
            "management_change",
            "promoter_change",
            "policy_regulatory",
            "fund_raise",
            "merger_acquisition",
        ],
        help="Event categories allowed for catalyst confirmation.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
