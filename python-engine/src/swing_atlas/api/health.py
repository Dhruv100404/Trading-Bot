from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Plain liveness check -- no DB dependency, matches compose healthcheck cadence."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """Readiness check -- confirms ClickHouse is reachable."""
    ch = request.app.state.clickhouse
    await ch.ping()
    return {"status": "ok"}
