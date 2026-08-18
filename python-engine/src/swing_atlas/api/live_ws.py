"""Live strategy WebSocket route -- mirrors engine/src/api/mod.rs's
`/ws/live-strategies` -> swing::live_strategy_ws.
"""

from fastapi import APIRouter, WebSocket

from swing_atlas.services.live_strategy_ws_service import LiveStrategyWsService

router = APIRouter()


@router.websocket("/ws/live-strategies")
async def live_strategies(websocket: WebSocket) -> None:
    service: LiveStrategyWsService = websocket.app.state.live_strategy_ws_service
    await service.stream(websocket)
