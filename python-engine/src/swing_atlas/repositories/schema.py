"""ClickHouse DDL for tables the Rust engine created at startup via embedded
`CREATE TABLE IF NOT EXISTS` (rather than init/*.sql) -- mirrors
engine/src/api/news.rs::ensure_news_schema and
engine/src/api/market_activity.rs::ensure_market_activity_schema.

Consolidating these here (rather than scattering CREATE TABLE strings across
service modules, as the Rust original does) is a deliberate improvement -- see
the migration plan's note about eventually moving all of this into versioned
init/*.sql files once every module has been ported.
"""

from __future__ import annotations

from swing_atlas.repositories.clickhouse import ClickHouseRepo

NEWS_ARTICLES = """
CREATE TABLE IF NOT EXISTS trading.news_articles (
    article_id String,
    source LowCardinality(String),
    source_kind LowCardinality(String),
    category LowCardinality(String),
    title String,
    url String,
    summary String,
    published_at Nullable(DateTime('Asia/Kolkata')),
    fetched_at DateTime('Asia/Kolkata'),
    inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(fetched_at)
ORDER BY (source, article_id)
TTL fetched_at + INTERVAL 45 DAY
"""

NEWS_MENTIONS = """
CREATE TABLE IF NOT EXISTS trading.news_mentions (
    article_id String,
    symbol String,
    security_id String,
    company_name String,
    match_confidence Float32,
    matched_text String,
    inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (article_id, symbol)
TTL inserted_at + INTERVAL 45 DAY
"""

NEWS_SCORES = """
CREATE TABLE IF NOT EXISTS trading.news_scores (
    article_id String,
    symbol String,
    sentiment Float32,
    impact_score Float32,
    direction LowCardinality(String),
    horizon LowCardinality(String),
    confidence Float32,
    reason String,
    model LowCardinality(String),
    inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (article_id, symbol)
TTL inserted_at + INTERVAL 45 DAY
"""

NSE_LARGE_DEALS = """
CREATE TABLE IF NOT EXISTS trading.nse_large_deals (
    deal_id String,
    deal_type LowCardinality(String),
    deal_date Date,
    deal_date_raw String,
    symbol String,
    security_name String,
    client_name String,
    side LowCardinality(String),
    quantity Float64,
    price Float64,
    value_lakh Float64,
    source_url String,
    fetched_at DateTime('Asia/Kolkata'),
    inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(deal_date)
ORDER BY (deal_date, deal_type, symbol, client_name, side, deal_id)
TTL deal_date + INTERVAL 10 YEAR
"""

# Older deployments created nse_large_deals with a 180-day ingestion-time TTL,
# which made long-horizon deal research impossible. Idempotent -- safe to run
# on every startup, matching the Rust original.
NSE_LARGE_DEALS_TTL_MIGRATION = """
ALTER TABLE trading.nse_large_deals
MODIFY TTL deal_date + INTERVAL 10 YEAR
"""

CORPORATE_EVENTS = """
CREATE TABLE IF NOT EXISTS trading.corporate_events (
    event_id String,
    source LowCardinality(String),
    source_event_id String,
    symbol String,
    company_name String,
    event_time DateTime64(3, 'Asia/Kolkata'),
    event_date Date,
    event_category LowCardinality(String),
    catalyst_score UInt8,
    title String,
    summary String,
    attachment_url String,
    source_url String,
    inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(event_date)
ORDER BY (symbol, event_date, event_category, event_id)
"""

# trading.backtest_trades is intentionally NOT redefined here -- it's already created
# by init/02_backtests.sql (auto-run by ClickHouse's docker-entrypoint-initdb.d), and
# the two definitions were byte-identical duplicates in the Rust original
# (engine/src/api/backtest.rs's embedded CREATE_BACKTEST_TRADES vs the SQL file).
# daily_backtest_features has no init/*.sql home -- it's created here, matching how
# the Rust engine created it at runtime.
DAILY_BACKTEST_FEATURES = """
CREATE TABLE IF NOT EXISTS trading.daily_backtest_features (
    symbol                    String,
    trade_date                Date,
    rn                        UInt32,
    day_open                  Float64,
    day_high                  Float64,
    day_low                   Float64,
    day_close                 Float64,
    day_volume                Float64,
    sma20                     Float64,
    sma50                     Float64,
    sma200                    Float64,
    avg_volume20              Float64,
    high_20d                  Float64,
    high_52w                  Float64,
    low_52w                   Float64,
    rsi10                     Float64,
    breakout_pct              Float64,
    distance_to_52w_high_pct  Float64,
    range_position_pct        Float64,
    volume_ratio              Float64,
    trend_up                  UInt8,
    pullback_zone             UInt8,
    rsi10_pullback            UInt8,
    score                     UInt8,
    refreshed_at              DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(refreshed_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol, trade_date)
"""

DAILY_SCREENER_FEATURES = """
CREATE TABLE IF NOT EXISTS trading.daily_screener_features (
    trade_date     Date,
    symbol         String,
    day_open       Float64,
    day_high       Float64,
    day_low        Float64,
    day_close      Float64,
    prev_close     Float64,
    day_volume     UInt64,
    sma20          Float64,
    sma50          Float64,
    sma200         Float64,
    avg_volume20   Float64,
    high_20d       Float64,
    high_52w       Float64,
    low_52w        Float64,
    rsi10          Float64,
    atr14          Float64,
    atr_pct        Float64,
    range_pct      Float64,
    close_location Float64,
    gap_pct        Float64,
    prior_high20   Float64,
    prior_high55   Float64,
    prior_high252  Float64,
    prior_close3   Float64,
    prior_low20    Float64,
    ret3           Float64,
    range_atr      Float64,
    recovery_from_low_pct Float64,
    rs60_rank      Float64,
    rs120_rank     Float64,
    market_breadth200 Float64,
    refreshed_at   DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(refreshed_at)
ORDER BY (trade_date, symbol)
"""

