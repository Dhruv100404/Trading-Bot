"""Value objects for the market-activity domain -- mirrors engine/src/market_activity.rs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketActivitySource:
    source: str
    metric_type: str
    exchange: str
    index_name: str
    url: str


@dataclass(frozen=True, slots=True)
class MarketActivityRow:
    row_id: str
    snapshot_at: str
    trading_date: str
    source: str
    metric_type: str
    exchange: str
    index_name: str
    rank: int
    symbol: str
    stock_name: str
    moneycontrol_id: str
    slug: str
    price: float
    change_abs: float
    change_pct: float
    day_high: float
    day_low: float
    open: float
    prev_close: float
    volume: int
    avg_volume: int
    volume_multiplier: float
    volume_change_pct: float
    value_cr: float
    vwap: float
    direction: str
    mcap_cr: float
    month_return_pct: float
    month3_return_pct: float
    share_url: str
    source_url: str
    fetched_at: str


def default_sources() -> list[MarketActivitySource]:
    return [
        MarketActivitySource(
            source="moneycontrol",
            metric_type="volume_shockers",
            exchange="NSE",
            index_name="all",
            url="https://www.moneycontrol.com/stocks/market-stats/volume-shockers-nse/",
        ),
        MarketActivitySource(
            source="moneycontrol",
            metric_type="most_active",
            exchange="NSE",
            index_name="all",
            url="https://www.moneycontrol.com/stocks/market-stats/most-active-stocks-nse/",
        ),
    ]


def parse_configured_sources(raw: str) -> list[MarketActivitySource]:
    """Parses MARKET_ACTIVITY_SOURCES ('source|metric_type|exchange|index|url' entries,
    ';'-separated). Falls back to default_sources() if raw is blank or nothing parses."""
    if not raw.strip():
        return default_sources()

    sources: list[MarketActivitySource] = []
    for entry in raw.split(";"):
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) != 5:
            continue
        sources.append(
            MarketActivitySource(
                source=parts[0],
                metric_type=parts[1],
                exchange=parts[2],
                index_name=parts[3],
                url=parts[4],
            )
        )

    return sources if sources else default_sources()
