"""Tests for CandidateSeed construction from a live Dhan quote -- mirrors
engine/src/api/swing.rs's seed_from_quote/build_candidate_seeds/liquidity_from_tiers.
"""

from swing_atlas.domain.swing.models import WatchRow
from swing_atlas.domain.swing.seeds import (
    build_candidate_seeds,
    liquidity_from_tiers,
    seed_from_quote,
)
from swing_atlas.repositories.dhan.market_data import QuoteItem, QuoteOhlc


def _watch_row(**overrides: object) -> WatchRow:
    defaults: dict[str, object] = dict(
        security_id="1333", symbol="DEMO", company_name="Demo Ltd", tiers=[], enabled=1,
        min_volume=100_000,
    )  # fmt: skip
    defaults.update(overrides)
    return WatchRow(**defaults)  # type: ignore[arg-type]


def test_liquidity_from_tiers_enabled_row_is_large() -> None:
    row = _watch_row(tiers=[], enabled=1)
    assert liquidity_from_tiers(row) == "LARGE"


def test_liquidity_from_tiers_disabled_no_tier1_is_mid() -> None:
    row = _watch_row(tiers=["Tier2"], enabled=0)
    assert liquidity_from_tiers(row) == "MID"


def test_seed_from_quote_uses_ohlc_when_present() -> None:
    row = _watch_row()
    ohlc = QuoteOhlc(open=101.0, high=106.0, low=100.0, close=100.0)
    quote = QuoteItem(last_price=105.0, volume=200_000, ohlc=ohlc)

    seed = seed_from_quote(row, quote, {})

    assert seed.symbol == "DEMO"
    assert seed.last_price == 105.0
    assert seed.prev_close == 100.0
    assert seed.day_change_pct == 5.0
    assert seed.source == "dhan-live"
    assert seed.liquidity_bucket == "LARGE"  # enabled=1 -> liquidity_from_tiers fallback


def test_seed_from_quote_falls_back_when_ohlc_missing() -> None:
    row = _watch_row()
    quote = QuoteItem(
        last_price=105.0, volume=200_000, ohlc=QuoteOhlc(open=0.0, high=0.0, low=0.0, close=0.0)
    )

    seed = seed_from_quote(row, quote, {})

    # prev_close falls back to 0.01 floor, open falls back to prev_close.
    assert seed.prev_close == 0.01
    assert seed.open_price == 0.01


def test_seed_from_quote_uses_volume_map_over_liquidity_from_tiers() -> None:
    row = _watch_row()
    quote = QuoteItem(
        last_price=100.0, volume=1, ohlc=QuoteOhlc(open=100.0, high=100.0, low=100.0, close=100.0)
    )

    seed = seed_from_quote(row, quote, {"DEMO": "MEGA"})

    assert seed.liquidity_bucket == "MEGA"


def test_build_candidate_seeds_returns_empty_without_live_quote_map() -> None:
    rows = [_watch_row()]
    assert build_candidate_seeds(rows, {}, None) == []


def test_build_candidate_seeds_drops_rows_without_a_matching_quote() -> None:
    rows = [_watch_row(security_id="1"), _watch_row(security_id="2", symbol="OTHER")]
    quotes = {"1": QuoteItem(last_price=100.0, volume=100)}

    seeds = build_candidate_seeds(rows, {}, quotes)

    assert len(seeds) == 1
    assert seeds[0].symbol == "DEMO"
