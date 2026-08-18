"""Multi-broker live positions/margin aggregation -- mirrors engine/src/api/positions.rs.

Never fails the whole response: each account's fetch errors are captured independently.
"""

from __future__ import annotations

from typing import Any

import httpx

from swing_atlas.config import Settings
from swing_atlas.repositories.accounts_repo import AccountRow, AccountsRepo
from swing_atlas.repositories.dhan import positions_client as dhan_positions
from swing_atlas.repositories.zerodha import rest_client as zerodha


def _combine_errors(a: str | None, b: str | None) -> str | None:
    if a and b:
        return f"{a}; {b}"
    return a or b


class PositionsService:
    def __init__(self, settings: Settings, accounts_repo: AccountsRepo) -> None:
        self._settings = settings
        self._accounts_repo = accounts_repo

    async def list_accounts(self) -> list[dict[str, Any]]:
        accounts = await self._accounts_repo.all_accounts_newest_first()
        by_client_id: dict[str, AccountRow] = {}
        for account in accounts:
            if not account.enabled:
                continue
            by_client_id.setdefault(account.client_id, account)

        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for account in by_client_id.values():
                if account.broker == "ZERODHA":
                    results.append(await self._fetch_zerodha(client, account))
                else:
                    results.append(await self._fetch_dhan(client, account))
        return results

    async def _fetch_zerodha(
        self, client: httpx.AsyncClient, account: AccountRow
    ) -> dict[str, Any]:
        positions, pos_error = await zerodha.fetch_positions(
            client, account.api_key, account.access_token
        )
        balance, bal_error = await zerodha.fetch_margins(
            client, account.api_key, account.access_token
        )
        return {
            "client_id": account.client_id,
            "name": account.name,
            "broker": "ZERODHA",
            "balance": balance,
            "positions": positions,
            "error": _combine_errors(pos_error, bal_error),
        }

    async def _fetch_dhan(self, client: httpx.AsyncClient, account: AccountRow) -> dict[str, Any]:
        access_token = self._settings.dhan_access_token or account.access_token
        client_id = self._settings.dhan_client_id or account.client_id
        positions, pos_error = await dhan_positions.fetch_positions(client, access_token, client_id)
        balance, bal_error = await dhan_positions.fetch_fund_limit(client, access_token, client_id)
        return {
            "client_id": client_id,
            "name": account.name,
            "broker": "DHAN",
            "balance": balance,
            "positions": positions,
            "error": _combine_errors(pos_error, bal_error),
        }
