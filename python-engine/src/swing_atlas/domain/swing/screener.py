"""The historical-screener row derivation -- mirrors
engine/src/api/swing.rs::map_historical_screener_row/historical_trade_plan and
the fresh-signals paper-eligibility/staging rules.

This is a *second*, independent setup-family/strategy classification path from
domain/swing/live_signal.py's evaluate_live_signal: this one works off the
daily_screener_features cache alone (no live quote), and historical_screener /
fresh_signals are its only callers. Keep the two separate, as the Rust original
does -- they compute overlapping but not identical setup_family boolean rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date

from swing_atlas.domain.numeric import round2, round_half_away_from_zero
from swing_atlas.domain.swing.models import HistoricalScreenerFeatureRow, HistoricalScreenerRow
from swing_atlas.domain.swing.strategies import (
    ScreenerContext,
    default_strategy_status,
    strategy_match_for_screener,
)
from swing_atlas.domain.time_utils import now_ist


def map_historical_screener_row(
    row: HistoricalScreenerFeatureRow, strategy_statuses: dict[str, str]
) -> HistoricalScreenerRow | None:
    """Returns None when the row is missing a required field or fails the
    basic sanity gate (non-positive close/high/low) -- the caller filter_maps
    these away, matching Rust's Option-chaining `?` early-returns."""
    if row.symbol is None or row.trade_date is None or row.day_close is None:
        return None
    if row.day_high is None or row.day_low is None:
        return None
    if row.day_volume is None:
        return None
    try:
        day_volume = int(row.day_volume)
    except ValueError:
        return None
    if row.sma20 is None or row.sma50 is None:
        return None
    if row.avg_volume20 is None or row.high_20d is None:
        return None
    if row.high_52w is None or row.low_52w is None:
        return None

    symbol = row.symbol
    trade_date = row.trade_date
    day_close = row.day_close
    sma20 = row.sma20
    sma50 = row.sma50
    sma200 = row.sma200 if row.sma200 is not None else sma50
    avg_volume20 = row.avg_volume20
    high_20d = row.high_20d
    high_52w = row.high_52w
    low_52w = row.low_52w
    day_high = row.day_high
    day_low = row.day_low

    if day_close <= 0.0 or high_20d <= 0.0 or high_52w <= 0.0 or low_52w <= 0.0:
        return None

    atr14 = max(row.atr14 if row.atr14 is not None else abs(day_high - day_low), 0.0)
    atr_pct = (
        row.atr_pct
        if row.atr_pct is not None and row.atr_pct > 0.0
        else (atr14 / day_close if day_close > 0.0 else 0.0)
    )
    range_pct = (
        row.range_pct
        if row.range_pct is not None and row.range_pct > 0.0
        else (max(day_high - day_low, 0.0) / day_close if day_close > 0.0 else 0.0)
    )
    close_location = min(
        max(
            row.close_location
            if row.close_location is not None
            else ((day_close - day_low) / (day_high - day_low) if day_high > day_low else 0.5),
            0.0,
        ),
        1.0,
    )
    if row.gap_pct is not None:
        gap_pct = row.gap_pct
    elif row.day_open is not None and row.day_open > 0.0:
        gap_pct = ((row.day_open - day_close) / day_close) * 100.0
    else:
        gap_pct = 0.0
    prior_high20 = (
        row.prior_high20 if row.prior_high20 is not None and row.prior_high20 > 0.0 else high_20d
    )
    prior_high55 = (
        row.prior_high55
        if row.prior_high55 is not None and row.prior_high55 > 0.0
        else prior_high20
    )
    prior_high252 = (
        row.prior_high252 if row.prior_high252 is not None and row.prior_high252 > 0.0 else high_52w
    )
    prior_close3 = (
        row.prior_close3 if row.prior_close3 is not None and row.prior_close3 > 0.0 else day_close
    )
    prior_low20 = (
        row.prior_low20 if row.prior_low20 is not None and row.prior_low20 > 0.0 else day_low
    )
    rs60_rank = min(max(row.rs60_rank if row.rs60_rank is not None else 0.5, 0.0), 1.0)
    rs120_rank = min(max(row.rs120_rank if row.rs120_rank is not None else 0.5, 0.0), 1.0)
    market_breadth200 = min(
        max(row.market_breadth200 if row.market_breadth200 is not None else 0.5, 0.0), 1.0
    )
    ret3 = (
        row.ret3
        if row.ret3 is not None
        else (day_close / prior_close3 - 1.0 if prior_close3 > 0.0 else 0.0)
    )
    range_atr = (
        row.range_atr
        if row.range_atr is not None and row.range_atr > 0.0
        else (max(day_high - day_low, 0.0) / atr14 if atr14 > 0.0 else 0.0)
    )
    recovery_from_low_pct = (
        row.recovery_from_low_pct
        if row.recovery_from_low_pct is not None
        else (max(day_close - day_low, 0.0) / day_low if day_low > 0.0 else 0.0)
    )

    breakout_pct = ((prior_high20 - day_close) / prior_high20) * 100.0
    distance_to_52w_high_pct = ((high_52w - day_close) / high_52w) * 100.0
    range_span = max(high_52w - low_52w, 0.01)
    range_position_pct = ((day_close - low_52w) / range_span) * 100.0
    volume_ratio = day_volume / max(avg_volume20, 1.0)
    trend_up = day_close > sma20 and sma20 > sma50
    pullback_zone = sma20 * 0.98 <= day_close <= sma20 * 1.03
    rsi10 = row.rsi10 if row.rsi10 is not None else 50.0
    rsi10_pullback = day_close > sma200 and rsi10 < 30.0
    breakout_close = day_close > prior_high20 and close_location >= 0.6
    compression_breakout = (
        breakout_close
        and volume_ratio >= 1.05
        and atr_pct < 0.08
        and range_pct <= max(atr_pct * 1.05, 0.015)
    )
    failed_breakdown_reclaim = (
        day_low < prior_low20
        and day_close > prior_low20
        and close_location >= 0.65
        and volume_ratio >= 0.8
    )
    tuned_ma_breakout = (
        trend_up
        and market_breadth200 >= 0.38
        and rs60_rank >= 0.58
        and volume_ratio >= 1.3
        and close_location >= 0.58
        and atr14 > 0.0
        and day_high >= prior_high20 * 1.001
        and day_close >= prior_high20 * 1.001 * 0.985
    )
    tuned_panic_reversal = (
        ret3 <= -0.08
        and range_atr >= 1.35
        and close_location >= 0.64
        and recovery_from_low_pct >= 0.012
        and atr14 > 0.0
    )
    relative_strength_leader = (
        rs60_rank >= 0.75
        and rs120_rank >= 0.65
        and day_close > sma50
        and (
            distance_to_52w_high_pct <= 10.0
            or day_close > prior_high55
            or day_close > prior_high252
        )
    )

    if tuned_panic_reversal:
        setup_family = "Panic Reversal"
    elif tuned_ma_breakout:
        setup_family = "MA Breakout"
    elif rsi10_pullback:
        setup_family = "RSI10 Pullback Reversion"
    elif failed_breakdown_reclaim:
        setup_family = "Failed Breakdown Reclaim"
    elif compression_breakout:
        setup_family = "Compression Breakout"
    elif trend_up and breakout_close and volume_ratio >= 1.1:
        setup_family = "Breakout Continuation"
    elif relative_strength_leader:
        setup_family = "Relative Strength Leader"
    elif trend_up and pullback_zone:
        setup_family = "Pullback To 20 DMA"
    elif day_close > sma50 and distance_to_52w_high_pct <= 8.0:
        setup_family = "Near 52W High"
    else:
        setup_family = "Trend Filter"

    if trend_up:
        trend_label = "Uptrend"
    elif day_close > sma50:
        trend_label = "Constructive"
    else:
        trend_label = "Needs Work"

    score = 50.0
    if trend_up:
        score += 18.0
    if breakout_close:
        score += 14.0
    elif breakout_pct <= 1.5:
        score += 10.0
    elif breakout_pct <= 4.0:
        score += 8.0
    if distance_to_52w_high_pct <= 8.0:
        score += 10.0
    if volume_ratio >= 1.2:
        score += 10.0
    elif volume_ratio >= 1.0:
        score += 5.0
    if pullback_zone:
        score += 8.0
    if rsi10_pullback:
        score += 16.0
    if failed_breakdown_reclaim:
        score += 14.0
    if tuned_ma_breakout:
        score += 20.0
    if tuned_panic_reversal:
        score += 24.0
    if compression_breakout:
        score += 12.0
    if rs60_rank >= 0.75:
        score += 8.0
    elif rs60_rank >= 0.60:
        score += 4.0
    if close_location >= 0.75:
        score += 6.0
    if market_breadth200 >= 0.45:
        score += 4.0
    if range_position_pct >= 70.0:
        score += 6.0
    final_score = min(max(round_half_away_from_zero(score), 50), 96)

    strategy_id, strategy_label = strategy_match_for_screener(
        ScreenerContext(
            setup_family=setup_family,
            score=final_score,
            trend_up=trend_up,
            pullback_zone=pullback_zone,
            breakout_pct=breakout_pct,
            distance_to_52w_high_pct=distance_to_52w_high_pct,
            range_position_pct=range_position_pct,
            volume_ratio=volume_ratio,
            day_close=day_close,
            sma20=sma20,
            sma200=sma200,
            rsi10=rsi10,
            tuned_panic_reversal=tuned_panic_reversal,
        )
    )
    strategy_status = strategy_statuses.get(strategy_id, default_strategy_status(strategy_id))
    planned_entry, stop_loss, target_price, risk_reward = historical_trade_plan(
        setup_family, day_close, day_low, sma20, atr14, prior_high20, prior_close3, prior_low20
    )

    return HistoricalScreenerRow(
        symbol=symbol,
        as_of=trade_date,
        setup_family=setup_family,
        strategy_id=strategy_id,
        strategy_label=strategy_label,
        strategy_status=strategy_status,
        score=final_score,
        trend_label=trend_label,
        close=round2(day_close),
        sma20=round2(sma20),
        sma50=round2(sma50),
        avg_volume20=round2(avg_volume20),
        volume_ratio=round2(volume_ratio),
        distance_to_20d_high_pct=round2(max(breakout_pct, 0.0)),
        distance_to_52w_high_pct=round2(max(distance_to_52w_high_pct, 0.0)),
        range_position_pct=round2(min(max(range_position_pct, 0.0), 100.0)),
        atr14=round2(atr14),
        atr_pct=round2(atr_pct * 100.0),
        close_location=round2(close_location * 100.0),
        gap_pct=round2(gap_pct),
        rs60_rank=round2(rs60_rank * 100.0),
        rs120_rank=round2(rs120_rank * 100.0),
        market_breadth200=round2(market_breadth200 * 100.0),
        planned_entry=planned_entry,
        stop_loss=stop_loss,
        target_price=target_price,
        risk_reward=risk_reward,
    )


