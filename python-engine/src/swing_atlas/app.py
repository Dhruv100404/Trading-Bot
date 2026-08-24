import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from swing_atlas.api.backtests import router as backtests_router
from swing_atlas.api.backtests_python import router as backtests_python_router
from swing_atlas.api.health import router as health_router
from swing_atlas.api.live_ws import router as live_ws_router
from swing_atlas.api.market_activity import router as market_activity_router
from swing_atlas.api.news import router as news_router
from swing_atlas.api.paper_trades import router as paper_trades_router
from swing_atlas.api.positions import router as positions_router
from swing_atlas.api.swing import router as swing_router
from swing_atlas.background.jobs import market_activity_refresh_job, news_refresh_job
from swing_atlas.background.scheduler import create_scheduler, schedule_interval_job
from swing_atlas.config import get_settings
from swing_atlas.core.errors import AppError, app_error_handler
from swing_atlas.logging_config import configure_logging
from swing_atlas.repositories.accounts_repo import AccountsRepo
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.corporate_events_repo import backfill_corporate_events_if_empty
from swing_atlas.repositories.dhan.quote_cache import QuoteCache
from swing_atlas.repositories.market_activity_repo import MarketActivityRepo
from swing_atlas.repositories.news_repo import NewsRepo
from swing_atlas.repositories.schema import (
    ensure_corporate_events_schema,
    ensure_market_activity_schema,
    ensure_news_schema,
)
from swing_atlas.repositories.watchlist_repo import WatchlistRepo
from swing_atlas.services.broker_status_service import BrokerStatusService
from swing_atlas.services.live_strategy_ws_service import LiveStrategyWsService
from swing_atlas.services.market_activity_service import MarketActivityService
from swing_atlas.services.news_service import NewsService
from swing_atlas.services.paper_trades_service import PaperTradesService
from swing_atlas.services.positions_service import PositionsService
from swing_atlas.services.swing_dashboard_service import SwingDashboardService
from swing_atlas.services.swing_history_service import SwingHistoryService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.debug)

    ch = await ClickHouseRepo.connect(settings.clickhouse_url)
    app.state.clickhouse = ch

    await ensure_news_schema(ch)
    await ensure_corporate_events_schema(ch)
    await ensure_market_activity_schema(ch)

    try:
        seeded = await backfill_corporate_events_if_empty(ch)
        logger.info("[INIT] corporate-event history available (%d rows)", seeded)
    except Exception:  # noqa: BLE001 -- a missing research cache must never block startup
        logger.warning("[INIT] corporate-event history backfill skipped", exc_info=True)

    accounts_repo = AccountsRepo(ch)
    watchlist_repo = WatchlistRepo(ch)
    news_repo = NewsRepo(ch)
    market_activity_repo = MarketActivityRepo(ch)

    broker_status_service = BrokerStatusService(settings, accounts_repo)
    app.state.broker_status_service = broker_status_service
    app.state.positions_service = PositionsService(settings, accounts_repo)
    app.state.news_repo = news_repo
    app.state.market_activity_repo = market_activity_repo
    app.state.news_service = NewsService(settings, news_repo, watchlist_repo)
    app.state.market_activity_service = MarketActivityService(settings, market_activity_repo)
    quote_cache = QuoteCache()
    app.state.paper_trades_service = PaperTradesService(settings, ch, watchlist_repo, quote_cache)
    app.state.swing_dashboard_service = SwingDashboardService(
        settings, ch, watchlist_repo, broker_status_service, quote_cache
    )
    app.state.swing_history_service = SwingHistoryService(
        settings, ch, watchlist_repo, broker_status_service, quote_cache
    )
    app.state.live_strategy_ws_service = LiveStrategyWsService(
        settings, ch, watchlist_repo, broker_status_service, quote_cache
    )

    scheduler = create_scheduler()
    if settings.news_auto_refresh:
        schedule_interval_job(
            scheduler,
            "news-refresh",
            news_refresh_job,
            settings.news_refresh_interval_secs,
            app.state.news_service,
        )
        logger.info(
            "[NEWS] auto-refresh enabled; interval=%ds", settings.news_refresh_interval_secs
        )
    else:
        logger.info("[NEWS] auto-refresh disabled")

    if settings.market_activity_auto_refresh:
        schedule_interval_job(
            scheduler,
            "market-activity-refresh",
            market_activity_refresh_job,
            settings.market_activity_refresh_interval_secs,
            app.state.market_activity_service,
        )
        logger.info(
            "[MARKET-ACTIVITY] auto-refresh enabled; interval=%ds",
            settings.market_activity_refresh_interval_secs,
        )
    else:
        logger.info("[MARKET-ACTIVITY] auto-refresh disabled")
    scheduler.start()

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await ch.close()


def create_app() -> FastAPI:
    app = FastAPI(title="swing-atlas", lifespan=lifespan)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(health_router)
    app.include_router(live_ws_router)
    app.include_router(swing_router)
    app.include_router(positions_router)
    app.include_router(news_router)
    app.include_router(market_activity_router)
    app.include_router(backtests_router)
    app.include_router(backtests_python_router)
    app.include_router(paper_trades_router)
    return app


app = create_app()
