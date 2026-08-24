"""Reads from trading.accounts -- multi-broker credential storage."""

from __future__ import annotations

from dataclasses import dataclass

from swing_atlas.repositories.clickhouse import ClickHouseRepo


@dataclass(frozen=True, slots=True)
class AccountRow:
    name: str
    client_id: str
    access_token: str
    broker: str
    api_key: str
    enabled: bool


class AccountsRepo:
    def __init__(self, ch: ClickHouseRepo) -> None:
        self._ch = ch

    async def latest_enabled_dhan_account(self) -> tuple[str, str] | None:
        """Returns (client_id, access_token) for the most recently inserted enabled Dhan account."""
        rows = await self._ch.query_rows(
            "SELECT client_id, access_token FROM trading.accounts "
            "WHERE enabled = 1 AND broker = 'DHAN' ORDER BY inserted_at DESC LIMIT 1"
        )
        if not rows:
            return None
        return rows[0]["client_id"], rows[0]["access_token"]

    async def all_accounts_newest_first(self) -> list[AccountRow]:
        rows = await self._ch.query_rows(
            "SELECT name, client_id, access_token, broker, api_key, enabled "
            "FROM trading.accounts ORDER BY inserted_at DESC"
        )
        return [
            AccountRow(
                name=r["name"],
                client_id=r["client_id"],
                access_token=r["access_token"],
                broker=r["broker"],
                api_key=r["api_key"],
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]
