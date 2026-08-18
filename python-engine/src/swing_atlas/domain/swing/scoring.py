"""Swing-candidate scoring -- mirrors engine/src/api/swing.rs's live_strategy_score,
fallback_candidate_score, and the 5 "research confluence" quality diagnostics
(signal/technical/trend/volume/regime/risk_reward_quality).

The 5 quality scores are DISPLAY-ONLY diagnostics for the research-confluence
panel -- they do not feed back into a candidate's live entry/exit decision or its
`score` field. `total_score` is `model_score` plus/minus the news catalyst
adjustment, nothing else. Preserve this distinction when wiring these in --
blending the quality scores into a combined number would be a behavior change
from the original, not just a refactor.
"""

from __future__ import annotations

from swing_atlas.domain.numeric import round_half_away_from_zero
from swing_atlas.domain.swing.models import (
    CandidateSeed,
    HistoricalScreenerFeatureRow,
    LiveSignal,
    MarketRegime,
)


def live_strategy_score(
    trend_up: bool,
    breakout_pct: float,
    distance_to_52w_high_pct: float,
    volume_ratio: float,
    pullback_zone: bool,
    range_position_pct: float,
) -> int:
    score = 50.0
    if trend_up:
        score += 18.0
    if breakout_pct <= 1.5:
        score += 14.0
    elif breakout_pct <= 4.0:
        score += 8.0
    if distance_to_52w_high_pct <= 8.0:
        score += 10.0
    if volume_ratio >= 1.2:
        score += 10.0
    elif volume_ratio >= 1.0:
        score += 5.0
    if pullback_zone:
        score += 8.0
    if range_position_pct >= 70.0:
        score += 6.0
    return max(50, min(96, round_half_away_from_zero(score)))


def fallback_candidate_score(seed: CandidateSeed, family: str, regime: MarketRegime) -> int:
    liquidity_bonus = {"MEGA": 12.0, "LARGE": 8.0, "MID": 4.0}.get(seed.liquidity_bucket, 2.0)
    family_bonus = {
        "Breakout Continuation": 18.0,
        "Gap-and-Hold": 16.0,
        "Relative Strength Leader": 15.0,
        "Pullback To Support": 13.0,
        "Oversold Reclaim": 11.0,
    }.get(family, 10.0)
    regime_bonus = 8.0 if regime.tone == "bullish" else 5.0 if regime.tone == "neutral" else 2.0
    action_bonus = max(seed.day_change_pct, 0.0) * 8.0 + seed.recovery_pct * 4.5
    tightness_bonus = max((2.5 - max(0.0, min(2.5, seed.distance_to_high_pct))) * 6.0, 0.0)
    range_penalty = max(seed.intraday_range_pct - 3.5, 0.0) * 3.5
    tier_bonus = 6.0 if "Tier1" in seed.tiers else 0.0

    raw = (
        44.0
        + family_bonus
        + regime_bonus
        + liquidity_bonus
        + action_bonus
        + tightness_bonus
        + tier_bonus
        - range_penalty
    )
    return max(58, min(96, round_half_away_from_zero(raw)))


def signal_quality(signal: LiveSignal) -> int:
    if signal.status == "INVALIDATED":
        return 15
    if signal.strategy_status == "Rejected" or signal.status == "NO_TRADE":
        return 30
    if signal.status == "ENTRY_NOW":
        return 92
    if signal.status == "WATCH":
        return 68
    if signal.strategy_id == "unscored":
        return 35
    return 76


def technical_quality(seed: CandidateSeed, selected: LiveSignal, model_score: int) -> int:
    quality = 35
    if seed.distance_to_high_pct <= 1.5:
        quality += 25
    elif seed.distance_to_high_pct <= 4.0:
        quality += 14
    if seed.day_change_pct >= 2.0:
        quality += 15
    elif seed.day_change_pct >= 0.5:
        quality += 9
    elif seed.day_change_pct < -1.5:
        quality -= 12
    if seed.recovery_pct >= 1.0:
        quality += 8
    quality += min(max(model_score - 50, 0) // 4, 12)
    if selected.status == "INVALIDATED":
        quality -= 30
    return max(10, min(100, quality))


def trend_quality(daily: LiveSignal, weekly: LiveSignal | None) -> int:
    daily_quality = signal_quality(daily)
    if weekly is None:
        return daily_quality
    weekly_quality = signal_quality(weekly)
    if daily.strategy_id == "unscored":
        return weekly_quality
    return (daily_quality + weekly_quality) // 2


def volume_quality(seed: CandidateSeed, baseline: HistoricalScreenerFeatureRow | None) -> int:
    if baseline is not None:
        avg_volume = baseline.avg_volume20 or 0.0
        if avg_volume > 0.0 and seed.day_volume > 0.0:
            ratio = seed.day_volume / avg_volume
            if ratio >= 1.5:
                return 95
            if ratio >= 1.2:
                return 82
            if ratio >= 1.0:
                return 68
            if ratio >= 0.75:
                return 48
            return 28

    return {"MEGA": 70, "LARGE": 62, "MID": 52}.get(seed.liquidity_bucket, 42)


def regime_quality(regime: MarketRegime) -> int:
    return {"bullish": 85, "neutral": 65, "cautious": 40}.get(regime.tone, 50)


def risk_reward_quality(risk_reward: float) -> int:
    if risk_reward >= 4.0:
        return 100
    if risk_reward >= 3.0:
        return 90
    if risk_reward >= 2.0:
        return 80
    if risk_reward >= 1.5:
        return 65
    return 40