def historical_trade_plan(
    setup_family: str,
    close: float,
    low: float,
    sma20: float,
    atr14: float,
    prior_high20: float,
    prior_close3: float,
    prior_low20: float,
) -> tuple[str, float, float, float]:
    atr = max(atr14, close * 0.015, 0.01)
    if setup_family == "MA Breakout":
        raw_stop = min(low, close - atr)
    elif setup_family == "Panic Reversal":
        raw_stop = low
    elif setup_family in ("Breakout Continuation", "Compression Breakout"):
        raw_stop = min(prior_high20 - 0.35 * atr, close - 1.2 * atr)
    elif setup_family == "Pullback To 20 DMA":
        raw_stop = min(sma20 - 0.8 * atr, low - 0.25 * atr)
    elif setup_family == "Failed Breakdown Reclaim":
        raw_stop = min(prior_low20, low) - 0.25 * atr
    elif setup_family == "RSI10 Pullback Reversion":
        raw_stop = close - 1.4 * atr
    elif setup_family in ("Relative Strength Leader", "Near 52W High"):
        raw_stop = close - 1.6 * atr
    else:
        raw_stop = close - 1.5 * atr
    stop_loss = round2(min(max(raw_stop, close * 0.88), close - 0.01))
    risk = max(close - stop_loss, 0.01)

    if setup_family == "MA Breakout":
        reward_multiple = 2.5
    elif setup_family == "Panic Reversal":
        reward_multiple = 2.0
    elif setup_family == "RSI10 Pullback Reversion":
        reward_multiple = 1.4
    elif setup_family in ("Pullback To 20 DMA", "Failed Breakdown Reclaim"):
        reward_multiple = 1.8
    else:
        reward_multiple = 2.0

    if setup_family == "Panic Reversal" and prior_close3 > close:
        target_price = round2(prior_close3)
    else:
        target_price = round2(close + risk * reward_multiple)
    risk_reward = round2((target_price - close) / risk)

    if setup_family == "MA Breakout":
        planned_entry = f"Breakout trigger above Rs {prior_high20:.2f}; confirm the 20D level holds"
    elif setup_family == "Panic Reversal":
        target_hint = max(prior_close3, close)
        planned_entry = (
            f"Panic reclaim hold above Rs {low:.2f}; target pre-panic close Rs {target_hint:.2f}"
        )
    elif setup_family in ("Breakout Continuation", "Compression Breakout"):
        planned_entry = f"Next session strength above Rs {max(prior_high20, close):.2f}"
    elif setup_family == "Pullback To 20 DMA":
        planned_entry = f"Buy zone near 20 DMA Rs {sma20:.2f} to close Rs {close:.2f}"
    elif setup_family == "Failed Breakdown Reclaim":
        planned_entry = f"Reclaim hold above Rs {prior_low20:.2f}"
    else:
        planned_entry = f"Next session confirmation near Rs {close:.2f}"

    return planned_entry, stop_loss, target_price, risk_reward


