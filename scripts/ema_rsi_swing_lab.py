"""Daily swing adaptation of the supplied EMA-200 / RSI-14 commodity setup.

The PDF is an intraday, 3-minute MCX strategy.  This script deliberately does
not claim that it works unchanged for stocks or for swing trading.  It tests a
predeclared daily, long-only adaptation using next-session stop-entry fills and
conservative OHLC exit rules.

No-lookahead contract
---------------------
* A setup is evaluated only after the daily candle has closed.
* EMA, RSI, liquidity, market breadth, and relative-strength values use data
  available on that close or earlier.
* A signal can only enter on the following session.
* Stops, targets, and trailing stops are evaluated only after the entry.
* When a daily bar cannot reveal the order of a stop and target, the stop wins.

Run from the repository root with:
    py -3.12 scripts/ema_rsi_swing_lab.py

The report and all machine-readable analysis artifacts are written beneath
docs/ema_rsi_swing_v1 by default.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"
DEFAULT_OUT_DIR = ROOT / "docs" / "ema_rsi_swing_v1"


@dataclasses.dataclass(frozen=True)
class StrategyConfig:
    """All live-rule settings; changing any of these changes the strategy ID."""

    name: str = "daily_ema200_rsi40_pullback_breakout_v1"
    initial_capital: float = 1_000_000.0
    max_positions: int = 8
    max_new_positions_per_day: int = 2
    min_price: float = 50.0
    min_prior_volume: float = 100_000.0
    min_prior_adv: float = 20_000_000.0
    min_market_breadth_200: float = 0.45
    min_rs60_rank: float = 0.60
    ema_slope_days: int = 20
    rsi_length: int = 14
    rsi_reclaim_level: float = 40.0
    rsi_pullback_lookback: int = 5
    stop_lookback_days: int = 3
    min_risk_pct: float = 0.015
    max_risk_pct: float = 0.080
    max_hold_days: int = 20
    brokerage_and_tax_bps_per_side: float = 15.0
    slippage_bps_per_side: float = 10.0

    @property
    def side_cost(self) -> float:
        return (self.brokerage_and_tax_bps_per_side + self.slippage_bps_per_side) / 10_000.0

    @property
    def slot_capital(self) -> float:
        return self.initial_capital / self.max_positions


def as_jsonable(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, float_format="%.8f")


def markdown_table(frame: pd.DataFrame, columns: list[str] | None = None, max_rows: int | None = None) -> str:
    if frame.empty:
        return "_No rows._"
    out = frame.copy()
    if columns:
        out = out[[column for column in columns if column in out.columns]]
    if max_rows is not None:
        out = out.head(max_rows)

    def display(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, (float, np.floating)):
            if math.isinf(float(value)):
                return "inf" if value > 0 else "-inf"
            return f"{float(value):,.3f}"
        return str(value).replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(str(column).replace("_", " ") for column in out.columns) + " |"
    divider = "|" + "|".join("---" for _ in out.columns) + "|"
    body = ["| " + " | ".join(display(value) for value in row) + " |" for row in out.itertuples(index=False, name=None)]
    return "\n".join([header, divider, *body])


def load_daily_bars(path: Path) -> pd.DataFrame:
    required = {"symbol", "trade_date", "open", "high", "low", "close", "volume"}
    if not path.exists():
        raise FileNotFoundError(f"Daily data source not found: {path}")
    daily = pd.read_parquet(path).copy()
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"Daily data is missing required columns: {sorted(missing)}")
    daily["symbol"] = daily["symbol"].astype(str).str.upper().str.strip()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    for column in ["open", "high", "low", "close", "volume"]:
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily[
        daily["symbol"].ne("")
        & daily["trade_date"].notna()
        & daily[["open", "high", "low", "close"]].gt(0).all(axis=1)
        & daily["high"].ge(daily[["open", "close"]].max(axis=1))
        & daily["low"].le(daily[["open", "close"]].min(axis=1))
        & daily["volume"].ge(0)
    ].copy()
    daily = daily.sort_values(["symbol", "trade_date"], kind="mergesort")
    daily = daily.drop_duplicates(["symbol", "trade_date"], keep="last").reset_index(drop=True)
    return daily


def add_features(daily: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Create only end-of-session features.  There are intentionally no negative shifts here."""
    df = daily.sort_values(["symbol", "trade_date"], kind="mergesort").copy()
    grouped = df.groupby("symbol", group_keys=False, sort=False)

    previous_close = grouped["close"].shift(1)
    previous_volume = grouped["volume"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.groupby(df["symbol"], sort=False).rolling(14, min_periods=14).mean().reset_index(level=0, drop=True)
    df["ema200"] = grouped["close"].transform(
        lambda values: values.ewm(span=200, adjust=False, min_periods=200).mean()
    )
    df["ema200_lag"] = grouped["ema200"].shift(config.ema_slope_days)

    delta = grouped["close"].diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta.clip(upper=0.0))
    average_gain = gains.groupby(df["symbol"], sort=False).transform(
        lambda values: values.ewm(alpha=1 / config.rsi_length, adjust=False, min_periods=config.rsi_length).mean()
    )
    average_loss = losses.groupby(df["symbol"], sort=False).transform(
        lambda values: values.ewm(alpha=1 / config.rsi_length, adjust=False, min_periods=config.rsi_length).mean()
    )
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    df["rsi14"] = 100.0 - (100.0 / (1.0 + relative_strength))
    df["rsi14_prev"] = grouped["rsi14"].shift(1)
    df["rsi14_min_prior5"] = grouped["rsi14"].transform(
        lambda values: values.shift(1).rolling(config.rsi_pullback_lookback, min_periods=config.rsi_pullback_lookback).min()
    )

    df["vol20_prior"] = previous_volume.groupby(df["symbol"], sort=False).rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    df["adv20_prior"] = (previous_close * previous_volume).groupby(df["symbol"], sort=False).rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    df["ret60"] = df["close"] / grouped["close"].shift(60) - 1.0
    df["rs60_rank"] = df.groupby("trade_date", sort=False)["ret60"].rank(pct=True)
    df["market_breadth200"] = (
        (df["close"] > df["ema200"]).groupby(df["trade_date"], sort=False).transform("mean")
    )
    df["stop_reference"] = grouped["low"].transform(
        lambda values: values.rolling(config.stop_lookback_days, min_periods=config.stop_lookback_days).min()
    )
    df["local_index"] = grouped.cumcount().astype(np.int32)

    # These boolean values are all known at the close of the signal day.
    df["liquid"] = (
        (df["close"] >= config.min_price)
        & (df["vol20_prior"] >= config.min_prior_volume)
        & (df["adv20_prior"] >= config.min_prior_adv)
    )
    df["trend_ok"] = (
        (df["close"] > df["ema200"])
        & (df["ema200"] > df["ema200_lag"])
        & (df["market_breadth200"] >= config.min_market_breadth_200)
        & (df["rs60_rank"] >= config.min_rs60_rank)
    )
    df["rsi_reclaim"] = (
        (df["rsi14"] > config.rsi_reclaim_level)
        & (df["rsi14_prev"] <= config.rsi_reclaim_level)
        & (df["rsi14_min_prior5"] <= config.rsi_reclaim_level)
    )
    df["rank_score"] = (
        df["rs60_rank"].fillna(0.0) * 5.0
        + (df["rsi14"].fillna(0.0) - config.rsi_reclaim_level).clip(lower=0.0) * 0.04
        + (df["close"] / df["ema200"].replace(0.0, np.nan) - 1.0).fillna(0.0) * 50.0
    )
    return df


