"""Thin async ClickHouse wrapper.

Replaces both the typed `clickhouse` crate client and the raw param-binding
HTTP helper in engine/src/api/ch_http.rs -- clickhouse-connect's AsyncClient
covers both typed row access and ad-hoc parameterized SQL in one client.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

logger = logging.getLogger(__name__)

_WAIT_ATTEMPTS = 30
_WAIT_INTERVAL_SECS = 5

# clickhouse-connect binds `parameters` by running Python `%`-substitution over the
# whole query text, so a literal '%' anywhere in the SQL (e.g. formatDateTime's
# '%Y-%m-%d' format string) collides with it unless escaped as '%%'. Match any '%'
# NOT immediately followed by '(' -- i.e. everything except our own `%(name)s`
# placeholders -- and double it, so callers can write natural ClickHouse SQL.
_UNESCAPED_PERCENT_RE = re.compile(r"%(?!\()")


class ClickHouseNotReadyError(RuntimeError):
    pass


class ClickHouseRepo:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    @classmethod
    async def connect(cls, url: str, database: str = "trading") -> ClickHouseRepo:
        """Connect, polling until ClickHouse answers -- mirrors main.rs's wait_for_clickhouse
        (~2.5 min total budget) so a ClickHouse cold-start doesn't hard-crash this service."""
        parsed = urlparse(url)
        client = await clickhouse_connect.get_async_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or 8123,
            database=database,
        )
        repo = cls(client)
        for attempt in range(1, _WAIT_ATTEMPTS + 1):
            try:
                await repo.ping()
                return repo
            except Exception as exc:  # noqa: BLE001 -- broad on purpose, mirrors the Rust retry loop
                logger.warning(
                    "ClickHouse not ready (attempt %d/%d): %s", attempt, _WAIT_ATTEMPTS, exc
                )
                if attempt < _WAIT_ATTEMPTS:
                    await asyncio.sleep(_WAIT_INTERVAL_SECS)
        raise ClickHouseNotReadyError(
            f"ClickHouse did not become healthy after {_WAIT_ATTEMPTS} attempts"
        )

    async def ping(self) -> bool:
        return await self._client.ping()

    async def query_rows(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a parameterized query, return rows as a list of dicts.

        Use Python `%(name)s` placeholders in `sql` and pass values via `parameters`.
        clickhouse-connect binds these client-side (escapes/quotes each value into a SQL
        literal, then substitutes via `%`) rather than ClickHouse's server-side `{name:Type}`
        binding that engine/src/api/ch_http.rs used -- different mechanism, same
        injection-safety guarantee: never string-interpolate a value into `sql` yourself.
        """
        safe_sql = _UNESCAPED_PERCENT_RE.sub("%%", sql) if parameters else sql
        result = await self._client.query(safe_sql, parameters=parameters or {})
        columns = result.column_names
        return [dict(zip(columns, row, strict=True)) for row in result.result_rows]

    async def command(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        """DDL / non-query statements (CREATE TABLE, ALTER, INSERT ... VALUES ...).

        Same `%(name)s` parameter binding and auto-escaping as query_rows -- see
        its docstring for why the escaping is necessary."""
        safe_sql = _UNESCAPED_PERCENT_RE.sub("%%", sql) if parameters else sql
        await self._client.command(safe_sql, parameters=parameters or {})

    async def insert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert dataclass-derived row dicts. Replaces the raw HTTP
        `INSERT ... FORMAT JSONEachRow` POST engine/src/api/{news,market_activity}.rs
        build by hand -- clickhouse-connect's native insert() covers the same thing."""
        if not rows:
            return
        column_names = list(rows[0].keys())
        data = [[row[col] for col in column_names] for row in rows]
        await self._client.insert(table, data, column_names=column_names)

    async def close(self) -> None:
        await self._client.close()
