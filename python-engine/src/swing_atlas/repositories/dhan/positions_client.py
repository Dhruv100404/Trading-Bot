"""Dhan positions/fund-limit REST calls used by the positions endpoint.

Uses per-account credentials passed as arguments rather than a fixed client-wide
Config, matching engine/src/api/positions.rs, which bypasses the general DhanClient
(dhan/client.rs) entirely and makes its own raw calls with per-account headers.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_SECS = 10.0


async def fetch_positions(
    client: httpx.AsyncClient, access_token: str, client_id: str
) -> tuple[Any, str | None]:
    return await _get(
        client, "https://api.dhan.co/v2/positions", access_token, client_id, "Dhan positions", []
    )


async def fetch_fund_limit(
    client: httpx.AsyncClient, access_token: str, client_id: str
) -> tuple[Any, str | None]:
    return await _get(
        client, "https://api.dhan.co/v2/fundlimit", access_token, client_id, "Dhan fund limit", {}
    )


async def _get(
    client: httpx.AsyncClient,
    url: str,
    access_token: str,
    client_id: str,
    label: str,
    default: Any,
) -> tuple[Any, str | None]:
    headers = {"access-token": access_token, "client-id": client_id}
    try:
        response = await client.get(url, headers=headers, timeout=TIMEOUT_SECS)
    except httpx.HTTPError as exc:
        return default, f"{label}: {exc}"

    try:
        body = response.json()
    except ValueError:
        body = default

    if response.status_code >= 400:
        message = "API error"
        if isinstance(body, dict):
            message = body.get("remarks") or body.get("message") or message
        return default, f"{label}: {message} (HTTP {response.status_code})"

    return body, None
