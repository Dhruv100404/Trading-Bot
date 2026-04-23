import { useDeferredValue, useEffect, useMemo, useState, startTransition, type ReactNode } from 'react'
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
  Clock3,
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
  getSwingCandidate,
  getSwingHome,
  getSwingScanner,
  type BrokerAccountSnapshot,
  type BrokerStatus,
  type SetupMix,
  type SwingCandidate,
  type SwingHomeResponse,
  type SwingScannerResponse,
} from './api'

type View = 'home' | 'scanner' | 'watchlist' | 'portfolio' | 'research' | 'settings'

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
  { id: 'research', label: 'Research', icon: BrainCircuit, blurb: 'Setup mix and process notes' },
  { id: 'settings', label: 'Settings', icon: Compass, blurb: 'Dhan status and API direction' },
]

const WATCHLIST_STORAGE_KEY = 'swing-watchlist'
const PAPER_STORAGE_KEY = 'swing-paper-queue'

type Tone = 'positive' | 'warning' | 'danger' | 'neutral'

interface StageMeta {
  label: string
  tone: Tone
  note: string
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
    constraint: 'Good reference point for deciding what should stay in the swing workspace versus the research worker.',
    docUrl: 'https://docs.dhanhq.co/',
  },
]

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

function upsertCandidate(list: SwingCandidate[], candidate: SwingCandidate) {
  const next = list.filter((item) => item.symbol !== candidate.symbol)
  return [candidate, ...next]
}

function removeCandidate(list: SwingCandidate[], symbol: string) {
  return list.filter((item) => item.symbol !== symbol)
}

function riskPerShare(candidate: SwingCandidate) {
  return Math.max(candidate.last_price - candidate.stop_loss, 0)
}

function watchlistStage(candidate: SwingCandidate, queued: boolean): StageMeta {
  if (queued) {
    return {
      label: 'Paper Queued',
      tone: 'positive',
      note: 'Already promoted into the paper desk, so this stays on the monitor list only.',
    }
  }
  if (candidate.score >= 85 && candidate.risk_reward >= 1.6) {
    return {
      label: 'Paper Ready',
      tone: 'positive',
      note: 'The current score and risk box are clean enough to stage a paper trade now.',
    }
  }
  if (candidate.score >= 75) {
    return {
      label: 'Needs Confirmation',
      tone: 'warning',
      note: 'Strong enough to track closely, but still wants one more confirmation before promotion.',
    }
  }
  return {
    label: 'Early Watch',
    tone: 'neutral',
    note: 'Worth tracking, but not yet strong enough to move into paper planning.',
  }
}

