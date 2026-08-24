"""Recent, symbol-level news confluence for the swing dashboard -- mirrors
engine/src/api/swing.rs::load_recent_news_confluence.
"""

from __future__ import annotations

from swing_atlas.domain.swing.news_confluence import NewsConfluenceRow, decode_hex_text
from swing_atlas.repositories.clickhouse import ClickHouseRepo


async def load_recent_news_confluence(
    ch: ClickHouseRepo, symbols: list[str]
) -> dict[str, NewsConfluenceRow]:
    if not symbols:
        return {}
    query = """
        SELECT s.symbol AS symbol,
            count() AS article_count,
            countIf(s.direction = 'BULLISH' AND s.sentiment >= 0.18 AND s.impact_score >= 1.2) AS bullish_articles,
            countIf(s.direction = 'BEARISH' AND s.sentiment <= -0.18 AND s.impact_score >= 1.2) AS bearish_articles,
            avg(s.sentiment) AS avg_sentiment,
            toFloat64(max(s.impact_score)) AS max_impact,
            /* Feed text is external input. Hex encodes it before the ClickHouse
               client decodes rows, so one malformed source byte cannot hide
               news evidence for every scanner symbol. */
            argMax(hex(toValidUTF8(s.reason)), s.inserted_at) AS latest_reason,
            argMax(hex(toValidUTF8(a.title)), s.inserted_at) AS latest_headline,
            argMax(hex(toValidUTF8(a.source)), s.inserted_at) AS latest_source,
            argMax(hex(toValidUTF8(a.url)), s.inserted_at) AS latest_url
        FROM (SELECT article_id, symbol, sentiment, impact_score, direction, reason, inserted_at
              FROM trading.news_scores FINAL) AS s
        LEFT JOIN (SELECT article_id, source, title, url
                   FROM trading.news_articles FINAL) AS a
          ON a.article_id = s.article_id
        WHERE s.inserted_at >= now() - INTERVAL 72 HOUR AND s.symbol IN %(symbols)s
        GROUP BY s.symbol
    """
    rows = await ch.query_rows(query, parameters={"symbols": symbols})

    result: dict[str, NewsConfluenceRow] = {}
    for row in rows:
        confluence_row = NewsConfluenceRow(
            symbol=row["symbol"],
            article_count=int(row["article_count"]),
            bullish_articles=int(row["bullish_articles"]),
            bearish_articles=int(row["bearish_articles"]),
            avg_sentiment=row["avg_sentiment"],
            max_impact=row["max_impact"],
            latest_reason=decode_hex_text(row["latest_reason"]),
            latest_headline=decode_hex_text(row["latest_headline"]),
            latest_source=decode_hex_text(row["latest_source"]),
            latest_url=decode_hex_text(row["latest_url"]),
        )
        result[confluence_row.symbol] = confluence_row
    return result
