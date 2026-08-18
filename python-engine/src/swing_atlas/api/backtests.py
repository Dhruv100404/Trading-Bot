from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from swing_atlas.api.deps import get_clickhouse
from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.services import backtest_service

router = APIRouter(prefix="/api/backtests")


@router.get("/dashboard")
async def dashboard(ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)]) -> JSONResponse:
    run_id = await backtest_service.resolve_latest_run_id(ch)
    result = await backtest_service.build_dashboard(ch, run_id)
    return JSONResponse(status_code=200, content=asdict(result))


@router.get("/datewise")
async def datewise(
    ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)],
    date: str | None = None,
    strategy: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> JSONResponse:
    run_id = await backtest_service.resolve_latest_run_id(ch)
    resolved_page = max(1, page if page is not None else 1)
    resolved_page_size = max(10, min(50, page_size if page_size is not None else 25))
    result = await backtest_service.build_datewise(
        ch, run_id, date, strategy, resolved_page, resolved_page_size
    )
    return JSONResponse(status_code=200, content=asdict(result))


@router.post("/run")
async def run(ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)]) -> JSONResponse:
    try:
        run_id, message, cache, dashboard_result = await backtest_service.run_backtest(ch)
    except Exception as exc:  # noqa: BLE001 -- mirrors backtest.rs::run's 500 mapping
        return JSONResponse(status_code=500, content={"message": f"backtest run failed: {exc}"})
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "run_id": run_id,
            "message": message,
            "cache": asdict(cache),
            "dashboard": asdict(dashboard_result),
        },
    )


@router.post("/feature-cache/refresh")
async def refresh_cache(ch: Annotated[ClickHouseRepo, Depends(get_clickhouse)]) -> JSONResponse:
    try:
        cache = await backtest_service.refresh_cache(ch)
    except Exception as exc:  # noqa: BLE001 -- mirrors backtest.rs::refresh_cache's 500 mapping
        return JSONResponse(
            status_code=500, content={"message": f"backtest cache refresh failed: {exc}"}
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "updated_at": now_ist().isoformat(),
            "cache": asdict(cache),
            "message": (
                "Backtest feature cache refreshed from parquet. Future backtest runs read "
                "ClickHouse cached features instead of rebuilding indicators from raw parquet."
            ),
        },
    )
