"""Extracts market-activity rows from Moneycontrol's __NEXT_DATA__ JSON blob.

Mirrors engine/src/market_activity.rs. This scrape is coupled to Moneycontrol's
current Next.js props shape -- there's no documented API, so `looks_like_activity_row`
and the many-alias field lookups below must be ported verbatim; if Moneycontrol
changes their frontend, this breaks silently (returns nothing, not an error).
"""

from __future__ import annotations

import math
from typing import Any

from swing_atlas.domain.hashing import hash_id
from swing_atlas.domain.market_activity.models import MarketActivityRow, MarketActivitySource
from swing_atlas.domain.numeric import parse_number, round2, round_half_away_from_zero
from swing_atlas.domain.time_utils import now_ist, now_sql


def collect_json_object_rows(value: Any, rows: list[dict[str, Any]]) -> None:
    """Walks the whole JSON tree, collecting every nested object encountered."""
    if isinstance(value, list):
        for item in value:
            collect_json_object_rows(item, rows)
    elif isinstance(value, dict):
        rows.append(value)
        for child in value.values():
            collect_json_object_rows(child, rows)


def json_field(row: dict[str, Any], keys: list[str]) -> Any | None:
    for key in keys:
        if key in row:
            return row[key]
    for actual_key, value in row.items():
        if any(actual_key.lower() == key.lower() for key in keys):
            return value
    return None


def json_string(row: dict[str, Any], keys: list[str]) -> str | None:
    value = json_field(row, keys)
    if isinstance(value, str):
        raw = value
    elif isinstance(value, bool):
        raw = "true" if value else "false"
    elif isinstance(value, (int, float)):
        raw = str(value)
    else:
        return None
    cleaned = " ".join(raw.replace("\xa0", " ").split())
    return cleaned if cleaned and cleaned != "-" else None


def json_f64(row: dict[str, Any], keys: list[str]) -> float | None:
    value = json_field(row, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return parse_number(value)
    return None


def json_u64(row: dict[str, Any], keys: list[str]) -> int | None:
    value = json_f64(row, keys)
    if value is None or not math.isfinite(value) or value < 0.0:
        return None
    return round_half_away_from_zero(value)


def looks_like_activity_row(row: dict[str, Any]) -> bool:
    return (
        json_field(row, ["stockName"]) is not None
        and json_field(row, ["symbol"]) is not None
        and (
            json_field(row, ["volume"]) is not None
            or json_field(row, ["volMultiplier"]) is not None
            or json_field(row, ["value"]) is not None
        )
    )


def parse_moneycontrol_activity_json(
    source: MarketActivitySource, payload: Any, max_rows: int
) -> list[MarketActivityRow]:
    snapshot_at = now_sql()
    trading_date = now_ist().date().isoformat()

    row_values: list[dict[str, Any]] = []
    collect_json_object_rows(payload, row_values)

    seen_symbols: set[str] = set()
    out: list[MarketActivityRow] = []
    for row in row_values:
        symbol = (json_string(row, ["symbol", "SYMBOL", "nseSymbol"]) or "").strip().upper()
        stock_name = json_string(row, ["stockName", "companyName", "name"]) or ""
        if not symbol or not stock_name or not looks_like_activity_row(row):
            continue
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)

        rank = len(out) + 1
        moneycontrol_id = json_string(row, ["scId", "sc_id", "id"]) or ""
        slug = json_string(row, ["slug"]) or ""
        share_url = json_string(row, ["shareUrl", "share_url"]) or ""
        volume = json_u64(row, ["volume", "vol"]) or 0
        avg_volume = json_u64(row, ["avgVol", "avgVolume", "averageVolume"]) or 0
        volume_multiplier = json_f64(row, ["volMultiplier", "volumeMultiplier", "vol_mult"]) or 0.0
        volume_change_pct = json_f64(row, ["volChg", "volumeChange", "volumeChangePct"]) or 0.0

        row_id = hash_id(
            [source.source, source.metric_type, source.exchange, snapshot_at, str(rank), symbol]
        )

        out.append(
            MarketActivityRow(
                row_id=row_id,
                snapshot_at=snapshot_at,
                trading_date=trading_date,
                source=source.source,
                metric_type=source.metric_type,
                exchange=(json_string(row, ["exchange", "ex"]) or source.exchange).upper(),
                index_name=source.index_name,
                rank=rank,
                symbol=symbol,
                stock_name=stock_name,
                moneycontrol_id=moneycontrol_id,
                slug=slug,
                price=round2(json_f64(row, ["currentPrice", "ltp", "price"]) or 0.0),
                change_abs=round2(json_f64(row, ["priceChange", "change", "netChange"]) or 0.0),
                change_pct=round2(json_f64(row, ["perChange", "currPerChange", "pChange"]) or 0.0),
                day_high=round2(json_f64(row, ["high", "dayHigh"]) or 0.0),
                day_low=round2(json_f64(row, ["low", "dayLow"]) or 0.0),
                open=round2(json_f64(row, ["open"]) or 0.0),
                prev_close=round2(json_f64(row, ["prevClose", "previousClose"]) or 0.0),
                volume=volume,
                avg_volume=avg_volume,
                volume_multiplier=round2(volume_multiplier),
                volume_change_pct=round2(volume_change_pct),
                value_cr=round2(json_f64(row, ["value", "turnover", "tradedValue"]) or 0.0),
                vwap=round2(json_f64(row, ["vwap"]) or 0.0),
                direction=json_string(row, ["direction"]) or "",
                mcap_cr=round2(json_f64(row, ["mcap", "marketCap"]) or 0.0),
                month_return_pct=round2(json_f64(row, ["monthReturn"]) or 0.0),
                month3_return_pct=round2(json_f64(row, ["month3Return"]) or 0.0),
                share_url=share_url,
                source_url=source.url,
                fetched_at=snapshot_at,
            )
        )

        if len(out) >= max_rows:
            break

    return out
