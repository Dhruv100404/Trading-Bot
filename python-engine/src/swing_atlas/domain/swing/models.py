"""Value objects for the swing-scoring domain -- mirrors the structs in
engine/src/api/swing.rs (LiveSignal, CandidateSeed, MarketRegime,
HistoricalScreenerFeatureRow, WeeklyLabCandidate).
"""

from __future__ import annotations

from dataclasses import dataclass

from swing_atlas.schemas.swing import BrokerStatus


@dataclass(frozen=True, slots=True)
class LiveSignal:
    status: str
    label: str
    reason: str
    strategy_id: str
    strategy_label: str
    strategy_status: str
    setup_family: str
    score: int
    as_of: str
    trigger_price: float | None
    trigger_source: str | None


@dataclass(frozen=True, slots=True)
class CandidateSeed:
    symbol: str
    company_name: str
    tiers: list[str]
    liquidity_bucket: str
    open_price: float
    high_price: float
    low_price: float
    last_price: float
    prev_close: float
    day_volume: float
    day_change_pct: float
    open_gap_pct: float
    recovery_pct: float
    distance_to_high_pct: float
    intraday_range_pct: float
    source: str


@dataclass(frozen=True, slots=True)
class MarketRegime:
    label: str
    tone: str
    summary: str
    advances: int
    declines: int
    breadth_ratio: float


@dataclass(frozen=True, slots=True)
class HistoricalScreenerFeatureRow:
    """The daily_screener_features "baseline" row evaluate_live_signal blends
    with today's live quote. All fields optional, matching ClickHouse Nullable
    columns / a symbol with no cached history yet."""

    symbol: str | None = None
    trade_date: str | None = None
    day_open: float | None = None
    prev_close: float | None = None
    day_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    day_volume: str | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    avg_volume20: float | None = None
    high_20d: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    rsi10: float | None = None
    atr14: float | None = None
    atr_pct: float | None = None
    range_pct: float | None = None
    close_location: float | None = None
    gap_pct: float | None = None
    prior_high20: float | None = None
    prior_high55: float | None = None
    prior_high252: float | None = None
    prior_close3: float | None = None
    prior_low20: float | None = None
    ret3: float | None = None
    range_atr: float | None = None
    recovery_from_low_pct: float | None = None
    rs60_rank: float | None = None
    rs120_rank: float | None = None
    market_breadth200: float | None = None


@dataclass(frozen=True, slots=True)
class HistoricalScreenerRow:
    """A fully-resolved screener row -- output of map_historical_screener_row."""

    symbol: str
    as_of: str
    setup_family: str
    strategy_id: str
    strategy_label: str
    strategy_status: str
    score: int
    trend_label: str
    close: float
    sma20: float
    sma50: float
    avg_volume20: float
    volume_ratio: float
    distance_to_20d_high_pct: float
    distance_to_52w_high_pct: float
    range_position_pct: float
    atr14: float
    atr_pct: float
    close_location: float
    gap_pct: float
    rs60_rank: float
    rs120_rank: float
    market_breadth200: float
    planned_entry: str
    stop_loss: float
    target_price: float
    risk_reward: float


@dataclass(frozen=True, slots=True)
class WeeklyLabCandidate:
    symbol: str
    strategy_id: str
    strategy_label: str
    setup_family: str
    strategy_status: str
    signal_date: str
    trigger_price: float
    close: float
    supertrend: float
    rank_score: float
    relvol: float
    rs13w_rank: float
    body_ratio: float
    range_atr: float