def matches_setup_filter(row: HistoricalScreenerRow, setup_filter: str) -> bool:
    if setup_filter == "all":
        return True
    if setup_filter in ("ma", "ma-breakout"):
        return row.setup_family == "MA Breakout"
    if setup_filter in ("panic", "panic-reversal"):
        return row.setup_family == "Panic Reversal"
    if setup_filter == "breakout":
        return row.setup_family in (
            "Breakout Setup",
            "Breakout Continuation",
            "Compression Breakout",
        )
    if setup_filter == "pullback":
        return row.setup_family == "Pullback To 20 DMA"
    if setup_filter == "compression":
        return row.setup_family == "Compression Breakout"
    if setup_filter in ("reclaim", "failed-breakdown"):
        return row.setup_family == "Failed Breakdown Reclaim"
    if setup_filter in ("rs", "relative-strength"):
        return row.setup_family == "Relative Strength Leader"
    if setup_filter in ("rsi10", "reversion"):
        return row.setup_family == "RSI10 Pullback Reversion"
    if setup_filter in ("52wh", "near-high"):
        return row.setup_family == "Near 52W High"
    if setup_filter == "trend":
        return row.setup_family == "Trend Filter"
    return True


def matches_strategy_filter(row: HistoricalScreenerRow, strategy_filter: str) -> bool:
    if strategy_filter == "all":
        return True
    if strategy_filter in ("fresh", "signals"):
        return row.strategy_status in ("Candidate", "Watch")
    return (
        row.strategy_id.lower() == strategy_filter
        or row.strategy_label.lower().replace(" ", "-") == strategy_filter
        or row.strategy_status.lower() == strategy_filter
    )


