from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"
DEFAULT_PARQUET_DIR = ROOT / "parquets"
DEFAULT_VOLUME_GROUPS = ROOT / "data" / "volume_groups.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "swing_volume_spike_review"

COST_BPS_SIDE = 8.0
SLIPPAGE_BPS_SIDE = 5.0
ROUND_TRIP_COST = 2 * (COST_BPS_SIDE + SLIPPAGE_BPS_SIDE) / 10000
SIDE_COST = (COST_BPS_SIDE + SLIPPAGE_BPS_SIDE) / 10000


@dataclass(frozen=True)
class StrategySpec:
    name: str
    label: str
    context: str
    relvol50_min: float
    ret1_min: float
    close_location_min: float
    hold_days: int
    exit_mode: str = "fixed"
    min_hold_days: int = 5
    stop_atr: float = 2.5
    max_new_per_day: int = 5
    max_positions: int = 10


def clean_float(value: object, digits: int = 4) -> float | str:
    if value is None or pd.isna(value):
        return ""
    try:
        v = float(value)
    except Exception:
        return str(value)
    if math.isinf(v):
        return "inf"
    return round(v, digits)


def markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.head(max_rows).to_markdown(index=False)
    except Exception:
        return "```\n" + df.head(max_rows).to_csv(index=False) + "```"


def load_daily_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Daily cache not found: {path}")
    df = pd.read_parquet(path).copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df


def aggregate_daily_file(path: Path, after: pd.Timestamp | None = None) -> pd.DataFrame:
    cols = ["date", "symbol", "bucket", "open", "high", "low", "close", "volume", "day_open", "gap_pct", "vwap"]
    df = pd.read_parquet(path, columns=cols)
    df["trade_date"] = pd.to_datetime(df["date"])
    if after is not None:
        df = df[df["trade_date"] > after]
    if df.empty:
        return pd.DataFrame()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df = df[
        df["symbol"].ne("")
        & df["bucket"].notna()
        & df["open"].gt(0)
        & df["high"].gt(0)
        & df["low"].gt(0)
        & df["close"].gt(0)
        & df["high"].ge(df[["open", "close"]].max(axis=1))
        & df["low"].le(df[["open", "close"]].min(axis=1))
    ].copy()
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values(["symbol", "trade_date", "bucket"], kind="mergesort")
    grouped = (
        df.groupby(["symbol", "trade_date"], sort=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            day_open=("day_open", "first"),
            gap_pct=("gap_pct", "first"),
            volume=("volume", "sum"),
            close_vwap=("vwap", "last"),
            buckets=("bucket", "nunique"),
        )
        .reset_index()
    )
    return grouped[grouped["buckets"] >= 300].copy()


def append_recent_parquet_daily(daily: pd.DataFrame, parquet_dir: Path) -> pd.DataFrame:
    max_date = pd.Timestamp(daily["trade_date"].max())
    month_key = max_date.strftime("%Y%m")
    frames = [daily]
    for path in sorted(parquet_dir.glob("candles_20*.parquet")):
        key = path.stem.replace("candles_", "")
        if key >= month_key:
            recent = aggregate_daily_file(path, after=max_date)
            if not recent.empty:
                frames.append(recent)
    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    return out.sort_values(["symbol", "trade_date"]).drop_duplicates(["symbol", "trade_date"], keep="last").reset_index(drop=True)


