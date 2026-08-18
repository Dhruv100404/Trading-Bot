"""Tests for the /api/swing/history/{symbol} pure logic -- mirrors
engine/src/api/swing.rs's normalize_history_range/append_live_quote_candle/
compute_historical_summary/pct_change_from_offset/intraday_response_to_candles.
"""

from swing_atlas.domain.swing.history import (
    append_live_quote_candle,
    compute_historical_summary,
    history_month_span,
    history_where_clause,
    intraday_response_to_candles,
    normalize_history_range,
    pct_change_from_offset,
)
from swing_atlas.domain.swing.models import HistoricalCandle
from swing_atlas.repositories.dhan.market_data import IntradayResponse, QuoteItem, QuoteOhlc


def _candle(
    date: str, close: float, high: float | None = None, low: float | None = None
) -> HistoricalCandle:
    return HistoricalCandle(
        date=date, open=close, high=high or close, low=low or close, close=close, volume=1000
    )


def test_normalize_history_range() -> None:
    assert normalize_history_range(None) == "1d"
    assert normalize_history_range("LIVE") == "1d"
    assert normalize_history_range("intraday") == "1d"
    assert normalize_history_range("3m") == "3m"
    assert normalize_history_range("5Y") == "5y"
    assert normalize_history_range("garbage") == "1y"


def test_history_where_clause_and_month_span() -> None:
    assert history_where_clause("3m") == "subtractMonths(today(), 3)"
    assert history_where_clause("5y") == "subtractYears(today(), 5)"
    assert history_where_clause("1y") == "subtractYears(today(), 1)"

    assert history_month_span("3m") == 3
    assert history_month_span("5y") == 60
    assert history_month_span("1y") == 12


def test_pct_change_from_offset() -> None:
    candles = [_candle(f"2026-01-{i:02d}", close=float(100 + i)) for i in range(1, 11)]

    # latest close = 110 (day 10), offset 9 -> day 1 close = 101
    change = pct_change_from_offset(candles, 9)
    assert change == round(((110 - 101) / 101) * 100, 2)


def test_pct_change_from_offset_returns_zero_when_insufficient_history() -> None:
    candles = [_candle("2026-01-01", close=100.0)]
    assert pct_change_from_offset(candles, 21) == 0.0
    assert pct_change_from_offset([], 21) == 0.0


def test_compute_historical_summary_empty_returns_none() -> None:
    assert compute_historical_summary([]) is None


def test_compute_historical_summary_uses_last_20_and_252() -> None:
    candles = [
        _candle(f"2026-01-{i:02d}", close=float(100 + i), high=float(105 + i), low=float(95 + i))
        for i in range(1, 31)
    ]

    summary = compute_historical_summary(candles)

    assert summary is not None
    assert summary.latest_close == candles[-1].close
    assert summary.high_52w == max(c.high for c in candles)
    assert summary.low_52w == min(c.low for c in candles)


def test_append_live_quote_candle_updates_last_bar_in_place() -> None:
    candles = [_candle("2026-01-01T09:15:00", close=100.0, high=101.0, low=99.0)]
    quote = QuoteItem(
        last_price=102.0, volume=5000, ohlc=QuoteOhlc(open=99.5, high=102.5, low=98.5, close=99.0)
    )

    append_live_quote_candle(candles, quote)

    assert len(candles) == 1
    assert candles[0].close == 102.0
    assert candles[0].high == 102.5  # max of prior high and live high
    assert candles[0].low == 98.5  # min of prior low and live low
    assert candles[0].volume == 5000


def test_append_live_quote_candle_appends_new_bar_when_no_candles() -> None:
    quote = QuoteItem(
        last_price=50.0, volume=100, ohlc=QuoteOhlc(open=48.0, high=51.0, low=47.0, close=49.0)
    )

    candles: list[HistoricalCandle] = []
    append_live_quote_candle(candles, quote)

    assert len(candles) == 1
    assert candles[0].close == 50.0


def test_append_live_quote_candle_ignores_non_positive_last_price() -> None:
    candles = [_candle("2026-01-01", close=100.0)]
    quote = QuoteItem(last_price=0.0, volume=100, ohlc=QuoteOhlc())

    append_live_quote_candle(candles, quote)

    assert candles[0].close == 100.0  # unchanged


def test_intraday_response_to_candles_truncates_to_shortest_array() -> None:
    response = IntradayResponse(
        open=[100.0, 101.0],
        high=[102.0, 103.0],
        low=[99.0, 100.0],
        close=[101.0, 102.0],
        timestamp=[1_700_000_000.0, 1_700_000_060.0, 1_700_000_120.0],  # one extra
        volume=[500.0, 600.0],
    )

    candles = intraday_response_to_candles(response)

    assert len(candles) == 2
    assert candles[0].close == 101.0
    assert candles[1].close == 102.0
