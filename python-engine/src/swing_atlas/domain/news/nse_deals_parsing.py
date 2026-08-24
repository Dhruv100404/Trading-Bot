"""Parses NSE bulk/block deal CSV reports -- mirrors engine/src/news.rs's
parse_nse_daily_report/parse_nse_date. Handles NSE's historically inconsistent
column headers (trailing-space variants) and date formats.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime

from swing_atlas.domain.hashing import hash_id
from swing_atlas.domain.news.models import NseLargeDeal
from swing_atlas.domain.numeric import parse_number, round2

# Hardcoded regardless of which endpoint actually produced the CSV (primary
# historicalOR API or the daily bulk.csv/block.csv fallback) -- matches the Rust
# original, which does the same in parse_nse_daily_report.
NSE_ARCHIVES_URL = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"

_MONTH_NAMES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# canonical field -> accepted (already-trimmed) CSV header names
_FIELD_ALIASES: dict[str, list[str]] = {
    "date": ["Date"],
    "symbol": ["Symbol"],
    "security_name": ["Security Name"],
    "client_name": ["Client Name"],
    "side": ["Buy/Sell", "Buy / Sell"],
    "quantity": ["Quantity Traded"],
    "price": ["Trade Price / Wght. Avg. Price"],
}


def month_number(value: str) -> int | None:
    return _MONTH_NAMES.get(value.strip().upper()[:3])


def parse_nse_date(value: str) -> date | None:
    cleaned = value.strip().replace("/", "-")
    if not cleaned:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass

    parts = cleaned.split("-")
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
        year = int(parts[2])
    except ValueError:
        return None
    month = month_number(parts[1])
    if month is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _map_headers(fieldnames: Sequence[str]) -> dict[str, str]:
    """canonical field -> the actual raw (untrimmed) CSV header name."""
    mapping: dict[str, str] = {}
    for raw_header in fieldnames:
        trimmed = raw_header.strip()
        for canonical, aliases in _FIELD_ALIASES.items():
            if trimmed in aliases:
                mapping[canonical] = raw_header
    return mapping


def parse_nse_daily_report(body: str, deal_type: str, fetched_at: str) -> list[NseLargeDeal]:
    body = body.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(body))
    header_map = _map_headers(reader.fieldnames or [])

    def field(record: dict[str, str | None], name: str) -> str:
        raw_header = header_map.get(name)
        if raw_header is None:
            return ""
        return (record.get(raw_header) or "").strip()

    out: list[NseLargeDeal] = []
    for record in reader:
        symbol = field(record, "symbol").upper()
        if not symbol or symbol == "NO RECORDS":
            continue

        deal_date_raw = field(record, "date")
        parsed_date = parse_nse_date(deal_date_raw)
        if parsed_date is None:
            continue

        quantity = parse_number(field(record, "quantity")) or 0.0
        price = parse_number(field(record, "price")) or 0.0
        side = field(record, "side").upper()
        client_name = field(record, "client_name")
        quantity_key = f"{quantity:.0f}"
        price_key = f"{price:.4f}"
        deal_id = hash_id(
            [deal_type, deal_date_raw, symbol, client_name, side, quantity_key, price_key]
        )

        out.append(
            NseLargeDeal(
                deal_id=deal_id,
                deal_type=deal_type,
                deal_date=parsed_date.strftime("%Y-%m-%d"),
                deal_date_raw=deal_date_raw,
                symbol=symbol,
                security_name=field(record, "security_name"),
                client_name=client_name,
                side=side,
                quantity=quantity,
                price=price,
                value_lakh=round2(quantity * price / 100_000.0),
                source_url=NSE_ARCHIVES_URL,
                fetched_at=fetched_at,
            )
        )
    return out