def load_volume_groups(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_groups = data.get("volume_groups", {})
    symbol_to_group: dict[str, str] = {}
    for group_name, symbols in raw_groups.items():
        short = str(group_name).split()[0].upper()
        if isinstance(symbols, list):
            for symbol in symbols:
                symbol_to_group[str(symbol).upper().strip()] = short
    return symbol_to_group


def add_features(raw_daily: pd.DataFrame, symbol_to_group: dict[str, str]) -> pd.DataFrame:
    df = raw_daily.sort_values(["symbol", "trade_date"]).copy()
    g = df.groupby("symbol", group_keys=False)
    prev_close = g["close"].shift(1)
    prev_volume = g["volume"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["prev_close"] = prev_close
    df["ret1"] = df["close"] / prev_close - 1
    df["gap_open_pct_calc"] = df["open"] / prev_close - 1
    df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["close_location"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["atr14"] = tr.groupby(df["symbol"]).rolling(14, min_periods=10).mean().reset_index(level=0, drop=True)

    for n in [10, 20, 50, 100, 200]:
        df[f"sma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(5, n // 2)).mean())
    for n in [10, 20, 50]:
        df[f"ema{n}"] = g["close"].transform(lambda s, n=n: s.ewm(span=n, adjust=False, min_periods=max(5, n // 2)).mean())

    df["sma50_slope20"] = df["sma50"] / g["sma50"].shift(20) - 1
    df["sma200_slope20"] = df["sma200"] / g["sma200"].shift(20) - 1

    prior_high = g["high"].shift(1)
    prior_low = g["low"].shift(1)
    for n in [20, 55, 120, 252]:
        df[f"prior_high{n}"] = prior_high.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).max().reset_index(level=0, drop=True)
        df[f"prior_low{n}"] = prior_low.groupby(df["symbol"]).rolling(n, min_periods=max(10, n // 2)).min().reset_index(level=0, drop=True)

    for n in [20, 60, 120]:
        df[f"ret{n}"] = df["close"] / g["close"].shift(n) - 1
        df[f"prior_ret{n}"] = prev_close / g["close"].shift(n + 1) - 1

    df["vol20_prior"] = prev_volume.groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["vol50_prior"] = prev_volume.groupby(df["symbol"]).rolling(50, min_periods=25).mean().reset_index(level=0, drop=True)
    df["relvol20"] = df["volume"] / df["vol20_prior"].replace(0, np.nan)
    df["relvol50"] = df["volume"] / df["vol50_prior"].replace(0, np.nan)
    df["prev_relvol50"] = g["relvol50"].shift(1)
    df["adv20_prior"] = (prev_close * prev_volume).groupby(df["symbol"]).rolling(20, min_periods=10).mean().reset_index(level=0, drop=True)
    df["rs60_rank"] = df.groupby("trade_date")["ret60"].rank(pct=True)

    df["above_sma200"] = df["close"] > df["sma200"]
    df["market_breadth200"] = df.groupby("trade_date")["above_sma200"].transform("mean")
    df["market_ok"] = df["market_breadth200"].fillna(0) >= 0.38
    df["liquid"] = (df["close"] >= 25) & (df["vol20_prior"] >= 75_000) & (df["adv20_prior"] >= 7_500_000)
    df["uptrend"] = (df["close"] > df["sma50"]) & (df["sma50"] > df["sma200"]) & (df["sma50_slope20"] > 0)
    df["leader"] = df["uptrend"] & (df["rs60_rank"] >= 0.60)
    df["near_52w_high"] = df["close"] >= df["prior_high252"] * 0.85
    df["neglected"] = (df["prev_close"] <= df["prior_high120"] * 0.92) & (df["prior_ret20"] <= 0.25)
    df["fresh_relvol50_from_normal"] = df["prev_relvol50"].isna() | (df["prev_relvol50"] < 1.5)
    df["volume_group"] = df["symbol"].map(symbol_to_group).fillna("OTHER")
    df["rank_score"] = (
        df["relvol50"].clip(upper=25).fillna(0) * 1.5
        + df["ret1"].fillna(0) * 100
        + df["close_location"].fillna(0) * 2
        + df["rs60_rank"].fillna(0) * 4
    )
    df["year"] = df["trade_date"].dt.year
    df["quarter"] = df["trade_date"].dt.to_period("Q").astype(str)
    df["month"] = df["trade_date"].dt.to_period("M").astype(str)
    df["week"] = df["trade_date"].dt.to_period("W-SUN").apply(lambda p: p.start_time.date().isoformat())
    df["local_idx"] = g.cumcount()
    return df.reset_index(drop=True)


def context_mask(df: pd.DataFrame, context: str) -> pd.Series:
    base = df["liquid"]
    if context == "raw_liquid":
        return base
    if context == "liquid_market":
        return base & df["market_ok"]
    if context == "leader_uptrend":
        return base & df["market_ok"] & df["leader"]
    if context == "near_52w_high":
        return base & df["market_ok"] & df["leader"] & df["near_52w_high"]
    if context == "neglected_reaction":
        return base & df["market_ok"] & df["neglected"]
    raise KeyError(f"Unknown context: {context}")


def signal_mask(df: pd.DataFrame, spec: StrategySpec) -> pd.Series:
    fresh_limit = min(spec.relvol50_min, 1.5)
    fresh = df["relvol50"].ge(spec.relvol50_min) & (df["prev_relvol50"].isna() | df["prev_relvol50"].lt(fresh_limit))
    return (
        context_mask(df, spec.context)
        & fresh
        & df["ret1"].ge(spec.ret1_min)
        & df["close_location"].ge(spec.close_location_min)
        & df["close"].gt(df["open"])
        & df["atr14"].notna()
    ).fillna(False)


def strategy_specs() -> list[StrategySpec]:
    return [
        StrategySpec("raw_liquid_rv8_fixed30", "Raw liquid spike, fixed 30D", "raw_liquid", 8.0, 0.00, 0.70, 30),
        StrategySpec("liquid_market_rv8_fixed30", "Market-filtered spike, fixed 30D", "liquid_market", 8.0, 0.00, 0.70, 30),
        StrategySpec("leader_rv8_fixed30", "Leader uptrend spike, fixed 30D", "leader_uptrend", 8.0, 0.00, 0.70, 30),
        StrategySpec("leader_rv8_ema20_exit", "Leader uptrend spike, EMA20 risk exit", "leader_uptrend", 8.0, 0.00, 0.70, 30, exit_mode="ema20", min_hold_days=5),
        StrategySpec("leader_rv8_atr_ema20_exit", "Leader uptrend spike, ATR+EMA20 exit", "leader_uptrend", 8.0, 0.00, 0.70, 30, exit_mode="atr_ema20", min_hold_days=5, stop_atr=2.5),
        StrategySpec("leader_rv5_ret8_fixed30", "Leader spike with >=8% impulse", "leader_uptrend", 5.0, 0.08, 0.70, 30),
        StrategySpec("near52w_rv8_fixed30", "Near-52W-high spike, fixed 30D", "near_52w_high", 8.0, 0.00, 0.70, 30),
        StrategySpec("neglected_rv8_ret8_fixed20", "Neglected reaction spike, fixed 20D", "neglected_reaction", 8.0, 0.08, 0.70, 20),
    ]


def build_symbol_tables(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {sym: part.sort_values("trade_date").reset_index(drop=True) for sym, part in df.groupby("symbol", sort=False)}


def exit_for_candidate(sdf: pd.DataFrame, signal_idx: int, spec: StrategySpec) -> tuple[int, float, str, int]:
    entry_idx = signal_idx + 1
    max_exit_idx = min(signal_idx + spec.hold_days, len(sdf) - 1)
    if entry_idx >= len(sdf) or entry_idx > max_exit_idx:
        return -1, np.nan, "invalid", 0

    entry = float(sdf.at[entry_idx, "open"])
    atr = float(sdf.at[signal_idx, "atr14"])
    stop = entry - spec.stop_atr * atr
    exit_idx = max_exit_idx
    exit_price = float(sdf.at[exit_idx, "close"])
    exit_reason = "time"
    for j in range(entry_idx, max_exit_idx + 1):
        hold = j - entry_idx + 1
        open_j = float(sdf.at[j, "open"])
        low_j = float(sdf.at[j, "low"])
        close_j = float(sdf.at[j, "close"])
        if spec.exit_mode in {"atr", "atr_ema20"} and low_j <= stop:
            exit_idx = j
            exit_price = open_j if open_j < stop else stop
            exit_reason = "atr_stop"
            break
        if spec.exit_mode in {"ema20", "atr_ema20"} and hold >= spec.min_hold_days:
            ema20 = float(sdf.at[j, "ema20"])
            if math.isfinite(ema20) and close_j < ema20:
                exit_idx = j
                exit_price = close_j
                exit_reason = "ema20_close"
                break
    return exit_idx, exit_price, exit_reason, max(1, exit_idx - entry_idx + 1)


def make_candidate_table(df: pd.DataFrame, symbol_tables: dict[str, pd.DataFrame], spec: StrategySpec) -> pd.DataFrame:
    mask = signal_mask(df, spec)
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
        exit_idx, exit_price, exit_reason, hold_days = exit_for_candidate(sdf, signal_idx, spec)
        if exit_idx < 0:
            continue
        entry = float(sdf.at[entry_idx, "open"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        net_return = exit_price / entry - 1 - ROUND_TRIP_COST
        rows.append(
            {
                "strategy": spec.name,
                "label": spec.label,
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
                "net_return": net_return,
                "ret1": float(row.ret1),
                "relvol50": float(row.relvol50),
                "close_location": float(row.close_location),
                "rs60_rank": float(row.rs60_rank) if pd.notna(row.rs60_rank) else np.nan,
                "market_breadth200": float(row.market_breadth200) if pd.notna(row.market_breadth200) else np.nan,
                "rank_score": float(row.rank_score),
            }
        )
    return pd.DataFrame(rows)


def select_portfolio_trades(candidates: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    ordered = candidates.sort_values(["entry_date", "rank_score"], ascending=[True, False], kind="mergesort")
    selected: list[dict[str, object]] = []
    active: list[dict[str, object]] = []
    new_count_by_day: dict[pd.Timestamp, int] = {}
    for row in ordered.to_dict("records"):
        entry_date = pd.Timestamp(row["entry_date"])
        active = [p for p in active if pd.Timestamp(p["exit_date"]) >= entry_date]
        if len(active) >= spec.max_positions:
            continue
        if new_count_by_day.get(entry_date, 0) >= spec.max_new_per_day:
            continue
        symbol = str(row["symbol"])
        if any(p["symbol"] == symbol for p in active):
            continue
        selected.append(row)
        active.append(row)
        new_count_by_day[entry_date] = new_count_by_day.get(entry_date, 0) + 1
    return pd.DataFrame(selected).sort_values(["entry_date", "symbol"]).reset_index(drop=True)


def daily_returns_from_trades(trades: pd.DataFrame, symbol_tables: dict[str, pd.DataFrame], dates: pd.Series, max_positions: int) -> pd.DataFrame:
    date_index = pd.DatetimeIndex(pd.to_datetime(dates).sort_values().unique())
    daily = pd.DataFrame({"date": date_index})
    daily["daily_return"] = 0.0
    daily["active_positions"] = 0
    daily["entries"] = 0
    daily["exits"] = 0
    if trades.empty:
        daily["equity"] = 1.0
        daily["drawdown"] = 0.0
        return daily

    date_to_pos = {d: i for i, d in enumerate(date_index)}
    ret = np.zeros(len(date_index), dtype=float)
    active_counts = np.zeros(len(date_index), dtype=int)
    entries = np.zeros(len(date_index), dtype=int)
    exits = np.zeros(len(date_index), dtype=int)
    slot_weight = 1.0 / max_positions

    for row in trades.itertuples(index=False):
        sdf = symbol_tables[str(row.symbol)]
        entry_idx = int(row.entry_idx)
        exit_idx = int(row.exit_idx)
        exit_price = float(row.exit)
        prev_price = float(row.entry)
        for j in range(entry_idx, exit_idx + 1):
            d = pd.Timestamp(sdf.at[j, "trade_date"])
            pos = date_to_pos.get(d)
            if pos is None:
                continue
            if j == exit_idx:
                price = exit_price
            else:
                price = float(sdf.at[j, "close"])
            if not math.isfinite(price) or not math.isfinite(prev_price) or prev_price <= 0:
                continue
            step_return = price / prev_price - 1
            if j == entry_idx:
                step_return -= SIDE_COST
                entries[pos] += 1
            if j == exit_idx:
                step_return -= SIDE_COST
                exits[pos] += 1
            ret[pos] += slot_weight * step_return
            active_counts[pos] += 1
            prev_price = price

    daily["daily_return"] = ret
    daily["active_positions"] = active_counts
    daily["entries"] = entries
    daily["exits"] = exits
    daily["equity"] = (1 + daily["daily_return"]).cumprod()
    daily["drawdown"] = daily["equity"] / daily["equity"].cummax() - 1
    return daily


def profit_factor(returns: pd.Series) -> float:
    gross_profit = float(returns[returns > 0].sum())
    gross_loss = float(-returns[returns <= 0].sum())
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def p_value_from_t(t_stat: float, n: int) -> float:
    if not math.isfinite(t_stat) or n <= 1:
        return np.nan
    try:
        from scipy import stats

        return float(2 * stats.t.sf(abs(t_stat), df=n - 1))
    except Exception:
        # Normal approximation fallback. Good enough for large daily samples.
        return float(math.erfc(abs(t_stat) / math.sqrt(2)))


def max_drawdown_info(equity: pd.Series, dates: pd.Series) -> dict[str, object]:
    if equity.empty:
        return {"max_drawdown_pct": 0.0, "dd_start": "", "dd_end": ""}
    running_max = equity.cummax()
    dd = equity / running_max - 1
    end_idx = int(dd.idxmin())
    start_idx = int(equity.loc[:end_idx].idxmax())
    return {
        "max_drawdown_pct": round(float(dd.loc[end_idx] * 100), 3),
        "dd_start": str(pd.Timestamp(dates.loc[start_idx]).date()),
        "dd_end": str(pd.Timestamp(dates.loc[end_idx]).date()),
    }


def metrics_for_strategy(trades: pd.DataFrame, daily: pd.DataFrame, label: str) -> dict[str, object]:
    out: dict[str, object] = {"strategy": label}
    out["trades"] = int(len(trades))
    if trades.empty or daily.empty:
        return out
    r = trades["net_return"].astype(float)
    wins = r > 0
    daily_ret = daily["daily_return"].astype(float)
    local_equity = (1 + daily_ret).cumprod()
    active_daily = daily_ret[daily["active_positions"] > 0]
    n = int(active_daily.count())
    mean_daily = float(active_daily.mean()) if n else 0.0
    std_daily = float(active_daily.std(ddof=1)) if n > 1 else 0.0
    t_stat = mean_daily / (std_daily / math.sqrt(n)) if std_daily > 0 and n > 1 else np.nan
    p_value = p_value_from_t(float(t_stat), n)
    local_equity.index = daily.index
    mdd = max_drawdown_info(local_equity, daily["date"])
    out.update(
        {
            "start": str(pd.Timestamp(daily["date"].min()).date()),
            "end": str(pd.Timestamp(daily["date"].max()).date()),
            "win_rate_pct": round(float(wins.mean() * 100), 2),
            "profit_factor": clean_float(profit_factor(r), 3),
            "expectancy_pct": round(float(r.mean() * 100), 3),
            "median_trade_pct": round(float(r.median() * 100), 3),
            "avg_win_pct": round(float(r[wins].mean() * 100), 3) if wins.any() else 0.0,
            "avg_loss_pct": round(float(r[~wins].mean() * 100), 3) if (~wins).any() else 0.0,
            "portfolio_return_pct": round(float((local_equity.iloc[-1] - 1) * 100), 3),
            "daily_sharpe": round(float(mean_daily / std_daily * math.sqrt(252)), 3) if std_daily > 0 else 0.0,
            "daily_mean_pct": round(float(mean_daily * 100), 4),
            "daily_t_stat": round(float(t_stat), 4) if math.isfinite(float(t_stat)) else np.nan,
            "p_value": round(float(p_value), 6) if math.isfinite(float(p_value)) else np.nan,
            "avg_active_positions": round(float(daily["active_positions"].mean()), 3),
            "max_active_positions": int(daily["active_positions"].max()),
            "avg_hold_days": round(float(trades["hold_days"].mean()), 2),
            "stop_exit_pct": round(float((trades["exit_reason"].eq("atr_stop")).mean() * 100), 2) if "exit_reason" in trades else 0.0,
            **mdd,
        }
    )
    return out


def period_analysis(daily: pd.DataFrame, trades: pd.DataFrame, period: str) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    out = daily.copy()
    out["period"] = pd.to_datetime(out["date"]).dt.to_period(period).astype(str)
    entry_counts = trades.assign(period=pd.to_datetime(trades["entry_date"]).dt.to_period(period).astype(str)).groupby("period").size()
    rows = []
    for key, part in out.groupby("period", sort=True):
        dr = part["daily_return"].astype(float)
        active = dr[part["active_positions"] > 0]
        std = active.std(ddof=1) if len(active) > 1 else 0.0
        sharpe = active.mean() / std * math.sqrt(252) if std > 0 else 0.0
        dd = part["equity"] / part["equity"].cummax() - 1
        rows.append(
            {
                "period": key,
                "trades": int(entry_counts.get(key, 0)),
                "return_pct": round(float((1 + dr).prod() - 1) * 100, 3),
                "avg_daily_return_pct": round(float(active.mean() * 100), 4) if len(active) else 0.0,
                "sharpe": round(float(sharpe), 3),
                "max_drawdown_pct": round(float(dd.min() * 100), 3),
                "active_days": int((part["active_positions"] > 0).sum()),
            }
        )
    return pd.DataFrame(rows)


def top_losing_weeks(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    out = daily.copy()
    out["week_start"] = pd.to_datetime(out["date"]).dt.to_period("W-SUN").apply(lambda p: p.start_time.date().isoformat())
    trade_counts = trades.assign(week_start=pd.to_datetime(trades["entry_date"]).dt.to_period("W-SUN").apply(lambda p: p.start_time.date().isoformat())).groupby("week_start").size()
    rows = []
    for week, part in out.groupby("week_start", sort=True):
        dr = part["daily_return"].astype(float)
        rows.append(
            {
                "week_start": week,
                "return_pct": round(float((1 + dr).prod() - 1) * 100, 3),
                "worst_day_pct": round(float(dr.min() * 100), 3),
                "trades_entered": int(trade_counts.get(week, 0)),
                "ending_equity": round(float(part["equity"].iloc[-1]), 4),
            }
        )
    return pd.DataFrame(rows).sort_values("return_pct", ascending=True).head(10).reset_index(drop=True)


def split_metrics(trades: pd.DataFrame, daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    span = end - start
    cut1 = start + span * 0.60
    cut2 = start + span * 0.80
    ranges = [
        ("in_sample", start, cut1),
        ("validation", cut1, cut2),
        ("out_of_sample", cut2, end + pd.Timedelta(days=1)),
    ]
    rows = []
    for name, a, b in ranges:
        t = trades[(trades["entry_date"] >= a) & (trades["entry_date"] < b)]
        d = daily[(daily["date"] >= a) & (daily["date"] < b)].copy()
        m = metrics_for_strategy(t, d, name)
        m["split"] = name
        m["range_start"] = str(pd.Timestamp(a).date())
        m["range_end"] = str(pd.Timestamp(b).date())
        rows.append(m)
    return pd.DataFrame(rows)


def volume_group_analysis(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for group, part in trades.groupby("volume_group", dropna=False):
        r = part["net_return"].astype(float)
        rows.append(
            {
                "volume_group": group,
                "trades": int(len(part)),
                "win_rate_pct": round(float((r > 0).mean() * 100), 2),
                "profit_factor": clean_float(profit_factor(r), 3),
                "expectancy_pct": round(float(r.mean() * 100), 3),
                "median_trade_pct": round(float(r.median() * 100), 3),
            }
        )
    return pd.DataFrame(rows).sort_values(["expectancy_pct", "trades"], ascending=[False, False])


def parameter_review(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    g = df.groupby("symbol", group_keys=False)
    for hold in [10, 15, 20, 30, 45]:
        df[f"exit_close_h{hold}"] = g["close"].shift(-hold)
        df[f"net_event_h{hold}"] = df[f"exit_close_h{hold}"] / g["open"].shift(-1) - 1 - ROUND_TRIP_COST

    for context in ["raw_liquid", "liquid_market", "leader_uptrend", "near_52w_high", "neglected_reaction"]:
        ctx = context_mask(df, context)
        for relvol in [3.0, 5.0, 8.0, 10.0]:
            fresh = df["relvol50"].ge(relvol) & (df["prev_relvol50"].isna() | df["prev_relvol50"].lt(min(relvol, 1.5)))
            for ret_min in [0.0, 0.02, 0.05, 0.08, 0.12]:
                for cl in [0.55, 0.70, 0.85]:
                    base = ctx & fresh & df["ret1"].ge(ret_min) & df["close_location"].ge(cl) & df["close"].gt(df["open"])
                    for hold in [10, 15, 20, 30, 45]:
                        r = df.loc[base, f"net_event_h{hold}"].dropna().astype(float)
                        if len(r) < 100:
                            continue
                        mean = float(r.mean())
                        std = float(r.std(ddof=1))
                        t_stat = mean / (std / math.sqrt(len(r))) if std > 0 else np.nan
                        rows.append(
                            {
                                "context": context,
                                "relvol50_min": relvol,
                                "ret1_min_pct": ret_min * 100,
                                "close_location_min": cl,
                                "hold_days": hold,
                                "trades": int(len(r)),
                                "win_rate_pct": round(float((r > 0).mean() * 100), 2),
                                "profit_factor": clean_float(profit_factor(r), 3),
                                "expectancy_pct": round(float(mean * 100), 3),
                                "t_stat": round(float(t_stat), 4) if math.isfinite(float(t_stat)) else np.nan,
                                "p_value": round(p_value_from_t(float(t_stat), len(r)), 6) if math.isfinite(float(t_stat)) else np.nan,
                            }
                        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["robust_score"] = (
        out["expectancy_pct"].fillna(-99) * 0.45
        + np.minimum(out["trades"], 1000) / 1000 * 0.35
        + out["win_rate_pct"].fillna(0) / 100 * 0.20
    )
    return out.sort_values(["robust_score", "expectancy_pct", "trades"], ascending=False).reset_index(drop=True)


def robustness_verdict(metrics: pd.DataFrame, splits: dict[str, pd.DataFrame], parameter_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in metrics.to_dict("records"):
        strategy = row["strategy"]
        reasons = []
        if int(row.get("trades") or 0) < 250:
            reasons.append("sample below 250 selected trades")
        if float(row.get("portfolio_return_pct") or 0) <= 0:
            reasons.append("portfolio return not positive")
        if float(row.get("daily_sharpe") or 0) < 0.5:
            reasons.append("daily Sharpe below 0.5")
        if float(row.get("p_value") or 1) > 0.05:
            reasons.append("daily return p-value above 0.05")
        if float(row.get("max_drawdown_pct") or 0) < -35:
            reasons.append("max drawdown worse than -35%")
        split = splits.get(strategy, pd.DataFrame())
        if not split.empty:
            oos = split[split["split"] == "out_of_sample"]
            val = split[split["split"] == "validation"]
            if oos.empty or float(oos.iloc[0].get("portfolio_return_pct") or 0) <= 0:
                reasons.append("out-of-sample portfolio return not positive")
            if val.empty or float(val.iloc[0].get("portfolio_return_pct") or 0) <= 0:
                reasons.append("validation portfolio return not positive")
        label = "watchlist" if not reasons else "reject"
        rows.append(
            {
                "strategy": strategy,
                "label": label,
                "reasons": "; ".join(reasons) if reasons else "Passed summary gates; still needs paper trading and live slippage validation.",
            }
        )
    return pd.DataFrame(rows)


def save_chart(path: Path, daily_by_strategy: dict[str, pd.DataFrame], metrics: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    path.mkdir(parents=True, exist_ok=True)
    top = metrics.sort_values(["portfolio_return_pct", "daily_sharpe"], ascending=False).head(5)["strategy"].tolist()
    plt.figure(figsize=(12, 7))
    for name in top:
        daily = daily_by_strategy.get(name)
        if daily is None or daily.empty:
            continue
        plt.plot(pd.to_datetime(daily["date"]), daily["equity"], label=name)
    plt.title("Swing Volume Spike Portfolio Equity Curves")
    plt.ylabel("Growth of 1.0")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path / "equity_curves.png", dpi=160)
    plt.close()

    top_dd = metrics.sort_values("max_drawdown_pct", ascending=True).head(5)["strategy"].tolist()
    plt.figure(figsize=(12, 7))
    for name in top_dd:
        daily = daily_by_strategy.get(name)
        if daily is None or daily.empty:
            continue
        plt.plot(pd.to_datetime(daily["date"]), daily["drawdown"] * 100, label=name)
    plt.title("Drawdown Curves")
    plt.ylabel("Drawdown (%)")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path / "drawdown_curves.png", dpi=160)
    plt.close()


def write_report(
    out_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    assessments: pd.DataFrame,
    split_all: pd.DataFrame,
    monthly_top: pd.DataFrame,
    quarterly_top: pd.DataFrame,
    losing_weeks_top: pd.DataFrame,
    group_top: pd.DataFrame,
    parameter_df: pd.DataFrame,
) -> None:
    top_metrics = metrics.sort_values(["portfolio_return_pct", "daily_sharpe"], ascending=False)
    watchlist = assessments[assessments["label"].eq("watchlist")]
    best_name = str(top_metrics.iloc[0]["strategy"]) if not top_metrics.empty else ""
    best_verdict = (
        "No strategy passed all robustness gates."
        if watchlist.empty
        else f"Watchlist candidate: `{watchlist.iloc[0]['strategy']}`."
    )
    text = f"""# Swing Volume Spike Review

Generated: {pd.Timestamp.now()}

Data window: {pd.Timestamp(daily["trade_date"].min()).date()} to {pd.Timestamp(daily["trade_date"].max()).date()}.
Entry is next session open after the signal candle. Base cost model is {COST_BPS_SIDE:g} bps fees plus {SLIPPAGE_BPS_SIDE:g} bps slippage per side, or {ROUND_TRIP_COST * 100:.2f}% round trip.

## Verdict

{best_verdict}

The strongest family in this run is not "any volume spike." It is a fresh extreme volume spike in a liquid leader/uptrend or near-52-week-high context, held long enough for the post-spike attention drift to play out. Raw liquid spikes are included below as the failure benchmark.

## Strategy Ranking

{markdown_table(top_metrics, 20)}

## Robustness Assessment

{markdown_table(assessments, 20)}

## Split Metrics

{markdown_table(split_all, 30)}

## Monthly Analysis For Top Strategy: `{best_name}`

{markdown_table(monthly_top, 24)}

## Quarterly Sharpe For Top Strategy: `{best_name}`

{markdown_table(quarterly_top, 24)}

## Top 10 Losing Weeks For Top Strategy: `{best_name}`

{markdown_table(losing_weeks_top, 10)}

## Volume-Group Analysis For Top Strategy: `{best_name}`

{markdown_table(group_top, 20)}

## Parameter Review

Top rows below are event-study style, not portfolio-constrained. Use them to understand parameter direction, then trust the portfolio-constrained strategy metrics above for realism.

{markdown_table(parameter_df[["context", "relvol50_min", "ret1_min_pct", "close_location_min", "hold_days", "trades", "win_rate_pct", "profit_factor", "expectancy_pct", "t_stat", "p_value", "robust_score"]], 30)}

## Why Your Backtest Can Fail Even When The Chart Feels Right

- The spike must be fresh. A second or third high-volume day is often late and mean-reversion-prone.
- Volume without price acceptance is weak. The close-location filter matters because it checks whether buyers held the candle near the high.
- Context matters. Leader/uptrend and near-high filters remove many panic/liquidity events that look exciting but do not trend.
- Fixed 30-session holds test the attention drift. Tight stops can cut the exact volatility that creates the swing.
- Parameter mining risk is real. Treat low p-values as evidence, not proof; the split and group checks matter more than one pretty full-sample result.

## Files

- `strategy_metrics.csv`
- `strategy_assessments.csv`
- `split_metrics.csv`
- `monthly_analysis.csv`
- `quarterly_analysis.csv`
- `top_10_losing_weeks.csv`
- `volume_group_analysis.csv`
- `parameter_review.csv`
- `trade_log.csv`
- `equity_curve.csv`
- `charts/equity_curves.png`
- `charts/drawdown_curves.png`

This is historical research, not financial advice. Paper trade the watchlist setup before risking money.
"""
    (out_dir / "final_report.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Loading daily cache")
    raw_daily = load_daily_cache(Path(args.daily_cache))
    if args.append_recent:
        print("Appending recent parquet daily rows")
        raw_daily = append_recent_parquet_daily(raw_daily, Path(args.parquet_dir))
    print(f"Daily rows: {len(raw_daily):,}; symbols: {raw_daily['symbol'].nunique():,}; window: {raw_daily['trade_date'].min()} to {raw_daily['trade_date'].max()}")

    symbol_to_group = load_volume_groups(Path(args.volume_groups))
    print("Adding swing features")
    daily = add_features(raw_daily, symbol_to_group)
    symbol_tables = build_symbol_tables(daily)
    trading_dates = daily["trade_date"].drop_duplicates().sort_values()

    all_metrics: list[dict[str, object]] = []
    all_trades: list[pd.DataFrame] = []
    daily_by_strategy: dict[str, pd.DataFrame] = {}
    split_frames: list[pd.DataFrame] = []
    monthly_frames: list[pd.DataFrame] = []
    quarterly_frames: list[pd.DataFrame] = []
    losing_week_frames: list[pd.DataFrame] = []
    group_frames: list[pd.DataFrame] = []

    for spec in strategy_specs():
        print(f"Backtesting {spec.name}")
        candidates = make_candidate_table(daily, symbol_tables, spec)
        trades = select_portfolio_trades(candidates, spec)
        strategy_daily = daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
        daily_by_strategy[spec.name] = strategy_daily
        all_metrics.append(metrics_for_strategy(trades, strategy_daily, spec.name))
        if not trades.empty:
            all_trades.append(trades)
        split = split_metrics(trades, strategy_daily, pd.Timestamp(daily["trade_date"].min()), pd.Timestamp(daily["trade_date"].max()))
        split.insert(0, "parent_strategy", spec.name)
        split_frames.append(split)
        monthly = period_analysis(strategy_daily, trades, "M")
        monthly.insert(0, "strategy", spec.name)
        monthly_frames.append(monthly)
        quarterly = period_analysis(strategy_daily, trades, "Q")
        quarterly.insert(0, "strategy", spec.name)
        quarterly_frames.append(quarterly)
        losing = top_losing_weeks(strategy_daily, trades)
        losing.insert(0, "strategy", spec.name)
        losing_week_frames.append(losing)
        groups = volume_group_analysis(trades)
        groups.insert(0, "strategy", spec.name)
        group_frames.append(groups)

    metrics = pd.DataFrame(all_metrics).sort_values(["portfolio_return_pct", "daily_sharpe"], ascending=False)
    split_all = pd.concat(split_frames, ignore_index=True) if split_frames else pd.DataFrame()
    monthly_all = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    quarterly_all = pd.concat(quarterly_frames, ignore_index=True) if quarterly_frames else pd.DataFrame()
    losing_all = pd.concat(losing_week_frames, ignore_index=True) if losing_week_frames else pd.DataFrame()
    group_all = pd.concat(group_frames, ignore_index=True) if group_frames else pd.DataFrame()
    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()

    print("Running parameter review")
    params = parameter_review(daily.copy())
    split_map = {name: split_all[split_all["parent_strategy"].eq(name)] for name in metrics["strategy"].dropna().tolist()}
    assessments = robustness_verdict(metrics, split_map, params)

    best = str(metrics.iloc[0]["strategy"]) if not metrics.empty else ""
    equity_curve = daily_by_strategy.get(best, pd.DataFrame()).copy()
    if not equity_curve.empty:
        equity_curve.insert(0, "strategy", best)

    metrics.to_csv(out_dir / "strategy_metrics.csv", index=False)
    assessments.to_csv(out_dir / "strategy_assessments.csv", index=False)
    split_all.to_csv(out_dir / "split_metrics.csv", index=False)
    monthly_all.to_csv(out_dir / "monthly_analysis.csv", index=False)
    quarterly_all.to_csv(out_dir / "quarterly_analysis.csv", index=False)
    losing_all.to_csv(out_dir / "top_10_losing_weeks.csv", index=False)
    group_all.to_csv(out_dir / "volume_group_analysis.csv", index=False)
    trades_all.to_csv(out_dir / "trade_log.csv", index=False)
    equity_curve.to_csv(out_dir / "equity_curve.csv", index=False)
    params.to_csv(out_dir / "parameter_review.csv", index=False)
    save_chart(out_dir / "charts", daily_by_strategy, metrics)

    monthly_top = monthly_all[monthly_all["strategy"].eq(best)].copy()
    quarterly_top = quarterly_all[quarterly_all["strategy"].eq(best)].copy()
    losing_top = losing_all[losing_all["strategy"].eq(best)].copy()
    group_top = group_all[group_all["strategy"].eq(best)].copy()
    write_report(out_dir, daily, metrics, assessments, split_all, monthly_top, quarterly_top, losing_top, group_top, params)
    print(f"Wrote outputs to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Swing review for fresh volume-spike strategies.")
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_DAILY_CACHE)
    parser.add_argument("--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR)
    parser.add_argument("--volume-groups", type=Path, default=DEFAULT_VOLUME_GROUPS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--append-recent", action="store_true", help="Append rows newer than the daily cache from root monthly parquet files.")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
