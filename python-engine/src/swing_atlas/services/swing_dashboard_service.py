"""Swing dashboard orchestration -- mirrors engine/src/api/swing.rs's
build_dashboard_bundle plus the home/scanner/candidate_detail handler bodies.

Shares the same QuoteCache instance as PaperTradesService (see app.py's
lifespan), matching the Rust original's single process-wide state.quote_cache
used by both swing.rs and paper.rs.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from swing_atlas.config import Settings
from swing_atlas.domain.swing.candidate_builder import (
    build_live_candidate,
    compute_market_regime,
    compute_setup_mix,
)
from swing_atlas.domain.swing.live_signal import live_signal_rank
from swing_atlas.domain.swing.models import MarketRegime, SwingCandidate
from swing_atlas.domain.swing.news_confluence import apply_news_confluence
from swing_atlas.domain.swing.seeds import build_candidate_seeds
from swing_atlas.domain.time_utils import is_regular_session_now, now_ist
from swing_atlas.repositories import (
    live_signal_baseline_repo,
    news_confluence_repo,
    weekly_lab_repo,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.dhan.quote_cache import QuoteCache
from swing_atlas.repositories.screener_feature_cache_repo import load_latest_strategy_statuses
from swing_atlas.repositories.watchlist_repo import WatchlistRepo
from swing_atlas.schemas.swing import BrokerStatus
from swing_atlas.services.broker_status_service import BrokerStatusService

logger = logging.getLogger(__name__)

_WARMING_UP_REGIME = MarketRegime(
    label="Scanner Warming Up",
    tone="neutral",
    summary=(
        "No watchlist rows were found. Start the engine with ClickHouse and seed the Dhan "
        "scrip master to build the swing universe."
    ),
    advances=0,
    declines=0,
    breadth_ratio=1.0,
)


@dataclass(frozen=True, slots=True)
class DashboardBundle:
    broker: BrokerStatus
    market_regime: MarketRegime
    candidates: list[SwingCandidate]


class SwingDashboardService:
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

    async def build_dashboard_bundle(
        self, limit: int, symbol_filter: str | None
    ) -> DashboardBundle:
        watch_rows = await self._watchlist_repo.load_watch_rows(max(limit, 24), symbol_filter)
        if not watch_rows:
            broker = await self._broker_status_service.resolve_broker_status()
            return DashboardBundle(broker=broker, market_regime=_WARMING_UP_REGIME, candidates=[])

        broker = await self._broker_status_service.resolve_broker_status()
        volume_map = weekly_lab_repo.load_volume_groups_map()
        weekly_lab_candidates = weekly_lab_repo.load_weekly_lab_candidates()
        regular_session = is_regular_session_now()

        live_quote_map = None
        if broker.state == "ready":
            credentials = await self._broker_status_service.resolve_dhan_credentials()
            if credentials is not None:
                security_ids = [row.security_id for row in watch_rows]
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        quotes = await self._quote_cache.get_live_quotes(
                            client,
                            self._settings.dhan_base_url,
                            credentials.access_token,
                            credentials.client_id,
                            self._settings.dhan_quote_endpoint,
                            security_ids,
                        )
                    broker.live_quotes = True
                    broker.message = (
                        "Live Dhan quotes are available for the regular NSE session."
                        if regular_session
                        else (
                            "Dhan quotes are available outside regular NSE hours; using the "
                            "latest broker-sourced prices."
                        )
                    )
                    live_quote_map = quotes
                except Exception as exc:  # noqa: BLE001 -- degrades broker status, never fatal
                    broker.live_quotes = False
                    broker.state = "degraded"
                    broker.message = (
                        f"Credentials are configured but live quote fetch failed: {exc}"
                    )

        symbols = [row.symbol for row in watch_rows]
        try:
            strategy_statuses = await load_latest_strategy_statuses(self._ch)
        except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default()
            logger.warning("latest strategy status lookup failed: %s", exc)
            strategy_statuses = {}
        try:
            live_baselines = await live_signal_baseline_repo.load_live_signal_baselines(
                self._ch, symbols
            )
        except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default()
            logger.warning("live signal baseline lookup failed: %s", exc)
            live_baselines = {}
        try:
            news_confluence = await news_confluence_repo.load_recent_news_confluence(
                self._ch, symbols
            )
        except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default()
            logger.warning("news confluence lookup failed: %s", exc)
            news_confluence = {}

        seeds = build_candidate_seeds(watch_rows, volume_map, live_quote_map)
        market_regime = compute_market_regime(seeds, broker.live_quotes)
        candidates = [
            build_live_candidate(
                seed,
                market_regime,
                live_baselines.get(seed.symbol),
                strategy_statuses,
                weekly_lab_candidates.get(seed.symbol),
                regular_session,
            )
            for seed in seeds
        ]

        apply_news_confluence(candidates, news_confluence)
        candidates.sort(key=lambda c: (live_signal_rank(c.live_signal.status), -c.score, c.symbol))
        return DashboardBundle(
            broker=broker, market_regime=market_regime, candidates=candidates[:limit]
        )

    async def home(self) -> dict[str, Any]:
        bundle = await self.build_dashboard_bundle(16, None)
        return {
            "updated_at": now_ist().isoformat(),
            "broker": bundle.broker.model_dump(),
            "market_regime": asdict(bundle.market_regime),
            "scanner_count": len(bundle.candidates),
            "top_candidates": [asdict(c) for c in bundle.candidates[:6]],
            "setup_mix": [asdict(m) for m in compute_setup_mix(bundle.candidates)],
        }

    async def scanner(self, limit: int | None) -> dict[str, Any]:
        resolved_limit = min(max(limit if limit is not None else 24, 6), 48)
        bundle = await self.build_dashboard_bundle(resolved_limit, None)
        return {
            "updated_at": now_ist().isoformat(),
            "broker": bundle.broker.model_dump(),
            "market_regime": asdict(bundle.market_regime),
            "live_data": bundle.broker.live_quotes,
            "total_candidates": len(bundle.candidates),
            "candidates": [asdict(c) for c in bundle.candidates],
        }

    async def candidate_detail(self, symbol: str) -> dict[str, Any]:
        bundle = await self.build_dashboard_bundle(40, symbol)
        candidate = next((c for c in bundle.candidates if c.symbol.upper() == symbol.upper()), None)
        message = (
            "No swing candidates are available yet. Seed the watchlist and add a valid Dhan "
            "token to unlock live quotes."
            if not bundle.candidates
            else None
        )
        return {
            "updated_at": now_ist().isoformat(),
            "broker": bundle.broker.model_dump(),
            "market_regime": asdict(bundle.market_regime),
            "candidate": asdict(candidate) if candidate is not None else None,
            "message": message,
        }
