import { useDeferredValue, useEffect, useMemo, useRef, useState, startTransition, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  BellRing,
  Bookmark,
  BriefcaseBusiness,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Compass,
  Database,
  ExternalLink,
  Filter,
  Landmark,
  LayoutDashboard,
  ListTodo,
  Newspaper,
  Radar,
  RefreshCw,
  Search,
  Sparkles,
  Target,
  TrendingUp,
  WalletCards,
  Zap,
} from 'lucide-react'
import {
  getBrokerAccounts,
  getBacktestDashboard,
  getBacktestDatewise,
  getBambooLatest,
  getCorporateEvents,
  getHistoricalScreener,
  getMarketActivity,
  getNews,
  getNewsPredictionHistory,
  getNseLargeDeals,
  closePaperTrade,
  deletePaperTrade,
  getPaperBudget,
  getPaperTrades,
  getSwingCandidate,
  getSwingHistory,
  getSwingHome,
  getSwingScanner,
  getPythonBacktestLab,
  pythonBacktestChartUrl,
  refreshBacktestCache,
  refreshFeatureCache,
  refreshMarketActivity,
  refreshNews,
  runBacktest,
  runPythonBacktestLab,
  savePaperTrade,
  stageFreshSignals,
  type BrokerAccountSnapshot,
  type BrokerStatus,
  type BacktestDashboardResponse,
  type BacktestCacheStatus,
  type BacktestCashEquityPoint,
  type BacktestCashProfile,
  type BacktestDatewiseResponse,
  type BacktestDayQuality,
  type BacktestEquityPoint,
  type BacktestRunSummary,
  type BacktestPrediction,
  type CorporateEvent,
  type ConfluenceFinding,
  type ConfluenceNewsArticle,
  type ConfluenceNewsSummary,
  type ConfluenceScoreBreakdown,
  type BacktestStrategyDiagnostic,
  type PythonBacktestLabResponse,
  type PythonBacktestMetricRow,
  type PythonBacktestPeriodRow,
  type BambooLatestResponse,
  type BambooLatestSignal,
  type HistoricalScreenerResponse,
  type HistoricalScreenerRow,
  type FreshSignalsResponse,
  type LiveStrategySnapshot,
  type LiveStrategyRow,
  type LiveSignal,
  type MarketActivityItem,
  type MarketActivityRefreshResult,
  type NewsItem,
  type NewsRefreshResult,
  type NseLargeDeal,
  type PaperTrade,
  type PaperBudget,
  type ResearchConfluence,
  type SymbolHistoryResponse,
  type SwingCandidate,
  type SwingHomeResponse,
  type SwingScannerResponse,
  type SetupMix,
} from './api'

type View = 'home' | 'market' | 'scanner' | 'watchlist' | 'portfolio' | 'backtests' | 'settings' | 'stock'
type HistoryRange = '1d' | '3m' | '6m' | '1y' | '3y' | '5y'
type PaperDeskTab = 'open' | 'closed' | 'weekly' | 'strategy' | 'intake'

interface NavItem {
  id: View
  label: string
  icon: LucideIcon
  blurb: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Dashboard', icon: LayoutDashboard, blurb: 'Market, risk, and top picks' },
  { id: 'market', label: 'Market Hub', icon: Activity, blurb: 'News, deals, and volume shockers' },
  { id: 'scanner', label: 'Scanner', icon: Radar, blurb: 'Strategy-first signal board' },
  { id: 'watchlist', label: 'Watchlist', icon: Bookmark, blurb: 'Organize stocks to monitor' },
  { id: 'backtests', label: 'Backtest', icon: BarChart3, blurb: 'Strategy returns and trades' },
  { id: 'portfolio', label: 'Paper Desk', icon: WalletCards, blurb: 'Live paper execution' },
  { id: 'settings', label: 'Settings', icon: Compass, blurb: 'Dhan status and API direction' },
]

const WATCHLIST_STORAGE_KEY = 'swing-watchlist'
const LIVE_ALERTS_STORAGE_KEY = 'swing-live-trigger-alerts'
const PAPER_CAPITAL_PER_STOCK = 50000
const PAPER_HOLD_SESSIONS = 10
const AUTO_PAPER_MAX_SUGGESTIONS = 7
const MIN_BACKTEST_STRATEGY_TRADES = 30
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
type ScannerDisplayRow = HistoricalScreenerRow & {
  company_name?: string
  source?: string
  signal_status?: LiveSignal['status']
  confluence?: ResearchConfluence
}
type ResearchCommitteeState = 'ENTRY_NOW' | 'ARMED' | 'WATCH'

interface ResearchCommitteePick {
  rank: number
  symbol: string
  companyName: string
  primary: ScannerDisplayRow
  state: ResearchCommitteeState
  researchScore: number
  committeeScore: number
  pillarCount: number
  conflictCount: number
  modelFamilies: string[]
  news?: ConfluenceNewsSummary
  newsTone: Tone
  reason: string
}

interface StrategyPlaybookEntry {
  name: string
  disposition: string
  tone: Tone
  parameters: string
}

interface StrategyPlaybookPillar {
  name: string
  description: string
  entries: StrategyPlaybookEntry[]
}

const RESEARCH_COMMITTEE_PICK_LIMIT = 5
const RESEARCH_COMMITTEE_MIN_PILLARS = 3
const RESEARCH_COMMITTEE_MIN_MODEL_FAMILIES = 2
const RESEARCH_COMMITTEE_MAX_CONFLICTS = 1
const RESEARCH_COMMITTEE_MIN_RISK_REWARD = 2

const STRATEGY_PLAYBOOK_PILLARS: StrategyPlaybookPillar[] = [
  {
    name: 'Trend breakout',
    description: 'Multiple 52W, momentum, breakout, compression, and relative-strength labels describe one correlated trend pillar.',
    entries: [
      {
        name: 'Momentum Core',
        disposition: 'Keep · primary',
        tone: 'positive',
        parameters: 'Within 3% of 52W high · ≥85% through range · close > SMA20 > SMA50 · score ≥92 · trigger 0.1% above 52W high.',
      },
      {
        name: '52W Runner',
        disposition: 'Rejected',
        tone: 'danger',
        parameters: 'Within 3% · uptrend · volume ≥0.8× · score ≥90. It is a weaker Momentum Core variant.',
      },
      {
        name: '52W Volume',
        disposition: 'Fragile',
        tone: 'warning',
        parameters: 'Within 6% of 52W high · volume ≥1.15× · range position ≥75% · score ≥88.',
      },
      {
        name: 'Near 52W High',
        disposition: 'Fragile',
        tone: 'warning',
        parameters: 'Within 8% of 52W high · close > SMA50 · score ≥80. This is the basic fallback trend label.',
      },
      {
        name: 'Breakout Continuation',
        disposition: 'Watch only',
        tone: 'warning',
        parameters: 'Clear 20D high · close location ≥60% · volume ≥1.1× · score ≥88.',
      },
      {
        name: 'Compression Breakout',
        disposition: 'Watch only',
        tone: 'warning',
        parameters: 'Breakout + volume ≥1.05× · ATR% <8 · candle range ≤ max(1.05× ATR%, 1.5) · score ≥88.',
      },
      {
        name: 'RS Leader Continuation',
        disposition: 'Watch only',
        tone: 'warning',
        parameters: 'RS60 ≥75th percentile · RS120 ≥65th · close > SMA50 · near 52W high or 55D/252D breakout · score ≥86.',
      },
      {
        name: 'Swing Breakout',
        disposition: 'Rejected',
        tone: 'danger',
        parameters: 'Fallback 20D breakout; trigger is prior 20D high +0.1%. It should not be counted separately from the trend pillar.',
      },
    ],
  },
  {
    name: 'Pullback / reclaim',
    description: 'These are counter-trend entries inside an uptrend, not a second vote for the same breakout.',
    entries: [
      {
        name: 'Pullback Quality',
        disposition: 'Rejected',
        tone: 'danger',
        parameters: 'Uptrend · within roughly 2–3% of SMA20 · volume ≥0.8× · close ≥SMA20 · score ≥88.',
      },
      {
        name: 'Pullback 20DMA',
        disposition: 'Rejected',
        tone: 'danger',
        parameters: 'Lower-quality SMA20 pullback fallback; same underlying family as Pullback Quality, never a separate confirmation.',
      },
    ],
  },
  {
    name: 'Reversal',
    description: 'Mean-reversion ideas remain research until they receive current validation and a clear entry trigger.',
    entries: [
      {
        name: 'RSI10 Pullback Reversion',
        disposition: 'Research only · unvalidated',
        tone: 'neutral',
        parameters: 'Close > SMA200 and RSI(10) <30. There is no explicit live trigger price, so it cannot become Entry Now automatically.',
      },
      {
        name: 'Failed Breakdown Reclaim',
        disposition: 'Research only · unvalidated',
        tone: 'neutral',
        parameters: 'Undercut prior 20D low then reclaim · close location ≥65% · volume ≥0.8× · score ≥86.',
      },
      {
        name: 'Tuned Panic Reversal',
        disposition: 'Watch / research',
        tone: 'warning',
        parameters: '3D return ≤−8% · range ≥1.35 ATR · close location ≥64% · recovery-low ≥1.2 · trigger after a 25% recovery.',
      },
    ],
  },
]

const STRATEGY_PLAYBOOK_RESEARCH_FEEDS: StrategyPlaybookEntry[] = [
  {
    name: 'Weekly Supertrend 10,3',
    disposition: 'Fresh-weekly confirmation only',
    tone: 'warning',
    parameters: 'Weekly Supertrend ATR(10)×3 + ₹50 minimum + 20W liquidity + breadth ≥35% + RS13W ≥50th percentile. Stale weekly rows are quarantined.',
  },
  {
    name: 'King Candle Quality',
    disposition: 'Fresh-weekly confirmation only',
    tone: 'warning',
    parameters: 'Weekly Supertrend positive · body/range ≥85% · close location ≥78% · range ≥1.15 ATR · rel. volume ≥1.2× · RS13W ≥85% · 20W breakout. Loose variant is backtest-only.',
  },
  {
    name: 'King Candle Supertrend (loose)',
    disposition: 'Backtest research only',
    tone: 'neutral',
    parameters: 'Looser weekly version: body/range ≥65% and RS13W ≥55%. It is never a live Top Pick and remains correlated with King Candle Quality.',
  },
  {
    name: 'Bamboo MTF Breakout (six variants)',
    disposition: 'Research only',
    tone: 'neutral',
    parameters: 'Long-base/daily breakout family: SMA50/SMA200 trend · ₹50 minimum · 20D breakout · relative volume ≥1.1× · close location ≥65%.',
  },
  {
    name: 'Unlinked Screener',
    disposition: 'Never eligible',
    tone: 'danger',
    parameters: 'Catch-all raw MA/breakout row without a linked validated strategy. It stays out of Top Picks.',
  },
]

interface BacktestPaperRule {
  stopLossPct: number
  takeProfitPct: number
  source: string
}

