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
  positive_months_pct: number
  median_monthly_pnl: number
  worst_month: number
  best_month: number
  max_drawdown_rs: number
  stability_score: number
  status: string
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
