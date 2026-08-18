"""The live swing-signal rule engine -- mirrors engine/src/api/swing.rs's
evaluate_live_signal / evaluate_weekly_lab_signal / choose_live_signal.

evaluate_live_signal is the single authority for the live entry/exit decision --
the research-confluence "independent confirmation" rules elsewhere are
diagnostics only and must never override what this function decides.
"""

from __future__ import annotations

from swing_atlas.domain.numeric import round2, round_half_away_from_zero
from swing_atlas.domain.swing.models import CandidateSeed, HistoricalScreenerFeatureRow, LiveSignal
from swing_atlas.domain.swing.models import WeeklyLabCandidate as WeeklyLabRow
from swing_atlas.domain.swing.scoring import live_strategy_score
from swing_atlas.domain.swing.strategies import (
    ScreenerContext,
    default_strategy_status,
    strategy_match_for_screener,
)


def replace_latest_average(avg: float, old_value: float, new_value: float, window: float) -> float:
    return ((avg * window) - old_value + new_value) / window


def parse_volume(raw: str | None) -> float:
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def signal_confidence(status: str, score: int) -> str:
    if status == "ENTRY_NOW":
        return "Enter Now"
    if status == "WATCH":
        return "Watch Only"
    if status == "NO_TRADE":
        return "No Trade"
    if status == "INVALIDATED":
        return "Invalidated"
    return "Wait For Trigger" if score >= 88 else "Developing"


def live_signal_rank(status: str) -> int:
    return {"ENTRY_NOW": 0, "WATCH": 1, "WAIT_FOR_TRIGGER": 2, "NO_TRADE": 3, "INVALIDATED": 4}.get(
        status, 5
    )


def default_live_signal() -> LiveSignal:
    return LiveSignal(
        status="WAIT_FOR_TRIGGER",
        label="Wait For Trigger",
        reason="Live signal rules could not be evaluated from the available data.",
        strategy_id="unscored",
        strategy_label="Unscored",
        strategy_status="Unknown",
        setup_family="Unscored",
        score=0,
        as_of="unknown",
        trigger_price=None,
        trigger_source=None,
    )


def choose_live_signal(daily: LiveSignal, weekly: LiveSignal | None) -> LiveSignal:
    if weekly is None:
        return daily
    if weekly.strategy_id == "king-candle-quality-v1":
        return weekly
    if daily.status in ("ENTRY_NOW", "WATCH"):
        return daily
    return weekly


def evaluate_weekly_lab_signal(
    seed: CandidateSeed, row: WeeklyLabRow, entry_window_open: bool
) -> LiveSignal:
    trigger = max(row.trigger_price, 0.01)
    live_price = max(seed.last_price, 0.01)
    trigger_source = (
        "King candle high + 0.1%"
        if row.strategy_id == "king-candle-quality-v1"
        else "Weekly close baseline"
    )
    score = max(
        60,
        min(
            96,
            round_half_away_from_zero(
                68.0
                + max(0.0, min(1.0, row.rs13w_rank)) * 14.0
                + max(0.0, min(6.0, row.relvol)) * 1.6
                + max(0.0, min(1.0, row.body_ratio)) * 5.0
                + max(0.0, min(4.0, row.range_atr)) * 2.0
                + max(0.0, min(6.0, row.rank_score / 3.0))
            ),
        ),
    )

    lost_supertrend = row.supertrend > 0.0 and live_price < row.supertrend
    trigger_hit = live_price >= trigger

    if lost_supertrend:
        status, label = "INVALIDATED", "Invalidated"
        reason = (
            f"{row.strategy_label} is below weekly Supertrend support: "
            f"live {live_price:.2f}, Supertrend {row.supertrend:.2f}."
        )
    elif row.strategy_id == "king-candle-quality-v1" and trigger_hit and entry_window_open:
        status, label = "ENTRY_NOW", "Enter Now"
        reason = (
            f"King Candle Quality trigger is live: LTP {live_price:.2f} is above "
            f"trigger {trigger:.2f}; relvol {row.relvol:.2f}x and RS13 rank "
            f"{row.rs13w_rank * 100.0:.0f}%."
        )
    elif row.strategy_id == "king-candle-quality-v1" and trigger_hit:
        status, label = "WAIT_FOR_TRIGGER", "Signal Ready"
        reason = (
            f"King Candle Quality trigger {trigger:.2f} is cleared, but "
            "regular-session entry is closed right now."
        )
    elif row.strategy_id == "king-candle-quality-v1":
        status, label = "WAIT_FOR_TRIGGER", "Wait Above King High"
        reason = (
            f"King Candle Quality is armed from {row.signal_date}; needs live price "
            f"above {trigger:.2f}. Current LTP {live_price:.2f}."
        )
    else:
        status, label = "WATCH", "Weekly Trend Watch"
        reason = (
            f"Weekly Supertrend 10-3 is positive from {row.signal_date}; LTP "
            f"{live_price:.2f}, weekly close baseline {row.close:.2f}, "
            f"Supertrend {row.supertrend:.2f}."
        )

    return LiveSignal(
        status=status,
        label=label,
        reason=reason,
        strategy_id=row.strategy_id,
        strategy_label=row.strategy_label,
        strategy_status=row.strategy_status,
        setup_family=row.setup_family,
        score=score,
        as_of=f"dhan-live / weekly {row.signal_date}",
        trigger_price=trigger,
        trigger_source=trigger_source,
    )


