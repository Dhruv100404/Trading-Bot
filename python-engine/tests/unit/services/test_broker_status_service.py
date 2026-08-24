import base64
import json
import time

from swing_atlas.config import Settings
from swing_atlas.services.broker_status_service import BrokerStatusService


def _make_token(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


class _FakeAccountsRepo:
    def __init__(self, account: tuple[str, str] | None = None) -> None:
        self._account = account

    async def latest_enabled_dhan_account(self) -> tuple[str, str] | None:
        return self._account


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


async def test_missing_when_no_env_token_and_no_db_account() -> None:
    service = BrokerStatusService(_settings(dhan_access_token=""), _FakeAccountsRepo(account=None))

    status = await service.resolve_broker_status()

    assert status.state == "missing"
    assert status.configured is False
    assert status.live_quotes is False


async def test_ready_when_env_token_valid_and_unexpired() -> None:
    token = _make_token({"exp": int(time.time()) + 3600, "iat": 1000, "dhanClientId": "CID1"})
    service = BrokerStatusService(
        _settings(dhan_access_token=token, dhan_client_id=""), _FakeAccountsRepo()
    )

    status = await service.resolve_broker_status()

    assert status.state == "ready"
    assert status.live_quotes is True
    assert status.client_id == "CID1"
    assert status.credential_source == "environment"


async def test_expired_when_exp_in_the_past() -> None:
    token = _make_token({"exp": int(time.time()) - 10, "iat": 1000, "dhanClientId": "CID1"})
    service = BrokerStatusService(_settings(dhan_access_token=token), _FakeAccountsRepo())

    status = await service.resolve_broker_status()

    assert status.state == "expired"
    assert status.live_quotes is False


async def test_invalid_when_token_cannot_be_decoded() -> None:
    service = BrokerStatusService(_settings(dhan_access_token="not-a-jwt"), _FakeAccountsRepo())

    status = await service.resolve_broker_status()

    assert status.state == "invalid"
    assert status.live_quotes is False


async def test_jwt_client_id_claim_wins_over_env_client_id_in_displayed_status() -> None:
    # Mirrors a real quirk in engine/src/api/swing.rs::resolve_broker_status: it re-decodes the
    # JWT and lets the token's own `dhanClientId` claim override the already-resolved
    # credentials.client_id whenever the claim is present -- even one set via DHAN_CLIENT_ID.
    token = _make_token({"exp": int(time.time()) + 3600, "iat": 1000, "dhanClientId": "FROM_JWT"})
    service = BrokerStatusService(
        _settings(dhan_access_token=token, dhan_client_id="FROM_ENV"), _FakeAccountsRepo()
    )

    status = await service.resolve_broker_status()

    assert status.client_id == "FROM_JWT"


async def test_falls_back_to_db_account_when_no_env_token() -> None:
    token = _make_token({"exp": int(time.time()) + 3600, "iat": 1000, "dhanClientId": "IGNORED"})
    service = BrokerStatusService(
        _settings(dhan_access_token=""), _FakeAccountsRepo(account=("DB_CLIENT", token))
    )

    status = await service.resolve_broker_status()

    assert status.state == "ready"
    assert status.credential_source == "accounts-db"
    assert status.client_id == "IGNORED"  # JWT claim wins over the DB row's client_id when present
