"""Dhan quote REST fetch -- mirrors engine/src/dhan/market_data.rs::fetch_quotes.

Retries up to 2 times (3 attempts total) on 429/5xx with exponential backoff,
matching the Rust original -- this feeds live paper-trade price enrichment and
the swing dashboard, both of which degrade to cached/historical prices on
failure rather than surfacing an error to the browser.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_REQUEST_TIMEOUT_SECS = 15.0


@dataclass(frozen=True, slots=True)
class QuoteOhlc:
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0  # previous day's EOD closing price


@dataclass(frozen=True, slots=True)
class QuoteItem:
    last_price: float = 0.0
    ohlc: QuoteOhlc = field(default_factory=QuoteOhlc)
    volume: int = 0


async def fetch_quotes(
    client: httpx.AsyncClient,
    base_url: str,
    access_token: str,
    client_id: str,
    security_ids: list[str],
    endpoint: str,
) -> dict[str, QuoteItem]:
    """Fetch quotes for a batch of security IDs (max 1000 per call). Dhan
    requires security IDs sent as integers, not strings."""
    ids = [int(sid) for sid in security_ids if sid.isdigit()]
    url = f"{base_url.rstrip('/')}{endpoint}"
    headers = {
        "access-token": access_token,
        "client-id": client_id,
        "Content-Type": "application/json",
    }

    last_err = ""
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            backoff = 2**attempt
            logger.warning("[QUOTE] Retry %d/2 after %ds backoff", attempt, backoff)
            await asyncio.sleep(backoff)

        try:
            response = await client.post(
                url, json={"NSE_EQ": ids}, headers=headers, timeout=_REQUEST_TIMEOUT_SECS
            )
        except httpx.HTTPError as exc:
            last_err = f"request error: {exc}"
            continue

        if response.status_code == 429:
            logger.warning("[QUOTE] 429 rate limited (attempt %d)", attempt + 1)
            last_err = "429 rate limited"
            continue
        if response.status_code >= 500:
            logger.warning(
                "[QUOTE] Server error %d (attempt %d)", response.status_code, attempt + 1
            )
            last_err = f"server error {response.status_code}"
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            preview = response.text[:300]
            raise RuntimeError(f"JSON parse error: {exc} | body: {preview}") from exc

        status = payload.get("status", "unknown")
        if status != "success":
            message = str(payload.get("data", ""))[:200]
            raise RuntimeError(f"Dhan API status={status} | {message}")

        return _parse_quote_segments(payload.get("data") or {})

    raise RuntimeError(f"fetch_quotes failed after {_MAX_ATTEMPTS} attempts: {last_err}")


def _parse_quote_segments(segments: dict[str, Any]) -> dict[str, QuoteItem]:
    result: dict[str, QuoteItem] = {}
    for items in segments.values():
        if not isinstance(items, dict):
            continue
        for security_id, item in items.items():
            try:
                ohlc = item.get("ohlc") or {}
                result[security_id] = QuoteItem(
                    last_price=float(item.get("last_price", 0.0)),
                    ohlc=QuoteOhlc(
                        open=float(ohlc.get("open", 0.0)),
                        high=float(ohlc.get("high", 0.0)),
                        low=float(ohlc.get("low", 0.0)),
                        close=float(ohlc.get("close", 0.0)),
                    ),
                    volume=int(item.get("volume", 0)),
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Skipping sec_id=%s: parse error: %s", security_id, exc)
    return result


@dataclass(frozen=True, slots=True)
class IntradayResponse:
    """1-minute intraday candles from Dhan's charts API, in Dhan's own
    parallel-array wire format (not an array of candle objects)."""

    open: list[float] = field(default_factory=list)
    high: list[float] = field(default_factory=list)
    low: list[float] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    timestamp: list[float] = field(default_factory=list)
    volume: list[float] = field(default_factory=list)


async def fetch_intraday_candles(
    client: httpx.AsyncClient,
    base_url: str,
    access_token: str,
    client_id: str,
    security_id: str,
    from_date: str,
    to_date: str,
) -> IntradayResponse:
    """Fetch 1-minute intraday candles for a security between two dates
    ("YYYY-MM-DD HH:MM:SS"). Retries up to 2 times (3 attempts total) on
    429/400/5xx with exponential backoff, matching the Rust original."""
    url = f"{base_url.rstrip('/')}/charts/intraday"
    headers = {
        "access-token": access_token,
        "client-id": client_id,
        "Content-Type": "application/json",
    }
    body = {
        "securityId": security_id,
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "interval": "1",
        "oi": False,
        "fromDate": from_date,
        "toDate": to_date,
    }

    last_err = ""
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            await asyncio.sleep(2**attempt)

        try:
            response = await client.post(
                url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT_SECS
            )
        except httpx.HTTPError as exc:
            last_err = f"request error: {exc}"
            continue

        preview = response.text[:300]
        if response.status_code in (429, 400):
            logger.warning(
                "[INTRADAY] HTTP %d (attempt %d): %s", response.status_code, attempt + 1, preview
            )
            last_err = f"HTTP {response.status_code}: {preview}"
            continue
        if response.status_code >= 500:
            last_err = f"server error {response.status_code}: {preview}"
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            last_err = f"json error: {exc} | body: {preview}"
            continue

        return IntradayResponse(
            open=[float(v) for v in payload.get("open", [])],
            high=[float(v) for v in payload.get("high", [])],
            low=[float(v) for v in payload.get("low", [])],
            close=[float(v) for v in payload.get("close", [])],
            timestamp=[float(v) for v in payload.get("timestamp", [])],
            volume=[float(v) for v in payload.get("volume", [])],
        )

    raise RuntimeError(f"fetch_intraday_candles failed after {_MAX_ATTEMPTS} attempts: {last_err}")
