"""Research-confluence dossier -- mirrors the ~800-line block in
engine/src/api/swing.rs (research_trade_state through build_research_confluence).

This is deliberately separate from evaluate_live_signal: it never selects the
live entry/stop/target, it only explains and corroborates the decision that
evaluate_live_signal already made. A high-ranked rejected/watch rule must never
look like an approved entry here -- trade_state always tracks the selected
signal's status, independent daily-model "confirmations" are explicitly capped
to Candidate/Watch statuses, and collapse_research_model_matches prevents
correlated labels (e.g. three near-52w-high variants) from each casting a
separate vote.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from swing_atlas.domain.numeric import round2, round_half_away_from_zero
from swing_atlas.domain.swing.live_signal import parse_volume, replace_latest_average
from swing_atlas.domain.swing.models import (
    CandidateSeed,
    HistoricalScreenerFeatureRow,
    LiveSignal,
    MarketRegime,
    NewsEvidenceSummary,
    ResearchConfluence,
    ResearchEvidenceItem,
    ResearchScoreBreakdown,
    StrategyMatch,
)
from swing_atlas.domain.swing.scoring import (
    live_strategy_score,
    regime_quality,
    risk_reward_quality,
    technical_quality,
    trend_quality,
    volume_quality,
)
from swing_atlas.domain.swing.screener import signal_date_is_fresh
from swing_atlas.domain.swing.strategies import default_strategy_status
from swing_atlas.domain.time_utils import now_ist

# King Candle entries may remain armed for four completed weeks. Beyond that
# window the CSV is research history, not current evidence, and must never
# affect a live decision or a Top Pick.
WEEKLY_LAB_MAX_SIGNAL_AGE_DAYS = 28

_NEWS_CONFLUENCE_LOOKBACK_HOURS = 72


def research_evidence_item(
    pillar: str, stance: str, title: str, detail: str
) -> ResearchEvidenceItem:
    return ResearchEvidenceItem(pillar=pillar, stance=stance, title=title, detail=detail)


def research_trade_state(signal_status: str) -> str:
    return {
        "ENTRY_NOW": "ENTRY_READY",
        "WAIT_FOR_TRIGGER": "ARMED",
        "WATCH": "WATCH",
        "INVALIDATED": "INVALIDATED",
    }.get(signal_status, "NO_TRADE")


def research_confluence_state(trade_state: str) -> str:
    return {
        "ENTRY_READY": "CONFIRMED",
        "ARMED": "BUILDING",
        "WATCH": "WATCH_ONLY",
        "INVALIDATED": "BLOCKED",
        "NO_TRADE": "BLOCKED",
    }.get(trade_state, "MIXED")


def signal_matches_selected(signal: LiveSignal, selected: LiveSignal) -> bool:
    return (
        signal.strategy_id == selected.strategy_id
        and signal.as_of == selected.as_of
        and signal.status == selected.status
        and signal.score == selected.score
    )


def strategy_match_from_signal(timeframe: str, signal: LiveSignal, selected: bool) -> StrategyMatch:
    return StrategyMatch(
        timeframe=timeframe,
        strategy_id=signal.strategy_id,
        strategy_label=signal.strategy_label,
        strategy_status=signal.strategy_status,
        setup_family=signal.setup_family,
        signal_status=signal.status,
        signal_label=signal.label,
        score=signal.score,
        selected=selected,
        trigger_price=signal.trigger_price,
        trigger_source=signal.trigger_source,
        as_of=signal.as_of,
        reason=signal.reason,
    )


def strategy_status_is_research_valid(status: str) -> bool:
    """A confluence match is research evidence, not another order instruction.
    Only models with a current feature baseline and an approved research
    status are allowed into this list -- in particular, a rejected/fragile
    rule must not turn into a "vote" merely because its technical condition
    happens to be true today."""
    return status in ("Candidate", "Watch")


def signal_as_of_is_current(signal: LiveSignal, max_age_days: int) -> bool:
    date_token: date | None = None
    for token in reversed(signal.as_of.split()):
        try:
            date_token = date.fromisoformat(token)
            break
        except ValueError:
            continue
    if date_token is None:
        return False
    age_days = (now_ist().date() - date_token).days
    return 0 <= age_days <= max_age_days


def signal_is_current_research_confirmation(timeframe: str, signal: LiveSignal) -> bool:
    max_age_days = WEEKLY_LAB_MAX_SIGNAL_AGE_DAYS if timeframe == "weekly" else 4
    return (
        signal_as_of_is_current(signal, max_age_days)
        and strategy_status_is_research_valid(signal.strategy_status)
        and signal.strategy_id not in ("unscored", "unlinked-screener")
        and signal.status not in ("NO_TRADE", "INVALIDATED")
    )


def research_model_family_key(strategy_id: str, setup_family: str) -> str:
    """Multiple labels are often different expressions of the same price
    state. Confluence deliberately collapses correlated daily labels into one
    research family instead of treating every near-high/breakout variation as
    a separate vote. Weekly evidence remains visible as a timeframe
    confirmation of that same family."""
    if strategy_id in (
        "momentum-core-v1",
        "near-52w-high-v1",
        "near-52w-high-runner-v2",
        "near-52w-high-volume-v3",
        "swing-breakout-v1",
        "breakout-continuation-v1",
        "compression-breakout-v1",
        "rs-leader-continuation-v1",
        "weekly-supertrend-10-3",
        "king-candle-quality-v1",
    ):
        return "trend-breakout"
    if strategy_id in ("pullback-20dma-v1", "pullback-quality-v2"):
        return "pullback"
    if strategy_id in (
        "failed-breakdown-reclaim-v1",
        "rsi10-pullback-reversion-v1",
        "tuned-panic-reversal-v1",
    ):
        return "reversal"
    if setup_family.lower() == "unscored":
        return "unscored"
    return "other"


def research_model_family_label(strategy_id: str, setup_family: str) -> str:
    key = research_model_family_key(strategy_id, setup_family)
    return {
        "trend-breakout": "Trend Breakout",
        "pullback": "Pullback",
        "reversal": "Oversold Reversal",
    }.get(key, setup_family)


def strategy_match_from_current_signal(
    timeframe: str, signal: LiveSignal, selected: bool
) -> StrategyMatch | None:
    if not signal_is_current_research_confirmation(timeframe, signal):
        return None
    match = strategy_match_from_signal(timeframe, signal, selected)
    return replace(
        match, setup_family=research_model_family_label(match.strategy_id, match.setup_family)
    )


def independent_daily_confirmation(
    strategy_id: str,
    strategy_label: str,
    strategy_status: str,
    setup_family: str,
    score: int,
    as_of: str,
    trigger_price: float | None,
    trigger_source: str | None,
    reason: str,
) -> StrategyMatch | None:
    if not strategy_status_is_research_valid(strategy_status):
        return None
    is_candidate = strategy_status == "Candidate"
    return StrategyMatch(
        timeframe="daily",
        strategy_id=strategy_id,
        strategy_label=strategy_label,
        strategy_status=strategy_status,
        setup_family=research_model_family_label(strategy_id, setup_family),
        # These are explicitly research confirmations. The selected live
        # signal remains the sole authority for ENTRY_NOW / trade_state.
        signal_status="WAIT_FOR_TRIGGER" if is_candidate else "WATCH",
        signal_label="Independent confirmation" if is_candidate else "Research watch confirmation",
        score=score,
        selected=False,
        trigger_price=trigger_price,
        trigger_source=trigger_source,
        as_of=as_of,
        reason=reason,
    )


def model_confirmation_should_replace(candidate: StrategyMatch, existing: StrategyMatch) -> bool:
    if candidate.selected != existing.selected:
        return candidate.selected
    candidate_is_candidate = candidate.strategy_status == "Candidate"
    existing_is_candidate = existing.strategy_status == "Candidate"
    if candidate_is_candidate != existing_is_candidate:
        return candidate_is_candidate
    return candidate.score > existing.score or (
        candidate.score == existing.score and candidate.strategy_id < existing.strategy_id
    )


def collapse_research_model_matches(matches: list[StrategyMatch]) -> list[StrategyMatch]:
    collapsed: list[StrategyMatch] = []
    for match in matches:
        family = research_model_family_key(match.strategy_id, match.setup_family)
        # Weekly evidence is retained as a timeframe confirmation, but it
        # shares its same model-family key with the corresponding daily
        # signal. That keeps it visible in the dossier without granting a
        # second independent-model vote to the Top Picks ranking.
        existing_index = next(
            (
                i
                for i, existing in enumerate(collapsed)
                if existing.timeframe == match.timeframe
                and research_model_family_key(existing.strategy_id, existing.setup_family) == family
            ),
            None,
        )
        if existing_index is None:
            collapsed.append(match)
        elif model_confirmation_should_replace(match, collapsed[existing_index]):
            collapsed[existing_index] = match

    collapsed.sort(key=lambda m: (not m.selected, m.timeframe, m.strategy_label))
    return collapsed


def independent_current_daily_matches(
    seed: CandidateSeed,
    baseline: HistoricalScreenerFeatureRow | None,
    strategy_statuses: dict[str, str],
) -> list[StrategyMatch]:
    """Evaluate the current quote against the distinct, already-defined daily
    models. This is intentionally separate from evaluate_live_signal: it
    provides research corroboration only and never participates in selecting
    the live entry, stop, target, or trade state."""
    if baseline is None:
        return []
    baseline_date = baseline.trade_date
    if baseline_date is None or not signal_date_is_fresh(baseline_date):
        return []

    historical_close = max(
        baseline.day_close if baseline.day_close is not None else seed.prev_close, 0.01
    )
    close = seed.last_price
    high = max(seed.high_price, seed.last_price)
    low = min(seed.low_price, seed.last_price)
    sma20 = replace_latest_average(
        baseline.sma20 if baseline.sma20 is not None else historical_close,
        historical_close,
        close,
        20.0,
    )
    sma50 = replace_latest_average(
        baseline.sma50 if baseline.sma50 is not None else historical_close,
        historical_close,
        close,
        50.0,
    )
    sma200 = replace_latest_average(
        baseline.sma200 if baseline.sma200 is not None else historical_close,
        historical_close,
        close,
        200.0,
    )
    avg_volume20 = max(baseline.avg_volume20 if baseline.avg_volume20 is not None else 0.0, 1.0)
    high_52w = max(baseline.high_52w if baseline.high_52w is not None else high, high, 0.01)
    low_52w = min(baseline.low_52w if baseline.low_52w is not None else low, low)
    high_20d = max(baseline.high_20d if baseline.high_20d is not None else high, high)
    if close <= 0.0 or high_20d <= 0.0 or low_52w <= 0.0:
        return []

    day_volume = seed.day_volume if seed.day_volume > 0.0 else parse_volume(baseline.day_volume)
    volume_ratio = day_volume / avg_volume20
    prior_high20 = (
        baseline.prior_high20
        if baseline.prior_high20 is not None and baseline.prior_high20 > 0.0
        else high_20d
    )
    prior_high55 = (
        baseline.prior_high55
        if baseline.prior_high55 is not None and baseline.prior_high55 > 0.0
        else prior_high20
    )
    prior_high252 = (
        baseline.prior_high252
        if baseline.prior_high252 is not None and baseline.prior_high252 > 0.0
        else high_52w
    )
    prior_low20 = (
        baseline.prior_low20
        if baseline.prior_low20 is not None and baseline.prior_low20 > 0.0
        else low
    )
    prior_close3 = (
        baseline.prior_close3
        if baseline.prior_close3 is not None and baseline.prior_close3 > 0.0
        else close
    )
    rsi10 = baseline.rsi10 if baseline.rsi10 is not None else 50.0
    rs60_rank = min(max(baseline.rs60_rank if baseline.rs60_rank is not None else 0.5, 0.0), 1.0)
    rs120_rank = min(max(baseline.rs120_rank if baseline.rs120_rank is not None else 0.5, 0.0), 1.0)
    atr14 = max(baseline.atr14 if baseline.atr14 is not None else abs(high - low), close * 0.01)
    close_location = min(max((close - low) / (high - low), 0.0), 1.0) if high > low else 0.5
    range_atr = max(high - low, 0.0) / atr14 if atr14 > 0.0 else 0.0
    recovery_from_low_pct = max(close - low, 0.0) / low if low > 0.0 else 0.0
    ret3 = close / prior_close3 - 1.0 if prior_close3 > 0.0 else 0.0
    breakout_pct = ((high_20d - close) / high_20d) * 100.0
    distance_to_52w_high_pct = ((high_52w - close) / high_52w) * 100.0
    range_span = max(high_52w - low_52w, 0.01)
    range_position_pct = ((close - low_52w) / range_span) * 100.0
    trend_up = close > sma20 and sma20 > sma50
    rsi10_pullback = close > sma200 and rsi10 < 30.0
    breakout_close = close > prior_high20 and close_location >= 0.60
    compression_breakout = (
        breakout_close
        and volume_ratio >= 1.05
        and (atr14 / close) < 0.08
        and (max(high - low, 0.0) / close) <= max((atr14 / close) * 1.05, 0.015)
    )
    failed_breakdown_reclaim = (
        low < prior_low20 and close > prior_low20 and close_location >= 0.65 and volume_ratio >= 0.8
    )
    tuned_panic_reversal = (
        ret3 <= -0.08
        and range_atr >= 1.35
        and close_location >= 0.64
        and recovery_from_low_pct >= 0.012
        and atr14 > 0.0
    )
    relative_strength_leader = (
        rs60_rank >= 0.75
        and rs120_rank >= 0.65
        and close > sma50
        and (distance_to_52w_high_pct <= 10.0 or close > prior_high55 or close > prior_high252)
    )
    score = live_strategy_score(
        trend_up,
        breakout_pct,
        distance_to_52w_high_pct,
        volume_ratio,
        close >= sma20 * 0.98 and close <= sma20 * 1.03,
        range_position_pct,
    )
    lost_structure = close < sma50 * 0.985 or range_position_pct < 40.0
    if lost_structure:
        return []

    as_of = f"{seed.source} / baseline {baseline_date}"

    def status_for(strategy_id: str) -> str:
        return strategy_statuses.get(strategy_id, default_strategy_status(strategy_id))

    matches: list[StrategyMatch] = []
    if distance_to_52w_high_pct <= 3.0 and range_position_pct >= 85.0 and trend_up and score >= 92:
        match = independent_daily_confirmation(
            "momentum-core-v1",
            "Momentum Core",
            status_for("momentum-core-v1"),
            "Trend Breakout",
            score,
            as_of,
            round2(high_52w * 1.001),
            "52W high + 0.1%",
            f"Trend, 52W range position {range_position_pct:.0f}%, and current volume "
            f"{volume_ratio:.2f}x its 20D average meet Momentum Core conditions.",
        )
        if match is not None:
            matches.append(match)
    if breakout_close and trend_up and volume_ratio >= 1.1 and score >= 88:
        match = independent_daily_confirmation(
            "breakout-continuation-v1",
            "Breakout Continuation",
            status_for("breakout-continuation-v1"),
            "Trend Breakout",
            score,
            as_of,
            round2(prior_high20 * 1.001),
            "Prior 20D high + 0.1%",
            f"Close is above the prior 20D high with {volume_ratio:.2f}x volume and a "
            f"{close_location * 100.0:.0f}% close location.",
        )
        if match is not None:
            matches.append(match)
    if compression_breakout and score >= 88:
        match = independent_daily_confirmation(
            "compression-breakout-v1",
            "Compression Breakout",
            status_for("compression-breakout-v1"),
            "Trend Breakout",
            score,
            as_of,
            round2(prior_high20 * 1.001),
            "Prior 20D high + 0.1%",
            f"A tight {range_atr:.2f} ATR range broke above the prior 20D high on "
            f"{volume_ratio:.2f}x volume.",
        )
        if match is not None:
            matches.append(match)
    if relative_strength_leader and score >= 86:
        match = independent_daily_confirmation(
            "rs-leader-continuation-v1",
            "RS Leader Continuation",
            status_for("rs-leader-continuation-v1"),
            "Relative Strength",
            score,
            as_of,
            round2(prior_high55 * 1.001),
            "Prior 55D high + 0.1%",
            f"RS60 is {rs60_rank * 100.0:.0f}%, RS120 is {rs120_rank * 100.0:.0f}%, and price "
            "remains above SMA50.",
        )
        if match is not None:
            matches.append(match)
    if rsi10_pullback:
        match = independent_daily_confirmation(
            "rsi10-pullback-reversion-v1",
            "RSI10 Pullback",
            status_for("rsi10-pullback-reversion-v1"),
            "Oversold Reversal",
            score,
            as_of,
            None,
            None,
            f"RSI10 is {rsi10:.1f} while price remains above SMA200, meeting the oversold "
            "reversion screen.",
        )
        if match is not None:
            matches.append(match)
    if failed_breakdown_reclaim and score >= 86:
        match = independent_daily_confirmation(
            "failed-breakdown-reclaim-v1",
            "Failed Breakdown Reclaim",
            status_for("failed-breakdown-reclaim-v1"),
            "Failed Breakdown Reclaim",
            score,
            as_of,
            round2(prior_low20),
            "Prior 20D low reclaim",
            f"Price undercut then reclaimed the prior 20D low with a {close_location * 100.0:.0f}% "
            f"close location and {volume_ratio:.2f}x volume.",
        )
        if match is not None:
            matches.append(match)
    if tuned_panic_reversal:
        match = independent_daily_confirmation(
            "tuned-panic-reversal-v1",
            "Panic Reversal Lab",
            status_for("tuned-panic-reversal-v1"),
            "Oversold Reversal",
            score,
            as_of,
            round2(low + 0.25 * (high - low)),
            "25% recovery from intraday low",
            f"The 3D move is {ret3 * 100.0:+.1f}%, range is {range_atr:.2f} ATR, and the close "
            f"has recovered {recovery_from_low_pct * 100.0:.1f}% from the low.",
        )
        if match is not None:
            matches.append(match)

    return matches


def refresh_confluence_pillar_counts(confluence: ResearchConfluence) -> None:
    supporting = {item.pillar for item in confluence.evidence if item.stance == "SUPPORT"}
    conflicting = {item.pillar for item in confluence.risks if item.stance == "RISK"}
    confluence.supporting_pillars = len(supporting)
    confluence.pillar_count = confluence.supporting_pillars
    confluence.conflicting_pillars = len(conflicting)


def default_news_evidence_summary() -> NewsEvidenceSummary:
    return NewsEvidenceSummary(
        lookback_hours=_NEWS_CONFLUENCE_LOOKBACK_HOURS,
        article_count=0,
        bullish_articles=0,
        bearish_articles=0,
        average_sentiment=0.0,
        max_impact=0.0,
        direction="NONE",
        score_adjustment=0,
        latest_reason=None,
        latest_headline=None,
        latest_source=None,
        latest_url=None,
    )


def build_research_confluence(
    seed: CandidateSeed,
    regime: MarketRegime,
    baseline: HistoricalScreenerFeatureRow | None,
    daily_signal: LiveSignal,
    daily_model_matches: list[StrategyMatch],
    weekly_signal: LiveSignal | None,
    selected_signal: LiveSignal,
    model_score: int,
    risk_reward: float,
    stop_loss: float,
) -> ResearchConfluence:
    trade_state = research_trade_state(selected_signal.status)
    technical = technical_quality(seed, selected_signal, model_score)
    trend = trend_quality(daily_signal, weekly_signal)
    volume = volume_quality(seed, baseline)
    regime_score = regime_quality(regime)
    rr_score = risk_reward_quality(risk_reward)

    strategy_matches = list(daily_model_matches)
    daily_match = strategy_match_from_current_signal(
        "daily", daily_signal, signal_matches_selected(daily_signal, selected_signal)
    )
    if daily_match is not None:
        strategy_matches.append(daily_match)
    if weekly_signal is not None:
        weekly_match = strategy_match_from_current_signal(
            "weekly", weekly_signal, signal_matches_selected(weekly_signal, selected_signal)
        )
        if weekly_match is not None:
            strategy_matches.append(weekly_match)
    # Keep only genuine, fresh model-family confirmations. The selected signal
    # is added above only when it meets the same validity rule, so a rejected
    # or stale rule remains visible as a decision risk rather than a
    # misleading strategy agreement.
    strategy_matches = collapse_research_model_matches(strategy_matches)

    evidence: list[ResearchEvidenceItem] = []
    risks: list[ResearchEvidenceItem] = []

    if technical >= 60:
        evidence.append(
            research_evidence_item(
                "technical",
                "SUPPORT",
                "Price structure is constructive",
                f"LTP is {seed.distance_to_high_pct:.2f}% from the session high with a "
                f"{seed.day_change_pct:+.2f}% day move.",
            )
        )
    else:
        risks.append(
            research_evidence_item(
                "technical",
                "RISK",
                "Price structure needs confirmation",
                f"LTP is {seed.distance_to_high_pct:.2f}% from the session high with a "
                f"{seed.day_change_pct:+.2f}% day move.",
            )
        )

    if trend >= 60:
        weekly_suffix = (
            f"; weekly: {weekly_signal.strategy_label} ({weekly_signal.label})"
            if weekly_signal is not None
            else ""
        )
        evidence.append(
            research_evidence_item(
                "trend",
                "SUPPORT",
                "Trend evidence is present",
                f"Daily: {daily_signal.strategy_label} ({daily_signal.label}){weekly_suffix}.",
            )
        )
    else:
        risks.append(
            research_evidence_item(
                "trend", "RISK", "Trend evidence is weak or invalidated", selected_signal.reason
            )
        )

    volume_detail = (
        f"{seed.liquidity_bucket} liquidity bucket; live volume is "
        f"{round_half_away_from_zero(seed.day_volume)}."
    )
    if volume >= 60:
        evidence.append(
            research_evidence_item(
                "volume", "SUPPORT", "Liquidity or volume supports execution", volume_detail
            )
        )
    else:
        risks.append(
            research_evidence_item(
                "volume", "RISK", "Volume confirmation is limited", volume_detail
            )
        )

    if regime.tone == "bullish":
        evidence.append(
            research_evidence_item(
                "regime", "SUPPORT", "Market breadth is supportive", regime.summary
            )
        )
    elif regime.tone == "cautious":
        risks.append(
            research_evidence_item("regime", "RISK", "Market breadth is cautious", regime.summary)
        )

    if rr_score >= 65:
        evidence.append(
            research_evidence_item(
                "risk_reward",
                "SUPPORT",
                "Risk/reward plan is defined",
                f"Planned reward/risk is {risk_reward:.2f}R.",
            )
        )
    else:
        risks.append(
            research_evidence_item(
                "risk_reward",
                "RISK",
                "Risk/reward plan is below target",
                f"Planned reward/risk is {risk_reward:.2f}R.",
            )
        )
    risks.append(
        research_evidence_item(
            "risk_management",
            "RISK",
            "Structural invalidation level",
            f"The thesis weakens below Rs {stop_loss:.2f}.",
        )
    )

    if trade_state == "ENTRY_READY":
        evidence.append(
            research_evidence_item(
                "decision", "SUPPORT", "Live entry gate is cleared", selected_signal.reason
            )
        )
    elif trade_state == "ARMED":
        risks.append(
            research_evidence_item(
                "decision", "RISK", "Trigger or session gate is still open", selected_signal.reason
            )
        )
    elif trade_state == "WATCH":
        risks.append(
            research_evidence_item(
                "decision", "RISK", "Strategy is watch-only", selected_signal.reason
            )
        )
    elif trade_state == "INVALIDATED":
        risks.append(
            research_evidence_item(
                "decision", "RISK", "Live structure is invalidated", selected_signal.reason
            )
        )
    else:
        risks.append(
            research_evidence_item(
                "decision",
                "RISK",
                "Research rank is not a trade approval",
                f"{selected_signal.strategy_label} is {selected_signal.strategy_status} in the "
                f"latest strategy diagnostics. {selected_signal.reason}",
            )
        )

    confluence = ResearchConfluence(
        research_score=model_score,
        research_state=trade_state,
        trade_state=trade_state,
        confluence_state=research_confluence_state(trade_state),
        pillar_count=0,
        supporting_pillars=0,
        conflicting_pillars=0,
        score_breakdown=ResearchScoreBreakdown(
            model_score=model_score,
            technical_quality=technical,
            trend_quality=trend,
            volume_quality=volume,
            regime_quality=regime_score,
            risk_reward_quality=rr_score,
            catalyst_adjustment=0,
            total_score=model_score,
        ),
        strategy_matches=strategy_matches,
        evidence=evidence,
        risks=risks,
        news=default_news_evidence_summary(),
    )
    refresh_confluence_pillar_counts(confluence)
    return confluence
