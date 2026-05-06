import { useDeferredValue, useEffect, useMemo, useRef, useState, startTransition, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Bookmark,
  BrainCircuit,
  BriefcaseBusiness,
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
  getHistoricalScreener,
  closePaperTrade,
  deletePaperTrade,
  getPaperBudget,
  getPaperTrades,
  getSwingCandidate,
  getSwingHistory,
  getSwingHome,
  getSwingScanner,
  runBacktest,
  savePaperTrade,
  type BrokerAccountSnapshot,
  type BrokerStatus,
  type BacktestDashboardResponse,
  type HistoricalScreenerResponse,
  type HistoricalScreenerRow,
  type LiveSignal,
  type PaperTrade,
  type PaperBudget,
  type SymbolHistoryResponse,
  type SetupMix,
  type SwingCandidate,
  type SwingHomeResponse,
  type SwingScannerResponse,
} from './api'

type View = 'home' | 'scanner' | 'watchlist' | 'portfolio' | 'backtests' | 'research' | 'settings' | 'stock'
type HistoryRange = '3m' | '6m' | '1y' | '3y' | '5y'

interface NavItem {
  id: View
  label: string
  icon: LucideIcon
  blurb: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Home', icon: LayoutDashboard, blurb: 'Regime and opportunity pulse' },
  { id: 'scanner', label: 'Scanner', icon: Radar, blurb: 'Find fresh swing setups' },
  { id: 'watchlist', label: 'Watchlist', icon: Bookmark, blurb: 'Organize stocks to monitor' },
  { id: 'portfolio', label: 'Paper Desk', icon: WalletCards, blurb: 'Stage paper trade plans' },
  { id: 'backtests', label: 'Backtests', icon: BarChart3, blurb: 'Strategy returns and analytics' },
  { id: 'research', label: 'Research', icon: BrainCircuit, blurb: 'Setup mix and process notes' },
  { id: 'settings', label: 'Settings', icon: Compass, blurb: 'Dhan status and API direction' },
]

const WATCHLIST_STORAGE_KEY = 'swing-watchlist'
const PAPER_CAPITAL_PER_STOCK = 50000

type Tone = 'positive' | 'warning' | 'danger' | 'neutral'

interface ResearchStrategyCard {
  name: string
  status: string
  tone: Tone
  rule: string
  trades: number
  monthly: number
  winRate: number
  profitFactor: number
  expectancy: number
  oos: string
  warning: string
}

const RESEARCH_STRATEGIES: ResearchStrategyCard[] = [
  {
    name: 'ATR Stretch Liquid Only',
    status: 'Paper-test watchlist',
    tone: 'warning',
    rule: 'Close above SMA200, more than 2.5 ATR below EMA20, RSI14 below 35, price at least 50, 20-day volume at least 100k; enter next session open.',
    trades: 1881,
    monthly: 32.9,
    winRate: 54.01,
    profitFactor: 1.464,
    expectancy: 1.042,
    oos: 'Second-pass OOS passed narrowly: 384 trades, PF 1.061, expectancy +0.144%.',
    warning: 'Paper-test only. 2026 remains weak: 79 trades, PF 0.750, expectancy -0.839%.',
  },
  {
    name: 'ATR Stretch Reversal',
    status: 'Base variant rejected',
    tone: 'danger',
    rule: 'Close more than 2.5 ATR below EMA20 while still above SMA200, RSI14 below 35; enter next session open.',
    trades: 2560,
    monthly: 44.8,
    winRate: 51.76,
    profitFactor: 1.348,
    expectancy: 0.839,
    oos: 'Full-period strong, but 2026 failed: 101 trades, PF 0.538, expectancy -1.764%.',
    warning: 'Use the liquid-only paper-test variant instead of this broad version.',
  },
  {
    name: 'NR7 Breakout Close',
    status: 'Rejected after second pass',
    tone: 'danger',
    rule: 'Narrowest 7-day range, close above prior 20-day high, relative volume above 1.0; enter next session open.',
    trades: 1450,
    monthly: 22.9,
    winRate: 43.79,
    profitFactor: 1.12,
    expectancy: 0.318,
    oos: 'Second-pass NR7 filters did not pass validation and walk-forward gates.',
    warning: 'Do not show this as a tradable signal yet.',
  },
  {
    name: 'Broad Trend/Pullback Setups',
    status: 'Rejected for now',
    tone: 'danger',
    rule: 'EMA pullbacks, 20/55-day breakouts, squeeze breakouts, gap-down reversals, RSI2 reversion.',
    trades: 100000,
    monthly: 200,
    winRate: 40.0,
    profitFactor: 1.0,
    expectancy: 0,
    oos: 'Most collapsed in 2025-26 after fees and slippage.',
    warning: 'Do not show these as tradable signals without new filters.',
  },
]

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
    constraint: 'Good reference point for deciding what should stay in the swing workspace versus the research worker.',
    docUrl: 'https://docs.dhanhq.co/',
  },
]

