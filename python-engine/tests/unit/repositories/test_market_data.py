"""Tests for the Dhan quote REST fetch -- mirrors
engine/src/dhan/market_data.rs::fetch_quotes's retry/parse behavior.
"""

from unittest.mock import AsyncMock, patch

import httpx
import respx

from swing_atlas.repositories.dhan.market_data import fetch_quotes


@respx.mock
async def test_parses_successful_quote_response() -> None:
    respx.post("https://api.dhan.co/marketfeed/quote").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ": {
                        "1333": {
                            "last_price": 755.5,
                            "volume": 12345,
                            "ohlc": {"open": 750.0, "high": 760.0, "low": 748.0, "close": 752.0},
                        }
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        quotes = await fetch_quotes(
            client, "https://api.dhan.co", "tok", "cid", ["1333"], "/marketfeed/quote"
        )

    assert quotes["1333"].last_price == 755.5
    assert quotes["1333"].volume == 12345
    assert quotes["1333"].ohlc.close == 752.0


@respx.mock
async def test_retries_on_429_then_succeeds() -> None:
    route = respx.post("https://api.dhan.co/marketfeed/quote")
    route.side_effect = [
        httpx.Response(429, json={}),
        httpx.Response(
            200, json={"status": "success", "data": {"NSE_EQ": {"1333": {"last_price": 100.0}}}}
        ),
    ]

    with patch("asyncio.sleep", AsyncMock()):
        async with httpx.AsyncClient() as client:
            quotes = await fetch_quotes(
                client, "https://api.dhan.co", "tok", "cid", ["1333"], "/marketfeed/quote"
            )

    assert quotes["1333"].last_price == 100.0
    assert route.call_count == 2


@respx.mock
async def test_non_success_status_raises() -> None:
    respx.post("https://api.dhan.co/marketfeed/quote").mock(
        return_value=httpx.Response(200, json={"status": "failure", "data": "bad token"})
    )

    async with httpx.AsyncClient() as client:
        try:
            await fetch_quotes(
                client, "https://api.dhan.co", "tok", "cid", ["1333"], "/marketfeed/quote"
            )
        except RuntimeError as exc:
            assert "Dhan API status=failure" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
