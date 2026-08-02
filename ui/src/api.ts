export interface BrokerStatus {
  provider: string
  configured: boolean
  state: 'missing' | 'invalid' | 'expired' | 'ready' | 'degraded' | string
  message: string
  credential_source: string
  client_id: string | null
  issued_at_utc: string | null
  expires_at_utc: string | null
  live_quotes: boolean
}

export interface NewsItem {
  article_id: string
  source: string
  source_kind: string
  category: string
  title: string
  url: string
  summary: string
  published_at: string
  fetched_at: string
  symbol: string
  security_id: string
  company_name: string
  match_confidence: number
  matched_text: string
  sentiment: number
  impact_score: number
  direction: 'BULLISH' | 'BEARISH' | 'WATCH' | 'NEUTRAL' | string
  horizon: string
  confidence: number
  reason: string
  model: string
}

export interface NseLargeDeal {
  deal_id: string
  deal_type: string
  deal_date: string
  deal_date_raw: string
  symbol: string
  security_name: string
  client_name: string
  side: string
  quantity: number
  price: number
  value_lakh: number
  source_url: string
  fetched_at: string
}

export interface CorporateEvent {
  event_id: string
  source: string
  source_event_id: string
  symbol: string
  company_name: string
  event_time: string
  event_date: string
  event_category: string
  catalyst_score: number
  title: string
  summary: string
  attachment_url: string
  source_url: string
  evidence_count?: number
}

export interface BacktestPrediction {
  strategy?: string
  signal_id?: string
  symbol: string
  signal_date: string
  entry_date: string
  exit_date: string
  entry_price?: number
  target_price?: number
  stop_price?: number
  exit_price?: number
  entry?: number
  target?: number
  stop?: number
  exit?: number
  outcome?: string
  exit_reason?: string
  target_hit: boolean | null
  hold_sessions: number
  net_return_pct: number
  split?: string
  relvol50?: number
  event_category?: string
  event_categories?: string
  event_title?: string
  event_titles?: string
}

export interface PredictionHistoryResponse {
  predictions: BacktestPrediction[]
  matched: number
  evidence_kind: 'historical_backtest' | string
  live_predictions: boolean
  message: string
}

export interface MarketActivityItem {
  row_id: string
  snapshot_at: string
  trading_date: string
  source: string
  metric_type: string
  exchange: string
  index_name: string
  rank: number
  symbol: string
  stock_name: string
  moneycontrol_id: string
  slug: string
  price: number
  change_abs: number
  change_pct: number
  day_high: number
  day_low: number
  open: number
  prev_close: number
  volume: number
  avg_volume: number
  volume_multiplier: number
  volume_change_pct: number
  value_cr: number
  vwap: number
  direction: string
  mcap_cr: number
  month_return_pct: number
  month3_return_pct: number
  share_url: string
  source_url: string
  fetched_at: string
}

export interface NewsRefreshResult {
  ok: boolean
  articles: number
  mentions: number
  scores: number
  deals: number
  watchlist_companies: number
  refreshed_at: string
  deal_error: string | null
}

export interface MarketActivityRefreshResult {
  ok: boolean
  rows: number
  refreshed_at: string
}

export interface MarketRegime {
  label: string
  tone: 'bullish' | 'neutral' | 'cautious' | string
  summary: string
  advances: number
  declines: number
  breadth_ratio: number
}

export interface LiveSignal {
  status: 'ENTRY_NOW' | 'WATCH' | 'WAIT_FOR_TRIGGER' | 'INVALIDATED' | 'NO_TRADE' | string
  label: string
  reason: string
  strategy_id: string
  strategy_label: string
  strategy_status: string
  setup_family: string
  score: number
  as_of: string
  trigger_price: number | null
  trigger_source: string | null
}

/**
 * Optional research evidence supplied alongside a strategy row.  The scanner
 * remains usable with older engine payloads, so every field is deliberately
 * optional and the UI can fall back to the original strategy-only view.
 */
export interface ConfluenceScoreBreakdown {
  base_score?: number | null
  model_score?: number | null
  technical?: number | null
  technical_quality?: number | null
  trend?: number | null
  trend_quality?: number | null
  volume?: number | null
  volume_quality?: number | null
  regime?: number | null
  regime_quality?: number | null
  risk_reward?: number | null
  risk_reward_quality?: number | null
  catalyst_adjustment?: number | null
  total_score?: number | null
}