const HISTORY_RANGES: HistoryRange[] = ['3m', '6m', '1y', '3y', '5y']

function currency(value: number) {
  return `Rs ${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
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
  return candidate.live_signal?.status === 'ENTRY_NOW'
}

function signalClass(status: LiveSignal['status']) {
  return `signal-${String(status).toLowerCase().replace(/_/g, '-')}`
}

function parseRouteHash(): { view: View; symbol: string | null } {
  const path = window.location.pathname.replace(/^\/+/, '')
  const [viewPart, symbolPart] = path.split('/')
  const knownViews: View[] = ['home', 'scanner', 'watchlist', 'portfolio', 'backtests', 'research', 'settings', 'stock']
  const view = knownViews.includes(viewPart as View) ? (viewPart as View) : 'home'
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

function createCandidateFromHistoricalRow(row: HistoricalScreenerRow): SwingCandidate {
  const stopLoss = Number((row.sma50 * 0.99).toFixed(2))
  const targetPrice = Number((row.close * 1.1).toFixed(2))
  const strategyLabel = row.strategy_label && row.strategy_label !== 'Unlinked Screen' ? row.strategy_label : row.setup_family
  return {
    symbol: row.symbol,
    company_name: row.symbol,
    setup_family: strategyLabel,
    bias: 'Long',
    score: row.score,
    confidence: row.score >= 88 ? 'High Conviction' : row.score >= 78 ? 'Actionable' : 'Watchlist',
    regime_fit: Math.min(95, Math.max(55, row.score - 4)),
    risk_reward: Number((((targetPrice - row.close) / Math.max(row.close - stopLoss, 0.01))).toFixed(2)),
    last_price: row.close,
    day_change_pct: 0,
    open_gap_pct: 0,
    distance_to_high_pct: row.distance_to_20d_high_pct,
    liquidity_bucket: row.avg_volume20 >= 1_000_000 ? 'LARGE' : row.avg_volume20 >= 250_000 ? 'MID' : 'SMALL',
    entry_zone: `Rs ${(row.close * 0.995).toFixed(2)} - Rs ${(row.close * 1.01).toFixed(2)}`,
    stop_loss: stopLoss,
    target_price: targetPrice,
    expected_hold: row.setup_family === 'Pullback To 20 DMA' ? '5-10 sessions' : '8-15 sessions',
    thesis: `${row.symbol} is ranking well in the parquet screener under ${strategyLabel} because its trend structure, position vs highs, and participation profile still look constructive.`,
    reasons: [
      `${row.symbol} is ${row.distance_to_20d_high_pct.toFixed(2)}% away from the 20-day high.`,
      `Volume ratio is ${row.volume_ratio.toFixed(2)}x against the 20-day average.`,
      `Trend profile is ${row.trend_label}; strategy lab status is ${row.strategy_status}.`,
    ],
    risks: [
      `If price loses the 50-day structure near Rs ${stopLoss.toFixed(2)}, the setup quality drops fast.`,
      'Historical trend strength does not guarantee a clean follow-through in the next few sessions.',
    ],
    source: 'parquet-screener',
    live_signal: defaultLiveSignal({
      status: row.strategy_status === 'Research' ? 'WAIT_FOR_TRIGGER' : row.strategy_status === 'Watch' ? 'WATCH' : 'NO_TRADE',
      label: row.strategy_status === 'Research' ? 'Needs Live Trigger' : row.strategy_status === 'Watch' ? 'Watch Only' : 'No Trade',
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

function maxSessionsFromHold(text: string) {
  const matches = text.match(/\d+/g)?.map(Number) ?? []
  return Math.max(1, matches.length ? Math.max(...matches) : 10)
}

function sessionsElapsed(plannedAt?: string | null) {
  if (!plannedAt) return 0
  const planned = new Date(plannedAt)
  if (Number.isNaN(planned.getTime())) return 0
  const now = new Date()
  const oneDay = 24 * 60 * 60 * 1000
  return Math.max(0, Math.floor((now.getTime() - planned.getTime()) / oneDay))
}

function isTradeExpired(trade: PaperTrade) {
  return trade.enabled === 1 && sessionsElapsed(trade.planned_at) >= trade.max_sessions
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
            title={paperReady ? 'Send this live entry to Paper Desk' : liveSignal.reason}
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
          <BrainCircuit size={18} />
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
  watchlistCount,
  paperCount,
  selectedSymbol,
  onSelect,
}: {
  home: SwingHomeResponse | null
  watchlistCount: number
  paperCount: number
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
}) {
  if (!home) return <PageSkeleton />

  const entryNowCount = home.top_candidates.filter((candidate) => candidate.live_signal.status === 'ENTRY_NOW').length

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
          <CandidateStat label="Scanner" value={String(home.scanner_count)} tone="positive" />
          <CandidateStat label="Enter Now" value={String(entryNowCount)} tone={entryNowCount > 0 ? 'positive' : 'warning'} />
          <CandidateStat label="Broker" value={home.broker.state.toUpperCase()} tone={stateTone(home.broker.state)} />
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
              <span className="eyebrow">Top Opportunities</span>
              <h2>Best names right now</h2>
            </div>
            <div className="mini-chip">
              <Activity size={14} />
              <span>{Math.min(home.top_candidates.length, 5)} shown</span>
            </div>
          </div>
          <div className="home-candidate-list">
            {home.top_candidates.slice(0, 5).map((candidate, index) => (
              <button
                key={candidate.symbol}
                type="button"
                onClick={() => onSelect(candidate.symbol)}
                className={candidate.symbol === selectedSymbol ? 'home-candidate-row home-candidate-row-active' : 'home-candidate-row'}
              >
                <span className="rank-cell">{index + 1}</span>
                <span className="home-symbol-cell">
                  <strong>{candidate.symbol}</strong>
                  <em>{candidate.company_name}</em>
                </span>
                <span className="home-setup-cell">{candidate.setup_family}</span>
                <span className="home-price-cell">{currency(candidate.last_price)}</span>
                <span className={`home-change-cell ${candidate.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'}`}>
                  {candidate.day_change_pct >= 0 ? '+' : ''}{candidate.day_change_pct.toFixed(2)}%
                </span>
                <span className="home-score-cell">{candidate.score}</span>
                <span className={`home-signal-cell ${signalClass(candidate.live_signal.status)}`}>{candidate.live_signal.label}</span>
              </button>
            ))}
          </div>
        </Surface>

        <div className="home-side-stack">
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
  selectedSymbol,
  onSelect,
}: {
  scanner: SwingScannerResponse | null
  historicalScreener: HistoricalScreenerResponse | null
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
}) {
  const [search, setSearch] = useState('')
  const [familyFilter, setFamilyFilter] = useState<string>('All')
  const [page, setPage] = useState(1)
  const pageSize = 12
  const deferredSearch = useDeferredValue(search)

  const families = useMemo(() => {
    const options = new Set<string>(['All'])
    historicalScreener?.rows.forEach((row) => options.add(row.setup_family))
    return Array.from(options)
  }, [historicalScreener])

  const filtered = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase()
    return (historicalScreener?.rows ?? []).filter((row) => {
      const matchesFamily = familyFilter === 'All' || row.setup_family === familyFilter
      const matchesSearch =
        !term ||
        row.symbol.toLowerCase().includes(term)
        || row.strategy_label.toLowerCase().includes(term)
        || row.strategy_status.toLowerCase().includes(term)
      return matchesFamily && matchesSearch
    })
  }, [deferredSearch, familyFilter, historicalScreener])

  useEffect(() => {
    setPage(1)
  }, [deferredSearch, familyFilter])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageRows = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  if (!scanner || !historicalScreener) return <PageSkeleton />

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head scanner-toolbar">
          <div>
            <span className="eyebrow">Historical Screener</span>
              <h2>Backtested rules plus live Dhan overlay drive the entry signal</h2>
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
          </div>
        </div>

        <div className="scanner-summary-strip">
          <CandidateStat label="Rows From History" value={String(historicalScreener.total_rows)} />
          <CandidateStat label="Scanner Candidates" value={String(scanner.total_candidates)} tone="positive" />
          <CandidateStat label="Data As Of" value={compactDate(historicalScreener.updated_at)} />
          <CandidateStat label="Quote Layer" value={scanner.live_data ? 'Live' : 'Historical'} tone={scanner.live_data ? 'positive' : 'warning'} />
        </div>

        <div className="filter-strip">
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
                <p>No candidates matched the current filters.</p>
              </div>
            )}
          </div>
          {filtered.length > 0 && (
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
                          title={ready ? 'Send this live entry to Paper Desk' : signal.reason}
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

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Paper Desk</span>
            <h2>Paper positions</h2>
          </div>
          <div className="hero-actions">
            <CandidateStat label="Open" value={String(paperTrades.length)} />
            <CandidateStat label="Budget" value={currency(totalBudget)} />
            <CandidateStat label="Allocated" value={currency(allocatedBudget)} />
            <CandidateStat label="Available" value={currency(availableBudget)} tone={availableBudget >= 0 ? 'positive' : 'danger'} />
            <CandidateStat label={pnlLabel} value={currency(totalPnl)} tone={hasLivePrices ? (totalPnl >= 0 ? 'positive' : 'danger') : 'warning'} />
            <CandidateStat label="Closed P&L" value={currency(realizedPnl)} tone={realizedPnl >= 0 ? 'positive' : 'danger'} />
          </div>
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

        <Surface className="inner-surface budget-panel">
          <div>
            <span className="eyebrow">Budget</span>
            <h3>Paper trading budget</h3>
          </div>
          <div>
            <span className="micro-label">Total Budget</span>
            <strong>{currency(totalBudget)}</strong>
          </div>
          <div>
            <span className="micro-label">Allocated</span>
            <strong>{currency(allocatedBudget)}</strong>
          </div>
          <div>
            <span className="micro-label">Available</span>
            <strong className={availableBudget >= 0 ? 'tone-positive' : 'tone-danger'}>{currency(availableBudget)}</strong>
          </div>
        </Surface>

        {paperTrades.length === 0 ? (
          <div className="portfolio-empty">
            <BriefcaseBusiness size={20} />
            <p>No open paper positions. Add a stock from Scanner or Watchlist.</p>
          </div>
        ) : (
          <Surface className="inner-surface paper-table-surface">
            <div className="paper-table">
              <div className="paper-table-row paper-table-head">
                <span>Stock</span>
                <span>Qty</span>
                <span>Entry</span>
                <span>{priceColumnLabel}</span>
                <span>Budget</span>
                <span>Value</span>
                <span>{hasLivePrices ? 'P&L' : 'Ref P&L'}</span>
                <span>Action</span>
              </div>
              {paperTrades.map((trade) => {
                const pnl = trade.unrealized_pnl
                return (
                  <div key={trade.symbol} className="paper-table-row">
                    <button type="button" className="paper-symbol-cell" onClick={() => onSelect(trade.symbol)}>
                      <strong>{trade.symbol}</strong>
                      <span>{trade.company_name}</span>
                    </button>
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
                    <strong>{currency(trade.entry_price)}</strong>
                    <strong>
                      {currency(trade.current_price)}
                      <small>{paperQuoteLabel(trade)}</small>
                    </strong>
                    <div>
                      <input
                        key={`${trade.symbol}-budget-${Math.round(trade.capital_allocated)}`}
                        className="paper-input amount-input"
                        type="number"
                        min={trade.entry_price}
                        step="100"
                        defaultValue={Math.round(trade.capital_allocated)}
                        onBlur={(event) => {
                          const capital = Math.max(trade.entry_price, Number(event.currentTarget.value) || trade.capital_allocated)
                          const quantity = quantityForCapital(trade.entry_price, capital)
                          if (Math.round(capital) !== Math.round(trade.capital_allocated) || quantity !== trade.quantity) {
                            onUpdate(trade, { capital_allocated: capital, quantity })
                          }
                        }}
                      />
                      <small>qty from budget</small>
                    </div>
                    <strong>{currency(trade.current_value)}</strong>
                    <strong className={pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {currency(pnl)}
                      <small>{trade.unrealized_pnl_pct.toFixed(2)}%</small>
                    </strong>
                    <div className="paper-actions">
                      <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(trade.symbol)}>
                        Remove
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </Surface>
        )}
        {closedTrades.length > 0 && (
          <Surface className="inner-surface closed-summary">
            <div className="lane-head">
              <div>
                <span className="eyebrow">Closed Summary</span>
                <h3>What happened after the paper session ended</h3>
              </div>
              <div className="mini-chip">
                <ListTodo size={14} />
                <span>{closedTrades.length} closed</span>
              </div>
            </div>
            <div className="closed-trade-list">
              {closedTrades.slice(0, 8).map((trade) => {
                const pnl = trade.realized_pnl
                const exit = trade.exit_price ?? trade.entry_price
                return (
                  <div key={`${trade.symbol}-${trade.closed_at ?? trade.close_reason}`} className="closed-trade-row">
                    <div>
                      <strong>{trade.symbol}</strong>
                      <span>{trade.close_reason || 'closed'}{trade.closed_at ? ` | ${compactDate(trade.closed_at)}` : ''}</span>
                    </div>
                    <span>{trade.quantity} qty</span>
                    <span>{currency(trade.entry_price)} to {currency(exit)}</span>
                    <strong className={pnl >= 0 ? 'tone-positive' : 'tone-danger'}>
                      {currency(pnl)} ({tradeReturnPct(pnl, trade).toFixed(2)}%)
                    </strong>
                  </div>
                )
              })}
            </div>
          </Surface>
        )}
      </Surface>
    </div>
  )
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

function BacktestsView({
  dashboard,
  running,
  onRun,
}: {
  dashboard: BacktestDashboardResponse | null
  running: boolean
  onRun: () => void
}) {
  const [selectedStrategy, setSelectedStrategy] = useState<string>('near-52w-high-v1')
  const [selectedYear, setSelectedYear] = useState<number | null>(null)

  useEffect(() => {
    if (!dashboard?.summaries.length) return
    const profitableSummaries = dashboard.summaries.filter((summary) => summary.total_pnl > 0)
    const visibleSummaries = profitableSummaries.length ? profitableSummaries : dashboard.summaries
    if (!visibleSummaries.some((summary) => summary.strategy_id === selectedStrategy)) {
      setSelectedStrategy(visibleSummaries[0].strategy_id)
    }
  }, [dashboard, selectedStrategy])

  if (!dashboard) return <PageSkeleton />

  const profitableSummaries = dashboard.summaries.filter((summary) => summary.total_pnl > 0)
  const visibleSummaries = profitableSummaries.length ? profitableSummaries : dashboard.summaries
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
  const removedStrategies = dashboard.summaries.length - visibleSummaries.length

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Strategy Lab</span>
            <h2>Backtested swing strategy returns from parquet history</h2>
          </div>
          <div className="hero-actions">
            <button type="button" className="primary-button" onClick={onRun} disabled={running}>
              <RefreshCw size={14} className={running ? 'spin' : ''} />
              <span>{running ? 'Running Backtest' : 'Run Backtest'}</span>
            </button>
            <CandidateStat label="Run" value={dashboard.run_id.replace('watchlist-swing-', '')} />
            <CandidateStat label="Profitable" value={String(visibleSummaries.length)} />
            <CandidateStat label="Removed" value={String(Math.max(removedStrategies, 0))} tone={removedStrategies > 0 ? 'warning' : 'neutral'} />
            <CandidateStat label="Trades Stored" value={String(visibleSummaries.reduce((sum, item) => sum + item.total_trades, 0).toLocaleString('en-IN'))} />
          </div>
        </div>

        <div className="backtest-run-note">
          <Database size={16} />
          <span>Runs use the current parquet files and active watchlist. Positive systems enter the shortlist; weak systems stay in the scorecard as rejected research.</span>
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
                <span>Positive Months</span>
                <span>Profit Factor</span>
                <span>P&L</span>
              </div>
              {dashboard.diagnostics.map((row) => {
                const canOpen = visibleSummaries.some((summary) => summary.strategy_id === row.strategy_id)
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
                    <span>{row.positive_months_pct.toFixed(2)}%</span>
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
                  <CandidateStat label="Time Exits" value={selectedSummary.time_exits.toLocaleString('en-IN')} tone="warning" />
                  <CandidateStat label="Worst Day" value={dayQuality ? currency(dayQuality.worst_day) : 'N/A'} tone="danger" />
                  <CandidateStat label="Best Day" value={dayQuality ? currency(dayQuality.best_day) : 'N/A'} tone="positive" />
                  <CandidateStat label="Days Tested" value={dayQuality ? String(dayQuality.trading_days) : 'N/A'} />
                </div>
              </Surface>
            </div>

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
              <BacktestSymbolList title="Best Stocks" rows={winners} positive />
              <BacktestSymbolList title="Worst Stocks To Filter" rows={losers} />
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
      </Surface>
    </div>
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
      </div>
    </Surface>
  )
}

function ResearchView({ setupMix }: { setupMix: SetupMix[] }) {
  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Research Layer</span>
            <h2>What the swing engine is currently emphasizing</h2>
          </div>
          <div className="mini-chip">
            <Database size={14} />
            <span>Nightly Python worker is still the right place for full calibration and backtests</span>
          </div>
        </div>

        <div className="strategy-research-panel">
          <div className="compact-section-head">
            <div>
              <span className="eyebrow">Validated Research Output</span>
              <h2>What is actually worth showing from the parquet study</h2>
            </div>
            <div className="mini-chip">
              <ShieldCheck size={14} />
              <span>No 100% setup found with enough sample</span>
            </div>
          </div>
          <div className="strategy-research-grid">
            {RESEARCH_STRATEGIES.map((strategy) => (
              <Surface key={strategy.name} className={`inner-surface strategy-research-card tone-${strategy.tone}`}>
                <div className="strategy-research-head">
                  <div>
                    <span className="micro-label">{strategy.status}</span>
                    <strong>{strategy.name}</strong>
                  </div>
                  <span className={`status-dot tone-${strategy.tone}`} />
                </div>
                <p>{strategy.rule}</p>
                <div className="strategy-metric-grid">
                  <CandidateStat label="Trades" value={strategy.trades >= 100000 ? 'Many' : strategy.trades.toLocaleString('en-IN')} />
                  <CandidateStat label="Monthly" value={strategy.monthly >= 200 ? 'Too many' : strategy.monthly.toFixed(1)} />
                  <CandidateStat label="Win" value={`${strategy.winRate.toFixed(2)}%`} tone={strategy.winRate >= 50 ? 'positive' : 'warning'} />
                  <CandidateStat label="PF" value={strategy.profitFactor.toFixed(2)} tone={strategy.profitFactor > 1.1 ? 'positive' : strategy.profitFactor > 1 ? 'warning' : 'danger'} />
                  <CandidateStat label="Expectancy" value={`${strategy.expectancy >= 0 ? '+' : ''}${strategy.expectancy.toFixed(3)}%`} tone={strategy.expectancy > 0 ? 'positive' : 'danger'} />
                </div>
                <div className="research-verdict">
                  <strong>{strategy.oos}</strong>
                  <span>{strategy.warning}</span>
                </div>
              </Surface>
            ))}
          </div>
        </div>

        <div className="research-grid">
          {setupMix.map((mix) => (
            <Surface key={mix.family} className="inner-surface research-card">
              <span className="micro-label">{mix.family}</span>
              <strong>{mix.count} active names</strong>
              <p>Average score {mix.avg_score.toFixed(1)} across the current live scanner cut.</p>
            </Surface>
          ))}
        </div>

        <div className="research-notes">
          <div>
            <ShieldCheck size={16} />
            <span>Current build uses explainable rule scoring, not a black-box model.</span>
          </div>
          <div>
            <TrendingUp size={16} />
            <span>The next real upgrade is historical setup-family validation, not just more UI polish.</span>
          </div>
          <div>
            <BarChart3 size={16} />
            <span>Watchlist and paper-trade organization are now split cleanly from raw scanner discovery.</span>
          </div>
        </div>
      </Surface>
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
        <div className="research-grid">
          <Surface className="inner-surface research-card">
            <span className="micro-label">Market Quote</span>
            <strong>`/marketfeed/ltp`, `/marketfeed/ohlc`, `/marketfeed/quote`</strong>
            <p>Best for scanner snapshots. Official docs say quote requests support up to 1000 instruments and are rate-limited to 1 request per second.</p>
          </Surface>
          <Surface className="inner-surface research-card">
            <span className="micro-label">Funds & Margin</span>
            <strong>`/fundlimit`, `/margincalculator`</strong>
            <p>Use this for paper-trade buying power, position sizing hints, and eventually a cleaner capital allocator.</p>
          </Surface>
          <Surface className="inner-surface research-card">
            <span className="micro-label">Portfolio & Positions</span>
            <strong>`/holdings`, `/positions`, `/positions/convert`</strong>
            <p>These are the right official APIs for account snapshots and live position awareness beside our paper-trade workflow.</p>
          </Surface>
          <Surface className="inner-surface research-card">
            <span className="micro-label">Orders</span>
            <strong>`/orders`, `/trades`</strong>
            <p>Official Dhan docs note order APIs need static IP whitelisting, so paper trading should stay primary until we’re ready for that constraint.</p>
          </Surface>
          {DHAN_API_SURFACES.map((surface) => (
            <Surface key={surface.docUrl} className="inner-surface research-card">
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
  const [accounts, setAccounts] = useState<BrokerAccountSnapshot[]>([])
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([])
  const [paperBudget, setPaperBudget] = useState<PaperBudget | null>(null)
  const [backtests, setBacktests] = useState<BacktestDashboardResponse | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(initialRoute.symbol)
  const [detailCandidate, setDetailCandidate] = useState<SwingCandidate | null>(null)
  const [history, setHistory] = useState<SymbolHistoryResponse | null>(null)
  const [historyRange, setHistoryRange] = useState<HistoryRange>('1y')
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [runningBacktest, setRunningBacktest] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const autoClosingSymbols = useRef(new Set<string>())
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

  const refreshAll = async () => {
    setRefreshing(true)
    setError('')
    try {
      const [homeData, scannerData, screenerData, accountData, paperTradeData, paperBudgetData, backtestData] = await Promise.all([
        getSwingHome(),
        getSwingScanner(28),
        getHistoricalScreener({ limit: 80, setup: 'all', minPrice: 80, minAvgVolume: 100000 }),
        getBrokerAccounts().catch(() => []),
        getPaperTrades().catch(() => []),
        getPaperBudget().catch(() => null),
        getBacktestDashboard().catch(() => null),
      ])
      startTransition(() => {
        setHome(homeData)
        setScanner(scannerData)
        setHistoricalScreener(screenerData)
        setAccounts(accountData)
        setPaperTrades(paperTradeData)
        setPaperBudget(paperBudgetData)
        setBacktests(backtestData)
        const defaultSymbol =
          selectedSymbol ??
          screenerData.rows[0]?.symbol ??
          homeData.top_candidates[0]?.symbol ??
          scannerData.candidates[0]?.symbol ??
          watchlist[0]?.symbol ??
          paperTradeData.find((trade) => trade.enabled === 1)?.symbol ??
          null
        setSelectedSymbol(defaultSymbol)
      })
    } catch (err) {
      setError(String(err))
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    refreshAll()
    const timer = setInterval(refreshAll, 60_000)
    return () => clearInterval(timer)
  }, [])

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
      setError(`${candidate.symbol} is ${candidate.live_signal.label}; Paper Desk only accepts Enter Now signals.`)
      return
    }
    const quantity = quantityForCapital(candidate.last_price)
    const tradePayload = {
      symbol: candidate.symbol,
      company_name: candidate.company_name,
      setup_family: candidate.setup_family,
      bias: candidate.bias,
      entry_price: candidate.last_price,
      quantity,
      max_sessions: maxSessionsFromHold(candidate.expected_hold),
      capital_allocated: PAPER_CAPITAL_PER_STOCK,
      stop_loss: candidate.stop_loss,
      target_price: candidate.target_price,
      expected_hold: candidate.expected_hold,
      thesis: candidate.thesis,
      notes: candidate.reasons.join(' '),
    }
    setWatchlist((current) => upsertCandidate(current, candidate))
    try {
      const saved = await savePaperTrade(tradePayload)
      setPaperTrades((current) => [saved, ...current.filter((trade) => trade.symbol !== saved.symbol)])
    } catch (err) {
      setError(String(err))
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
      setError(String(err))
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
      setError(String(err))
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
      setError(String(err))
    }
  }

  const runBacktestNow = async () => {
    setRunningBacktest(true)
    setError('')
    try {
      const result = await runBacktest()
      setBacktests(result.dashboard)
    } catch (err) {
      setError(String(err))
    } finally {
      setRunningBacktest(false)
    }
  }

  useEffect(() => {
    if (!scanner || paperTrades.length === 0) return
    const candidatesBySymbol = new Map(scanner.candidates.map((candidate) => [candidate.symbol, candidate]))
    paperTrades
      .filter((trade) => isTradeExpired(trade) && !autoClosingSymbols.current.has(trade.symbol))
      .forEach((trade) => {
        const candidate = candidatesBySymbol.get(trade.symbol)
        void closePaperPlan(trade, trade.current_price ?? candidate?.last_price ?? trade.entry_price, `auto-closed after ${trade.max_sessions} sessions`)
      })
  }, [scanner, paperTrades])

  const broker = home?.broker ?? scanner?.broker ?? null
  const updatedAt = home?.updated_at ?? scanner?.updated_at ?? null
  const selectedHistoricalRow =
    historicalScreener?.rows.find((row) => row.symbol === selectedSymbol) ?? null
  const selectedActionCandidate =
    detailCandidate ??
    (selectedHistoricalRow ? createCandidateFromHistoricalRow(selectedHistoricalRow) : null) ??
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
              <span>{refreshing ? 'Refreshing' : 'Refresh'}</span>
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
                watchlistCount={watchlist.length}
                paperCount={queueSymbols.size}
                selectedSymbol={selectedSymbol}
                onSelect={openStock}
              />
            )}
            {view === 'scanner' && (
              <ScannerView
                scanner={scanner}
                historicalScreener={historicalScreener}
                selectedSymbol={selectedSymbol}
                onSelect={openStock}
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
            {view === 'backtests' && <BacktestsView dashboard={backtests} running={runningBacktest} onRun={runBacktestNow} />}
            {view === 'research' && <ResearchView setupMix={home?.setup_mix ?? []} />}
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
