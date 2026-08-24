from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from swing_atlas.api.deps import (
    get_broker_status_service,
    get_clickhouse,
    get_swing_dashboard_service,
    get_swing_history_service,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.schemas.swing import BrokerStatus
from swing_atlas.services import screener_service
from swing_atlas.services.broker_status_service import BrokerStatusService
from swing_atlas.services.swing_dashboard_service import SwingDashboardService
from swing_atlas.services.swing_history_service import SwingHistoryService

router = APIRouter(prefix="/api/swing")


@router.get("/broker-status", response_model=BrokerStatus)
async def broker_status(
    service: Annotated[BrokerStatusService, Depends(get_broker_status_service)],
) -> BrokerStatus:
    return await service.resolve_broker_status()


@router.get("/home")
async def home(
    service: Annotated[SwingDashboardService, Depends(get_swing_dashboard_service)],
) -> JSONResponse:
    return JSONResponse(status_code=200, content=await service.home())


@router.get("/scanner")
async def scanner(
    service: Annotated[SwingDashboardService, Depends(get_swing_dashboard_service)],
    limit: int | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=200, content=await service.scanner(limit))


@router.get("/candidates/{symbol}")
async def candidate_detail(
    symbol: str,
    service: Annotated[SwingDashboardService, Depends(get_swing_dashboard_service)],
) -> JSONResponse:
    return JSONResponse(status_code=200, content=await service.candidate_detail(symbol))


@router.get("/history/{symbol}")
async def history(
    symbol: str,
    service: Annotated[SwingHistoryService, Depends(get_swing_history_service)],
    range: str | None = None,  # noqa: A002 -- mirrors the Rust query param name exactly
) -> JSONResponse:
    return JSONResponse(status_code=200, content=await service.history(symbol, range))


@router.get("/bamboo/latest")
async def bamboo_latest(
    service: Annotated[SwingHistoryService, Depends(get_swing_history_service)],
) -> JSONResponse:
    return JSONResponse(status_code=200, content=await service.bamboo_latest())


@router.get("/historical-screener")
async def historical_screener(
    ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)],
    limit: int | None = None,
    setup: str | None = None,
    strategy: str | None = None,
    min_price: float | None = None,
    min_avg_volume: float | None = None,
) -> JSONResponse:
    result = await screener_service.historical_screener(
        ch, limit, setup, strategy, min_price, min_avg_volume
    )
    return JSONResponse(status_code=200, content=asdict(result))


@router.post("/fresh-signals")
async def fresh_signals(
    ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)],
    limit: int | None = None,
    min_price: float | None = None,
    min_avg_volume: float | None = None,
) -> JSONResponse:
    try:
        result = await screener_service.fresh_signals(ch, limit, min_price, min_avg_volume)
    except Exception as exc:  # noqa: BLE001 -- mirrors swing.rs::fresh_signals's 500 mapping
        return JSONResponse(
            status_code=500, content={"message": f"Fresh signal screener query failed: {exc}"}
        )
    return JSONResponse(status_code=200, content=asdict(result))


@router.post("/feature-cache/refresh")
async def refresh_feature_cache(
    ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)],
) -> JSONResponse:
    try:
        result = await screener_service.refresh_feature_cache(ch)
    except Exception as exc:  # noqa: BLE001 -- mirrors swing.rs::refresh_feature_cache's 500 mapping
        return JSONResponse(
            status_code=500, content={"message": f"Feature cache refresh failed: {exc}"}
        )
    return JSONResponse(status_code=200, content=asdict(result))
