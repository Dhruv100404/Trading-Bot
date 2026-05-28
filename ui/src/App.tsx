import { useDeferredValue, useEffect, useMemo, useRef, useState, startTransition, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Bookmark,
  BriefcaseBusiness,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Compass,
  Database,
  Landmark,
  LayoutDashboard,
  ListTodo,
  Radar,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import {
  getBrokerAccounts,
  getBacktestDashboard,
  getBacktestDatewise,
  getBambooLatest,
  getHistoricalScreener,
  closePaperTrade,
  deletePaperTrade,
  getPaperBudget,
  getPaperTrades,
  getSwingCandidate,
  getSwingHistory,
  getSwingHome,
  getSwingScanner,
  refreshBacktestCache,
  refreshFeatureCache,
  runBacktest,
  savePaperTrade,
  stageFreshSignals,
  type BrokerAccountSnapshot,
  type BrokerStatus,
  type BacktestDashboardResponse,
  type BacktestCacheStatus,
  type BacktestDatewiseResponse,
  type BacktestDayQuality,
  type BacktestRunSummary,
  type BacktestStrategyDiagnostic,
  type BambooLatestResponse,
  type BambooLatestSignal,
  type HistoricalScreenerResponse,
  type HistoricalScreenerRow,
  type FreshSignalsResponse,
  type LiveSignal,
  type PaperTrade,
  type PaperBudget,
  type SymbolHistoryResponse,
  type SwingCandidate,
  type SwingHomeResponse,
  type SwingScannerResponse,
} from './api'

type View = 'home' | 'scanner' | 'watchlist' | 'portfolio' | 'backtests' | 'settings' | 'stock'
type HistoryRange = '3m' | '6m' | '1y' | '3y' | '5y'
type PaperDeskTab = 'open' | 'closed' | 'weekly' | 'strategy' | 'intake'

interface NavItem {
  id: View
  label: string
  icon: LucideIcon
  blurb: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Dashboard', icon: LayoutDashboard, blurb: 'Market, risk, and top picks' },
  { id: 'scanner', label: 'Scanner', icon: Radar, blurb: 'Strategy-first signal board' },
  { id: 'watchlist', label: 'Watchlist', icon: Bookmark, blurb: 'Organize stocks to monitor' },
  { id: 'backtests', label: 'Backtest', icon: BarChart3, blurb: 'Strategy returns and trades' },
  { id: 'portfolio', label: 'Paper Desk', icon: WalletCards, blurb: 'Live paper execution' },
  { id: 'settings', label: 'Settings', icon: Compass, blurb: 'Dhan status and API direction' },
]

const WATCHLIST_STORAGE_KEY = 'swing-watchlist'
const PAPER_CAPITAL_PER_STOCK = 50000
const PAPER_HOLD_SESSIONS = 5
const AUTO_PAPER_MAX_SUGGESTIONS = 7
const NSE_HOLIDAYS = new Set([
  '2025-01-26', '2025-02-26', '2025-03-14', '2025-03-31', '2025-04-10', '2025-04-14',
  '2025-04-18', '2025-05-01', '2025-06-26', '2025-07-06', '2025-08-15', '2025-08-16',
  '2025-08-27', '2025-10-02', '2025-10-21', '2025-10-22', '2025-11-05', '2025-11-26',
  '2025-12-25',
  '2026-01-15', '2026-01-26', '2026-03-03', '2026-03-14', '2026-03-26', '2026-03-30',
  '2026-03-31', '2026-04-03', '2026-04-14', '2026-05-01', '2026-05-28', '2026-06-26',
  '2026-09-14', '2026-10-02', '2026-10-20', '2026-11-10', '2026-11-24', '2026-12-25',
])

type Tone = 'positive' | 'warning' | 'danger' | 'neutral'

interface BacktestPaperRule {
  stopLossPct: number
  takeProfitPct: number
  source: string
}

const BACKTEST_PAPER_RULES: Record<string, BacktestPaperRule> = {
  'near-52w-high-v1': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'engine near-52w-high model',
  },
  'near-52w-high-runner-v2': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'near-52w-high backtest family',
  },
  'near-52w-high-volume-v3': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'near-52w-high backtest family',
  },
  'near-52w-high-tight-v2': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'near-52w-high backtest family',
  },
  'momentum-core-v1': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'near-52w-high backtest family',
  },
  'pullback-20dma-v1': {
    stopLossPct: 3,
    takeProfitPct: 6,
    source: 'engine pullback-20dma model',
  },
  'pullback-quality-v2': {
    stopLossPct: 3,
    takeProfitPct: 6,
    source: 'pullback-20dma backtest family',
  },
  'rsi10-pullback-reversion-v1': {
    stopLossPct: 4,
    takeProfitPct: 4,
    source: 'engine RSI10 pullback model',
  },
  'swing-breakout-v1': {
    stopLossPct: 4,
    takeProfitPct: 8,
    source: 'engine swing-breakout model',
  },
  'breakout-volume-v2': {
    stopLossPct: 4,
    takeProfitPct: 8,
    source: 'swing-breakout backtest family',
  },
  'failed-breakdown-reclaim-v1': {
    stopLossPct: 4,
    takeProfitPct: 7,
    source: 'daily failed-breakdown reclaim model',
  },
  'compression-breakout-v1': {
    stopLossPct: 4,
    takeProfitPct: 8,
    source: 'compression breakout model',
  },
  'breakout-continuation-v1': {
    stopLossPct: 4,
    takeProfitPct: 8,
    source: 'breakout continuation model',
  },
  'rs-leader-continuation-v1': {
    stopLossPct: 5,
    takeProfitPct: 10,
    source: 'relative-strength continuation model',
  },
}

interface DhanApiSurface {
  label: string
  endpoints: string
  summary: string
  constraint: string
  docUrl: string
}

const DHAN_API_SURFACES: DhanApiSurface[] = [
  {
    label: 'Market Quote',
    endpoints: '/marketfeed/ltp, /marketfeed/ohlc, /marketfeed/quote',
    summary: 'Best fit for scanner snapshots, live watchlist refreshes, and thesis detail hydration.',
    constraint: 'Official docs say quote APIs support up to 1000 instruments per request with a 1 request per second limit.',
    docUrl: 'https://dhanhq.co/docs/v2/market-quote/',
  },
  {
    label: 'Portfolio & Positions',
    endpoints: '/holdings, /positions, /positions/convert',
    summary: 'Use these for live account snapshots, delivery holdings, and real position awareness beside paper trading.',
    constraint: 'This is the clean source of truth for the linked Dhan account once we move beyond UI-local paper plans.',
    docUrl: 'https://dhanhq.co/docs/v2/portfolio/',
  },
  {
    label: 'Orders & Trades',
    endpoints: '/orders, /trades',
    summary: 'This is the eventual live execution layer after the paper workflow has been hardened.',
    constraint: 'Official docs note static IP whitelisting for order placement, modification, and cancellation APIs.',
    docUrl: 'https://dhanhq.co/docs/v2/orders/',
  },
  {
    label: 'Platform Overview',
    endpoints: 'Market Feed, Statements, Margin Calculator, and more',
    summary: 'Useful as the top-level integration map while we expand beyond quote snapshots and account status.',
    constraint: 'Good reference point for deciding what should stay in the swing workspace versus the analysis worker.',
    docUrl: 'https://docs.dhanhq.co/',
  },
]

const HISTORY_RANGES: HistoryRange[] = ['3m', '6m', '1y', '3y', '5y']