def evaluate_live_signal(
    seed: CandidateSeed,
    baseline: HistoricalScreenerFeatureRow | None,
    strategy_statuses: dict[str, str],
    entry_window_open: bool,
) -> LiveSignal:
    if baseline is None:
        return LiveSignal(
            status="WAIT_FOR_TRIGGER",
            label="Need History",
            reason=(
                "Live rule evaluation needs rolling historical features before it "
                "can produce an entry signal."
            ),
            strategy_id="unscored",
            strategy_label="Unscored",
            strategy_status="Unknown",
            setup_family="Unscored",
            score=0,
            as_of=seed.source,
            trigger_price=None,
            trigger_source=None,
        )
    row = baseline

    historical_close = max(row.day_close if row.day_close is not None else seed.prev_close, 0.01)
    close = seed.last_price
    high = max(seed.high_price, seed.last_price)
    low = min(seed.low_price, seed.last_price)
    sma20 = replace_latest_average(
        row.sma20 if row.sma20 is not None else historical_close, historical_close, close, 20.0
    )
    sma50 = replace_latest_average(
        row.sma50 if row.sma50 is not None else historical_close, historical_close, close, 50.0
    )
    sma200 = replace_latest_average(
        row.sma200 if row.sma200 is not None else historical_close, historical_close, close, 200.0
    )
    rsi10 = row.rsi10 if row.rsi10 is not None else 50.0
    avg_volume20 = max(row.avg_volume20 if row.avg_volume20 is not None else 0.0, 1.0)
    high_20d = max(row.high_20d if row.high_20d is not None else high, high)
    historical_high_52w = max(row.high_52w if row.high_52w is not None else high, 0.01)
    high_52w = max(historical_high_52w, high)
    low_52w = min(row.low_52w if row.low_52w is not None else low, low)
    day_volume = seed.day_volume if seed.day_volume > 0.0 else parse_volume(row.day_volume)

    if close <= 0.0 or high_20d <= 0.0 or high_52w <= 0.0 or low_52w <= 0.0:
        return default_live_signal()

    breakout_pct = (high_20d - close) / high_20d * 100.0
    distance_to_52w_high_pct = (high_52w - close) / high_52w * 100.0
    range_span = max(high_52w - low_52w, 0.01)
    range_position_pct = (close - low_52w) / range_span * 100.0
    volume_ratio = day_volume / avg_volume20
    atr14 = max(row.atr14 if row.atr14 is not None else abs(high - low), close * 0.01)
    prior_high20 = (
        row.prior_high20 if row.prior_high20 is not None and row.prior_high20 > 0.0 else high_20d
    )
    prior_close3 = (
        row.prior_close3 if row.prior_close3 is not None and row.prior_close3 > 0.0 else close
    )
    close_location = max(0.0, min(1.0, (close - low) / (high - low))) if high > low else 0.5
    range_atr = max(high - low, 0.0) / atr14 if atr14 > 0.0 else 0.0
    recovery_from_low_pct = max(close - low, 0.0) / low if low > 0.0 else 0.0
    ret3 = close / prior_close3 - 1.0 if prior_close3 > 0.0 else 0.0
    rs60_rank = max(0.0, min(1.0, row.rs60_rank if row.rs60_rank is not None else 0.5))
    market_breadth200 = max(
        0.0, min(1.0, row.market_breadth200 if row.market_breadth200 is not None else 0.5)
    )

    trend_up = close > sma20 and sma20 > sma50
    pullback_zone = sma20 * 0.98 <= close <= sma20 * 1.03
    rsi10_pullback = close > sma200 and rsi10 < 30.0
    tuned_ma_breakout = (
        trend_up
        and market_breadth200 >= 0.38
        and rs60_rank >= 0.58
        and volume_ratio >= 1.3
        and close_location >= 0.58
        and atr14 > 0.0
        and high >= prior_high20 * 1.001
        and close >= prior_high20 * 1.001 * 0.985
    )
    tuned_panic_reversal = (
        ret3 <= -0.08
        and range_atr >= 1.35
        and close_location >= 0.64
        and recovery_from_low_pct >= 0.012
        and atr14 > 0.0
    )

    if tuned_panic_reversal:
        setup_family = "Panic Reversal"
    elif tuned_ma_breakout:
        setup_family = "MA Breakout"
    elif rsi10_pullback:
        setup_family = "RSI10 Pullback Reversion"
    elif trend_up and breakout_pct <= 1.5 and volume_ratio >= 1.1:
        setup_family = "Breakout Setup"
    elif trend_up and pullback_zone:
        setup_family = "Pullback To 20 DMA"
    elif close > sma50 and distance_to_52w_high_pct <= 8.0:
        setup_family = "Near 52W High"
    else:
        setup_family = "Trend Filter"

    score = live_strategy_score(
        trend_up,
        breakout_pct,
        distance_to_52w_high_pct,
        volume_ratio,
        pullback_zone,
        range_position_pct,
    )
    strategy_id, strategy_label = strategy_match_for_screener(
        ScreenerContext(
            setup_family=setup_family,
            score=score,
            trend_up=trend_up,
            pullback_zone=pullback_zone,
            breakout_pct=breakout_pct,
            distance_to_52w_high_pct=distance_to_52w_high_pct,
            range_position_pct=range_position_pct,
            volume_ratio=volume_ratio,
            day_close=close,
            sma20=sma20,
            sma200=sma200,
            rsi10=rsi10,
            tuned_panic_reversal=tuned_panic_reversal,
        )
    )
    strategy_status = strategy_statuses.get(strategy_id) or default_strategy_status(strategy_id)

    trigger_price: float | None
    trigger_source: str | None
    if setup_family in ("MA Breakout", "Breakout Setup"):
        trigger_price = round2(prior_high20 * 1.001)
        trigger_source = "Prior 20D high + 0.1%"
    elif setup_family == "Panic Reversal":
        trigger_price = round2(low + 0.25 * (high - low))
        trigger_source = "25% recovery from intraday low"
    elif setup_family == "Pullback To 20 DMA":
        trigger_price = round2(sma20)
        trigger_source = "Live-adjusted SMA20 reclaim"
    elif setup_family == "Near 52W High":
        trigger_price = round2(historical_high_52w * 1.001)
        trigger_source = "52W high + 0.1%"
    else:
        trigger_price = None
        trigger_source = None

    trigger_hit = trigger_price is not None and close >= trigger_price
    lost_structure = close < sma50 * 0.985 or range_position_pct < 40.0

    if lost_structure:
        status, label = "INVALIDATED", "Invalidated"
        reason = (
            f"Price is below the live trend structure: close {close:.2f}, "
            f"SMA50 {sma50:.2f}, range position {range_position_pct:.2f}%."
        )
    elif strategy_id == "unlinked-screener":
        status, label = "WAIT_FOR_TRIGGER", "Wait For Trigger"
        reason = (
            f"No backtest-linked rule is active yet; score {score}, setup "
            f"{setup_family}, volume ratio {volume_ratio:.2f}."
        )
    elif strategy_status == "Candidate" and not trigger_hit:
        status, label = "WAIT_FOR_TRIGGER", "Wait For Trigger"
        if trigger_price is not None:
            reason = (
                f"{strategy_label} is approved, but live LTP {close:.2f} has not "
                f"crossed trigger {trigger_price:.2f} "
                f"({trigger_source or 'strategy trigger'}) yet."
            )
        else:
            reason = (
                f"{strategy_label} is approved, but no live trigger could be "
                f"derived for setup {setup_family}."
            )
    elif strategy_status == "Candidate" and entry_window_open:
        status, label = "ENTRY_NOW", "Enter Now"
        shown_trigger = trigger_price if trigger_price is not None else close
        reason = (
            f"{strategy_label} trigger is live: LTP {close:.2f} is above trigger "
            f"{shown_trigger:.2f}; score {score}, distance to 52W high "
            f"{distance_to_52w_high_pct:.2f}%, volume ratio {volume_ratio:.2f}."
        )
    elif strategy_status == "Candidate":
        status, label = "WAIT_FOR_TRIGGER", "Signal Ready"
        reason = (
            f"{strategy_label} matches the approved rule, but NSE regular-session "
            "entry is closed right now."
        )
    elif strategy_status == "Watch":
        status, label = "WATCH", "Watch Only"
        reason = (
            f"{strategy_label} matches, but latest backtest diagnostics mark it "
            "Watch rather than Candidate."
        )
    else:
        status, label = "NO_TRADE", "No Trade"
        reason = (
            f"{strategy_label} is {strategy_status}, so this rule is not approved "
            "for fresh live entries."
        )

    return LiveSignal(
        status=status,
        label=label,
        reason=reason,
        strategy_id=strategy_id,
        strategy_label=strategy_label,
        strategy_status=strategy_status,
        setup_family=setup_family,
        score=score,
        as_of=f"{seed.source} / baseline {row.trade_date or ''}",
        trigger_price=trigger_price,
        trigger_source=trigger_source,
    )