def signal_key_for(row: HistoricalScreenerRow) -> str:
    return f"{row.symbol}|{row.strategy_id}"


def signal_date_is_fresh(value: str) -> bool:
    try:
        year, month, day = (int(part) for part in value.split("-"))
        signal_date = _date(year, month, day)
    except ValueError:
        return False
    age_days = (now_ist().date() - signal_date).days
    return 0 <= age_days <= 4


@dataclass(frozen=True, slots=True)
class PaperRule:
    stop_loss_pct: float
    take_profit_pct: float
    source: str


_PAPER_RULES: dict[str, PaperRule] = {
    "tuned-panic-reversal-v1": PaperRule(4.0, 10.0, "tuned panic reversal lab model"),
    "near-52w-high-v1": PaperRule(5.0, 10.0, "near-52w-high backtest family"),
    "near-52w-high-runner-v2": PaperRule(5.0, 10.0, "near-52w-high backtest family"),
    "near-52w-high-volume-v3": PaperRule(5.0, 10.0, "near-52w-high backtest family"),
    "momentum-core-v1": PaperRule(5.0, 10.0, "near-52w-high backtest family"),
    "pullback-20dma-v1": PaperRule(3.0, 6.0, "pullback-20dma backtest family"),
    "pullback-quality-v2": PaperRule(3.0, 6.0, "pullback-20dma backtest family"),
    "rsi10-pullback-reversion-v1": PaperRule(4.0, 4.0, "engine RSI10 pullback model"),
    "failed-breakdown-reclaim-v1": PaperRule(4.0, 7.0, "daily failed-breakdown reclaim model"),
    "compression-breakout-v1": PaperRule(4.0, 8.0, "swing-breakout backtest family"),
    "breakout-continuation-v1": PaperRule(4.0, 8.0, "swing-breakout backtest family"),
    "swing-breakout-v1": PaperRule(4.0, 8.0, "swing-breakout backtest family"),
    "rs-leader-continuation-v1": PaperRule(5.0, 10.0, "relative-strength continuation model"),
}


def paper_rule_for_strategy(strategy_id: str) -> PaperRule | None:
    return _PAPER_RULES.get(strategy_id)


def is_paper_eligible_signal(row: HistoricalScreenerRow) -> bool:
    return (
        row.strategy_status in ("Candidate", "Watch")
        and paper_rule_for_strategy(row.strategy_id) is not None
        and row.close > 0.0
        and signal_date_is_fresh(row.as_of)
    )


def quantity_for_capital(price: float, capital: float) -> int:
    return max(int(capital / max(price, 0.01)), 1)
