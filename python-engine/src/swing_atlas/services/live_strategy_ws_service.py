"""Live strategy WebSocket bridge -- mirrors engine/src/api/swing.rs's
stream_live_strategies/stream_rest_strategy_snapshots/maybe_send_telegram_trigger_alerts.

Replicates the Rust original's tokio::select! three-way multiplex (heartbeat /
browser-recv / Dhan-tick) with asyncio.wait over three recreated-per-iteration
tasks. One intentional behavioral difference: if more than one of the three
events is ready in the same iteration, this processes all of them before
looping (tokio::select! processes exactly one, chosen pseudo-randomly, per
iteration) -- both converge to the same steady-state behavior, this is just
fewer iterations under a tick burst.

Named improvement over the Rust original (per the migration plan): Dhan
auto-reconnect-on-disconnect. The Rust engine has none -- a Dhan disconnect
just leaves the browser panel un-updating until the browser's own
auto-reconnect (see ui/src/App.tsx) reopens the socket from scratch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

import httpx
import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from swing_atlas.config import Settings
from swing_atlas.domain.swing.live_snapshot import (
    build_live_strategy_snapshot,
    empty_live_snapshot,
    format_telegram_trigger_message,
)
from swing_atlas.domain.swing.models import (
    HistoricalScreenerFeatureRow,
    LiveStrategySnapshot,
    WatchRow,
    WeeklyLabCandidate,
)
from swing_atlas.domain.swing.news_confluence import NewsConfluenceRow
from swing_atlas.domain.swing.tick_parser import DhanQuoteTick, parse_dhan_quote_packets
from swing_atlas.repositories import (
    live_signal_baseline_repo,
    news_confluence_repo,
    weekly_lab_repo,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.dhan.market_data import QuoteItem, QuoteOhlc
from swing_atlas.repositories.dhan.quote_cache import QuoteCache
from swing_atlas.repositories.screener_feature_cache_repo import load_latest_strategy_statuses
from swing_atlas.repositories.telegram_repo import send_telegram_message
from swing_atlas.repositories.watchlist_repo import WatchlistRepo
from swing_atlas.schemas.swing import BrokerStatus
from swing_atlas.services.broker_status_service import BrokerStatusService, ResolvedDhanCredentials

logger = logging.getLogger(__name__)

_DHAN_FEED_URL = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"
_HEARTBEAT_SECS = 5.0
_NEWS_CONFLUENCE_REFRESH_SECS = 300.0
_TICK_PUBLISH_INTERVAL_SECS = 0.9
_REST_POLL_INTERVAL_SECS = 10.0
_SUBSCRIBE_CHUNK_SIZE = 100

_T = TypeVar("_T")


@dataclass
class _LiveTriggerAlertMarker:
    last_price: float = 0.0
    notified_trigger: float | None = None


@dataclass
class _StreamContext:
    """The mutable, per-connection state threaded through the streaming loop
    -- mirrors the local variables Rust's stream_live_strategies/
    stream_rest_strategy_snapshots hold across tokio::select! iterations."""

    broker: BrokerStatus
    watch_rows: list[WatchRow]
    volume_map: dict[str, str]
    quote_map: dict[str, QuoteItem]
    baselines: dict[str, HistoricalScreenerFeatureRow]
    strategy_statuses: dict[str, str]
    weekly_lab_candidates: dict[str, WeeklyLabCandidate]
    news_confluence: dict[str, NewsConfluenceRow]
    symbols: list[str]


class LiveStrategyWsService:
    """One instance shared process-wide (see app.py's lifespan), matching
    Rust's AppState.telegram_alert_state -- alert dedup state must survive
    across browser reconnects, not reset per connection."""

    def __init__(
        self,
        settings: Settings,
        ch: ClickHouseRepo,
        watchlist_repo: WatchlistRepo,
        broker_status_service: BrokerStatusService,
        quote_cache: QuoteCache,
    ) -> None:
        self._settings = settings
        self._ch = ch
        self._watchlist_repo = watchlist_repo
        self._broker_status_service = broker_status_service
        self._quote_cache = quote_cache
        self._telegram_alert_state: dict[str, _LiveTriggerAlertMarker] = {}
        self._telegram_lock = asyncio.Lock()

    async def stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            await self._stream_live_strategies(websocket)
        except WebSocketDisconnect:
            pass
        finally:
            if websocket.client_state != WebSocketState.DISCONNECTED:
                with contextlib.suppress(Exception):
                    await websocket.close()

    async def _stream_live_strategies(self, websocket: WebSocket) -> None:
        broker = await self._broker_status_service.resolve_broker_status()
        credentials = await self._broker_status_service.resolve_dhan_credentials()
        if credentials is None:
            await self._publish(
                websocket,
                empty_live_snapshot(
                    broker,
                    "missing-credentials",
                    "No Dhan credentials are configured, so live strategy streaming cannot start.",
                ),
            )
            return

        if broker.state != "ready":
            await self._publish(
                websocket, empty_live_snapshot(broker, "broker-not-ready", broker.message)
            )
            return

        watch_rows = await self._watchlist_repo.load_watch_rows(1000, None)
        if not watch_rows:
            await self._publish(
                websocket,
                empty_live_snapshot(
                    broker,
                    "empty-universe",
                    "No enabled watchlist instruments were found for live strategy streaming.",
                ),
            )
            return

        symbols = [row.symbol for row in watch_rows]
        ctx = _StreamContext(
            broker=broker,
            watch_rows=watch_rows,
            volume_map=weekly_lab_repo.load_volume_groups_map(),
            quote_map={},
            baselines=await self._safe(
                live_signal_baseline_repo.load_live_signal_baselines(self._ch, symbols),
                {},
                "live signal baseline lookup",
            ),
            strategy_statuses=await self._safe(
                load_latest_strategy_statuses(self._ch), {}, "latest strategy status lookup"
            ),
            weekly_lab_candidates=weekly_lab_repo.load_weekly_lab_candidates(),
            news_confluence=await self._safe(
                news_confluence_repo.load_recent_news_confluence(self._ch, symbols),
                {},
                "live news confluence lookup",
            ),
            symbols=symbols,
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            ctx.quote_map = await self._initial_live_quotes(client, credentials, watch_rows)

        snapshot = build_live_strategy_snapshot(
            ctx.broker, "dhan-websocket", "connecting", ctx.watch_rows, ctx.volume_map,
            ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
            ctx.news_confluence, "Connecting to Dhan live market feed...",
        )  # fmt: skip
        if not await self._publish(websocket, snapshot):
            return

        url = _DHAN_FEED_URL.format(token=credentials.access_token, client_id=credentials.client_id)
        try:
            async with websockets.connect(url) as dhan_ws:
                await self._subscribe(dhan_ws, watch_rows)
                await self._run_streaming_loop(websocket, dhan_ws, ctx)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            logger.warning("Dhan websocket connect failed: %s", exc)
            await self._stream_rest_strategy_snapshots(
                websocket,
                credentials,
                ctx,
                "websocket-connect-failed",
                "Dhan websocket connection failed; keeping the live panel refreshed from REST "
                "quote snapshots.",
            )

    async def _subscribe(
        self, dhan_ws: websockets.ClientConnection, watch_rows: list[WatchRow]
    ) -> None:
        for start in range(0, len(watch_rows), _SUBSCRIBE_CHUNK_SIZE):
            chunk = watch_rows[start : start + _SUBSCRIBE_CHUNK_SIZE]
            message = {
                "RequestCode": 17,
                "InstrumentCount": len(chunk),
                "InstrumentList": [
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": row.security_id} for row in chunk
                ],
            }
            await dhan_ws.send(json.dumps(message))

    async def _run_streaming_loop(
        self, websocket: WebSocket, dhan_ws: websockets.ClientConnection, ctx: _StreamContext
    ) -> None:
        last_news_refresh = time.monotonic()
        last_sent = time.monotonic() - 2.0

        while True:
            heartbeat_task: asyncio.Task[Any] = asyncio.ensure_future(
                asyncio.sleep(_HEARTBEAT_SECS)
            )
            browser_task: asyncio.Task[Any] = asyncio.ensure_future(websocket.receive())
            dhan_task: asyncio.Task[Any] = asyncio.ensure_future(dhan_ws.recv())
            done, pending = await asyncio.wait(
                [heartbeat_task, browser_task, dhan_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            if browser_task in done and await self._handle_browser_event(browser_task):
                return

            if heartbeat_task in done:
                if time.monotonic() - last_news_refresh >= _NEWS_CONFLUENCE_REFRESH_SECS:
                    refreshed = await self._safe(
                        news_confluence_repo.load_recent_news_confluence(self._ch, ctx.symbols),
                        None,
                        "live news confluence refresh",
                    )
                    if refreshed is not None:
                        ctx.news_confluence = refreshed
                    last_news_refresh = time.monotonic()
                snapshot = build_live_strategy_snapshot(
                    ctx.broker, "dhan-websocket", "streaming", ctx.watch_rows, ctx.volume_map,
                    ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
                    ctx.news_confluence, None,
                )  # fmt: skip
                if not await self._publish(websocket, snapshot):
                    return

            if dhan_task in done:
                should_stop = await self._handle_dhan_event(websocket, dhan_task, ctx, last_sent)
                if should_stop is None:
                    return
                last_sent = should_stop

    async def _handle_dhan_event(
        self,
        websocket: WebSocket,
        dhan_task: asyncio.Task[Any],
        ctx: _StreamContext,
        last_sent: float,
    ) -> float | None:
        """Returns the updated last_sent timestamp, or None to signal the
        caller should stop the stream."""
        try:
            message = dhan_task.result()
        except websockets.exceptions.ConnectionClosed as exc:
            ctx.broker.live_quotes = False
            snapshot = build_live_strategy_snapshot(
                ctx.broker, "dhan-websocket", "disconnected", ctx.watch_rows, ctx.volume_map,
                ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
                ctx.news_confluence, "Dhan websocket disconnected. Reopen Strategies to reconnect.",
            )  # fmt: skip
            await self._publish(websocket, snapshot)
            logger.warning("Dhan websocket closed: %s", exc)
            return None
        except OSError as exc:
            ctx.broker.live_quotes = False
            snapshot = build_live_strategy_snapshot(
                ctx.broker, "dhan-websocket", "feed-error", ctx.watch_rows, ctx.volume_map,
                ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
                ctx.news_confluence, f"Dhan websocket error: {exc}",
            )  # fmt: skip
            await self._publish(websocket, snapshot)
            return None

        if not isinstance(message, bytes | bytearray):
            return last_sent

        for tick in parse_dhan_quote_packets(bytes(message)):
            _apply_tick(ctx.quote_map, tick)

        if time.monotonic() - last_sent < _TICK_PUBLISH_INTERVAL_SECS:
            return last_sent

        snapshot = build_live_strategy_snapshot(
            ctx.broker, "dhan-websocket", "streaming", ctx.watch_rows, ctx.volume_map,
            ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
            ctx.news_confluence, None,
        )  # fmt: skip
        if not await self._publish(websocket, snapshot):
            return None
        return time.monotonic()

    async def _handle_browser_event(self, browser_task: asyncio.Task[Any]) -> bool:
        try:
            event = browser_task.result()
        except WebSocketDisconnect:
            return True
        event_type = event.get("type") if isinstance(event, dict) else None
        return event_type == "websocket.disconnect"

    async def _stream_rest_strategy_snapshots(
        self,
        websocket: WebSocket,
        credentials: ResolvedDhanCredentials,
        ctx: _StreamContext,
        feed_status: str,
        message: str,
    ) -> None:
        last_news_refresh = time.monotonic()
        first = True
        while True:
            if not first:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    refreshed_quotes = await self._safe(
                        self._initial_live_quotes(client, credentials, ctx.watch_rows),
                        None,
                        "REST quote refresh",
                    )
                if refreshed_quotes is not None:
                    ctx.quote_map = refreshed_quotes
            if time.monotonic() - last_news_refresh >= _NEWS_CONFLUENCE_REFRESH_SECS:
                refreshed = await self._safe(
                    news_confluence_repo.load_recent_news_confluence(self._ch, ctx.symbols),
                    None,
                    "live news confluence refresh",
                )
                if refreshed is not None:
                    ctx.news_confluence = refreshed
                last_news_refresh = time.monotonic()

            snapshot = build_live_strategy_snapshot(
                ctx.broker, "rest-snapshot", feed_status, ctx.watch_rows, ctx.volume_map,
                ctx.quote_map, ctx.baselines, ctx.strategy_statuses, ctx.weekly_lab_candidates,
                ctx.news_confluence, message,
            )  # fmt: skip
            first = False
            if not await self._publish(websocket, snapshot):
                return

            poll_task: asyncio.Task[Any] = asyncio.ensure_future(
                asyncio.sleep(_REST_POLL_INTERVAL_SECS)
            )
            browser_task: asyncio.Task[Any] = asyncio.ensure_future(websocket.receive())
            done, pending = await asyncio.wait(
                [poll_task, browser_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if browser_task in done and await self._handle_browser_event(browser_task):
                return

    async def _initial_live_quotes(
        self,
        client: httpx.AsyncClient,
        credentials: ResolvedDhanCredentials,
        watch_rows: list[WatchRow],
    ) -> dict[str, QuoteItem]:
        security_ids = [row.security_id for row in watch_rows]
        return await self._quote_cache.get_live_quotes(
            client,
            self._settings.dhan_base_url,
            credentials.access_token,
            credentials.client_id,
            self._settings.dhan_quote_endpoint,
            security_ids,
        )

    async def _publish(self, websocket: WebSocket, snapshot: LiveStrategySnapshot) -> bool:
        await self._maybe_send_telegram_trigger_alerts(snapshot)
        try:
            await websocket.send_text(_snapshot_to_json(snapshot))
            return True
        except Exception as exc:  # noqa: BLE001 -- any send failure ends the stream, matching Rust
            logger.info("live strategy snapshot send failed: %s", exc)
            return False

    async def _maybe_send_telegram_trigger_alerts(self, snapshot: LiveStrategySnapshot) -> None:
        token = self._settings.telegram_bot_token.strip()
        chat_id = self._settings.telegram_chat_id.strip()
        if not token or not chat_id:
            return

        messages: list[str] = []
        async with self._telegram_lock:
            for row in snapshot.rows:
                if row.source != "dhan-live":
                    continue
                key = f"{row.strategy_id}:{row.symbol}"
                trigger = row.trigger_price
                if trigger is None or trigger <= 0.0:
                    self._telegram_alert_state[key] = _LiveTriggerAlertMarker(
                        last_price=row.last_price
                    )
                    continue

                marker = self._telegram_alert_state.setdefault(
                    key, _LiveTriggerAlertMarker(last_price=row.last_price)
                )
                crossed_trigger = (
                    marker.last_price > 0.0
                    and marker.last_price < trigger
                    and row.last_price >= trigger
                )
                already_notified = (
                    marker.notified_trigger is not None
                    and abs(marker.notified_trigger - trigger) < 0.005
                )
                if crossed_trigger and row.signal_status == "ENTRY_NOW" and not already_notified:
                    marker.notified_trigger = trigger
                    messages.append(format_telegram_trigger_message(row, trigger))
                marker.last_price = row.last_price

        if not messages:
            return
        async with httpx.AsyncClient() as client:
            for message in messages:
                try:
                    await send_telegram_message(client, token, chat_id, message)
                except Exception as exc:  # noqa: BLE001 -- one alert failing must not break the stream
                    logger.warning("telegram trigger alert failed: %s", exc)

    async def _safe(self, awaitable: Awaitable[_T], default: _T, label: str) -> _T:
        try:
            return await awaitable
        except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default() fallback
            logger.warning("%s failed: %s", label, exc)
            return default


def _apply_tick(quote_map: dict[str, QuoteItem], tick: DhanQuoteTick) -> None:
    existing = quote_map.get(tick.security_id)
    if existing is None:
        quote_map[tick.security_id] = QuoteItem(
            last_price=tick.last_price,
            ohlc=QuoteOhlc(open=tick.open, high=tick.high, low=tick.low, close=tick.prev_close),
            volume=tick.volume,
        )
        return
    quote_map[tick.security_id] = QuoteItem(
        last_price=tick.last_price if tick.last_price > 0.0 else existing.last_price,
        ohlc=QuoteOhlc(
            open=tick.open if tick.open > 0.0 else existing.ohlc.open,
            high=tick.high if tick.high > 0.0 else existing.ohlc.high,
            low=tick.low if tick.low > 0.0 else existing.ohlc.low,
            close=tick.prev_close if tick.prev_close > 0.0 else existing.ohlc.close,
        ),
        volume=tick.volume if tick.volume > 0 else existing.volume,
    )


def _snapshot_to_json(snapshot: LiveStrategySnapshot) -> str:
    payload = {
        "event": snapshot.event,
        "updated_at": snapshot.updated_at,
        "mode": snapshot.mode,
        "feed_status": snapshot.feed_status,
        "broker": snapshot.broker.model_dump(),
        "market_regime": asdict(snapshot.market_regime),
        "total_watching": snapshot.total_watching,
        "triggered": snapshot.triggered,
        "rows": [asdict(row) for row in snapshot.rows],
        "message": snapshot.message,
    }
    return json.dumps(payload)
