"""Kite Connect REST calls used by the positions endpoint.

Mirrors engine/src/api/positions.rs's Zerodha branch, including the
Dhan-compatible field normalization the UI expects.
"""

from __future__ import annotations

from typing import Any

import httpx


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_kite_positions(net: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions = []
    for p in net:
        buy_qty = int(p.get("buy_quantity") or 0)
        sell_qty = int(p.get("sell_quantity") or 0)
        net_qty = int(p.get("quantity") or 0)
        buy_avg = _as_float(p.get("buy_price"))
        sell_avg = _as_float(p.get("sell_price"))
        pnl = _as_float(p.get("pnl"))
        unrealised = _as_float(p.get("unrealised"))
        realised = _as_float(p.get("realised"))

        positions.append(
            {
                "tradingSymbol": p.get("tradingsymbol") or "",
                "securityId": str(int(p.get("instrument_token") or 0)),
                "positionType": "LONG" if net_qty > 0 else "SHORT" if net_qty < 0 else "CLOSED",
                "exchangeSegment": p.get("exchange") or "NSE",
                "productType": p.get("product") or "MIS",
                "buyAvg": buy_avg,
                "buyQty": buy_qty,
                "sellAvg": sell_avg,
                "sellQty": sell_qty,
                "netQty": net_qty,
                "realizedProfit": pnl if net_qty == 0 else realised,
                "unrealizedProfit": 0.0 if net_qty == 0 else unrealised,
                "costPrice": buy_avg if buy_avg > 0.0 else sell_avg,
                "dayBuyValue": _as_float(p.get("buy_value")),
                "daySellValue": _as_float(p.get("sell_value")),
            }
        )
    return positions


async def fetch_positions(
    client: httpx.AsyncClient, api_key: str, access_token: str
) -> tuple[list[dict[str, Any]], str | None]:
    headers = {"Authorization": f"token {api_key}:{access_token}", "X-Kite-Version": "3"}
    try:
        response = await client.get("https://api.kite.trade/portfolio/positions", headers=headers)
    except httpx.HTTPError as exc:
        return [], f"Zerodha positions: {exc}"

    try:
        body = response.json()
    except ValueError:
        body = {}

    if body.get("status") == "error":
        message = body.get("message") or "Unknown error"
        return [], f"Zerodha positions: {message} (HTTP {response.status_code})"

    net = (body.get("data") or {}).get("net") or []
    return normalize_kite_positions(net), None


async def fetch_margins(
    client: httpx.AsyncClient, api_key: str, access_token: str
) -> tuple[dict[str, Any], str | None]:
    headers = {"Authorization": f"token {api_key}:{access_token}", "X-Kite-Version": "3"}
    try:
        response = await client.get("https://api.kite.trade/user/margins/equity", headers=headers)
    except httpx.HTTPError as exc:
        return {}, f"Zerodha margins: {exc}"

    try:
        body = response.json()
    except ValueError:
        body = {}

    if body.get("status") == "error":
        message = body.get("message") or "Unknown error"
        return {}, f"Zerodha margins: {message}"

    data = body.get("data") or {}
    balance = {
        "availabelBalance": _as_float(data.get("net")),
        "utilizedAmount": _as_float((data.get("utilised") or {}).get("debits")),
    }
    return balance, None
