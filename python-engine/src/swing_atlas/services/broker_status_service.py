"""Resolves which Dhan credentials are active and whether they're usable.

Mirrors engine/src/api/swing.rs::{resolve_dhan_credentials,resolve_broker_status}.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from swing_atlas.config import Settings
from swing_atlas.repositories.accounts_repo import AccountsRepo
from swing_atlas.repositories.dhan.jwt import InvalidDhanTokenError, parse_dhan_jwt_claims
from swing_atlas.schemas.swing import BrokerStatus


@dataclass(frozen=True, slots=True)
class ResolvedDhanCredentials:
    access_token: str
    client_id: str
    source: str


def _format_utc(timestamp: int) -> str | None:
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class BrokerStatusService:
    def __init__(self, settings: Settings, accounts_repo: AccountsRepo) -> None:
        self._settings = settings
        self._accounts_repo = accounts_repo

    async def resolve_dhan_credentials(self) -> ResolvedDhanCredentials | None:
        if self._settings.dhan_access_token:
            client_id = self._settings.dhan_client_id
            if not client_id:
                try:
                    claims = parse_dhan_jwt_claims(self._settings.dhan_access_token)
                    client_id = claims.dhan_client_id or ""
                except InvalidDhanTokenError:
                    client_id = ""
            return ResolvedDhanCredentials(
                access_token=self._settings.dhan_access_token,
                client_id=client_id,
                source="environment",
            )

        account = await self._accounts_repo.latest_enabled_dhan_account()
        if account is None:
            return None
        client_id, access_token = account
        return ResolvedDhanCredentials(
            access_token=access_token, client_id=client_id, source="accounts-db"
        )

    async def resolve_broker_status(self) -> BrokerStatus:
        credentials = await self.resolve_dhan_credentials()
        if credentials is None:
            return BrokerStatus(
                provider="DHAN",
                configured=False,
                state="missing",
                message=(
                    "No Dhan credentials are configured yet. "
                    "Add a fresh token to enable live scanner quotes."
                ),
                credential_source="none",
                client_id=None,
                issued_at_utc=None,
                expires_at_utc=None,
                live_quotes=False,
            )

        try:
            claims = parse_dhan_jwt_claims(credentials.access_token)
        except InvalidDhanTokenError as exc:
            return BrokerStatus(
                provider="DHAN",
                configured=True,
                state="invalid",
                message=f"Dhan token could not be decoded: {exc}",
                credential_source=credentials.source,
                client_id=credentials.client_id,
                issued_at_utc=None,
                expires_at_utc=None,
                live_quotes=False,
            )

        expires_at = _format_utc(claims.exp)
        issued_at = _format_utc(claims.iat)
        client_id = claims.dhan_client_id or credentials.client_id

        if claims.exp <= int(time.time()):
            return BrokerStatus(
                provider="DHAN",
                configured=True,
                state="expired",
                message=(
                    f"The Dhan token expired on {expires_at or 'an unknown date'}. "
                    "Add a fresh token to restore live quote fetches."
                ),
                credential_source=credentials.source,
                client_id=client_id,
                issued_at_utc=issued_at,
                expires_at_utc=expires_at,
                live_quotes=False,
            )

        return BrokerStatus(
            provider="DHAN",
            configured=True,
            state="ready",
            message="Credentials are configured and live quote fetch is enabled.",
            credential_source=credentials.source,
            client_id=client_id,
            issued_at_utc=issued_at,
            expires_at_utc=expires_at,
            live_quotes=True,
        )