@dataclass(slots=True)
class HistoricalCandle:
    """Mutable -- append_live_quote_candle updates the latest bar in place
    when a live quote lands within an already-fetched intraday session."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class HistoricalSummary:
    latest_close: float
    change_pct_1m: float
    change_pct_3m: float
    change_pct_1y: float
    high_52w: float
    low_52w: float
    avg_volume_20d: float


@dataclass(frozen=True, slots=True)
class BambooLatestSignal:
    strategy: str
    symbol: str
    signal_date: str
    planned_entry: str
    close: float
    stop: float
    target_from_close: float
    risk_multiple: float
    risk_pct_vs_close: float
    relvol: float
    range_position_52w: float
    ema20_dist_atr: float
    prior_high20: float
    prior_high55: float
    gap_pct: float
    close_loc: float
    rank_score: float


@dataclass(frozen=True, slots=True)
class WatchRow:
    security_id: str
    symbol: str
    company_name: str
    tiers: list[str]
    enabled: int
    min_volume: int


@dataclass(frozen=True, slots=True)
class SetupMix:
    family: str
    count: int
    avg_score: float


@dataclass(frozen=True, slots=True)
class StrategyMatch:
    """One strategy evaluation contributing to the current research view. A
    symbol can have both daily and weekly evaluations; `selected` identifies
    the rule that drives the live decision state."""

    timeframe: str
    strategy_id: str
    strategy_label: str
    strategy_status: str
    setup_family: str
    signal_status: str
    signal_label: str
    score: int
    selected: bool
    trigger_price: float | None
    trigger_source: str | None
    as_of: str
    reason: str


@dataclass(slots=True)
class ResearchScoreBreakdown:
    """0-100 diagnostics, not additional entry triggers; model_score is the
    selected strategy score before the (capped) news adjustment."""

    model_score: int
    technical_quality: int
    trend_quality: int
    volume_quality: int
    regime_quality: int
    risk_reward_quality: int
    catalyst_adjustment: int
    total_score: int


@dataclass(frozen=True, slots=True)
class ResearchEvidenceItem:
    """A structured reason either supporting the research thesis or
    identifying a risk -- pillar/stance is a stable UI contract."""

    pillar: str
    stance: str
    title: str
    detail: str


@dataclass(slots=True)
class NewsEvidenceSummary:
    """Bounded, recent news context. A news catalyst can adjust ranking but
    can never independently turn a non-entry signal into an entry."""

    lookback_hours: int
    article_count: int
    bullish_articles: int
    bearish_articles: int
    average_sentiment: float
    max_impact: float
    direction: str
    score_adjustment: int
    latest_reason: str | None
    latest_headline: str | None
    latest_source: str | None
    latest_url: str | None


@dataclass(slots=True)
class ResearchConfluence:
    """Shared research payload emitted by both HTTP candidates and (in M5) live
    websocket strategy rows. Separates research quality from the trade state so
    a high-ranked rejected/watch rule cannot look like an approved entry."""

    research_score: int
    research_state: str
    trade_state: str
    confluence_state: str
    pillar_count: int
    supporting_pillars: int
    conflicting_pillars: int
    score_breakdown: ResearchScoreBreakdown
    strategy_matches: list[StrategyMatch]
    evidence: list[ResearchEvidenceItem]
    risks: list[ResearchEvidenceItem]
    news: NewsEvidenceSummary


@dataclass(slots=True)
class SwingCandidate:
    symbol: str
    company_name: str
    setup_family: str
    bias: str
    score: int
    confidence: str
    regime_fit: int
    risk_reward: float
    last_price: float
    day_change_pct: float
    open_gap_pct: float
    distance_to_high_pct: float
    liquidity_bucket: str
    entry_zone: str
    stop_loss: float
    target_price: float
    expected_hold: str
    thesis: str
    reasons: list[str]
    risks: list[str]
    source: str
    live_signal: LiveSignal
    confluence: ResearchConfluence


@dataclass(frozen=True, slots=True)
class LiveStrategyRow:
    security_id: str
    symbol: str
    company_name: str
    strategy_id: str
    strategy_label: str
    strategy_status: str
    setup_family: str
    signal_status: str
    signal_label: str
    reason: str
    score: int
    last_price: float
    day_change_pct: float
    open_gap_pct: float
    volume: int
    trigger_price: float | None
    trigger_source: str | None
    stop_loss: float
    target_price: float
    risk_reward: float
    source: str
    updated_at: str
    confluence: ResearchConfluence


@dataclass(frozen=True, slots=True)
class LiveStrategySnapshot:
    event: str
    updated_at: str
    mode: str
    feed_status: str
    broker: BrokerStatus
    market_regime: MarketRegime
    total_watching: int
    triggered: int
    rows: list[LiveStrategyRow]
    message: str | None
