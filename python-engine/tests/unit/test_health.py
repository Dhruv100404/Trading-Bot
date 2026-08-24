from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from swing_atlas.api.health import router as health_router


class _FakeClickHouseRepo:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def ping(self) -> bool:
        if not self._healthy:
            raise ConnectionError("clickhouse unreachable")
        return True


def _build_app(*, clickhouse_healthy: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    app.state.clickhouse = _FakeClickHouseRepo(clickhouse_healthy)
    return app


async def test_healthz_does_not_touch_clickhouse() -> None:
    app = _build_app(clickhouse_healthy=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_ok_when_clickhouse_reachable() -> None:
    app = _build_app(clickhouse_healthy=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_fails_when_clickhouse_unreachable() -> None:
    app = _build_app(clickhouse_healthy=False)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 500
