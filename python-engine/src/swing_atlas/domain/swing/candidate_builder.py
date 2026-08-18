"""Builds the swing dashboard's SwingCandidate rows -- mirrors
engine/src/api/swing.rs's compute_market_regime/build_live_candidate/
compute_setup_mix/*_for_family helpers.

build_candidate (a second, classify_setup_family-driven candidate builder) is
NOT ported here -- confirmed dead code in the Rust original (defined but never
called from any route handler; build_live_candidate is the only live path).
"""

from __future__ import annotations

from swing_atlas.domain.numeric import round2, round_half_away_from_zero
from swing_atlas.domain.swing.exit_plans import strategy_exit_plan
from swing_atlas.domain.swing.live_signal import (
    choose_live_signal,
    evaluate_live_signal,
    evaluate_weekly_lab_signal,
    signal_confidence,
)
from swing_atlas.domain.swing.models import (
    CandidateSeed,
    HistoricalScreenerFeatureRow,
    MarketRegime,
    SetupMix,
    SwingCandidate,
    WeeklyLabCandidate,
)
from swing_atlas.domain.swing.research_confluence import (
    build_research_confluence,
    independent_current_daily_matches,
)
from swing_atlas.domain.swing.scoring import fallback_candidate_score
from swing_atlas.domain.swing.setups import classify_setup_family


def compute_market_regime(seeds: list[CandidateSeed], live_quotes: bool) -> MarketRegime:
    advances = sum(1 for seed in seeds if seed.day_change_pct > 0.25)
    declines = sum(1 for seed in seeds if seed.day_change_pct < -0.25)
    breadth_ratio = float(advances) if declines == 0 else advances / declines

    if breadth_ratio >= 1.4:
        label, tone = "Risk-On Breadth", "bullish"
    elif breadth_ratio <= 0.8:
        label, tone = "Selective Tape", "cautious"
    else:
        label, tone = "Balanced Rotation", "neutral"

    if live_quotes:
        summary = (
            f"{label} across the active swing universe with {advances} advancing names "
            f"versus {declines} decliners."
        )
    else:
        summary = (
            "The scanner is running in fallback mode because live Dhan quotes are "
            "unavailable; regime is estimated from the curated watchlist model."
        )

    return MarketRegime(
        label=label, tone=tone, summary=summary, advances=advances, declines=declines,
        breadth_ratio=round2(breadth_ratio),
    )  # fmt: skip


def stop_buffer_for_family(family: str) -> float:
    return {
        "Breakout Continuation": 3.2,
        "Gap-and-Hold": 3.6,
        "Relative Strength Leader": 3.0,
        "Oversold Reclaim": 4.4,
    }.get(family, 3.8)


def target_buffer_for_family(family: str) -> float:
    return {
        "Breakout Continuation": 8.0,
        "Gap-and-Hold": 7.0,
        "Relative Strength Leader": 7.4,
        "Oversold Reclaim": 9.0,
    }.get(family, 6.5)


def expected_hold_for_family(family: str) -> str:
    return {
        "Breakout Continuation": "6-12 sessions",
        "Gap-and-Hold": "4-9 sessions",
        "Relative Strength Leader": "7-15 sessions",
        "Oversold Reclaim": "3-7 sessions",
    }.get(family, "5-10 sessions")


