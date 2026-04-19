import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { getStatus, type MarketStatus } from './api'
import { useWebSocket } from './useWebSocket'
import { Dashboard } from './views/Dashboard'
import { Accounts } from './views/Accounts'
import { Watchlist } from './views/Watchlist'
import { Performance } from './views/Performance'
import { Backtest } from './views/Backtest'
import { Positions } from './views/Positions'
import {
  LayoutDashboard,
  TrendingUp,
  Users,
  Eye,
  BarChart2,
  FlaskConical,
  Activity,
  Wifi,
  WifiOff,
  Circle,
} from 'lucide-react'

type View = 'dashboard' | 'positions' | 'accounts' | 'watchlist' | 'performance' | 'backtest'

interface NavItem {
  id: View
  label: string
  icon: React.ComponentType<{ size?: number; className?: string; strokeWidth?: number }>
}

const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard',   label: 'Live',        icon: LayoutDashboard },
  { id: 'positions',   label: 'Positions',   icon: TrendingUp },
  { id: 'accounts',    label: 'Accounts',    icon: Users },
  { id: 'watchlist',   label: 'Watchlist',   icon: Eye },
  { id: 'performance', label: 'Performance', icon: BarChart2 },
  { id: 'backtest',    label: 'Config',      icon: FlaskConical },
]

const MARKET_STATUS_CONFIG: Record<
  MarketStatus['market_status'],
  { label: string; dotColor: string; textColor: string; pulse: boolean }
> = {
  'PRE-OPEN': {
    label: 'Pre-Open',
    dotColor: 'bg-[#FFD740]',
    textColor: 'text-[#FFD740]',
    pulse: true,
  },
  LIVE: {
    label: 'Market Open',
    dotColor: 'bg-[#00E676]',
    textColor: 'text-[#00E676]',
    pulse: true,
  },
  CLOSED: {
    label: 'Market Closed',
    dotColor: 'bg-[#5A6478]',
    textColor: 'text-[#5A6478]',
    pulse: false,
  },
}

export default function App() {
  const [view, setView] = useState<View>('dashboard')
  const [status, setStatus] = useState<MarketStatus | null>(null)
  const { events, connected } = useWebSocket()

  useEffect(() => {
    let mounted = true
    const poll = async () => {
      try {
        const s = await getStatus()
        if (mounted) setStatus(s)
      } catch {
        // ignore
      }
    }
    poll()
    const interval = setInterval(poll, 15_000)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  const msCfg = status ? MARKET_STATUS_CONFIG[status.market_status] : null

  return (
    <div className="min-h-screen bg-[#0D0F14] text-gray-100 flex flex-col">
      {/* ── Top Navigation ───────────────────────────────────────────────────── */}
      <nav className="sticky top-0 z-50 bg-[#0A0C10]/95 backdrop-blur-sm border-b border-[#1E2330]">
        <div className="max-w-screen-2xl mx-auto px-6 flex items-center h-14 gap-2">

          {/* Logo */}
          <div className="flex items-center gap-2.5 mr-5 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#2979FF] to-[#00E676] flex items-center justify-center shadow-blue-glow">
              <Activity size={14} className="text-white" strokeWidth={2.5} />
            </div>
            <span className="font-bold text-white text-sm tracking-tight select-none">
              dhan<span className="text-[#2979FF]">trader</span>
            </span>
          </div>

          {/* Nav items */}
          <div className="flex items-center gap-0.5 flex-1 min-w-0">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon
              const active = view === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => setView(item.id)}
                  className={`relative flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-150 whitespace-nowrap ${
                    active
                      ? 'text-[#2979FF] bg-[#2979FF]/10'
                      : 'text-[#5A6478] hover:text-gray-200 hover:bg-[#141720]'
                  }`}
                >
                  <Icon size={14} strokeWidth={active ? 2.5 : 2} />
                  <span>{item.label}</span>
                  {active && (
                    <motion.span
                      layoutId="nav-indicator"
                      className="absolute inset-0 rounded-lg border border-[#2979FF]/30"
                      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                    />
                  )}
                </button>
              )
            })}
          </div>

          {/* Right section */}
          <div className="flex items-center gap-4 shrink-0">
            {/* Market status */}
            {msCfg ? (
              <div className="flex items-center gap-2">
                <span className="relative flex items-center">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${msCfg.dotColor} ${msCfg.pulse ? 'animate-pulse-dot' : ''}`}
                  />
                  {msCfg.pulse && (
                    <span
                      className={`absolute w-1.5 h-1.5 rounded-full ${msCfg.dotColor} opacity-40 animate-ping`}
                    />
                  )}
                </span>
                <span className={`text-xs font-medium ${msCfg.textColor}`}>{msCfg.label}</span>
                {status && (
                  <span className="text-xs text-[#5A6478] font-mono">{status.current_ist}</span>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Circle size={6} className="text-[#3A4255] fill-[#3A4255]" />
                <span className="text-xs text-[#3A4255]">Connecting…</span>
              </div>
            )}

            {/* Divider */}
            <div className="w-px h-4 bg-[#1E2330]" />

            {/* WS indicator */}
            <div className="flex items-center gap-1.5">
              {connected ? (
                <>
                  <Wifi size={12} className="text-[#00E676]" />
                  <span className="text-xs text-[#00E676] font-medium">Live</span>
                </>
              ) : (
                <>
                  <WifiOff size={12} className="text-[#FF5252]" />
                  <span className="text-xs text-[#FF5252] font-medium">Offline</span>
                </>
              )}
            </div>
          </div>
        </div>
      </nav>

      {/* ── Main content ─────────────────────────────────────────────────────── */}
      <main className="flex-1 max-w-screen-2xl mx-auto w-full px-6 py-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15, ease: 'easeOut' }}
          >
            {view === 'dashboard'   && <Dashboard events={events} />}
            {view === 'positions'   && <Positions />}
            {view === 'accounts'    && <Accounts />}
            {view === 'watchlist'   && <Watchlist />}
            {view === 'performance' && <Performance />}
            {view === 'backtest'    && <Backtest />}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* ── Footer ───────────────────────────────────────────────────────────── */}
      <footer className="border-t border-[#1E2330] py-3 px-6">
        <p className="text-center text-[10px] text-[#3A4255]">
          Trading involves substantial risk. For authorized internal use only.
          {status && <span className="ml-2">{status.today}</span>}
        </p>
      </footer>
    </div>
  )
}
