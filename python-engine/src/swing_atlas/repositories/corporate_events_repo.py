"""Corporate-events backfill -- mirrors engine/src/api/news.rs::backfill_corporate_events_if_empty.

Imports the locally cached official NSE corporate-event history once, from a
parquet file ClickHouse reads directly off disk (mounted read-only into its
user_files dir). Live refresh is independent, so a missing research cache never
prevents the service from starting -- this only ever runs if the table is empty.
"""

from __future__ import annotations

from swing_atlas.repositories.clickhouse import ClickHouseRepo

_BACKFILL_SQL = """
INSERT INTO trading.corporate_events
(event_id, source, source_event_id, symbol, company_name, event_time,
 event_date, event_category, catalyst_score, title, summary,
 attachment_url, source_url)
SELECT
   lower(hex(MD5(concat(
       ifNull(source, ''), '|', ifNull(source_event_id, ''), '|',
       ifNull(symbol, ''), '|', toString(event_date), '|',
       ifNull(event_category, ''), '|', ifNull(title, '')
   )))) AS event_id,
   ifNull(source, ''),
   ifNull(source_event_id, ''),
   upper(ifNull(symbol, '')),
   ifNull(company_name, ''),
   coalesce(
       subtractMinutes(toDateTime64(event_time, 3, 'Asia/Kolkata'), 330),
       toDateTime64(event_date, 3, 'Asia/Kolkata')
   ),
   event_date,
   ifNull(event_category, 'other'),
   toUInt8(greatest(0, least(100, catalyst_score))),
   ifNull(title, ''),
   ifNull(summary, ''),
   ifNull(attachment_url, ''),
   ifNull(source_url, '')
FROM file('events/corporate_catalysts.parquet', Parquet)
WHERE event_date >= toDate('2021-01-01')
  AND event_date <= today()
  AND catalyst_score >= 70
  AND source IN (
       'nse_announcements', 'nse_financial_results',
       'nse_integrated_financials'
  )
  AND event_category IN (
       'financial_results', 'big_order',
       'merger_acquisition', 'policy_regulatory'
  )
  AND upper(ifNull(symbol, '')) IN (
       SELECT symbol FROM trading.watchlist FINAL WHERE symbol != ''
  )
"""


async def backfill_corporate_events_if_empty(ch: ClickHouseRepo) -> int:
    existing = await ch.query_rows("SELECT count() AS n FROM trading.corporate_events")
    if existing[0]["n"] > 0:
        return 0

    await ch.command(_BACKFILL_SQL)

    after = await ch.query_rows("SELECT count() AS n FROM trading.corporate_events")
    count: int = after[0]["n"]
    return count