def build_live_candidate(
    seed: CandidateSeed,
    regime: MarketRegime,
    baseline: HistoricalScreenerFeatureRow | None,
    strategy_statuses: dict[str, str],
    weekly_lab: WeeklyLabCandidate | None,
    entry_window_open: bool,
) -> SwingCandidate:
    fallback_family = classify_setup_family(seed)
    fallback_score = fallback_candidate_score(seed, fallback_family, regime)
    daily_signal = evaluate_live_signal(seed, baseline, strategy_statuses, entry_window_open)
    daily_model_matches = independent_current_daily_matches(seed, baseline, strategy_statuses)
    weekly_signal = (
        evaluate_weekly_lab_signal(seed, weekly_lab, entry_window_open)
        if weekly_lab is not None
        else None
    )
    live_signal = choose_live_signal(daily_signal, weekly_signal)

    family = fallback_family if live_signal.setup_family == "Unscored" else live_signal.setup_family
    score = live_signal.score if live_signal.score > 0 else fallback_score
    confidence = signal_confidence(live_signal.status, score)

    if regime.tone == "bullish":
        regime_fit = min(max(round_half_away_from_zero(score * 0.96), 60), 95)
    elif regime.tone == "neutral":
        regime_fit = min(max(round_half_away_from_zero(score * 0.9), 55), 90)
    else:
        regime_fit = min(max(round_half_away_from_zero(score * 0.82), 50), 84)

    exit_plan = strategy_exit_plan(live_signal.strategy_id)
    if exit_plan is not None:
        target_buffer = exit_plan.target_buffer_pct
        stop_buffer = exit_plan.stop_buffer_pct
        max_hold_sessions = exit_plan.hold_label
    else:
        target_buffer = target_buffer_for_family(fallback_family)
        stop_buffer = stop_buffer_for_family(fallback_family)
        max_hold_sessions = expected_hold_for_family(fallback_family)

    planned_entry = (
        live_signal.trigger_price
        if live_signal.trigger_price is not None and live_signal.trigger_price > 0.0
        else seed.last_price
    )
    stop_loss = round2(planned_entry * (1.0 - stop_buffer / 100.0))
    target_price = round2(planned_entry * (1.0 + target_buffer / 100.0))
    risk_reward = round2((target_price - planned_entry) / max(planned_entry - stop_loss, 0.01))
    if live_signal.trigger_price is not None:
        entry_zone = f"Trigger Rs {planned_entry:.2f}"
    else:
        entry_zone = f"Rs {seed.last_price * 0.995:.2f} - Rs {seed.last_price * 1.008:.2f}"
    expected_hold = str(max_hold_sessions)

    tiers_text = ", ".join(seed.tiers) if seed.tiers else "base watchlist"
    reasons = [
        live_signal.reason,
        (
            f"{seed.symbol} is sitting {seed.distance_to_high_pct:.2f}% from the session high "
            f"with {seed.day_change_pct:+.2f}% day change and {seed.open_gap_pct:+.2f}% "
            "opening gap."
        ),
        (
            f"{seed.liquidity_bucket} liquidity bucket plus {tiers_text} tiers make execution "
            "quality more dependable."
        ),
    ]
    deduped_reasons: list[str] = []
    for reason in reasons:
        if not deduped_reasons or deduped_reasons[-1] != reason:
            deduped_reasons.append(reason)

    if live_signal.status != "ENTRY_NOW":
        second_risk = (
            "This is not an approved live entry unless the signal status changes to Enter Now."
        )
    elif regime.tone == "cautious":
        second_risk = (
            "Market breadth is not fully supportive right now, so position size should "
            "stay controlled."
        )
    else:
        second_risk = (
            "A failed move near the trigger can pull the setup back into a base-building phase."
        )
    risks = [
        f"If price loses Rs {stop_loss:.2f}, the structure weakens and the thesis should be "
        "invalidated quickly.",
        second_risk,
    ]

    if live_signal.status == "ENTRY_NOW":
        thesis = (
            f"{seed.symbol} is currently matching {live_signal.strategy_label} from the latest "
            "backtest-approved live rules."
        )
    elif live_signal.status == "WATCH":
        thesis = (
            f"{seed.symbol} matches {live_signal.strategy_label}, but the latest backtest status "
            "is watch-only, so it should be monitored rather than entered automatically."
        )
    elif live_signal.status == "INVALIDATED":
        thesis = (
            f"{seed.symbol} has lost the live rule structure and should not be treated as an "
            "entry until it rebuilds."
        )
    elif live_signal.status == "NO_TRADE":
        thesis = (
            f"{seed.symbol} is not a live entry because the matching rule is not approved by the "
            "latest backtest diagnostics."
        )
    else:
        thesis = (
            f"{seed.symbol} is on the radar, but the live data has not satisfied a "
            "backtest-approved entry rule yet."
        )

    confluence = build_research_confluence(
        seed,
        regime,
        baseline,
        daily_signal,
        daily_model_matches,
        weekly_signal,
        live_signal,
        score,
        risk_reward,
        stop_loss,
    )

    return SwingCandidate(
        symbol=seed.symbol,
        company_name=seed.company_name,
        setup_family=family,
        bias="Long",
        score=score,
        confidence=confidence,
        regime_fit=regime_fit,
        risk_reward=risk_reward,
        last_price=seed.last_price,
        day_change_pct=seed.day_change_pct,
        open_gap_pct=seed.open_gap_pct,
        distance_to_high_pct=seed.distance_to_high_pct,
        liquidity_bucket=seed.liquidity_bucket,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        target_price=target_price,
        expected_hold=expected_hold,
        thesis=thesis,
        reasons=deduped_reasons,
        risks=risks,
        source=seed.source,
        live_signal=live_signal,
        confluence=confluence,
    )


def compute_setup_mix(candidates: list[SwingCandidate]) -> list[SetupMix]:
    grouped: dict[str, tuple[int, int]] = {}
    for candidate in candidates:
        count, score_sum = grouped.get(candidate.setup_family, (0, 0))
        grouped[candidate.setup_family] = (count + 1, score_sum + candidate.score)

    mix = [
        SetupMix(family=family, count=count, avg_score=round2(score_sum / count))
        for family, (count, score_sum) in grouped.items()
    ]
    mix.sort(key=lambda m: (-m.count, -m.avg_score))
    return mix
