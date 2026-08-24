"""NSE bulk/block deal fetching -- mirrors engine/src/news.rs::fetch_nse_large_deals.

NSE actively blocks bots, so this does a cookie warm-up GET before the real data
request. Uses one shared httpx.AsyncClient across both calls -- unlike Rust's
reqwest (which needs Set-Cookie captured and replayed manually), httpx's client
keeps its own cookie jar automatically, so the warm-up cookies are attached to the
follow-up requests for free as long as the same client instance is reused.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx

from swing_atlas.domain.news.models import NseLargeDeal
from swing_atlas.domain.news.nse_deals_parsing import NSE_ARCHIVES_URL, parse_nse_daily_report
from swing_atlas.domain.time_utils import now_ist, now_sql

logger = logging.getLogger(__name__)

NSE_BULK_BLOCK_HISTORY_API = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
NSE_BULK_REPORT_CSV = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
NSE_BLOCK_REPORT_CSV = "https://nsearchives.nseindia.com/content/equities/block.csv"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Referer": NSE_ARCHIVES_URL,
}
_CSV_ACCEPT_HEADER = {"Accept": "text/csv,*/*;q=0.8"}


class NseWarmupFailedError(RuntimeError):
    pass


class NseFetchFailedError(RuntimeError):
    pass


async def _fetch_nse_historical_deal_csv(
    client: httpx.AsyncClient,
    deal_type: str,
    option_type: str,
    from_: str,
    to: str,
    fetched_at: str,
) -> list[NseLargeDeal]:
    response = await client.get(
        NSE_BULK_BLOCK_HISTORY_API,
        headers=_CSV_ACCEPT_HEADER,
        params={"optionType": option_type, "from": from_, "to": to, "csv": "true"},
    )
    response.raise_for_status()
    return parse_nse_daily_report(response.text, deal_type, fetched_at)


async def _fetch_nse_daily_report(
    client: httpx.AsyncClient, deal_type: str, csv_url: str, fetched_at: str
) -> list[NseLargeDeal]:
    response = await client.get(csv_url, headers=_CSV_ACCEPT_HEADER)
    response.raise_for_status()
    return parse_nse_daily_report(response.text, deal_type, fetched_at)


async def fetch_nse_large_deals(lookback_days: int) -> list[NseLargeDeal]:
    lookback_days = max(0, min(30, lookback_days))
    today = now_ist().date()
    from_date = today - timedelta(days=lookback_days)
    from_s = from_date.strftime("%d-%m-%Y")
    to_s = today.strftime("%d-%m-%Y")
    fetched_at = now_sql()

    async with httpx.AsyncClient(headers=_BROWSER_HEADERS, timeout=20.0) as client:
        warmup = await client.get(NSE_ARCHIVES_URL)
        if warmup.is_error:
            raise NseWarmupFailedError(f"NSE cookie warm-up returned {warmup.status_code}")

        rows: list[NseLargeDeal] = []
        historical_errors: list[str] = []
        for deal_type, option_type in (("BULK", "bulk_deals"), ("BLOCK", "block_deals")):
            try:
                rows.extend(
                    await _fetch_nse_historical_deal_csv(
                        client, deal_type, option_type, from_s, to_s, fetched_at
                    )
                )
            except (httpx.HTTPError, ValueError) as exc:
                historical_errors.append(f"{deal_type}: {exc}")

        # Keep the daily archive CSVs as an emergency fallback -- the primary
        # historicalOR endpoint is requested with csv=true because its JSON view
        # is capped, while the CSV contains the complete selected date range.
        if not rows or historical_errors:
            fallback_rows: list[NseLargeDeal] = []
            fallback_errors: list[str] = []
            for deal_type, csv_url in (
                ("BULK", NSE_BULK_REPORT_CSV),
                ("BLOCK", NSE_BLOCK_REPORT_CSV),
            ):
                try:
                    fallback_rows.extend(
                        await _fetch_nse_daily_report(client, deal_type, csv_url, fetched_at)
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    fallback_errors.append(f"{deal_type}: {exc}")

            if fallback_rows:
                logger.info(
                    "[NEWS] NSE historical endpoint unavailable; loaded %d rows from "
                    "official daily CSV reports",
                    len(fallback_rows),
                )
                rows.extend(fallback_rows)
            elif not rows and fallback_errors:
                raise NseFetchFailedError(
                    f"NSE historical fetch failed ({'; '.join(historical_errors)}); "
                    f"daily report fallback failed ({'; '.join(fallback_errors)})"
                )

    seen: set[str] = set()
    deduped: list[NseLargeDeal] = []
    for deal in rows:
        if deal.deal_id in seen:
            continue
        seen.add(deal.deal_id)
        deduped.append(deal)
    return deduped
