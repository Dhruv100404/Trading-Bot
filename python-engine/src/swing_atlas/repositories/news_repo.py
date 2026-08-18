"""ClickHouse reads/writes for the news domain (articles, mentions, scores, NSE deals).

Read query shapes mirror engine/src/api/news.rs::{list,large_deals,corporate_events}
exactly -- same joins, same ORDER BY, same filter semantics -- so the frontend sees
identical data. Inserts replace engine/src/api/news.rs::insert_json_each_row.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from swing_atlas.domain.news.models import NewsArticle, NewsMention, NewsScore, NseLargeDeal
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.query_utils import clamp, clean


class NewsRepo:
    def __init__(self, ch: ClickHouseRepo) -> None:
        self._ch = ch

    async def list_news(
        self,
        *,
        symbol: str | None,
        source: str | None,
        min_impact: float | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": clamp(limit if limit is not None else 100, 1, 250)}
        filters = []

        if (clean_symbol := clean(symbol, upper=True)) is not None:
            params["symbol"] = clean_symbol
            filters.append("m.symbol = %(symbol)s")
        if (clean_source := clean(source)) is not None:
            params["source"] = clean_source
            filters.append("a.source = %(source)s")
        if min_impact is not None:
            params["min_impact"] = min_impact
            filters.append("ifNull(s.impact_score, 0) >= %(min_impact)s")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql = f"""
            SELECT
                a.article_id AS article_id,
                a.source,
                a.source_kind,
                a.category,
                a.title,
                a.url,
                a.summary,
                if(isNull(a.published_at), '', formatDateTime(
                    assumeNotNull(a.published_at), '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata'
                )) AS published_at,
                formatDateTime(a.fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at,
                ifNull(m.symbol, '') AS symbol,
                ifNull(m.security_id, '') AS security_id,
                ifNull(m.company_name, '') AS company_name,
                ifNull(m.match_confidence, 0) AS match_confidence,
                ifNull(m.matched_text, '') AS matched_text,
                ifNull(s.sentiment, 0) AS sentiment,
                ifNull(s.impact_score, 0) AS impact_score,
                if(empty(s.direction), 'NEUTRAL', s.direction) AS direction,
                if(empty(s.horizon), 'MARKET', s.horizon) AS horizon,
                ifNull(s.confidence, 0) AS confidence,
                ifNull(s.reason, '') AS reason,
                ifNull(s.model, '') AS model
            FROM (
                SELECT article_id, source, source_kind, category, title, url, summary,
                       published_at, fetched_at
                FROM trading.news_articles FINAL
            ) AS a
            LEFT JOIN (
                SELECT article_id, symbol, security_id, company_name, match_confidence, matched_text
                FROM trading.news_mentions FINAL
            ) AS m ON m.article_id = a.article_id
            LEFT JOIN (
                SELECT article_id, symbol, sentiment, impact_score, direction, horizon,
                       confidence, reason, model
                FROM trading.news_scores FINAL
            ) AS s ON s.article_id = m.article_id AND s.symbol = m.symbol
            {where_clause}
            ORDER BY ifNull(a.published_at, a.fetched_at) DESC, impact_score DESC, a.title ASC
            LIMIT %(limit)s
        """
        return await self._ch.query_rows(sql, params)

    async def large_deals(
        self,
        *,
        symbol: str | None,
        deal_type: str | None,
        side: str | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": clamp(limit if limit is not None else 100, 1, 250)}
        filters = []

        if (clean_symbol := clean(symbol, upper=True)) is not None:
            params["symbol"] = clean_symbol
            filters.append("symbol = %(symbol)s")
        if (clean_deal_type := clean(deal_type, upper=True)) is not None:
            params["deal_type"] = clean_deal_type
            filters.append("deal_type = %(deal_type)s")
        if (clean_side := clean(side, upper=True)) is not None:
            params["side"] = clean_side
            filters.append("side = %(side)s")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql = f"""
            SELECT
                deal_id,
                deal_type,
                toString(deal_date) AS deal_date,
                deal_date_raw,
                symbol,
                security_name,
                client_name,
                side,
                quantity,
                price,
                value_lakh,
                source_url,
                formatDateTime(fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at
            FROM trading.nse_large_deals FINAL
            {where_clause}
            ORDER BY deal_date DESC, value_lakh DESC, symbol ASC
            LIMIT %(limit)s
        """
        return await self._ch.query_rows(sql, params)

    async def corporate_events(
        self,
        *,
        symbol: str | None,
        category: str | None,
        lookback_days: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": clamp(limit if limit is not None else 30, 1, 100),
            "lookback_days": clamp(lookback_days if lookback_days is not None else 730, 1, 2000),
        }
        filters = [
            "event_date >= today() - toIntervalDay(%(lookback_days)s)",
            "event_date <= today()",
        ]
        if (clean_symbol := clean(symbol, upper=True)) is not None:
            params["symbol"] = clean_symbol
            filters.append("symbol = %(symbol)s")
        if (clean_category := clean(category, lower=True)) is not None:
            params["category"] = clean_category
            filters.append("event_category = %(category)s")

        sql = f"""
            SELECT
                event_id,
                source,
                source_event_id,
                symbol,
                company_name,
                formatDateTime(
                    latest_event_time, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata'
                ) AS event_time,
                toString(event_date) AS event_date,
                event_category,
                catalyst_score,
                title,
                summary,
                attachment_url,
                source_url,
                evidence_count
            FROM (
                SELECT
                    argMax(event_id, event_time) AS event_id,
                    arrayStringConcat(groupUniqArray(source), ', ') AS source,
                    argMax(source_event_id, event_time) AS source_event_id,
                    symbol,
                    argMax(company_name, event_time) AS company_name,
                    max(event_time) AS latest_event_time,
                    event_date,
                    event_category,
                    max(catalyst_score) AS catalyst_score,
                    argMax(title, event_time) AS title,
                    argMax(summary, event_time) AS summary,
                    argMax(attachment_url, event_time) AS attachment_url,
                    argMax(source_url, event_time) AS source_url,
                    count() AS evidence_count
                FROM trading.corporate_events FINAL
                WHERE {" AND ".join(filters)}
                GROUP BY symbol, event_date, event_category
            )
            ORDER BY event_date DESC, catalyst_score DESC, latest_event_time DESC, symbol ASC
            LIMIT %(limit)s
        """
        return await self._ch.query_rows(sql, params)

    async def insert_articles(self, articles: list[NewsArticle]) -> None:
        await self._ch.insert_rows("trading.news_articles", [asdict(a) for a in articles])

    async def insert_mentions(self, mentions: list[NewsMention]) -> None:
        await self._ch.insert_rows("trading.news_mentions", [asdict(m) for m in mentions])

    async def insert_scores(self, scores: list[NewsScore]) -> None:
        await self._ch.insert_rows("trading.news_scores", [asdict(s) for s in scores])

    async def insert_large_deals(self, deals: list[NseLargeDeal]) -> None:
        await self._ch.insert_rows("trading.nse_large_deals", [asdict(d) for d in deals])