def signal_mask(features: pd.DataFrame) -> pd.Series:
    return (
        features["liquid"]
        & features["trend_ok"]
        & features["rsi_reclaim"]
        & features["stop_reference"].notna()
        & features["atr14"].notna()
    ).fillna(False)


def effective_exit(raw_price: float, config: StrategyConfig) -> float:
    return raw_price * (1.0 - config.side_cost)


def effective_entry(raw_price: float, config: StrategyConfig) -> float:
    return raw_price * (1.0 + config.side_cost)


def simulate_candidate(
    symbol_bars: pd.DataFrame,
    signal_index: int,
    config: StrategyConfig,
) -> dict[str, Any] | None:
    """Simulate one stop-entry trade after a fully known signal close.

    If a bar reaches both a protective stop and a profit event, the adverse stop
    is used.  This convention is intentionally pessimistic because daily OHLC
    cannot reveal intraday ordering.
    """
    entry_index = signal_index + 1
    if entry_index >= len(symbol_bars):
        return None

    signal = symbol_bars.iloc[signal_index]
    entry_bar = symbol_bars.iloc[entry_index]
    trigger = float(signal.high)
    initial_stop = float(signal.stop_reference)
    if not all(math.isfinite(value) and value > 0.0 for value in (trigger, initial_stop)):
        return None

    # A buy stop above the confirmed signal candle.  The next session is the
    # earliest possible fill.  Using high is normal stop-order backtest logic,
    # not a signal feature.
    if float(entry_bar.high) < trigger:
        return None
    raw_entry = max(float(entry_bar.open), trigger)
    entry = effective_entry(raw_entry, config)
    risk = entry - initial_stop
    risk_pct = risk / entry
    if risk <= 0.0 or not (config.min_risk_pct <= risk_pct <= config.max_risk_pct):
        return None

    tp1 = entry + risk
    max_exit_index = min(entry_index + config.max_hold_days - 1, len(symbol_bars) - 1)
    tp1_hit = False
    tp1_index: int | None = None
    tp1_raw_exit: float | None = None
    runner_stop = initial_stop
    runner_exit_index: int | None = None
    runner_raw_exit: float | None = None
    runner_exit_reason = "time"

    for index in range(entry_index, max_exit_index + 1):
        bar = symbol_bars.iloc[index]
        open_ = float(bar.open)
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)

        # Once TP1 has been booked, move the runner stop using only the two
        # *completed* candles before this bar.  Current-bar lows never set it.
        if tp1_hit and index > entry_index:
            prior_two_lows = symbol_bars.iloc[index - 2 : index]["low"]
            if len(prior_two_lows) == 2:
                runner_stop = max(runner_stop, float(prior_two_lows.min()))

        active_stop = runner_stop if tp1_hit else initial_stop
        if open_ <= active_stop:
            runner_exit_index = index
            runner_raw_exit = open_
            runner_exit_reason = "gap_stop" if not tp1_hit else "runner_gap_stop"
            break
        if low <= active_stop:
            runner_exit_index = index
            runner_raw_exit = active_stop
            runner_exit_reason = "initial_stop" if not tp1_hit else "runner_stop"
            break

        if not tp1_hit and high >= tp1:
            tp1_hit = True
            tp1_index = index
            tp1_raw_exit = max(open_, tp1)

            # Daily bars cannot prove whether the runner's breakeven stop was
            # hit after TP1.  Treat any such same-day ambiguity as breakeven.
            if low <= entry:
                runner_exit_index = index
                runner_raw_exit = entry
                runner_exit_reason = "runner_breakeven_same_day"
                break

        if index == max_exit_index:
            runner_exit_index = index
            runner_raw_exit = close
            runner_exit_reason = "time"
            break

    if runner_exit_index is None or runner_raw_exit is None:
        return None

    final_exit = effective_exit(runner_raw_exit, config)
    if tp1_hit and tp1_raw_exit is not None and tp1_index is not None:
        tp1_exit = effective_exit(tp1_raw_exit, config)
        net_return = 0.5 * (tp1_exit / entry - 1.0) + 0.5 * (final_exit / entry - 1.0)
        gross_return = 0.5 * (tp1_raw_exit / raw_entry - 1.0) + 0.5 * (runner_raw_exit / raw_entry - 1.0)
        exit_reason = f"TP1+{runner_exit_reason}"
    else:
        tp1_exit = np.nan
        net_return = final_exit / entry - 1.0
        gross_return = runner_raw_exit / raw_entry - 1.0
        exit_reason = runner_exit_reason

    return {
        "symbol": str(signal.symbol),
        "signal_date": pd.Timestamp(signal.trade_date),
        "entry_date": pd.Timestamp(entry_bar.trade_date),
        "exit_date": pd.Timestamp(symbol_bars.iloc[runner_exit_index].trade_date),
        "signal_index": int(signal_index),
        "entry_index": int(entry_index),
        "exit_index": int(runner_exit_index),
        "tp1_index": int(tp1_index) if tp1_index is not None else np.nan,
        "tp1_date": pd.Timestamp(symbol_bars.iloc[tp1_index].trade_date) if tp1_index is not None else pd.NaT,
        "trigger": trigger,
        "raw_entry": raw_entry,
        "entry": entry,
        "initial_stop": initial_stop,
        "final_trail_stop": runner_stop if tp1_hit else initial_stop,
        "tp1": tp1,
        "tp1_exit": tp1_exit,
        "runner_exit": final_exit,
        "raw_runner_exit": runner_raw_exit,
        "gross_return": gross_return,
        "net_return": net_return,
        "risk_pct": risk_pct,
        "r_multiple_net": net_return / risk_pct,
        "hold_days": int(runner_exit_index - entry_index + 1),
        "tp1_hit": bool(tp1_hit),
        "exit_reason": exit_reason,
        "runner_exit_reason": runner_exit_reason,
        "rank_score": float(signal.rank_score),
        "rsi14": float(signal.rsi14),
        "rs60_rank": float(signal.rs60_rank),
        "market_breadth200": float(signal.market_breadth200),
        "adv20_prior": float(signal.adv20_prior),
        "vol20_prior": float(signal.vol20_prior),
    }


