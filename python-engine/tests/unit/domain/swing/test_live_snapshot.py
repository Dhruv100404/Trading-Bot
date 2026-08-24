"""Tests for build_live_strategy_snapshot/empty_live_snapshot/
format_telegram_trigger_message -- mirrors the corresponding functions in
engine/src/api/swing.rs.
"""

from swing_atlas.domain.swing.live_snapshot import (
    build_live_strategy_snapshot,
    empty_live_snapshot,
    format_telegram_trigger_message,
)
from swing_atlas.domain.swing.models import (
    LiveStrategyRow,
    ResearchConfluence,
    ResearchScoreBreakdown,
    WatchRow,
)
from swing_atlas.domain.swing.research_confluence import default_news_evidence_summary
from swing_atlas.repositories.dhan.market_data import QuoteItem, QuoteOhlc
from swing_atlas.schemas.swing import BrokerStatus


def _broker(**overrides: object) -> BrokerStatus:
    defaults: dict[str, object] = dict(
        provider="DHAN", configured=True, state="ready", message="ok",
        credential_source="environment", client_id="C1", issued_at_utc=None,
        expires_at_utc=None, live_quotes=True,
    )  # fmt: skip
    defaults.update(overrides)
    return BrokerStatus(**defaults)  # type: ignore[arg-type]


def _watch_row(**overrides: object) -> WatchRow:
    defaults: dict[str, object] = dict(
        security_id="1333", symbol="DEMO", company_name="Demo Ltd", tiers=[], enabled=1,
        min_volume=100_000,
    )  # fmt: skip
    defaults.update(overrides)
    return WatchRow(**defaults)  # type: ignore[arg-type]


def test_empty_live_snapshot_shape() -> None:
    snapshot = empty_live_snapshot(_broker(), "missing-credentials", "no creds configured")

    assert snapshot.event == "live-strategy-snapshot"
    assert snapshot.feed_status == "missing-credentials"
    assert snapshot.market_regime.label == "Live Feed Unavailable"
    assert snapshot.rows == []
    assert snapshot.total_watching == 0
    assert snapshot.message == "no creds configured"


def test_format_telegram_trigger_message_includes_key_fields() -> None:
    confluence = ResearchConfluence(
        research_score=90, research_state="ENTRY_READY", trade_state="ENTRY_READY",
        confluence_state="CONFIRMED", pillar_count=0, supporting_pillars=0, conflicting_pillars=0,
        score_breakdown=ResearchScoreBreakdown(90, 80, 80, 80, 80, 80, 0, 90),
        strategy_matches=[], evidence=[], risks=[], news=default_news_evidence_summary(),
    )  # fmt: skip
    row = LiveStrategyRow(
        security_id="1333", symbol="DEMO", company_name="Demo Ltd", strategy_id="momentum-core-v1",
        strategy_label="Momentum Core", strategy_status="Candidate", setup_family="f",
        signal_status="ENTRY_NOW", signal_label="Enter Now", reason="r", score=90,
        last_price=110.0, day_change_pct=2.0, open_gap_pct=0.5, volume=500_000,
        trigger_price=109.5, trigger_source="52W high + 0.1%", stop_loss=104.0,
        target_price=120.0, risk_reward=2.0, source="dhan-live", updated_at="now",
        confluence=confluence,
    )  # fmt: skip

    message = format_telegram_trigger_message(row, trigger=109.5)

    assert "DEMO" in message
    assert "Momentum Core" in message
    assert "110.00" in message
    assert "109.50" in message
    assert "52W high + 0.1%" in message


def test_format_telegram_trigger_message_falls_back_when_no_trigger_source() -> None:
    confluence = ResearchConfluence(
        research_score=90, research_state="ENTRY_READY", trade_state="ENTRY_READY",
        confluence_state="CONFIRMED", pillar_count=0, supporting_pillars=0, conflicting_pillars=0,
        score_breakdown=ResearchScoreBreakdown(90, 80, 80, 80, 80, 80, 0, 90),
        strategy_matches=[], evidence=[], risks=[], news=default_news_evidence_summary(),
    )  # fmt: skip
    row = LiveStrategyRow(
        security_id="1333", symbol="DEMO", company_name="Demo Ltd", strategy_id="x",
        strategy_label="X", strategy_status="Candidate", setup_family="f",
        signal_status="ENTRY_NOW", signal_label="Enter Now", reason="r", score=90,
        last_price=110.0, day_change_pct=2.0, open_gap_pct=0.5, volume=500_000,
        trigger_price=109.5, trigger_source=None, stop_loss=104.0, target_price=120.0,
        risk_reward=2.0, source="dhan-live", updated_at="now", confluence=confluence,
    )  # fmt: skip

    message = format_telegram_trigger_message(row, trigger=109.5)

    assert "strategy trigger" in message


def test_build_live_strategy_snapshot_drops_rows_without_a_quote() -> None:
    watch_rows = [_watch_row(security_id="1"), _watch_row(security_id="2", symbol="OTHER")]
    quote_map = {
        "1": QuoteItem(
            last_price=100.0,
            volume=500_000,
            ohlc=QuoteOhlc(open=99.0, high=101.0, low=98.0, close=98.5),
        )
    }

    snapshot = build_live_strategy_snapshot(
        _broker(), "dhan-websocket", "streaming", watch_rows, {}, quote_map, {}, {}, {}, {}, None
    )

    assert snapshot.mode == "dhan-websocket"
    assert snapshot.feed_status == "streaming"
    # OTHER has no quote -> dropped before scoring even runs.
    assert all(row.symbol != "OTHER" for row in snapshot.rows)


def test_build_live_strategy_snapshot_drops_unscored_and_unlinked_rows() -> None:
    # A quote with no historical baseline evaluates to strategy_id="unscored"
    # in evaluate_live_signal -- build_live_strategy_snapshot must filter it
    # out entirely (it's a WS panel row, not a research candidate list).
    watch_rows = [_watch_row(security_id="1")]
    quote_map = {
        "1": QuoteItem(
            last_price=100.0,
            volume=500_000,
            ohlc=QuoteOhlc(open=99.0, high=101.0, low=98.0, close=98.5),
        )
    }

    snapshot = build_live_strategy_snapshot(
        _broker(), "dhan-websocket", "streaming", watch_rows, {}, quote_map, {}, {}, {}, {}, None
    )

    assert snapshot.rows == []
    assert snapshot.total_watching == 0
