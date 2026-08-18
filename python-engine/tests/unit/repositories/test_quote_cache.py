"""Tests for the double-checked-locking Dhan quote cache -- mirrors
engine/src/api/swing.rs::get_live_quotes/read_cached_quotes/read_stale_cached_quotes.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from swing_atlas.repositories.dhan.market_data import QuoteItem
from swing_atlas.repositories.dhan.quote_cache import QuoteCache


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def test_fetches_and_caches_on_first_call(client: httpx.AsyncClient) -> None:
    cache = QuoteCache()
    quote = QuoteItem(last_price=100.0)
    with patch(
        "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
        AsyncMock(return_value={"123": quote}),
    ) as mock_fetch:
        result = await cache.get_live_quotes(
            client, "https://api.dhan.co", "tok", "cid", "/q", ["123"]
        )

    assert result == {"123": quote}
    mock_fetch.assert_awaited_once()


async def test_second_call_within_ttl_uses_cache_not_refetch(client: httpx.AsyncClient) -> None:
    cache = QuoteCache()
    quote = QuoteItem(last_price=100.0)
    with patch(
        "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
        AsyncMock(return_value={"123": quote}),
    ) as mock_fetch:
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])

    mock_fetch.assert_awaited_once()


async def test_requesting_a_security_id_not_in_cache_is_a_full_miss(
    client: httpx.AsyncClient,
) -> None:
    cache = QuoteCache()
    with patch(
        "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
        AsyncMock(return_value={"123": QuoteItem(last_price=100.0)}),
    ) as mock_fetch:
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])
        await cache.get_live_quotes(
            client, "https://api.dhan.co", "tok", "cid", "/q", ["123", "456"]
        )

    assert mock_fetch.await_count == 2


async def test_fetch_failure_falls_back_to_stale_cache(client: httpx.AsyncClient) -> None:
    cache = QuoteCache()
    quote = QuoteItem(last_price=100.0)
    with patch(
        "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
        AsyncMock(return_value={"123": quote}),
    ):
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])
    # Force the fresh window to have elapsed so the next call re-fetches.
    assert cache._cache is not None
    cache._cache.fetched_at -= 30.0

    with patch(
        "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
        AsyncMock(side_effect=RuntimeError("dhan down")),
    ):
        result = await cache.get_live_quotes(
            client, "https://api.dhan.co", "tok", "cid", "/q", ["123"]
        )

    assert result == {"123": quote}


async def test_fetch_failure_with_no_cache_propagates_error(client: httpx.AsyncClient) -> None:
    cache = QuoteCache()
    with (
        patch(
            "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
            AsyncMock(side_effect=RuntimeError("dhan down")),
        ),
        pytest.raises(RuntimeError, match="dhan down"),
    ):
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])


async def test_empty_fetch_result_raises_without_stale_fallback(client: httpx.AsyncClient) -> None:
    cache = QuoteCache()
    with (
        patch(
            "swing_atlas.repositories.dhan.quote_cache.fetch_quotes",
            AsyncMock(return_value={}),
        ),
        pytest.raises(RuntimeError, match="did not include any NSE_EQ quotes"),
    ):
        await cache.get_live_quotes(client, "https://api.dhan.co", "tok", "cid", "/q", ["123"])
