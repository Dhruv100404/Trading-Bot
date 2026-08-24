from typing import Annotated, Any

from fastapi import APIRouter, Depends

from swing_atlas.api.deps import get_positions_service
from swing_atlas.services.positions_service import PositionsService

router = APIRouter(prefix="/api")


@router.get("/positions")
async def list_positions(
    service: Annotated[PositionsService, Depends(get_positions_service)],
) -> dict[str, list[dict[str, Any]]]:
    return {"accounts": await service.list_accounts()}
