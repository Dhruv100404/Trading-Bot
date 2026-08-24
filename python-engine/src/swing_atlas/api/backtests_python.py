from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories import backtest_lab_repo
from swing_atlas.repositories.backtest_lab_repo import InvalidChartNameError

router = APIRouter(prefix="/api/backtests/python")


@router.get("/latest")
async def python_latest() -> JSONResponse:
    try:
        payload = backtest_lab_repo.read_python_lab_payload()
    except Exception as exc:  # noqa: BLE001 -- mirrors backtest.rs::python_latest's 404 mapping
        return JSONResponse(
            status_code=404,
            content={"message": f"python backtest lab output not found: {exc}"},
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "updated_at": now_ist().isoformat(),
            "duration_ms": None,
            "message": "Loaded current strategy lab results directly from CSV files.",
            "payload": payload,
        },
    )


@router.get("/charts/{name}")
async def python_chart(name: str) -> Response:
    try:
        data = backtest_lab_repo.read_chart_bytes(name)
    except InvalidChartNameError:
        return JSONResponse(status_code=400, content={"message": "invalid chart name"})
    except OSError as exc:
        return JSONResponse(status_code=404, content={"message": f"chart not found: {exc}"})
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})