const BACKTEST_PAPER_RULES: Record<string, BacktestPaperRule> = {
  'tuned-panic-reversal-v1': {
    stopLossPct: 4,
    takeProfitPct: 10,
    source: 'tuned panic reversal lab model',
  },
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

const HISTORY_RANGES: HistoryRange[] = ['1d', '3m', '6m', '1y', '3y', '5y']

function currency(value: number) {
  return `Rs ${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function compactCurrency(value: number) {
  const absValue = Math.abs(value)
  if (absValue >= 100000) return `Rs ${(value / 100000).toFixed(1)}L`
  if (absValue >= 1000) return `Rs ${(value / 1000).toFixed(1)}k`
  return currency(value)
}

function compactNumber(value: number) {
  const absValue = Math.abs(value)
  if (absValue >= 10000000) return `${(value / 10000000).toFixed(2)}Cr`
  if (absValue >= 100000) return `${(value / 100000).toFixed(2)}L`
  if (absValue >= 1000) return `${(value / 1000).toFixed(1)}k`
  return value.toLocaleString('en-IN')
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

function marketDateAgeDays(value: string | null | undefined) {
  if (!value) return Number.POSITIVE_INFINITY
  const normalized = value.includes('T') ? value : value.includes(' ') ? `${value.replace(' ', 'T')}+05:30` : `${value}T00:00:00+05:30`
  const timestamp = new Date(normalized).getTime()
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : Math.floor((Date.now() - timestamp) / 86_400_000)
}

function marketTimestamp(value: string | null | undefined) {
  if (!value) return 'Time unavailable'
  const normalized = value.includes('T') ? value : value.includes(' ') ? `${value.replace(' ', 'T')}+05:30` : `${value}T00:00:00+05:30`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatSourceName(source: string) {
  return (source || 'source')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function readableLabel(value: string | null | undefined, fallback = 'Not calculated') {
  if (!value) return fallback
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function asFiniteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function confluencePillarCount(confluence: ResearchConfluence | undefined) {
  const declared = asFiniteNumber(confluence?.pillar_count)
  if (declared !== null) return Math.max(0, Math.round(declared))
  return confluence?.strategy_matches?.length ?? 0
}

function confluenceStrategyCount(confluence: ResearchConfluence | undefined) {
  return confluence?.strategy_matches?.length ?? 0
}

function confluenceTradeState(confluence: ResearchConfluence | undefined) {
  return confluence?.trade_state || confluence?.research_state || confluence?.confluence_state
}

function researchTone(confluence: ResearchConfluence | undefined, fallbackScore?: number): Tone {
  const state = confluenceTradeState(confluence)?.toUpperCase() ?? ''
  if (/(INVALID|NO[_ -]?TRADE|REJECT|CONFLICT|RISK)/.test(state)) return 'danger'
  if (/(ENTRY|CONFIRM|READY|SUPPORTED|QUALIFIED)/.test(state)) return 'positive'
  if (/(WATCH|ARM|WAIT|REVIEW)/.test(state)) return 'warning'
  if ((fallbackScore ?? 0) >= 85) return 'positive'
  return 'neutral'
}

function newsTone(news: ConfluenceNewsSummary | undefined): Tone {
  if (!news) return 'neutral'
  const direction = news.direction?.toUpperCase() ?? ''
  const bullish = asFiniteNumber(news.bullish_articles) ?? 0
  const bearish = asFiniteNumber(news.bearish_articles) ?? 0
  if (direction.includes('BEAR') || bearish > bullish) return 'danger'
  if (direction.includes('BULL') || bullish > bearish) return 'positive'
  if (direction.includes('WATCH') || direction.includes('CAUTION')) return 'warning'
  return 'neutral'
}

function researchCommitteeStateForRow(row: ScannerDisplayRow): ResearchCommitteeState | null {
  const declared = (confluenceTradeState(row.confluence) ?? '').toUpperCase()
  if (/(ENTRY[ _-]?(READY|NOW)|CONFIRMED)/.test(declared)) return 'ENTRY_NOW'
  if (/(ARMED|WAIT[ _-]?FOR[ _-]?TRIGGER)/.test(declared)) return 'ARMED'
  if (/WATCH/.test(declared)) return 'WATCH'

  const state = `${row.signal_status ?? ''} ${row.trend_label ?? ''}`.toUpperCase()
  if (/(ENTRY[ _-]?(READY|NOW)|ENTER NOW)/.test(state)) return 'ENTRY_NOW'
  if (/(ARMED|WAIT[ _-]?FOR[ _-]?TRIGGER|SIGNAL READY)/.test(state)) return 'ARMED'
  if (/WATCH/.test(state)) return 'WATCH'
  if (row.strategy_status === 'Candidate') return 'ARMED'
  if (row.strategy_status === 'Watch') return 'WATCH'
  return null
}

function researchCommitteeStateRank(state: ResearchCommitteeState) {
  if (state === 'ENTRY_NOW') return 3
  if (state === 'ARMED') return 2
  return 1
}

function researchCommitteeStateLabel(state: ResearchCommitteeState) {
  if (state === 'ENTRY_NOW') return 'Entry Now'
  if (state === 'ARMED') return 'Armed'
  return 'Watch'
}

function researchCommitteeStateTone(state: ResearchCommitteeState): Tone {
  if (state === 'ENTRY_NOW') return 'positive'
  if (state === 'ARMED') return 'warning'
  return 'neutral'
}

function researchCommitteeExcluded(row: ScannerDisplayRow) {
  const values = [
    row.strategy_status,
    row.trend_label,
    confluenceTradeState(row.confluence),
    row.confluence?.confluence_state,
  ]
    .filter(Boolean)
    .join(' ')
    .toUpperCase()
  return /(NO[ _-]?TRADE|INVALID|REJECT|FRAGILE|WATCH|BLOCKED)/.test(values)
}

function researchCommitteeCurrent(row: ScannerDisplayRow) {
  if (row.source !== 'dhan-live') return false
  const ageDays = marketDateAgeDays(row.as_of)
  if (Number.isFinite(ageDays) && ageDays > 4) return false
  return row.strategy_status === 'Candidate'
}

function researchCommitteeApprovedTradeState(row: ScannerDisplayRow) {
  const state = (confluenceTradeState(row.confluence) ?? '').toUpperCase()
  return /(ENTRY[ _-]?(READY|NOW)|CONFIRMED|ARMED|WAIT[ _-]?FOR[ _-]?TRIGGER)/.test(state)
}

function researchCommitteeScore(row: ScannerDisplayRow) {
  const score = asFiniteNumber(row.confluence?.research_score)
    ?? asFiniteNumber(row.confluence?.score_breakdown?.total_score)
    ?? row.score
  return Math.max(0, Math.min(100, Math.round(score)))
}

function researchCommitteePillarCount(rows: ScannerDisplayRow[]) {
  return rows.reduce((best, row) => {
    const declared = asFiniteNumber(row.confluence?.supporting_pillars)
      ?? asFiniteNumber(row.confluence?.pillar_count)
      ?? 0
    return Math.max(best, Math.round(declared))
  }, 0)
}

function researchCommitteeConflictCount(rows: ScannerDisplayRow[]) {
  return rows.reduce((best, row) => {
    const declared = asFiniteNumber(row.confluence?.conflicting_pillars) ?? 0
    return Math.max(best, Math.round(declared))
  }, 0)
}

function researchCommitteeModelFamily(
  strategyId: string | undefined,
  strategyLabel: string | undefined,
  setupFamily: string | undefined,
) {
  const source = `${strategyId ?? ''} ${strategyLabel ?? ''} ${setupFamily ?? ''}`.toLowerCase()
  const canonicalFamily = (setupFamily ?? '').trim().toLowerCase()
  if (!source.trim() || /(unlinked|unscored)/.test(source)) return null
  // The API provides canonical families for genuine cross-model matches.
  // Weekly trend remains timeframe confirmation of the same breakout pillar.
  if (canonicalFamily === 'trend breakout' || canonicalFamily === 'weekly trend') return 'Trend breakout'
  if (canonicalFamily === 'relative strength') return 'Trend breakout'
  if (canonicalFamily === 'pullback') return 'Pullback / reclaim'
  if (canonicalFamily === 'failed breakdown reclaim') return 'Oversold reversal'
  if (canonicalFamily === 'oversold reversal') return 'Oversold reversal'
  if (/(52w|momentum|relative[ _-]?strength|rs[ _-]?leader)/.test(source)) return 'Trend breakout'
  if (/(breakout|compression|king[ _-]?candle|supertrend)/.test(source)) return 'Breakout / trend'
  if (/(pullback|reversal|reclaim|panic|rsi)/.test(source)) return 'Pullback / reversal'
  if (/trend/.test(source)) return 'Trend filter'
  return 'Other approved model'
}

function researchCommitteeModelFamilies(rows: ScannerDisplayRow[]) {
  const families = new Set<string>()
  const addModel = (strategyId?: string, strategyLabel?: string, setupFamily?: string) => {
    const family = researchCommitteeModelFamily(strategyId, strategyLabel, setupFamily)
    if (family) families.add(family)
  }

  rows.forEach((row) => {
    addModel(row.strategy_id, row.strategy_label, row.setup_family)
    row.confluence?.strategy_matches?.forEach((match) => {
      // A watch-only rule is useful dossier context, but cannot create the
      // appearance of independent model agreement in the decision shortlist.
      if (match.strategy_status === 'Candidate') {
        addModel(match.strategy_id, match.strategy_label, match.setup_family)
      }
    })
  })

  return Array.from(families).sort()
}

function researchCommitteeNews(rows: ScannerDisplayRow[]) {
  let selected: ConfluenceNewsSummary | undefined
  let selectedWeight = -1
  rows.forEach((row) => {
    const news = row.confluence?.news
    if (!news) return
    const articles = asFiniteNumber(news.article_count) ?? 0
    const impact = Math.abs(asFiniteNumber(news.max_impact) ?? 0)
    const adjustment = Math.abs(asFiniteNumber(news.score_adjustment) ?? 0)
    const weight = articles * 100 + impact * 10 + adjustment
    if (weight > selectedWeight) {
      selected = news
      selectedWeight = weight
    }
  })
  return selected
}

function researchCommitteeReason({
  pillarCount,
  modelFamilies,
  news,
  conflictCount,
}: Pick<ResearchCommitteePick, 'pillarCount' | 'modelFamilies' | 'news' | 'conflictCount'>) {
  const parts = [
    `${pillarCount} independent pillar${pillarCount === 1 ? '' : 's'}`,
    `${modelFamilies.length} model famil${modelFamilies.length === 1 ? 'y' : 'ies'}`,
  ]
  const tone = newsTone(news)
  if (tone === 'positive') parts.push(`${newsSummaryLabel(news)} supports it`)
  else if (tone === 'danger') parts.push(`${newsSummaryLabel(news)} is a caution`)
  else if (news) parts.push('news is not decisive')
  else parts.push('no linked catalyst')
  if (conflictCount > 0) parts.push(`${conflictCount} risk flag${conflictCount === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

function buildResearchCommitteePicks(rows: ScannerDisplayRow[]): ResearchCommitteePick[] {
  const eligibleRows = rows.filter((row) => (
    Boolean(row.confluence)
    && researchCommitteeCurrent(row)
    && researchCommitteeApprovedTradeState(row)
    && !researchCommitteeExcluded(row)
    && researchCommitteeStateForRow(row) !== null
  ))
  const bySymbol = new Map<string, ScannerDisplayRow[]>()
  eligibleRows.forEach((row) => {
    const key = row.symbol.trim().toUpperCase()
    if (!key) return
    bySymbol.set(key, [...(bySymbol.get(key) ?? []), row])
  })

  const picks = Array.from(bySymbol.entries()).map(([symbol, symbolRows]) => {
    const primary = [...symbolRows].sort((left, right) => {
      const stateDelta = researchCommitteeStateRank(researchCommitteeStateForRow(right)!) - researchCommitteeStateRank(researchCommitteeStateForRow(left)!)
      if (stateDelta !== 0) return stateDelta
      return researchCommitteeScore(right) - researchCommitteeScore(left)
        || right.score - left.score
    })[0]
    const state = researchCommitteeStateForRow(primary)!
    const researchScore = researchCommitteeScore(primary)
    const pillarCount = researchCommitteePillarCount(symbolRows)
    const conflictCount = researchCommitteeConflictCount(symbolRows)
    const modelFamilies = researchCommitteeModelFamilies(symbolRows)
    const news = researchCommitteeNews(symbolRows)
    const catalystAdjustment = newsTone(news) === 'positive'
      ? 4
      : newsTone(news) === 'danger'
        ? -8
        : newsTone(news) === 'warning'
          ? -2
          : 0
    const riskRewardAdjustment = primary.risk_reward >= 2 ? 2 : primary.risk_reward >= 1.5 ? 1 : 0
    const committeeScore = Math.max(0, Math.min(100, Math.round(
      researchScore * 0.72
      + Math.min(pillarCount, 5) * 4
      + Math.min(modelFamilies.length, 3) * 3
      + catalystAdjustment
      + riskRewardAdjustment
      - Math.min(conflictCount, 3) * 4,
    )))

    return {
      rank: 0,
      symbol,
      companyName: primary.company_name || '',
      primary,
      state,
      researchScore,
      committeeScore,
      pillarCount,
      conflictCount,
      modelFamilies,
      news,
      newsTone: newsTone(news),
      reason: researchCommitteeReason({ pillarCount, modelFamilies, news, conflictCount }),
    }
  })

  return picks
    .filter((pick) => (
      pick.pillarCount >= RESEARCH_COMMITTEE_MIN_PILLARS
      && pick.newsTone !== 'danger'
    ))
    .sort((left, right) => (
      researchCommitteeStateRank(right.state) - researchCommitteeStateRank(left.state)
      || right.committeeScore - left.committeeScore
      || right.researchScore - left.researchScore
      || left.symbol.localeCompare(right.symbol)
    ))
    .slice(0, RESEARCH_COMMITTEE_PICK_LIMIT)
    .map((pick, index) => ({ ...pick, rank: index + 1 }))
}

function confluenceFindingText(finding: string | ConfluenceFinding) {
  if (typeof finding === 'string') return finding
  const title = finding.title || finding.label || finding.pillar || finding.source || 'Research evidence'
  const detail = finding.detail || finding.reason
  return detail && detail !== title ? `${title}: ${detail}` : title
}

function confluenceFindingUrl(finding: string | ConfluenceFinding) {
  return typeof finding === 'string' ? undefined : finding.source_url || finding.url
}

function confluenceNewsArticles(news: ConfluenceNewsSummary | undefined): ConfluenceNewsArticle[] {
  if (!news) return []
  const articles = [...(news.articles ?? []), ...(news.items ?? [])]
  if (articles.length > 0) return articles
  if (news.latest_url) {
    return [{
      title: news.latest_headline || news.latest_reason || 'Open linked news source',
      source: news.latest_source || news.source,
      url: news.latest_url,
    }]
  }
  if (news.source_url || news.url) {
    return [{
      title: news.latest_reason || 'Open linked news source',
      source: news.source,
      source_url: news.source_url,
      url: news.url,
    }]
  }
  return []
}

function newsSummaryLabel(news: ConfluenceNewsSummary | undefined) {
  if (!news) return 'News not linked'
  const bullish = asFiniteNumber(news.bullish_articles) ?? 0
  const bearish = asFiniteNumber(news.bearish_articles) ?? 0
  const articles = asFiniteNumber(news.article_count) ?? bullish + bearish
  if (bearish > 0 && bearish >= bullish) return `${bearish} bearish / ${articles} article${articles === 1 ? '' : 's'}`
  if (bullish > 0) return `${bullish} bullish / ${articles} article${articles === 1 ? '' : 's'}`
  return articles > 0 ? `${articles} recent article${articles === 1 ? '' : 's'}` : 'No recent article'
}

function formatActivityMetric(metric: string) {
  if (metric === 'volume_shockers') return 'Volume Shocker'
  if (metric === 'most_active') return 'Most Active'
  return formatSourceName(metric || 'activity')
}

function formatConfidence(value: number) {
  if (!Number.isFinite(value)) return '0%'
  return `${Math.round(value * 100)}%`
}

function marketTimestampValue(value: string | null | undefined) {
  if (!value) return 0
  const normalized = value.includes('T') ? value : value.includes(' ') ? `${value.replace(' ', 'T')}+05:30` : `${value}T00:00:00+05:30`
  const timestamp = new Date(normalized).getTime()
  return Number.isNaN(timestamp) ? 0 : timestamp
}

type NewsDeskSort = 'latest' | 'volume' | 'deals' | 'impact'
type NewsDeskView = 'actionable' | 'linked'

interface NewsPastEvidence {
  article_id: string
  symbol: string
  prediction_status?: string
  outcome?: string
  return_pct?: number
  target_hit?: boolean
  evaluated_at?: string
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
    trigger_source: null,
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
    && candidate.live_signal.status !== 'NO_TRADE'
    && candidate.live_signal.status !== 'INVALIDATED'
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
    news: 'market',
    activity: 'market',
    'market-hub': 'market',
  }
  const knownViews: View[] = ['home', 'market', 'scanner', 'watchlist', 'portfolio', 'backtests', 'settings', 'stock']
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

void [upsertCandidate, removeCandidate, createCandidateFromHistoricalRow, createCandidateFromBambooSignal]

function ruleForStrategy(strategyId: string) {
  return BACKTEST_PAPER_RULES[strategyId] ?? null
}

function createCandidateFromHistoricalRow(row: HistoricalScreenerRow): SwingCandidate {
  const rule = ruleForStrategy(row.strategy_id)
  const staleSignal = marketDateAgeDays(row.as_of) > 4
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
    confidence: staleSignal ? 'Historical Only' : row.score >= 88 ? 'High Conviction' : row.score >= 78 ? 'Actionable' : 'Watchlist',
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
    thesis: staleSignal
      ? `${row.symbol} matched ${strategyLabel} on ${row.as_of}, but that dataset is stale and cannot authorize a fresh entry.`
      : `${row.symbol} is staged only because the latest parquet screener row maps to the backtest-tracked ${strategyLabel} strategy status: ${row.strategy_status}.`,
    reasons: [
      rule ? `Exit model uses ${rule.source}: ${stopLossPct}% stop, ${takeProfitPct}% target, capped at ${PAPER_HOLD_SESSIONS} trading sessions.` : 'No strategy config was found for this row, so it is review-only unless a stop is supplied.',
      `${row.symbol} is ${row.distance_to_20d_high_pct.toFixed(2)}% away from the 20-day high.`,
      `Volume ratio is ${row.volume_ratio.toFixed(2)}x against the 20-day average.`,
      `ATR is ${row.atr_pct.toFixed(2)}% of close; RS60 rank is ${row.rs60_rank.toFixed(0)}.`,
      `Trend profile is ${row.trend_label}; strategy lab status is ${row.strategy_status}.`,
    ],
    risks: [
      `Stop is fixed at ${currency(stopLoss)} from the configured backtest rule.`,
      ...(staleSignal ? [`Signal date ${row.as_of} is more than four calendar days old; refresh market data before any paper or live entry.`] : []),
      'Paper staging is evidence-gathering only; backtested behavior can fail in forward trading.',
    ],
    source: 'parquet-screener',
    live_signal: defaultLiveSignal({
      status: staleSignal ? 'NO_TRADE' : row.strategy_status === 'Watch' ? 'WATCH' : row.strategy_status === 'Rejected' ? 'NO_TRADE' : 'WAIT_FOR_TRIGGER',
      label: staleSignal ? 'Refresh Data' : row.strategy_status === 'Watch' ? 'Watch Only' : row.strategy_status === 'Rejected' ? 'No Trade' : 'Needs Live Trigger',
      reason: staleSignal
        ? `Latest strategy bar is ${row.as_of}; no entry is allowed until the parquet/live feature data is current.`
        : `Historical screener row maps to ${strategyLabel}; live Dhan confirmation is still required before entry.`,
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
      trigger_source: 'Prior 20D high',
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

function createCandidateFromLiveRow(row: LiveStrategyRow): SwingCandidate {
  const strategyLabel = row.strategy_label || row.setup_family || strategyLabelForDisplay(row.strategy_id)
  const triggerText = row.trigger_price && row.trigger_price > 0
    ? `Live trigger Rs ${row.trigger_price.toFixed(2)}`
    : `Live LTP Rs ${row.last_price.toFixed(2)}`
  const status = row.signal_status as LiveSignal['status']
  const confidence =
    status === 'ENTRY_NOW' ? 'Enter Now'
      : status === 'WATCH' ? 'Watch Only'
      : status === 'NO_TRADE' || status === 'INVALIDATED' ? 'No Trade'
      : row.score >= 88 ? 'Wait For Trigger'
      : 'Live Candidate'
  return {
    symbol: row.symbol,
    company_name: row.company_name || row.symbol,
    setup_family: row.setup_family || strategyLabel,
    bias: 'Long',
    score: row.score,
    confidence,
    regime_fit: Math.max(50, Math.min(95, row.score - 4)),
    risk_reward: row.risk_reward,
    last_price: row.last_price,
    day_change_pct: row.day_change_pct,
    open_gap_pct: row.open_gap_pct,
    distance_to_high_pct: 0,
    liquidity_bucket: 'LIVE',
    entry_zone: triggerText,
    stop_loss: row.stop_loss,
    target_price: row.target_price,
    expected_hold: 'Live strategy plan',
    thesis: row.reason,
    reasons: [
      row.reason,
      `Live Dhan source ${row.source}; volume ${row.volume.toLocaleString('en-IN')}.`,
      `Signal status is ${row.signal_label} under ${strategyLabel}.`,
    ],
    risks: [
      `Stop loss is ${currency(row.stop_loss)}.`,
      status === 'ENTRY_NOW'
        ? 'Live signal is active now; size still needs manual risk control.'
        : 'Do not treat this as an entry until the live status says Enter Now.',
    ],
    source: row.source,
    confluence: row.confluence,
    live_signal: {
      status,
      label: row.signal_label,
      reason: row.reason,
      strategy_id: row.strategy_id,
      strategy_label: strategyLabel,
      strategy_status: row.strategy_status,
      setup_family: row.setup_family,
      score: row.score,
      as_of: row.updated_at,
      trigger_price: row.trigger_price,
      trigger_source: row.trigger_source,
    },
  }
}

function createHistoricalRowFromLiveRow(row: LiveStrategyRow): ScannerDisplayRow {
  const triggerCleared = !!row.trigger_price && row.trigger_price > 0 && row.last_price >= row.trigger_price
  const plannedEntry = row.signal_status === 'ENTRY_NOW'
    ? `Live entry Rs ${row.last_price.toFixed(2)}`
    : triggerCleared
      ? `Live LTP Rs ${row.last_price.toFixed(2)}`
      : row.trigger_price && row.trigger_price > 0
      ? `Trigger Rs ${row.trigger_price.toFixed(2)}`
      : `Live LTP Rs ${row.last_price.toFixed(2)}`
  return {
    symbol: row.symbol,
    company_name: row.company_name,
    as_of: row.updated_at,
    setup_family: row.setup_family || row.strategy_label,
    strategy_id: row.strategy_id,
    strategy_label: row.strategy_label,
    strategy_status: row.strategy_status,
    score: row.score,
    trend_label: row.signal_label,
    close: row.last_price,
    sma20: row.last_price,
    sma50: row.last_price,
    avg_volume20: row.volume,
    volume_ratio: 0,
    distance_to_20d_high_pct: 0,
    distance_to_52w_high_pct: 0,
    range_position_pct: 0,
    atr14: 0,
    atr_pct: 0,
    close_location: 0,
    gap_pct: row.open_gap_pct,
    rs60_rank: row.score,
    rs120_rank: row.score,
    market_breadth200: 0,
    planned_entry: plannedEntry,
    trigger_price: row.trigger_price,
    trigger_source: row.trigger_source,
    stop_loss: row.stop_loss,
    target_price: row.target_price,
    risk_reward: row.risk_reward,
    source: row.source,
    signal_status: row.signal_status as LiveSignal['status'],
    confluence: row.confluence,
  }
}

function strategyLabelForDisplay(strategyId: string) {
  return strategyId
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Live Strategy'
}

function setupMixFromCandidates(candidates: SwingCandidate[]): SetupMix[] {
  const groups = new Map<string, { count: number; score: number }>()
  candidates.forEach((candidate) => {
    const family = candidate.setup_family || candidate.live_signal.strategy_label || 'Live Strategy'
    const next = groups.get(family) ?? { count: 0, score: 0 }
    next.count += 1
    next.score += candidate.score
    groups.set(family, next)
  })
  return Array.from(groups.entries())
    .map(([family, value]) => ({
      family,
      count: value.count,
      avg_score: value.score / Math.max(value.count, 1),
    }))
    .sort((a, b) => b.count - a.count || b.avg_score - a.avg_score)
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

function createHistoricalRowFromCandidate(candidate: SwingCandidate): ScannerDisplayRow {
  return {
    symbol: candidate.symbol,
    company_name: candidate.company_name,
    as_of: candidate.live_signal.as_of,
    setup_family: candidate.setup_family,
    strategy_id: candidate.live_signal.strategy_id,
    strategy_label: candidate.live_signal.strategy_label || candidate.setup_family,
    strategy_status: candidate.live_signal.strategy_status || 'Candidate',
    score: candidate.score,
    trend_label: candidate.live_signal.label || (candidate.regime_fit >= 70 ? 'Constructive' : 'Mixed'),
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
    trigger_price: candidate.live_signal.trigger_price,
    trigger_source: candidate.live_signal.trigger_source,
    stop_loss: candidate.stop_loss,
    target_price: candidate.target_price,
    risk_reward: candidate.risk_reward,
    source: candidate.source,
    signal_status: candidate.live_signal.status,
    confluence: candidate.confluence,
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
  const isDhanIntraday = history.source === 'dhan-intraday'
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
          <h3>{history.symbol} {isDhanIntraday ? 'live intraday chart from Dhan' : 'price structure from parquet history'}</h3>
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
          {isDhanIntraday ? (
            <>
              <CandidateStat label="Last Price" value={currency(summary.latest_close)} />
              <CandidateStat label="Session High" value={currency(summary.high_52w)} tone="positive" />
              <CandidateStat label="Session Low" value={currency(summary.low_52w)} tone="warning" />
              <CandidateStat label="Avg Vol" value={compactNumber(summary.avg_volume_20d)} />
            </>
          ) : (
            <>
              <CandidateStat label="1M Return" value={`${summary.change_pct_1m >= 0 ? '+' : ''}${summary.change_pct_1m.toFixed(2)}%`} tone={summary.change_pct_1m >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="3M Return" value={`${summary.change_pct_3m >= 0 ? '+' : ''}${summary.change_pct_3m.toFixed(2)}%`} tone={summary.change_pct_3m >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="1Y Return" value={`${summary.change_pct_1y >= 0 ? '+' : ''}${summary.change_pct_1y.toFixed(2)}%`} tone={summary.change_pct_1y >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="52W Range" value={`Rs ${summary.low_52w.toFixed(0)} - ${summary.high_52w.toFixed(0)}`} />
            </>
          )}
        </div>
      )}
    </Surface>
  )
}

function ScannerResearchCell({
  confluence,
  score,
}: {
  confluence: ResearchConfluence | undefined
  score: number
}) {
  if (!confluence) {
    return (
      <span className="scanner-confluence-cell scanner-confluence-empty">
        <strong>Base setup</strong>
        <small>Research layer pending</small>
      </span>
    )
  }

  const pillarCount = confluencePillarCount(confluence)
  const strategyCount = confluenceStrategyCount(confluence)
  return (
    <span className={`scanner-confluence-cell tone-${researchTone(confluence, score)}`}>
      <strong>{readableLabel(confluenceTradeState(confluence), 'Researching')}</strong>
      <small>{pillarCount} pillar{pillarCount === 1 ? '' : 's'} · {strategyCount} strateg{strategyCount === 1 ? 'y' : 'ies'}</small>
    </span>
  )
}

function ScannerNewsCell({ news }: { news: ConfluenceNewsSummary | undefined }) {
  if (!news) {
    return (
      <span className="scanner-news-cell scanner-news-empty">
        <strong>Not linked</strong>
        <small>News data pending</small>
      </span>
    )
  }

  const direction = readableLabel(news.direction, 'Mixed')
  return (
    <span className={`scanner-news-cell tone-${newsTone(news)}`}>
      <strong>{direction}</strong>
      <small>{newsSummaryLabel(news)}</small>
    </span>
  )
}

function ResearchCommitteePanel({
  picks,
  selectedSymbol,
  onSelect,
}: {
  picks: ResearchCommitteePick[]
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
}) {
  const entryNowCount = picks.filter((pick) => pick.state === 'ENTRY_NOW').length
  const multiModelEntryNowCount = picks.filter((pick) => (
    pick.state === 'ENTRY_NOW'
    && pick.modelFamilies.length >= RESEARCH_COMMITTEE_MIN_MODEL_FAMILIES
    && pick.conflictCount <= RESEARCH_COMMITTEE_MAX_CONFLICTS
    && pick.primary.risk_reward >= RESEARCH_COMMITTEE_MIN_RISK_REWARD
  )).length
  const armedCount = picks.filter((pick) => pick.state === 'ARMED').length
  const title = multiModelEntryNowCount > 0
    ? 'Top picks: one stock, one decision'
    : 'Research watchlist: waiting for independent confirmation'

  return (
    <Surface className="inner-surface research-committee-panel">
      <div className="research-committee-head">
        <div>
          <span className="eyebrow">Research Committee</span>
          <h3>{title}</h3>
          <p>
            Ranked once per symbol from current Dhan-live Candidate research. A true Top Pick needs Entry Now, at least {RESEARCH_COMMITTEE_MIN_PILLARS} independent evidence pillars, {RESEARCH_COMMITTEE_MIN_MODEL_FAMILIES} Candidate model families, R:R at least {RESEARCH_COMMITTEE_MIN_RISK_REWARD}:1, at most {RESEARCH_COMMITTEE_MAX_CONFLICTS} risk flag, and no bearish linked news. Correlated 52W and momentum variants count as one Trend breakout family; this is a research shortlist, not buy advice.
          </p>
        </div>
        <div className="research-committee-stats">
          <CandidateStat label="Shortlist" value={`${picks.length}/${RESEARCH_COMMITTEE_PICK_LIMIT}`} tone={picks.length > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Entry Now" value={String(entryNowCount)} tone={entryNowCount > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="True Top Picks" value={String(multiModelEntryNowCount)} tone={multiModelEntryNowCount > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Armed" value={String(armedCount)} tone={armedCount > 0 ? 'warning' : 'neutral'} />
        </div>
      </div>

      {picks.length > 0 ? (
        <div className="research-committee-grid">
          {picks.map((pick) => {
            const trigger = pick.primary.trigger_price && pick.primary.trigger_price > 0
              ? currency(pick.primary.trigger_price)
              : 'Live confirmation'
            const newsLabel = pick.newsTone === 'positive'
              ? 'Catalyst supports'
              : pick.newsTone === 'danger'
                ? 'News caution'
                : pick.news
                  ? 'News neutral'
                  : 'No catalyst'
            return (
              <button
                key={pick.symbol}
                type="button"
                onClick={() => onSelect(pick.symbol)}
                className={`committee-pick-card committee-state-${pick.state.toLowerCase().replace(/_/g, '-')} ${pick.symbol === selectedSymbol ? 'committee-pick-active' : ''}`.trim()}
              >
                <div className="committee-pick-top">
                  <div className="committee-symbol-block">
                    <span className="committee-rank">#{pick.rank}</span>
                    <span>
                      <strong>{pick.symbol}</strong>
                      <small>{pick.companyName || pick.primary.strategy_label}</small>
                    </span>
                  </div>
                  <span className="committee-score" title="Committee ranking score, not a trade signal">
                    <strong>{pick.committeeScore}</strong>
                    <small>rank</small>
                  </span>
                </div>

                <div className="committee-pick-status">
                  <StagePill label={researchCommitteeStateLabel(pick.state)} tone={researchCommitteeStateTone(pick.state)} />
                  <span>{pick.primary.strategy_label}</span>
                </div>

                <p className="committee-reason">{pick.reason}</p>

                <div className="committee-chip-row">
                  <span>{pick.modelFamilies.join(' + ')}</span>
                  <span className={`tone-${pick.newsTone}`}>{newsLabel}</span>
                </div>

                <div className="committee-plan-row">
                  <span>Trigger {trigger}</span>
                  <span>R:R {pick.primary.risk_reward.toFixed(1)}</span>
                  <span>Research {pick.researchScore}/100</span>
                </div>
                <span className="committee-open-copy">Open full research <ArrowUpRight size={13} /></span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="research-committee-empty">
          <CircleAlert size={17} />
          <p>
            No current research picks qualify yet. The committee requires a current Dhan-live Candidate row in Entry Now or Armed state, at least {RESEARCH_COMMITTEE_MIN_PILLARS} independent evidence pillars, and no bearish linked news; watch-only, rejected, fragile, invalidated, no-trade, and stale rows stay in the raw research section below.
          </p>
        </div>
      )}
    </Surface>
  )
}

function StrategyPlaybookPanel() {
  return (
    <Surface className="inner-surface strategy-playbook-panel">
      <details open>
        <summary className="strategy-playbook-summary">
          <div>
            <span className="eyebrow">Strategy playbook</span>
            <h3>Three independent pillars, not a pile of duplicate votes</h3>
            <p>Every current model label, its core filters, and its present role in research.</p>
          </div>
          <span className="strategy-playbook-summary-chip">{STRATEGY_PLAYBOOK_PILLARS.length} pillars + research feeds</span>
        </summary>

        <div className="strategy-playbook-body">
          <div className="strategy-playbook-rules">
            <span>Top Picks count independent pillars, not every matching label.</span>
            <span>Research score ranks evidence; it is not a probability or a buy instruction.</span>
            <span>Fresh data, a valid trade state, trigger, risk plan, and non-bearish news are still required.</span>
          </div>

          <div className="strategy-playbook-pillars">
            {STRATEGY_PLAYBOOK_PILLARS.map((pillar) => (
              <section key={pillar.name} className="strategy-playbook-pillar">
                <div className="strategy-playbook-pillar-head">
                  <span className="eyebrow">Evidence pillar</span>
                  <h4>{pillar.name}</h4>
                  <p>{pillar.description}</p>
                </div>
                <div className="strategy-playbook-entries">
                  {pillar.entries.map((entry) => (
                    <article key={entry.name} className="strategy-playbook-entry">
                      <div className="strategy-playbook-entry-head">
                        <strong>{entry.name}</strong>
                        <StagePill label={entry.disposition} tone={entry.tone} />
                      </div>
                      <p>{entry.parameters}</p>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>

          <section className="strategy-playbook-feeds">
            <div className="strategy-playbook-pillar-head">
              <span className="eyebrow">Confirmation / research feeds</span>
              <h4>Useful context, never extra votes</h4>
              <p>Weekly and Bamboo signals can support a clean setup, but are not independent Top Pick strategies.</p>
            </div>
            <div className="strategy-playbook-feed-grid">
              {STRATEGY_PLAYBOOK_RESEARCH_FEEDS.map((entry) => (
                <article key={entry.name} className="strategy-playbook-entry">
                  <div className="strategy-playbook-entry-head">
                    <strong>{entry.name}</strong>
                    <StagePill label={entry.disposition} tone={entry.tone} />
                  </div>
                  <p>{entry.parameters}</p>
                </article>
              ))}
            </div>
          </section>
        </div>
      </details>
    </Surface>
  )
}

function HistoricalScreenerTableRow({
  row,
  active,
  onSelect,
}: {
  row: ScannerDisplayRow
  active: boolean
  onSelect: (symbol: string) => void
}) {
  const triggerLabel = row.trigger_price && row.trigger_price > 0
    ? `${currency(row.trigger_price)}${row.close >= row.trigger_price ? ' passed' : ''}`
    : 'N/A'
  const triggerSource = row.trigger_source || ''

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
      <td>{row.score}</td>
      <td>{currency(row.close)}</td>
      <td className="trigger-cell">
        <span>{triggerLabel}</span>
        {triggerSource && <small>{triggerSource}</small>}
      </td>
      <td>{currency(row.stop_loss)}</td>
      <td>{currency(row.target_price)}</td>
      <td>{row.volume_ratio > 0 ? `${row.volume_ratio.toFixed(2)}x` : row.avg_volume20.toLocaleString('en-IN')}</td>
      <td><ScannerResearchCell confluence={row.confluence} score={row.score} /></td>
      <td><ScannerNewsCell news={row.confluence?.news} /></td>
    </tr>
  )
}

const SCORE_BREAKDOWN_FIELDS: Array<{ keys: Array<keyof ConfluenceScoreBreakdown>; label: string; adjustment?: boolean }> = [
  { keys: ['model_score', 'base_score'], label: 'Model score' },
  { keys: ['technical_quality', 'technical'], label: 'Technical' },
  { keys: ['trend_quality', 'trend'], label: 'Trend' },
  { keys: ['volume_quality', 'volume'], label: 'Volume' },
  { keys: ['regime_quality', 'regime'], label: 'Regime' },
  { keys: ['risk_reward_quality', 'risk_reward'], label: 'Risk / reward' },
  { keys: ['catalyst_adjustment'], label: 'News catalyst', adjustment: true },
  { keys: ['total_score'], label: 'Research score' },
]

function formatConfluenceScore(value: number, adjustment = false) {
  if (adjustment) return `${value >= 0 ? '+' : ''}${value.toFixed(1)}`
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function ResearchVerdictPanel({ candidate }: { candidate: SwingCandidate }) {
  const confluence = candidate.confluence
  const breakdown = confluence?.score_breakdown
  const scoreMetrics = SCORE_BREAKDOWN_FIELDS
    .map((field) => ({
      ...field,
      value: field.keys
        .map((key) => asFiniteNumber(breakdown?.[key]))
        .find((value): value is number => value !== null) ?? null,
    }))
    .filter((field): field is { keys: Array<keyof ConfluenceScoreBreakdown>; label: string; adjustment?: boolean; value: number } => field.value !== null)
  const strategyMatches = confluence?.strategy_matches ?? []
  const selectedMatches = strategyMatches.filter((match) => match.selected)
  const researchScore = asFiniteNumber(confluence?.research_score) ?? asFiniteNumber(breakdown?.total_score) ?? candidate.score
  const state = confluenceTradeState(confluence)
  const hasConfluence = Boolean(confluence)

  return (
    <Surface className="inner-surface research-verdict-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Research Verdict</span>
          <h3>{hasConfluence ? readableLabel(state, 'Researching') : 'Strategy score only'}</h3>
        </div>
        <StagePill label={hasConfluence ? readableLabel(state, 'Researching') : 'Confluence Pending'} tone={researchTone(confluence, candidate.score)} />
      </div>

      <p className="research-verdict-copy">
        {hasConfluence
          ? `${confluencePillarCount(confluence)} independent research pillar${confluencePillarCount(confluence) === 1 ? '' : 's'} are represented in this snapshot. Price and risk rules remain the entry gate.`
          : 'This snapshot has not supplied structured confluence yet. The score and trade state shown above still come from the original strategy feed.'}
      </p>

      <div className="research-verdict-stats">
        <CandidateStat label="Research Score" value={`${formatConfluenceScore(researchScore)}/100`} tone={researchTone(confluence, candidate.score)} />
        <CandidateStat label="Strategy Matches" value={String(confluenceStrategyCount(confluence))} tone={confluenceStrategyCount(confluence) > 1 ? 'positive' : 'neutral'} />
        <CandidateStat label="Selected Models" value={String(selectedMatches.length)} tone={selectedMatches.length > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="News Read" value={confluence?.news ? readableLabel(confluence.news.direction, 'Mixed') : 'Not linked'} tone={newsTone(confluence?.news)} />
      </div>

      {scoreMetrics.length > 0 && (
        <div className="score-breakdown-grid" aria-label="Research score breakdown">
          {scoreMetrics.map((metric) => (
            <div key={metric.keys.join('-')} className={metric.keys.includes('total_score') ? 'score-breakdown-item score-breakdown-total' : 'score-breakdown-item'}>
              <span>{metric.label}</span>
              <strong className={metric.adjustment && metric.value < 0 ? 'tone-danger' : metric.adjustment && metric.value > 0 ? 'tone-positive' : ''}>
                {formatConfluenceScore(metric.value, metric.adjustment)}
              </strong>
            </div>
          ))}
        </div>
      )}

      {strategyMatches.length > 0 && (
        <div className="strategy-match-list">
          <span className="micro-label">Strategy agreement</span>
          <div className="strategy-match-chips">
            {strategyMatches.map((match, index) => {
              const label = match.strategy_label || match.setup_family || match.strategy_id || 'Strategy match'
              const detail = match.signal_label || match.signal_status || match.strategy_status || match.status || match.reason
              return (
                <span key={`${label}-${index}`} className={match.selected ? 'strategy-match-chip strategy-match-selected' : 'strategy-match-chip'}>
                  <strong>{label}</strong>
                  {(match.timeframe || detail) && <small>{[match.timeframe, detail].filter(Boolean).join(' · ')}</small>}
                </span>
              )
            })}
          </div>
        </div>
      )}
    </Surface>
  )
}

function ResearchEvidencePanels({ confluence }: { confluence: ResearchConfluence | undefined }) {
  const evidence = confluence?.evidence ?? []
  const risks = confluence?.risks ?? []
  if (evidence.length === 0 && risks.length === 0) return null

  const renderFinding = (finding: string | ConfluenceFinding, index: number) => {
    const text = confluenceFindingText(finding)
    const href = confluenceFindingUrl(finding)
    return (
      <li key={`${text}-${index}`}>
        {href ? (
          <a href={href} target="_blank" rel="noreferrer" className="research-source-link">
            <span>{text}</span>
            <ExternalLink size={13} />
          </a>
        ) : text}
      </li>
    )
  }

  return (
    <div className="split-grid research-evidence-grid">
      {evidence.length > 0 && (
        <Surface className="inner-surface">
          <span className="eyebrow">Research Evidence</span>
          <ul className="detail-list">
            {evidence.map(renderFinding)}
          </ul>
        </Surface>
      )}
      {risks.length > 0 && (
        <Surface className="inner-surface">
          <span className="eyebrow">Conflicting Evidence</span>
          <ul className="detail-list warning-list">
            {risks.map(renderFinding)}
          </ul>
        </Surface>
      )}
    </div>
  )
}

function ResearchNewsPanel({ news }: { news: ConfluenceNewsSummary | undefined }) {
  if (!news) return null
  const articles = confluenceNewsArticles(news)
  const scoreAdjustment = asFiniteNumber(news.score_adjustment)
  const lookbackHours = asFiniteNumber(news.lookback_hours)
  const sentiment = asFiniteNumber(news.average_sentiment) ?? asFiniteNumber(news.avg_sentiment)
  const impact = asFiniteNumber(news.max_impact)

  return (
    <Surface className={`inner-surface research-news-panel tone-${newsTone(news)}`}>
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Catalyst & News</span>
          <h3>{readableLabel(news.direction, 'Mixed evidence')}</h3>
        </div>
        <StagePill label={newsSummaryLabel(news)} tone={newsTone(news)} />
      </div>

      {news.latest_reason && <p className="research-news-reason">{news.latest_reason}</p>}

      <div className="research-news-stats">
        <CandidateStat label="Lookback" value={lookbackHours === null ? 'N/A' : `${lookbackHours}h`} />
        <CandidateStat label="Bullish" value={String(asFiniteNumber(news.bullish_articles) ?? 0)} tone="positive" />
        <CandidateStat label="Bearish" value={String(asFiniteNumber(news.bearish_articles) ?? 0)} tone={(asFiniteNumber(news.bearish_articles) ?? 0) > 0 ? 'danger' : 'neutral'} />
        <CandidateStat label="Score effect" value={scoreAdjustment === null ? 'N/A' : formatConfluenceScore(scoreAdjustment, true)} tone={scoreAdjustment !== null && scoreAdjustment < 0 ? 'danger' : scoreAdjustment !== null && scoreAdjustment > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="Sentiment" value={sentiment === null ? 'N/A' : `${sentiment >= 0 ? '+' : ''}${sentiment.toFixed(2)}`} tone={sentiment !== null && sentiment < 0 ? 'danger' : sentiment !== null && sentiment > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="Max impact" value={impact === null ? 'N/A' : `${impact.toFixed(1)}/5`} />
      </div>

      {articles.length > 0 ? (
        <div className="research-news-links">
          <span className="micro-label">Source articles</span>
          {articles.map((article, index) => {
            const title = article.title || article.reason || article.summary || 'Open linked news source'
            const href = article.source_url || article.url
            return href ? (
              <a key={`${title}-${index}`} href={href} target="_blank" rel="noreferrer" className="research-news-link">
                <span>
                  <strong>{title}</strong>
                  {(article.source || article.published_at) && <small>{[article.source, article.published_at ? compactDate(article.published_at) : ''].filter(Boolean).join(' · ')}</small>}
                </span>
                <ExternalLink size={14} />
              </a>
            ) : (
              <div key={`${title}-${index}`} className="research-news-link research-news-link-static">
                <span>
                  <strong>{title}</strong>
                  {(article.source || article.published_at) && <small>{[article.source, article.published_at ? compactDate(article.published_at) : ''].filter(Boolean).join(' · ')}</small>}
                </span>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="research-news-unlinked">News counts are included in this research snapshot, but no source article links were supplied.</p>
      )}
    </Surface>
  )
}

function DetailPanel({
  candidate,
  history,
  historyRange,
  watchlisted,
  queued,
  onWatch,
  onQueue,
  onHistoryRangeChange,
}: {
  candidate: SwingCandidate | null
  history: SymbolHistoryResponse | null
  historyRange: HistoryRange
  watchlisted: boolean
  queued: boolean
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
  onHistoryRangeChange: (range: HistoryRange) => void
}) {
  const resolvedCandidate = candidate

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

      <ResearchVerdictPanel candidate={resolvedCandidate} />

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

      <ResearchEvidencePanels confluence={resolvedCandidate.confluence} />
      <ResearchNewsPanel news={resolvedCandidate.confluence?.news} />

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
  const resolvedCandidate = candidate

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
  const resolvedCandidate = candidate

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
  liveSnapshot,
  liveSocketState,
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
  liveSnapshot: LiveStrategySnapshot | null
  liveSocketState: 'connecting' | 'open' | 'closed' | 'error'
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
  const liveCandidates = useMemo(
    () => (liveSnapshot?.rows ?? [])
      .filter((row) => row.source === 'dhan-live')
      .map(createCandidateFromLiveRow),
    [liveSnapshot],
  )
  const httpLiveCandidates = useMemo(
    () => (home?.top_candidates ?? []).filter((candidate) => candidate.source === 'dhan-live'),
    [home],
  )
  const displayHome = home ?? (liveSnapshot
    ? {
        updated_at: liveSnapshot.updated_at,
        broker: liveSnapshot.broker,
        market_regime: liveSnapshot.market_regime,
        top_candidates: liveCandidates,
        scanner_count: liveSnapshot.rows.length,
        setup_mix: setupMixFromCandidates(liveCandidates),
      }
    : null)

  if (!displayHome) {
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

  const topCandidates = liveCandidates.length > 0 ? liveCandidates : httpLiveCandidates
  const marketRegime = liveSnapshot?.market_regime ?? displayHome.market_regime
  const scannerCount = liveSnapshot?.rows.length ?? topCandidates.length
  const setupMix = liveCandidates.length > 0 ? setupMixFromCandidates(liveCandidates) : setupMixFromCandidates(topCandidates)
  const entryNowCount = topCandidates.filter((candidate) => candidate.live_signal.status === 'ENTRY_NOW').length
  const strategies = ['All', ...Array.from(new Set(topCandidates.map((candidate) => candidate.live_signal.strategy_label || candidate.setup_family)))]
  const filteredTopPicks = topCandidates
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
  const topStrategy = setupMix[0]?.family ?? strategies.find((strategy) => strategy !== 'All') ?? 'No active strategy'
  const socketTone: Tone = liveSocketState === 'open' ? 'positive' : liveSocketState === 'connecting' ? 'warning' : 'danger'

  return (
    <div className="home-dashboard">
      <Surface className="home-market-panel">
        <div className="home-market-copy">
          <span className="eyebrow">Live Market Pulse</span>
          <h2>{marketRegime.label}</h2>
          <p>{marketRegime.summary}</p>
        </div>
        <div className="home-kpi-grid">
          <CandidateStat label="WebSocket" value={liveSocketState} tone={socketTone} />
          <CandidateStat label="Adv / Dec" value={`${marketRegime.advances} / ${marketRegime.declines}`} />
          <CandidateStat label="Breadth" value={marketRegime.breadth_ratio.toFixed(2)} tone={marketRegime.breadth_ratio >= 1 ? 'positive' : 'warning'} />
          <CandidateStat label="Live Rows" value={String(scannerCount)} tone={scannerCount > 0 ? 'positive' : 'warning'} />
          <CandidateStat label="Top Strategy" value={topStrategy} tone="positive" />
          <CandidateStat label="Enter Now" value={String(entryNowCount)} tone={entryNowCount > 0 ? 'positive' : 'warning'} />
        </div>
      </Surface>

      <div className="market-ribbon compact-market-ribbon">
        {topCandidates.slice(0, 5).map((candidate) => (
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
        {topCandidates.length === 0 && (
          <div className="empty-table live-empty-row">
            <Activity size={18} />
            <p>No live strategy recommendations are being shown until Dhan quote data arrives.</p>
          </div>
        )}
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
            {filteredTopPicks.length === 0 && (
              <div className="empty-table">
                <Activity size={18} />
                <p>Waiting for live strategy rows from the websocket. Historical backtest rows stay in the Backtest view.</p>
              </div>
            )}
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
              {setupMix.slice(0, 4).map((mix) => (
                <div key={mix.family} className="setup-mix-row">
                  <span>{mix.family}</span>
                  <strong>{mix.count}</strong>
                  <em>{mix.avg_score.toFixed(1)}</em>
                </div>
              ))}
              {setupMix.length === 0 && <p className="settings-copy">No live setup mix yet.</p>}
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
  liveSnapshot,
  liveSocketState,
  liveAlertsEnabled,
  selectedSymbol,
  onSelect,
  onStageFresh,
  onRefreshCache,
  onReload,
  onEnableLiveAlerts,
  loading,
  loadError,
  stagingFresh,
  refreshingCache,
}: {
  scanner: SwingScannerResponse | null
  historicalScreener: HistoricalScreenerResponse | null
  freshSignals: FreshSignalsResponse | null
  bambooLatest: BambooLatestResponse | null
  liveSnapshot: LiveStrategySnapshot | null
  liveSocketState: 'connecting' | 'open' | 'closed' | 'error'
  liveAlertsEnabled: boolean
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
  onStageFresh: () => void
  onRefreshCache: () => void
  onReload: () => void
  onEnableLiveAlerts: () => void
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
  const liveRows = useMemo(
    () => (liveSnapshot?.rows ?? [])
      .filter((row) => row.source === 'dhan-live')
      .map(createHistoricalRowFromLiveRow),
    [liveSnapshot],
  )
  const sourceRows = useMemo(
    () => liveRows.length > 0
      ? liveRows
      : (scanner?.candidates ?? []).map(createHistoricalRowFromCandidate),
    [liveRows, scanner],
  )
  const committeePicks = useMemo(() => buildResearchCommitteePicks(sourceRows), [sourceRows])

  const families = useMemo(() => {
    const options = new Set<string>(['All'])
    sourceRows.forEach((row) => options.add(row.setup_family))
    return Array.from(options)
  }, [sourceRows])

  const strategies = useMemo(() => {
    const options = new Map<string, string>([['All', 'All Strategies']])
    sourceRows.forEach((row) => {
      options.set(row.strategy_id, row.strategy_label)
    })
    return Array.from(options.entries()).map(([id, label]) => ({ id, label }))
  }, [sourceRows])

  const filtered = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase()
    return sourceRows.filter((row) => {
      const matchesFamily = familyFilter === 'All' || row.setup_family === familyFilter
      const matchesStrategy = strategyFilter === 'All' || row.strategy_id === strategyFilter
      const matchesSearch =
        !term ||
        row.symbol.toLowerCase().includes(term)
        || row.strategy_label.toLowerCase().includes(term)
        || row.strategy_status.toLowerCase().includes(term)
        || readableLabel(confluenceTradeState(row.confluence), '').toLowerCase().includes(term)
        || (row.confluence?.strategy_matches ?? []).some((match) => (
          `${match.strategy_label ?? ''} ${match.setup_family ?? ''} ${match.strategy_id ?? ''}`.toLowerCase().includes(term)
        ))
      return matchesFamily && matchesStrategy && matchesSearch
    })
  }, [deferredSearch, familyFilter, sourceRows, strategyFilter])

  useEffect(() => {
    setPage(1)
  }, [deferredSearch, familyFilter, strategyFilter])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)
  const groupedRows = useMemo(() => {
    const groups = new Map<string, ScannerDisplayRow[]>()
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

  if (!scanner && !liveSnapshot) {
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
  const latestSignalDate = liveSnapshot?.updated_at ?? freshSignals?.signal_date ?? historicalScreener?.signal_date ?? historicalScreener?.rows[0]?.as_of ?? 'not available'
  const historicalSignalDate = historicalScreener?.signal_date ?? historicalScreener?.rows[0]?.as_of ?? ''
  const historicalDataStale = marketDateAgeDays(historicalSignalDate) > 4
  const liveFeedLabel = liveSnapshot?.feed_status === 'streaming'
    ? 'Dhan websocket live'
    : liveRows.length > 0
      ? `Live rows via ${liveSnapshot?.mode ?? 'websocket'}`
      : sourceRows.length > 0
        ? 'HTTP research snapshot'
        : 'Waiting for live quote rows'
  const uniqueConfluenceRows = Array.from(new Map(
    sourceRows
      .filter((row) => row.confluence)
      .map((row) => [row.symbol, row]),
  ).values())
  const multiStrategyCount = uniqueConfluenceRows.filter((row) => confluenceStrategyCount(row.confluence) > 1).length
  const catalystSupportedCount = uniqueConfluenceRows.filter((row) => newsTone(row.confluence?.news) === 'positive').length
  const newsConflictCount = uniqueConfluenceRows.filter((row) => newsTone(row.confluence?.news) === 'danger').length

  return (
    <div className="page-stack">
      <ResearchCommitteePanel
        picks={committeePicks}
        selectedSymbol={selectedSymbol}
        onSelect={onSelect}
      />

      <StrategyPlaybookPanel />

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
            <span className="eyebrow">Raw Strategy Research</span>
              <h2>All individual model signals from {latestSignalDate}</h2>
          </div>
          <div className="toolbar-right screener-toolbar-meta">
            <div className="mini-chip">
              <Database size={14} />
              <span>{liveRows.length} live strategy rows</span>
            </div>
            <div className="mini-chip">
              <Activity size={14} />
              <span>{liveFeedLabel}</span>
            </div>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="text-input"
              placeholder="Search symbol"
            />
            <button type="button" className="primary-button" onClick={onStageFresh} disabled={stagingFresh || historicalDataStale}>
              <RefreshCw size={14} className={stagingFresh ? 'spin' : ''} />
              <span>{stagingFresh ? 'Staging' : historicalDataStale ? 'Refresh Data First' : 'Stage Fresh Signals'}</span>
            </button>
            <button type="button" className="ghost-button" onClick={onEnableLiveAlerts} disabled={liveAlertsEnabled}>
              <BellRing size={14} />
              <span>{liveAlertsEnabled ? 'Alerts On' : 'Enable Alerts'}</span>
            </button>
            <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache}>
              <Database size={14} />
              <span>{refreshingCache ? 'Caching' : 'Refresh Cache'}</span>
            </button>
          </div>
        </div>

        {historicalDataStale && (
          <div className="warning-banner">
            Entry safety gate: latest parquet signal date is {historicalSignalDate || 'unavailable'}. Historical rows remain visible for study, but paper staging is disabled until the feature data is current.
          </div>
        )}

        <div className="scanner-summary-strip">
          <CandidateStat label="New Signals" value={String(freshSignalCount)} tone={freshSignalCount > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Auto-Staged" value={String(freshSignals?.staged_rows ?? 0)} tone={(freshSignals?.staged_rows ?? 0) > 0 ? 'positive' : 'neutral'} />
          <CandidateStat label="Already Seen" value={String(freshSignals?.seen_rows ?? 0)} tone={(freshSignals?.seen_rows ?? 0) > 0 ? 'warning' : 'neutral'} />
          <CandidateStat label="Live Updated" value={compactDate(latestSignalDate)} />
        </div>

        <div className="research-radar-strip">
          <div>
            <span className="eyebrow">Research Radar</span>
            <p>Agreement is shown across independent pillars; news supports or challenges a setup but never creates an entry by itself.</p>
          </div>
          <div className="research-radar-stats">
            <CandidateStat label="Confluence" value={`${uniqueConfluenceRows.length}/${new Set(sourceRows.map((row) => row.symbol)).size}`} tone={uniqueConfluenceRows.length > 0 ? 'positive' : 'neutral'} />
            <CandidateStat label="Multi-Strategy" value={String(multiStrategyCount)} tone={multiStrategyCount > 0 ? 'positive' : 'neutral'} />
            <CandidateStat label="Catalyst Support" value={String(catalystSupportedCount)} tone={catalystSupportedCount > 0 ? 'positive' : 'neutral'} />
            <CandidateStat label="News Warnings" value={String(newsConflictCount)} tone={newsConflictCount > 0 ? 'danger' : 'neutral'} />
          </div>
        </div>

        <LiveStrategyTape snapshot={liveSnapshot} socketState={liveSocketState} selectedStrategy={strategyFilter} onSelect={onSelect} />

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
                          <th>Score</th>
                          <th>Live LTP</th>
                          <th>Trigger</th>
                          <th>Stop</th>
                          <th>Target</th>
                          <th>Volume</th>
                          <th>Research</th>
                          <th>News</th>
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
                  <p>{liveSnapshot?.message ?? historicalScreener?.message ?? 'No live strategy signals matched the current filters.'}</p>
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
                  <th>Score</th>
                  <th>Live LTP</th>
                  <th>Trigger</th>
                  <th>Stop</th>
                  <th>Target</th>
                  <th>Volume</th>
                  <th>Research</th>
                  <th>News</th>
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
                <p>{liveSnapshot?.message ?? freshSignals?.message ?? 'No live strategy rows matched the current filters.'}</p>
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

const STRATEGY_LABELS: Record<string, string> = {
  'cash-portfolio-all': 'Cash-Constrained Portfolio',
  'tuned-panic-reversal-v1': 'Panic Reversal Lab',
  'weekly-supertrend-10-3': 'Weekly Supertrend 10-3',
  'king-candle-supertrend-breakout-v1': 'King Candle Supertrend Breakout',
  'king-candle-quality-v1': 'King Candle Quality',
}

function strategyLabel(strategyId: string) {
  const fixedLabel = STRATEGY_LABELS[strategyId]
  if (fixedLabel) return fixedLabel
  return strategyId
    .replace('-v1', '')
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function pct(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function metricValue(row: PythonBacktestMetricRow | undefined, key: string) {
  const value = row?.[key]
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : 0
  }
  return 0
}

function metricLabel(row: PythonBacktestMetricRow | undefined, key: string) {
  const value = row?.[key]
  if (value === null || value === undefined || value === '') return 'N/A'
  return String(value)
}

function formatMetric(row: PythonBacktestMetricRow | undefined, key: string, suffix = '') {
  const value = metricValue(row, key)
  return `${value.toLocaleString('en-IN', { maximumFractionDigits: 3 })}${suffix}`
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function LiveStrategyTape({
  snapshot,
  socketState,
  selectedStrategy,
  onSelect,
}: {
  snapshot: LiveStrategySnapshot | null
  socketState: 'connecting' | 'open' | 'closed' | 'error'
  selectedStrategy: string
  onSelect: (symbol: string) => void
}) {
  const rows = useMemo(() => {
    const source = (snapshot?.rows ?? []).filter((row) => row.source === 'dhan-live')
    return (selectedStrategy === 'All'
      ? source
      : source.filter((row) => row.strategy_id === selectedStrategy)
    ).slice(0, 10)
  }, [selectedStrategy, snapshot])
  const stateTone: Tone = socketState === 'open' ? 'positive' : socketState === 'connecting' ? 'warning' : 'danger'
  const feedTone: Tone = snapshot?.feed_status === 'streaming' ? 'positive' : snapshot?.feed_status === 'connecting' ? 'warning' : snapshot ? 'danger' : stateTone

  return (
    <Surface className="inner-surface live-strategy-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Live Strategy Feed</span>
          <h2>Today onward websocket signals</h2>
        </div>
        <div className="hero-actions">
          <StagePill label={socketState} tone={stateTone} />
          <StagePill label={snapshot?.feed_status ?? 'waiting'} tone={feedTone} />
          <div className="mini-chip">
            <Activity size={14} />
            <span>{snapshot ? compactDate(snapshot.updated_at) : 'Connecting'}</span>
          </div>
        </div>
      </div>

      <div className="live-strategy-kpis">
        <CandidateStat label="Mode" value={snapshot?.mode ?? 'websocket'} tone={snapshot?.mode === 'dhan-websocket' ? 'positive' : 'warning'} />
        <CandidateStat label="Watching" value={(snapshot?.total_watching ?? 0).toLocaleString('en-IN')} />
        <CandidateStat label="Entry Now" value={(snapshot?.triggered ?? 0).toLocaleString('en-IN')} tone={(snapshot?.triggered ?? 0) > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="Broker" value={snapshot?.broker.state ?? 'checking'} tone={snapshot?.broker.state === 'ready' ? 'positive' : 'warning'} />
      </div>

      {snapshot?.message && (
        <div className="backtest-run-note live-strategy-note">
          <CircleAlert size={16} />
          <span>{snapshot.message}</span>
        </div>
      )}

      <div className="live-strategy-table">
        <div className="live-strategy-row live-strategy-head">
          <span>Stock</span>
          <span>Strategy</span>
          <span>Status</span>
          <span>LTP</span>
          <span>Day</span>
          <span>Stop</span>
          <span>Target</span>
          <span>Score</span>
          <span>Research</span>
          <span>Catalyst</span>
        </div>
        {rows.map((row) => (
          <button key={`${row.strategy_id}-${row.symbol}`} type="button" className="live-strategy-row live-strategy-button" onClick={() => onSelect(row.symbol)}>
            <strong>{row.symbol}<small>{row.company_name}</small></strong>
            <span>{row.strategy_label}<small>{row.setup_family}</small></span>
            <span className={`watch-signal-pill signal-${row.signal_status.toLowerCase().replace(/_/g, '-')}`}>{row.signal_label}</span>
            <strong>{currency(row.last_price)}</strong>
            <span className={row.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.day_change_pct)}</span>
            <span>{currency(row.stop_loss)}</span>
            <span>{currency(row.target_price)}</span>
            <strong>{row.score}</strong>
            <ScannerResearchCell confluence={row.confluence} score={row.score} />
            <ScannerNewsCell news={row.confluence?.news} />
          </button>
        ))}
      </div>

      {rows.length === 0 && (
        <div className="empty-table">
          <Activity size={18} />
          <p>{snapshot ? 'No live strategy rows match this filter yet.' : 'Connecting to the live strategy websocket.'}</p>
        </div>
      )}
    </Surface>
  )
}

function PythonBacktestLabPanel({
  lab,
  running,
  loading,
  onLoad,
  onRun,
}: {
  lab: PythonBacktestLabResponse | null
  running: boolean
  loading: boolean
  onLoad: () => void
  onRun: () => void
}) {
  const payload = lab?.payload
  const maBest = payload?.best.ma[0]
  const panicBest = payload?.best.panic[0]
  const predictions = payload?.predictions ?? []
  const yearlyRows: Array<PythonBacktestPeriodRow & { label: string }> = [
    ...(payload?.period_returns.ma_yearly ?? []).map((row) => ({ ...row, label: 'MA Breakout' })),
    ...(payload?.period_returns.panic_yearly ?? []).map((row) => ({ ...row, label: 'Panic Reversal' })),
  ].sort((a, b) => b.year - a.year || a.label.localeCompare(b.label))
  const monthlyRows: Array<PythonBacktestPeriodRow & { label: string }> = [
    ...(payload?.period_returns.ma_monthly ?? []).map((row) => ({ ...row, label: 'MA' })),
    ...(payload?.period_returns.panic_monthly ?? []).map((row) => ({ ...row, label: 'Panic' })),
  ].slice(-18)
  const chartNames = [
    { title: 'MA Equity', name: 'ma_top_equity_curves.png' },
    { title: 'MA Monthly', name: 'ma_best_monthly_heatmap.png' },
    { title: 'MA Yearly', name: 'ma_best_yearly_returns.png' },
    { title: 'Panic Equity', name: 'panic_top_equity_curves.png' },
    { title: 'Panic Monthly', name: 'panic_best_monthly_heatmap.png' },
    { title: 'Panic Yearly', name: 'panic_best_yearly_returns.png' },
  ]

  return (
    <Surface className="inner-surface python-lab-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">CSV Strategy Files</span>
          <h2>MA breakout and panic reversal results</h2>
        </div>
        <div className="hero-actions">
          <button type="button" className="ghost-button" onClick={onLoad} disabled={loading || running}>
            <Database size={14} className={loading ? 'spin' : ''} />
            <span>{loading ? 'Loading Lab' : 'Load Results'}</span>
          </button>
          <button type="button" className="primary-button" onClick={onRun} disabled={running}>
            <RefreshCw size={14} className={running ? 'spin' : ''} />
            <span>{running ? 'Running Python' : 'Run Python/NumPy Lab'}</span>
          </button>
        </div>
      </div>

      {lab ? (
        <>
          <div className="backtest-run-note python-lab-status">
            <Activity size={16} />
            <span>{lab.message}</span>
          </div>

          <div className="python-lab-best-grid">
            <div className="python-best-card">
              <span className="eyebrow">Best Moving Average Variant</span>
              <h3>{metricLabel(maBest, 'name')}</h3>
              <div className="python-metric-grid">
                <CandidateStat label="Score" value={formatMetric(maBest, 'score')} />
                <CandidateStat label="Full Trades" value={formatMetric(maBest, 'full_trades')} />
                <CandidateStat label="Full Exp" value={formatMetric(maBest, 'full_expectancy_pct', '%')} tone={metricValue(maBest, 'full_expectancy_pct') >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="OOS Exp" value={formatMetric(maBest, 'oos_expectancy_pct', '%')} tone={metricValue(maBest, 'oos_expectancy_pct') >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="OOS PF" value={formatMetric(maBest, 'oos_profit_factor')} tone={metricValue(maBest, 'oos_profit_factor') >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="OOS Trades" value={formatMetric(maBest, 'oos_trades')} />
              </div>
            </div>
            <div className="python-best-card">
              <span className="eyebrow">Best Panic Reversal Variant</span>
              <h3>{metricLabel(panicBest, 'name')}</h3>
              <div className="python-metric-grid">
                <CandidateStat label="Score" value={formatMetric(panicBest, 'score')} />
                <CandidateStat label="Full Trades" value={formatMetric(panicBest, 'full_trades')} />
                <CandidateStat label="Full Exp" value={formatMetric(panicBest, 'full_expectancy_pct', '%')} tone={metricValue(panicBest, 'full_expectancy_pct') >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="OOS Exp" value={formatMetric(panicBest, 'oos_expectancy_pct', '%')} tone={metricValue(panicBest, 'oos_expectancy_pct') >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="OOS PF" value={formatMetric(panicBest, 'oos_profit_factor')} tone={metricValue(panicBest, 'oos_profit_factor') >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="OOS Trades" value={formatMetric(panicBest, 'oos_trades')} />
              </div>
            </div>
          </div>

          <div className="python-chart-grid">
            {chartNames.map((chart) => (
              <figure key={chart.name} className="python-chart-card">
                <img src={pythonBacktestChartUrl(chart.name)} alt={chart.title} loading="lazy" />
                <figcaption>{chart.title}</figcaption>
              </figure>
            ))}
          </div>

          <div className="python-table-grid">
            <div className="python-data-table">
              <div className="python-data-row python-data-head">
                <span>Strategy</span>
                <span>Year</span>
                <span>Trades</span>
                <span>Win</span>
                <span>Return</span>
              </div>
              {yearlyRows.slice(0, 16).map((row) => (
                <div key={`${row.label}-${row.year}`} className="python-data-row">
                  <strong>{row.label}</strong>
                  <span>{row.year}</span>
                  <span>{row.trades.toLocaleString('en-IN')}</span>
                  <span>{row.win_rate.toFixed(2)}%</span>
                  <strong className={row.return_proxy_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.return_proxy_pct)}</strong>
                </div>
              ))}
            </div>

            <div className="python-data-table">
              <div className="python-data-row python-data-head">
                <span>Family</span>
                <span>Month</span>
                <span>Trades</span>
                <span>Win</span>
                <span>Return</span>
              </div>
              {monthlyRows.map((row) => (
                <div key={`${row.label}-${row.month_label}`} className="python-data-row">
                  <strong>{row.label}</strong>
                  <span>{row.month_label ?? `${row.year}-${row.month}`}</span>
                  <span>{row.trades.toLocaleString('en-IN')}</span>
                  <span>{row.win_rate.toFixed(2)}%</span>
                  <strong className={row.return_proxy_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.return_proxy_pct)}</strong>
                </div>
              ))}
            </div>
          </div>

          <div className="python-signal-table">
            <div className="python-signal-row python-data-head">
              <span>Signal</span>
              <span>Strategy</span>
              <span>Entry</span>
              <span>Stop</span>
              <span>Target</span>
              <span>Score</span>
            </div>
            {predictions.slice(0, 18).map((signal) => (
              <div key={`${signal.strategy_family}-${signal.symbol}-${signal.signal_date}`} className="python-signal-row">
                <strong>{signal.symbol}<small>{signal.signal_date}</small></strong>
                <span>{signal.strategy_family.replace(/_/g, ' ')}<small>{signal.reason}</small></span>
                <span>{currency(signal.entry)}</span>
                <span>{currency(signal.stop)}</span>
                <span>{currency(signal.target)}</span>
                <strong>{signal.score.toFixed(2)}</strong>
              </div>
            ))}
            {predictions.length === 0 && (
              <div className="python-signal-row">
                <span>No fresh Python signals in the latest window.</span>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="empty-action-panel">
          <Sparkles size={24} />
          <div>
            <h2>No Python lab results loaded</h2>
            <p>Run the optimizer to generate tuned MA breakout and panic reversal results, signal predictions, and chart artifacts.</p>
          </div>
        </div>
      )}
    </Surface>
  )
}

function BacktestsView({
  dashboard,
  cache,
  pythonLab,
  running,
  pythonRunning,
  loading,
  pythonLoading,
  refreshingCache,
  onLoad,
  onLoadPythonLab,
  onRefreshCache,
  onRun,
  onRunPythonLab,
}: {
  dashboard: BacktestDashboardResponse | null
  cache: BacktestCacheStatus | null
  pythonLab: PythonBacktestLabResponse | null
  running: boolean
  pythonRunning: boolean
  loading: boolean
  pythonLoading: boolean
  refreshingCache: boolean
  onLoad: () => void
  onLoadPythonLab: () => void
  onRefreshCache: () => void
  onRun: () => void
  onRunPythonLab: () => void
}) {
  const [selectedStrategy, setSelectedStrategy] = useState<string>('near-52w-high-v1')
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [familyFilter, setFamilyFilter] = useState('all')
  const [backtestSort, setBacktestSort] = useState('stability')
  const [backtestSearch, setBacktestSearch] = useState('')
  const [backtestMode] = useState<'datewise' | 'strategy'>('strategy')
  const [datewise, setDatewise] = useState<BacktestDatewiseResponse | null>(null)
  const [datewiseDate, setDatewiseDate] = useState('')
  const [datewiseStrategy, setDatewiseStrategy] = useState('all')
  const [datewisePage, setDatewisePage] = useState(1)
  const [datewisePageSize, setDatewisePageSize] = useState(25)
  const [loadingDatewise, setLoadingDatewise] = useState(false)

  useEffect(() => {
    if (!dashboard || backtestMode !== 'datewise') return
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
  }, [backtestMode, dashboard, datewiseDate, datewisePage, datewisePageSize, datewiseStrategy])

  const methodFamilies = useMemo(() => {
    if (!dashboard) return []
    const eligibleStrategyIds = new Set(
      dashboard.summaries
        .filter((summary) => summary.total_trades >= MIN_BACKTEST_STRATEGY_TRADES)
        .map((summary) => summary.strategy_id),
    )
    return Array.from(new Set(
      dashboard.diagnostics
        .filter((row) => eligibleStrategyIds.has(row.strategy_id))
        .map((row) => row.method_family),
    )).sort()
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
        return summary.total_trades >= MIN_BACKTEST_STRATEGY_TRADES && matchesStatus && matchesFamily && matchesSearch
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
          <PythonBacktestLabPanel
            lab={pythonLab}
            running={pythonRunning}
            loading={pythonLoading}
            onLoad={onLoadPythonLab}
            onRun={onRunPythonLab}
          />
        </Surface>
      </div>
    )
  }

  const selectedSummary =
    visibleSummaries.find((summary) => summary.strategy_id === selectedStrategy) ??
    visibleSummaries[0] ??
    null
  const activeStrategyId = selectedSummary?.strategy_id ?? selectedStrategy
  const selectedDiagnostic = dashboard.diagnostics.find((row) => row.strategy_id === activeStrategyId)
  const yearlyRows = dashboard.yearly_returns.filter((row) => row.strategy_id === activeStrategyId)
  const selectedYearExists = selectedYear !== null && yearlyRows.some((row) => row.year === selectedYear)
  const activeYear = selectedYearExists ? selectedYear : yearlyRows[yearlyRows.length - 1]?.year ?? null
  const monthlyRows = dashboard.monthly_returns.filter((row) => row.strategy_id === activeStrategyId)
  const selectedYearMonths = activeYear ? monthlyRows.filter((row) => row.year === activeYear) : []
  const selectedEquity = dashboard.equity_curve.filter((row) => row.strategy_id === activeStrategyId)
  const cashProfiles = dashboard.cash_profiles ?? []
  const cashPortfolio = cashProfiles.find((row) => row.strategy_id === 'cash-portfolio-all')
  const selectedCashProfile = cashProfiles.find((row) => row.strategy_id === activeStrategyId)
  const selectedCashEquity = (dashboard.cash_equity_curve ?? []).filter((row) => row.strategy_id === activeStrategyId)
  const selectedCashMonths = activeYear
    ? (dashboard.cash_monthly_returns ?? []).filter((row) => row.strategy_id === activeStrategyId && row.year === activeYear)
    : []
  const dayQuality = dashboard.day_quality.find((row) => row.strategy_id === activeStrategyId)
  const visibleStrategyIds = new Set(visibleSummaries.map((summary) => summary.strategy_id))
  const visibleDiagnostics = dashboard.diagnostics.filter((row) => visibleStrategyIds.has(row.strategy_id))
  const dayQualityByStrategy = new Map(dashboard.day_quality.map((row) => [row.strategy_id, row]))
  const cashByStrategy = new Map(cashProfiles.map((row) => [row.strategy_id, row]))
  const summaryByStrategy = new Map(visibleSummaries.map((summary) => [summary.strategy_id, summary]))
  const strategyRollups = new Map(visibleSummaries.map((summary) => {
    const months = dashboard.monthly_returns.filter((row) => row.strategy_id === summary.strategy_id)
    const profitableMonths = months.filter((row) => row.pnl > 0).length
    const monthPct = months.length > 0 ? (profitableMonths / months.length) * 100 : 0
    const equityRows = dashboard.equity_curve.filter((row) => row.strategy_id === summary.strategy_id)
    const latestEquity = equityRows[equityRows.length - 1]
    return [
      summary.strategy_id,
      {
        finalReturnPct: latestEquity?.cumulative_return_pct ?? summary.deployed_return_pct,
        profitableMonths,
        totalMonths: months.length,
        monthPct,
      },
    ]
  }))
  const profitableMonths = monthlyRows.filter((row) => row.pnl > 0).length
  const profitableMonthPct = monthlyRows.length > 0 ? (profitableMonths / monthlyRows.length) * 100 : 0
  const bestMonth = monthlyRows.reduce<BacktestDashboardResponse['monthly_returns'][number] | null>(
    (best, row) => (!best || row.pnl > best.pnl ? row : best),
    null,
  )
  const worstMonth = monthlyRows.reduce<BacktestDashboardResponse['monthly_returns'][number] | null>(
    (worst, row) => (!worst || row.pnl < worst.pnl ? row : worst),
    null,
  )
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
      <Surface className="backtest-page-surface">
        <div className="backtest-hero">
          <div>
            <span className="eyebrow">Strategy Backtest</span>
            <h2>Pre-live strategy review</h2>
            <p>Compare every strategy by profitability, drawdown, consistency, and return over time before choosing what can move toward live execution.</p>
          </div>
          <div className="backtest-actions">
            <button type="button" className="ghost-button" onClick={onRefreshCache} disabled={refreshingCache || running}>
              <Database size={14} className={refreshingCache ? 'spin' : ''} />
              <span>{refreshingCache ? 'Refreshing Cache' : 'Refresh Cache'}</span>
            </button>
            <button type="button" className="primary-button" onClick={onRun} disabled={running}>
              <RefreshCw size={14} className={running ? 'spin' : ''} />
              <span>{running ? 'Running Backtest' : 'Run Backtest'}</span>
            </button>
          </div>
        </div>

        <div className="backtest-summary-strip">
          <CandidateStat label="Run" value={dashboard.run_id.replace('watchlist-swing-', '')} />
          <CandidateStat label="Strategies" value={String(dashboard.summaries.length)} />
          <CandidateStat label="Shown" value={String(visibleSummaries.length)} tone={visibleSummaries.length > 0 ? 'positive' : 'warning'} />
          <CandidateStat label="Trades Stored" value={dashboard.summaries.reduce((sum, item) => sum + item.total_trades, 0).toLocaleString('en-IN')} />
          <CandidateStat label="Min Sample" value={`${MIN_BACKTEST_STRATEGY_TRADES}+ trades`} />
          {cache && <CandidateStat label="Data Window" value={`${cache.from_date || 'N/A'} to ${cache.to_date || 'N/A'}`} />}
        </div>

        {cashPortfolio && (
          <Surface className="inner-surface cash-reality-panel">
            <div className="compact-section-head">
              <div>
                <span className="eyebrow">Cash Reality</span>
                <h2>Portfolio after blocked overlapping trades</h2>
              </div>
              <div className="mini-chip">
                <WalletCards size={14} />
                <span>{currency(cashPortfolio.initial_capital)} base</span>
              </div>
            </div>
            <div className="backtest-kpi-grid cash-kpi-grid">
              <CandidateStat label="Cash P&L" value={currency(cashPortfolio.total_pnl)} tone={cashPortfolio.total_pnl >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Cash Return" value={pct(cashPortfolio.return_pct)} tone={cashPortfolio.return_pct >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Annualized" value={pct(cashPortfolio.annualized_return_pct)} tone={cashPortfolio.annualized_return_pct >= 0 ? 'positive' : 'danger'} />
              <CandidateStat label="Taken / Skipped" value={`${cashPortfolio.trades_taken.toLocaleString('en-IN')} / ${cashPortfolio.skipped_entries.toLocaleString('en-IN')}`} tone={cashPortfolio.skipped_entries > 0 ? 'warning' : 'positive'} />
              <CandidateStat label="Peak Used" value={`${pct(cashPortfolio.peak_capital_used_pct)} ${currency(cashPortfolio.peak_capital_used)}`} tone={cashPortfolio.peak_capital_used_pct <= 100 ? 'positive' : 'warning'} />
              <CandidateStat label="Max Open" value={`${cashPortfolio.max_open_positions} positions`} />
              <CandidateStat label="Max Drawdown" value={`${currency(cashPortfolio.max_drawdown_rs)} (${pct(cashPortfolio.max_drawdown_pct)})`} tone="danger" />
              <CandidateStat label="Sharpe" value={`${cashPortfolio.sharpe_ratio.toFixed(2)} / ${cashPortfolio.sortino_ratio.toFixed(2)}`} tone={cashPortfolio.sharpe_ratio >= 1 ? 'positive' : 'warning'} />
            </div>
          </Surface>
        )}

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
        <div className="desk-control-panel backtest-control-panel strategy-filter-panel">
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
            <select className="select-input" value={activeStrategyId} onChange={(event) => setSelectedStrategy(event.currentTarget.value)}>
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
                <span className="eyebrow">Strategy Wise Overall</span>
                <h2>All strategies</h2>
              </div>
              <div className="mini-chip">
                <Target size={14} />
                <span>{visibleSummaries.length} visible</span>
              </div>
            </div>
            <div className="method-score-table">
              <div className="method-score-row method-score-head">
                <span>Strategy</span>
                <span>Status</span>
                <span>Total Return</span>
                <span>Annualized</span>
                <span>Cash Ret</span>
                <span>P&L</span>
                <span>Taken / Skip</span>
                <span>Win</span>
                <span>PF</span>
                <span>Sharpe</span>
                <span>Months</span>
                <span>Positive Days</span>
                <span>Max DD %</span>
                <span>Loss Streak</span>
              </div>
              {visibleDiagnostics.map((row) => {
                const canOpen = visibleSummaries.some((summary) => summary.strategy_id === row.strategy_id)
                const positiveDaysPct = dayQualityByStrategy.get(row.strategy_id)?.positive_days_pct ?? 0
                const summary = summaryByStrategy.get(row.strategy_id)
                const rollup = strategyRollups.get(row.strategy_id)
                const cashProfile = cashByStrategy.get(row.strategy_id)
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
                    <strong className={(rollup?.finalReturnPct ?? 0) >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(rollup?.finalReturnPct ?? 0)}</strong>
                    <strong className={row.annualized_return_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(row.annualized_return_pct)}</strong>
                    <strong className={(cashProfile?.return_pct ?? 0) >= 0 ? 'tone-positive' : 'tone-danger'}>{cashProfile ? pct(cashProfile.return_pct) : 'N/A'}</strong>
                    <strong className={(summary?.total_pnl ?? row.total_pnl) >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(summary?.total_pnl ?? row.total_pnl)}</strong>
                    <span>{cashProfile ? `${cashProfile.trades_taken}/${cashProfile.skipped_entries}` : 'N/A'}</span>
                    <span>{row.win_rate.toFixed(2)}%</span>
                    <span>{row.profit_factor.toFixed(2)}</span>
                    <span className={row.sharpe_ratio >= 1 ? 'tone-positive' : row.sharpe_ratio >= 0 ? 'tone-warning' : 'tone-danger'}>{row.sharpe_ratio.toFixed(2)}</span>
                    <span>{rollup ? `${rollup.profitableMonths}/${rollup.totalMonths} (${rollup.monthPct.toFixed(0)}%)` : 'N/A'}</span>
                    <span>{positiveDaysPct.toFixed(2)}%</span>
                    <strong className="tone-danger">{pct(row.max_drawdown_pct)}</strong>
                    <span>{row.max_losing_streak}</span>
                  </button>
                )
              })}
            </div>
          </Surface>
        )}

        {!selectedSummary && (
          <div className="portfolio-empty">
            <ListTodo size={20} />
            <p>No backtest methods match the current filters.</p>
          </div>
        )}

        {selectedSummary && (
          <>
            <Surface className="inner-surface selected-strategy-panel">
              <div className="compact-section-head">
                <div>
                  <span className="eyebrow">Selected Strategy</span>
                  <h2>{strategyLabel(activeStrategyId)}</h2>
                </div>
                <StagePill label={selectedDiagnostic?.status ?? 'Review'} tone={(selectedDiagnostic?.status ?? '') === 'Candidate' ? 'positive' : (selectedDiagnostic?.status ?? '') === 'Rejected' ? 'danger' : 'warning'} />
              </div>
              <div className="backtest-kpi-grid selected-kpi-grid">
                <CandidateStat label="Total P&L" value={currency(selectedSummary.total_pnl)} tone={selectedSummary.total_pnl >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="Return" value={pct(selectedEquity[selectedEquity.length - 1]?.cumulative_return_pct ?? selectedSummary.deployed_return_pct)} tone={selectedSummary.deployed_return_pct >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="Annualized" value={selectedDiagnostic ? pct(selectedDiagnostic.annualized_return_pct) : 'N/A'} tone={(selectedDiagnostic?.annualized_return_pct ?? 0) >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="Cash Return" value={selectedCashProfile ? pct(selectedCashProfile.return_pct) : 'N/A'} tone={(selectedCashProfile?.return_pct ?? 0) >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="Cash Annualized" value={selectedCashProfile ? pct(selectedCashProfile.annualized_return_pct) : 'N/A'} tone={(selectedCashProfile?.annualized_return_pct ?? 0) >= 0 ? 'positive' : 'danger'} />
                <CandidateStat label="Win Rate" value={`${selectedSummary.win_rate.toFixed(2)}%`} tone={selectedSummary.win_rate >= 50 ? 'positive' : 'warning'} />
                <CandidateStat label="Profit Factor" value={selectedDiagnostic ? selectedDiagnostic.profit_factor.toFixed(2) : 'N/A'} tone={(selectedDiagnostic?.profit_factor ?? 0) >= 1.05 ? 'positive' : 'warning'} />
                <CandidateStat label="Sharpe" value={selectedDiagnostic ? selectedDiagnostic.sharpe_ratio.toFixed(2) : 'N/A'} tone={(selectedDiagnostic?.sharpe_ratio ?? 0) >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="Cash Sharpe" value={selectedCashProfile ? selectedCashProfile.sharpe_ratio.toFixed(2) : 'N/A'} tone={(selectedCashProfile?.sharpe_ratio ?? 0) >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="Sortino" value={selectedDiagnostic ? selectedDiagnostic.sortino_ratio.toFixed(2) : 'N/A'} tone={(selectedDiagnostic?.sortino_ratio ?? 0) >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="Payoff" value={selectedDiagnostic ? selectedDiagnostic.payoff_ratio.toFixed(2) : 'N/A'} tone={(selectedDiagnostic?.payoff_ratio ?? 0) >= 1 ? 'positive' : 'warning'} />
                <CandidateStat label="Avg Win" value={selectedDiagnostic ? pct(selectedDiagnostic.avg_win_pct) : 'N/A'} tone="positive" />
                <CandidateStat label="Avg Loss" value={selectedDiagnostic ? pct(selectedDiagnostic.avg_loss_pct) : 'N/A'} tone="danger" />
                <CandidateStat label="Profitable Months" value={`${profitableMonths}/${monthlyRows.length || 0}`} tone={profitableMonthPct >= 55 ? 'positive' : 'warning'} />
                <CandidateStat label="Positive Days" value={dayQuality ? `${dayQuality.positive_days_pct.toFixed(2)}%` : 'N/A'} tone={(dayQuality?.positive_days_pct ?? 0) >= 55 ? 'positive' : 'warning'} />
                <CandidateStat label="Max Drawdown" value={dayQuality ? `${currency(dayQuality.max_drawdown_rs)} (${selectedDiagnostic ? pct(selectedDiagnostic.max_drawdown_pct) : 'N/A'})` : 'N/A'} tone="danger" />
                <CandidateStat label="Cash Max DD" value={selectedCashProfile ? `${currency(selectedCashProfile.max_drawdown_rs)} (${pct(selectedCashProfile.max_drawdown_pct)})` : 'N/A'} tone="danger" />
                <CandidateStat label="Taken / Skipped" value={selectedCashProfile ? `${selectedCashProfile.trades_taken.toLocaleString('en-IN')} / ${selectedCashProfile.skipped_entries.toLocaleString('en-IN')}` : 'N/A'} tone={(selectedCashProfile?.skipped_entries ?? 0) > 0 ? 'warning' : 'positive'} />
                <CandidateStat label="Peak Cash Used" value={selectedCashProfile ? `${pct(selectedCashProfile.peak_capital_used_pct)} ${currency(selectedCashProfile.peak_capital_used)}` : 'N/A'} tone={(selectedCashProfile?.peak_capital_used_pct ?? 0) <= 100 ? 'positive' : 'warning'} />
                <CandidateStat label="Loss Streak" value={selectedDiagnostic ? `${selectedDiagnostic.max_losing_streak} trades` : 'N/A'} tone={(selectedDiagnostic?.max_losing_streak ?? 99) <= 4 ? 'positive' : 'warning'} />
              </div>
            </Surface>

            <div className="backtest-grid backtest-insight-grid">
              <StrategyEquityCurvePanel strategyId={activeStrategyId} rows={selectedEquity} />
              <StrategyCashRealityPanel
                strategyId={activeStrategyId}
                profile={selectedCashProfile}
                rows={selectedCashEquity}
                months={selectedCashMonths}
              />
              <StrategySuccessMetricsPanel
                summary={selectedSummary}
                diagnostic={selectedDiagnostic}
                dayQuality={dayQuality}
              />
            </div>

            <div className="backtest-grid">
              <Surface className="inner-surface backtest-panel">
                <div className="compact-section-head">
                  <div>
                    <span className="eyebrow">Yearly Returns</span>
                    <h2>{strategyLabel(activeStrategyId)}</h2>
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
              <div className="monthly-summary-grid">
                <CandidateStat label="Profitable Months" value={`${profitableMonths}/${monthlyRows.length || 0}`} tone={profitableMonthPct >= 55 ? 'positive' : 'warning'} />
                <CandidateStat label="Monthly Hit Rate" value={`${profitableMonthPct.toFixed(1)}%`} tone={profitableMonthPct >= 55 ? 'positive' : 'warning'} />
                <CandidateStat label="Best Month" value={bestMonth ? `${bestMonth.month_label} ${bestMonth.year} ${currency(bestMonth.pnl)}` : 'N/A'} tone="positive" />
                <CandidateStat label="Worst Month" value={worstMonth ? `${worstMonth.month_label} ${worstMonth.year} ${currency(worstMonth.pnl)}` : 'N/A'} tone="danger" />
              </div>
              <MonthlyReturnBars rows={selectedYearMonths} activeYear={activeYear} />
              <div className="monthly-pnl-grid">
                {MONTH_LABELS.map((label, index) => {
                  const month = selectedYearMonths.find((row) => row.month === index + 1)
                  const hasTrades = (month?.trades ?? 0) > 0
                  const pnl = month?.pnl ?? 0
                  const activeClass = pnl > 0 ? 'monthly-cell-positive' : pnl < 0 ? 'monthly-cell-negative' : 'monthly-cell-flat'
                  return (
                    <div key={`${activeYear}-${label}`} className={`monthly-cell ${activeClass}`}>
                      <span>{label}</span>
                      <strong>{hasTrades ? currency(pnl) : 'No trades'}</strong>
                      <small>{hasTrades ? `${month!.trades.toLocaleString('en-IN')} trades | ${pct(month!.return_pct)}` : '0 trades'}</small>
                    </div>
                  )
                })}
              </div>
            </Surface>
            </div>
          </>
        )}
          </>
        )}
      </Surface>
    </div>
  )
}

function StrategyEquityCurvePanel({
  strategyId,
  rows,
}: {
  strategyId: string
  rows: BacktestEquityPoint[]
}) {
  if (rows.length === 0) {
    return (
      <Surface className="inner-surface backtest-panel equity-panel empty-panel">
        <div className="empty-icon-shell">
          <BarChart3 size={18} />
        </div>
        <h3>No equity curve yet</h3>
        <p>Run the backtest again so the dashboard can build daily cumulative P&L points.</p>
      </Surface>
    )
  }

  const width = 760
  const height = 260
  const padX = 38
  const padY = 24
  const innerWidth = width - padX * 2
  const innerHeight = height - padY * 2
  const values = rows.map((row) => row.cumulative_return_pct)
  const minValue = Math.min(0, ...values)
  const maxValue = Math.max(0, ...values)
  const span = Math.max(maxValue - minValue, 1)
  const pointFor = (value: number, index: number) => {
    const x = padX + (index / Math.max(rows.length - 1, 1)) * innerWidth
    const y = padY + ((maxValue - value) / span) * innerHeight
    return { x, y }
  }
  const points = rows.map((row, index) => pointFor(row.cumulative_return_pct, index))
  const linePath = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ')
  const zeroY = pointFor(0, 0).y
  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(2)} ${zeroY.toFixed(2)} L ${points[0].x.toFixed(2)} ${zeroY.toFixed(2)} Z`
  const latest = rows[rows.length - 1]
  const worstDrawdown = Math.min(...rows.map((row) => row.drawdown_rs))
  const bestPoint = rows.reduce((best, row) => (row.cumulative_pnl > best.cumulative_pnl ? row : best), rows[0])
  const returnBarWidth = Math.min(100, Math.max(6, (Math.abs(latest.cumulative_return_pct) / 200) * 100))
  const drawdownBarWidth = Math.min(100, Math.max(6, (Math.abs(worstDrawdown) / Math.max(Math.abs(bestPoint.cumulative_pnl), 1)) * 100))

  return (
    <Surface className="inner-surface backtest-panel equity-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Equity Curve</span>
          <h2>{strategyLabel(strategyId)} return percentage over time</h2>
        </div>
        <div className="mini-chip">
          <Activity size={14} />
          <span>{rows.length.toLocaleString('en-IN')} trading days</span>
        </div>
      </div>

      <div className="equity-chart-shell">
        <svg viewBox={`0 0 ${width} ${height}`} className="equity-chart" role="img" aria-label={`${strategyLabel(strategyId)} equity curve`}>
          <defs>
            <linearGradient id={`equity-fill-${strategyId}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(38,223,154,0.28)" />
              <stop offset="100%" stopColor="rgba(38,223,154,0.02)" />
            </linearGradient>
          </defs>
          {[0, 1, 2, 3].map((line) => {
            const y = padY + (line / 3) * innerHeight
            return <line key={line} x1={padX} x2={padX + innerWidth} y1={y} y2={y} className="chart-grid-line" />
          })}
          <line x1={padX} x2={padX + innerWidth} y1={zeroY} y2={zeroY} className="equity-zero-line" />
          <path d={areaPath} className="equity-area" fill={`url(#equity-fill-${strategyId})`} />
          <path d={linePath} className="equity-line" />
        </svg>
        <div className="equity-axis-labels">
          <span>{rows[0].trade_date}</span>
          <span>{latest.trade_date}</span>
        </div>
      </div>

      <div className="equity-value-bars">
        <div className="equity-value-row">
          <div>
            <span className="micro-label">Net return</span>
            <strong className={latest.cumulative_return_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(latest.cumulative_return_pct)}</strong>
          </div>
          <div className="equity-value-track">
            <span
              className={latest.cumulative_return_pct >= 0 ? 'equity-value-fill equity-value-positive' : 'equity-value-fill equity-value-negative'}
              style={{ width: `${returnBarWidth}%` }}
            />
          </div>
        </div>
        <div className="equity-value-row">
          <div>
            <span className="micro-label">Max drawdown</span>
            <strong className="tone-danger">{currency(worstDrawdown)}</strong>
          </div>
          <div className="equity-value-track">
            <span className="equity-value-fill equity-value-negative" style={{ width: `${drawdownBarWidth}%` }} />
          </div>
        </div>
      </div>

      <div className="chart-summary-grid">
        <CandidateStat label="Final P&L" value={currency(latest.cumulative_pnl)} tone={latest.cumulative_pnl >= 0 ? 'positive' : 'danger'} />
        <CandidateStat label="Return Over Time" value={pct(latest.cumulative_return_pct)} tone={latest.cumulative_return_pct >= 0 ? 'positive' : 'danger'} />
        <CandidateStat label="Worst Drawdown" value={currency(worstDrawdown)} tone="danger" />
        <CandidateStat label="Peak Date" value={`${bestPoint.trade_date} ${currency(bestPoint.cumulative_pnl)}`} tone="positive" />
      </div>
    </Surface>
  )
}

function StrategyCashRealityPanel({
  strategyId,
  profile,
  rows,
  months,
}: {
  strategyId: string
  profile?: BacktestCashProfile
  rows: BacktestCashEquityPoint[]
  months: BacktestDashboardResponse['cash_monthly_returns']
}) {
  if (!profile || rows.length === 0) {
    return (
      <Surface className="inner-surface backtest-panel equity-panel empty-panel">
        <div className="empty-icon-shell">
          <WalletCards size={18} />
        </div>
        <h3>No cash curve yet</h3>
        <p>Run the backtest again to build cash-constrained analytics.</p>
      </Surface>
    )
  }

  const width = 760
  const height = 240
  const padX = 38
  const padY = 24
  const innerWidth = width - padX * 2
  const innerHeight = height - padY * 2
  const values = rows.map((row) => row.return_pct)
  const minValue = Math.min(0, ...values)
  const maxValue = Math.max(0, ...values)
  const span = Math.max(maxValue - minValue, 1)
  const pointFor = (value: number, index: number) => {
    const x = padX + (index / Math.max(rows.length - 1, 1)) * innerWidth
    const y = padY + ((maxValue - value) / span) * innerHeight
    return { x, y }
  }
  const points = rows.map((row, index) => pointFor(row.return_pct, index))
  const linePath = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ')
  const zeroY = pointFor(0, 0).y
  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(2)} ${zeroY.toFixed(2)} L ${points[0].x.toFixed(2)} ${zeroY.toFixed(2)} Z`
  const latest = rows[rows.length - 1]
  const worstDrawdown = Math.min(...rows.map((row) => row.drawdown_rs))
  const bestPoint = rows.reduce((best, row) => (row.cumulative_pnl > best.cumulative_pnl ? row : best), rows[0])
  const greenMonths = months.filter((row) => row.pnl > 0).length

  return (
    <Surface className="inner-surface backtest-panel equity-panel cash-curve-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Cash Equity</span>
          <h2>{strategyLabel(strategyId)} executable curve</h2>
        </div>
        <div className="mini-chip">
          <WalletCards size={14} />
          <span>{profile.trades_taken}/{profile.candidate_trades} taken</span>
        </div>
      </div>

      <div className="equity-chart-shell">
        <svg viewBox={`0 0 ${width} ${height}`} className="equity-chart" role="img" aria-label={`${strategyLabel(strategyId)} cash-constrained equity curve`}>
          {[0, 1, 2, 3].map((line) => {
            const y = padY + (line / 3) * innerHeight
            return <line key={line} x1={padX} x2={padX + innerWidth} y1={y} y2={y} className="chart-grid-line" />
          })}
          <line x1={padX} x2={padX + innerWidth} y1={zeroY} y2={zeroY} className="equity-zero-line" />
          <path d={areaPath} className="cash-equity-area" />
          <path d={linePath} className="cash-equity-line" />
        </svg>
        <div className="equity-axis-labels">
          <span>{rows[0].trade_date}</span>
          <span>{latest.trade_date}</span>
        </div>
      </div>

      <div className="chart-summary-grid">
        <CandidateStat label="Cash P&L" value={currency(profile.total_pnl)} tone={profile.total_pnl >= 0 ? 'positive' : 'danger'} />
        <CandidateStat label="Cash Return" value={pct(profile.return_pct)} tone={profile.return_pct >= 0 ? 'positive' : 'danger'} />
        <CandidateStat label="Worst DD" value={currency(worstDrawdown)} tone="danger" />
        <CandidateStat label="Peak" value={`${bestPoint.trade_date} ${currency(bestPoint.cumulative_pnl)}`} tone="positive" />
        <CandidateStat label="Months" value={`${greenMonths}/${months.length || 0}`} tone={profile.positive_months_pct >= 55 ? 'positive' : 'warning'} />
        <CandidateStat label="Blocked" value={`${profile.cash_blocked_entries} cash / ${profile.duplicate_entries_skipped} dup`} tone={profile.skipped_entries > 0 ? 'warning' : 'positive'} />
      </div>
    </Surface>
  )
}

function MonthlyReturnBars({
  rows,
  activeYear,
}: {
  rows: BacktestDashboardResponse['monthly_returns']
  activeYear: number | null
}) {
  const rowsByMonth = new Map(rows.map((row) => [row.month, row]))
  const maxAbsPnl = Math.max(1, ...rows.map((row) => Math.abs(row.pnl)))

  return (
    <div className="monthly-bar-chart">
      <div className="monthly-bar-head">
        <div>
          <span className="eyebrow">Monthly Graph</span>
          <h3>{activeYear ?? 'Selected year'} P&L bars</h3>
        </div>
        <span className="mini-chip">{rows.filter((row) => row.pnl > 0).length} green months</span>
      </div>
      <div className="monthly-bar-area" role="img" aria-label={`${activeYear ?? 'Selected year'} monthly P&L bar chart`}>
        {MONTH_LABELS.map((label, index) => {
          const month = rowsByMonth.get(index + 1)
          const hasTrades = (month?.trades ?? 0) > 0
          const pnl = month?.pnl ?? 0
          const height = hasTrades ? Math.max(8, (Math.abs(pnl) / maxAbsPnl) * 100) : 0
          const tone = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'flat'
          return (
            <div key={`monthly-bar-${activeYear}-${label}`} className="monthly-bar-column">
              <strong className={pnl >= 0 ? 'tone-positive' : 'tone-danger'}>{hasTrades ? compactCurrency(pnl) : '-'}</strong>
              <div className="monthly-bar-track">
                <span className={`monthly-bar-fill monthly-bar-${tone}`} style={{ height: `${height}%` }} />
              </div>
              <small>{label}</small>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StrategySuccessMetricsPanel({
  summary,
  diagnostic,
  dayQuality,
}: {
  summary: BacktestRunSummary
  diagnostic?: BacktestStrategyDiagnostic
  dayQuality?: BacktestDayQuality
}) {
  const recoveryFactor = diagnostic && diagnostic.max_drawdown_rs < 0
    ? diagnostic.recovery_factor
    : null
  const stopShare = summary.total_trades > 0 ? (summary.sl_exits / summary.total_trades) * 100 : 0
  const timeExitShare = summary.total_trades > 0 ? (summary.time_exits / summary.total_trades) * 100 : 0
  const checks = [
    {
      label: 'Profit factor',
      value: diagnostic ? diagnostic.profit_factor.toFixed(2) : 'N/A',
      note: 'Above 1.30 is healthier; below 1.10 is thin edge.',
      tone: (diagnostic?.profit_factor ?? 0) >= 1.3 ? 'positive' : (diagnostic?.profit_factor ?? 0) >= 1.1 ? 'warning' : 'danger',
    },
    {
      label: 'Annualized return',
      value: diagnostic ? pct(diagnostic.annualized_return_pct) : 'N/A',
      note: 'This annualizes the equity-curve return on fixed strategy capital.',
      tone: (diagnostic?.annualized_return_pct ?? 0) >= 25 ? 'positive' : (diagnostic?.annualized_return_pct ?? 0) > 0 ? 'warning' : 'danger',
    },
    {
      label: 'Sharpe / Sortino',
      value: diagnostic ? `${diagnostic.sharpe_ratio.toFixed(2)} / ${diagnostic.sortino_ratio.toFixed(2)}` : 'N/A',
      note: 'Risk-adjusted daily P&L; above 1 is decent, above 2 is strong.',
      tone: (diagnostic?.sharpe_ratio ?? 0) >= 1.5 && (diagnostic?.sortino_ratio ?? 0) >= 2 ? 'positive' : (diagnostic?.sharpe_ratio ?? 0) >= 0.75 ? 'warning' : 'danger',
    },
    {
      label: 'Recovery factor',
      value: recoveryFactor === null ? 'N/A' : recoveryFactor.toFixed(2),
      note: 'Total profit divided by max drawdown; higher means smoother compounding.',
      tone: (recoveryFactor ?? 0) >= 2 ? 'positive' : (recoveryFactor ?? 0) >= 1 ? 'warning' : 'danger',
    },
    {
      label: 'Max losing streak',
      value: diagnostic ? `${diagnostic.max_losing_streak} trades` : 'N/A',
      note: 'Longest consecutive losing trade run in chronological order.',
      tone: (diagnostic?.max_losing_streak ?? 99) <= 4 ? 'positive' : (diagnostic?.max_losing_streak ?? 99) <= 8 ? 'warning' : 'danger',
    },
    {
      label: 'Payoff',
      value: diagnostic ? `${pct(diagnostic.avg_win_pct)} / ${pct(diagnostic.avg_loss_pct)}` : 'N/A',
      note: 'Average win versus average loss after the backtest exit model.',
      tone: (diagnostic?.payoff_ratio ?? 0) >= 1.4 ? 'positive' : (diagnostic?.payoff_ratio ?? 0) >= 1 ? 'warning' : 'danger',
    },
    {
      label: 'Consistency',
      value: diagnostic ? `${diagnostic.positive_months_pct.toFixed(1)}% months` : 'N/A',
      note: 'Positive months plus positive days tell you if returns are not one lucky burst.',
      tone: (diagnostic?.positive_months_pct ?? 0) >= 55 && (dayQuality?.positive_days_pct ?? 0) >= 50 ? 'positive' : 'warning',
    },
    {
      label: 'Sample size',
      value: summary.total_trades.toLocaleString('en-IN'),
      note: 'More trades across many months beats a tiny perfect-looking backtest.',
      tone: summary.total_trades >= 200 ? 'positive' : summary.total_trades >= 60 ? 'warning' : 'danger',
    },
    {
      label: 'Exit mix',
      value: `${stopShare.toFixed(0)}% SL / ${timeExitShare.toFixed(0)}% time`,
      note: 'Too many stop or time exits usually means entry quality needs tightening.',
      tone: stopShare <= 35 && timeExitShare <= 45 ? 'positive' : 'warning',
    },
  ] as const

  return (
    <Surface className="inner-surface backtest-panel success-metrics-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Strategy Success</span>
          <h2>Metrics to judge before going live</h2>
        </div>
      </div>
      <div className="metric-check-grid">
        {checks.map((check) => (
          <div key={check.label} className={`metric-check-card tone-${check.tone}`}>
            <span className="micro-label">{check.label}</span>
            <strong>{check.value}</strong>
            <p>{check.note}</p>
          </div>
        ))}
      </div>
    </Surface>
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

function MarketHubView({
  news,
  deals,
  activity,
  events,
  predictions,
  loading,
  refreshing,
  status,
  dealError,
  activityError,
  eventError,
  symbol,
  source,
  dealType,
  activityMetric,
  highImpactOnly,
  onSymbolChange,
  onSourceChange,
  onDealTypeChange,
  onActivityMetricChange,
  onHighImpactChange,
  onLoad,
  onRefresh,
  onSelect,
}: {
  news: NewsItem[]
  deals: NseLargeDeal[]
  activity: MarketActivityItem[]
  events: CorporateEvent[]
  predictions: BacktestPrediction[]
  loading: boolean
  refreshing: boolean
  status: string
  dealError: string
  activityError: string
  eventError: string
  symbol: string
  source: string
  dealType: string
  activityMetric: string
  highImpactOnly: boolean
  onSymbolChange: (value: string) => void
  onSourceChange: (value: string) => void
  onDealTypeChange: (value: string) => void
  onActivityMetricChange: (value: string) => void
  onHighImpactChange: (value: boolean) => void
  onLoad: () => void
  onRefresh: () => void
  onSelect: (symbol: string) => void
}) {
  const uniqueArticles = new Set(news.map((item) => item.article_id)).size
  const linkedSymbols = new Set(news.map((item) => item.symbol).filter(Boolean)).size
  const bullish = news.filter((item) => item.direction === 'BULLISH').length
  const bearish = news.filter((item) => item.direction === 'BEARISH').length
  const actionableCatalysts = news.filter((item) => item.symbol && item.impact_score >= 2.5).length
  const dealValueCr = deals.reduce((sum, deal) => sum + deal.value_lakh / 100, 0)
  const shockers = activity.filter((item) => item.metric_type === 'volume_shockers')
  const topVolume = activity.reduce((max, item) => Math.max(max, item.volume_multiplier || 0), 0)
  const sourceOptions = Array.from(new Set(news.map((item) => item.source).filter(Boolean))).sort()

  return (
    <div className="page-stack market-hub-page">
      <Surface className="market-hub-control">
        <div className="section-head">
          <div>
            <span className="eyebrow">Market Command Center</span>
            <h2>News, NSE deals, volume shockers, and active stocks</h2>
          </div>
          <div className="row-actions">
            <button type="button" className="ghost-button" onClick={onLoad} disabled={loading || refreshing}>
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
              <span>{loading ? 'Loading' : 'Reload'}</span>
            </button>
            <button type="button" className="primary-button" onClick={onRefresh} disabled={refreshing}>
              <Zap size={14} />
              <span>{refreshing ? 'Refreshing' : 'Refresh Sources'}</span>
            </button>
          </div>
        </div>

        <div className="market-filter-grid">
          <label className="input-with-icon">
            <Search size={13} />
            <input
              value={symbol}
              onChange={(event) => onSymbolChange(event.target.value.toUpperCase())}
              placeholder="Symbol"
              className="text-input"
            />
          </label>
          <select value={source} onChange={(event) => onSourceChange(event.target.value)} className="select-input">
            <option value="">All news sources</option>
            {sourceOptions.map((option) => (
              <option key={option} value={option}>{formatSourceName(option)}</option>
            ))}
          </select>
          <select value={dealType} onChange={(event) => onDealTypeChange(event.target.value)} className="select-input">
            <option value="">All NSE deals</option>
            <option value="BULK">Bulk deals</option>
            <option value="BLOCK">Block deals</option>
          </select>
          <select value={activityMetric} onChange={(event) => onActivityMetricChange(event.target.value)} className="select-input">
            <option value="">All activity</option>
            <option value="volume_shockers">Volume shockers</option>
            <option value="most_active">Most active stocks</option>
          </select>
          <button
            type="button"
            className={highImpactOnly ? 'filter-chip filter-chip-active' : 'filter-chip'}
            onClick={() => onHighImpactChange(!highImpactOnly)}
          >
            <Filter size={13} />
            <span>Impact 2.5+</span>
          </button>
        </div>

        {status && <div className="success-banner">{status}</div>}
        {dealError && <div className="warning-banner">NSE deals: {dealError}</div>}
        {activityError && <div className="warning-banner">Market activity: {activityError}</div>}
        {eventError && <div className="warning-banner">Previous catalysts: {eventError}</div>}
      </Surface>

      <div className="market-stat-grid">
        <CandidateStat label="Articles" value={String(uniqueArticles)} />
        <CandidateStat label="Linked Symbols" value={String(linkedSymbols)} tone={linkedSymbols > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="NSE Deal Value" value={`Rs ${dealValueCr.toFixed(1)}Cr`} tone={deals.length > 0 ? 'positive' : 'neutral'} />
        <CandidateStat label="Shockers" value={String(shockers.length)} tone={shockers.length > 0 ? 'warning' : 'neutral'} />
        <CandidateStat label="Top Vol X" value={topVolume > 0 ? `${topVolume.toFixed(2)}x` : '0x'} tone={topVolume >= 3 ? 'positive' : 'neutral'} />
        <CandidateStat label="Catalyst Checks" value={String(actionableCatalysts)} tone={actionableCatalysts > 0 ? 'warning' : 'neutral'} />
      </div>

      <FivePercentStrategyBrief catalystCount={actionableCatalysts} bullish={bullish} bearish={bearish} />

      <PredictionHistoryPanel predictions={predictions} loading={loading} onSelect={onSelect} />

      <PreviousCatalystsPanel events={events} loading={loading} onSelect={onSelect} />

      <div className="market-feed-grid">
        <MarketActivityPanel activity={activity} loading={loading} onSelect={onSelect} />
        <LargeDealsPanel deals={deals} loading={loading} onSelect={onSelect} />
      </div>

      <NewsTapePanel news={news} deals={deals} activity={activity} loading={loading} onSelect={onSelect} />
    </div>
  )
}

function FivePercentStrategyBrief({
  catalystCount,
  bullish,
  bearish,
}: {
  catalystCount: number
  bullish: number
  bearish: number
}) {
  return (
    <Surface className="five-percent-brief">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Corrected Historical Backtest · Paper Only</span>
          <h2>5% target / 10-session swing plan</h2>
        </div>
        <span className="stage-pill tone-warning">Needs fresh confirmation</span>
      </div>
      <div className="strategy-brief-grid">
        <div className="strategy-rule-stack">
          <strong>Entry must pass every gate</strong>
          <ol>
            <li>Realized same-day financial-result filing; a headline or scheduled event is not enough.</li>
            <li>Relative volume at least 8x the 50-session baseline.</li>
            <li>Close in the upper 30% of the signal candle, in an uptrend, and at least 85% of the prior 52-week high.</li>
            <li>Enter next session only; use a 5% stop, 5% target, and mandatory session-10 exit.</li>
          </ol>
        </div>
        <div className="strategy-evidence-grid">
          <span><strong>+1.09%</strong><small>Full backtest expectancy</small></span>
          <span><strong>+2.05%</strong><small>Validation expectancy</small></span>
          <span><strong>+0.24%</strong><small>Held-out OOS expectancy</small></span>
          <span><strong>1.11</strong><small>OOS profit factor</small></span>
          <span><strong>45.7%</strong><small>OOS 5% target hit</small></span>
          <span><strong>35</strong><small>OOS trades</small></span>
        </div>
      </div>
      <div className="strategy-brief-foot">
        <span>{catalystCount} current checks · tone: {bullish} bullish / {bearish} bearish.</span>
        <span>Event-only baselines lost money. These are backtest results, not successful live predictions.</span>
      </div>
    </Surface>
  )
}

function PredictionHistoryPanel({
  predictions,
  loading,
  onSelect,
}: {
  predictions: BacktestPrediction[]
  loading: boolean
  onSelect: (symbol: string) => void
}) {
  const [outcomeFilter, setOutcomeFilter] = useState<'all' | 'target' | 'miss'>('all')
  const filtered = predictions.filter((row) => (
    outcomeFilter === 'all'
      ? true
      : outcomeFilter === 'target'
        ? row.target_hit === true
        : row.target_hit !== true
  ))
  const visible = filtered.slice(0, 6)
  const targetHits = predictions.filter((row) => row.target_hit === true).length

  return (
    <Surface className="prediction-history-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Historical Simulation · Not Live Calls</span>
          <h2>What the corrected strategy predicted in the past</h2>
        </div>
        <span className="mini-chip">{targetHits} target hits / {predictions.length} loaded</span>
      </div>
      <div className="prediction-history-toolbar">
        <p>These rows are backtested next-session entries. Prospective paper predictions have not accumulated yet.</p>
        <div className="news-view-toggle" role="group" aria-label="Historical prediction outcome">
          <button type="button" className={outcomeFilter === 'all' ? 'active' : ''} onClick={() => setOutcomeFilter('all')}>Recent</button>
          <button type="button" className={outcomeFilter === 'target' ? 'active' : ''} onClick={() => setOutcomeFilter('target')}>5% hit</button>
          <button type="button" className={outcomeFilter === 'miss' ? 'active' : ''} onClick={() => setOutcomeFilter('miss')}>Miss / stop</button>
        </div>
      </div>
      {loading && predictions.length === 0 ? (
        <MarketRowsSkeleton count={3} />
      ) : visible.length === 0 ? (
        <MarketEmpty icon={<Target size={18} />} label="Historical prediction evidence is unavailable" />
      ) : (
        <div className="prediction-history-grid">
          {visible.map((row) => {
            const outcome = row.outcome || row.exit_reason || (row.target_hit ? 'TARGET' : 'NOT TARGET')
            const eventTitle = row.event_title || row.event_titles || row.event_category || row.event_categories || 'Financial-results catalyst'
            const returnPct = Number(row.net_return_pct)
            return (
              <div key={`${row.strategy || 'strategy'}-${row.symbol}-${row.signal_date}`} className="prediction-history-card">
                <div className="market-row-badges">
                  <button type="button" className="symbol-link" onClick={() => onSelect(row.symbol)}>{row.symbol}</button>
                  <span className={`stage-pill ${row.target_hit ? 'tone-positive' : outcome.toUpperCase().includes('STOP') ? 'tone-danger' : 'tone-warning'}`}>{outcome}</span>
                  <span className="stage-pill tone-neutral">{row.split || 'backtest'}</span>
                </div>
                <strong>{eventTitle}</strong>
                <small>{row.signal_date} signal · {row.exit_date} exit · {row.hold_sessions} sessions</small>
                <div className="prediction-history-facts">
                  <span><small>Net return</small><strong className={returnPct >= 0 ? 'tone-positive' : 'tone-danger'}>{Number.isFinite(returnPct) ? `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%` : 'Unavailable'}</strong></span>
                  <span><small>Signal volume</small><strong>{typeof row.relvol50 === 'number' && Number.isFinite(row.relvol50) ? `${row.relvol50.toFixed(2)}x` : 'Unavailable'}</strong></span>
                  <span><small>5% target</small><strong>{row.target_hit ? 'Hit' : 'Not hit'}</strong></span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Surface>
  )
}

function PreviousCatalystsPanel({
  events,
  loading,
  onSelect,
}: {
  events: CorporateEvent[]
  loading: boolean
  onSelect: (symbol: string) => void
}) {
  const rankedEvents = Array.from(
    new Map(events.map((event) => [event.event_id, event])).values(),
  ).sort((left, right) => {
    const freshness = marketTimestampValue(right.event_time || right.event_date) - marketTimestampValue(left.event_time || left.event_date)
    return freshness !== 0 ? freshness : right.catalyst_score - left.catalyst_score
  })
  const visibleEvents = rankedEvents.slice(0, 6)

  return (
    <Surface className="previous-catalysts-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Previous Catalysts · 730-Day Context</span>
          <h2>Recent corporate events worth comparing</h2>
        </div>
        <span className="mini-chip">Top {visibleEvents.length} of {rankedEvents.length}</span>
      </div>
      <p className="previous-catalysts-note">This is an event archive, not a success record. Price outcomes remain unavailable until the event-performance endpoint is linked.</p>
      {loading && events.length === 0 ? (
        <MarketRowsSkeleton count={3} />
      ) : visibleEvents.length === 0 ? (
        <MarketEmpty icon={<CalendarDays size={18} />} label="No previous corporate events in the current filter" />
      ) : (
        <div className="previous-catalyst-grid">
          {visibleEvents.map((event) => (
            <details key={event.event_id} className="previous-catalyst-card">
              <summary>
                <div className="market-row-badges">
                  <span className="symbol-chip-static">{event.symbol}</span>
                  <span className="stage-pill tone-neutral">{formatSourceName(event.event_category || 'event')}</span>
                </div>
                <strong>{event.title}</strong>
                <small>
                  {marketTimestamp(event.event_time || event.event_date)} · Catalyst score {Number.isFinite(event.catalyst_score) ? event.catalyst_score.toFixed(2) : 'unavailable'}
                  {(event.evidence_count ?? 0) > 1 ? ` · ${event.evidence_count} linked filings` : ''}
                </small>
              </summary>
              <div className="previous-catalyst-detail">
                <p>{event.summary || 'No event summary was returned.'}</p>
                <span><strong>Historical outcome</strong>Not tracked by this event response.</span>
                <div className="news-detail-actions">
                  <button type="button" className="ghost-button ghost-button-small" onClick={() => onSelect(event.symbol)}>Open {event.symbol}</button>
                  {(event.source_url || event.attachment_url) && (
                    <a href={event.source_url || event.attachment_url} target="_blank" rel="noreferrer" className="ghost-button ghost-button-small news-source-action">
                      <span>Open filing</span>
                      <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              </div>
            </details>
          ))}
        </div>
      )}
    </Surface>
  )
}

function MarketActivityPanel({
  activity,
  loading,
  onSelect,
}: {
  activity: MarketActivityItem[]
  loading: boolean
  onSelect: (symbol: string) => void
}) {
  const [rowLimit, setRowLimit] = useState(6)
  const rankedActivity = [...activity].sort((left, right) => {
    const freshness = marketTimestampValue(right.snapshot_at || right.fetched_at) - marketTimestampValue(left.snapshot_at || left.fetched_at)
    if (freshness !== 0) return freshness
    if (right.volume_multiplier !== left.volume_multiplier) return right.volume_multiplier - left.volume_multiplier
    return right.value_cr - left.value_cr
  })
  const visibleActivity = rankedActivity.slice(0, rowLimit)

  return (
    <Surface className="market-feed-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Moneycontrol Activity</span>
          <h2>Volume shockers and active stocks</h2>
        </div>
        <div className="market-panel-controls">
          <span className="mini-chip">Top {Math.min(rowLimit, activity.length)} of {activity.length}</span>
          <label className="compact-limit-control">
            <span>Show</span>
            <select value={rowLimit} onChange={(event) => setRowLimit(Number(event.target.value))} aria-label="Volume rows to show">
              <option value={6}>6</option>
              <option value={12}>12</option>
              <option value={24}>24</option>
            </select>
          </label>
        </div>
      </div>
      {loading ? (
        <MarketRowsSkeleton count={5} />
      ) : activity.length === 0 ? (
        <MarketEmpty icon={<Activity size={18} />} label="No activity rows yet" />
      ) : (
        <div className="market-row-list">
          {visibleActivity.map((item, index) => {
            const sourceUrl = item.share_url || item.source_url || 'https://www.moneycontrol.com/stocks/market-stats/'
            return (
              <div key={`${item.row_id}-${index}`} className="market-activity-row">
                <div className="market-row-main">
                  <div className="market-row-badges">
                    <span className="stage-pill">#{item.rank || index + 1}</span>
                    <span className="stage-pill tone-warning">{formatActivityMetric(item.metric_type)}</span>
                    <button type="button" className="symbol-chip" onClick={() => onSelect(item.symbol)}>{item.symbol}</button>
                  </div>
                  <strong>{item.stock_name || item.symbol}</strong>
                  <small>{item.snapshot_at || item.fetched_at}</small>
                </div>
                <div className="market-row-metrics">
                  <span>{currency(item.price)}<small>Price</small></span>
                  <span className={item.change_pct >= 0 ? 'tone-positive' : 'tone-danger'}>{pct(item.change_pct)}<small>Move</small></span>
                  <span className={item.volume_multiplier >= 3 ? 'tone-positive' : 'tone-neutral'}>{item.volume_multiplier > 0 ? `${item.volume_multiplier.toFixed(2)}x` : '-'}<small>Vol x</small></span>
                  <span>{item.value_cr.toFixed(1)}Cr<small>Value</small></span>
                </div>
                <a href={sourceUrl} target="_blank" rel="noreferrer" className="icon-link" aria-label="Open Moneycontrol source">
                  <ExternalLink size={14} />
                </a>
              </div>
            )
          })}
        </div>
      )}
      {activity.length > visibleActivity.length && (
        <p className="market-panel-foot">Ranked newest first, then by volume surge and traded value. Use the limit control to inspect more.</p>
      )}
    </Surface>
  )
}

function LargeDealsPanel({
  deals,
  loading,
  onSelect,
}: {
  deals: NseLargeDeal[]
  loading: boolean
  onSelect: (symbol: string) => void
}) {
  const [rowLimit, setRowLimit] = useState(6)
  const rankedDeals = [...deals].sort((left, right) => {
    const freshness = marketTimestampValue(right.deal_date || right.fetched_at) - marketTimestampValue(left.deal_date || left.fetched_at)
    if (freshness !== 0) return freshness
    return right.value_lakh - left.value_lakh
  })
  const visibleDeals = rankedDeals.slice(0, rowLimit)

  return (
    <Surface className="market-feed-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">NSE Official</span>
          <h2>Bulk and block disclosures</h2>
        </div>
        <div className="market-panel-controls">
          <span className="mini-chip">Top {Math.min(rowLimit, deals.length)} of {deals.length}</span>
          <label className="compact-limit-control">
            <span>Show</span>
            <select value={rowLimit} onChange={(event) => setRowLimit(Number(event.target.value))} aria-label="NSE deal rows to show">
              <option value={6}>6</option>
              <option value={12}>12</option>
              <option value={24}>24</option>
            </select>
          </label>
        </div>
      </div>
      {loading ? (
        <MarketRowsSkeleton count={5} />
      ) : deals.length === 0 ? (
        <MarketEmpty icon={<Landmark size={18} />} label="No NSE deal rows yet" />
      ) : (
        <div className="market-row-list">
          {visibleDeals.map((deal, index) => (
            <div key={`${deal.deal_id}-${index}`} className="market-deal-row">
              <div className="market-row-main">
                <div className="market-row-badges">
                  <span className="stage-pill">{deal.deal_type || 'DEAL'}</span>
                  <span className={deal.side === 'BUY' ? 'stage-pill tone-positive' : deal.side === 'SELL' ? 'stage-pill tone-danger' : 'stage-pill'}>{deal.side || 'NA'}</span>
                  <button type="button" className="symbol-chip" onClick={() => onSelect(deal.symbol)}>{deal.symbol}</button>
                </div>
                <strong>{deal.client_name || 'Unknown client'}</strong>
                <small>{deal.security_name || deal.symbol} · {deal.deal_date}</small>
              </div>
              <div className="market-row-metrics deal-metrics">
                <span>{compactNumber(deal.quantity)}<small>Qty</small></span>
                <span>{currency(deal.price)}<small>Price</small></span>
                <span className="tone-positive">Rs {(deal.value_lakh / 100).toFixed(2)}Cr<small>Value</small></span>
              </div>
              <a href={deal.source_url || 'https://www.nseindia.com/market-data/large-deals'} target="_blank" rel="noreferrer" className="icon-link" aria-label="Open NSE source">
                <ExternalLink size={14} />
              </a>
            </div>
          ))}
        </div>
      )}
      {deals.length > visibleDeals.length && (
        <p className="market-panel-foot">Ranked newest deal date first, then by disclosed value. A disclosure is evidence, not an entry by itself.</p>
      )}
    </Surface>
  )
}

function NewsTapePanel({
  news,
  deals,
  activity,
  pastEvidence = [],
  loading,
  onSelect,
}: {
  news: NewsItem[]
  deals: NseLargeDeal[]
  activity: MarketActivityItem[]
  pastEvidence?: NewsPastEvidence[]
  loading: boolean
  onSelect: (symbol: string) => void
}) {
  const [deskView, setDeskView] = useState<NewsDeskView>('actionable')
  const [sortMode, setSortMode] = useState<NewsDeskSort>('latest')
  const [rowLimit, setRowLimit] = useState(8)

  const uniqueNews = Array.from(
    new Map(news.map((item) => [`${item.article_id}|${item.symbol || 'MARKET'}`, item])).values(),
  )

  const activityBySymbol = new Map<string, MarketActivityItem>()
  activity.forEach((item) => {
    const symbol = item.symbol.trim().toUpperCase()
    if (!symbol) return
    const current = activityBySymbol.get(symbol)
    const currentTime = marketTimestampValue(current?.snapshot_at || current?.fetched_at)
    const nextTime = marketTimestampValue(item.snapshot_at || item.fetched_at)
    if (!current || nextTime > currentTime || (nextTime === currentTime && item.volume_multiplier > current.volume_multiplier)) {
      activityBySymbol.set(symbol, item)
    }
  })

  type DealEvidence = { count: number; valueCr: number; buyCr: number; sellCr: number; latestAt: number }
  const dealsBySymbol = new Map<string, DealEvidence>()
  deals.forEach((deal) => {
    const symbol = deal.symbol.trim().toUpperCase()
    if (!symbol) return
    const valueCr = deal.value_lakh / 100
    const current = dealsBySymbol.get(symbol) ?? { count: 0, valueCr: 0, buyCr: 0, sellCr: 0, latestAt: 0 }
    current.count += 1
    current.valueCr += valueCr
    if (deal.side.toUpperCase() === 'BUY') current.buyCr += valueCr
    if (deal.side.toUpperCase() === 'SELL') current.sellCr += valueCr
    current.latestAt = Math.max(current.latestAt, marketTimestampValue(deal.deal_date || deal.fetched_at))
    dealsBySymbol.set(symbol, current)
  })

  const evidenceFor = (item: NewsItem) => {
    const symbol = item.symbol.trim().toUpperCase()
    return {
      activity: activityBySymbol.get(symbol),
      deal: dealsBySymbol.get(symbol),
    }
  }

  const compareNews = (left: NewsItem, right: NewsItem) => {
    const leftEvidence = evidenceFor(left)
    const rightEvidence = evidenceFor(right)
    const leftTime = marketTimestampValue(left.published_at || left.fetched_at)
    const rightTime = marketTimestampValue(right.published_at || right.fetched_at)
    const leftVolume = leftEvidence.activity?.volume_multiplier ?? 0
    const rightVolume = rightEvidence.activity?.volume_multiplier ?? 0
    const leftDeals = leftEvidence.deal?.valueCr ?? 0
    const rightDeals = rightEvidence.deal?.valueCr ?? 0

    if (sortMode === 'volume' && rightVolume !== leftVolume) return rightVolume - leftVolume
    if (sortMode === 'deals' && rightDeals !== leftDeals) return rightDeals - leftDeals
    if (sortMode === 'impact' && right.impact_score !== left.impact_score) return right.impact_score - left.impact_score
    if (rightTime !== leftTime) return rightTime - leftTime
    if (rightVolume !== leftVolume) return rightVolume - leftVolume
    if (rightDeals !== leftDeals) return rightDeals - leftDeals
    return right.impact_score - left.impact_score
  }

  const linkedNews = uniqueNews.filter((item) => item.symbol).sort(compareNews)
  const actionableNews = linkedNews.filter((item) => {
    const evidence = evidenceFor(item)
    return item.impact_score >= 2.5
      || (evidence.activity?.volume_multiplier ?? 0) >= 2
      || (evidence.deal?.valueCr ?? 0) > 0
  })
  const selectedNews = deskView === 'actionable' ? actionableNews : linkedNews
  const visibleNews = selectedNews.slice(0, rowLimit)
  const marketNews = uniqueNews.filter((item) => !item.symbol).sort(compareNews)
  const visibleMarketNews = marketNews.slice(0, 4)
  const pastEvidenceByKey = new Map(
    pastEvidence.map((item) => [`${item.article_id}|${item.symbol.trim().toUpperCase()}`, item]),
  )

  const renderRows = (rows: NewsItem[]) => (
    <div className="compact-news-list">
      {rows.map((item) => {
        const direction = item.direction || 'NEUTRAL'
        const context = item.reason || item.summary || 'No explanatory context was returned by the news API.'
        const evidence = evidenceFor(item)
        const past = pastEvidenceByKey.get(`${item.article_id}|${item.symbol.trim().toUpperCase()}`)
        const volumeMultiplier = evidence.activity?.volume_multiplier ?? 0
        const dealValueCr = evidence.deal?.valueCr ?? 0
        const isFinancialResult = /result|earnings|quarter|profit/i.test(`${item.title} ${item.reason} ${item.category}`)
        const hasEntryVolume = Boolean(item.symbol) && volumeMultiplier >= 8
        const action = !item.symbol
          ? 'CONTEXT ONLY'
          : direction === 'BEARISH'
            ? 'REVIEW RISK / AVOID FRESH LONG'
            : direction === 'BULLISH' && item.impact_score >= 2.5 && isFinancialResult && hasEntryVolume
              ? 'CHECK FULL ENTRY GATES'
              : 'WATCH — NEEDS CONFIRMATION'
        const actionTone = direction === 'BEARISH'
          ? 'tone-danger'
          : action === 'CHECK FULL ENTRY GATES'
            ? 'tone-positive'
            : 'tone-warning'
        const predictionStatus = past?.prediction_status
          || (item.symbol ? 'Research check only — not a confirmed entry' : 'Market context — no stock prediction')

        return (
          <details key={`${item.article_id}-${item.symbol || 'market'}`} className={item.symbol ? 'compact-news-card compact-news-card-linked' : 'compact-news-card'}>
            <summary className="compact-news-summary">
              <div className="compact-news-main">
                <div className="market-row-badges">
                  {item.symbol ? <span className="symbol-chip-static">{item.symbol}</span> : <span className="stage-pill">MARKET</span>}
                  <span className={`stage-pill ${direction === 'BULLISH' ? 'tone-positive' : direction === 'BEARISH' ? 'tone-danger' : direction === 'WATCH' ? 'tone-warning' : ''}`}>{direction}</span>
                  <span className="stage-pill tone-neutral">{formatSourceName(item.source)}</span>
                </div>
                <strong>{item.title}</strong>
                <small>
                  {marketTimestamp(item.published_at || item.fetched_at)}
                  {item.company_name ? ` · ${item.company_name}` : ''}
                </small>
              </div>
              <div className="compact-news-decision">
                <strong className={actionTone}>{action}</strong>
                <span>
                  {volumeMultiplier > 0 ? `${volumeMultiplier.toFixed(2)}x volume` : 'Volume unavailable'}
                  {' · '}
                  {dealValueCr > 0 ? `Rs ${dealValueCr.toFixed(2)}Cr deals` : 'No matched NSE deal'}
                </span>
                <small>Open evidence</small>
              </div>
            </summary>

            <div className="compact-news-detail">
              <p className="news-context">{context}</p>
              <div className="news-evidence-grid">
                <span className={item.impact_score >= 2.5 ? 'tone-positive' : 'tone-neutral'}><strong>{item.impact_score.toFixed(2)}</strong><small>API impact</small></span>
                <span><strong>{formatConfidence(item.symbol ? item.match_confidence : item.confidence)}</strong><small>{item.symbol ? 'Symbol match' : 'API confidence'}</small></span>
                <span className={volumeMultiplier >= 8 ? 'tone-positive' : 'tone-neutral'}><strong>{volumeMultiplier > 0 ? `${volumeMultiplier.toFixed(2)}x` : 'Unavailable'}</strong><small>Volume surge (8x gate)</small></span>
                <span className={dealValueCr > 0 ? 'tone-positive' : 'tone-neutral'}><strong>{dealValueCr > 0 ? `Rs ${dealValueCr.toFixed(2)}Cr` : 'No match'}</strong><small>NSE deal value</small></span>
              </div>

              {item.symbol ? (
                <div className="news-paper-plan">
                  <span><small>Action</small><strong className={actionTone}>{action}</strong></span>
                  <span><small>Paper target</small><strong>+5%</strong></span>
                  <span><small>Risk stop</small><strong>-5%</strong></span>
                  <span><small>Max horizon</small><strong>10 sessions</strong></span>
                </div>
              ) : (
                <div className="news-context-callout">Context can change market risk, but it does not inherit the stock-entry target or stop.</div>
              )}

              <div className="news-prediction-audit">
                <span><strong>Prediction status</strong>{predictionStatus}</span>
                <span>
                  <strong>Past evidence</strong>
                  {past
                    ? `${past.outcome || 'Evaluated'}${Number.isFinite(past.return_pct) ? ` · ${past.return_pct! >= 0 ? '+' : ''}${past.return_pct!.toFixed(2)}%` : ''}${typeof past.target_hit === 'boolean' ? ` · 5% target ${past.target_hit ? 'hit' : 'not hit'}` : ''}${past.evaluated_at ? ` · ${marketTimestamp(past.evaluated_at)}` : ''}`
                    : 'Unavailable — the current news API does not return post-event performance.'}
                </span>
                <span><strong>Evidence source</strong>{item.model ? `News score: ${item.model}` : 'Scoring model unavailable'}{evidence.activity ? ` · Activity: ${formatActivityMetric(evidence.activity.metric_type)}` : ''}{evidence.deal ? ` · NSE rows: ${evidence.deal.count} (buy Rs ${evidence.deal.buyCr.toFixed(2)}Cr / sell Rs ${evidence.deal.sellCr.toFixed(2)}Cr)` : ''}</span>
              </div>

              <div className="news-detail-actions">
                {item.symbol && <button type="button" className="ghost-button ghost-button-small" onClick={() => onSelect(item.symbol)}>Open {item.symbol}</button>}
                {item.url && (
                  <a href={item.url} target="_blank" rel="noreferrer" className="ghost-button ghost-button-small news-source-action">
                    <span>Open source</span>
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
            </div>
          </details>
        )
      })}
    </div>
  )

  return (
    <Surface className="market-feed-panel">
      <div className="compact-section-head">
        <div>
          <span className="eyebrow">Action Desk · Bounded Shortlist</span>
          <h2>Latest catalysts ranked with volume and NSE deal evidence</h2>
        </div>
        <span className="mini-chip">Showing {visibleNews.length} of {selectedNews.length}</span>
      </div>
      {loading ? (
        <MarketRowsSkeleton count={5} />
      ) : uniqueNews.length === 0 ? (
        <MarketEmpty icon={<Newspaper size={18} />} label="No news rows yet" />
      ) : (
        <div className="ranked-news-sections">
          <div className="news-desk-toolbar">
            <div className="news-view-toggle" role="group" aria-label="News shortlist view">
              <button type="button" className={deskView === 'actionable' ? 'active' : ''} onClick={() => setDeskView('actionable')}>Actionable ({actionableNews.length})</button>
              <button type="button" className={deskView === 'linked' ? 'active' : ''} onClick={() => setDeskView('linked')}>All linked ({linkedNews.length})</button>
            </div>
            <label className="compact-select-control">
              <span>Rank</span>
              <select value={sortMode} onChange={(event) => setSortMode(event.target.value as NewsDeskSort)}>
                <option value="latest">Latest + evidence</option>
                <option value="volume">Volume surge first</option>
                <option value="deals">NSE deal value first</option>
                <option value="impact">API impact first</option>
              </select>
            </label>
            <label className="compact-select-control">
              <span>Show</span>
              <select value={rowLimit} onChange={(event) => setRowLimit(Number(event.target.value))}>
                <option value={5}>5</option>
                <option value={8}>8</option>
                <option value={12}>12</option>
                <option value={20}>20 max</option>
              </select>
            </label>
          </div>

          <section className="news-section">
            <div className="news-section-head">
              <div>
                <strong>{deskView === 'actionable' ? 'Top action checks' : 'All stock-linked headlines'}</strong>
                <small>Newest first by default; volume and deal facts are tie-breakers. Expand only the rows you need.</small>
              </div>
              <span className="mini-chip">Max {rowLimit} rows</span>
            </div>
            {visibleNews.length > 0
              ? renderRows(visibleNews)
              : <MarketEmpty icon={<Target size={18} />} label="No catalysts meet the current evidence filter" />}
            {selectedNews.length > visibleNews.length && (
              <p className="market-panel-foot">{selectedNews.length - visibleNews.length} lower-ranked rows are hidden. Increase “Show” only when you need deeper review.</p>
            )}
          </section>

          {marketNews.length > 0 && (
            <details className="market-context-drawer">
              <summary>
                <span><strong>Market-wide context</strong><small>Separated because these headlines are not stock entry triggers.</small></span>
                <span className="mini-chip">Top {visibleMarketNews.length} of {marketNews.length}</span>
              </summary>
              <div className="market-context-drawer-body">{renderRows(visibleMarketNews)}</div>
            </details>
          )}
        </div>
      )}
    </Surface>
  )
}

function MarketRowsSkeleton({ count }: { count: number }) {
  return (
    <div className="market-row-list">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="market-skeleton-row">
          <div className="skeleton-block skeleton-line-wide" />
          <div className="skeleton-block skeleton-line-short" />
        </div>
      ))}
    </div>
  )
}

function MarketEmpty({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <div className="market-empty">
      <div className="empty-icon-shell">{icon}</div>
      <p>{label}</p>
    </div>
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
  const [pythonBacktestLab, setPythonBacktestLab] = useState<PythonBacktestLabResponse | null>(null)
  const [newsItems, setNewsItems] = useState<NewsItem[]>([])
  const [nseLargeDeals, setNseLargeDeals] = useState<NseLargeDeal[]>([])
  const [marketActivity, setMarketActivity] = useState<MarketActivityItem[]>([])
  const [corporateEvents, setCorporateEvents] = useState<CorporateEvent[]>([])
  const [backtestPredictions, setBacktestPredictions] = useState<BacktestPrediction[]>([])
  const [liveSnapshot, setLiveSnapshot] = useState<LiveStrategySnapshot | null>(null)
  const [liveSocketState, setLiveSocketState] = useState<'connecting' | 'open' | 'closed' | 'error'>('connecting')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(initialRoute.symbol)
  const [detailCandidate, setDetailCandidate] = useState<SwingCandidate | null>(null)
  const [history, setHistory] = useState<SymbolHistoryResponse | null>(null)
  const [historyRange, setHistoryRange] = useState<HistoryRange>('1d')
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [loadingHome, setLoadingHome] = useState(false)
  const [loadingScanner, setLoadingScanner] = useState(false)
  const [loadingMarketHub, setLoadingMarketHub] = useState(false)
  const [runningBacktest, setRunningBacktest] = useState(false)
  const [runningPythonBacktestLab, setRunningPythonBacktestLab] = useState(false)
  const [loadingBacktestDashboard, setLoadingBacktestDashboard] = useState(false)
  const [loadingPythonBacktestLab, setLoadingPythonBacktestLab] = useState(false)
  const [refreshingBacktestCache, setRefreshingBacktestCache] = useState(false)
  const [stagingFresh, setStagingFresh] = useState(false)
  const [refreshingFeatureCache, setRefreshingFeatureCache] = useState(false)
  const [refreshingMarketHub, setRefreshingMarketHub] = useState(false)
  const [marketHubStatus, setMarketHubStatus] = useState('')
  const [marketHubDealError, setMarketHubDealError] = useState('')
  const [marketHubActivityError, setMarketHubActivityError] = useState('')
  const [marketHubEventError, setMarketHubEventError] = useState('')
  const [marketHubSymbol, setMarketHubSymbol] = useState('')
  const [marketHubSource, setMarketHubSource] = useState('')
  const [marketHubDealType, setMarketHubDealType] = useState('')
  const [marketHubActivityMetric, setMarketHubActivityMetric] = useState('')
  const [marketHubHighImpactOnly, setMarketHubHighImpactOnly] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [liveAlertsEnabled, setLiveAlertsEnabled] = useState(() => {
    try {
      return localStorage.getItem(LIVE_ALERTS_STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })
  const notifiedLiveTriggers = useRef(new Set<string>())
  const liveTriggerState = useRef(new Map<string, { lastPrice: number; triggerPrice: number | null }>())
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

  useEffect(() => {
    localStorage.setItem(LIVE_ALERTS_STORAGE_KEY, liveAlertsEnabled ? 'true' : 'false')
  }, [liveAlertsEnabled])

  useEffect(() => {
    if (!liveSnapshot) return

    const nextKeys = new Set<string>()
    liveSnapshot.rows
      .filter((row) => row.source === 'dhan-live')
      .forEach((row) => {
        const key = `${row.strategy_id}:${row.symbol}`
        const trigger = row.trigger_price && row.trigger_price > 0 ? row.trigger_price : null
        const previous = liveTriggerState.current.get(key)
        const crossedTrigger = !!previous && !!trigger && previous.lastPrice < trigger && row.last_price >= trigger
        nextKeys.add(key)

        if (
          liveAlertsEnabled
          && crossedTrigger
          && trigger !== null
          && row.signal_status === 'ENTRY_NOW'
          && 'Notification' in window
          && Notification.permission === 'granted'
        ) {
          const notificationKey = `${key}:${trigger}`
          if (!notifiedLiveTriggers.current.has(notificationKey)) {
            notifiedLiveTriggers.current.add(notificationKey)
            const source = row.trigger_source ? ` (${row.trigger_source})` : ''
            new Notification(`${row.symbol} live trigger hit`, {
              body: `${row.strategy_label}: crossed ${currency(trigger)}${source}. Stop ${currency(row.stop_loss)}, target ${currency(row.target_price)}.`,
              tag: notificationKey,
            })
          }
        }

        liveTriggerState.current.set(key, { lastPrice: row.last_price, triggerPrice: trigger })
      })

    Array.from(liveTriggerState.current.keys()).forEach((key) => {
      if (!nextKeys.has(key)) liveTriggerState.current.delete(key)
    })
  }, [liveAlertsEnabled, liveSnapshot])

  useEffect(() => {
    let reconnectTimer: number | null = null
    let stopped = false
    let socket: WebSocket | null = null

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${window.location.host}/ws/live-strategies`)
      setLiveSocketState('connecting')
      socket.onopen = () => setLiveSocketState('open')
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as LiveStrategySnapshot
          if (payload.event === 'live-strategy-snapshot') {
            setLiveSnapshot(payload)
            setSelectedSymbol((current) => current ?? payload.rows[0]?.symbol ?? null)
          }
        } catch {
          // Ignore malformed feed messages; the next server snapshot will replace it.
        }
      }
      socket.onerror = () => setLiveSocketState('error')
      socket.onclose = () => {
        if (stopped) return
        setLiveSocketState((current) => current === 'error' ? 'error' : 'closed')
        reconnectTimer = window.setTimeout(connect, 5000)
      }
    }

    connect()
    return () => {
      stopped = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

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

  const loadMarketHub = async () => {
    setLoadingMarketHub(true)
    setError('')
    setMarketHubDealError('')
    setMarketHubActivityError('')
    setMarketHubEventError('')
    const cleanSymbol = marketHubSymbol.trim().toUpperCase()
    try {
      const [nextNews, nextDeals, nextActivity, nextEvents, nextPredictions] = await Promise.all([
        getNews({
          symbol: cleanSymbol || undefined,
          source: marketHubSource || undefined,
          min_impact: marketHubHighImpactOnly ? 2.5 : undefined,
          limit: 150,
        }).catch((err) => {
          setError(errorMessage(err))
          return [] as NewsItem[]
        }),
        getNseLargeDeals({
          symbol: cleanSymbol || undefined,
          deal_type: marketHubDealType || undefined,
          limit: 120,
        }).catch((err) => {
          setMarketHubDealError(errorMessage(err))
          return [] as NseLargeDeal[]
        }),
        getMarketActivity({
          symbol: cleanSymbol || undefined,
          metric_type: marketHubActivityMetric || undefined,
          limit: 150,
        }).catch((err) => {
          setMarketHubActivityError(errorMessage(err))
          return [] as MarketActivityItem[]
        }),
        getCorporateEvents({
          symbol: cleanSymbol || undefined,
          lookback_days: 730,
          limit: 20,
        }).catch((err) => {
          setMarketHubEventError(errorMessage(err))
          return [] as CorporateEvent[]
        }),
        getNewsPredictionHistory({ limit: 60 }).then((response) => response.predictions).catch(() => [] as BacktestPrediction[]),
      ])
      startTransition(() => {
        setNewsItems(nextNews)
        setNseLargeDeals(nextDeals)
        setMarketActivity(nextActivity)
        setCorporateEvents(nextEvents)
        setBacktestPredictions(nextPredictions)
      })
    } finally {
      setLoadingMarketHub(false)
    }
  }

  const refreshMarketHubSources = async () => {
    setRefreshingMarketHub(true)
    setError('')
    setMarketHubStatus('')
    setMarketHubDealError('')
    setMarketHubActivityError('')
    setMarketHubEventError('')
    try {
      const newsResult: NewsRefreshResult = await refreshNews()
      let activityResult: MarketActivityRefreshResult | null = null
      try {
        activityResult = await refreshMarketActivity()
      } catch (err) {
        setMarketHubActivityError(errorMessage(err))
      }
      await loadMarketHub()
      if (newsResult.deal_error) {
        setMarketHubDealError(newsResult.deal_error)
      }
      setMarketHubStatus(
        `Fetched ${newsResult.articles} articles, ${newsResult.deals ?? 0} NSE deals, ${activityResult?.rows ?? 0} activity rows`,
      )
      window.setTimeout(() => setMarketHubStatus(''), 7000)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshingMarketHub(false)
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

      if (view === 'market') {
        await loadMarketHub()
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
        await Promise.all([
          loadBacktestDashboard(),
          loadPythonBacktestLab().catch(() => null),
        ])
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRefreshing(false)
    }
  }

  const enableLiveTriggerAlerts = async () => {
    if (!('Notification' in window)) {
      setError('This browser does not support desktop notifications.')
      return
    }
    if (Notification.permission === 'granted') {
      setLiveAlertsEnabled(true)
      return
    }
    if (Notification.permission === 'denied') {
      setError('Browser notifications are blocked. Enable them for localhost in Chrome site settings.')
      return
    }
    const permission = await Notification.requestPermission()
    setLiveAlertsEnabled(permission === 'granted')
    if (permission !== 'granted') {
      setError('Live trigger alerts need notification permission.')
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

  useEffect(() => {
    if (view !== 'market') return
    void loadMarketHub()
  }, [view, marketHubSymbol, marketHubSource, marketHubDealType, marketHubActivityMetric, marketHubHighImpactOnly])

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
    if ((view !== 'backtests' && view !== 'scanner') || backtests || loadingBacktestDashboard) return
    void loadBacktestDashboard()
  }, [view, backtests, loadingBacktestDashboard])

  const loadPythonBacktestLab = async (silent = false) => {
    setLoadingPythonBacktestLab(true)
    if (!silent) setError('')
    try {
      const result = await getPythonBacktestLab()
      setPythonBacktestLab(result)
    } catch (err) {
      if (!silent) setError(errorMessage(err))
    } finally {
      setLoadingPythonBacktestLab(false)
    }
  }

  useEffect(() => {
    if ((view !== 'backtests' && view !== 'scanner') || pythonBacktestLab || loadingPythonBacktestLab || runningPythonBacktestLab) return
    void loadPythonBacktestLab(true)
  }, [view, pythonBacktestLab, loadingPythonBacktestLab, runningPythonBacktestLab])

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

  const runPythonBacktestLabNow = async () => {
    setRunningPythonBacktestLab(true)
    setError('')
    try {
      const result = await runPythonBacktestLab()
      setPythonBacktestLab(result)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRunningPythonBacktestLab(false)
    }
  }

  useEffect(() => {
    if ((!scanner && !liveSnapshot) || paperTrades.length === 0) return
    const liveCandidatesBySymbol = new Map(
      (liveSnapshot?.rows ?? [])
        .filter((row) => row.source === 'dhan-live')
        .map((row) => {
          const candidate = createCandidateFromLiveRow(row)
          return [candidate.symbol, candidate] as const
        }),
    )
    const candidatesBySymbol = new Map((scanner?.candidates ?? []).map((candidate) => [candidate.symbol, candidate]))
    paperTrades
      .filter((trade) => (isTradeStopped(trade) || isTradeTargetHit(trade) || isTradeExpired(trade)) && !autoClosingSymbols.current.has(trade.symbol))
      .forEach((trade) => {
        const candidate = liveCandidatesBySymbol.get(trade.symbol) ?? candidatesBySymbol.get(trade.symbol)
        const stopped = isTradeStopped(trade)
        const targetHit = isTradeTargetHit(trade)
        void closePaperPlan(
          trade,
          stopped ? trade.stop_loss : targetHit ? trade.target_price : trade.current_price ?? candidate?.last_price ?? trade.entry_price,
          stopped ? 'stop-loss' : targetHit ? 'target-hit' : `auto-closed after ${trade.max_sessions} trading sessions`,
        )
      })
  }, [liveSnapshot, scanner, paperTrades])

  const liveCandidates = useMemo(
    () => (liveSnapshot?.rows ?? [])
      .filter((row) => row.source === 'dhan-live')
      .map(createCandidateFromLiveRow),
    [liveSnapshot],
  )
  const selectedLiveCandidate = liveCandidates.find((candidate) => candidate.symbol === selectedSymbol) ?? null
  const broker = liveSnapshot?.broker ?? home?.broker ?? scanner?.broker ?? null
  const updatedAt = liveSnapshot?.updated_at ?? home?.updated_at ?? scanner?.updated_at ?? null
  const selectedHistoricalRow =
    historicalScreener?.rows.find((row) => row.symbol === selectedSymbol) ?? null
  const selectedActionCandidate =
    selectedLiveCandidate ??
    (detailCandidate?.source === 'dhan-live' ? detailCandidate : null) ??
    (paperTrades.find((trade) => trade.symbol === selectedSymbol)
      ? createCandidateFromPaperTrade(paperTrades.find((trade) => trade.symbol === selectedSymbol) as PaperTrade)
      : null)
  const selectedDossierCandidate =
    (detailCandidate?.symbol === selectedSymbol ? detailCandidate : null)
    ?? selectedActionCandidate
    ?? scanner?.candidates.find((candidate) => candidate.symbol === selectedSymbol)
    ?? null
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
                liveSnapshot={liveSnapshot}
                liveSocketState={liveSocketState}
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
            {view === 'market' && (
              <MarketHubView
                news={newsItems}
                deals={nseLargeDeals}
                activity={marketActivity}
                events={corporateEvents}
                predictions={backtestPredictions}
                loading={loadingMarketHub}
                refreshing={refreshingMarketHub}
                status={marketHubStatus}
                dealError={marketHubDealError}
                activityError={marketHubActivityError}
                eventError={marketHubEventError}
                symbol={marketHubSymbol}
                source={marketHubSource}
                dealType={marketHubDealType}
                activityMetric={marketHubActivityMetric}
                highImpactOnly={marketHubHighImpactOnly}
                onSymbolChange={setMarketHubSymbol}
                onSourceChange={setMarketHubSource}
                onDealTypeChange={setMarketHubDealType}
                onActivityMetricChange={setMarketHubActivityMetric}
                onHighImpactChange={setMarketHubHighImpactOnly}
                onLoad={loadMarketHub}
                onRefresh={refreshMarketHubSources}
                onSelect={openStock}
              />
            )}
            {view === 'scanner' && (
              <ScannerView
                scanner={scanner}
                historicalScreener={historicalScreener}
                freshSignals={freshSignals}
                bambooLatest={bambooLatest}
                liveSnapshot={liveSnapshot}
                liveSocketState={liveSocketState}
                liveAlertsEnabled={liveAlertsEnabled}
                selectedSymbol={selectedSymbol}
                onSelect={openStock}
                onStageFresh={stageFreshNow}
                onRefreshCache={refreshFeatureCacheNow}
                onReload={loadScannerData}
                onEnableLiveAlerts={enableLiveTriggerAlerts}
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
                pythonLab={pythonBacktestLab}
                running={runningBacktest}
                pythonRunning={runningPythonBacktestLab}
                loading={loadingBacktestDashboard}
                pythonLoading={loadingPythonBacktestLab}
                refreshingCache={refreshingBacktestCache}
                onLoad={loadBacktestDashboard}
                onLoadPythonLab={() => void loadPythonBacktestLab()}
                onRefreshCache={refreshBacktestCacheNow}
                onRun={runBacktestNow}
                onRunPythonLab={runPythonBacktestLabNow}
              />
            )}
            {view === 'settings' && <SettingsView broker={broker} accounts={accounts} />}
            {view === 'stock' && (
              <StockDetailView
                candidate={selectedDossierCandidate}
                historicalRow={selectedHistoricalRow}
                history={history}
                historyRange={historyRange}
                watchlisted={!!selectedDossierCandidate && watchSymbols.has(selectedDossierCandidate.symbol)}
                queued={!!selectedDossierCandidate && queueSymbols.has(selectedDossierCandidate.symbol)}
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