function currency(value: number) {
  return `Rs ${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function errorMessage(err: unknown) {
  return String(err).replace(/^Error:\s*/, '')
}

function compactDate(value: string | null | undefined) {
  if (!value) return 'Not available'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('en-IN', {
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
}

function stateTone(state: BrokerStatus['state']) {
  if (state === 'ready') return 'positive'
  if (state === 'expired' || state === 'degraded') return 'warning'
  if (state === 'missing' || state === 'invalid') return 'danger'
  return 'neutral'
}

function defaultLiveSignal(overrides: Partial<LiveSignal> = {}): LiveSignal {
  return {
    status: 'WAIT_FOR_TRIGGER',
    label: 'Wait For Trigger',
    reason: 'Live entry signal has not been evaluated for this row yet.',
    strategy_id: 'unscored',
    strategy_label: 'Unscored',
    strategy_status: 'Unknown',
    setup_family: 'Unscored',
    score: 0,
    as_of: 'local',
    trigger_price: null,
    ...overrides,
  }
}

function signalTone(status: LiveSignal['status']): Tone {
  if (status === 'ENTRY_NOW') return 'positive'
  if (status === 'WATCH' || status === 'WAIT_FOR_TRIGGER') return 'warning'
  if (status === 'INVALIDATED' || status === 'NO_TRADE') return 'danger'
  return 'neutral'
}

function canSendToPaper(candidate: SwingCandidate) {
  return candidate.last_price > 0
    && candidate.stop_loss > 0
    && candidate.stop_loss < candidate.last_price
    && candidate.target_price > candidate.last_price
}

function signalClass(status: LiveSignal['status']) {
  return `signal-${String(status).toLowerCase().replace(/_/g, '-')}`
}

function parseRouteHash(): { view: View; symbol: string | null } {
  const path = window.location.pathname.replace(/^\/+/, '')
  const [viewPart, symbolPart] = path.split('/')
  const aliases: Record<string, View> = {
    dashboard: 'home',
    backtest: 'backtests',
    'paper-desk': 'portfolio',
  }
  const knownViews: View[] = ['home', 'scanner', 'watchlist', 'portfolio', 'backtests', 'settings', 'stock']
  const view = aliases[viewPart] ?? (knownViews.includes(viewPart as View) ? (viewPart as View) : 'home')
  if (viewPart && !aliases[viewPart] && !knownViews.includes(viewPart as View)) {
    window.history.replaceState(null, '', '/home')
  }
  return {
    view,
    symbol: view === 'stock' && symbolPart ? decodeURIComponent(symbolPart).toUpperCase() : null,
  }
}

function writeRouteHash(view: View, symbol?: string | null) {
  const nextPath = view === 'stock' && symbol ? `/stock/${encodeURIComponent(symbol)}` : `/${view}`
  if (window.location.pathname !== nextPath) {
    window.history.pushState(null, '', nextPath)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
}

function upsertCandidate(list: SwingCandidate[], candidate: SwingCandidate) {
  const next = list.filter((item) => item.symbol !== candidate.symbol)
  return [candidate, ...next]
}

function removeCandidate(list: SwingCandidate[], symbol: string) {
  return list.filter((item) => item.symbol !== symbol)
}

void [upsertCandidate, removeCandidate]

function ruleForStrategy(strategyId: string) {
  return BACKTEST_PAPER_RULES[strategyId] ?? null
}

function createCandidateFromHistoricalRow(row: HistoricalScreenerRow): SwingCandidate {
  const rule = ruleForStrategy(row.strategy_id)
  const stopLossPct = rule?.stopLossPct ?? 0
  const takeProfitPct = rule?.takeProfitPct ?? 0
  const stopLoss = row.stop_loss > 0 ? row.stop_loss : rule ? Number((row.close * (1 - stopLossPct / 100)).toFixed(2)) : 0
  const targetPrice = row.target_price > row.close ? row.target_price : rule ? Number((row.close * (1 + takeProfitPct / 100)).toFixed(2)) : row.close
  const strategyLabel = row.strategy_label && row.strategy_label !== 'Unlinked Screen' ? row.strategy_label : row.setup_family
  return {
    symbol: row.symbol,
    company_name: row.symbol,
    setup_family: strategyLabel,
    bias: 'Long',
    score: row.score,
    confidence: row.score >= 88 ? 'High Conviction' : row.score >= 78 ? 'Actionable' : 'Watchlist',
    regime_fit: Math.min(95, Math.max(55, row.score - 4)),
    risk_reward: row.risk_reward > 0 ? row.risk_reward : rule ? Number((((targetPrice - row.close) / Math.max(row.close - stopLoss, 0.01))).toFixed(2)) : 0,
    last_price: row.close,
    day_change_pct: 0,
    open_gap_pct: row.gap_pct,
    distance_to_high_pct: row.distance_to_20d_high_pct,
    liquidity_bucket: row.avg_volume20 >= 1_000_000 ? 'LARGE' : row.avg_volume20 >= 250_000 ? 'MID' : 'SMALL',
    entry_zone: row.planned_entry || `Backtest proxy close ${currency(row.close)}`,
    stop_loss: stopLoss,
    target_price: targetPrice,
    expected_hold: `${PAPER_HOLD_SESSIONS} trading sessions`,
    thesis: `${row.symbol} is staged only because the latest parquet screener row maps to the backtest-tracked ${strategyLabel} strategy status: ${row.strategy_status}.`,
    reasons: [
      rule ? `Exit model uses ${rule.source}: ${stopLossPct}% stop, ${takeProfitPct}% target, capped at ${PAPER_HOLD_SESSIONS} trading sessions.` : 'No strategy config was found for this row, so it is review-only unless a stop is supplied.',
      `${row.symbol} is ${row.distance_to_20d_high_pct.toFixed(2)}% away from the 20-day high.`,
      `Volume ratio is ${row.volume_ratio.toFixed(2)}x against the 20-day average.`,
      `ATR is ${row.atr_pct.toFixed(2)}% of close; RS60 rank is ${row.rs60_rank.toFixed(0)}.`,
      `Trend profile is ${row.trend_label}; strategy lab status is ${row.strategy_status}.`,
    ],
    risks: [
      `Stop is fixed at ${currency(stopLoss)} from the configured backtest rule.`,
      'Paper staging is evidence-gathering only; backtested behavior can fail in forward trading.',
    ],
    source: 'parquet-screener',
    live_signal: defaultLiveSignal({
      status: row.strategy_status === 'Watch' ? 'WATCH' : row.strategy_status === 'Rejected' ? 'NO_TRADE' : 'WAIT_FOR_TRIGGER',
      label: row.strategy_status === 'Watch' ? 'Watch Only' : row.strategy_status === 'Rejected' ? 'No Trade' : 'Needs Live Trigger',
      reason: `Historical screener row maps to ${strategyLabel}; live Dhan confirmation is still required before entry.`,
      strategy_id: row.strategy_id,
      strategy_label: strategyLabel,
      strategy_status: row.strategy_status,
      setup_family: row.setup_family,
      score: row.score,
      as_of: row.as_of,
    }),
  }
}

function createCandidateFromBambooSignal(signal: BambooLatestSignal): SwingCandidate {
  const risk = Math.max(signal.close - signal.stop, 0.01)
  const reward = Math.max(signal.target_from_close - signal.close, 0)
  const score = Math.round(Math.min(92, Math.max(65, 70 + signal.relvol * 5 + Math.min(signal.range_position_52w, 1.2) * 8 - signal.risk_pct_vs_close)))
  return {
    symbol: signal.symbol,
    company_name: signal.symbol,
    setup_family: 'Bamboo MTF Breakout',
    bias: 'Long',
    score,
    confidence: 'Review Signal',
    regime_fit: Math.max(55, Math.min(90, score - 4)),
    risk_reward: Number((reward / risk).toFixed(2)),
    last_price: signal.close,
    day_change_pct: 0,
    open_gap_pct: signal.gap_pct,
    distance_to_high_pct: Number((((signal.prior_high20 - signal.close) / Math.max(signal.prior_high20, 0.01)) * 100).toFixed(2)),
    liquidity_bucket: 'REVIEW',
    entry_zone: `Next open / live confirmation above Rs ${signal.prior_high20.toFixed(2)}`,
    stop_loss: signal.stop,
    target_price: signal.target_from_close,
    expected_hold: signal.risk_multiple >= 3 ? 'Up to 20 sessions' : 'Up to 15 sessions',
    thesis: `${signal.symbol} matched the Bamboo multi-timeframe breakout rules on ${signal.signal_date}: long-term resistance proxy cleared, daily prior high broken, and volume expanded to ${signal.relvol.toFixed(2)}x.`,
    reasons: [
      `Prior 20-day high trigger: Rs ${signal.prior_high20.toFixed(2)}.`,
      `Signal candle close location: ${(signal.close_loc * 100).toFixed(1)}% of range.`,
      `52-week range position: ${(signal.range_position_52w * 100).toFixed(1)}%.`,
    ],
    risks: [
      `Review-only setup. Latest Bamboo variants failed robustness gates overall.`,
      `Signal-candle-low stop is Rs ${signal.stop.toFixed(2)}, about ${signal.risk_pct_vs_close.toFixed(2)}% below close.`,
    ],
    source: 'bamboo-analysis',
    live_signal: defaultLiveSignal({
      status: 'WATCH',
      label: 'Review Only',
      reason: 'Bamboo signal is visible for study/paper review only; the strategy is not approved for fresh live entries.',
      strategy_id: signal.strategy,
      strategy_label: 'Bamboo MTF Breakout',
      strategy_status: 'Review Only',
      setup_family: 'Bamboo MTF Breakout',
      score,
      as_of: signal.signal_date,
      trigger_price: signal.prior_high20,
    }),
  }
}

function createCandidateFromPaperTrade(trade: PaperTrade): SwingCandidate {
  const risk = Math.max(trade.entry_price - trade.stop_loss, 0.01)
  const reward = Math.max(trade.target_price - trade.entry_price, 0)
  return {
    symbol: trade.symbol,
    company_name: trade.company_name || trade.symbol,
    setup_family: trade.setup_family || 'Paper Plan',
    bias: trade.bias || 'Long',
    score: 0,
    confidence: 'Paper Trade',
    regime_fit: 0,
    risk_reward: Number((reward / risk).toFixed(2)),
    last_price: trade.exit_price ?? trade.current_price ?? trade.entry_price,
    day_change_pct: 0,
    open_gap_pct: 0,
    distance_to_high_pct: 0,
    liquidity_bucket: 'PAPER',
    entry_zone: currency(trade.entry_price),
    stop_loss: trade.stop_loss,
    target_price: trade.target_price,
    expected_hold: trade.expected_hold,
    thesis: trade.thesis || trade.notes || 'Stored paper trade plan.',
    reasons: [trade.thesis || 'Stored from Paper Desk.'],
    risks: [`Stop loss is ${currency(trade.stop_loss)}.`],
    source: 'paper-db',
    live_signal: defaultLiveSignal({
      status: 'WATCH',
      label: 'Paper Plan',
      reason: 'This row is already staged in the paper desk.',
      strategy_label: trade.setup_family || 'Paper Plan',
      setup_family: trade.setup_family || 'Paper Plan',
    }),
  }
}

function riskPerShare(candidate: SwingCandidate) {
  return Math.max(candidate.last_price - candidate.stop_loss, 0)
}

function quantityForCapital(price: number, capital = PAPER_CAPITAL_PER_STOCK) {
  return Math.max(1, Math.floor(capital / Math.max(price, 0.01)))
}

function investedAmount(trade: PaperTrade) {
  return trade.entry_price * trade.quantity
}

function tradeReturnPct(pnl: number, trade: PaperTrade) {
  const invested = investedAmount(trade)
  return invested > 0 ? (pnl / invested) * 100 : 0
}

function riskAmount(trade: PaperTrade) {
  return Math.max(trade.entry_price - trade.stop_loss, 0) * trade.quantity
}

function sessionsRemaining(trade: PaperTrade) {
  return Math.max(0, trade.max_sessions - tradingSessionsElapsed(trade.planned_at))
}

function paperSourceLabel(trade: PaperTrade) {
  if (isSystemPaperTrade(trade)) return 'System intake'
  if (trade.notes.includes('Manual paper-stage')) return 'Manual'
  return trade.setup_family || 'Paper'
}

function createHistoricalRowFromCandidate(candidate: SwingCandidate): HistoricalScreenerRow {
  return {
    symbol: candidate.symbol,
    as_of: candidate.live_signal.as_of,
    setup_family: candidate.setup_family,
    strategy_id: candidate.live_signal.strategy_id,
    strategy_label: candidate.live_signal.strategy_label || candidate.setup_family,
    strategy_status: candidate.live_signal.strategy_status || 'Candidate',
    score: candidate.score,
    trend_label: candidate.regime_fit >= 70 ? 'Constructive' : 'Mixed',
    close: candidate.last_price,
    sma20: candidate.last_price,
    sma50: candidate.last_price,
    avg_volume20: 0,
    volume_ratio: 0,
    distance_to_20d_high_pct: candidate.distance_to_high_pct,
    distance_to_52w_high_pct: candidate.distance_to_high_pct,
    range_position_pct: 0,
    atr14: 0,
    atr_pct: 0,
    close_location: 0,
    gap_pct: candidate.open_gap_pct,
    rs60_rank: candidate.regime_fit,
    rs120_rank: candidate.regime_fit,
    market_breadth200: 0,
    planned_entry: candidate.entry_zone,
    stop_loss: candidate.stop_loss,
    target_price: candidate.target_price,
    risk_reward: candidate.risk_reward,
  }
}

function createHistoricalScreenerFromScanner(scanner: SwingScannerResponse): HistoricalScreenerResponse {
  return {
    updated_at: scanner.updated_at,
    range: 'live',
    signal_date: scanner.updated_at,
    total_rows: scanner.total_candidates,
    rows: scanner.candidates.map(createHistoricalRowFromCandidate),
    message: scanner.live_data
      ? null
      : 'Showing scanner candidates because the historical screener feed is unavailable.',
  }
}

function isSystemPaperTrade(trade: PaperTrade) {
  return trade.notes.includes('Auto-staged')
}

function paperSignalDate(trade: PaperTrade) {
  return trade.notes.match(/signal_date=([0-9-]+)/)?.[1] ?? null
}

function paperStrategyId(trade: PaperTrade) {
  return trade.notes.match(/strategy=([A-Za-z0-9_-]+)/)?.[1] ?? null
}

function isLivePaperQuote(trade: PaperTrade) {
  return trade.quote_source === 'dhan-live'
}

function isReferencePaperQuote(trade: PaperTrade) {
  return trade.quote_source.startsWith('last-close:') || trade.quote_source.startsWith('parquet-history:')
}

function paperQuoteLabel(trade: PaperTrade) {
  if (trade.quote_source === 'dhan-live') return 'Live price'
  if (trade.quote_source.startsWith('last-close:')) return `Last close ${trade.quote_source.replace('last-close:', '')}`
  if (trade.quote_source.startsWith('parquet-history:')) return `Historical close ${trade.quote_source.replace('parquet-history:', '')}`
  if (trade.quote_source === 'closed') return 'Closed'
  return 'Entry only'
}

function tradingSessionsElapsed(plannedAt?: string | null) {
  if (!plannedAt) return 0
  const planned = new Date(plannedAt)
  if (Number.isNaN(planned.getTime())) return 0

  let sessions = 0
  const cursor = new Date(planned)
  cursor.setHours(0, 0, 0, 0)
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  while (cursor <= today) {
    if (!isNseHoliday(cursor)) sessions += 1
    cursor.setDate(cursor.getDate() + 1)
  }
  return sessions
}

function isNseHoliday(date: Date) {
  const day = date.getDay()
  if (day === 0 || day === 6) return true
  return NSE_HOLIDAYS.has(localIsoDate(date))
}

function localIsoDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function isTradeExpired(trade: PaperTrade) {
  return trade.enabled === 1 && tradingSessionsElapsed(trade.planned_at) >= trade.max_sessions
}

function isTradeStopped(trade: PaperTrade) {
  return trade.enabled === 1 && trade.stop_loss > 0 && trade.current_price > 0 && trade.current_price <= trade.stop_loss
}

function isTradeTargetHit(trade: PaperTrade) {
  return trade.enabled === 1 && trade.target_price > 0 && trade.current_price > 0 && trade.current_price >= trade.target_price
}

function paperTradePayloadFromCandidate(candidate: SwingCandidate, sourceNote = '') {
  const entryPrice = Math.max(candidate.last_price, 0.01)
  const stopLoss = candidate.stop_loss
  const targetPrice = candidate.target_price
  const quantity = quantityForCapital(entryPrice)

  return {
    symbol: candidate.symbol,
    company_name: candidate.company_name,
    setup_family: candidate.setup_family,
    bias: candidate.bias,
    entry_price: entryPrice,
    quantity,
    max_sessions: PAPER_HOLD_SESSIONS,
    capital_allocated: PAPER_CAPITAL_PER_STOCK,
    stop_loss: stopLoss,
    target_price: targetPrice,
    expected_hold: `${PAPER_HOLD_SESSIONS} trading sessions`,
    thesis: candidate.thesis,
    notes: [
      sourceNote,
      `signal_date=${candidate.live_signal.as_of}`,
      `strategy=${candidate.live_signal.strategy_id}`,
      `strategy_status=${candidate.live_signal.strategy_status}`,
      ...candidate.reasons,
    ].filter(Boolean).join(' '),
  }
}

function paperTradePayloadFromTrade(trade: PaperTrade, overrides: Partial<Pick<PaperTrade, 'quantity' | 'capital_allocated'>> = {}) {
  const quantity = Math.max(
    1,
    Math.floor(
      overrides.quantity ??
        (overrides.capital_allocated ? quantityForCapital(trade.entry_price, overrides.capital_allocated) : trade.quantity),
    ),
  )
  const capitalAllocated = trade.entry_price * quantity
  return {
    symbol: trade.symbol,
    company_name: trade.company_name,
    setup_family: trade.setup_family,
    bias: trade.bias,
    entry_price: trade.entry_price,
    quantity,
    max_sessions: trade.max_sessions,
    capital_allocated: capitalAllocated,
    stop_loss: trade.stop_loss,
    target_price: trade.target_price,
    expected_hold: trade.expected_hold,
    thesis: trade.thesis,
    notes: trade.notes,
  }
}

function Surface({
  className = '',
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return <section className={`surface ${className}`.trim()}>{children}</section>
}

function CandidateStat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'positive' | 'warning' | 'danger' | 'neutral'
}) {
  return (
    <div className="stat-shell">
      <span className="micro-label">{label}</span>
      <span className={`stat-number tone-${tone}`}>{value}</span>
    </div>
  )
}

function BrokerBadge({ broker }: { broker: BrokerStatus }) {
  return (
    <div className={`broker-pill tone-${stateTone(broker.state)}`}>
      <span className="broker-dot" />
      <span>{broker.provider}</span>
      <span className="broker-state">{broker.state}</span>
    </div>
  )
}

function StagePill({ label, tone }: { label: string; tone: Tone }) {
  return <span className={`stage-pill tone-${tone}`}>{label}</span>
}

function HistoricalChart({
  history,
  range,
  onRangeChange,
}: {
  history: SymbolHistoryResponse | null
  range: HistoryRange
  onRangeChange: (range: HistoryRange) => void
}) {
  if (!history || history.candles.length === 0) {
    return (
      <Surface className="inner-surface chart-panel empty-panel">
        <div className="empty-icon-shell">
          <BarChart3 size={18} />
        </div>
        <h3>No chart data yet</h3>
        <p>Select a screener name with parquet history and the multi-year graph will open here.</p>
      </Surface>
    )
  }

  const candles = history.candles
  const highs = candles.map((candle) => candle.high)
  const lows = candles.map((candle) => candle.low)
  const minPrice = Math.min(...lows)
  const maxPrice = Math.max(...highs)
  const priceSpan = Math.max(maxPrice - minPrice, 0.01)
  const width = 640
  const height = 220
  const padX = 16
  const padY = 14
  const innerWidth = width - padX * 2
  const innerHeight = height - padY * 2
  const points = candles
    .map((candle, index) => {
      const x = padX + (index / Math.max(candles.length - 1, 1)) * innerWidth
      const y = padY + ((maxPrice - candle.close) / priceSpan) * innerHeight
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
  const areaPath = `${points ? `M ${points.replace(/ /, ' L ')}` : ''} L ${padX + innerWidth},${height - padY} L ${padX},${height - padY} Z`
  const summary = history.summary

  return (
    <Surface className="inner-surface chart-panel">
      <div className="chart-topbar">
        <div>
          <span className="eyebrow">Historical Chart</span>
          <h3>{history.symbol} price structure from parquet history</h3>
        </div>
        <div className="filter-strip compact-strip">
          {HISTORY_RANGES.map((option) => (
            <button
              key={option}
              type="button"
              className={range === option ? 'filter-chip filter-chip-active' : 'filter-chip'}
              onClick={() => onRangeChange(option)}
            >
              {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="chart-shell">
        <svg viewBox={`0 0 ${width} ${height}`} className="history-chart" role="img" aria-label={`${history.symbol} historical chart`}>
          <defs>
            <linearGradient id="price-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(80,216,144,0.42)" />
              <stop offset="100%" stopColor="rgba(80,216,144,0.02)" />
            </linearGradient>
          </defs>
          {[0, 1, 2, 3].map((line) => {
            const y = padY + (line / 3) * innerHeight
            return <line key={line} x1={padX} x2={padX + innerWidth} y1={y} y2={y} className="chart-grid-line" />
          })}
          <path d={areaPath} className="chart-area" />
          <polyline points={points} className="chart-line" />
        </svg>
      </div>

      {summary && (
        <div className="chart-summary-grid">
          <CandidateStat label="1M Return" value={`${summary.change_pct_1m >= 0 ? '+' : ''}${summary.change_pct_1m.toFixed(2)}%`} tone={summary.change_pct_1m >= 0 ? 'positive' : 'danger'} />
          <CandidateStat label="3M Return" value={`${summary.change_pct_3m >= 0 ? '+' : ''}${summary.change_pct_3m.toFixed(2)}%`} tone={summary.change_pct_3m >= 0 ? 'positive' : 'danger'} />
          <CandidateStat label="1Y Return" value={`${summary.change_pct_1y >= 0 ? '+' : ''}${summary.change_pct_1y.toFixed(2)}%`} tone={summary.change_pct_1y >= 0 ? 'positive' : 'danger'} />
          <CandidateStat label="52W Range" value={`Rs ${summary.low_52w.toFixed(0)} - ${summary.high_52w.toFixed(0)}`} />
        </div>
      )}
    </Surface>
  )
}

function HistoricalScreenerTableRow({
  row,
  active,
  onSelect,
}: {
  row: HistoricalScreenerRow
  active: boolean
  onSelect: (symbol: string) => void
}) {
  return (
    <tr className={active ? 'scanner-row scanner-row-active' : 'scanner-row'}>
        <td>
          <button type="button" className="row-link" onClick={() => onSelect(row.symbol)}>
            <strong>{row.symbol}</strong>
            <span>{row.as_of}</span>
          </button>
        </td>
        <td>
          <span className={`strategy-pill strategy-pill-${row.strategy_status.toLowerCase()}`}>
            {row.strategy_label}
            <small>{row.strategy_status}</small>
          </span>
        </td>
        <td>{row.setup_family}</td>
      <td>{row.score}</td>
      <td>{row.trend_label}</td>
      <td>Rs {row.close.toFixed(2)}</td>
      <td>{row.volume_ratio.toFixed(2)}x</td>
      <td>{row.distance_to_20d_high_pct.toFixed(2)}%</td>
      <td>{row.distance_to_52w_high_pct.toFixed(2)}%</td>
    </tr>
  )
}

function DetailPanel({
  candidate,
  historicalRow,
  history,
  historyRange,
  watchlisted,
  queued,
  onWatch,
  onQueue,
  onHistoryRangeChange,
}: {
  candidate: SwingCandidate | null
  historicalRow: HistoricalScreenerRow | null
  history: SymbolHistoryResponse | null
  historyRange: HistoryRange
  watchlisted: boolean
  queued: boolean
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
  onHistoryRangeChange: (range: HistoryRange) => void
}) {
  const resolvedCandidate = candidate ?? (historicalRow ? createCandidateFromHistoricalRow(historicalRow) : null)

  if (!resolvedCandidate) {
    return (
      <Surface className="detail-panel empty-panel">
        <div className="empty-icon-shell">
          <Sparkles size={18} />
        </div>
        <h3>Select a candidate</h3>
        <p>The thesis panel will open here with setup detail, reasons, risks, and actions for watchlist or paper trade.</p>
      </Surface>
    )
  }

  const paperReady = canSendToPaper(resolvedCandidate)
  const liveSignal = resolvedCandidate.live_signal

  return (
    <Surface className="detail-panel">
      <HistoricalChart history={history} range={historyRange} onRangeChange={onHistoryRangeChange} />

      <div className="detail-header">
        <div>
          <p className="detail-symbol">{resolvedCandidate.symbol}</p>
          <h2>{resolvedCandidate.company_name}</h2>
          <p className="detail-subline">
            {resolvedCandidate.setup_family} | {resolvedCandidate.confidence} | Source: {resolvedCandidate.source}
          </p>
        </div>
        <div className="detail-actions">
          <button type="button" className={watchlisted ? 'ghost-button active-ghost' : 'ghost-button'} onClick={() => onWatch(resolvedCandidate)}>
            <Bookmark size={14} />
            <span>{watchlisted ? 'On Watchlist' : 'Add To Watchlist'}</span>
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={() => onQueue(resolvedCandidate)}
            disabled={!paperReady}
            title={paperReady ? 'Send this setup to Paper Desk for 7 trading sessions' : 'Needs a valid stop loss before paper trading'}
          >
            <WalletCards size={14} />
            <span>{queued ? 'Refresh Paper Plan' : 'Send To Paper Desk'}</span>
          </button>
        </div>
      </div>

      <div className="detail-metrics-grid">
        <CandidateStat label="Last Price" value={currency(resolvedCandidate.last_price)} />
        <CandidateStat
          label="Day Change"
          value={`${resolvedCandidate.day_change_pct >= 0 ? '+' : ''}${resolvedCandidate.day_change_pct.toFixed(2)}%`}
          tone={resolvedCandidate.day_change_pct >= 0 ? 'positive' : 'danger'}
        />
        <CandidateStat label="Regime Fit" value={`${resolvedCandidate.regime_fit}/100`} />
        <CandidateStat label="Risk / Reward" value={`${resolvedCandidate.risk_reward.toFixed(2)}R`} tone="positive" />
        <CandidateStat label="Live Signal" value={liveSignal.label} tone={signalTone(liveSignal.status)} />
        <CandidateStat label="Strategy" value={liveSignal.strategy_label} tone={signalTone(liveSignal.status)} />
      </div>

      <div className={`live-signal-strip ${signalClass(liveSignal.status)}`}>
        <div>
          <span className="micro-label">{liveSignal.strategy_status}</span>
          <strong>{liveSignal.label}</strong>
        </div>
        <p>{liveSignal.reason}</p>
      </div>

      <Surface className="inner-surface thesis-panel">
        <span className="eyebrow">Why It Qualified</span>
        <p>{resolvedCandidate.thesis}</p>
      </Surface>

      <div className="split-grid">
        <Surface className="inner-surface">
          <span className="eyebrow">Trade Plan</span>
          <div className="plan-grid">
            <div>
              <span className="micro-label">Entry Zone</span>
              <strong>{resolvedCandidate.entry_zone}</strong>
            </div>
            <div>
              <span className="micro-label">Stop Loss</span>
              <strong>{currency(resolvedCandidate.stop_loss)}</strong>
            </div>
            <div>
              <span className="micro-label">Target</span>
              <strong>{currency(resolvedCandidate.target_price)}</strong>
            </div>
            <div>
              <span className="micro-label">Expected Hold</span>
              <strong>{resolvedCandidate.expected_hold}</strong>
            </div>
          </div>
        </Surface>

        <Surface className="inner-surface">
          <span className="eyebrow">Market Read</span>
          <div className="plan-grid">
            <div>
              <span className="micro-label">Open Gap</span>
              <strong>{resolvedCandidate.open_gap_pct >= 0 ? '+' : ''}{resolvedCandidate.open_gap_pct.toFixed(2)}%</strong>
            </div>
            <div>
              <span className="micro-label">Distance To High</span>
              <strong>{resolvedCandidate.distance_to_high_pct.toFixed(2)}%</strong>
            </div>
            <div>
              <span className="micro-label">Liquidity</span>
              <strong>{resolvedCandidate.liquidity_bucket}</strong>
            </div>
            <div>
              <span className="micro-label">Bias</span>
              <strong>{resolvedCandidate.bias}</strong>
            </div>
          </div>
        </Surface>
      </div>

      <div className="split-grid">
        <Surface className="inner-surface">
          <span className="eyebrow">Supporting Reasons</span>
          <ul className="detail-list">
            {resolvedCandidate.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </Surface>

        <Surface className="inner-surface">
          <span className="eyebrow">Risk Box</span>
          <ul className="detail-list warning-list">
            {resolvedCandidate.risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </Surface>
      </div>
    </Surface>
  )
}

function StockAnalysisPanel({
  candidate,
  historicalRow,
  history,
}: {
  candidate: SwingCandidate | null
  historicalRow: HistoricalScreenerRow | null
  history: SymbolHistoryResponse | null
}) {
  const resolvedCandidate = candidate ?? (historicalRow ? createCandidateFromHistoricalRow(historicalRow) : null)

  if (!resolvedCandidate) {
    return (
      <Surface className="stock-analysis-panel empty-panel">
        <div className="empty-icon-shell">
          <Target size={18} />
        </div>
        <h3>No analysis loaded</h3>
        <p>Pick a stock from Home, Scanner, Watchlist, or Paper Desk to open the full reasoning view.</p>
      </Surface>
    )
  }

  const risk = riskPerShare(resolvedCandidate)
  const reward = Math.max(resolvedCandidate.target_price - resolvedCandidate.last_price, 0)
  const summary = history?.summary

  return (
    <Surface className="stock-analysis-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Analysis Plan</span>
          <h2>Why this trade plan exists</h2>
        </div>
        <StagePill label={resolvedCandidate.confidence} tone={resolvedCandidate.score >= 85 ? 'positive' : 'warning'} />
      </div>

      <div className="analysis-step-list">
        <div className="analysis-step">
          <span>1</span>
          <div>
            <strong>Setup qualification</strong>
            <p>{resolvedCandidate.setup_family} with score {resolvedCandidate.score}/100 and regime fit {resolvedCandidate.regime_fit}/100.</p>
          </div>
        </div>
        <div className="analysis-step">
          <span>2</span>
          <div>
            <strong>Historical structure</strong>
            <p>
              {historicalRow
                ? `${historicalRow.trend_label}, ${historicalRow.distance_to_20d_high_pct.toFixed(2)}% from 20D high, ${historicalRow.distance_to_52w_high_pct.toFixed(2)}% from 52W high.`
                : 'Using live candidate thesis because detailed parquet row is not available for this symbol.'}
            </p>
          </div>
        </div>
        <div className="analysis-step">
          <span>3</span>
          <div>
            <strong>Participation check</strong>
            <p>
              {historicalRow
                ? `Volume is ${historicalRow.volume_ratio.toFixed(2)}x versus the 20-day average, with average volume around ${historicalRow.avg_volume20.toLocaleString('en-IN')}.`
                : `${resolvedCandidate.liquidity_bucket} liquidity bucket with current day change ${resolvedCandidate.day_change_pct.toFixed(2)}%.`}
            </p>
          </div>
        </div>
        <div className="analysis-step">
          <span>4</span>
          <div>
            <strong>Risk plan</strong>
            <p>Entry {resolvedCandidate.entry_zone}, stop {currency(resolvedCandidate.stop_loss)}, target {currency(resolvedCandidate.target_price)}. Risk/share {currency(risk)}, reward/share {currency(reward)}.</p>
          </div>
        </div>
      </div>

      <div className="analysis-mini-grid">
        <CandidateStat label="Risk / Reward" value={`${resolvedCandidate.risk_reward.toFixed(2)}R`} tone="positive" />
        <CandidateStat label="Expected Hold" value={resolvedCandidate.expected_hold} />
        <CandidateStat label="1M Hist" value={summary ? `${summary.change_pct_1m >= 0 ? '+' : ''}${summary.change_pct_1m.toFixed(2)}%` : 'N/A'} tone={(summary?.change_pct_1m ?? 0) >= 0 ? 'positive' : 'danger'} />
        <CandidateStat label="3M Hist" value={summary ? `${summary.change_pct_3m >= 0 ? '+' : ''}${summary.change_pct_3m.toFixed(2)}%` : 'N/A'} tone={(summary?.change_pct_3m ?? 0) >= 0 ? 'positive' : 'danger'} />
      </div>
    </Surface>
  )
}

function StockDetailView({
  candidate,
  historicalRow,
  history,
  historyRange,
  watchlisted,
  queued,
  onWatch,
  onQueue,
  onHistoryRangeChange,
  onBack,
}: {
  candidate: SwingCandidate | null
  historicalRow: HistoricalScreenerRow | null
  history: SymbolHistoryResponse | null
  historyRange: HistoryRange
  watchlisted: boolean
  queued: boolean
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
  onHistoryRangeChange: (range: HistoryRange) => void
  onBack: () => void
}) {
  const resolvedCandidate = candidate ?? (historicalRow ? createCandidateFromHistoricalRow(historicalRow) : null)

  return (
    <div className="page-stack">
      <div className="stock-detail-topbar">
        <button type="button" className="ghost-button" onClick={onBack}>
          <ArrowUpRight size={14} />
          <span>Back To Scanner</span>
        </button>
        {resolvedCandidate && (
          <div className="mini-chip">
            <Target size={14} />
            <span>{resolvedCandidate.symbol} full stock view</span>
          </div>
        )}
      </div>

      <div className="stock-detail-layout">
        <DetailPanel
          candidate={resolvedCandidate}
          historicalRow={historicalRow}
          history={history}
          historyRange={historyRange}
          watchlisted={watchlisted}
          queued={queued}
          onWatch={onWatch}
          onQueue={onQueue}
          onHistoryRangeChange={onHistoryRangeChange}
        />
        <StockAnalysisPanel candidate={resolvedCandidate} historicalRow={historicalRow} history={history} />
      </div>
    </div>
  )
}

function HomeView({
  home,
  loading,
  loadError,
  watchlistCount,
  paperCount,
  selectedSymbol,
  onSelect,
  onNavigate,
  onQueue,
  onReload,
}: {
  home: SwingHomeResponse | null
  loading: boolean
  loadError: string
  watchlistCount: number
  paperCount: number
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
  onNavigate: (view: View) => void
  onQueue: (candidate: SwingCandidate) => void
  onReload: () => void
}) {
  const [strategyFilter, setStrategyFilter] = useState('All')
  const [directionFilter, setDirectionFilter] = useState<'all' | 'long' | 'short' | 'high'>('all')
  const [sortBy, setSortBy] = useState<'score' | 'risk' | 'strategy' | 'latest'>('score')
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null)

  if (!home) {
    if (loading) return <PageSkeleton />
    return (
      <div className="page-stack">
        <Surface>
          <div className="empty-action-panel">
            <LayoutDashboard size={22} />
            <div>
              <span className="eyebrow">Dashboard</span>
              <h2>Dashboard data did not load</h2>
              <p>{loadError || 'The dashboard is ready to fetch the market overview and top picks.'}</p>
              <div className="empty-action-buttons">
                <button type="button" className="primary-button" onClick={onReload}>
                  <RefreshCw size={14} />
                  <span>Reload Dashboard</span>
                </button>
              </div>
            </div>
          </div>
        </Surface>
      </div>
    )
  }

  const entryNowCount = home.top_candidates.filter((candidate) => candidate.live_signal.status === 'ENTRY_NOW').length
  const strategies = ['All', ...Array.from(new Set(home.top_candidates.map((candidate) => candidate.live_signal.strategy_label || candidate.setup_family)))]
  const filteredTopPicks = home.top_candidates
    .filter((candidate) => {
      const strategyName = candidate.live_signal.strategy_label || candidate.setup_family
      const matchesStrategy = strategyFilter === 'All' || strategyName === strategyFilter
      const matchesDirection =
        directionFilter === 'all'
        || (directionFilter === 'long' && candidate.bias.toLowerCase().includes('long'))
        || (directionFilter === 'short' && candidate.bias.toLowerCase().includes('short'))
        || (directionFilter === 'high' && candidate.score >= 85)
      return matchesStrategy && matchesDirection
    })
    .sort((a, b) => {
      if (sortBy === 'risk') return b.risk_reward - a.risk_reward
      if (sortBy === 'strategy') return (a.live_signal.strategy_label || a.setup_family).localeCompare(b.live_signal.strategy_label || b.setup_family)
      if (sortBy === 'latest') return b.live_signal.as_of.localeCompare(a.live_signal.as_of)
      return b.score - a.score
    })
  const detailCandidate =
    filteredTopPicks.find((candidate) => candidate.symbol === detailSymbol)
    ?? filteredTopPicks.find((candidate) => candidate.symbol === selectedSymbol)
    ?? filteredTopPicks[0]
    ?? null
  const topStrategy = home.setup_mix[0]?.family ?? strategies.find((strategy) => strategy !== 'All') ?? 'No active strategy'

  return (
    <div className="home-dashboard">
      <Surface className="home-market-panel">
        <div className="home-market-copy">
          <span className="eyebrow">Market Pulse</span>
          <h2>{home.market_regime.label}</h2>
          <p>{home.market_regime.summary}</p>
        </div>
        <div className="home-kpi-grid">
          <CandidateStat label="Adv / Dec" value={`${home.market_regime.advances} / ${home.market_regime.declines}`} />
          <CandidateStat label="Breadth" value={home.market_regime.breadth_ratio.toFixed(2)} tone={home.market_regime.breadth_ratio >= 1 ? 'positive' : 'warning'} />
          <CandidateStat label="Active Signals" value={String(home.scanner_count)} tone="positive" />
          <CandidateStat label="Top Strategy" value={topStrategy} tone="positive" />
          <CandidateStat label="Enter Now" value={String(entryNowCount)} tone={entryNowCount > 0 ? 'positive' : 'warning'} />
          <CandidateStat label="Paper P/L" value="Open desk" tone={paperCount > 0 ? 'warning' : 'neutral'} />
        </div>
      </Surface>

      <div className="market-ribbon compact-market-ribbon">
        {home.top_candidates.slice(0, 5).map((candidate) => (
          <button
            key={candidate.symbol}
            type="button"
            onClick={() => onSelect(candidate.symbol)}
            className={candidate.symbol === selectedSymbol ? 'ticker-tile ticker-tile-active' : 'ticker-tile'}
          >
            <span>{candidate.symbol}</span>
            <strong>{currency(candidate.last_price)}</strong>
            <em className={candidate.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'}>
              {candidate.day_change_pct >= 0 ? '+' : ''}{candidate.day_change_pct.toFixed(2)}%
            </em>
            <small className={`ticker-signal ${signalClass(candidate.live_signal.status)}`}>{candidate.live_signal.label}</small>
          </button>
        ))}
      </div>

      <div className="home-grid">
        <Surface className="home-list-panel">
          <div className="compact-section-head">
            <div>
              <span className="eyebrow">Top Picks</span>
              <h2>Ranked trade ideas</h2>
            </div>
            <div className="mini-chip">
              <Activity size={14} />
              <span>{filteredTopPicks.length} shown</span>
            </div>
          </div>
          <div className="top-picks-toolbar">
            <div className="filter-strip compact-filter-strip">
              {strategies.slice(0, 6).map((strategy) => (
                <button
                  key={strategy}
                  type="button"
                  onClick={() => setStrategyFilter(strategy)}
                  className={strategyFilter === strategy ? 'filter-chip active-ghost' : 'filter-chip'}
                >
                  {strategy}
                </button>
              ))}
            </div>
            <div className="filter-strip compact-filter-strip">
              {[
                ['all', 'All'],
                ['long', 'Long'],
                ['short', 'Short'],
                ['high', 'High confidence'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setDirectionFilter(id as typeof directionFilter)}
                  className={directionFilter === id ? 'filter-chip active-ghost' : 'filter-chip'}
                >
                  {label}
                </button>
              ))}
              <select className="select-input top-picks-sort" value={sortBy} onChange={(event) => setSortBy(event.currentTarget.value as typeof sortBy)}>
                <option value="score">Score</option>
                <option value="risk">Risk/reward</option>
                <option value="strategy">Strategy</option>
                <option value="latest">Latest signal</option>
              </select>
            </div>
          </div>
          <div className="home-candidate-list">
            {filteredTopPicks.slice(0, 8).map((candidate, index) => (
              <div
                key={candidate.symbol}
                className={candidate.symbol === selectedSymbol ? 'home-candidate-row home-candidate-row-active' : 'home-candidate-row'}
              >
                <span className="rank-cell">{index + 1}</span>
                <button type="button" className="home-symbol-cell row-link" onClick={() => setDetailSymbol(candidate.symbol)}>
                  <strong>{candidate.symbol}</strong>
                  <em>{candidate.company_name}</em>
                </button>
                <span className="home-setup-cell">{candidate.setup_family}</span>
                <span className="home-price-cell">{currency(candidate.last_price)}</span>
                <span className={`home-change-cell ${candidate.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'}`}>
                  {candidate.day_change_pct >= 0 ? '+' : ''}{candidate.day_change_pct.toFixed(2)}%
                </span>
                <span className="home-score-cell">{candidate.score}</span>
                <span className={`home-signal-cell ${signalClass(candidate.live_signal.status)}`}>{candidate.live_signal.label}</span>
                <span className="home-row-actions">
                  <button type="button" title="View in Scanner" className="ghost-button ghost-button-small" onClick={() => onSelect(candidate.symbol)}>
                    <Radar size={13} />
                  </button>
                  <button type="button" title="Backtest setup" className="ghost-button ghost-button-small" onClick={() => onNavigate('backtests')}>
                    <BarChart3 size={13} />
                  </button>
                  <button type="button" title="Send to Paper Desk" className="ghost-button ghost-button-small" onClick={() => {
                    onQueue(candidate)
                    onNavigate('portfolio')
                  }}>
                    <WalletCards size={13} />
                  </button>
                </span>
              </div>
            ))}
          </div>
        </Surface>

        <div className="home-side-stack">
          {detailCandidate && (
            <Surface className="home-mini-panel">
              <div className="compact-section-head">
                <div>
                  <span className="eyebrow">Signal Details</span>
                  <h2>{detailCandidate.symbol} under {detailCandidate.live_signal.strategy_label || detailCandidate.setup_family}</h2>
                </div>
              </div>
              <div className="detail-metrics-grid compact-grid">
                <CandidateStat label="Entry" value={currency(detailCandidate.last_price)} />
                <CandidateStat label="Stop" value={currency(detailCandidate.stop_loss)} tone="danger" />
                <CandidateStat label="Target" value={currency(detailCandidate.target_price)} tone="positive" />
                <CandidateStat label="R/R" value={detailCandidate.risk_reward.toFixed(2)} tone={detailCandidate.risk_reward >= 1.5 ? 'positive' : 'warning'} />
              </div>
              <p className="settings-copy">{detailCandidate.live_signal.reason || detailCandidate.thesis}</p>
              <div className="hero-actions">
                <button type="button" className="ghost-button" onClick={() => onSelect(detailCandidate.symbol)}>
                  <Radar size={14} />
                  <span>Scanner</span>
                </button>
                <button type="button" className="ghost-button" onClick={() => onNavigate('backtests')}>
                  <BarChart3 size={14} />
                  <span>Backtest</span>
                </button>
                <button type="button" className="primary-button" onClick={() => {
                  onQueue(detailCandidate)
                  onNavigate('portfolio')
                }}>
                  <WalletCards size={14} />
                  <span>Paper</span>
                </button>
              </div>
            </Surface>
          )}

          <Surface className="home-mini-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Workflow</span>
                <h2>Desk status</h2>
              </div>
            </div>
            <div className="desk-status-grid">
              <CandidateStat label="Watchlist" value={String(watchlistCount)} />
              <CandidateStat label="Paper Plans" value={String(paperCount)} tone={paperCount > 0 ? 'positive' : 'neutral'} />
            </div>
            <div className="compact-workflow">
              <div><Radar size={15} /><span>Scan</span></div>
              <div><Bookmark size={15} /><span>Shortlist</span></div>
              <div><WalletCards size={15} /><span>Paper</span></div>
            </div>
          </Surface>

          <Surface className="home-mini-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Setup Mix</span>
                <h2>What is working</h2>
              </div>
            </div>
            <div className="setup-mix-compact">
              {home.setup_mix.slice(0, 4).map((mix) => (
                <div key={mix.family} className="setup-mix-row">
                  <span>{mix.family}</span>
                  <strong>{mix.count}</strong>
                  <em>{mix.avg_score.toFixed(1)}</em>
                </div>
              ))}
            </div>
          </Surface>
        </div>
      </div>
    </div>
  )
}

