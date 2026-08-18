"""Builds CandidateSeed rows from a live Dhan quote -- mirrors
engine/src/api/swing.rs's build_candidate_seeds/seed_from_quote/liquidity_from_tiers.

seed_from_history/load_historical_fallbacks (a parquet-based CandidateSeed
fallback path) are NOT ported -- confirmed dead code in the Rust original:
build_candidate_seeds only ever calls seed_from_quote, so a watch row with no
live quote is silently dropped from the scanner rather than falling back to a
historical seed. That's a real, if surprising, behavior preserved here as-is.
"""

from __future__ import annotations

from swing_atlas.domain.numeric import round2
from swing_atlas.domain.swing.models import CandidateSeed, WatchRow
from swing_atlas.repositories.dhan.market_data import QuoteItem


def liquidity_from_tiers(row: WatchRow) -> str:
    if any(tier in ("Tier1", "F&O") for tier in row.tiers) or row.enabled == 1:
        return "LARGE"
    return "MID"


def seed_from_quote(row: WatchRow, quote: QuoteItem, volume_map: dict[str, str]) -> CandidateSeed:
    prev_close = max(quote.ohlc.close, 0.01)
    open_price = quote.ohlc.open if quote.ohlc.open > 0.0 else prev_close
    high = quote.ohlc.high if quote.ohlc.high > 0.0 else max(quote.last_price, open_price)
    low = quote.ohlc.low if quote.ohlc.low > 0.0 else min(quote.last_price, open_price)
    last = quote.last_price if quote.last_price > 0.0 else prev_close
    day_change_pct = ((last - prev_close) / prev_close) * 100.0
    open_gap_pct = ((open_price - prev_close) / prev_close) * 100.0
    recovery_pct = ((last - low) / low) * 100.0 if low > 0.0 else 0.0
    distance_to_high_pct = ((high - last) / high) * 100.0 if high > 0.0 else 0.0
    intraday_range_pct = ((high - low) / prev_close) * 100.0 if prev_close > 0.0 else 0.0

    return CandidateSeed(
        symbol=row.symbol,
        company_name=row.company_name,
        tiers=row.tiers,
        liquidity_bucket=volume_map.get(row.symbol, liquidity_from_tiers(row)),
        open_price=round2(open_price),
        high_price=round2(high),
        low_price=round2(low),
        last_price=round2(last),
        prev_close=round2(prev_close),
        day_volume=float(quote.volume),
        day_change_pct=round2(day_change_pct),
        open_gap_pct=round2(open_gap_pct),
        recovery_pct=round2(recovery_pct),
        distance_to_high_pct=round2(distance_to_high_pct),
        intraday_range_pct=round2(intraday_range_pct),
        source="dhan-live",
    )


def build_candidate_seeds(
    rows: list[WatchRow], volume_map: dict[str, str], live_quote_map: dict[str, QuoteItem] | None
) -> list[CandidateSeed]:
    if live_quote_map is None:
        return []
    seeds = []
    for row in rows:
        quote = live_quote_map.get(row.security_id)
        if quote is not None:
            seeds.append(seed_from_quote(row, quote, volume_map))
    return seeds
