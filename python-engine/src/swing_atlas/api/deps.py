"""Depends() providers -- routes call these instead of touching request.app.state directly."""

from __future__ import annotations

from fastapi import Request

from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.market_activity_repo import MarketActivityRepo
from swing_atlas.repositories.news_repo import NewsRepo
from swing_atlas.services.broker_status_service import BrokerStatusService
from swing_atlas.services.market_activity_service import MarketActivityService
from swing_atlas.services.news_service import NewsService
from swing_atlas.services.paper_trades_service import PaperTradesService
from swing_atlas.services.positions_service import PositionsService
from swing_atlas.services.swing_dashboard_service import SwingDashboardService
from swing_atlas.services.swing_history_service import SwingHistoryService


def get_clickhouse(request: Request) -> ClickHouseRepo:
    repo: ClickHouseRepo = request.app.state.clickhouse
    return repo


def get_broker_status_service(request: Request) -> BrokerStatusService:
    service: BrokerStatusService = request.app.state.broker_status_service
    return service


def get_positions_service(request: Request) -> PositionsService:
    service: PositionsService = request.app.state.positions_service
    return service


def get_news_repo(request: Request) -> NewsRepo:
    repo: NewsRepo = request.app.state.news_repo
    return repo


def get_market_activity_repo(request: Request) -> MarketActivityRepo:
    repo: MarketActivityRepo = request.app.state.market_activity_repo
    return repo


def get_news_service(request: Request) -> NewsService:
    service: NewsService = request.app.state.news_service
    return service


def get_market_activity_service(request: Request) -> MarketActivityService:
    service: MarketActivityService = request.app.state.market_activity_service
    return service


def get_paper_trades_service(request: Request) -> PaperTradesService:
    service: PaperTradesService = request.app.state.paper_trades_service
    return service


def get_swing_dashboard_service(request: Request) -> SwingDashboardService:
    service: SwingDashboardService = request.app.state.swing_dashboard_service
    return service


def get_swing_history_service(request: Request) -> SwingHistoryService:
    service: SwingHistoryService = request.app.state.swing_history_service
    return service
