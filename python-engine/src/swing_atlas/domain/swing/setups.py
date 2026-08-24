"""Fallback setup classification -- mirrors engine/src/api/swing.rs::classify_setup_family.

Used only as a fallback inside build_live_candidate when the primary
evaluate_live_signal path can't classify a setup (e.g. no historical baseline
yet) -- not the main live-signal setup-family determination, which lives in
domain/swing/live_signal.py (it needs the historical feature baseline this
classifier deliberately doesn't require).
"""

from __future__ import annotations

from swing_atlas.domain.swing.models import CandidateSeed


def classify_setup_family(seed: CandidateSeed) -> str:
    if seed.day_change_pct >= 2.3 and seed.distance_to_high_pct <= 0.8:
        return "Breakout Continuation"
    if seed.open_gap_pct >= 1.0 and seed.day_change_pct >= 1.0:
        return "Gap-and-Hold"
    if seed.day_change_pct >= 0.7 and seed.recovery_pct >= 1.0:
        return "Relative Strength Leader"
    if seed.day_change_pct <= -0.6 and seed.recovery_pct >= 1.3:
        return "Oversold Reclaim"
    return "Pullback To Support"