function ScannerView({
  scanner,
  historicalScreener,
  freshSignals,
  bambooLatest,
  selectedSymbol,
  onSelect,
  onStageFresh,
  onRefreshCache,
  onReload,
  loading,
  loadError,
  stagingFresh,
  refreshingCache,
}: {
  scanner: SwingScannerResponse | null
  historicalScreener: HistoricalScreenerResponse | null
  freshSignals: FreshSignalsResponse | null
  bambooLatest: BambooLatestResponse | null
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
  onStageFresh: () => void
  onRefreshCache: () => void
  onReload: () => void
  loading: boolean
  loadError: string
  stagingFresh: boolean
  refreshingCache: boolean
}) {
  const [search, setSearch] = useState('')
  const [familyFilter, setFamilyFilter] = useState<string>('All')
  const [strategyFilter, setStrategyFilter] = useState<string>('All')
  const [page, setPage] = useState(1)
  const pageSize = 12
  const deferredSearch = useDeferredValue(search)

  const families = useMemo(() => {
    const options = new Set<string>(['All'])
    const sourceRows = freshSignals?.rows.length ? freshSignals.rows : historicalScreener?.rows ?? []
    sourceRows.forEach((row) => options.add(row.setup_family))
    return Array.from(options)
  }, [freshSignals, historicalScreener])

  const strategies = useMemo(() => {
    const options = new Map<string, string>([['All', 'All Strategies']])
    const sourceRows = freshSignals?.rows.length ? freshSignals.rows : historicalScreener?.rows ?? []
    sourceRows.forEach((row) => {
      options.set(row.strategy_id, row.strategy_label)
    })
    return Array.from(options.entries()).map(([id, label]) => ({ id, label }))
  }, [freshSignals, historicalScreener])

  const filtered = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase()
    const sourceRows = freshSignals?.rows.length ? freshSignals.rows : historicalScreener?.rows ?? []
    return sourceRows.filter((row) => {
      const matchesFamily = familyFilter === 'All' || row.setup_family === familyFilter
      const matchesStrategy = strategyFilter === 'All' || row.strategy_id === strategyFilter
      const matchesSearch =
        !term ||
        row.symbol.toLowerCase().includes(term)
        || row.strategy_label.toLowerCase().includes(term)
        || row.strategy_status.toLowerCase().includes(term)
      return matchesFamily && matchesStrategy && matchesSearch
    })
  }, [deferredSearch, familyFilter, freshSignals, historicalScreener, strategyFilter])

  useEffect(() => {
    setPage(1)
  }, [deferredSearch, familyFilter, strategyFilter])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)
  const groupedRows = useMemo(() => {
    const groups = new Map<string, HistoricalScreenerRow[]>()
    filtered.forEach((row) => {
      const key = row.strategy_label || strategyLabel(row.strategy_id)
      groups.set(key, [...(groups.get(key) ?? []), row])
    })
    return Array.from(groups.entries()).map(([label, rows]) => ({
      label,
      rows: rows.sort((a, b) => b.score - a.score),
      avgScore: rows.reduce((sum, row) => sum + row.score, 0) / Math.max(rows.length, 1),
      best: rows.reduce((best, row) => (row.score > best.score ? row : best), rows[0]),
    }))
  }, [filtered])

  if (!scanner || !historicalScreener) {
    if (loading) return <PageSkeleton />
    return (
      <div className="page-stack">
        <Surface>
          <div className="empty-action-panel">
            <Radar size={22} />
            <div>
              <span className="eyebrow">Scanner</span>
              <h2>Scanner data did not load</h2>
              <p>{loadError || 'The scanner is ready to fetch strategy signals and grouped opportunity rows.'}</p>
              <div className="empty-action-buttons">
                <button type="button" className="primary-button" onClick={onReload}>
                  <RefreshCw size={14} />
                  <span>Reload Scanner</span>
                </button>
                <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache}>
                  <Database size={14} />
                  <span>{refreshingCache ? 'Refreshing Cache' : 'Refresh Feature Cache'}</span>
                </button>
              </div>
            </div>
          </div>
        </Surface>
      </div>
    )
  }
  const bambooSignals = bambooLatest?.top_signals ?? []
  const freshSignalCount = freshSignals?.new_rows ?? 0
  const latestSignalDate = freshSignals?.signal_date ?? historicalScreener.signal_date ?? historicalScreener.rows[0]?.as_of ?? 'not available'

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head scanner-toolbar">
          <div>
            <span className="eyebrow">Bamboo MTF Breakout</span>
            <h2>Latest raw strategy signals</h2>
          </div>
          <div className="toolbar-right screener-toolbar-meta">
            <div className="mini-chip">
              <Target size={14} />
              <span>{bambooLatest?.unique_symbols ?? 0} unique stocks</span>
            </div>
            <div className="mini-chip">
              <Database size={14} />
              <span>Signal date {bambooLatest?.signal_date ?? 'not available'}</span>
            </div>
          </div>
        </div>
        {bambooSignals.length > 0 ? (
          <div className="bamboo-signal-table">
            <div className="bamboo-signal-row bamboo-signal-head">
              <span>Stock</span>
              <span>Close</span>
              <span>Stop</span>
              <span>Target</span>
              <span>Risk</span>
              <span>Vol</span>
              <span>Action</span>
            </div>
            {bambooSignals.map((signal) => (
              <button
                key={`${signal.strategy}-${signal.symbol}`}
                type="button"
                className={signal.symbol === selectedSymbol ? 'bamboo-signal-row bamboo-signal-active' : 'bamboo-signal-row'}
                onClick={() => onSelect(signal.symbol)}
              >
                <strong>{signal.symbol}<small>{signal.strategy.replace(/_/g, ' ')}</small></strong>
                <span>{currency(signal.close)}</span>
                <span>{currency(signal.stop)}</span>
                <span>{currency(signal.target_from_close)}</span>
                <span className={signal.risk_pct_vs_close <= 4 ? 'tone-positive' : signal.risk_pct_vs_close <= 8 ? 'tone-warning' : 'tone-danger'}>
                  {signal.risk_pct_vs_close.toFixed(2)}%
                </span>
                <span>{signal.relvol.toFixed(2)}x</span>
                <span className="watch-signal-pill signal-watch">Review</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-table">
            <CircleAlert size={18} />
            <p>{bambooLatest?.message ?? 'No Bamboo signals are available yet.'}</p>
          </div>
        )}
      </Surface>

      <Surface>
        <div className="section-head scanner-toolbar">
          <div>
            <span className="eyebrow">Strategy Scanner</span>
              <h2>Signals grouped by active strategy from {latestSignalDate}</h2>
          </div>
          <div className="toolbar-right screener-toolbar-meta">
            <div className="mini-chip">
              <Database size={14} />
              <span>{historicalScreener.total_rows} screened names</span>
            </div>
            <div className="mini-chip">
              <Activity size={14} />
              <span>{scanner.live_data ? 'Live Dhan overlay active' : 'Live quote overlay unavailable'}</span>
            </div>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="text-input"
              placeholder="Search symbol"
            />
            <button type="button" className="primary-button" onClick={onStageFresh} disabled={stagingFresh}>
              <RefreshCw size={14} className={stagingFresh ? 'spin' : ''} />
              <span>{stagingFresh ? 'Staging' : 'Stage Fresh Signals'}</span>
            </button>
            <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache}>
              <Database size={14} />
              <span>{refreshingCache ? 'Caching' : 'Refresh Cache'}</span>
            </button>
          </div>
        </div>

        <div className="scanner-summary-strip">
          <CandidateStat label="New Signals" value={String(freshSignalCount)} tone={freshSignalCount > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Auto-Staged" value={String(freshSignals?.staged_rows ?? 0)} tone={(freshSignals?.staged_rows ?? 0) > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Already Seen" value={String(freshSignals?.seen_rows ?? 0)} tone={(freshSignals?.seen_rows ?? 0) > 0 ? 'warning' : 'neutral'} />
          <CandidateStat label="Last Data Date" value={latestSignalDate} />
        </div>

        <div className="filter-strip">
          {strategies.map((strategy) => (
            <button
              key={strategy.id}
              type="button"
              onClick={() => setStrategyFilter(strategy.id)}
              className={strategyFilter === strategy.id ? 'filter-chip filter-chip-active' : 'filter-chip'}
            >
              {strategy.label}
            </button>
          ))}
        </div>

        <div className="filter-strip compact-filter-strip">
          {families.map((family) => (
            <button
              key={family}
              type="button"
              onClick={() => setFamilyFilter(family)}
              className={familyFilter === family ? 'filter-chip filter-chip-active' : 'filter-chip'}
            >
              {family}
            </button>
          ))}
        </div>

          <div className="scanner-layout scanner-layout-full">
          {strategyFilter === 'All' ? (
            <div className="scanner-strategy-groups">
              {groupedRows.map((group) => (
                <Surface key={group.label} className="inner-surface scanner-strategy-section">
                  <div className="scanner-strategy-head">
                    <div>
                      <span className="eyebrow">{group.rows.length} signals</span>
                      <h3>{group.label}</h3>
                    </div>
                    <div className="hero-actions">
                      <CandidateStat label="Avg Score" value={group.avgScore.toFixed(1)} tone={group.avgScore >= 80 ? 'positive' : 'warning'} />
                      <CandidateStat label="Pinned Best" value={group.best?.symbol ?? 'N/A'} tone="positive" />
                    </div>
                  </div>
                  <div className="scanner-table-shell">
                    <table className="scanner-table">
                      <thead>
                        <tr>
                          <th>Symbol</th>
                          <th>Strategy</th>
                          <th>Setup</th>
                          <th>Score</th>
                          <th>Trend</th>
                          <th>Close</th>
                          <th>Vol Ratio</th>
                          <th>20D High</th>
                          <th>52W High</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.rows.slice(0, 6).map((row) => (
                          <HistoricalScreenerTableRow
                            key={`${group.label}-${row.symbol}`}
                            row={row}
                            active={row.symbol === selectedSymbol}
                            onSelect={onSelect}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Surface>
              ))}
              {groupedRows.length === 0 && (
                <div className="empty-table">
                  <CircleAlert size={18} />
                  <p>{historicalScreener.message ?? 'No strategy signals matched the current filters.'}</p>
                </div>
              )}
            </div>
          ) : (
          <div className="scanner-table-shell">
            <table className="scanner-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Setup</th>
                  <th>Score</th>
                  <th>Trend</th>
                  <th>Close</th>
                  <th>Vol Ratio</th>
                  <th>20D High</th>
                  <th>52W High</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((row) => (
                  <HistoricalScreenerTableRow
                    key={row.symbol}
                    row={row}
                    active={row.symbol === selectedSymbol}
                    onSelect={onSelect}
                  />
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <div className="empty-table">
                <CircleAlert size={18} />
                <p>{freshSignals?.message ?? 'No new unique signals matched the current filters. Existing names are tracked in Paper Desk instead of being shown again.'}</p>
              </div>
            )}
          </div>
          )}
          {strategyFilter !== 'All' && filtered.length > 0 && (
            <div className="pagination-bar">
              <div>
                Showing {(safePage - 1) * pageSize + 1}-{Math.min(safePage * pageSize, filtered.length)} of {filtered.length}
              </div>
              <div className="pagination-actions">
                <button type="button" className="ghost-button ghost-button-small" disabled={safePage === 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                  Previous
                </button>
                <span>Page {safePage} / {totalPages}</span>
                <button type="button" className="ghost-button ghost-button-small" disabled={safePage === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </Surface>
    </div>
  )
}

function WatchlistView({
  watchlist,
  paperQueue,
  onSelect,
  onQueue,
  onRemove,
}: {
  watchlist: SwingCandidate[]
  paperQueue: SwingCandidate[]
  onSelect: (symbol: string) => void
  onQueue: (candidate: SwingCandidate) => void
  onRemove: (symbol: string) => void
}) {
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const queueSymbols = useMemo(() => new Set(paperQueue.map((candidate) => candidate.symbol)), [paperQueue])
  const filteredWatchlist = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase()
    if (!term) return watchlist
    return watchlist.filter((candidate) => (
      candidate.symbol.toLowerCase().includes(term) ||
      candidate.company_name.toLowerCase().includes(term) ||
      candidate.setup_family.toLowerCase().includes(term)
    ))
  }, [deferredSearch, watchlist])
  const queuedCount = watchlist.filter((item) => queueSymbols.has(item.symbol)).length
  const readyCount = watchlist.filter((candidate) => candidate.score >= 85 && candidate.risk_reward >= 1.6).length
  const avgScore = watchlist.length
    ? Math.round(watchlist.reduce((sum, candidate) => sum + candidate.score, 0) / watchlist.length)
    : 0

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head watchlist-headline">
          <div>
            <span className="eyebrow">Watchlist</span>
            <h2>Track saved stocks like a broker watchlist</h2>
          </div>
          <div className="hero-actions">
            <CandidateStat label="Tracked Stocks" value={String(watchlist.length)} />
            <CandidateStat label="In Paper Desk" value={String(queuedCount)} tone={queuedCount > 0 ? 'positive' : 'neutral'} />
            <CandidateStat label="Ready" value={String(readyCount)} tone="positive" />
            <CandidateStat label="Avg Score" value={watchlist.length ? String(avgScore) : '0'} />
          </div>
        </div>

        {watchlist.length === 0 ? (
          <div className="portfolio-empty">
            <Bookmark size={20} />
            <p>Your watchlist is empty. Save stocks from the scanner first, then review them here.</p>
          </div>
        ) : (
          <div className="watchlist-workspace">
            <div className="watchlist-toolbar">
              <div className="mini-chip">
                <Bookmark size={14} />
                <span>{filteredWatchlist.length} shown</span>
              </div>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="text-input watchlist-search"
                placeholder="Search symbol, company, setup"
              />
            </div>

            <Surface className="inner-surface watchlist-table-surface">
              <div className="watchlist-table">
                <div className="watchlist-row watchlist-table-head">
                  <span>Stock</span>
                  <span>LTP</span>
                  <span>Change</span>
                  <span>Setup</span>
                  <span>Score</span>
                  <span>Signal</span>
                  <span>Action</span>
                </div>
                {filteredWatchlist.map((candidate) => {
                  const queued = queueSymbols.has(candidate.symbol)
                  const ready = canSendToPaper(candidate)
                  const changeTone = candidate.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'
                  const signal = candidate.live_signal
                  return (
                    <div key={candidate.symbol} className={ready ? 'watchlist-row watchlist-row-ready' : 'watchlist-row'}>
                      <button type="button" className="watch-symbol-cell" onClick={() => onSelect(candidate.symbol)}>
                        <strong>{candidate.symbol}</strong>
                        <span>{candidate.company_name}</span>
                      </button>
                      <strong>{currency(candidate.last_price)}</strong>
                      <strong className={changeTone}>
                        {candidate.day_change_pct >= 0 ? '+' : ''}{candidate.day_change_pct.toFixed(2)}%
                      </strong>
                      <span className="watch-setup-cell">{candidate.setup_family}</span>
                      <span className={ready ? 'watch-score-cell watch-score-hot' : 'watch-score-cell'}>{candidate.score}</span>
                      <strong className={`watch-signal-pill ${signalClass(signal.status)}`}>{signal.label}</strong>
                      <div className="watch-actions">
                        <button
                          type="button"
                          className={queued ? 'ghost-button active-ghost' : 'primary-button'}
                          onClick={() => onQueue(candidate)}
                          disabled={!ready}
                          title={ready ? 'Send this setup to Paper Desk for 7 trading sessions' : 'Needs a valid stop loss before paper trading'}
                        >
                          <WalletCards size={14} />
                          <span>{queued ? 'Queued' : 'Paper'}</span>
                        </button>
                        <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(candidate.symbol)}>
                          Remove
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
              {filteredWatchlist.length === 0 && (
                <div className="empty-table">
                  <CircleAlert size={18} />
                  <p>No saved stocks match this search.</p>
                </div>
              )}
            </Surface>
          </div>
        )}
      </Surface>
    </div>
  )
}

function PortfolioView({
  paperTrades,
  closedTrades,
  budget,
  onSelect,
  onRemove,
  onUpdate,
}: {
  paperTrades: PaperTrade[]
  closedTrades: PaperTrade[]
  budget: PaperBudget | null
  onSelect: (symbol: string) => void
  onRemove: (symbol: string) => void
  onWatch: (candidate: SwingCandidate) => void
  onUpdate: (trade: PaperTrade, patch: { quantity?: number; capital_allocated?: number }) => void
}) {
  const totalPnl = paperTrades.reduce((sum, trade) => sum + trade.unrealized_pnl, 0)
  const allocatedBudget = budget?.allocated_budget ?? paperTrades.reduce((sum, trade) => sum + trade.capital_allocated, 0)
  const totalBudget = budget?.total_budget ?? allocatedBudget
  const availableBudget = budget?.available_budget ?? Math.max(totalBudget - allocatedBudget, 0)
  const realizedPnl = closedTrades.reduce((sum, trade) => sum + trade.realized_pnl, 0)
  const hasLivePrices = paperTrades.some(isLivePaperQuote)
  const hasReferencePrices = paperTrades.some(isReferencePaperQuote)
  const pnlLabel = hasLivePrices ? 'Open P&L' : 'Reference P&L'
  const priceColumnLabel = hasLivePrices ? 'Current' : 'Last close'
  const totalOpenRisk = paperTrades.reduce((sum, trade) => sum + riskAmount(trade), 0)
  const budgetUsePct = totalBudget > 0 ? Math.min(100, (allocatedBudget / totalBudget) * 100) : 0
  const stoppedCount = paperTrades.filter(isTradeStopped).length
  const expiringCount = paperTrades.filter((trade) => sessionsRemaining(trade) <= 1).length
  const systemOpenTrades = paperTrades.filter(isSystemPaperTrade)
  const manualOpenTrades = paperTrades.filter((trade) => !isSystemPaperTrade(trade))
  const systemClosedTrades = closedTrades.filter(isSystemPaperTrade)
  const manualClosedTrades = closedTrades.filter((trade) => !isSystemPaperTrade(trade))
  const systemOpenPnl = systemOpenTrades.reduce((sum, trade) => sum + trade.unrealized_pnl, 0)
  const manualOpenPnl = manualOpenTrades.reduce((sum, trade) => sum + trade.unrealized_pnl, 0)
  const systemClosedPnl = systemClosedTrades.reduce((sum, trade) => sum + trade.realized_pnl, 0)
  const manualClosedPnl = manualClosedTrades.reduce((sum, trade) => sum + trade.realized_pnl, 0)
  const systemSignalDates = systemOpenTrades
    .map(paperSignalDate)
    .filter((date): date is string => date !== null)
    .sort()
  const latestSystemSignal = systemSignalDates[systemSignalDates.length - 1] ?? 'none'
  const weeklyStrategyRows = buildWeeklyStrategyRows([...paperTrades, ...closedTrades])
  const [paperSourceFilter, setPaperSourceFilter] = useState('all')
  const [paperStatusFilter, setPaperStatusFilter] = useState('all')
  const [paperStrategyFilter, setPaperStrategyFilter] = useState('all')
  const [paperSort, setPaperSort] = useState('attention')
  const [paperSearch, setPaperSearch] = useState('')
  const [activePaperTab, setActivePaperTab] = useState<PaperDeskTab>('open')
  const [openPage, setOpenPage] = useState(1)
  const [closedPage, setClosedPage] = useState(1)
  const [weeklyPage, setWeeklyPage] = useState(1)
  const [strategyPage, setStrategyPage] = useState(1)
  const [intakePage, setIntakePage] = useState(1)
  const [paperPageSize, setPaperPageSize] = useState(10)
  const paperStrategyOptions = useMemo(() => {
    const options = new Map<string, string>()
    ;[...paperTrades, ...closedTrades].forEach((trade) => {
      const id = paperStrategyId(trade) ?? trade.setup_family
      options.set(id, trade.setup_family || strategyLabel(id))
    })
    return Array.from(options.entries()).sort((a, b) => a[1].localeCompare(b[1]))
  }, [closedTrades, paperTrades])
  const visiblePaperTrades = useMemo(() => {
    const term = paperSearch.trim().toLowerCase()
    return paperTrades
      .filter((trade) => {
        const source = isSystemPaperTrade(trade) ? 'system' : 'manual'
        const strategyId = paperStrategyId(trade) ?? trade.setup_family
        const remaining = sessionsRemaining(trade)
        const matchesSource = paperSourceFilter === 'all' || paperSourceFilter === source
        const matchesStrategy = paperStrategyFilter === 'all' || paperStrategyFilter === strategyId
        const matchesStatus =
          paperStatusFilter === 'all'
          || (paperStatusFilter === 'attention' && (isTradeStopped(trade) || remaining <= 1))
          || (paperStatusFilter === 'stopped' && isTradeStopped(trade))
          || (paperStatusFilter === 'expiring' && remaining <= 1)
          || (paperStatusFilter === 'winning' && trade.unrealized_pnl >= 0)
          || (paperStatusFilter === 'losing' && trade.unrealized_pnl < 0)
        const matchesSearch = !term
          || trade.symbol.toLowerCase().includes(term)
          || trade.company_name.toLowerCase().includes(term)
          || trade.setup_family.toLowerCase().includes(term)
        return matchesSource && matchesStrategy && matchesStatus && matchesSearch
      })
      .sort((a, b) => {
        if (paperSort === 'pnl-desc') return b.unrealized_pnl - a.unrealized_pnl
        if (paperSort === 'pnl-asc') return a.unrealized_pnl - b.unrealized_pnl
        if (paperSort === 'newest') return b.planned_at.localeCompare(a.planned_at)
        if (paperSort === 'symbol') return a.symbol.localeCompare(b.symbol)
        const aAttention = (isTradeStopped(a) ? 2 : sessionsRemaining(a) <= 1 ? 1 : 0)
        const bAttention = (isTradeStopped(b) ? 2 : sessionsRemaining(b) <= 1 ? 1 : 0)
        return bAttention - aAttention || a.unrealized_pnl - b.unrealized_pnl
      })
  }, [paperSearch, paperSort, paperSourceFilter, paperStatusFilter, paperStrategyFilter, paperTrades])
  const visibleClosedTrades = useMemo(() => {
    const term = paperSearch.trim().toLowerCase()
    return closedTrades
      .filter((trade) => {
        const source = isSystemPaperTrade(trade) ? 'system' : 'manual'
        const strategyId = paperStrategyId(trade) ?? trade.setup_family
        const matchesSource = paperSourceFilter === 'all' || paperSourceFilter === source
        const matchesStrategy = paperStrategyFilter === 'all' || paperStrategyFilter === strategyId
        const matchesSearch = !term
          || trade.symbol.toLowerCase().includes(term)
          || trade.company_name.toLowerCase().includes(term)
          || trade.setup_family.toLowerCase().includes(term)
          || trade.close_reason.toLowerCase().includes(term)
        return matchesSource && matchesStrategy && matchesSearch
      })
      .sort((a, b) => {
        if (paperSort === 'pnl-desc') return b.realized_pnl - a.realized_pnl
        if (paperSort === 'pnl-asc') return a.realized_pnl - b.realized_pnl
        if (paperSort === 'symbol') return a.symbol.localeCompare(b.symbol)
        return (b.closed_at ?? b.planned_at).localeCompare(a.closed_at ?? a.planned_at)
      })
  }, [closedTrades, paperSearch, paperSort, paperSourceFilter, paperStrategyFilter])
  const filteredWeeklyRows = weeklyStrategyRows.filter((row) => paperStrategyFilter === 'all' || row.strategyId === paperStrategyFilter)
  const strategyRows = useMemo(
    () => buildPaperStrategyRows([...paperTrades, ...closedTrades])
      .filter((row) => paperStrategyFilter === 'all' || row.strategyId === paperStrategyFilter)
      .filter((row) => {
        const term = paperSearch.trim().toLowerCase()
        return !term || row.strategyLabel.toLowerCase().includes(term) || row.strategyId.toLowerCase().includes(term)
      }),
    [closedTrades, paperSearch, paperStrategyFilter, paperTrades],
  )
  const intakeTrades = useMemo(() => {
    const term = paperSearch.trim().toLowerCase()
    return [...paperTrades, ...closedTrades]
      .filter((trade) => {
        const source = isSystemPaperTrade(trade) ? 'system' : 'manual'
        const strategyId = paperStrategyId(trade) ?? trade.setup_family
        const matchesSource = paperSourceFilter === 'all' || paperSourceFilter === source
        const matchesStrategy = paperStrategyFilter === 'all' || paperStrategyFilter === strategyId
        const matchesSearch = !term
          || trade.symbol.toLowerCase().includes(term)
          || trade.company_name.toLowerCase().includes(term)
          || trade.setup_family.toLowerCase().includes(term)
        return matchesSource && matchesStrategy && matchesSearch
      })
      .sort((a, b) => b.planned_at.localeCompare(a.planned_at))
  }, [closedTrades, paperSearch, paperSourceFilter, paperStrategyFilter, paperTrades])
  const openPageData = paginateRows(visiblePaperTrades, openPage, paperPageSize)
  const closedPageData = paginateRows(visibleClosedTrades, closedPage, paperPageSize)
  const weeklyPageData = paginateRows(filteredWeeklyRows, weeklyPage, paperPageSize)
  const strategyPageData = paginateRows(strategyRows, strategyPage, paperPageSize)
  const intakePageData = paginateRows(intakeTrades, intakePage, paperPageSize)
  useEffect(() => {
    setOpenPage(1)
    setClosedPage(1)
    setWeeklyPage(1)
    setStrategyPage(1)
    setIntakePage(1)
  }, [paperPageSize, paperSearch, paperSort, paperSourceFilter, paperStatusFilter, paperStrategyFilter])

  return (
    <div className="page-stack paper-desk-page">
      <Surface className="paper-desk-shell">
        <div className="paper-desk-hero">
          <div>
            <span className="eyebrow">Paper Desk</span>
            <h2>Backtest-gated paper board</h2>
            <p>Only backtest-tracked rows enter automatically. Every open trade must carry entry, stop, target, and a 5-session trading-week clock.</p>
          </div>
          <div className="paper-desk-hero-grid">
            <CandidateStat label="Open" value={String(paperTrades.length)} />
            <CandidateStat label={pnlLabel} value={currency(totalPnl)} tone={hasLivePrices ? (totalPnl >= 0 ? 'positive' : 'danger') : 'warning'} />
            <CandidateStat label="Open Risk" value={currency(totalOpenRisk)} tone={totalOpenRisk > 0 ? 'warning' : 'neutral'} />
            <CandidateStat label="Closed P&L" value={currency(realizedPnl)} tone={realizedPnl >= 0 ? 'positive' : 'danger'} />
          </div>
        </div>

        <div className="paper-policy-strip">
          <div>
            <span className="micro-label">Intake</span>
            <strong>Newest Candidate / Watch signals</strong>
          </div>
          <div>
            <span className="micro-label">Hold</span>
            <strong>{PAPER_HOLD_SESSIONS} trading sessions</strong>
          </div>
          <div>
            <span className="micro-label">Risk</span>
            <strong>Stop required</strong>
          </div>
          <div>
            <span className="micro-label">Auto exit</span>
            <strong>Stop or time</strong>
          </div>
        </div>

        <div className="paper-analytics-grid">
          <Surface className="inner-surface source-metric">
            <span className="micro-label">System Added</span>
            <strong>{systemOpenTrades.length} open</strong>
            <small>{currency(systemOpenPnl)} open P&L</small>
            <small>{currency(systemClosedPnl)} closed P&L</small>
          </Surface>
          <Surface className="inner-surface source-metric">
            <span className="micro-label">Manual Added</span>
            <strong>{manualOpenTrades.length} open</strong>
            <small>{currency(manualOpenPnl)} open P&L</small>
            <small>{currency(manualClosedPnl)} closed P&L</small>
          </Surface>
          <Surface className="inner-surface source-metric">
            <span className="micro-label">Newest Auto Signal</span>
            <strong>{latestSystemSignal}</strong>
            <small>same signal date and strategy will not be staged twice</small>
          </Surface>
        </div>

        {hasReferencePrices && !hasLivePrices && (
          <div className="market-closed-note">
            <CircleAlert size={17} />
            <div>
              <strong>Market is closed or live quotes are unavailable.</strong>
              <span>Paper Desk is marked to the latest stored close, so P&L is reference-only until live Dhan quotes resume during market hours.</span>
            </div>
          </div>
        )}

        <div className="paper-capital-panel">
          <div>
            <span className="eyebrow">Capital</span>
            <h3>{currency(totalBudget)}</h3>
            <div className="budget-meter" aria-label="Budget utilization">
              <span style={{ width: `${budgetUsePct}%` }} />
            </div>
          </div>
          <div>
            <span className="micro-label">Allocated</span>
            <strong>{currency(allocatedBudget)}</strong>
          </div>
          <div>
            <span className="micro-label">Available</span>
            <strong className={availableBudget >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(availableBudget)}</strong>
          </div>
          <div>
            <span className="micro-label">At Stop</span>
            <strong className={totalOpenRisk > 0 ? 'tone-warning' : ''}>{currency(totalOpenRisk)}</strong>
          </div>
          <div>
            <span className="micro-label">Needs Attention</span>
            <strong className={stoppedCount > 0 ? 'tone-danger' : expiringCount > 0 ? 'tone-warning' : 'tone-positive'}>
              {stoppedCount > 0 ? `${stoppedCount} stopped` : `${expiringCount} expiring`}
            </strong>
          </div>
        </div>

        <div className="desk-control-panel">
          <label>
            <span>Source</span>
            <select className="select-input" value={paperSourceFilter} onChange={(event) => setPaperSourceFilter(event.currentTarget.value)}>
              <option value="all">All sources</option>
              <option value="system">System added</option>
              <option value="manual">Manual added</option>
            </select>
          </label>
          <label>
            <span>Status</span>
            <select className="select-input" value={paperStatusFilter} onChange={(event) => setPaperStatusFilter(event.currentTarget.value)}>
              <option value="all">All open</option>
              <option value="attention">Needs attention</option>
              <option value="stopped">Stop hit</option>
              <option value="expiring">Expiring</option>
              <option value="winning">Winning</option>
              <option value="losing">Losing</option>
            </select>
          </label>
          <label>
            <span>Strategy</span>
            <select className="select-input" value={paperStrategyFilter} onChange={(event) => setPaperStrategyFilter(event.currentTarget.value)}>
              <option value="all">All strategies</option>
              {paperStrategyOptions.map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Sort</span>
            <select className="select-input" value={paperSort} onChange={(event) => setPaperSort(event.currentTarget.value)}>
              <option value="attention">Attention first</option>
              <option value="pnl-desc">Best P&L</option>
              <option value="pnl-asc">Worst P&L</option>
              <option value="newest">Newest</option>
              <option value="symbol">Symbol</option>
            </select>
          </label>
          <label className="wide-control">
            <span>Search</span>
            <input className="text-input" value={paperSearch} onChange={(event) => setPaperSearch(event.currentTarget.value)} placeholder="Symbol or strategy" />
          </label>
        </div>

        <div className="paper-tab-strip">
          {[
            ['open', 'Open Trades', visiblePaperTrades.length],
            ['closed', 'Closed Trades', visibleClosedTrades.length],
            ['weekly', 'Weekly Analytics', filteredWeeklyRows.length],
            ['strategy', 'Strategy View', strategyRows.length],
            ['intake', 'Intake Log', intakeTrades.length],
          ].map(([id, label, count]) => (
            <button
              key={id}
              type="button"
              className={activePaperTab === id ? 'paper-tab paper-tab-active' : 'paper-tab'}
              onClick={() => setActivePaperTab(id as PaperDeskTab)}
            >
              <span>{label}</span>
              <strong>{count}</strong>
            </button>
          ))}
        </div>

        <div className="paper-tab-body">
          {activePaperTab === 'open' && (
            <PaperDeskSection
              icon={BriefcaseBusiness}
              eyebrow="Open Trades"
              title="Active paper positions"
              countLabel={`${visiblePaperTrades.length} shown`}
              empty={paperTrades.length === 0 ? 'No open paper positions from the current backtest-gated intake.' : 'No open paper positions match the current filters.'}
              hasRows={visiblePaperTrades.length > 0}
            >
              <div className="paper-table-surface">
                <div className="paper-table">
                  <div className="paper-table-row paper-table-head">
                    <span>Stock</span>
                    <span>Source</span>
                    <span>Qty</span>
                    <span>Entry / Stop</span>
                    <span>{priceColumnLabel}</span>
                    <span>Risk</span>
                    <span>{hasLivePrices ? 'P&L' : 'Ref P&L'}</span>
                    <span>Clock</span>
                  </div>
                  {openPageData.rows.map((trade) => {
                    const pnl = trade.unrealized_pnl
                    const remaining = sessionsRemaining(trade)
                    const stopped = isTradeStopped(trade)
                    return (
                      <div key={trade.symbol} className={stopped ? 'paper-table-row paper-row-danger' : remaining <= 1 ? 'paper-table-row paper-row-warning' : 'paper-table-row'}>
                        <button type="button" className="paper-symbol-cell" onClick={() => onSelect(trade.symbol)}>
                          <strong>{trade.symbol}</strong>
                          <span>{trade.company_name}</span>
                        </button>
                        <span className="paper-source-pill">{paperSourceLabel(trade)}</span>
                        <input
                          key={`${trade.symbol}-qty-${trade.quantity}`}
                          className="paper-input"
                          type="number"
                          min="1"
                          defaultValue={trade.quantity}
                          onBlur={(event) => {
                            const quantity = Math.max(1, Math.floor(Number(event.currentTarget.value) || trade.quantity))
                            if (quantity !== trade.quantity) onUpdate(trade, { quantity })
                          }}
                        />
                        <strong className="paper-price-stack">
                          {currency(trade.entry_price)}
                          <small>Stop {currency(trade.stop_loss)}</small>
                          <small>Target {currency(trade.target_price)}</small>
                        </strong>
                        <strong className="paper-price-stack">
                          {currency(trade.current_price)}
                          <small>{paperQuoteLabel(trade)}</small>
                        </strong>
                        <strong className="paper-price-stack">
                          {currency(riskAmount(trade))}
                          <small>{currency(trade.capital_allocated)} allocated</small>
                        </strong>
                        <strong className={pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                          {currency(pnl)}
                          <small>{trade.unrealized_pnl_pct.toFixed(2)}%</small>
                        </strong>
                        <div className="paper-actions">
                          <strong>{remaining}</strong>
                          <small>{stopped ? 'stop hit' : 'sessions left'}</small>
                          <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(trade.symbol)}>
                            Remove
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
              <PaginationControls pageData={openPageData} pageSize={paperPageSize} onPageChange={setOpenPage} onPageSizeChange={setPaperPageSize} />
            </PaperDeskSection>
          )}

          {activePaperTab === 'closed' && (
            <PaperDeskSection
              icon={ListTodo}
              eyebrow="Closed Summary"
              title="What happened after the paper session ended"
              countLabel={`${visibleClosedTrades.length} closed`}
              empty="No closed paper trades match the current filters."
              hasRows={visibleClosedTrades.length > 0}
            >
              <div className="closed-trade-list paged-list">
                {closedPageData.rows.map((trade) => {
                  const pnl = trade.realized_pnl
                  const exit = trade.exit_price ?? trade.entry_price
                  return (
                    <div key={`${trade.symbol}-${trade.closed_at ?? trade.close_reason}`} className="closed-trade-row">
                      <div>
                        <strong>{trade.symbol}</strong>
                        <span>{trade.close_reason || 'closed'}{trade.closed_at ? ` | ${compactDate(trade.closed_at)}` : ''}</span>
                      </div>
                      <span>{paperSourceLabel(trade)}</span>
                      <span>{currency(trade.entry_price)} to {currency(exit)}</span>
                      <strong className={pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                        {currency(pnl)} ({tradeReturnPct(pnl, trade).toFixed(2)}%)
                      </strong>
                    </div>
                  )
                })}
              </div>
              <PaginationControls pageData={closedPageData} pageSize={paperPageSize} onPageChange={setClosedPage} onPageSizeChange={setPaperPageSize} />
            </PaperDeskSection>
          )}

          {activePaperTab === 'weekly' && (
            <PaperDeskSection
              icon={BarChart3}
              eyebrow="Trading Week Analytics"
              title="Strategy-wise result after signal tracking"
              countLabel={`${filteredWeeklyRows.length} strategy weeks`}
              empty="No weekly strategy rows match the current filters."
              hasRows={filteredWeeklyRows.length > 0}
            >
              <div className="weekly-strategy-table">
                <div className="weekly-strategy-row weekly-strategy-head">
                  <span>Week</span>
                  <span>Strategy</span>
                  <span>Entries</span>
                  <span>Active</span>
                  <span>Closed</span>
                  <span>W/L</span>
                  <span>Closed P&L</span>
                </div>
                {weeklyPageData.rows.map((row) => (
                  <div key={`${row.weekStart}-${row.strategyId}`} className="weekly-strategy-row">
                    <strong>{row.weekStart}</strong>
                    <span>{row.strategyLabel}</span>
                    <span>{row.entries}</span>
                    <span>{row.active}</span>
                    <span>{row.closed}</span>
                    <span>{row.wins}/{row.losses}</span>
                    <strong className={row.closedPnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {currency(row.closedPnl)}
                      <small>{row.closed > 0 ? `${row.avgReturnPct.toFixed(2)}% avg` : 'waiting close'}</small>
                    </strong>
                  </div>
                ))}
              </div>
              <PaginationControls pageData={weeklyPageData} pageSize={paperPageSize} onPageChange={setWeeklyPage} onPageSizeChange={setPaperPageSize} />
            </PaperDeskSection>
          )}

          {activePaperTab === 'strategy' && (
            <PaperDeskSection
              icon={Target}
              eyebrow="Strategy View"
              title="P&L and activity grouped by strategy"
              countLabel={`${strategyRows.length} strategies`}
              empty="No strategy rows match the current filters."
              hasRows={strategyRows.length > 0}
            >
              <div className="strategy-paper-table">
                <div className="strategy-paper-row strategy-paper-head">
                  <span>Strategy</span>
                  <span>Open</span>
                  <span>Closed</span>
                  <span>W/L</span>
                  <span>Open P&L</span>
                  <span>Closed P&L</span>
                </div>
                {strategyPageData.rows.map((row) => (
                  <div key={row.strategyId} className="strategy-paper-row">
                    <strong>{row.strategyLabel}<small>{row.strategyId}</small></strong>
                    <span>{row.open}</span>
                    <span>{row.closed}</span>
                    <span>{row.wins}/{row.losses}</span>
                    <strong className={row.openPnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.openPnl)}</strong>
                    <strong className={row.closedPnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {currency(row.closedPnl)}
                      <small>{row.closed > 0 ? `${row.avgReturnPct.toFixed(2)}% avg` : 'waiting close'}</small>
                    </strong>
                  </div>
                ))}
              </div>
              <PaginationControls pageData={strategyPageData} pageSize={paperPageSize} onPageChange={setStrategyPage} onPageSizeChange={setPaperPageSize} />
            </PaperDeskSection>
          )}

          {activePaperTab === 'intake' && (
            <PaperDeskSection
              icon={Sparkles}
              eyebrow="Intake Log"
              title="System-added and manual paper entries"
              countLabel={`${intakeTrades.length} entries`}
              empty="No intake rows match the current filters."
              hasRows={intakeTrades.length > 0}
            >
              <div className="intake-list">
                {intakePageData.rows.map((trade) => (
                  <button key={`${trade.symbol}-${trade.planned_at}-${trade.enabled}`} type="button" className="intake-row" onClick={() => onSelect(trade.symbol)}>
                    <strong>{trade.symbol}<small>{trade.setup_family}</small></strong>
                    <span>{paperSourceLabel(trade)}</span>
                    <span>{paperSignalDate(trade) ?? trade.planned_at.slice(0, 10)}</span>
                    <strong className={trade.enabled === 1 ? 'tone-warning' : trade.realized_pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {trade.enabled === 1 ? 'Open' : currency(trade.realized_pnl)}
                    </strong>
                  </button>
                ))}
              </div>
              <PaginationControls pageData={intakePageData} pageSize={paperPageSize} onPageChange={setIntakePage} onPageSizeChange={setPaperPageSize} />
            </PaperDeskSection>
          )}
        </div>
      </Surface>
    </div>
  )
}

function paginateRows<T>(rows: T[], page: number, pageSize: number) {
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const safePage = Math.min(Math.max(page, 1), totalPages)
  const start = (safePage - 1) * pageSize
  return {
    rows: rows.slice(start, start + pageSize),
    page: safePage,
    totalPages,
    totalRows: rows.length,
    start: rows.length === 0 ? 0 : start + 1,
    end: Math.min(start + pageSize, rows.length),
  }
}

function PaperDeskSection({
  icon: Icon,
  eyebrow,
  title,
  countLabel,
  empty,
  hasRows,
  children,
}: {
  icon: LucideIcon
  eyebrow: string
  title: string
  countLabel: string
  empty: string
  hasRows: boolean
  children: ReactNode
}) {
  return (
    <div className="paper-tab-panel">
      <div className="lane-head">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h3>{title}</h3>
        </div>
        <div className="mini-chip">
          <Icon size={14} />
          <span>{countLabel}</span>
        </div>
      </div>
      {hasRows ? children : (
        <div className="portfolio-empty">
          <Icon size={20} />
          <p>{empty}</p>
        </div>
      )}
    </div>
  )
}

function PaginationControls<T>({
  pageData,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  pageData: ReturnType<typeof paginateRows<T>>
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  if (pageData.totalRows === 0) return null
  return (
    <div className="pagination-bar">
      <span>
        {pageData.start}-{pageData.end} of {pageData.totalRows}
      </span>
      <div className="pagination-actions">
        <select className="select-input pagination-size" value={pageSize} onChange={(event) => onPageSizeChange(Number(event.currentTarget.value))}>
          <option value={10}>10 rows</option>
          <option value={25}>25 rows</option>
          <option value={50}>50 rows</option>
        </select>
        <button type="button" className="ghost-button" onClick={() => onPageChange(pageData.page - 1)} disabled={pageData.page <= 1}>
          Previous
        </button>
        <strong>{pageData.page} / {pageData.totalPages}</strong>
        <button type="button" className="ghost-button" onClick={() => onPageChange(pageData.page + 1)} disabled={pageData.page >= pageData.totalPages}>
          Next
        </button>
      </div>
    </div>
  )
}

function buildPaperStrategyRows(trades: PaperTrade[]) {
  const groups = new Map<string, {
    strategyId: string
    strategyLabel: string
    open: number
    closed: number
    wins: number
    losses: number
    openPnl: number
    closedPnl: number
    returnPctSum: number
  }>()

  for (const trade of trades) {
    const strategyId = paperStrategyId(trade) ?? trade.setup_family
    const row = groups.get(strategyId) ?? {
      strategyId,
      strategyLabel: trade.setup_family || strategyLabel(strategyId),
      open: 0,
      closed: 0,
      wins: 0,
      losses: 0,
      openPnl: 0,
      closedPnl: 0,
      returnPctSum: 0,
    }

    if (trade.enabled === 1) {
      row.open += 1
      row.openPnl += trade.unrealized_pnl
    } else if (trade.close_reason !== 'removed') {
      row.closed += 1
      row.closedPnl += trade.realized_pnl
      row.returnPctSum += tradeReturnPct(trade.realized_pnl, trade)
      if (trade.realized_pnl >= 0) row.wins += 1
      else row.losses += 1
    }
    groups.set(strategyId, row)
  }

  return Array.from(groups.values())
    .map((row) => ({
      ...row,
      avgReturnPct: row.closed > 0 ? row.returnPctSum / row.closed : 0,
    }))
    .sort((a, b) => (b.open + b.closed) - (a.open + a.closed) || a.strategyLabel.localeCompare(b.strategyLabel))
}

function buildWeeklyStrategyRows(trades: PaperTrade[]) {
  const groups = new Map<string, {
    weekStart: string
    strategyId: string
    strategyLabel: string
    entries: number
    active: number
    closed: number
    wins: number
    losses: number
    closedPnl: number
    returnPctSum: number
  }>()

  for (const trade of trades.filter(isSystemPaperTrade)) {
    const signalDate = paperSignalDate(trade) ?? trade.planned_at.slice(0, 10)
    const strategyId = paperStrategyId(trade) ?? trade.setup_family
    const weekStart = tradingWeekStart(signalDate)
    const key = `${weekStart}|${strategyId}`
    const row = groups.get(key) ?? {
      weekStart,
      strategyId,
      strategyLabel: trade.setup_family || strategyId,
      entries: 0,
      active: 0,
      closed: 0,
      wins: 0,
      losses: 0,
      closedPnl: 0,
      returnPctSum: 0,
    }

    row.entries += 1
    if (trade.enabled === 1) {
      row.active += 1
    } else if (trade.close_reason !== 'removed') {
      row.closed += 1
      row.closedPnl += trade.realized_pnl
      row.returnPctSum += tradeReturnPct(trade.realized_pnl, trade)
      if (trade.realized_pnl >= 0) row.wins += 1
      else row.losses += 1
    }
    groups.set(key, row)
  }

  return Array.from(groups.values())
    .map((row) => ({
      ...row,
      avgReturnPct: row.closed > 0 ? row.returnPctSum / row.closed : 0,
    }))
    .sort((a, b) => b.weekStart.localeCompare(a.weekStart) || a.strategyLabel.localeCompare(b.strategyLabel))
}

function tradingWeekStart(signalDate: string) {
  const date = new Date(`${signalDate}T00:00:00`)
  if (Number.isNaN(date.getTime())) return signalDate
  const day = date.getDay()
  const diff = (day + 6) % 7
  date.setDate(date.getDate() - diff)
  return localIsoDate(date)
}

function strategyLabel(strategyId: string) {
  return strategyId
    .replace('-v1', '')
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function pct(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const STRATEGY_ANALYSIS_NOTES: Record<string, {
  title: string
  idea: string
  entry: string
  exit: string
  interpretation: string
  caveat: string
}> = {
  'rsi10-pullback-reversion-v1': {
    title: 'RSI10 Pullback Reversion',
    idea: 'Mean-reversion setup: buy a sharp pullback only when the stock is still in a long-term uptrend.',
    entry: 'Close must be above SMA200 and RSI10 must close below 30. The simulated entry is the next session open.',
    exit: 'Primary exit is RSI10 recovery above 40. The backtest also tracks the 4% stop, 4% target, and 10-session time stop used for risk comparison.',
    interpretation: 'This strategy is working when RSI40 exits dominate, win rate stays above the baseline, and profit factor stays comfortably above 1.',
    caveat: 'Paper Desk still tracks this with a stricter 5 trading-session clock, so live forward evidence can differ from the 10-session historical test.',
  },
}

function BacktestsView({
  dashboard,
  cache,
  running,
  loading,
  refreshingCache,
  onLoad,
  onRefreshCache,
  onRun,
}: {
  dashboard: BacktestDashboardResponse | null
  cache: BacktestCacheStatus | null
  running: boolean
  loading: boolean
  refreshingCache: boolean
  onLoad: () => void
  onRefreshCache: () => void
  onRun: () => void
}) {
  const [selectedStrategy, setSelectedStrategy] = useState<string>('near-52w-high-v1')
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [familyFilter, setFamilyFilter] = useState('all')
  const [backtestSort, setBacktestSort] = useState('stability')
  const [backtestSearch, setBacktestSearch] = useState('')
  const [backtestMode, setBacktestMode] = useState<'datewise' | 'strategy'>('datewise')
  const [datewise, setDatewise] = useState<BacktestDatewiseResponse | null>(null)
  const [datewiseDate, setDatewiseDate] = useState('')
  const [datewiseStrategy, setDatewiseStrategy] = useState('all')
  const [datewisePage, setDatewisePage] = useState(1)
  const [datewisePageSize, setDatewisePageSize] = useState(25)
  const [loadingDatewise, setLoadingDatewise] = useState(false)

  useEffect(() => {
    if (!dashboard) return
    let cancelled = false
    setLoadingDatewise(true)
    getBacktestDatewise({
      date: datewiseDate || undefined,
      strategy: datewiseStrategy,
      page: datewisePage,
      pageSize: datewisePageSize,
    })
      .then((payload) => {
        if (cancelled) return
        setDatewise(payload)
        if (!datewiseDate && payload.selected_date) {
          setDatewiseDate(payload.selected_date)
        }
      })
      .catch(() => {
        if (!cancelled) setDatewise(null)
      })
      .finally(() => {
        if (!cancelled) setLoadingDatewise(false)
      })
    return () => {
      cancelled = true
    }
  }, [dashboard, datewiseDate, datewisePage, datewisePageSize, datewiseStrategy])

  const methodFamilies = useMemo(() => {
    if (!dashboard) return []
    return Array.from(new Set(dashboard.diagnostics.map((row) => row.method_family))).sort()
  }, [dashboard])
  const methodStatuses = useMemo(() => {
    if (!dashboard) return []
    return Array.from(new Set(dashboard.diagnostics.map((row) => row.status))).sort()
  }, [dashboard])
  const visibleSummaries = useMemo(() => {
    if (!dashboard) return []
    const term = backtestSearch.trim().toLowerCase()
    const diagnosticsById = new Map(dashboard.diagnostics.map((row) => [row.strategy_id, row]))
    const dayQualityById = new Map(dashboard.day_quality.map((row) => [row.strategy_id, row]))
    return dashboard.summaries
      .filter((summary) => {
        const diagnostic = diagnosticsById.get(summary.strategy_id)
        const positiveDaysPct = dayQualityById.get(summary.strategy_id)?.positive_days_pct ?? 0
        const status = diagnostic?.status ?? 'Unknown'
        const family = diagnostic?.method_family ?? 'Unknown'
        const matchesStatus =
          statusFilter === 'all'
          || (statusFilter === 'quality' && status !== 'Rejected' && summary.win_rate >= 50 && positiveDaysPct >= 50)
          || (statusFilter === 'active' && status !== 'Rejected')
          || status === statusFilter
        const matchesFamily = familyFilter === 'all' || family === familyFilter
        const label = strategyLabel(summary.strategy_id).toLowerCase()
        const matchesSearch = !term
          || summary.strategy_id.toLowerCase().includes(term)
          || label.includes(term)
          || family.toLowerCase().includes(term)
        return matchesStatus && matchesFamily && matchesSearch
      })
      .sort((a, b) => {
        const aDiagnostic = diagnosticsById.get(a.strategy_id)
        const bDiagnostic = diagnosticsById.get(b.strategy_id)
        if (backtestSort === 'pnl') return b.total_pnl - a.total_pnl
        if (backtestSort === 'win') return b.win_rate - a.win_rate
        if (backtestSort === 'trades') return b.total_trades - a.total_trades
        if (backtestSort === 'name') return strategyLabel(a.strategy_id).localeCompare(strategyLabel(b.strategy_id))
        return (bDiagnostic?.stability_score ?? 0) - (aDiagnostic?.stability_score ?? 0)
      })
  }, [backtestSearch, backtestSort, dashboard, familyFilter, statusFilter])

  useEffect(() => {
    if (!visibleSummaries.length) return
    if (!visibleSummaries.some((summary) => summary.strategy_id === selectedStrategy)) {
      setSelectedStrategy(visibleSummaries[0].strategy_id)
    }
  }, [selectedStrategy, visibleSummaries])

  if (!dashboard) {
    return (
      <div className="page-stack">
        <Surface>
          <div className="section-head">
            <div>
              <span className="eyebrow">Strategy Lab</span>
              <h2>Backtest dashboard loads only on button click</h2>
            </div>
            <div className="hero-actions">
              <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache}>
                <Database size={14} className={refreshingCache ? 'spin' : ''} />
                <span>{refreshingCache ? 'Refreshing Cache' : 'Refresh Backtest Cache'}</span>
              </button>
              <button type="button" className="ghost-button" onClick={onLoad} disabled={loading}>
                <Database size={14} />
                <span>{loading ? 'Loading Dashboard' : 'Load Dashboard'}</span>
              </button>
              <button type="button" className="primary-button" onClick={onRun} disabled={running}>
                <RefreshCw size={14} className={running ? 'spin' : ''} />
                <span>{running ? 'Running Backtest' : 'Run Backtest'}</span>
              </button>
            </div>
          </div>
          <div className="backtest-run-note">
            <CircleAlert size={16} />
            <span>Backtests now run from a ClickHouse feature cache. Refresh the cache only when parquet data or watchlist membership changes.</span>
          </div>
          {cache && (
            <div className="backtest-kpi-grid">
              <CandidateStat label="Cached Rows" value={cache.cached_rows.toLocaleString('en-IN')} />
              <CandidateStat label="Symbols" value={cache.symbols.toLocaleString('en-IN')} />
              <CandidateStat label="From" value={cache.from_date || 'N/A'} />
              <CandidateStat label="To" value={cache.to_date || 'N/A'} />
            </div>
          )}
        </Surface>
      </div>
    )
  }

  const selectedSummary =
    visibleSummaries.find((summary) => summary.strategy_id === selectedStrategy) ??
    visibleSummaries[0] ??
    null
  const selectedDiagnostic = dashboard.diagnostics.find((row) => row.strategy_id === selectedStrategy)
  const yearlyRows = dashboard.yearly_returns.filter((row) => row.strategy_id === selectedStrategy)
  const selectedYearExists = selectedYear !== null && yearlyRows.some((row) => row.year === selectedYear)
  const activeYear = selectedYearExists ? selectedYear : yearlyRows[yearlyRows.length - 1]?.year ?? null
  const monthlyRows = dashboard.monthly_returns.filter((row) => row.strategy_id === selectedStrategy)
  const selectedYearMonths = activeYear ? monthlyRows.filter((row) => row.year === activeYear) : []
  const dayQuality = dashboard.day_quality.find((row) => row.strategy_id === selectedStrategy)
  const winners = dashboard.winners.filter((row) => row.strategy_id === selectedStrategy).slice(0, 8)
  const losers = dashboard.losers.filter((row) => row.strategy_id === selectedStrategy).slice(0, 8)
  const trades = dashboard.trades.filter((row) => row.strategy_id === selectedStrategy).slice(0, 18)
  const visibleStrategyIds = new Set(visibleSummaries.map((summary) => summary.strategy_id))
  const visibleDiagnostics = dashboard.diagnostics.filter((row) => visibleStrategyIds.has(row.strategy_id))
  const dayQualityByStrategy = new Map(dashboard.day_quality.map((row) => [row.strategy_id, row]))
  const dateOptions = datewise?.available_dates ?? []
  const activeDate = datewise?.selected_date ?? datewiseDate
  const activeDateIndex = activeDate ? dateOptions.indexOf(activeDate) : -1
  const datewisePageData = {
    rows: datewise?.rows ?? [],
    page: datewise?.page ?? datewisePage,
    pageSize: datewise?.page_size ?? datewisePageSize,
    totalRows: datewise?.total_rows ?? 0,
    totalPages: Math.max(1, Math.ceil((datewise?.total_rows ?? 0) / Math.max(datewise?.page_size ?? datewisePageSize, 1))),
    start: (datewise?.total_rows ?? 0) === 0 ? 0 : ((datewise?.page ?? datewisePage) - 1) * (datewise?.page_size ?? datewisePageSize) + 1,
    end: Math.min((datewise?.total_rows ?? 0), (datewise?.page ?? datewisePage) * (datewise?.page_size ?? datewisePageSize)),
  }

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Strategy Lab</span>
            <h2>Backtested swing strategy returns from parquet history</h2>
          </div>
          <div className="hero-actions">
            <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache || running}>
              <Database size={14} className={refreshingCache ? 'spin' : ''} />
              <span>{refreshingCache ? 'Refreshing Cache' : 'Refresh Cache'}</span>
            </button>
            <button type="button" className="primary-button" onClick={onRun} disabled={running}>
              <RefreshCw size={14} className={running ? 'spin' : ''} />
              <span>{running ? 'Running Backtest' : 'Run Backtest'}</span>
            </button>
            <CandidateStat label="Run" value={dashboard.run_id.replace('watchlist-swing-', '')} />
            <CandidateStat label="Methods" value={String(dashboard.summaries.length)} />
            <CandidateStat label="Shown" value={String(visibleSummaries.length)} tone={visibleSummaries.length > 0 ? 'positive' : 'warning'} />
            <CandidateStat label="Trades Stored" value={String(dashboard.summaries.reduce((sum, item) => sum + item.total_trades, 0).toLocaleString('en-IN'))} />
          </div>
        </div>

        <div className="backtest-run-note">
          <Database size={16} />
          <span>Run Backtest refreshes the cache first, dedupes duplicate stock signals, and caps the simulation at 3 new positions per day with Rs 10k per trade.</span>
        </div>

        {cache && (
          <div className="backtest-kpi-grid">
            <CandidateStat label="Cache Rows" value={cache.cached_rows.toLocaleString('en-IN')} />
            <CandidateStat label="Cache Symbols" value={cache.symbols.toLocaleString('en-IN')} />
            <CandidateStat label="Cache From" value={cache.from_date || 'N/A'} />
            <CandidateStat label="Cache To" value={cache.to_date || 'N/A'} />
          </div>
        )}

        <div className="backtest-mode-toggle">
          <button
            type="button"
            className={backtestMode === 'datewise' ? 'filter-chip active-ghost' : 'filter-chip'}
            onClick={() => setBacktestMode('datewise')}
          >
            <CalendarDays size={14} />
            <span>Datewise</span>
          </button>
          <button
            type="button"
            className={backtestMode === 'strategy' ? 'filter-chip active-ghost' : 'filter-chip'}
            onClick={() => setBacktestMode('strategy')}
          >
            <BarChart3 size={14} />
            <span>Strategy View</span>
          </button>
        </div>

        {backtestMode === 'datewise' ? (
          <DatewiseBacktestView
            datewise={datewise}
            loading={loadingDatewise}
            dateOptions={dateOptions}
            activeDate={activeDate}
            activeDateIndex={activeDateIndex}
            strategy={datewiseStrategy}
            pageData={datewisePageData}
            pageSize={datewisePageSize}
            onDateChange={(date) => {
              setDatewiseDate(date)
              setDatewisePage(1)
            }}
            onStrategyChange={(strategy) => {
              setDatewiseStrategy(strategy)
              setDatewisePage(1)
            }}
            onPageChange={setDatewisePage}
            onPageSizeChange={(size) => {
              setDatewisePageSize(size)
              setDatewisePage(1)
            }}
          />
        ) : (
          <>
        <div className="desk-control-panel backtest-control-panel">
          <label>
            <span>Show</span>
            <select className="select-input" value={statusFilter} onChange={(event) => setStatusFilter(event.currentTarget.value)}>
              <option value="quality">Win & positive days 50%+</option>
              <option value="active">Active only</option>
              <option value="all">All methods</option>
              {methodStatuses.map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Family</span>
            <select className="select-input" value={familyFilter} onChange={(event) => setFamilyFilter(event.currentTarget.value)}>
              <option value="all">All families</option>
              {methodFamilies.map((family) => (
                <option key={family} value={family}>{family}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Strategy</span>
            <select className="select-input" value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.currentTarget.value)}>
              {visibleSummaries.map((summary) => (
                <option key={summary.strategy_id} value={summary.strategy_id}>{strategyLabel(summary.strategy_id)}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Sort</span>
            <select className="select-input" value={backtestSort} onChange={(event) => setBacktestSort(event.currentTarget.value)}>
              <option value="stability">Stability</option>
              <option value="pnl">Total P&L</option>
              <option value="win">Win rate</option>
              <option value="trades">Trades</option>
              <option value="name">Name</option>
            </select>
          </label>
          <label className="wide-control">
            <span>Search</span>
            <input className="text-input" value={backtestSearch} onChange={(event) => setBacktestSearch(event.currentTarget.value)} placeholder="Strategy or family" />
          </label>
        </div>

        {dashboard.diagnostics.length > 0 && (
          <Surface className="inner-surface method-score-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Method Scorecard</span>
                <h2>Strategy families under test</h2>
              </div>
              <div className="mini-chip">
                <Target size={14} />
                <span>{dashboard.diagnostics.filter((row) => row.status !== 'Rejected').length} methods still worth watching</span>
              </div>
            </div>
            <div className="method-score-table">
              <div className="method-score-row method-score-head">
                <span>Method</span>
                <span>Status</span>
                <span>Stability</span>
                <span>Win</span>
                <span>Positive Days</span>
                <span>Profit Factor</span>
                <span>P&L</span>
              </div>
              {visibleDiagnostics.map((row) => {
                const canOpen = visibleSummaries.some((summary) => summary.strategy_id === row.strategy_id)
                const positiveDaysPct = dayQualityByStrategy.get(row.strategy_id)?.positive_days_pct ?? 0
                return (
                  <button
                    key={row.strategy_id}
                    type="button"
                    className={`method-score-row method-score-button method-status-${row.status.toLowerCase()}`}
                    onClick={() => {
                      if (canOpen) setSelectedStrategy(row.strategy_id)
                    }}
                    disabled={!canOpen}
                  >
                    <strong>{strategyLabel(row.strategy_id)}<small>{row.method_family}</small></strong>
                    <span>{row.status}</span>
                    <strong>{row.stability_score.toFixed(1)}</strong>
                    <span>{row.win_rate.toFixed(2)}%</span>
                    <span>{positiveDaysPct.toFixed(2)}%</span>
                    <span>{row.profit_factor.toFixed(2)}</span>
                    <strong className={row.total_pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.total_pnl)}</strong>
                  </button>
                )
              })}
            </div>
          </Surface>
        )}

        <div className="backtest-tabs">
          {visibleSummaries.map((summary) => (
            <button
              key={summary.strategy_id}
              type="button"
              className={summary.strategy_id === selectedStrategy ? 'backtest-tab backtest-tab-active' : 'backtest-tab'}
              onClick={() => setSelectedStrategy(summary.strategy_id)}
            >
              <span>{strategyLabel(summary.strategy_id)}</span>
              <strong className={summary.total_pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(summary.total_pnl)}</strong>
            </button>
          ))}
        </div>

        {!selectedSummary && (
          <div className="portfolio-empty">
            <ListTodo size={20} />
            <p>No backtest methods match the current filters.</p>
          </div>
        )}

        {selectedSummary && (
          <>
            <div className="backtest-kpi-grid">
              <CandidateStat label="Total P&L" value={currency(selectedSummary.total_pnl)} tone={selectedSummary.total_pnl >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Deployed Return" value={pct(selectedSummary.deployed_return_pct)} tone={selectedSummary.deployed_return_pct >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Win Rate" value={`${selectedSummary.win_rate.toFixed(2)}%`} tone={selectedSummary.win_rate >= 50 ? 'positive' : 'warning'} />
              <CandidateStat label="Stability" value={selectedDiagnostic ? selectedDiagnostic.stability_score.toFixed(1) : 'N/A'} tone={(selectedDiagnostic?.stability_score ?? 0) >= 62 ? 'positive' : 'warning'} />
              <CandidateStat label="Profit Factor" value={selectedDiagnostic ? selectedDiagnostic.profit_factor.toFixed(2) : 'N/A'} tone={(selectedDiagnostic?.profit_factor ?? 0) >= 1.05 ? 'positive' : 'warning'} />
              <CandidateStat label="Positive Months" value={selectedDiagnostic ? `${selectedDiagnostic.positive_months_pct.toFixed(2)}%` : 'N/A'} tone={(selectedDiagnostic?.positive_months_pct ?? 0) >= 52 ? 'positive' : 'warning'} />
              <CandidateStat label="Avg Trade" value={pct(selectedSummary.avg_return_pct)} tone={selectedSummary.avg_return_pct >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Trades" value={selectedSummary.total_trades.toLocaleString('en-IN')} />
              <CandidateStat label="Avg Hold" value={`${selectedSummary.avg_hold_sessions.toFixed(1)} sessions`} />
              <CandidateStat label="Positive Days" value={dayQuality ? `${dayQuality.positive_days_pct.toFixed(2)}%` : 'N/A'} tone={(dayQuality?.positive_days_pct ?? 0) >= 55 ? 'positive' : 'warning'} />
              <CandidateStat label="Max Drawdown" value={dayQuality ? currency(dayQuality.max_drawdown_rs) : 'N/A'} tone="danger" />
            </div>

            <div className="backtest-grid">
              <Surface className="inner-surface backtest-panel">
                <div className="compact-section-head">
                  <div>
                    <span className="eyebrow">Yearly Returns</span>
                    <h2>{strategyLabel(selectedStrategy)}</h2>
                  </div>
                </div>
                <div className="backtest-table">
                  <div className="backtest-row backtest-row-head">
                    <span>Year</span>
                    <span>Trades</span>
                    <span>Win</span>
                    <span>Avg</span>
                    <span>P&L</span>
                    <span>Return</span>
                  </div>
                  {yearlyRows.map((row) => (
                    <div key={`${row.strategy_id}-${row.year}`} className="backtest-row">
                      <strong>{row.year}</strong>
                      <span>{row.trades.toLocaleString('en-IN')}</span>
                      <span>{row.win_rate.toFixed(2)}%</span>
                      <span className={row.avg_return_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.avg_return_pct)}</span>
                      <strong className={row.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.pnl)}</strong>
                      <span className={row.return_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.return_pct)}</span>
                    </div>
                  ))}
                </div>
              </Surface>

              <Surface className="inner-surface backtest-panel">
                <div className="compact-section-head">
                  <div>
                    <span className="eyebrow">Exit Quality</span>
                    <h2>Where the money came from</h2>
                  </div>
                </div>
                <div className="exit-quality-grid">
              <CandidateStat label="Target Hits" value={selectedSummary.tp_exits.toLocaleString('en-IN')} tone="positive" />
              <CandidateStat label="Stop Loss" value={selectedSummary.sl_exits.toLocaleString('en-IN')} tone="danger" />
              <CandidateStat label="RSI40 Exits" value={(selectedSummary.rsi_exits ?? 0).toLocaleString('en-IN')} tone="positive" />
              <CandidateStat label="Time Exits" value={selectedSummary.time_exits.toLocaleString('en-IN')} tone="warning" />
              <CandidateStat label="Worst Day" value={dayQuality ? currency(dayQuality.worst_day) : 'N/A'} tone="danger" />
              <CandidateStat label="Best Day" value={dayQuality ? currency(dayQuality.best_day) : 'N/A'} tone="positive" />
                  <CandidateStat label="Days Tested" value={dayQuality ? String(dayQuality.trading_days) : 'N/A'} />
                </div>
              </Surface>
            </div>

            <StrategyAnalysisPanel
              strategyId={selectedStrategy}
              summary={selectedSummary}
              diagnostic={selectedDiagnostic}
              dayQuality={dayQuality}
            />

            <Surface className="inner-surface backtest-panel monthly-panel">
              <div className="compact-section-head">
                <div>
                  <span className="eyebrow">Monthly P&L</span>
                  <h2>Yearwise breakdown</h2>
                </div>
                <div className="year-selector">
                  {yearlyRows.map((row) => (
                    <button
                      key={`${row.strategy_id}-year-${row.year}`}
                      type="button"
                      className={row.year === activeYear ? 'filter-chip active-ghost' : 'filter-chip'}
                      onClick={() => setSelectedYear(row.year)}
                    >
                      {row.year}
                    </button>
                  ))}
                </div>
              </div>
              <div className="monthly-pnl-grid">
                {MONTH_LABELS.map((label, index) => {
                  const month = selectedYearMonths.find((row) => row.month === index + 1)
                  const pnl = month?.pnl ?? 0
                  const activeClass = pnl > 0 ? 'monthly-cell-positive' : pnl < 0 ? 'monthly-cell-negative' : 'monthly-cell-flat'
                  return (
                    <div key={`${activeYear}-${label}`} className={`monthly-cell ${activeClass}`}>
                      <span>{label}</span>
                      <strong>{month ? currency(pnl) : 'No trades'}</strong>
                      <small>{month ? `${month.trades.toLocaleString('en-IN')} trades | ${pct(month.return_pct)}` : '0 trades'}</small>
                    </div>
                  )
                })}
              </div>
            </Surface>

            <div className="backtest-grid">
              <BacktestSymbolList title="Best Strategy-Stock Edge" rows={winners} positive />
              <BacktestSymbolList title="Weak Stock Matches" rows={losers} />
            </div>

            <Surface className="inner-surface backtest-panel">
              <div className="compact-section-head">
                <div>
                  <span className="eyebrow">Trade Log</span>
                  <h2>Latest high-impact trades</h2>
                </div>
              </div>
              <div className="trade-log-table">
                <div className="trade-log-row trade-log-head">
                  <span>Stock</span>
                  <span>Entry</span>
                  <span>Exit</span>
                  <span>Qty</span>
                  <span>P&L</span>
                  <span>Reason</span>
                </div>
                {trades.map((trade) => (
                  <div key={`${trade.symbol}-${trade.entry_date}-${trade.exit_date}-${trade.pnl}`} className="trade-log-row">
                    <strong>{trade.symbol}<small>{trade.setup_family}</small></strong>
                    <span>{trade.entry_date}<small>{currency(trade.entry_price)}</small></span>
                    <span>{trade.exit_date}<small>{currency(trade.exit_price)}</small></span>
                    <span>{trade.quantity}</span>
                    <strong className={trade.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {currency(trade.pnl)}
                      <small>{pct(trade.return_pct)}</small>
                    </strong>
                    <span>{trade.exit_reason}<small>{trade.hold_sessions} sessions | score {trade.score}</small></span>
                  </div>
                ))}
              </div>
            </Surface>
          </>
        )}
          </>
        )}
      </Surface>
    </div>
  )
}

function DatewiseBacktestView({
  datewise,
  loading,
  dateOptions,
  activeDate,
  activeDateIndex,
  strategy,
  pageData,
  pageSize,
  onDateChange,
  onStrategyChange,
  onPageChange,
  onPageSizeChange,
}: {
  datewise: BacktestDatewiseResponse | null
  loading: boolean
  dateOptions: string[]
  activeDate: string
  activeDateIndex: number
  strategy: string
  pageData: ReturnType<typeof paginateRows<BacktestDatewiseResponse['rows'][number]>>
  pageSize: number
  onDateChange: (date: string) => void
  onStrategyChange: (strategy: string) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  const summary = datewise?.summary
  const strategyOptions = datewise?.strategy_options ?? []
  const canMoveNewer = activeDateIndex > 0
  const canMoveOlder = activeDateIndex >= 0 && activeDateIndex < dateOptions.length - 1

  return (
    <>
      <div className="datewise-toolbar">
        <div className="datewise-date-nav">
          <button
            type="button"
            className="ghost-button"
            onClick={() => onDateChange(dateOptions[0])}
            disabled={!dateOptions.length || activeDateIndex === 0}
          >
            Latest
          </button>
          <button
            type="button"
            className="ghost-button icon-button-text"
            onClick={() => onDateChange(dateOptions[activeDateIndex - 1])}
            disabled={!canMoveNewer}
          >
            <ChevronLeft size={14} />
            <span>Newer</span>
          </button>
          <button
            type="button"
            className="ghost-button icon-button-text"
            onClick={() => onDateChange(dateOptions[activeDateIndex + 1])}
            disabled={!canMoveOlder}
          >
            <span>Older</span>
            <ChevronRight size={14} />
          </button>
        </div>
        <label>
          <span>Date</span>
          <select className="select-input" value={activeDate} onChange={(event) => onDateChange(event.currentTarget.value)}>
            {dateOptions.map((date) => (
              <option key={date} value={date}>{date}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Strategy</span>
          <select className="select-input" value={strategy} onChange={(event) => onStrategyChange(event.currentTarget.value)}>
            <option value="all">All strategies</option>
            {strategyOptions.map((strategyId) => (
              <option key={strategyId} value={strategyId}>{strategyLabel(strategyId)}</option>
            ))}
          </select>
        </label>
      </div>

      {loading && (
        <div className="backtest-run-note">
          <RefreshCw size={16} className="spin" />
          <span>Loading date bucket...</span>
        </div>
      )}

      {summary ? (
        <>
          <div className="backtest-kpi-grid datewise-kpi-grid">
            <CandidateStat label="Date P&L" value={currency(summary.total_pnl)} tone={summary.total_pnl >= 0 ? 'positive' : 'danger'} />
            <CandidateStat label="Trades" value={summary.total_trades.toLocaleString('en-IN')} />
            <CandidateStat label="Win Rate" value={`${summary.win_rate.toFixed(2)}%`} tone={summary.win_rate >= 50 ? 'positive' : 'warning'} />
            <CandidateStat label="Avg Trade" value={pct(summary.avg_return_pct)} tone={summary.avg_return_pct >= 0 ? 'positive' : 'danger'} />
            <CandidateStat label="Winners" value={summary.winners.toLocaleString('en-IN')} tone="positive" />
            <CandidateStat label="Losers" value={summary.losers.toLocaleString('en-IN')} tone="danger" />
            <CandidateStat label="Top Gainer" value={`${summary.best_symbol || 'N/A'} ${currency(summary.best_pnl)}`} tone={summary.best_pnl >= 0 ? 'positive' : 'neutral'} />
            <CandidateStat label="Top Loser" value={`${summary.worst_symbol || 'N/A'} ${currency(summary.worst_pnl)}`} tone={summary.worst_pnl < 0 ? 'danger' : 'neutral'} />
          </div>

          <Surface className="inner-surface datewise-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Strategy Buckets</span>
                <h2>{summary.trade_date} P&L by method</h2>
              </div>
              <div className="mini-chip">
                <Target size={14} />
                <span>{(datewise?.strategy_summaries.length ?? 0).toLocaleString('en-IN')} active strategies</span>
              </div>
            </div>
            <div className="datewise-strategy-table">
              <div className="datewise-strategy-row datewise-strategy-head">
                <span>Strategy</span>
                <span>Trades</span>
                <span>Win</span>
                <span>P&L</span>
                <span>Best</span>
                <span>Worst</span>
              </div>
              {(datewise?.strategy_summaries ?? []).map((row) => (
                <div key={row.strategy_id} className="datewise-strategy-row">
                  <strong>{strategyLabel(row.strategy_id)}<small>{row.setup_family}</small></strong>
                  <span>{row.trades.toLocaleString('en-IN')}</span>
                  <span>{row.win_rate.toFixed(2)}%</span>
                  <strong className={row.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.pnl)}</strong>
                  <span>{row.best_symbol}<small className={row.best_pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.best_pnl)}</small></span>
                  <span>{row.worst_symbol}<small className={row.worst_pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.worst_pnl)}</small></span>
                </div>
              ))}
            </div>
          </Surface>

          <div className="backtest-grid">
            <DatewiseMoverList title="Top Gainers" rows={datewise?.top_gainers ?? []} positive />
            <DatewiseMoverList title="Top Losers" rows={datewise?.top_losers ?? []} />
          </div>

          <Surface className="inner-surface datewise-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Date Trade Log</span>
                <h2>Paginated entries for {summary.trade_date}</h2>
              </div>
            </div>
            <div className="trade-log-table datewise-trade-log">
              <div className="trade-log-row datewise-trade-row trade-log-head">
                <span>Stock</span>
                <span>Entry</span>
                <span>Exit</span>
                <span>Qty</span>
                <span>P&L</span>
                <span>Reason</span>
              </div>
              {pageData.rows.map((trade) => (
                <div key={`${trade.strategy_id}-${trade.symbol}-${trade.entry_date}-${trade.exit_date}-${trade.pnl}`} className="trade-log-row datewise-trade-row">
                  <strong>{trade.symbol}<small>{strategyLabel(trade.strategy_id)}</small></strong>
                  <span>{trade.entry_date}<small>signal {trade.signal_date} | {currency(trade.entry_price)}</small></span>
                  <span>{trade.exit_date}<small>{currency(trade.exit_price)}</small></span>
                  <span>{trade.quantity}</span>
                  <strong className={trade.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                    {currency(trade.pnl)}
                    <small>{pct(trade.return_pct)}</small>
                  </strong>
                  <span>{trade.exit_reason}<small>{trade.hold_sessions} sessions | score {trade.score}</small></span>
                </div>
              ))}
            </div>
            <PaginationControls pageData={pageData} pageSize={pageSize} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} />
          </Surface>
        </>
      ) : (
        <div className="portfolio-empty datewise-empty">
          <CalendarDays size={20} />
          <p>No trades found for this date bucket.</p>
        </div>
      )}
    </>
  )
}

function DatewiseMoverList({
  title,
  rows,
  positive = false,
}: {
  title: string
  rows: BacktestDatewiseResponse['rows']
  positive?: boolean
}) {
  return (
    <Surface className="inner-surface datewise-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">{positive ? 'Best of day' : 'Weakest of day'}</span>
          <h2>{title}</h2>
        </div>
      </div>
      <div className="datewise-mover-list">
        {rows.map((row) => (
          <div key={`${title}-${row.strategy_id}-${row.symbol}-${row.entry_date}-${row.pnl}`} className="datewise-mover-row">
            <strong>{row.symbol}<small>{strategyLabel(row.strategy_id)}</small></strong>
            <span>{row.entry_date}<small>{row.exit_reason}</small></span>
            <strong className={row.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.pnl)}<small>{pct(row.return_pct)}</small></strong>
          </div>
        ))}
      </div>
    </Surface>
  )
}

function BacktestSymbolList({
  title,
  rows,
  positive = false,
}: {
  title: string
  rows: BacktestDashboardResponse['winners']
  positive?: boolean
}) {
  return (
    <Surface className="inner-surface backtest-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">{positive ? 'Leaders' : 'Avoid List'}</span>
          <h2>{title}</h2>
        </div>
      </div>
      <div className="symbol-result-list">
        {rows.map((row) => (
          <div key={`${row.strategy_id}-${row.symbol}`} className="symbol-result-row">
            <strong>{row.symbol}</strong>
            <span>{row.trades} trades</span>
            <span>{row.win_rate.toFixed(2)}% win</span>
            <strong className={row.pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(row.pnl)}</strong>
            <span className={row.avg_return_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.avg_return_pct)}</span>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="symbol-result-row symbol-result-empty">
            <span>Need at least 5 trades on a stock for this strategy before calling it an edge.</span>
          </div>
        )}
      </div>
    </Surface>
  )
}

function StrategyAnalysisPanel({
  strategyId,
  summary,
  diagnostic,
  dayQuality,
}: {
  strategyId: string
  summary: BacktestRunSummary
  diagnostic?: BacktestStrategyDiagnostic
  dayQuality?: BacktestDayQuality
}) {
  const note = STRATEGY_ANALYSIS_NOTES[strategyId]
  if (!note) return null

  const rsiExitShare = summary.total_trades > 0 ? ((summary.rsi_exits ?? 0) / summary.total_trades) * 100 : 0
  const stopShare = summary.total_trades > 0 ? (summary.sl_exits / summary.total_trades) * 100 : 0
  const verdict = (diagnostic?.profit_factor ?? 0) >= 1.4 && summary.win_rate >= 60
    ? 'Historically constructive'
    : (diagnostic?.profit_factor ?? 0) > 1
      ? 'Positive but needs forward evidence'
      : 'Review only'

  return (
    <Surface className="inner-surface strategy-analysis-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Strategy Breakdown</span>
          <h2>{note.title}</h2>
        </div>
        <div className="mini-chip">
          <ShieldCheck size={14} />
          <span>{verdict}</span>
        </div>
      </div>
      <div className="strategy-analysis-grid">
        <div className="strategy-rule-card">
          <span className="micro-label">How it works</span>
          <p>{note.idea}</p>
          <div className="strategy-rule-steps">
            <div>
              <strong>Entry</strong>
              <span>{note.entry}</span>
            </div>
            <div>
              <strong>Exit</strong>
              <span>{note.exit}</span>
            </div>
            <div>
              <strong>Read</strong>
              <span>{note.interpretation}</span>
            </div>
          </div>
        </div>
        <div className="strategy-rule-card">
          <span className="micro-label">Latest run analysis</span>
          <div className="strategy-analysis-metrics">
            <CandidateStat label="Trades" value={summary.total_trades.toLocaleString('en-IN')} />
            <CandidateStat label="P&L" value={currency(summary.total_pnl)} tone={summary.total_pnl >= 0 ? 'positive' : 'danger'} />
            <CandidateStat label="Win Rate" value={`${summary.win_rate.toFixed(2)}%`} tone={summary.win_rate >= 55 ? 'positive' : 'warning'} />
            <CandidateStat label="Profit Factor" value={diagnostic ? diagnostic.profit_factor.toFixed(2) : 'N/A'} tone={(diagnostic?.profit_factor ?? 0) >= 1.2 ? 'positive' : 'warning'} />
            <CandidateStat label="RSI Exit Share" value={`${rsiExitShare.toFixed(1)}%`} tone={rsiExitShare >= 50 ? 'positive' : 'warning'} />
            <CandidateStat label="Stop Share" value={`${stopShare.toFixed(1)}%`} tone={stopShare <= 20 ? 'positive' : 'warning'} />
            <CandidateStat label="Positive Days" value={dayQuality ? `${dayQuality.positive_days_pct.toFixed(2)}%` : 'N/A'} tone={(dayQuality?.positive_days_pct ?? 0) >= 55 ? 'positive' : 'warning'} />
            <CandidateStat label="Avg Hold" value={`${summary.avg_hold_sessions.toFixed(1)} sessions`} />
          </div>
          <p className="strategy-analysis-note">{note.caveat}</p>
        </div>
      </div>
    </Surface>
  )
}

function SettingsView({
  broker,
  accounts,
}: {
  broker: BrokerStatus | null
  accounts: BrokerAccountSnapshot[]
}) {
  if (!broker) return <PageSkeleton />

  const dhanAccount = accounts.find((account) => account.broker === 'DHAN')
  const positionsCount = dhanAccount?.positions?.length ?? 0
  const availableBalance = Number(dhanAccount?.balance?.availabelBalance ?? 0)
  const utilizedAmount = Number(dhanAccount?.balance?.utilizedAmount ?? 0)

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Runtime Settings</span>
            <h2>Dhan status, account snapshot, and official API path</h2>
          </div>
          <BrokerBadge broker={broker} />
        </div>

        <div className="settings-grid">
          <Surface className="inner-surface">
            <span className="eyebrow">Dhan Credentials</span>
            <div className="settings-list">
              <div>
                <span className="micro-label">State</span>
                <strong>{broker.state}</strong>
              </div>
              <div>
                <span className="micro-label">Client ID</span>
                <strong>{broker.client_id ?? 'Not detected'}</strong>
              </div>
              <div>
                <span className="micro-label">Credential Source</span>
                <strong>{broker.credential_source}</strong>
              </div>
              <div>
                <span className="micro-label">Issued</span>
                <strong>{compactDate(broker.issued_at_utc)}</strong>
              </div>
              <div>
                <span className="micro-label">Expires</span>
                <strong>{compactDate(broker.expires_at_utc)}</strong>
              </div>
            </div>
          </Surface>

          <Surface className="inner-surface">
            <span className="eyebrow">Live Account Snapshot</span>
            {dhanAccount ? (
              <div className="settings-list">
                <div>
                  <span className="micro-label">Available Balance</span>
                  <strong>{currency(availableBalance)}</strong>
                </div>
                <div>
                  <span className="micro-label">Utilized Amount</span>
                  <strong>{currency(utilizedAmount)}</strong>
                </div>
                <div>
                  <span className="micro-label">Open Positions</span>
                  <strong>{positionsCount}</strong>
                </div>
                <div>
                  <span className="micro-label">Account Name</span>
                  <strong>{dhanAccount.name}</strong>
                </div>
              </div>
            ) : (
              <p className="settings-copy">No live Dhan account snapshot is available yet.</p>
            )}
          </Surface>
        </div>
      </Surface>

      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Official Dhan APIs</span>
            <h2>The endpoints we should build around next</h2>
          </div>
        </div>
        <div className="api-grid">
          <Surface className="inner-surface api-card">
            <span className="micro-label">Market Quote</span>
            <strong>`/marketfeed/ltp`, `/marketfeed/ohlc`, `/marketfeed/quote`</strong>
            <p>Best for scanner snapshots. Official docs say quote requests support up to 1000 instruments and are rate-limited to 1 request per second.</p>
          </Surface>
          <Surface className="inner-surface api-card">
            <span className="micro-label">Funds & Margin</span>
            <strong>`/fundlimit`, `/margincalculator`</strong>
            <p>Use this for paper-trade buying power, position sizing hints, and eventually a cleaner capital allocator.</p>
          </Surface>
          <Surface className="inner-surface api-card">
            <span className="micro-label">Portfolio & Positions</span>
            <strong>`/holdings`, `/positions`, `/positions/convert`</strong>
            <p>These are the right official APIs for account snapshots and live position awareness beside our paper-trade workflow.</p>
          </Surface>
          <Surface className="inner-surface api-card">
            <span className="micro-label">Orders</span>
            <strong>`/orders`, `/trades`</strong>
            <p>Official Dhan docs note order APIs need static IP whitelisting, so paper trading should stay primary until we’re ready for that constraint.</p>
          </Surface>
          {DHAN_API_SURFACES.map((surface) => (
            <Surface key={surface.docUrl} className="inner-surface api-card">
              <span className="micro-label">{surface.label} Doc</span>
              <strong>{surface.endpoints}</strong>
              <p>{surface.summary}</p>
              <p className="api-constraint">{surface.constraint}</p>
              <a className="doc-link" href={surface.docUrl} target="_blank" rel="noreferrer">
                <span>Open official doc</span>
                <ArrowUpRight size={14} />
              </a>
            </Surface>
          ))}
        </div>
      </Surface>
    </div>
  )
}

function PageSkeleton() {
  return (
    <div className="page-stack">
      <Surface>
        <div className="skeleton-block skeleton-hero" />
        <div className="skeleton-grid">
          <div className="skeleton-block" />
          <div className="skeleton-block" />
          <div className="skeleton-block" />
        </div>
      </Surface>
    </div>
  )
}

export default function App() {
  const initialRoute = parseRouteHash()
  const [view, setView] = useState<View>(initialRoute.view)
  const [home, setHome] = useState<SwingHomeResponse | null>(null)
  const [scanner, setScanner] = useState<SwingScannerResponse | null>(null)
  const [historicalScreener, setHistoricalScreener] = useState<HistoricalScreenerResponse | null>(null)
  const [freshSignals, setFreshSignals] = useState<FreshSignalsResponse | null>(null)
  const [bambooLatest, setBambooLatest] = useState<BambooLatestResponse | null>(null)
  const [accounts, setAccounts] = useState<BrokerAccountSnapshot[]>([])
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([])
  const [paperBudget, setPaperBudget] = useState<PaperBudget | null>(null)
  const [backtests, setBacktests] = useState<BacktestDashboardResponse | null>(null)
  const [backtestCache, setBacktestCache] = useState<BacktestCacheStatus | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(initialRoute.symbol)
  const [detailCandidate, setDetailCandidate] = useState<SwingCandidate | null>(null)
  const [history, setHistory] = useState<SymbolHistoryResponse | null>(null)
  const [historyRange, setHistoryRange] = useState<HistoryRange>('1y')
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [loadingHome, setLoadingHome] = useState(false)
  const [loadingScanner, setLoadingScanner] = useState(false)
  const [runningBacktest, setRunningBacktest] = useState(false)
  const [loadingBacktestDashboard, setLoadingBacktestDashboard] = useState(false)
  const [refreshingBacktestCache, setRefreshingBacktestCache] = useState(false)
  const [stagingFresh, setStagingFresh] = useState(false)
  const [refreshingFeatureCache, setRefreshingFeatureCache] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const autoClosingSymbols = useRef(new Set<string>())
  const autoLoadedHome = useRef(false)
  const autoLoadedScanner = useRef(false)
  const [watchlist, setWatchlist] = useState<SwingCandidate[]>(() => {
    try {
      const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY)
      return raw ? (JSON.parse(raw) as SwingCandidate[]) : []
    } catch {
      return []
    }
  })
  useEffect(() => {
    const syncRoute = () => {
      const route = parseRouteHash()
      setView(route.view)
      if (route.symbol) setSelectedSymbol(route.symbol)
    }
    syncRoute()
    window.addEventListener('popstate', syncRoute)
    return () => window.removeEventListener('popstate', syncRoute)
  }, [])

  useEffect(() => {
    localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist))
  }, [watchlist])

  const loadHomeDashboard = async () => {
    setLoadingHome(true)
    setError('')
    try {
      const homeData = await getSwingHome()
      startTransition(() => {
        setHome(homeData)
        setSelectedSymbol((current) => current ?? homeData.top_candidates[0]?.symbol ?? null)
      })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoadingHome(false)
    }
  }

  const loadScannerData = async () => {
    setLoadingScanner(true)
    setError('')
    try {
      const [scannerData, historicalData, bambooData] = await Promise.all([
        getSwingScanner(80),
        getHistoricalScreener({ limit: 120 }).catch(() => null),
        getBambooLatest().catch(() => null),
      ])
      const screenerData = historicalData ?? createHistoricalScreenerFromScanner(scannerData)
      startTransition(() => {
        setScanner(scannerData)
        setHistoricalScreener(screenerData)
        setBambooLatest(bambooData)
        setSelectedSymbol((current) => current ?? screenerData.rows[0]?.symbol ?? bambooData?.top_signals[0]?.symbol ?? scannerData.candidates[0]?.symbol ?? null)
      })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoadingScanner(false)
    }
  }

  const refreshAll = async () => {
    setRefreshing(true)
    setError('')
    try {
      if (view === 'home') {
        await loadHomeDashboard()
        return
      }

      if (view === 'scanner' || view === 'stock') {
        await loadScannerData()
        return
      }

      if (view === 'portfolio' || view === 'watchlist') {
        const [nextPaperTrades, nextPaperBudget] = await Promise.all([
          getPaperTrades().catch(() => []),
          getPaperBudget().catch(() => null),
        ])
        startTransition(() => {
          setPaperTrades(nextPaperTrades)
          setPaperBudget(nextPaperBudget)
          setSelectedSymbol((current) => current ?? nextPaperTrades.find((trade) => trade.enabled === 1)?.symbol ?? watchlist[0]?.symbol ?? null)
        })
        return
      }

      if (view === 'settings') {
        const [homeData, accountData] = await Promise.all([
          getSwingHome(),
          getBrokerAccounts().catch(() => []),
        ])
        startTransition(() => {
          setHome(homeData)
          setAccounts(accountData)
        })
        return
      }

      if (view === 'backtests') {
        await loadBacktestDashboard()
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    if (view !== 'home' || home || loadingHome || autoLoadedHome.current) return
    autoLoadedHome.current = true
    void loadHomeDashboard()
  }, [view, home, loadingHome])

  useEffect(() => {
    if ((view !== 'scanner' && view !== 'stock') || (scanner && historicalScreener) || loadingScanner || autoLoadedScanner.current) return
    autoLoadedScanner.current = true
    void loadScannerData()
  }, [view, scanner, historicalScreener, loadingScanner])

  const stageFreshNow = async () => {
    setStagingFresh(true)
    setError('')
    try {
      const freshSignalData = await stageFreshSignals({ limit: AUTO_PAPER_MAX_SUGGESTIONS, minPrice: 80, minAvgVolume: 100000 })
      const screenerData: HistoricalScreenerResponse = {
        updated_at: freshSignalData.updated_at,
        range: '1y',
        signal_date: freshSignalData.signal_date,
        total_rows: freshSignalData.eligible_rows,
        rows: freshSignalData.rows,
        message: freshSignalData.message,
      }
      const [nextPaperTrades, nextPaperBudget] = await Promise.all([
        getPaperTrades().catch(() => []),
        getPaperBudget().catch(() => null),
      ])
      startTransition(() => {
        setFreshSignals(freshSignalData)
        setHistoricalScreener(screenerData)
        setPaperTrades(nextPaperTrades)
        setPaperBudget(nextPaperBudget)
        if (!selectedSymbol && freshSignalData.rows[0]?.symbol) {
          setSelectedSymbol(freshSignalData.rows[0].symbol)
        }
      })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setStagingFresh(false)
    }
  }

  const refreshFeatureCacheNow = async () => {
    setRefreshingFeatureCache(true)
    setError('')
    try {
      const result = await refreshFeatureCache()
      setFreshSignals((current) => current
        ? {
            ...current,
            updated_at: result.updated_at,
            signal_date: result.data_date ?? current.signal_date,
            message: result.message,
          }
        : current)
      setHistoricalScreener((current) => current
        ? {
            ...current,
            updated_at: result.updated_at,
            signal_date: result.data_date ?? current.signal_date,
            total_rows: result.cached_rows || current.total_rows,
            message: result.message,
          }
        : current)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshingFeatureCache(false)
    }
  }

  const loadBacktestDashboard = async () => {
    setLoadingBacktestDashboard(true)
    setError('')
    try {
      const dashboard = await getBacktestDashboard()
      setBacktests(dashboard)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoadingBacktestDashboard(false)
    }
  }

  const refreshBacktestCacheNow = async () => {
    setRefreshingBacktestCache(true)
    setError('')
    try {
      const result = await refreshBacktestCache()
      setBacktestCache(result.cache)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshingBacktestCache(false)
    }
  }

  useEffect(() => {
    if (view !== 'backtests' || backtests || loadingBacktestDashboard) return
    void loadBacktestDashboard()
  }, [view, backtests, loadingBacktestDashboard])

  useEffect(() => {
    if (!selectedSymbol) return
    let cancelled = false
    setLoadingDetail(true)
    getSwingCandidate(selectedSymbol)
      .then((payload) => {
        if (!cancelled) {
          setDetailCandidate(payload.candidate)
        }
      })
      .catch(() => {
        const fallback =
          scanner?.candidates.find((candidate) => candidate.symbol === selectedSymbol) ??
          watchlist.find((candidate) => candidate.symbol === selectedSymbol) ??
          (paperTrades.find((trade) => trade.symbol === selectedSymbol)
            ? createCandidateFromPaperTrade(paperTrades.find((trade) => trade.symbol === selectedSymbol) as PaperTrade)
            : null) ??
          null
        if (!cancelled) setDetailCandidate(fallback)
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSymbol, scanner, watchlist, paperTrades])

  useEffect(() => {
    if (!selectedSymbol) return
    let cancelled = false
    setLoadingHistory(true)
    getSwingHistory(selectedSymbol, historyRange)
      .then((payload) => {
        if (!cancelled) {
          setHistory(payload)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHistory(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSymbol, historyRange])

  const addToWatchlist = (candidate: SwingCandidate) => {
    setWatchlist((current) => upsertCandidate(current, candidate))
  }

  const addToPaperDesk = async (candidate: SwingCandidate) => {
    if (!canSendToPaper(candidate)) {
      setError(`${candidate.symbol} needs a valid entry price and stop loss before it can go to Paper Desk.`)
      return
    }
    const tradePayload = paperTradePayloadFromCandidate(candidate, 'Manual paper-stage.')
    setWatchlist((current) => upsertCandidate(current, candidate))
    try {
      const saved = await savePaperTrade(tradePayload)
      setPaperTrades((current) => [saved, ...current.filter((trade) => trade.symbol !== saved.symbol)])
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const removeFromWatchlist = (symbol: string) => {
    setWatchlist((current) => removeCandidate(current, symbol))
  }

  const removeFromPaperDesk = async (symbol: string) => {
    setPaperTrades((current) => current.filter((trade) => trade.symbol !== symbol))
    try {
      await deletePaperTrade(symbol)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const closePaperPlan = async (trade: PaperTrade, exitPrice: number, reason = 'manual-close') => {
    autoClosingSymbols.current.add(trade.symbol)
    try {
      const closed = await closePaperTrade(trade.symbol, {
        exit_price: exitPrice,
        close_reason: reason,
      })
      setPaperTrades((current) => [closed, ...current.filter((item) => item.symbol !== closed.symbol)])
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      autoClosingSymbols.current.delete(trade.symbol)
    }
  }

  const updatePaperPlan = async (trade: PaperTrade, patch: { quantity?: number; capital_allocated?: number }) => {
    try {
      const saved = await savePaperTrade(paperTradePayloadFromTrade(trade, patch))
      setPaperTrades((current) => [saved, ...current.filter((item) => item.symbol !== saved.symbol)])
      const nextBudget = await getPaperBudget().catch(() => null)
      if (nextBudget) setPaperBudget(nextBudget)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const runBacktestNow = async () => {
    setRunningBacktest(true)
    setError('')
    try {
      const result = await runBacktest()
      setBacktestCache(result.cache)
      setBacktests(result.dashboard)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRunningBacktest(false)
    }
  }

  useEffect(() => {
    if (!scanner || paperTrades.length === 0) return
    const candidatesBySymbol = new Map(scanner.candidates.map((candidate) => [candidate.symbol, candidate]))
    paperTrades
      .filter((trade) => (isTradeStopped(trade) || isTradeTargetHit(trade) || isTradeExpired(trade)) && !autoClosingSymbols.current.has(trade.symbol))
      .forEach((trade) => {
        const candidate = candidatesBySymbol.get(trade.symbol)
        const stopped = isTradeStopped(trade)
        const targetHit = isTradeTargetHit(trade)
        void closePaperPlan(
          trade,
          stopped ? trade.stop_loss : targetHit ? trade.target_price : trade.current_price ?? candidate?.last_price ?? trade.entry_price,
          stopped ? 'stop-loss' : targetHit ? 'target-hit' : `auto-closed after ${trade.max_sessions} trading sessions`,
        )
      })
  }, [scanner, paperTrades])

  const broker = home?.broker ?? scanner?.broker ?? null
  const updatedAt = home?.updated_at ?? scanner?.updated_at ?? null
  const selectedHistoricalRow =
    historicalScreener?.rows.find((row) => row.symbol === selectedSymbol) ?? null
  const selectedBambooSignal =
    bambooLatest?.all_signals.find((signal) => signal.symbol === selectedSymbol) ??
    bambooLatest?.top_signals.find((signal) => signal.symbol === selectedSymbol) ??
    null
  const selectedActionCandidate =
    detailCandidate ??
    (selectedHistoricalRow ? createCandidateFromHistoricalRow(selectedHistoricalRow) : null) ??
    (selectedBambooSignal ? createCandidateFromBambooSignal(selectedBambooSignal) : null) ??
    (paperTrades.find((trade) => trade.symbol === selectedSymbol)
      ? createCandidateFromPaperTrade(paperTrades.find((trade) => trade.symbol === selectedSymbol) as PaperTrade)
      : null)
  const watchSymbols = new Set(watchlist.map((item) => item.symbol))
  const activePaperTrades = paperTrades.filter((trade) => trade.enabled === 1)
  const closedPaperTrades = paperTrades.filter((trade) => trade.enabled !== 1 && trade.close_reason !== 'removed')
  const queueSymbols = new Set(activePaperTrades.map((item) => item.symbol))

  const navigateView = (nextView: View) => {
    setView(nextView)
    writeRouteHash(nextView)
  }

  const openStock = (symbol: string) => {
    setSelectedSymbol(symbol)
    setView('stock')
    writeRouteHash('stock', symbol)
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">
            <TrendingUp size={18} />
          </div>
          <div>
            <p className="brand-title">Swing Atlas</p>
            <p className="brand-subtitle">Dhan-backed swing workspace</p>
          </div>
        </div>

        <nav className="nav-stack">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const active = view === item.id || (view === 'stock' && item.id === 'scanner')
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => navigateView(item.id)}
                className={active ? 'nav-item nav-item-active' : 'nav-item'}
              >
                <Icon size={17} />
                <div>
                  <strong>{item.label}</strong>
                  <span>{item.blurb}</span>
                </div>
              </button>
            )
          })}
        </nav>

        {selectedActionCandidate && (
          <Surface className="sidebar-card selected-stock-card">
            <span className="eyebrow">Selected Stock</span>
            <button type="button" className="selected-stock-button" onClick={() => openStock(selectedActionCandidate.symbol)}>
              <span>
                <strong>{selectedActionCandidate.symbol}</strong>
                <em>{selectedActionCandidate.company_name}</em>
              </span>
              <span className="score-chip">{selectedActionCandidate.score}</span>
            </button>
          </Surface>
        )}

        <Surface className="sidebar-card">
          <span className="eyebrow">Broker Pulse</span>
          {broker ? (
            <>
              <BrokerBadge broker={broker} />
              <p className="sidebar-card-copy">{broker.message}</p>
            </>
          ) : (
            <p className="sidebar-card-copy">Waiting for broker state...</p>
          )}
        </Surface>

        <Surface className="sidebar-card">
          <span className="eyebrow">Workflow Counts</span>
          <div className="sidebar-mini-stack">
            <div className="sidebar-mini-row">
              <Bookmark size={14} />
              <span>{watchlist.length} saved to watchlist</span>
            </div>
            <div className="sidebar-mini-row">
              <WalletCards size={14} />
              <span>{queueSymbols.size} paper trade plans</span>
            </div>
            <div className="sidebar-mini-row">
              <Landmark size={14} />
              <span>{accounts.length} live broker snapshots</span>
            </div>
          </div>
        </Surface>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <span className="eyebrow">Workspace</span>
            <h1 className="topbar-title">Swing desk</h1>
          </div>
          <div className="topbar-actions">
            {updatedAt && <div className="mini-chip">Updated {compactDate(updatedAt)}</div>}
            <button type="button" className="ghost-button" onClick={refreshAll} disabled={refreshing}>
              <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
              <span>{refreshing ? 'Loading' : 'Load Workspace'}</span>
            </button>
          </div>
        </header>

        {error && (
          <div className="alert-banner">
            <CircleAlert size={16} />
            <span>{error}</span>
          </div>
        )}

        {(loadingDetail || loadingHistory) && (view === 'scanner' || view === 'watchlist' || view === 'portfolio' || view === 'stock') && (
          <div className="mini-status">
            <Sparkles size={14} />
            <span>{loadingHistory ? 'Refreshing historical chart...' : 'Refreshing thesis panel...'}</span>
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.16, ease: 'easeOut' }}
          >
            {view === 'home' && (
              <HomeView
                home={home}
                loading={loadingHome}
                loadError={error}
                watchlistCount={watchlist.length}
                paperCount={queueSymbols.size}
                selectedSymbol={selectedSymbol}
                onSelect={openStock}
                onNavigate={navigateView}
                onQueue={addToPaperDesk}
                onReload={loadHomeDashboard}
              />
            )}
            {view === 'scanner' && (
              <ScannerView
                scanner={scanner}
                historicalScreener={historicalScreener}
                freshSignals={freshSignals}
                bambooLatest={bambooLatest}
                selectedSymbol={selectedSymbol}
                onSelect={openStock}
                onStageFresh={stageFreshNow}
                onRefreshCache={refreshFeatureCacheNow}
                onReload={loadScannerData}
                loading={loadingScanner}
                loadError={error}
                stagingFresh={stagingFresh}
                refreshingCache={refreshingFeatureCache}
              />
            )}
            {view === 'watchlist' && (
              <WatchlistView
                watchlist={watchlist}
                paperQueue={activePaperTrades.map(createCandidateFromPaperTrade)}
                onSelect={openStock}
                onQueue={addToPaperDesk}
                onRemove={removeFromWatchlist}
              />
            )}
            {view === 'portfolio' && (
              <PortfolioView
                paperTrades={activePaperTrades}
                closedTrades={closedPaperTrades}
                budget={paperBudget}
                onSelect={openStock}
                onRemove={removeFromPaperDesk}
                onWatch={addToWatchlist}
                onUpdate={updatePaperPlan}
              />
            )}
            {view === 'backtests' && (
              <BacktestsView
                dashboard={backtests}
                cache={backtestCache}
                running={runningBacktest}
                loading={loadingBacktestDashboard}
                refreshingCache={refreshingBacktestCache}
                onLoad={loadBacktestDashboard}
                onRefreshCache={refreshBacktestCacheNow}
                onRun={runBacktestNow}
              />
            )}
            {view === 'settings' && <SettingsView broker={broker} accounts={accounts} />}
            {view === 'stock' && (
              <StockDetailView
                candidate={selectedActionCandidate}
                historicalRow={selectedHistoricalRow}
                history={history}
                historyRange={historyRange}
                watchlisted={!!selectedActionCandidate && watchSymbols.has(selectedActionCandidate.symbol)}
                queued={!!selectedActionCandidate && queueSymbols.has(selectedActionCandidate.symbol)}
                onWatch={addToWatchlist}
                onQueue={addToPaperDesk}
                onHistoryRangeChange={setHistoryRange}
                onBack={() => navigateView('scanner')}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}
