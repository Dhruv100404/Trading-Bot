from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from swing_atlas.api.deps import get_market_activity_repo, get_market_activity_service
from swing_atlas.api.response_helpers import degrade_on_error
from swing_atlas.repositories.market_activity_repo import MarketActivityRepo
from swing_atlas.services.market_activity_service import MarketActivityService

router = APIRouter(prefix="/api")


@router.get("/market-activity")
async def list_market_activity(
    repo: Annotated[MarketActivityRepo, Depends(get_market_activity_repo)],
    symbol: str | None = None,
    metric_type: str | None = None,
    exchange: str | None = None,
    source: str | None = None,
    min_volume_multiplier: float | None = None,
    limit: int | None = None,
) -> JSONResponse:
    return await degrade_on_error(
        "activity",
        lambda: repo.list_activity(
            symbol=symbol,
            metric_type=metric_type,
            exchange=exchange,
            source=source,
            min_volume_multiplier=min_volume_multiplier,
            limit=limit,
        ),
    )


@router.post("/market-activity/refresh")
async def refresh(
    service: Annotated[MarketActivityService, Depends(get_market_activity_service)],
) -> JSONResponse:
    try:
        summary = await service.refresh_all()
    except Exception as exc:  # noqa: BLE001 -- mirrors market_activity.rs::refresh's 502 mapping
        return JSONResponse(
            status_code=502, content={"ok": False, "message": str(exc), "error": str(exc)}
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": summary.ok,
            "rows": summary.rows,
            "sources": [
                {
                    "source": s.source,
                    "metric_type": s.metric_type,
                    "exchange": s.exchange,
                    "index_name": s.index_name,
                    "url": s.url,
                }
                for s in summary.sources
            ],
            "refreshed_at": summary.refreshed_at,
        },
    )
