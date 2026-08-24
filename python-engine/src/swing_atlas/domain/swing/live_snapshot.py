"""Builds the WS live-strategy snapshot from pre-fetched maps -- mirrors
engine/src/api/swing.rs's build_live_strategy_snapshot/empty_live_snapshot/
format_telegram_trigger_message.

Deliberately synchronous / I/O-free: every input is already-fetched data
(quote_map, baselines, strategy_statuses, ...), matching the Rust original's
build_live_strategy_snapshot signature, which takes no AppState and isn't
async. The WS handler in services/live_strategy_ws_service.py owns the I/O
and calls this on every tick/heartbeat.
"""

from __future__ import annotations

from swing_atlas.domain.swing.candidate_builder import build_live_candidate, compute_market_regime
from swing_atlas.domain.swing.live_signal import live_signal_rank
from swing_atlas.domain.swing.models import (
    HistoricalScreenerFeatureRow,
    LiveStrategyRow,
    LiveStrategySnapshot,
    MarketRegime,
    WatchRow,
    WeeklyLabCandidate,
)
from swing_atlas.domain.swing.news_confluence import NewsConfluenceRow, apply_news_evidence
from swing_atlas.domain.swing.seeds import seed_from_quote
from swing_atlas.domain.swing.strategies import strategy_status_rank
from swing_atlas.domain.time_utils import is_regular_session_now, now_ist
from swing_atlas.repositories.dhan.market_data import QuoteItem
from swing_atlas.schemas.swing import BrokerStatus

_MAX_ROWS = 80


def empty_live_snapshot(broker: BrokerStatus, status: str, message: str) -> LiveStrategySnapshot:
    return LiveStrategySnapshot(
        event="live-strategy-snapshot",
        updated_at=now_ist().isoformat(),
        mode="dhan-websocket",
        feed_status=status,
        broker=broker,
        market_regime=MarketRegime(
            label="Live Feed Unavailable",
            tone="neutral",
            summary=message,
            advances=0,
            declines=0,
            breadth_ratio=0.0,
        ),
        total_watching=0,
        triggered=0,
        rows=[],
        message=message,
    )


def build_live_strategy_snapshot(
    broker: BrokerStatus,
    mode: str,
    feed_status: str,
    watch_rows: list[WatchRow],
    volume_map: dict[str, str],
    quote_map: dict[str, QuoteItem],
    baselines: dict[str, HistoricalScreenerFeatureRow],
    strategy_statuses: dict[str, str],
    weekly_lab_candidates: dict[str, WeeklyLabCandidate],
    news_confluence: dict[str, NewsConfluenceRow],
    message: str | None,
) -> LiveStrategySnapshot:
    regular_session = is_regular_session_now()
    seeds = [
        seed_from_quote(row, quote_map[row.security_id], volume_map)
        for row in watch_rows
        if row.security_id in quote_map
    ]
    market_regime = compute_market_regime(seeds, True)
    now = now_ist().isoformat()

    rows: list[LiveStrategyRow] = []
    for watch in watch_rows:
        quote = quote_map.get(watch.security_id)
        if quote is None:
            continue
        seed = seed_from_quote(watch, quote, volume_map)
        candidate = build_live_candidate(
            seed,
            market_regime,
            baselines.get(watch.symbol),
            strategy_statuses,
            weekly_lab_candidates.get(watch.symbol),
            regular_session,
        )
        news = news_confluence.get(watch.symbol)
        if news is not None:
            apply_news_evidence(candidate, news)
        if candidate.live_signal.strategy_id in ("unscored", "unlinked-screener"):
            continue
        rows.append(
            LiveStrategyRow(
                security_id=watch.security_id,
                symbol=candidate.symbol,
                company_name=candidate.company_name,
                strategy_id=candidate.live_signal.strategy_id,
                strategy_label=candidate.live_signal.strategy_label,
                strategy_status=candidate.live_signal.strategy_status,
                setup_family=candidate.setup_family,
                signal_status=candidate.live_signal.status,
                signal_label=candidate.live_signal.label,
                reason=candidate.live_signal.reason,
                score=candidate.score,
                last_price=candidate.last_price,
                day_change_pct=candidate.day_change_pct,
                open_gap_pct=candidate.open_gap_pct,
                volume=quote.volume,
                trigger_price=candidate.live_signal.trigger_price,
                trigger_source=candidate.live_signal.trigger_source,
                stop_loss=candidate.stop_loss,
                target_price=candidate.target_price,
                risk_reward=candidate.risk_reward,
                source=candidate.source,
                updated_at=now,
                confluence=candidate.confluence,
            )
        )

    rows.sort(
        key=lambda r: (
            live_signal_rank(r.signal_status),
            strategy_status_rank(r.strategy_status),
            -r.score,
            r.symbol,
        )
    )
    rows = rows[:_MAX_ROWS]
    triggered = sum(1 for row in rows if row.signal_status == "ENTRY_NOW")

    return LiveStrategySnapshot(
        event="live-strategy-snapshot",
        updated_at=now,
        mode=mode,
        feed_status=feed_status,
        broker=broker,
        market_regime=market_regime,
        total_watching=len(rows),
        triggered=triggered,
        rows=rows,
        message=message,
    )


def format_telegram_trigger_message(row: LiveStrategyRow, trigger: float) -> str:
    source = row.trigger_source if row.trigger_source else "strategy trigger"
    return (
        f"Swing Atlas trigger hit\n{row.symbol} - {row.strategy_label}\n"
        f"LTP Rs {row.last_price:.2f} crossed trigger Rs {trigger:.2f} ({source})\n"
        f"Stop Rs {row.stop_loss:.2f} | Target Rs {row.target_price:.2f} | "
        f"Score {row.score} | Volume {row.volume}"
    )