export interface ConfluenceStrategyMatch {
  timeframe?: string
  strategy_id?: string
  strategy_label?: string
  setup_family?: string
  status?: string
  strategy_status?: string
  signal_status?: string
  signal_label?: string
  selected?: boolean
  score?: number | null
  reason?: string
}

export interface ConfluenceFinding {
  label?: string
  title?: string
  detail?: string
  reason?: string
  pillar?: string
  status?: string
  tone?: string
  source?: string
  source_url?: string
  url?: string
  published_at?: string
}

export interface ConfluenceNewsArticle {
  title?: string
  summary?: string
  reason?: string
  source?: string
  source_url?: string
  url?: string
  published_at?: string
  direction?: string
  impact_score?: number | null
}

export interface ConfluenceNewsSummary {
  lookback_hours?: number | null
  article_count?: number | null
  bullish_articles?: number | null
  bearish_articles?: number | null
  avg_sentiment?: number | null
  average_sentiment?: number | null
  max_impact?: number | null
  direction?: string
  score_adjustment?: number | null
  latest_reason?: string
  latest_headline?: string
  latest_source?: string
  latest_url?: string
  source?: string
  source_url?: string
  url?: string
  articles?: ConfluenceNewsArticle[]
  items?: ConfluenceNewsArticle[]
}

export interface ResearchConfluence {
  research_score?: number | null
  research_state?: string
  trade_state?: string
  confluence_state?: string
  pillar_count?: number | null
  supporting_pillars?: number | null
  conflicting_pillars?: number | null
  score_breakdown?: ConfluenceScoreBreakdown
  strategy_matches?: ConfluenceStrategyMatch[]
  evidence?: Array<string | ConfluenceFinding>
  risks?: Array<string | ConfluenceFinding>
  news?: ConfluenceNewsSummary
}

export interface LiveStrategyRow {
  security_id: string
  symbol: string
  company_name: string
  strategy_id: string
  strategy_label: string
  strategy_status: string
  setup_family: string
  signal_status: string
  signal_label: string
  reason: string
  score: number
  last_price: number
  day_change_pct: number
  open_gap_pct: number
  volume: number
  trigger_price: number | null
  trigger_source: string | null
  stop_loss: number
  target_price: number
  risk_reward: number
  source: string
  updated_at: string
  confluence?: ResearchConfluence
}

export interface LiveStrategySnapshot {
  event: string
  updated_at: string
  mode: string
  feed_status: string
  broker: BrokerStatus
  market_regime: MarketRegime
  total_watching: number
  triggered: number
  rows: LiveStrategyRow[]
  message: string | null
}

export interface SwingCandidate {
  symbol: string
  company_name: string
  setup_family: string
  bias: string
  score: number
  confidence: string
  regime_fit: number
  risk_reward: number
  last_price: number
  day_change_pct: number
  open_gap_pct: number
  distance_to_high_pct: number
  liquidity_bucket: string
  entry_zone: string
  stop_loss: number
  target_price: number
  expected_hold: string
  thesis: string
  reasons: string[]
  risks: string[]
  source: string
  live_signal: LiveSignal
  confluence?: ResearchConfluence
}

export interface SetupMix {
  family: string
  count: number
  avg_score: number
}

export interface SwingHomeResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  top_candidates: SwingCandidate[]
  scanner_count: number
  setup_mix: SetupMix[]
}

export interface SwingScannerResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  live_data: boolean
  total_candidates: number
  candidates: SwingCandidate[]
}

export interface SwingCandidateResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  candidate: SwingCandidate | null
  message: string | null
}

