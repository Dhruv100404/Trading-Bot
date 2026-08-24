"""Daily Volume Breakout -- frozen spec, plus one-factor-at-a-time tuning.

Frozen baseline (as specified):
  green candle, volume >= 8x prior 50d avg, close in top 15% of range,
  close > EMA20 > EMA50 with EMA20 rising, close within 15% of the prior
  252-session high, price >= 25, prior 20d traded value >= 75 lakh.
  Entry next open, rank by relative volume, max 5 positions equal weight,
  exit at close after 20 sessions, 0.26% round trip.

This differs from swing_volume_spike_review's built-in strategies (which use
an SMA50/200 trend stack, a 0.70 close-location floor, a market-breadth gate
and a composite rank score), so the signal is built here rather than reusing
context_mask. Everything downstream -- exit simulation, portfolio selection,
daily P&L, metrics, splits -- is imported from that module so results stay
directly comparable and the no-lookahead execution path is the same one
already verified there (signal at close of T, fill at open of T+1).

"EMA20 rising" is implemented as ema20 > ema20 five sessions ago; a literal
one-day comparison is too noisy to be a meaningful trend condition.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from swing_volume_spike_review import (
    DEFAULT_DAILY_CACHE,
    DEFAULT_OUT_DIR,
    DEFAULT_PARQUET_DIR,
    DEFAULT_VOLUME_GROUPS,
    ROUND_TRIP_COST,
    StrategySpec,
    add_features,
    append_recent_parquet_daily,
    build_symbol_tables,
    daily_returns_from_trades,
    exit_for_candidate,
    load_daily_cache,
    load_volume_groups,
    metrics_for_strategy,
    period_analysis,
    select_portfolio_trades,
    split_metrics,
)


class Config:
    """One backtest configuration. Defaults are the frozen spec."""

    def __init__(
        self,
        name: str,
        relvol_min: float = 8.0,
        close_loc_min: float = 0.85,
        hold_days: int = 20,
        max_positions: int = 5,
        high_prox: float = 0.85,
        stop_atr: float | None = None,
        require_fresh: bool = False,
        min_price: float = 25.0,
        min_adv: float = 7_500_000.0,
    ) -> None:
        self.name = name
        self.relvol_min = relvol_min
        self.close_loc_min = close_loc_min
        self.hold_days = hold_days
        self.max_positions = max_positions
        self.high_prox = high_prox
        self.stop_atr = stop_atr
        self.require_fresh = require_fresh
        self.min_price = min_price
        self.min_adv = min_adv

    def to_spec(self) -> StrategySpec:
        return StrategySpec(
            name=self.name,
            label=self.name,
            context="raw_liquid",  # unused: signal is built by build_signal below
            relvol50_min=self.relvol_min,
            ret1_min=0.0,
            close_location_min=self.close_loc_min,
            hold_days=self.hold_days,
            exit_mode="fixed" if self.stop_atr is None else "target_stop",
            stop_atr=self.stop_atr if self.stop_atr is not None else 2.5,
            target_atr=None,
            max_new_per_day=self.max_positions,
            max_positions=self.max_positions,
        )


def build_signal(df: pd.DataFrame, cfg: Config) -> pd.Series:
    fresh = (
        (df["prev_relvol50"].isna() | df["prev_relvol50"].lt(1.5))
        if cfg.require_fresh
        else pd.Series(True, index=df.index)
    )
    return (
        df["close"].gt(df["open"])
        & df["relvol50"].ge(cfg.relvol_min)
        & df["close_location"].ge(cfg.close_loc_min)
        & df["close"].gt(df["ema20"])
        & df["ema20"].gt(df["ema50"])
        & df["ema20_rising"]
        & df["close"].ge(df["prior_high252"] * cfg.high_prox)
        & df["close"].ge(cfg.min_price)
        & df["adv20_prior"].ge(cfg.min_adv)
        & df["atr14"].notna()
        & fresh
    ).fillna(False)


def candidates_for(df: pd.DataFrame, symbol_tables: dict[str, pd.DataFrame], cfg: Config) -> pd.DataFrame:
    spec = cfg.to_spec()
    mask = build_signal(df, cfg)
    cols = ["symbol", "trade_date", "local_idx", "relvol50", "close_location", "volume_group"]
    cand = df.loc[mask, cols].copy()
    if cand.empty:
        return cand

    rows: list[dict[str, object]] = []
    for row in cand.itertuples(index=False):
        sdf = symbol_tables.get(row.symbol)
        if sdf is None:
            continue
        signal_idx = int(row.local_idx)
        entry_idx = signal_idx + 1
        if entry_idx >= len(sdf):
            continue
        exit_idx, exit_price, exit_reason, hold = exit_for_candidate(sdf, signal_idx, spec)
        if exit_idx < 0:
            continue
        entry = float(sdf.at[entry_idx, "open"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        rows.append(
            {
                "strategy": cfg.name,
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
                "hold_days": hold,
                "net_return": exit_price / entry - 1 - ROUND_TRIP_COST,
                "relvol50": float(row.relvol50),
                "close_location": float(row.close_location),
                # select_portfolio_trades ranks on this; spec says highest relvol first
                "rank_score": float(row.relvol50),
            }
        )
    return pd.DataFrame(rows)


def run_config(df, symbol_tables, trading_dates, start, end, cfg: Config) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    spec = cfg.to_spec()
    cand = candidates_for(df, symbol_tables, cfg)
    trades = select_portfolio_trades(cand, spec) if not cand.empty else cand
    daily = daily_returns_from_trades(trades, symbol_tables, trading_dates, spec.max_positions)
    m = metrics_for_strategy(trades, daily, cfg.name)
    split = split_metrics(trades, daily, start, end) if not trades.empty else pd.DataFrame()
    for tag in ("in_sample", "validation", "out_of_sample"):
        sub = split[split["split"] == tag] if not split.empty else pd.DataFrame()
        m[f"{tag}_return_pct"] = round(float(sub.iloc[0]["portfolio_return_pct"]), 2) if not sub.empty else np.nan
        m[f"{tag}_sharpe"] = round(float(sub.iloc[0]["daily_sharpe"]), 3) if not sub.empty else np.nan
        m[f"{tag}_trades"] = int(sub.iloc[0]["trades"]) if not sub.empty else 0
    m["relvol_min"] = cfg.relvol_min
    m["close_loc_min"] = cfg.close_loc_min
    m["hold_days"] = cfg.hold_days
    m["max_positions"] = cfg.max_positions
    m["high_prox"] = cfg.high_prox
    m["stop_atr"] = cfg.stop_atr if cfg.stop_atr is not None else "none"
    m["require_fresh"] = cfg.require_fresh
    return m, trades, daily


def build_configs() -> list[Config]:
    cfgs = [Config("baseline_frozen_spec")]
    for v in [4.0, 5.0, 6.0, 10.0, 12.0]:
        cfgs.append(Config(f"relvol_{v}", relvol_min=v))
    for c in [0.60, 0.70, 0.80, 0.90]:
        cfgs.append(Config(f"closeloc_{c}", close_loc_min=c))
    for h in [10, 15, 25, 30, 40]:
        cfgs.append(Config(f"hold_{h}", hold_days=h))
    for p in [3, 8, 10, 15]:
        cfgs.append(Config(f"positions_{p}", max_positions=p))
    for hp in [0.75, 0.80, 0.90, 0.95]:
        cfgs.append(Config(f"highprox_{hp}", high_prox=hp))
    for s in [1.5, 2.0, 2.5, 3.0]:
        cfgs.append(Config(f"stop_{s}", stop_atr=s))
    cfgs.append(Config("fresh_spike_only", require_fresh=True))
    return cfgs


def main() -> None:
    print("Loading daily cache")
    raw = load_daily_cache(DEFAULT_DAILY_CACHE)
    raw = append_recent_parquet_daily(raw, DEFAULT_PARQUET_DIR)
    groups = load_volume_groups(DEFAULT_VOLUME_GROUPS)
    print("Adding swing features")
    df = add_features(raw, groups)
    df["ema20_rising"] = df["ema20"] > df.groupby("symbol")["ema20"].shift(5)
    symbol_tables = build_symbol_tables(df)
    trading_dates = df["trade_date"].drop_duplicates().sort_values()
    start, end = pd.Timestamp(df["trade_date"].min()), pd.Timestamp(df["trade_date"].max())
    print(f"Window: {start.date()} -> {end.date()}; symbols: {df['symbol'].nunique():,}")

    rows = []
    for cfg in build_configs():
        print(f"Backtesting {cfg.name}")
        m, trades, daily = run_config(df, symbol_tables, trading_dates, start, end, cfg)
        rows.append(m)
        if cfg.name == "baseline_frozen_spec":
            trades.to_csv(DEFAULT_OUT_DIR / "dvb_baseline_trades.csv", index=False)
            daily.to_csv(DEFAULT_OUT_DIR / "dvb_baseline_equity.csv", index=False)
            monthly = period_analysis(daily, trades, "M")
            monthly.to_csv(DEFAULT_OUT_DIR / "dvb_baseline_monthly.csv", index=False)

    out = pd.DataFrame(rows)
    cols = [
        "strategy", "trades", "win_rate_pct", "profit_factor", "expectancy_pct",
        "avg_win_pct", "avg_loss_pct", "portfolio_return_pct", "daily_sharpe", "p_value",
        "max_drawdown_pct", "avg_hold_days", "avg_active_positions",
        "in_sample_return_pct", "validation_return_pct", "out_of_sample_return_pct",
        "out_of_sample_sharpe", "out_of_sample_trades",
        "relvol_min", "close_loc_min", "hold_days", "max_positions", "high_prox", "stop_atr", "require_fresh",
    ]
    out = out[[c for c in cols if c in out.columns]]
    path = DEFAULT_OUT_DIR / "daily_volume_breakout_tuning.csv"
    out.to_csv(path, index=False)
    print(out.to_string(index=False))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
