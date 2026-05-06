CREATE DATABASE IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.snapshots (
    trading_date      Date,
    symbol            String,
    security_id       String,
    snapshot_ts       DateTime('Asia/Kolkata'),
    bucket            UInt16,
    ltp               Float32,
    candle_open       Float32,
    candle_high       Float32,
    candle_low        Float32,
    volume_cum        UInt64,
    volume_delta      UInt32,
    trade_count       UInt32,
    oi_total          UInt64,
    oi_delta          Int64,
    bid               Float32,
    ask               Float32,
    bid_qty           UInt32,
    ask_qty           UInt32,
    spread_pct        Float32,
    vwap              Float32,
    price_velocity    Float32,
    volume_rate       Float32,
    candle_body_ratio Float32,
    inserted_at       DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(trading_date)
ORDER BY (trading_date, symbol, security_id, bucket);

CREATE TABLE IF NOT EXISTS trading.daily_ref (
    trading_date      Date,
    symbol            String,
    security_id       String,
    prev_close        Float32,
    pre_open_price    Float32,
    day_open          Float32,
    gap_pct           Float32,
    prev_day_high     Float32,
    prev_day_low      Float32,
    closing_price     Float32  DEFAULT 0,
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(trading_date)
ORDER BY (trading_date, symbol);

CREATE TABLE IF NOT EXISTS trading.signals (
    id                UUID DEFAULT generateUUIDv4(),
    trading_date      Date,
    symbol            String,
    security_id       String,
    direction         Enum8('BUY' = 1, 'SELL' = 2),
    entry_price       Float32,
    entry_bucket      UInt16,
    entry_ts          DateTime('Asia/Kolkata'),
    score             UInt8,
    signals_fired     Array(String),
    tp_price          Float32,
    sl_price          Float32,
    quantity          UInt32,
    exit_price        Nullable(Float32),
    exit_bucket       Nullable(UInt16),
    exit_reason       Nullable(Enum8('TP' = 1, 'SL' = 2, 'TIME' = 3)),
    actual_return_pct Nullable(Float32),
    pnl_rupees        Nullable(Float32),
    cfg_entry_start      UInt8,
    cfg_entry_end        UInt8,
    cfg_min_move_pct     Float32,
    cfg_min_volume       UInt32,
    cfg_min_score        UInt8,
    cfg_tp_pct           Float32,
    cfg_sl_pct           Float32,
    cfg_hard_exit_bucket UInt16,
    cfg_quantity         UInt32,
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(trading_date)
ORDER BY (trading_date, symbol, entry_bucket, id);

CREATE TABLE IF NOT EXISTS trading.accounts (
    name              String,
    client_id         String,
    access_token      String,
    mode              Enum8('PAPER' = 1, 'LIVE' = 2) DEFAULT 1,
    enabled           UInt8 DEFAULT 1,
    broker            String DEFAULT 'DHAN',
    api_key           String DEFAULT '',
    api_secret        String DEFAULT '',
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY client_id;

CREATE TABLE IF NOT EXISTS trading.orders (
    trading_date      Date,
    signal_id         UUID,
    account_client_id String,
    dhan_order_id     String,
    symbol            String,
    direction         Enum8('BUY' = 1, 'SELL' = 2),
    quantity          UInt32,
    order_type        Enum8('ENTRY' = 1, 'EXIT' = 2),
    status            Enum8('PENDING' = 1, 'FILLED' = 2, 'REJECTED' = 3, 'CANCELLED' = 4),
    filled_price      Nullable(Float32),
    error_message     Nullable(String),
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(trading_date)
ORDER BY (trading_date, signal_id, account_client_id, order_type);

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
    max_sessions      UInt16 DEFAULT 10,
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
ORDER BY symbol;

CREATE TABLE IF NOT EXISTS trading.watchlist (
    security_id       String,
    symbol            String,
    company_name      String,
    tiers             Array(String),
    enabled           UInt8 DEFAULT 1,
    min_volume        UInt32 DEFAULT 0,
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY security_id;

CREATE TABLE IF NOT EXISTS trading.api_errors (
    ts                DateTime DEFAULT now(),
    endpoint          String,
    status_code       UInt16,
    error_message     String,
    symbol            Nullable(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY ts
TTL ts + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS trading.gap15_config (
    total_capital      UInt32,
    leverage           UInt32,
    top_n              UInt32,
    tp_pct             Float32,
    sl_pct             Float32,
    exit_bucket        UInt16,
    gap_min_pct        Float32,
    gap_max_pct        Float32 DEFAULT 15.0,
    price_max          Float32,
    cap_mult           Float32,
    -- Slippage % for the LIMIT fallback when a MARKET entry stays PENDING >20s.
    -- Column kept as entry_slippage_pct for backwards compatibility with existing rows.
    entry_slippage_pct Float32 DEFAULT 0.30,
    inserted_at        DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY tuple();

CREATE TABLE IF NOT EXISTS trading.system_settings (
    key          String,
    value        String,
    inserted_at  DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY key;

CREATE TABLE IF NOT EXISTS trading.tier_state (
    tier_name    String,
    enabled      UInt8 DEFAULT 0,
    inserted_at  DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY tier_name;

INSERT INTO trading.tier_state (tier_name, enabled) VALUES
    ('F&O', 0), ('Nifty50', 0), ('Nifty500', 0),
    ('AllNSE', 0), ('NSEActive', 0),
    ('Tier1', 0), ('Tier2', 0), ('Margin4x', 0), ('Liquid5L', 0);

CREATE TABLE IF NOT EXISTS trading.volume_group_state (
    group_name   String,
    enabled      UInt8 DEFAULT 1,
    inserted_at  DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY group_name;

INSERT INTO trading.volume_group_state (group_name, enabled) VALUES
    ('MEGA', 1), ('LARGE', 1), ('MID', 0), ('SMALL', 0);

CREATE VIEW IF NOT EXISTS trading.daily_performance AS
SELECT
    trading_date,
    countIf(direction = 'BUY')   AS buy_signals,
    countIf(direction = 'SELL')  AS sell_signals,
    countIf(actual_return_pct > 0) AS profitable,
    countIf(actual_return_pct < 0) AS losses,
    round(avg(actual_return_pct), 3) AS avg_return_pct,
    round(sum(pnl_rupees), 2) AS net_pnl,
    round(sum(toFloat64(entry_price) * quantity) / 5, 2) AS capital_used,
    round(sum(pnl_rupees) / nullIf(sum(toFloat64(entry_price) * quantity) / 5, 0) * 100, 2) AS roc_pct
FROM trading.signals FINAL
WHERE exit_reason IS NOT NULL
GROUP BY trading_date
ORDER BY trading_date DESC;