def generate_candidates(features: pd.DataFrame, config: StrategyConfig) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    symbols = {
        symbol: part.reset_index(drop=True)
        for symbol, part in features.groupby("symbol", sort=False)
    }
    mask = signal_mask(features)
    rows: list[dict[str, Any]] = []
    for row in features.loc[mask, ["symbol", "local_index"]].itertuples(index=False):
        candidate = simulate_candidate(symbols[row.symbol], int(row.local_index), config)
        if candidate is not None:
            rows.append(candidate)
    candidates = pd.DataFrame(rows)
    if candidates.empty:
        return candidates, symbols
    return candidates.sort_values(["entry_date", "rank_score", "symbol"], ascending=[True, False, True]).reset_index(drop=True), symbols


def select_portfolio(candidates: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Apply capacity controls only with information available before entry."""
    if candidates.empty:
        return candidates.copy()
    selected: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    entries_on_date: defaultdict[pd.Timestamp, int] = defaultdict(int)
    ordered = candidates.sort_values(["entry_date", "rank_score", "symbol"], ascending=[True, False, True], kind="mergesort")
    for candidate in ordered.to_dict("records"):
        entry_date = pd.Timestamp(candidate["entry_date"])
        active = [position for position in active if pd.Timestamp(position["exit_date"]) >= entry_date]
        if entries_on_date[entry_date] >= config.max_new_positions_per_day:
            continue
        if len(active) >= config.max_positions:
            continue
        if any(str(position["symbol"]) == str(candidate["symbol"]) for position in active):
            continue
        selected.append(candidate)
        active.append(candidate)
        entries_on_date[entry_date] += 1
    return pd.DataFrame(selected).sort_values(["entry_date", "symbol"]).reset_index(drop=True)


def build_equity_curve(
    trades: pd.DataFrame,
    symbols: dict[str, pd.DataFrame],
    dates: Iterable[pd.Timestamp],
    config: StrategyConfig,
) -> pd.DataFrame:
    dates_index = pd.DatetimeIndex(pd.to_datetime(list(dates)).sort_values().unique())
    if dates_index.empty:
        return pd.DataFrame()
    by_entry: defaultdict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    by_tp1: defaultdict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    by_exit: defaultdict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades.to_dict("records") if not trades.empty else []:
        by_entry[pd.Timestamp(trade["entry_date"])].append(trade)
        if pd.notna(trade.get("tp1_date")):
            by_tp1[pd.Timestamp(trade["tp1_date"])].append(trade)
        by_exit[pd.Timestamp(trade["exit_date"])].append(trade)

    close_lookup = {
        symbol: dict(zip(pd.to_datetime(frame["trade_date"]), frame["close"].astype(float)))
        for symbol, frame in symbols.items()
    }
    cash = config.initial_capital
    open_positions: dict[tuple[str, pd.Timestamp], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    previous_equity = config.initial_capital

    for date in dates_index:
        entered = tp1_count = exited = 0
        # Every accepted candidate has a fill by construction.  Capacity was
        # checked before entry; cash checking makes the equity model explicit.
        for trade in by_entry.get(date, []):
            if cash + 1e-6 < config.slot_capital:
                continue
            key = (str(trade["symbol"]), pd.Timestamp(trade["entry_date"]))
            shares = config.slot_capital / float(trade["entry"])
            cash -= config.slot_capital
            open_positions[key] = {"trade": trade, "shares": shares, "tp1_done": False}
            entered += 1

        # TP1 comes before final exit when both happen on the same bar.
        for trade in by_tp1.get(date, []):
            key = (str(trade["symbol"]), pd.Timestamp(trade["entry_date"]))
            position = open_positions.get(key)
            if position is None or position["tp1_done"]:
                continue
            cash += (position["shares"] * 0.5) * float(trade["tp1_exit"])
            position["shares"] *= 0.5
            position["tp1_done"] = True
            tp1_count += 1

        for trade in by_exit.get(date, []):
            key = (str(trade["symbol"]), pd.Timestamp(trade["entry_date"]))
            position = open_positions.pop(key, None)
            if position is None:
                continue
            cash += position["shares"] * float(trade["runner_exit"])
            exited += 1

        market_value = 0.0
        for key, position in open_positions.items():
            symbol = key[0]
            mark = close_lookup.get(symbol, {}).get(date)
            if mark is not None and math.isfinite(float(mark)):
                market_value += position["shares"] * float(mark)
        equity = cash + market_value
        daily_return = equity / previous_equity - 1.0 if previous_equity > 0 else 0.0
        rows.append(
            {
                "date": date,
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": 0.0,
                "cash": cash,
                "market_value": market_value,
                "capital_deployed": market_value,
                "open_positions": len(open_positions),
                "entries": entered,
                "tp1_exits": tp1_count,
                "final_exits": exited,
            }
        )
        previous_equity = equity

    equity_frame = pd.DataFrame(rows)
    equity_frame["drawdown"] = equity_frame["equity"] / equity_frame["equity"].cummax() - 1.0
    equity_frame["exposure_pct"] = equity_frame["capital_deployed"] / equity_frame["equity"].replace(0.0, np.nan)
    return equity_frame


def longest_streak(values: pd.Series, want_positive: bool) -> int:
    best = current = 0
    for value in values.astype(float):
        if (value > 0.0) == want_positive:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def metrics_for(trades: pd.DataFrame, equity: pd.DataFrame, label: str, config: StrategyConfig) -> dict[str, Any]:
    if equity.empty:
        return {"segment": label, "trades": 0, "portfolio_return_pct": 0.0}
    start_equity = float(equity["equity"].iloc[0] / (1.0 + float(equity["daily_return"].iloc[0])))
    end_equity = float(equity["equity"].iloc[-1])
    portfolio_return = end_equity / start_equity - 1.0 if start_equity > 0 else 0.0
    days = max((pd.Timestamp(equity["date"].iloc[-1]) - pd.Timestamp(equity["date"].iloc[0])).days, 1)
    cagr = (end_equity / start_equity) ** (365.25 / days) - 1.0 if start_equity > 0 and end_equity > 0 else -1.0
    returns = equity["daily_return"].astype(float)
    volatility = returns.std(ddof=1) * math.sqrt(252) if len(returns) > 1 else 0.0
    downside = returns[returns < 0.0]
    downside_deviation = math.sqrt(float((downside**2).mean())) * math.sqrt(252) if len(downside) else 0.0
    sharpe = returns.mean() / returns.std(ddof=1) * math.sqrt(252) if len(returns) > 1 and returns.std(ddof=1) > 0 else 0.0
    sortino = returns.mean() / math.sqrt(float((downside**2).mean())) * math.sqrt(252) if len(downside) and float((downside**2).mean()) > 0 else 0.0
    max_drawdown = float(equity["drawdown"].min())
    ulcer_index = math.sqrt(float((equity["drawdown"] ** 2).mean()))
    net_profit = end_equity - start_equity
    peak_to_trough = abs(max_drawdown * float(equity["equity"].cummax().max()))

    result: dict[str, Any] = {
        "segment": label,
        "start": pd.Timestamp(equity["date"].iloc[0]).date().isoformat(),
        "end": pd.Timestamp(equity["date"].iloc[-1]).date().isoformat(),
        "trading_days": int(len(equity)),
        "trades": int(len(trades)),
        "starting_equity": round(start_equity, 2),
        "ending_equity": round(end_equity, 2),
        "net_profit": round(net_profit, 2),
        "portfolio_return_pct": round(portfolio_return * 100.0, 3),
        "cagr_pct": round(cagr * 100.0, 3),
        "annual_volatility_pct": round(volatility * 100.0, 3),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown_pct": round(max_drawdown * 100.0, 3),
        "ulcer_index_pct": round(ulcer_index * 100.0, 3),
        "calmar_ratio": round(cagr / abs(max_drawdown), 3) if max_drawdown < 0 else 0.0,
        "recovery_factor": round(net_profit / peak_to_trough, 3) if peak_to_trough > 0 else 0.0,
        "positive_days_pct": round(float((returns > 0.0).mean() * 100.0), 3),
        "positive_months_pct": 0.0,
        "avg_exposure_pct": round(float(equity["exposure_pct"].mean() * 100.0), 3),
        "max_exposure_pct": round(float(equity["exposure_pct"].max() * 100.0), 3),
        "max_open_positions": int(equity["open_positions"].max()),
        "side_cost_bps": round(config.side_cost * 10_000.0, 2),
    }
    monthly = equity.copy()
    monthly["month"] = pd.to_datetime(monthly["date"]).dt.to_period("M")
    monthly_return = monthly.groupby("month", sort=False)["daily_return"].apply(lambda values: (1.0 + values).prod() - 1.0)
    result["positive_months_pct"] = round(float((monthly_return > 0.0).mean() * 100.0), 3) if len(monthly_return) else 0.0

    if trades.empty:
        return result
    ordered = trades.sort_values(["exit_date", "symbol"], kind="mergesort")
    trade_return = ordered["net_return"].astype(float)
    wins = trade_return > 0.0
    gross_profit = float(trade_return[wins].sum())
    gross_loss = float(-trade_return[~wins].sum())
    result.update(
        {
            "win_rate_pct": round(float(wins.mean() * 100.0), 3),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else np.inf,
            "expectancy_pct": round(float(trade_return.mean() * 100.0), 3),
            "median_trade_pct": round(float(trade_return.median() * 100.0), 3),
            "avg_win_pct": round(float(trade_return[wins].mean() * 100.0), 3) if wins.any() else 0.0,
            "avg_loss_pct": round(float(trade_return[~wins].mean() * 100.0), 3) if (~wins).any() else 0.0,
            "avg_r_multiple_net": round(float(ordered["r_multiple_net"].mean()), 3),
            "tp1_hit_rate_pct": round(float(ordered["tp1_hit"].mean() * 100.0), 3),
            "avg_hold_days": round(float(ordered["hold_days"].mean()), 3),
            "max_win_streak": longest_streak(trade_return, True),
            "max_loss_streak": longest_streak(trade_return, False),
            "best_trade_pct": round(float(trade_return.max() * 100.0), 3),
            "worst_trade_pct": round(float(trade_return.min() * 100.0), 3),
        }
    )
    return result


def make_period_metrics(
    candidates: pd.DataFrame,
    symbols: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
    config: StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    start, end = dates.min(), dates.max()
    span = end - start
    cut1 = start + span * 0.60
    cut2 = start + span * 0.80
    periods = [
        ("in_sample", start, cut1),
        ("validation", cut1, cut2),
        ("out_of_sample", cut2, end + pd.Timedelta(days=1)),
    ]
    rows: list[dict[str, Any]] = []
    curves: dict[str, pd.DataFrame] = {}
    for name, left, right in periods:
        candidate_part = candidates[(candidates["entry_date"] >= left) & (candidates["entry_date"] < right)].copy()
        trades = select_portfolio(candidate_part, config)
        date_part = dates[(dates >= left) & (dates < right)]
        curve = build_equity_curve(trades, symbols, date_part, config)
        curves[name] = curve
        metric = metrics_for(trades, curve, name, config)
        metric["range_start"] = pd.Timestamp(left).date().isoformat()
        metric["range_end"] = (pd.Timestamp(right) - pd.Timedelta(days=1)).date().isoformat()
        rows.append(metric)
    return pd.DataFrame(rows), curves


def monthly_metrics(equity: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame()
    df = equity.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M")
    rows = []
    for month, part in df.groupby("month", sort=True):
        returns = part["daily_return"].astype(float)
        rows.append(
            {
                "month": str(month),
                "return_pct": (float((1.0 + returns).prod()) - 1.0) * 100.0,
                "ending_equity": float(part["equity"].iloc[-1]),
                "max_drawdown_pct": float(part["drawdown"].min()) * 100.0,
                "avg_exposure_pct": float(part["exposure_pct"].mean()) * 100.0,
                "entries": int(part["entries"].sum()),
                "exits": int(part["final_exits"].sum()),
            }
        )
    return pd.DataFrame(rows)


def yearly_metrics(
    candidates: pd.DataFrame,
    symbols: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
    config: StrategyConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year in sorted(pd.Series(dates.year).unique()):
        date_part = dates[dates.year == year]
        candidates_part = candidates[pd.to_datetime(candidates["entry_date"]).dt.year == year].copy()
        trades = select_portfolio(candidates_part, config)
        curve = build_equity_curve(trades, symbols, date_part, config)
        metric = metrics_for(trades, curve, str(year), config)
        metric["year"] = int(year)
        rows.append(metric)
    return pd.DataFrame(rows)


def exit_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    result = (
        trades.groupby("exit_reason", dropna=False)
        .agg(
            trades=("net_return", "size"),
            avg_net_return_pct=("net_return", lambda values: float(values.mean()) * 100.0),
            win_rate_pct=("net_return", lambda values: float((values > 0.0).mean()) * 100.0),
            avg_hold_days=("hold_days", "mean"),
        )
        .reset_index()
        .sort_values("trades", ascending=False)
    )
    return result


def create_chart(equity: pd.DataFrame, output: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    if equity.empty:
        return
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, height_ratios=[2, 1])
    dates = pd.to_datetime(equity["date"])
    top.plot(dates, equity["equity"], color="#1f77b4", linewidth=1.5, label="Net equity")
    top.set_title("Daily EMA-200 + RSI Swing v1 - Portfolio Equity")
    top.set_ylabel("Equity (Rs)")
    top.grid(alpha=0.25)
    top.legend(loc="upper left")
    bottom.fill_between(dates, equity["drawdown"] * 100.0, 0.0, color="#d62728", alpha=0.45)
    bottom.set_ylabel("Drawdown (%)")
    bottom.set_xlabel("Date")
    bottom.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def verdict(full: dict[str, Any], splits: pd.DataFrame) -> tuple[str, list[str]]:
    failures: list[str] = []
    oos = splits[splits["segment"] == "out_of_sample"]
    if oos.empty:
        failures.append("no out-of-sample period")
    else:
        row = oos.iloc[0]
        if int(row.get("trades", 0)) < 30:
            failures.append("out-of-sample has fewer than 30 trades")
        if float(row.get("profit_factor", 0.0)) < 1.15:
            failures.append("out-of-sample profit factor is below 1.15")
        if float(row.get("sharpe_ratio", 0.0)) < 0.50:
            failures.append("out-of-sample Sharpe is below 0.50")
    if float(full.get("profit_factor", 0.0)) < 1.15:
        failures.append("full-sample profit factor is below 1.15")
    if float(full.get("max_drawdown_pct", 0.0)) < -25.0:
        failures.append("full-sample maximum drawdown is worse than -25%")
    return ("paper_candidate" if not failures else "not_approved"), failures


def build_report(
    output: Path,
    config: StrategyConfig,
    source: Path,
    features: pd.DataFrame,
    candidate_count: int,
    trades: pd.DataFrame,
    full_metrics: dict[str, Any],
    split_metrics: pd.DataFrame,
    year_metrics: pd.DataFrame,
    month_metrics: pd.DataFrame,
    exits: pd.DataFrame,
    status: str,
    reasons: list[str],
) -> None:
    best = trades.nlargest(10, "net_return") if not trades.empty else trades
    worst = trades.nsmallest(10, "net_return") if not trades.empty else trades
    result = f"""# Daily EMA-200 + RSI Swing v1 - Locked Research Report

## Status

**{status.upper()}**

{"All quantitative gates passed. This remains a paper-trading candidate, not an order-authorisation." if not reasons else "This ruleset is not approved for live deployment because: " + "; ".join(reasons) + "."}

## Source strategy review

The supplied TradeIQ PDF is a **3-minute MCX commodity** reversal-continuation rule: price above/below EMA 200, RSI(14) recovers through 40/falls through 60, half exits at 1R, the stop moves to cost, and the remainder trails a two-candle low/high. It does not state a tested swing edge, universe filter, gap rule, or daily-bar execution policy. This research is a new daily-stock adaptation, not a validation of the original intraday claim.

## Locked daily strategy rules

1. Long-only liquid stock universe: price >= Rs {config.min_price:.0f}, prior 20-session average volume >= {config.min_prior_volume:,.0f}, and prior average daily value >= Rs {config.min_prior_adv:,.0f}.
2. Trend regime: close above EMA 200, EMA 200 higher than {config.ema_slope_days} sessions ago, equal-weight universe breadth above EMA 200 >= {config.min_market_breadth_200:.0%}, and 60-session cross-sectional relative-strength rank >= {config.min_rs60_rank:.0%}.
3. Setup: RSI(14) crosses above {config.rsi_reclaim_level:.0f} today after having been at or below {config.rsi_reclaim_level:.0f} in the preceding {config.rsi_pullback_lookback} completed sessions.
4. Entry: only on the next session, through a buy stop at the signal session high. A gap through the trigger fills at that next open. No same-candle or same-close fill is allowed.
5. Initial stop: the lowest low of the last {config.stop_lookback_days} completed daily candles including the signal candle. Trades are rejected if initial risk is outside {config.min_risk_pct:.1%}-{config.max_risk_pct:.1%}.
6. Exit: sell 50% at 1R; move the runner to cost; then trail with the two completed prior daily lows. Close any remainder after {config.max_hold_days} sessions.
7. Portfolio: at most {config.max_positions} equal Rs {config.slot_capital:,.0f} slots, at most {config.max_new_positions_per_day} new names per session, and never duplicate a live symbol.
8. Costs: {config.side_cost * 10_000:.0f} bps per side ({config.side_cost * 20_000:.0f} bps round trip), including a stated {config.slippage_bps_per_side:.0f} bps/slippage side.

## No-lookahead and execution controls

- Every signal feature is calculated at the completed session close or from earlier bars. All entries occur strictly later.
- The stop-entry, target, and trailing-stop simulation uses future OHLC only to model events after an already-existing order; it never selects a signal with future data.
- Intraday sequence is unavailable in daily OHLC. Stop-versus-target ambiguity is always recorded as a stop; a same-day target followed by a possible breakeven runner stop is also taken at breakeven. Gap stops fill at the opening price.
- The configuration is predeclared in `config.json`; no parameter sweep or out-of-sample selection was used in this run.

## Data

- Source: `{source}`
- Rows after quality filtering: {len(features):,}
- Symbols: {features['symbol'].nunique():,}
- Window: {pd.Timestamp(features['trade_date'].min()).date()} to {pd.Timestamp(features['trade_date'].max()).date()}
- Raw valid setups with a next-day fill: {candidate_count:,}
- Capacity-selected portfolio trades: {len(trades):,}

## Full-sample portfolio performance

{markdown_table(pd.DataFrame([full_metrics]), max_rows=1)}

## Chronological split check

The model was not tuned on the first 60%; these partitions are a stability check. The final 20% is held out for the report.

{markdown_table(split_metrics, max_rows=10)}

## Year-by-year performance

{markdown_table(year_metrics, ["year", "trades", "portfolio_return_pct", "sharpe_ratio", "max_drawdown_pct", "profit_factor", "win_rate_pct"], 20)}

## Exit distribution

{markdown_table(exits, max_rows=20)}

## Best ten selected trades

{markdown_table(best, ["symbol", "signal_date", "entry_date", "exit_date", "net_return", "r_multiple_net", "hold_days", "exit_reason"], 10)}

## Worst ten selected trades

{markdown_table(worst, ["symbol", "signal_date", "entry_date", "exit_date", "net_return", "r_multiple_net", "hold_days", "exit_reason"], 10)}

## Required limitations before live use

- This is daily NSE-style stock data, whereas the source PDF uses intraday MCX commodities. It is an adaptation only.
- The data set's survivorship, corporate-action adjustment, delisted symbols, auction constraints, and actual bid/ask spreads are not proven here.
- Daily OHLC cannot establish the event order within the bar; the conservative fill policy reduces but cannot eliminate that uncertainty.
- Paper trade the exact settings and record live/realistic fills before risking capital. Do not loosen rules based on a few attractive historical examples.

## Locked reporting pack

Every run writes the strategy configuration and input hashes plus: summary, split, year, monthly, exit, trade, equity, and lookahead-audit CSV/JSON artifacts; a rendered equity/drawdown chart; and this report. Future changes should use a new version name rather than silently changing this one.
"""
    (output / "report.md").write_text(result, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    config = StrategyConfig()
    output = Path(args.out_dir).resolve()
    source = Path(args.source).resolve()
    output.mkdir(parents=True, exist_ok=True)

    print(f"Loading {source}")
    raw = load_daily_bars(source)
    print(f"Adding features to {len(raw):,} daily rows")
    features = add_features(raw, config)
    dates = pd.DatetimeIndex(pd.to_datetime(features["trade_date"]).sort_values().unique())
    print("Generating no-lookahead next-session candidates")
    candidates, symbols = generate_candidates(features, config)
    trades = select_portfolio(candidates, config)
    print(f"Candidates: {len(candidates):,}; selected: {len(trades):,}")
    equity = build_equity_curve(trades, symbols, dates, config)
    full_metrics = metrics_for(trades, equity, "full", config)
    splits, _ = make_period_metrics(candidates, symbols, dates, config)
    years = yearly_metrics(candidates, symbols, dates, config)
    months = monthly_metrics(equity)
    exits = exit_breakdown(trades)
    status, reasons = verdict(full_metrics, splits)

    selected_signal_dates = set(pd.to_datetime(trades["signal_date"])) if not trades.empty else set()
    lookahead_audit = pd.DataFrame(
        [
            {
                "strategy": config.name,
                "signal_features_available_at_close": True,
                "all_entries_strictly_after_signal": bool((pd.to_datetime(trades["entry_date"]) > pd.to_datetime(trades["signal_date"])).all()) if not trades.empty else True,
                "negative_shift_used_for_signal_features": False,
                "same_bar_stop_target_policy": "stop_first",
                "same_bar_tp1_runner_policy": "breakeven_if_possible",
                "gap_stop_policy": "fill_at_open",
                "candidate_signals": int(signal_mask(features).sum()),
                "filled_candidates": int(len(candidates)),
                "selected_trades": int(len(trades)),
                "selected_signal_dates": int(len(selected_signal_dates)),
            }
        ]
    )
    quality = pd.DataFrame(
        [
            {
                "rows_after_ohlcv_quality_filter": int(len(raw)),
                "symbols": int(raw["symbol"].nunique()),
                "start": pd.Timestamp(raw["trade_date"].min()).date().isoformat(),
                "end": pd.Timestamp(raw["trade_date"].max()).date().isoformat(),
                "duplicate_symbol_date_rows_after_dedup": int(raw.duplicated(["symbol", "trade_date"]).sum()),
                "invalid_ohlc_rows_after_filter": int((~(raw["high"].ge(raw[["open", "close"]].max(axis=1)) & raw["low"].le(raw[["open", "close"]].min(axis=1)))).sum()),
            }
        ]
    )

    config_dict = dataclasses.asdict(config)
    manifest = {
        "strategy": config.name,
        "status": status,
        "status_reasons": reasons,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "config": config_dict,
        "outputs": [
            "report.md",
            "config.json",
            "run_manifest.json",
            "summary_metrics.csv",
            "split_metrics.csv",
            "yearly_metrics.csv",
            "monthly_metrics.csv",
            "exit_breakdown.csv",
            "trade_log.csv",
            "portfolio_equity_curve.csv",
            "lookahead_audit.csv",
            "data_quality.csv",
            "equity_and_drawdown.png",
        ],
    }
    (output / "config.json").write_text(json.dumps(config_dict, indent=2, default=as_jsonable) + "\n", encoding="utf-8")
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=as_jsonable) + "\n", encoding="utf-8")
    write_csv(pd.DataFrame([full_metrics]), output / "summary_metrics.csv")
    write_csv(splits, output / "split_metrics.csv")
    write_csv(years, output / "yearly_metrics.csv")
    write_csv(months, output / "monthly_metrics.csv")
    write_csv(exits, output / "exit_breakdown.csv")
    write_csv(trades, output / "trade_log.csv")
    write_csv(equity, output / "portfolio_equity_curve.csv")
    write_csv(lookahead_audit, output / "lookahead_audit.csv")
    write_csv(quality, output / "data_quality.csv")
    create_chart(equity, output / "equity_and_drawdown.png")
    build_report(
        output,
        config,
        source,
        features,
        len(candidates),
        trades,
        full_metrics,
        splits,
        years,
        months,
        exits,
        status,
        reasons,
    )
    print(f"Wrote locked research pack to {output}")
    print(json.dumps({"status": status, "summary": full_metrics}, indent=2, default=as_jsonable))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="No-lookahead daily EMA-200 + RSI swing research pack.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Daily OHLCV parquet source.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for the locked report pack.")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