function paperStage(candidate: SwingCandidate): StageMeta {
  if (candidate.score >= 88 && candidate.risk_reward >= 1.7) {
    return {
      label: 'Priority Plan',
      tone: 'positive',
      note: 'High-conviction structure with a tight enough risk box for first review.',
    }
  }
  if (candidate.score >= 80) {
    return {
      label: 'Active Plan',
      tone: 'warning',
      note: 'Actionable plan, but sizing and trigger discipline matter more here.',
    }
  }
  return {
    label: 'Review Again',
    tone: 'neutral',
    note: 'Still useful to keep on deck, though the setup quality is not top tier yet.',
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

function CandidateTableRow({
  candidate,
  active,
  watchlisted,
  queued,
  onSelect,
  onWatch,
  onQueue,
}: {
  candidate: SwingCandidate
  active: boolean
  watchlisted: boolean
  queued: boolean
  onSelect: (symbol: string) => void
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
}) {
  return (
    <tr className={active ? 'scanner-row scanner-row-active' : 'scanner-row'}>
      <td>
        <button type="button" className="row-link" onClick={() => onSelect(candidate.symbol)}>
          <strong>{candidate.symbol}</strong>
          <span>{candidate.company_name}</span>
        </button>
      </td>
      <td>{candidate.setup_family}</td>
      <td>{candidate.confidence}</td>
      <td>{candidate.liquidity_bucket}</td>
      <td className={candidate.day_change_pct >= 0 ? 'tone-positive' : 'tone-danger'}>
        {candidate.day_change_pct >= 0 ? '+' : ''}
        {candidate.day_change_pct.toFixed(2)}%
      </td>
      <td>{candidate.regime_fit}</td>
      <td>{candidate.risk_reward.toFixed(2)}R</td>
      <td>{candidate.expected_hold}</td>
      <td>
        <div className="row-actions">
          <button type="button" className={watchlisted ? 'ghost-button ghost-button-small active-ghost' : 'ghost-button ghost-button-small'} onClick={() => onWatch(candidate)}>
            {watchlisted ? 'Saved' : 'Watch'}
          </button>
          <button type="button" className={queued ? 'ghost-button ghost-button-small active-ghost' : 'ghost-button ghost-button-small'} onClick={() => onQueue(candidate)}>
            {queued ? 'Queued' : 'Paper'}
          </button>
        </div>
      </td>
    </tr>
  )
}

function DetailPanel({
  candidate,
  watchlisted,
  queued,
  onWatch,
  onQueue,
}: {
  candidate: SwingCandidate | null
  watchlisted: boolean
  queued: boolean
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
}) {
  if (!candidate) {
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

  return (
    <Surface className="detail-panel">
      <div className="detail-header">
        <div>
          <p className="detail-symbol">{candidate.symbol}</p>
          <h2>{candidate.company_name}</h2>
          <p className="detail-subline">
            {candidate.setup_family} | {candidate.confidence} | Source: {candidate.source}
          </p>
        </div>
        <div className="detail-actions">
          <button type="button" className={watchlisted ? 'ghost-button active-ghost' : 'ghost-button'} onClick={() => onWatch(candidate)}>
            <Bookmark size={14} />
            <span>{watchlisted ? 'On Watchlist' : 'Add To Watchlist'}</span>
          </button>
          <button type="button" className="primary-button" onClick={() => onQueue(candidate)}>
            <WalletCards size={14} />
            <span>{queued ? 'Refresh Paper Plan' : 'Send To Paper Desk'}</span>
          </button>
        </div>
      </div>

      <div className="detail-metrics-grid">
        <CandidateStat label="Last Price" value={currency(candidate.last_price)} />
        <CandidateStat
          label="Day Change"
          value={`${candidate.day_change_pct >= 0 ? '+' : ''}${candidate.day_change_pct.toFixed(2)}%`}
          tone={candidate.day_change_pct >= 0 ? 'positive' : 'danger'}
        />
        <CandidateStat label="Regime Fit" value={`${candidate.regime_fit}/100`} />
        <CandidateStat label="Risk / Reward" value={`${candidate.risk_reward.toFixed(2)}R`} tone="positive" />
      </div>

      <Surface className="inner-surface thesis-panel">
        <span className="eyebrow">Why It Qualified</span>
        <p>{candidate.thesis}</p>
      </Surface>

      <div className="split-grid">
        <Surface className="inner-surface">
          <span className="eyebrow">Trade Plan</span>
          <div className="plan-grid">
            <div>
              <span className="micro-label">Entry Zone</span>
              <strong>{candidate.entry_zone}</strong>
            </div>
            <div>
              <span className="micro-label">Stop Loss</span>
              <strong>{currency(candidate.stop_loss)}</strong>
            </div>
            <div>
              <span className="micro-label">Target</span>
              <strong>{currency(candidate.target_price)}</strong>
            </div>
            <div>
              <span className="micro-label">Expected Hold</span>
              <strong>{candidate.expected_hold}</strong>
            </div>
          </div>
        </Surface>

        <Surface className="inner-surface">
          <span className="eyebrow">Market Read</span>
          <div className="plan-grid">
            <div>
              <span className="micro-label">Open Gap</span>
              <strong>{candidate.open_gap_pct >= 0 ? '+' : ''}{candidate.open_gap_pct.toFixed(2)}%</strong>
            </div>
            <div>
              <span className="micro-label">Distance To High</span>
              <strong>{candidate.distance_to_high_pct.toFixed(2)}%</strong>
            </div>
            <div>
              <span className="micro-label">Liquidity</span>
              <strong>{candidate.liquidity_bucket}</strong>
            </div>
            <div>
              <span className="micro-label">Bias</span>
              <strong>{candidate.bias}</strong>
            </div>
          </div>
        </Surface>
      </div>

      <div className="split-grid">
        <Surface className="inner-surface">
          <span className="eyebrow">Supporting Reasons</span>
          <ul className="detail-list">
            {candidate.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </Surface>

        <Surface className="inner-surface">
          <span className="eyebrow">Risk Box</span>
          <ul className="detail-list warning-list">
            {candidate.risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </Surface>
      </div>
    </Surface>
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

  return (
    <div className="page-stack">
      <Surface className="hero-surface">
        <div className="hero-copy">
          <span className="eyebrow">Swing Control Center</span>
          <h1>Discover candidates, move the best names to a watchlist, then stage clean paper trades.</h1>
          <p>{home.market_regime.summary}</p>
          <div className="hero-actions">
            <BrokerBadge broker={home.broker} />
            <div className="mini-chip">
              <Activity size={14} />
              <span>{home.scanner_count} names in the active scanner</span>
            </div>
          </div>
        </div>

        <div className="hero-stat-grid">
          <CandidateStat label="Breadth Ratio" value={home.market_regime.breadth_ratio.toFixed(2)} tone="positive" />
          <CandidateStat label="Watchlist" value={String(watchlistCount)} />
          <CandidateStat label="Paper Plans" value={String(paperCount)} />
          <CandidateStat label="Broker State" value={home.broker.state.toUpperCase()} tone={stateTone(home.broker.state)} />
        </div>
      </Surface>

      <div className="two-column-home">
        <Surface>
          <div className="section-head">
            <div>
              <span className="eyebrow">Top Opportunities</span>
              <h2>What deserves attention first</h2>
            </div>
          </div>
          <div className="opportunity-grid">
            {home.top_candidates.map((candidate) => (
              <button
                key={candidate.symbol}
                type="button"
                onClick={() => onSelect(candidate.symbol)}
                className={`candidate-card ${candidate.symbol === selectedSymbol ? 'candidate-card-active' : ''}`}
              >
                <div className="candidate-card-top">
                  <div>
                    <p className="symbol-line">{candidate.symbol}</p>
                    <p className="company-line">{candidate.company_name}</p>
                  </div>
                  <div className="score-chip">
                    <span>{candidate.score}</span>
                  </div>
                </div>
                <p className="setup-chip">{candidate.setup_family}</p>
                <div className="candidate-card-metrics">
                  <span>{candidate.confidence}</span>
                  <span>{candidate.expected_hold}</span>
                  <span>{candidate.liquidity_bucket}</span>
                </div>
              </button>
            ))}
          </div>
        </Surface>

        <Surface>
          <div className="section-head">
            <div>
              <span className="eyebrow">Workflow</span>
              <h2>How this should feel now</h2>
            </div>
          </div>
          <div className="workflow-stack">
            <div className="workflow-row">
              <div className="workflow-icon"><Radar size={16} /></div>
              <div>
                <strong>1. Scan the market</strong>
                <p>Find live Dhan-backed swing candidates in the Scanner and inspect the thesis.</p>
              </div>
            </div>
            <div className="workflow-row">
              <div className="workflow-icon"><Bookmark size={16} /></div>
              <div>
                <strong>2. Save to Watchlist</strong>
                <p>Keep promising stocks organized before promoting them into a real paper plan.</p>
              </div>
            </div>
            <div className="workflow-row">
              <div className="workflow-icon"><WalletCards size={16} /></div>
              <div>
                <strong>3. Stage paper trades</strong>
                <p>Review entry, stop, target, and risk/reward in one dedicated paper-trade desk.</p>
              </div>
            </div>
          </div>
        </Surface>
      </div>
    </div>
  )
}

function ScannerView({
  scanner,
  selectedSymbol,
  detailCandidate,
  watchlist,
  paperQueue,
  onSelect,
  onWatch,
  onQueue,
}: {
  scanner: SwingScannerResponse | null
  selectedSymbol: string | null
  detailCandidate: SwingCandidate | null
  watchlist: SwingCandidate[]
  paperQueue: SwingCandidate[]
  onSelect: (symbol: string) => void
  onWatch: (candidate: SwingCandidate) => void
  onQueue: (candidate: SwingCandidate) => void
}) {
  const [search, setSearch] = useState('')
  const [familyFilter, setFamilyFilter] = useState<string>('All')
  const deferredSearch = useDeferredValue(search)

  const families = useMemo(() => {
    const options = new Set<string>(['All'])
    scanner?.candidates.forEach((candidate) => options.add(candidate.setup_family))
    return Array.from(options)
  }, [scanner])

  const filtered = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase()
    return (scanner?.candidates ?? []).filter((candidate) => {
      const matchesFamily = familyFilter === 'All' || candidate.setup_family === familyFilter
      const matchesSearch =
        !term ||
        candidate.symbol.toLowerCase().includes(term) ||
        candidate.company_name.toLowerCase().includes(term)
      return matchesFamily && matchesSearch
    })
  }, [deferredSearch, familyFilter, scanner])

  const watchSymbols = useMemo(() => new Set(watchlist.map((item) => item.symbol)), [watchlist])
  const queueSymbols = useMemo(() => new Set(paperQueue.map((item) => item.symbol)), [paperQueue])

  if (!scanner) return <PageSkeleton />

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head scanner-toolbar">
          <div>
            <span className="eyebrow">Scanner</span>
            <h2>{scanner.live_data ? 'Live Dhan-backed swing candidates' : 'Fallback swing candidates from the curated universe'}</h2>
          </div>
          <div className="toolbar-right">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="text-input"
              placeholder="Search symbol or company"
            />
          </div>
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

        <div className="scanner-layout">
          <div className="scanner-table-shell">
            <table className="scanner-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Setup</th>
                  <th>Confidence</th>
                  <th>Liquidity</th>
                  <th>Day %</th>
                  <th>Regime</th>
                  <th>R / R</th>
                  <th>Hold</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((candidate) => (
                  <CandidateTableRow
                    key={candidate.symbol}
                    candidate={candidate}
                    active={candidate.symbol === selectedSymbol}
                    watchlisted={watchSymbols.has(candidate.symbol)}
                    queued={queueSymbols.has(candidate.symbol)}
                    onSelect={onSelect}
                    onWatch={onWatch}
                    onQueue={onQueue}
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

          <DetailPanel
            candidate={detailCandidate}
            watchlisted={!!detailCandidate && watchSymbols.has(detailCandidate.symbol)}
            queued={!!detailCandidate && queueSymbols.has(detailCandidate.symbol)}
            onWatch={onWatch}
            onQueue={onQueue}
          />
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
  const queueSymbols = new Set(paperQueue.map((candidate) => candidate.symbol))
  const paperReady = watchlist.filter((candidate) => {
    const stage = watchlistStage(candidate, queueSymbols.has(candidate.symbol))
    return stage.label === 'Paper Ready'
  })
  const monitoringLane = watchlist.filter((candidate) => !paperReady.some((item) => item.symbol === candidate.symbol))

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Watchlist</span>
            <h2>Organize the stocks you want to monitor before paper trading them</h2>
          </div>
          <div className="hero-actions">
            <CandidateStat label="Tracked Stocks" value={String(watchlist.length)} />
            <CandidateStat label="Already Queued" value={String(watchlist.filter((item) => queueSymbols.has(item.symbol)).length)} />
            <CandidateStat label="Ready To Promote" value={String(paperReady.length)} tone="positive" />
          </div>
        </div>

        {watchlist.length === 0 ? (
          <div className="portfolio-empty">
            <Bookmark size={20} />
            <p>Your watchlist is empty. Save stocks from the scanner first, then review them here.</p>
          </div>
        ) : (
          <div className="lane-grid">
            <Surface className="lane-surface">
              <div className="lane-head">
                <div>
                  <span className="eyebrow">Priority Lane</span>
                  <h3>Ready for paper trade staging</h3>
                </div>
                <div className="mini-chip">
                  <Target size={14} />
                  <span>{paperReady.length} names</span>
                </div>
              </div>
              {paperReady.length === 0 ? (
                <div className="lane-empty">
                  <Clock3 size={18} />
                  <p>No names are in the paper-ready lane yet. Keep building the watchlist from the scanner.</p>
                </div>
              ) : (
                <div className="lane-list">
                  {paperReady.map((candidate) => {
                    const stage = watchlistStage(candidate, queueSymbols.has(candidate.symbol))
                    return (
                      <Surface key={candidate.symbol} className="inner-surface queue-card">
                        <div className="candidate-card-top">
                          <div>
                            <p className="symbol-line">{candidate.symbol}</p>
                            <p className="company-line">{candidate.company_name}</p>
                          </div>
                          <StagePill label={stage.label} tone={stage.tone} />
                        </div>
                        <p className="queue-thesis">{stage.note}</p>
                        <div className="plan-grid compact-grid">
                          <div>
                            <span className="micro-label">Setup</span>
                            <strong>{candidate.setup_family}</strong>
                          </div>
                          <div>
                            <span className="micro-label">Score</span>
                            <strong>{candidate.score}</strong>
                          </div>
                          <div>
                            <span className="micro-label">Risk / Reward</span>
                            <strong>{candidate.risk_reward.toFixed(2)}R</strong>
                          </div>
                          <div>
                            <span className="micro-label">Last Price</span>
                            <strong>{currency(candidate.last_price)}</strong>
                          </div>
                        </div>
                        <div className="queue-actions">
                          <button type="button" className="ghost-button" onClick={() => onSelect(candidate.symbol)}>
                            Open Thesis
                          </button>
                          <button
                            type="button"
                            className={queueSymbols.has(candidate.symbol) ? 'ghost-button active-ghost' : 'primary-button'}
                            onClick={() => onQueue(candidate)}
                          >
                            {queueSymbols.has(candidate.symbol) ? 'Already Queued' : 'Promote To Paper'}
                          </button>
                        </div>
                      </Surface>
                    )
                  })}
                </div>
              )}
            </Surface>

            <Surface className="lane-surface">
              <div className="lane-head">
                <div>
                  <span className="eyebrow">Monitor Lane</span>
                  <h3>Names to keep tracking</h3>
                </div>
                <div className="mini-chip">
                  <ListTodo size={14} />
                  <span>{monitoringLane.length} names</span>
                </div>
              </div>
              <div className="lane-list">
                {monitoringLane.map((candidate) => {
                  const stage = watchlistStage(candidate, queueSymbols.has(candidate.symbol))
                  return (
                    <Surface key={candidate.symbol} className="inner-surface queue-card">
                      <div className="candidate-card-top">
                        <div>
                          <p className="symbol-line">{candidate.symbol}</p>
                          <p className="company-line">{candidate.company_name}</p>
                        </div>
                        <StagePill label={stage.label} tone={stage.tone} />
                      </div>
                      <p className="queue-thesis">{stage.note}</p>
                      <div className="plan-grid compact-grid">
                        <div>
                          <span className="micro-label">Expected Hold</span>
                          <strong>{candidate.expected_hold}</strong>
                        </div>
                        <div>
                          <span className="micro-label">Regime Fit</span>
                          <strong>{candidate.regime_fit}/100</strong>
                        </div>
                        <div>
                          <span className="micro-label">Distance To High</span>
                          <strong>{candidate.distance_to_high_pct.toFixed(2)}%</strong>
                        </div>
                        <div>
                          <span className="micro-label">Risk / Reward</span>
                          <strong>{candidate.risk_reward.toFixed(2)}R</strong>
                        </div>
                      </div>
                      <div className="queue-actions">
                        <button type="button" className="ghost-button" onClick={() => onSelect(candidate.symbol)}>
                          Open Thesis
                        </button>
                        <button
                          type="button"
                          className={queueSymbols.has(candidate.symbol) ? 'ghost-button active-ghost' : 'ghost-button'}
                          onClick={() => onQueue(candidate)}
                        >
                          {queueSymbols.has(candidate.symbol) ? 'In Paper Desk' : 'Send To Paper'}
                        </button>
                        <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(candidate.symbol)}>
                          Remove
                        </button>
                      </div>
                    </Surface>
                  )
                })}
              </div>
            </Surface>
          </div>
        )}
      </Surface>
    </div>
  )
}

function PortfolioView({
  queue,
  onSelect,
  onRemove,
  onWatch,
}: {
  queue: SwingCandidate[]
  onSelect: (symbol: string) => void
  onRemove: (symbol: string) => void
  onWatch: (candidate: SwingCandidate) => void
}) {
  const totalRisk = queue.reduce((sum, item) => sum + riskPerShare(item), 0)
  const avgRR = queue.length ? queue.reduce((sum, item) => sum + item.risk_reward, 0) / queue.length : 0
  const priorityPlans = queue.filter((candidate) => paperStage(candidate).label === 'Priority Plan')
  const reviewPlans = queue.filter((candidate) => paperStage(candidate).label !== 'Priority Plan')

  return (
    <div className="page-stack">
      <Surface>
        <div className="section-head">
          <div>
            <span className="eyebrow">Paper Desk</span>
            <h2>Stage and review paper trades in one place before we automate full execution</h2>
          </div>
          <div className="hero-actions">
            <CandidateStat label="Paper Plans" value={String(queue.length)} />
            <CandidateStat label="Average R / R" value={`${avgRR.toFixed(2)}R`} tone="positive" />
            <CandidateStat label="Total Risk / Share" value={currency(totalRisk)} tone="warning" />
            <CandidateStat label="Priority Plans" value={String(priorityPlans.length)} tone="positive" />
          </div>
        </div>

        {queue.length === 0 ? (
          <div className="portfolio-empty">
            <BriefcaseBusiness size={20} />
            <p>The paper desk is empty. Add names from the scanner or watchlist to start planning paper trades.</p>
          </div>
        ) : (
          <div className="lane-grid">
            <Surface className="lane-surface">
              <div className="lane-head">
                <div>
                  <span className="eyebrow">Execution Board</span>
                  <h3>Top paper trade candidates</h3>
                </div>
                <div className="mini-chip">
                  <Target size={14} />
                  <span>{priorityPlans.length} priority plans</span>
                </div>
              </div>
              {priorityPlans.length === 0 ? (
                <div className="lane-empty">
                  <Clock3 size={18} />
                  <p>No plan has reached the top priority lane yet. Keep promoting stronger setups from the watchlist.</p>
                </div>
              ) : (
                <div className="lane-list">
                  {priorityPlans.map((candidate) => {
                    const stage = paperStage(candidate)
                    return (
                      <Surface key={candidate.symbol} className="inner-surface queue-card">
                        <div className="candidate-card-top">
                          <div>
                            <p className="symbol-line">{candidate.symbol}</p>
                            <p className="company-line">{candidate.company_name}</p>
                          </div>
                          <StagePill label={stage.label} tone={stage.tone} />
                        </div>
                        <p className="queue-thesis">{stage.note}</p>
                        <div className="plan-grid compact-grid">
                          <div>
                            <span className="micro-label">Entry Zone</span>
                            <strong>{candidate.entry_zone}</strong>
                          </div>
                          <div>
                            <span className="micro-label">Stop</span>
                            <strong>{currency(candidate.stop_loss)}</strong>
                          </div>
                          <div>
                            <span className="micro-label">Target</span>
                            <strong>{currency(candidate.target_price)}</strong>
                          </div>
                          <div>
                            <span className="micro-label">Risk / Share</span>
                            <strong>{currency(riskPerShare(candidate))}</strong>
                          </div>
                        </div>
                        <div className="queue-actions">
                          <button type="button" className="ghost-button" onClick={() => onSelect(candidate.symbol)}>
                            Open Thesis
                          </button>
                          <button type="button" className="ghost-button" onClick={() => onWatch(candidate)}>
                            Keep On Watchlist
                          </button>
                          <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(candidate.symbol)}>
                            Remove Plan
                          </button>
                        </div>
                      </Surface>
                    )
                  })}
                </div>
              )}
            </Surface>

            <Surface className="lane-surface">
              <div className="lane-head">
                <div>
                  <span className="eyebrow">Review Queue</span>
                  <h3>Paper plans still under review</h3>
                </div>
                <div className="mini-chip">
                  <Clock3 size={14} />
                  <span>{reviewPlans.length} plans</span>
                </div>
              </div>
              <div className="lane-list">
                {reviewPlans.map((candidate) => {
                  const stage = paperStage(candidate)
                  return (
                    <Surface key={candidate.symbol} className="inner-surface queue-card">
                      <div className="candidate-card-top">
                        <div>
                          <p className="symbol-line">{candidate.symbol}</p>
                          <p className="company-line">{candidate.company_name}</p>
                        </div>
                        <StagePill label={stage.label} tone={stage.tone} />
                      </div>
                      <p className="queue-thesis">{stage.note}</p>
                      <div className="plan-grid compact-grid">
                        <div>
                          <span className="micro-label">Setup</span>
                          <strong>{candidate.setup_family}</strong>
                        </div>
                        <div>
                          <span className="micro-label">Score</span>
                          <strong>{candidate.score}</strong>
                        </div>
                        <div>
                          <span className="micro-label">Expected Hold</span>
                          <strong>{candidate.expected_hold}</strong>
                        </div>
                        <div>
                          <span className="micro-label">Risk / Reward</span>
                          <strong>{candidate.risk_reward.toFixed(2)}R</strong>
                        </div>
                      </div>
                      <div className="queue-actions">
                        <button type="button" className="ghost-button" onClick={() => onSelect(candidate.symbol)}>
                          Open Thesis
                        </button>
                        <button type="button" className="ghost-button" onClick={() => onWatch(candidate)}>
                          Keep On Watchlist
                        </button>
                        <button type="button" className="ghost-button danger-ghost" onClick={() => onRemove(candidate.symbol)}>
                          Remove Plan
                        </button>
                      </div>
                    </Surface>
                  )
                })}
              </div>
            </Surface>
          </div>
        )}
      </Surface>
    </div>
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
  const [view, setView] = useState<View>('home')
  const [home, setHome] = useState<SwingHomeResponse | null>(null)
  const [scanner, setScanner] = useState<SwingScannerResponse | null>(null)
  const [accounts, setAccounts] = useState<BrokerAccountSnapshot[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [detailCandidate, setDetailCandidate] = useState<SwingCandidate | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [watchlist, setWatchlist] = useState<SwingCandidate[]>(() => {
    try {
      const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY)
      return raw ? (JSON.parse(raw) as SwingCandidate[]) : []
    } catch {
      return []
    }
  })
  const [paperQueue, setPaperQueue] = useState<SwingCandidate[]>(() => {
    try {
      const raw = localStorage.getItem(PAPER_STORAGE_KEY)
      return raw ? (JSON.parse(raw) as SwingCandidate[]) : []
    } catch {
      return []
    }
  })

  useEffect(() => {
    localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist))
  }, [watchlist])

  useEffect(() => {
    localStorage.setItem(PAPER_STORAGE_KEY, JSON.stringify(paperQueue))
  }, [paperQueue])

  const refreshAll = async () => {
    setRefreshing(true)
    setError('')
    try {
      const [homeData, scannerData, accountData] = await Promise.all([
        getSwingHome(),
        getSwingScanner(28),
        getBrokerAccounts().catch(() => []),
      ])
      startTransition(() => {
        setHome(homeData)
        setScanner(scannerData)
        setAccounts(accountData)
        const defaultSymbol =
          selectedSymbol ??
          homeData.top_candidates[0]?.symbol ??
          scannerData.candidates[0]?.symbol ??
          watchlist[0]?.symbol ??
          paperQueue[0]?.symbol ??
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
          paperQueue.find((candidate) => candidate.symbol === selectedSymbol) ??
          null
        if (!cancelled) setDetailCandidate(fallback)
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSymbol, scanner, watchlist, paperQueue])

  const addToWatchlist = (candidate: SwingCandidate) => {
    setWatchlist((current) => upsertCandidate(current, candidate))
  }

  const addToPaperDesk = (candidate: SwingCandidate) => {
    setPaperQueue((current) => upsertCandidate(current, candidate))
    setWatchlist((current) => upsertCandidate(current, candidate))
  }

  const removeFromWatchlist = (symbol: string) => {
    setWatchlist((current) => removeCandidate(current, symbol))
  }

  const removeFromPaperDesk = (symbol: string) => {
    setPaperQueue((current) => removeCandidate(current, symbol))
  }

  const broker = home?.broker ?? scanner?.broker ?? null
  const updatedAt = home?.updated_at ?? scanner?.updated_at ?? null

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
            const active = view === item.id
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setView(item.id)}
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
              <span>{paperQueue.length} paper trade plans</span>
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
            <h1 className="topbar-title">A cleaner swing workflow: scan, save to watchlist, then move the best names into paper trades.</h1>
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

        {loadingDetail && (view === 'scanner' || view === 'watchlist' || view === 'portfolio') && (
          <div className="mini-status">
            <Sparkles size={14} />
            <span>Refreshing thesis panel...</span>
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
                paperCount={paperQueue.length}
                selectedSymbol={selectedSymbol}
                onSelect={setSelectedSymbol}
              />
            )}
            {view === 'scanner' && (
              <ScannerView
                scanner={scanner}
                selectedSymbol={selectedSymbol}
                detailCandidate={detailCandidate}
                watchlist={watchlist}
                paperQueue={paperQueue}
                onSelect={setSelectedSymbol}
                onWatch={addToWatchlist}
                onQueue={addToPaperDesk}
              />
            )}
            {view === 'watchlist' && (
              <WatchlistView
                watchlist={watchlist}
                paperQueue={paperQueue}
                onSelect={setSelectedSymbol}
                onQueue={addToPaperDesk}
                onRemove={removeFromWatchlist}
              />
            )}
            {view === 'portfolio' && (
              <PortfolioView
                queue={paperQueue}
                onSelect={setSelectedSymbol}
                onRemove={removeFromPaperDesk}
                onWatch={addToWatchlist}
              />
            )}
            {view === 'research' && <ResearchView setupMix={home?.setup_mix ?? []} />}
            {view === 'settings' && <SettingsView broker={broker} accounts={accounts} />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}