_SCREENER_FEATURE_ALTER = (
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS {} Float64 DEFAULT 0"
)
DAILY_SCREENER_FEATURES_MIGRATIONS = tuple(
    _SCREENER_FEATURE_ALTER.format(column)
    for column in (
        "atr14",
        "atr_pct",
        "range_pct",
        "close_location",
        "gap_pct",
        "prior_high20",
        "prior_high55",
        "prior_high252",
        "prior_close3",
        "prior_low20",
        "ret3",
        "range_atr",
        "recovery_from_low_pct",
        "rs60_rank",
        "rs120_rank",
        "market_breadth200",
    )  # fmt: skip
)

SIGNAL_LEDGER = """
CREATE TABLE IF NOT EXISTS trading.signal_ledger (
    signal_key       String,
    symbol           String,
    strategy_id      String,
    strategy_label   String,
    strategy_status  String,
    setup_family     String,
    signal_date      String,
    first_seen_at    DateTime DEFAULT now(),
    last_seen_at     DateTime DEFAULT now(),
    entry_price      Float64,
    quantity         UInt32,
    stop_loss        Float64,
    target_price     Float64,
    score            UInt8,
    source           String DEFAULT 'historical-screener',
    status           String DEFAULT 'active',
    paper_status     String DEFAULT 'staged',
    close_reason     String DEFAULT '',
    realized_pnl     Float64 DEFAULT 0,
    inserted_at      DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY signal_key
"""

PAPER_TRADES = """
CREATE TABLE IF NOT EXISTS trading.paper_trades (
    symbol            String,
    company_name      String,
    setup_family      String,
    bias              String DEFAULT 'Long',
    entry_price       Float64,
    quantity          UInt32 DEFAULT 1,
    stop_loss         Float64,
    target_price      Float64,
    planned_at        DateTime DEFAULT now(),
    max_sessions      UInt16 DEFAULT 7,
    capital_allocated Float64 DEFAULT 50000,
    expected_hold     String DEFAULT '',
    thesis            String DEFAULT '',
    notes             String DEFAULT '',
    exit_price        Nullable(Float64),
    closed_at         Nullable(DateTime),
    close_reason      String DEFAULT '',
    realized_pnl      Float64 DEFAULT 0,
    enabled           UInt8 DEFAULT 1,
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY symbol
"""

PAPER_TRADES_MIGRATIONS = (
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS max_sessions UInt16 DEFAULT 10"
    " AFTER planned_at",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS capital_allocated Float64 DEFAULT"
    " 50000 AFTER max_sessions",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS exit_price Nullable(Float64) AFTER"
    " notes",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS closed_at Nullable(DateTime) AFTER"
    " exit_price",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS close_reason String DEFAULT ''"
    " AFTER closed_at",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS realized_pnl Float64 DEFAULT 0"
    " AFTER close_reason",
)

MARKET_ACTIVITY_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS trading.market_activity_snapshots (
    row_id String,
    snapshot_at DateTime('Asia/Kolkata'),
    trading_date Date,
    source LowCardinality(String),
    metric_type LowCardinality(String),
    exchange LowCardinality(String),
    index_name LowCardinality(String),
    rank UInt32,
    symbol String,
    stock_name String,
    moneycontrol_id String,
    slug String,
    price Float64,
    change_abs Float64,
    change_pct Float64,
    day_high Float64,
    day_low Float64,
    open Float64,
    prev_close Float64,
    volume UInt64,
    avg_volume UInt64,
    volume_multiplier Float64,
    volume_change_pct Float64,
    value_cr Float64,
    vwap Float64,
    direction LowCardinality(String),
    mcap_cr Float64,
    month_return_pct Float64,
    month3_return_pct Float64,
    share_url String,
    source_url String,
    fetched_at DateTime('Asia/Kolkata'),
    inserted_at DateTime DEFAULT now()
) ENGINE = MergeTree
PARTITION BY toYYYYMM(trading_date)
ORDER BY (trading_date, metric_type, exchange, index_name, snapshot_at, rank, symbol)
TTL snapshot_at + INTERVAL 180 DAY
"""


async def ensure_news_schema(ch: ClickHouseRepo) -> None:
    for ddl in (
        NEWS_ARTICLES,
        NEWS_MENTIONS,
        NEWS_SCORES,
        NSE_LARGE_DEALS,
        NSE_LARGE_DEALS_TTL_MIGRATION,
    ):
        await ch.command(ddl)


async def ensure_corporate_events_schema(ch: ClickHouseRepo) -> None:
    await ch.command(CORPORATE_EVENTS)


async def ensure_backtest_schema(ch: ClickHouseRepo) -> None:
    await ch.command(DAILY_BACKTEST_FEATURES)


async def ensure_market_activity_schema(ch: ClickHouseRepo) -> None:
    await ch.command(MARKET_ACTIVITY_SNAPSHOTS)


async def ensure_screener_feature_cache_schema(ch: ClickHouseRepo) -> None:
    await ch.command(DAILY_SCREENER_FEATURES)
    for migration in DAILY_SCREENER_FEATURES_MIGRATIONS:
        await ch.command(migration)


async def ensure_signal_ledger_schema(ch: ClickHouseRepo) -> None:
    await ch.command(SIGNAL_LEDGER)


async def ensure_paper_trades_schema(ch: ClickHouseRepo) -> None:
    await ch.command(PAPER_TRADES)
    for migration in PAPER_TRADES_MIGRATIONS:
        await ch.command(migration)
