"""Pure logic for the /api/swing/history/{symbol} endpoint -- mirrors
engine/src/api/swing.rs's normalize_history_range/history_where_clause/
history_month_span/intraday_chart_day/intraday_response_to_candles/
append_live_quote_candle/compute_historical_summary/pct_change_from_offset.
"""

from __future__ import annotations

from datetime import UTC, datetime

from swing_atlas.domain.numeric import round2, round_half_away_from_zero
from swing_atlas.domain.swing.models import HistoricalCandle, HistoricalSummary
from swing_atlas.domain.time_utils import (
    IST,
    compute_bucket,
    is_nse_holiday,
    now_ist,
    prev_trading_day,
)
from swing_atlas.repositories.dhan.market_data import IntradayResponse, QuoteItem


def normalize_history_range(raw: str | None) -> str:
    normalized = (raw or "1d").lower()
    if normalized in ("1d", "live", "intraday"):
        return "1d"
    if normalized in ("3m", "6m", "1y", "3y", "5y"):
        return normalized
    return "1y"


def history_where_clause(range_: str) -> str:
    return {
        "3m": "subtractMonths(today(), 3)",
        "6m": "subtractMonths(today(), 6)",
        "3y": "subtractYears(today(), 3)",
        "5y": "subtractYears(today(), 5)",
    }.get(range_, "subtractYears(today(), 1)")


def history_month_span(range_: str) -> int:
    return {"3m": 3, "6m": 6, "3y": 36, "5y": 60}.get(range_, 12)


def intraday_chart_day() -> datetime:
    now = now_ist()
    today = now.date()
    session_has_started = (
        compute_bucket(now) > 0 or now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    )
    day = today if (not is_nse_holiday(today) and session_has_started) else prev_trading_day(today)
    return datetime(day.year, day.month, day.day, tzinfo=IST)


def intraday_response_to_candles(response: IntradayResponse) -> list[HistoricalCandle]:
    length = min(
        len(response.timestamp),
        len(response.open),
        len(response.high),
        len(response.low),
        len(response.close),
        len(response.volume),
    )
    candles = []
    for idx in range(length):
        try:
            ts = datetime.fromtimestamp(round_half_away_from_zero(response.timestamp[idx]), tz=UTC)
        except (OverflowError, OSError, ValueError):
            continue
        candles.append(
            HistoricalCandle(
                date=ts.astimezone(IST).isoformat(),
                open=round2(response.open[idx]),
                high=round2(response.high[idx]),
                low=round2(response.low[idx]),
                close=round2(response.close[idx]),
                volume=int(max(response.volume[idx], 0.0)),
            )
        )
    return candles


def append_live_quote_candle(candles: list[HistoricalCandle], quote: QuoteItem) -> None:
    last_price = quote.last_price
    if last_price <= 0.0:
        return
    live_price = round2(last_price)
    live_high = round2(max(quote.ohlc.high, last_price))
    live_low = round2(min(quote.ohlc.low, last_price) if quote.ohlc.low > 0.0 else last_price)
    live_open = round2(quote.ohlc.open if quote.ohlc.open > 0.0 else last_price)
    live_volume = quote.volume

    if candles:
        last = candles[-1]
        last.close = live_price
        last.high = max(last.high, live_high)
        last.low = min(last.low, live_low) if last.low > 0.0 else live_low
        if live_volume > 0:
            last.volume = live_volume
        return

    candles.append(
        HistoricalCandle(
            date=now_ist().isoformat(),
            open=live_open,
            high=live_high,
            low=live_low,
            close=live_price,
            volume=live_volume,
        )
    )


def pct_change_from_offset(candles: list[HistoricalCandle], offset: int) -> float:
    if not candles or len(candles) <= offset:
        return 0.0
    latest = candles[-1]
    base = candles[len(candles) - 1 - offset].close
    if base <= 0.0:
        return 0.0
    return round2(((latest.close - base) / base) * 100.0)


def compute_historical_summary(candles: list[HistoricalCandle]) -> HistoricalSummary | None:
    if not candles:
        return None
    latest = candles[-1]
    last_20 = candles[-20:]
    last_252 = candles[-252:]

    avg_volume_20d = sum(c.volume for c in last_20) / len(last_20) if last_20 else 0.0
    high_52w = max((c.high for c in last_252), default=latest.high)
    low_52w = min((c.low for c in last_252), default=latest.low)

    return HistoricalSummary(
        latest_close=round2(latest.close),
        change_pct_1m=pct_change_from_offset(candles, 21),
        change_pct_3m=pct_change_from_offset(candles, 63),
        change_pct_1y=pct_change_from_offset(candles, 252),
        high_52w=round2(high_52w),
        low_52w=round2(low_52w),
        avg_volume_20d=round2(avg_volume_20d),
    )
