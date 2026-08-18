"""Paper-trade CRUD -- mirrors engine/src/api/paper.rs's route handlers.

Mutating endpoints: failures raise AppError and go through the org-standard
{"error": {...}} envelope (see core/errors.py), unlike the always-200
read-heavy dashboard GETs elsewhere in this API.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from swing_atlas.api.deps import get_paper_trades_service
from swing_atlas.schemas.paper_trades import PaperBudgetInput, PaperTradeCloseInput, PaperTradeInput
from swing_atlas.services.paper_trades_service import PaperTradesService

router = APIRouter()

_Service = Annotated[PaperTradesService, Depends(get_paper_trades_service)]


@router.get("/api/paper-trades")
async def list_trades(service: _Service) -> JSONResponse:
    trades = await service.list_trades()
    return JSONResponse(content={"trades": [asdict(trade) for trade in trades]})


@router.post("/api/paper-trades")
async def upsert_trade(trade_input: PaperTradeInput, service: _Service) -> JSONResponse:
    trade = await service.upsert(trade_input)
    return JSONResponse(content=asdict(trade))


@router.post("/api/paper-trades/{symbol}/close")
async def close_trade(
    symbol: str, close_input: PaperTradeCloseInput, service: _Service
) -> JSONResponse:
    trade = await service.close(symbol, close_input.exit_price, close_input.close_reason)
    return JSONResponse(content=asdict(trade))


@router.delete("/api/paper-trades/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_trade(symbol: str, service: _Service) -> None:
    await service.remove(symbol)


@router.get("/api/paper-budget")
async def budget(service: _Service) -> JSONResponse:
    return JSONResponse(content=await service.budget())


@router.post("/api/paper-budget")
async def set_budget(budget_input: PaperBudgetInput, service: _Service) -> JSONResponse:
    return JSONResponse(content=await service.set_budget(budget_input.total_budget))
