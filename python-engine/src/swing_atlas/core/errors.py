"""Standard error envelope for mutating endpoints (paper-trade CRUD).

Read-heavy dashboard GETs and refresh/trigger POSTs intentionally do NOT use this --
they preserve the Rust engine's always-200-with-inline-error contract instead,
since the frontend never branches on non-200 status for those routes.
"""

from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": str(uuid4()),
            }
        },
    )
