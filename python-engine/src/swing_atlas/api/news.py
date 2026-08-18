from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from swing_atlas.api.deps import get_news_repo, get_news_service
from swing_atlas.api.response_helpers import degrade_on_error
from swing_atlas.repositories import files_repo
from swing_atlas.repositories.news_repo import NewsRepo
from swing_atlas.services.news_service import NewsService

router = APIRouter(prefix="/api")


@router.get("/news")
async def list_news(
    repo: Annotated[NewsRepo, Depends(get_news_repo)],
    symbol: str | None = None,
    source: str | None = None,
    min_impact: float | None = None,
    limit: int | None = None,
) -> JSONResponse:
    return await degrade_on_error(
        "news",
        lambda: repo.list_news(symbol=symbol, source=source, min_impact=min_impact, limit=limit),
    )


@router.post("/news/refresh")
async def refresh(service: Annotated[NewsService, Depends(get_news_service)]) -> JSONResponse:
    try:
        summary = await service.refresh_all()
    except Exception as exc:  # noqa: BLE001 -- mirrors news.rs::refresh's 502 mapping
        return JSONResponse(
            status_code=502, content={"ok": False, "message": str(exc), "error": str(exc)}
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": summary.ok,
            "articles": summary.articles,
            "mentions": summary.mentions,
            "scores": summary.scores,
            "deals": summary.deals,
            "watchlist_companies": summary.watchlist_companies,
            "sources": [
                {"source": s.source, "kind": s.kind.value, "category": s.category, "url": s.url}
                for s in summary.sources
            ],
            "refreshed_at": summary.refreshed_at,
            "deal_error": summary.deal_error,
        },
    )


@router.get("/nse/large-deals")
async def large_deals(
    repo: Annotated[NewsRepo, Depends(get_news_repo)],
    symbol: str | None = None,
    deal_type: str | None = None,
    side: str | None = None,
    limit: int | None = None,
) -> JSONResponse:
    return await degrade_on_error(
        "deals",
        lambda: repo.large_deals(symbol=symbol, deal_type=deal_type, side=side, limit=limit),
    )


@router.get("/news/events")
async def corporate_events(
    repo: Annotated[NewsRepo, Depends(get_news_repo)],
    symbol: str | None = None,
    category: str | None = None,
    lookback_days: int | None = None,
    limit: int | None = None,
) -> JSONResponse:
    return await degrade_on_error(
        "events",
        lambda: repo.corporate_events(
            symbol=symbol, category=category, lookback_days=lookback_days, limit=limit
        ),
    )


@router.get("/news/strategy-evidence")
async def strategy_evidence() -> JSONResponse:
    try:
        evidence = files_repo.read_strategy_evidence()
    except Exception as exc:  # noqa: BLE001 -- mirrors news.rs::strategy_evidence's catch-all 404
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "evidence_kind": "historical_backtest",
                "message": f"Strategy evidence is unavailable: {exc}",
            },
        )
    return JSONResponse(status_code=200, content=evidence)


@router.get("/news/predictions")
async def prediction_history(
    success: bool | None = None,
    split: str | None = None,
    limit: int | None = None,
) -> JSONResponse:
    clamped_limit = max(1, min(100, limit if limit is not None else 12))
    try:
        rows, matched = files_repo.read_prediction_history(
            success=success, split=split, limit=clamped_limit
        )
    except Exception as exc:  # noqa: BLE001 -- mirrors news.rs::prediction_history's catch-all 404
        return JSONResponse(
            status_code=404,
            content={
                "predictions": [],
                "matched": 0,
                "evidence_kind": "historical_backtest",
                "live_predictions": False,
                "message": f"Prediction history is unavailable: {exc}",
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "predictions": rows,
            "matched": matched,
            "evidence_kind": "historical_backtest",
            "live_predictions": False,
            "message": (
                "Historical simulation only; prospective paper outcomes are not available yet."
            ),
        },
    )
