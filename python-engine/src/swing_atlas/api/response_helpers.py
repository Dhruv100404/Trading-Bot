"""Shared response shaping for read-heavy dashboard GETs.

These always return 200 on success and degrade to an inline error on failure
rather than a bare 500, mirroring engine/src/api/{news,market_activity}.rs's
query_json error handling -- the frontend never branches on non-200 status here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def degrade_on_error(
    response_key: str, fetch: Callable[[], Awaitable[list[dict[str, Any]]]]
) -> JSONResponse:
    try:
        rows = await fetch()
    except Exception as exc:  # noqa: BLE001 -- any failure degrades the response, never a bare 500
        logger.warning("%s query failed: %s", response_key, exc)
        return JSONResponse(
            status_code=502,
            content={response_key: [], "ok": False, "message": str(exc), "error": str(exc)},
        )
    return JSONResponse(status_code=200, content={response_key: rows})
