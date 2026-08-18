"""Moneycontrol market-activity scraper -- mirrors engine/src/market_activity.rs."""

from __future__ import annotations

import json

import httpx
from bs4 import BeautifulSoup

from swing_atlas.domain.market_activity.models import MarketActivityRow, MarketActivitySource
from swing_atlas.domain.market_activity.parsing import parse_moneycontrol_activity_json


class MoneycontrolNextDataNotFoundError(RuntimeError):
    pass


def parse_moneycontrol_activity_html(
    source: MarketActivitySource, html: str, max_rows: int
) -> list[MarketActivityRow]:
    soup = BeautifulSoup(html, "lxml")
    script_tag = soup.select_one("script#__NEXT_DATA__")
    script_text = script_tag.string if script_tag is not None else None
    if not script_text or not script_text.strip():
        raise MoneycontrolNextDataNotFoundError("Moneycontrol __NEXT_DATA__ script not found")

    payload = json.loads(script_text)
    return parse_moneycontrol_activity_json(source, payload, max_rows)


async def fetch_moneycontrol_source(
    client: httpx.AsyncClient, source: MarketActivitySource, max_rows: int
) -> list[MarketActivityRow]:
    response = await client.get(source.url)
    response.raise_for_status()
    return parse_moneycontrol_activity_html(source, response.text, max_rows)