export interface HistoricalCandle {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface HistoricalSummary {
  latest_close: number
  change_pct_1m: number
  change_pct_3m: number
  change_pct_1y: number
  high_52w: number
  low_52w: number
  avg_volume_20d: number
}

export interface SymbolHistoryResponse {
  updated_at: string
  symbol: string
  range: string
  source: string
  candles: HistoricalCandle[]
  summary: HistoricalSummary | null
  message: string | null
}

export interface HistoricalScreenerRow {
  symbol: string
  as_of: string
  setup_family: string
  strategy_id: string
  strategy_label: string
  strategy_status: string
  score: number
  trend_label: string
  close: number
  sma20: number
  sma50: number
  avg_volume20: number
  volume_ratio: number
  distance_to_20d_high_pct: number
  distance_to_52w_high_pct: number
  range_position_pct: number
  atr14: number
  atr_pct: number
  close_location: number
  gap_pct: number
  rs60_rank: number
  rs120_rank: number
  market_breadth200: number
  planned_entry: string
  trigger_price: number | null
  trigger_source?: string | null
  stop_loss: number
  target_price: number
  risk_reward: number
}

export interface HistoricalScreenerResponse {
  updated_at: string
  range: string
  signal_date: string | null
  total_rows: number
  rows: HistoricalScreenerRow[]
  message: string | null
}

export interface FreshSignalsResponse {
  updated_at: string
  signal_date: string | null
  eligible_rows: number
  new_rows: number
  seen_rows: number
  staged_rows: number
  rows: HistoricalScreenerRow[]
  message: string | null
}

export interface FeatureCacheRefreshResponse {
  updated_at: string
  data_date: string | null
  cached_rows: number
  message: string
}

export interface BambooLatestSignal {
  strategy: string
  symbol: string
  signal_date: string
  planned_entry: string
  close: number
  stop: number
  target_from_close: number
  risk_multiple: number
  risk_pct_vs_close: number
  relvol: number
  range_position_52w: number
  ema20_dist_atr: number
  prior_high20: number
  prior_high55: number
  gap_pct: number
  close_loc: number
  rank_score: number
}

export interface BambooLatestResponse {
  updated_at: string
  signal_date: string | null
  total_rows: number
  unique_symbols: number
  top_signals: BambooLatestSignal[]
  all_signals: BambooLatestSignal[]
  message: string | null
}

export interface BrokerAccountBalance {
  availabelBalance?: number
  utilizedAmount?: number
  sodLimit?: number
  withdrawableBalance?: number
}

export interface BrokerPosition {
  tradingSymbol?: string
  securityId?: string
  positionType?: string
  netQty?: number
  realizedProfit?: number
  unrealizedProfit?: number
  costPrice?: number
}

export interface BrokerAccountSnapshot {
  client_id: string
  name: string
  broker: string
  balance: BrokerAccountBalance
  positions: BrokerPosition[]
  error?: string | null
}

export interface PaperTrade {
  symbol: string
  company_name: string
  setup_family: string
  bias: string
  entry_price: number
  quantity: number
  stop_loss: number
  target_price: number
  planned_at: string
  max_sessions: number
  capital_allocated: number
  expected_hold: string
  thesis: string
  notes: string
  exit_price: number | null
  closed_at: string | null
  close_reason: string
  realized_pnl: number
  current_price: number
  current_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  quote_source: string
  quote_updated_at: string
  enabled: number
}

export type PaperTradeInput = Omit<
  PaperTrade,
  | 'enabled'
  | 'planned_at'
  | 'exit_price'
  | 'closed_at'
  | 'close_reason'
  | 'realized_pnl'
  | 'current_price'
  | 'current_value'
  | 'unrealized_pnl'
  | 'unrealized_pnl_pct'
  | 'quote_source'
  | 'quote_updated_at'
>

export interface PaperBudget {
  total_budget: number
  allocated_budget: number
  available_budget: number
}

export interface BacktestRunSummary {
  strategy_id: string
  strategy_name: string
  total_trades: number
  win_rate: number
  avg_return_pct: number
  total_pnl: number
  deployed_return_pct: number
  avg_hold_sessions: number
  tp_exits: number
  sl_exits: number
  time_exits: number
  rsi_exits: number
  from_date: string
  to_date: string
}

export interface BacktestYearlyReturn {
  strategy_id: string
  year: number
  trades: number
  win_rate: number
  avg_return_pct: number
  pnl: number
  return_pct: number
}

export interface BacktestMonthlyReturn {
  strategy_id: string
  year: number
  month: number
  month_label: string
  trades: number
  win_rate: number
  pnl: number
  return_pct: number
}

export interface BacktestEquityPoint {
  strategy_id: string
  trade_date: string
  daily_pnl: number
  cumulative_pnl: number
  drawdown_rs: number
  cumulative_return_pct: number
}

export interface BacktestSymbolResult {
  strategy_id: string
  symbol: string
  trades: number
  win_rate: number
  pnl: number
  avg_return_pct: number
}

export interface BacktestDayQuality {
  strategy_id: string
  trading_days: number
  positive_days_pct: number
  worst_day: number
  best_day: number
  max_drawdown_rs: number
}

export interface BacktestStrategyDiagnostic {
  strategy_id: string
  method_family: string
  total_trades: number
  total_pnl: number
  win_rate: number
  profit_factor: number
  expectancy_pct: number
  annualized_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  sortino_ratio: number
  avg_win_pct: number
  avg_loss_pct: number
  payoff_ratio: number
  max_losing_streak: number
  recovery_factor: number
  positive_months_pct: number
  median_monthly_pnl: number
  worst_month: number
  best_month: number
  max_drawdown_rs: number
  stability_score: number
  status: string
}

export interface BacktestCashProfile {
  strategy_id: string
  method_family: string
  initial_capital: number
  candidate_trades: number
  trades_taken: number
  skipped_entries: number
  cash_blocked_entries: number
  duplicate_entries_skipped: number
  total_pnl: number
  return_pct: number
  annualized_return_pct: number
  win_rate: number
  profit_factor: number
  sharpe_ratio: number
  sortino_ratio: number
  max_drawdown_rs: number
  max_drawdown_pct: number
  recovery_factor: number
  max_losing_streak: number
  positive_months_pct: number
  max_open_positions: number
  peak_capital_used: number
  peak_capital_used_pct: number
  avg_capital_used_pct: number
  from_date: string
  to_date: string
}

export interface BacktestCashMonthlyReturn {
  strategy_id: string
  year: number
  month: number
  month_label: string
  trades_closed: number
  entries_taken: number
  skipped_entries: number
  pnl: number
  return_pct: number
  ending_equity: number
  max_drawdown_pct: number
}

export interface BacktestCashEquityPoint {
  strategy_id: string
  trade_date: string
  realized_pnl: number
  cumulative_pnl: number
  equity_value: number
  drawdown_rs: number
  return_pct: number
  open_positions: number
  capital_used: number
  cash_available: number
  entries_taken: number
  skipped_entries: number
}

export interface BacktestTradeLogRow {
  strategy_id: string
  symbol: string
  signal_date: string
  entry_date: string
  exit_date: string
  setup_family: string
  entry_price: number
  exit_price: number
  quantity: number
  pnl: number
  return_pct: number
  exit_reason: string
  hold_sessions: number
  score: number
}

export interface BacktestDateSummary {
  trade_date: string
  total_trades: number
  winners: number
  losers: number
  win_rate: number
  total_pnl: number
  avg_return_pct: number
  best_symbol: string
  best_pnl: number
  worst_symbol: string
  worst_pnl: number
}

export interface BacktestDateStrategySummary {
  strategy_id: string
  setup_family: string
  trades: number
  win_rate: number
  pnl: number
  best_symbol: string
  best_pnl: number
  worst_symbol: string
  worst_pnl: number
}

export interface BacktestDashboardResponse {
  run_id: string
  updated_at: string
  summaries: BacktestRunSummary[]
  yearly_returns: BacktestYearlyReturn[]
  monthly_returns: BacktestMonthlyReturn[]
  equity_curve: BacktestEquityPoint[]
  diagnostics: BacktestStrategyDiagnostic[]
  winners: BacktestSymbolResult[]
  losers: BacktestSymbolResult[]
  day_quality: BacktestDayQuality[]
  trades: BacktestTradeLogRow[]
  cash_profiles: BacktestCashProfile[]
  cash_monthly_returns: BacktestCashMonthlyReturn[]
  cash_equity_curve: BacktestCashEquityPoint[]
}

export interface BacktestDatewiseResponse {
  run_id: string
  updated_at: string
  selected_date: string | null
  available_dates: string[]
  strategy_options: string[]
  summary: BacktestDateSummary | null
  strategy_summaries: BacktestDateStrategySummary[]
  top_gainers: BacktestTradeLogRow[]
  top_losers: BacktestTradeLogRow[]
  rows: BacktestTradeLogRow[]
  page: number
  page_size: number
  total_rows: number
}

export interface BacktestCacheStatus {
  cached_rows: number
  symbols: number
  from_date: string
  to_date: string
  refreshed_at: string
}

export interface BacktestRunResponse {
  ok: boolean
  run_id: string
  message: string
  cache: BacktestCacheStatus
  dashboard: BacktestDashboardResponse
}

export interface BacktestCacheRefreshResponse {
  ok: boolean
  updated_at: string
  cache: BacktestCacheStatus
  message: string
}

export type PythonBacktestMetricRow = Record<string, string | number | boolean | null>

export interface PythonBacktestPeriodRow {
  strategy_family: string
  year: number
  month?: number
  month_label?: string
  trades: number
  win_rate: number
  avg_return_pct: number
  return_proxy_pct: number
}

export interface PythonBacktestPrediction {
  strategy_family: string
  model: string
  signal_date: string
  symbol: string
  direction: string
  entry: number
  stop: number
  target: number
  score: number
  close: number
  reason: string
}

export interface PythonBacktestLabPayload {
  updated_at: string
  output_dir: string
  best: {
    ma: PythonBacktestMetricRow[]
    panic: PythonBacktestMetricRow[]
  }
  scorecards: {
    ma: PythonBacktestMetricRow[]
    panic: PythonBacktestMetricRow[]
  }
  period_returns: {
    ma_monthly: PythonBacktestPeriodRow[]
    ma_yearly: PythonBacktestPeriodRow[]
    panic_monthly: PythonBacktestPeriodRow[]
    panic_yearly: PythonBacktestPeriodRow[]
  }
  predictions: PythonBacktestPrediction[]
  charts: Record<string, string>
  files: Record<string, string>
}

export interface PythonBacktestLabResponse {
  ok: boolean
  updated_at: string
  duration_ms: number | null
  message: string
  payload: PythonBacktestLabPayload
}

async function apiFetch<T>(path: string, options?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const timeoutMs = options?.timeoutMs ?? 60000
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  const { timeoutMs: _timeoutMs, signal, ...fetchOptions } = options ?? {}
  if (signal) {
    signal.addEventListener('abort', () => controller.abort(), { once: true })
  }
  let res: Response
  try {
    res = await fetch(path, { ...fetchOptions, signal: controller.signal })
  } catch (err) {
    if (controller.signal.aborted) {
      throw new Error(`Engine API timed out after ${Math.round(timeoutMs / 1000)}s.`)
    }
    throw err
  } finally {
    window.clearTimeout(timeout)
  }
  const contentType = res.headers.get('content-type') ?? ''
  if (!res.ok) {
    if (!contentType.includes('application/json')) {
      throw new Error(`Engine API is unavailable (${res.status}).`)
    }
    const payload = await res.json().catch(() => null)
    const message = typeof payload === 'string'
      ? payload
      : payload && typeof payload === 'object' && 'message' in payload
        ? String(payload.message)
        : payload && typeof payload === 'object' && 'error' in payload
          ? String(payload.error)
        : `API request failed (${res.status}).`
    throw new Error(message)
  }
  if (res.status === 204) return undefined as T
  if (!contentType.includes('application/json')) {
    throw new Error('Engine API is unavailable or returned a non-JSON response.')
  }
  return res.json() as Promise<T>
}

export async function getSwingHome(): Promise<SwingHomeResponse> {
  return apiFetch<SwingHomeResponse>('/api/swing/home', { timeoutMs: 10000 })
}

export async function getSwingScanner(limit = 24): Promise<SwingScannerResponse> {
  return apiFetch<SwingScannerResponse>(`/api/swing/scanner?limit=${limit}`, { timeoutMs: 15000 })
}

export async function getSwingCandidate(symbol: string): Promise<SwingCandidateResponse> {
  return apiFetch<SwingCandidateResponse>(`/api/swing/candidates/${encodeURIComponent(symbol)}`)
}

export async function getSwingHistory(symbol: string, range = '1y'): Promise<SymbolHistoryResponse> {
  return apiFetch<SymbolHistoryResponse>(`/api/swing/history/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`)
}

export async function getHistoricalScreener(params?: {
  limit?: number
  setup?: string
  strategy?: string
  minPrice?: number
  minAvgVolume?: number
}): Promise<HistoricalScreenerResponse> {
  const search = new URLSearchParams()
  if (params?.limit) search.set('limit', String(params.limit))
  if (params?.setup) search.set('setup', params.setup)
  if (params?.strategy) search.set('strategy', params.strategy)
  if (params?.minPrice) search.set('min_price', String(params.minPrice))
  if (params?.minAvgVolume) search.set('min_avg_volume', String(params.minAvgVolume))
  const query = search.toString()
  return apiFetch<HistoricalScreenerResponse>(`/api/swing/historical-screener${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
}

export async function stageFreshSignals(params?: {
  limit?: number
  minPrice?: number
  minAvgVolume?: number
}): Promise<FreshSignalsResponse> {
  const search = new URLSearchParams()
  if (params?.limit) search.set('limit', String(params.limit))
  if (params?.minPrice) search.set('min_price', String(params.minPrice))
  if (params?.minAvgVolume) search.set('min_avg_volume', String(params.minAvgVolume))
  const query = search.toString()
  return apiFetch<FreshSignalsResponse>(`/api/swing/fresh-signals${query ? `?${query}` : ''}`, {
    method: 'POST',
  })
}

export async function refreshFeatureCache(): Promise<FeatureCacheRefreshResponse> {
  return apiFetch<FeatureCacheRefreshResponse>('/api/swing/feature-cache/refresh', {
    method: 'POST',
  })
}

export async function getBambooLatest(): Promise<BambooLatestResponse> {
  return apiFetch<BambooLatestResponse>('/api/swing/bamboo/latest')
}

export async function getNews(params?: {
  symbol?: string
  source?: string
  min_impact?: number
  limit?: number
}): Promise<NewsItem[]> {
  const search = new URLSearchParams()
  if (params?.symbol) search.set('symbol', params.symbol)
  if (params?.source) search.set('source', params.source)
  if (params?.min_impact) search.set('min_impact', String(params.min_impact))
  if (params?.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  const data = await apiFetch<{ news: NewsItem[] }>(`/api/news${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
  return data.news ?? []
}

export async function refreshNews(): Promise<NewsRefreshResult> {
  return apiFetch<NewsRefreshResult>('/api/news/refresh', {
    method: 'POST',
    timeoutMs: 120000,
  })
}

export async function getNseLargeDeals(params?: {
  symbol?: string
  deal_type?: string
  side?: string
  limit?: number
}): Promise<NseLargeDeal[]> {
  const search = new URLSearchParams()
  if (params?.symbol) search.set('symbol', params.symbol)
  if (params?.deal_type) search.set('deal_type', params.deal_type)
  if (params?.side) search.set('side', params.side)
  if (params?.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  const data = await apiFetch<{ deals: NseLargeDeal[] }>(`/api/nse/large-deals${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
  return data.deals ?? []
}

export async function getCorporateEvents(params?: {
  symbol?: string
  category?: string
  lookback_days?: number
  limit?: number
}): Promise<CorporateEvent[]> {
  const search = new URLSearchParams()
  if (params?.symbol) search.set('symbol', params.symbol)
  if (params?.category) search.set('category', params.category)
  if (params?.lookback_days) search.set('lookback_days', String(params.lookback_days))
  if (params?.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  const data = await apiFetch<{ events: CorporateEvent[] }>(`/api/news/events${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
  return data.events ?? []
}

export async function getNewsPredictionHistory(params?: {
  success?: boolean
  split?: string
  limit?: number
}): Promise<PredictionHistoryResponse> {
  const search = new URLSearchParams()
  if (typeof params?.success === 'boolean') search.set('success', String(params.success))
  if (params?.split) search.set('split', params.split)
  if (params?.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  return apiFetch<PredictionHistoryResponse>(`/api/news/predictions${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
}

export async function getMarketActivity(params?: {
  symbol?: string
  metric_type?: string
  exchange?: string
  source?: string
  min_volume_multiplier?: number
  limit?: number
}): Promise<MarketActivityItem[]> {
  const search = new URLSearchParams()
  if (params?.symbol) search.set('symbol', params.symbol)
  if (params?.metric_type) search.set('metric_type', params.metric_type)
  if (params?.exchange) search.set('exchange', params.exchange)
  if (params?.source) search.set('source', params.source)
  if (params?.min_volume_multiplier) search.set('min_volume_multiplier', String(params.min_volume_multiplier))
  if (params?.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  const data = await apiFetch<{ activity: MarketActivityItem[] }>(`/api/market-activity${query ? `?${query}` : ''}`, { timeoutMs: 15000 })
  return data.activity ?? []
}

export async function refreshMarketActivity(): Promise<MarketActivityRefreshResult> {
  return apiFetch<MarketActivityRefreshResult>('/api/market-activity/refresh', {
    method: 'POST',
    timeoutMs: 90000,
  })
}

export async function getBrokerStatus(): Promise<BrokerStatus> {
  return apiFetch<BrokerStatus>('/api/swing/broker-status')
}

export async function getBrokerAccounts(): Promise<BrokerAccountSnapshot[]> {
  const data = await apiFetch<{ accounts: BrokerAccountSnapshot[] }>('/api/positions')
  return data.accounts ?? []
}

export async function getPaperTrades(): Promise<PaperTrade[]> {
  const data = await apiFetch<{ trades: PaperTrade[] }>('/api/paper-trades')
  return data.trades ?? []
}

export async function getPaperBudget(): Promise<PaperBudget> {
  return apiFetch<PaperBudget>('/api/paper-budget')
}

export async function savePaperBudget(totalBudget: number): Promise<PaperBudget> {
  return apiFetch<PaperBudget>('/api/paper-budget', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ total_budget: totalBudget }),
  })
}

export async function savePaperTrade(trade: PaperTradeInput): Promise<PaperTrade> {
  return apiFetch<PaperTrade>('/api/paper-trades', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(trade),
  })
}

export async function closePaperTrade(symbol: string, payload: {
  exit_price: number
  close_reason?: string
}): Promise<PaperTrade> {
  return apiFetch<PaperTrade>(`/api/paper-trades/${encodeURIComponent(symbol)}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deletePaperTrade(symbol: string): Promise<void> {
  await apiFetch<unknown>(`/api/paper-trades/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

export async function getBacktestDashboard(): Promise<BacktestDashboardResponse> {
  return apiFetch<BacktestDashboardResponse>('/api/backtests/dashboard')
}

export async function getBacktestDatewise(params?: {
  date?: string
  strategy?: string
  page?: number
  pageSize?: number
}): Promise<BacktestDatewiseResponse> {
  const search = new URLSearchParams()
  if (params?.date) search.set('date', params.date)
  if (params?.strategy) search.set('strategy', params.strategy)
  if (params?.page) search.set('page', String(params.page))
  if (params?.pageSize) search.set('page_size', String(params.pageSize))
  const query = search.toString()
  return apiFetch<BacktestDatewiseResponse>(`/api/backtests/datewise${query ? `?${query}` : ''}`)
}

export async function refreshBacktestCache(): Promise<BacktestCacheRefreshResponse> {
  return apiFetch<BacktestCacheRefreshResponse>('/api/backtests/feature-cache/refresh', {
    method: 'POST',
    timeoutMs: 300000,
  })
}

export async function runBacktest(): Promise<BacktestRunResponse> {
  return apiFetch<BacktestRunResponse>('/api/backtests/run', {
    method: 'POST',
    timeoutMs: 300000,
  })
}

export async function getPythonBacktestLab(): Promise<PythonBacktestLabResponse> {
  return apiFetch<PythonBacktestLabResponse>('/api/backtests/python/latest', {
    timeoutMs: 15000,
  })
}

export async function runPythonBacktestLab(): Promise<PythonBacktestLabResponse> {
  return apiFetch<PythonBacktestLabResponse>('/api/backtests/python/run', {
    method: 'POST',
    timeoutMs: 900000,
  })
}

export function pythonBacktestChartUrl(name: string) {
  return `/api/backtests/python/charts/${encodeURIComponent(name)}`
}
