import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { getSignals, postCloseAll, type Signal } from '../api'
import { Badge } from '../components/Badge'
import type { WsEvent } from '../useWebSocket'
import {
  Calendar,
  RefreshCw,
  AlertTriangle,
  Zap,
  Clock,
  TrendingUp,
  TrendingDown,
  CheckCircle2,
  XCircle,
  Timer,
  CircleDot,
} from 'lucide-react'

interface DashboardProps {
  events: WsEvent[]
}

// ─── Pure helpers (no JSX) ────────────────────────────────────────────────────

function getEventInfo(evt: WsEvent): { text: string; kind: 'signal' | 'exit' | 'poll' | 'other' } {
  const r = evt.raw
  if (evt.type === 'poll_done') return { text: 'Engine poll completed', kind: 'poll' }
  if (evt.type === 'signal_fired') {
    const dir = String(r.direction ?? '')
    const sym = String(r.symbol ?? '')
    const price = r.entry_price != null ? ` @ ₹${Number(r.entry_price).toFixed(2)}` : ''
    return { text: `${dir} signal — ${sym}${price}`, kind: 'signal' }
  }
  if (evt.type === 'exit') {
    const sym = String(r.symbol ?? '')
    const ret = r.actual_return_pct != null
      ? ` ${Number(r.actual_return_pct) >= 0 ? '+' : ''}${Number(r.actual_return_pct).toFixed(2)}%`
      : ''
    return { text: `Exit ${sym}${ret}`, kind: 'exit' }
  }
  return { text: `[${evt.type}]`, kind: 'other' }
}

function exitVariant(reason: Signal['exit_reason']): 'tp' | 'sl' | 'time' | 'open' {
  if (reason === 'TP')   return 'tp'
  if (reason === 'SL')   return 'sl'
  if (reason === 'TIME') return 'time'
  return 'open'
}

function exitLabel(reason: Signal['exit_reason']): string {
  if (reason === 'TP')   return 'TP'
  if (reason === 'SL')   return 'SL'
  if (reason === 'TIME') return 'TIME'
  return 'OPEN'
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ExitStatusCell({ reason }: { reason: Signal['exit_reason'] }) {
  const icon =
    reason === 'TP'   ? <CheckCircle2 size={11} className="text-[#2979FF]" /> :
    reason === 'SL'   ? <XCircle      size={11} className="text-[#FF5252]" /> :
    reason === 'TIME' ? <Timer        size={11} className="text-[#FFD740]" /> :
                        <CircleDot    size={11} className="text-[#5A6478]" />
  return (
    <div className="flex items-center gap-1.5">
      {icon}
      <Badge label={exitLabel(reason)} variant={exitVariant(reason)} />
    </div>
  )
}

function SignalStats({ signals }: { signals: Signal[] }) {
  const total   = signals.length
  const tpHit   = signals.filter((s) => s.exit_reason === 'TP').length
  const slHit   = signals.filter((s) => s.exit_reason === 'SL').length
  const open    = signals.filter((s) => s.exit_reason == null).length
  const netPnl  = signals.reduce((acc, s) => acc + (s.pnl_rupees ?? 0), 0)
  const pnlClass = netPnl > 0 ? 'text-[#00E676]' : netPnl < 0 ? 'text-[#FF5252]' : 'text-[#5A6478]'

  const stats = [
    {
      label: 'Signals',
      value: String(total),
      sub: 'fired today',
      icon: <Zap size={14} className="text-[#2979FF]" />,
    },
    {
      label: 'TP Hits',
      value: String(tpHit),
      sub: `${total > 0 ? ((tpHit / total) * 100).toFixed(0) : 0}% hit rate`,
      icon: <CheckCircle2 size={14} className="text-[#2979FF]" />,
    },
    {
      label: 'SL + Open',
      value: `${slHit} / ${open}`,
      sub: 'stop-outs / active',
      icon: <XCircle size={14} className="text-[#FF5252]" />,
    },
    {
      label: 'Net P&L',
      value: netPnl === 0 ? '₹0' : `${netPnl > 0 ? '+' : ''}₹${Math.abs(netPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`,
      sub: 'realized today',
      icon: netPnl >= 0
        ? <TrendingUp   size={14} className="text-[#00E676]" />
        : <TrendingDown size={14} className="text-[#FF5252]" />,
      valueClass: pnlClass,
    },
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
      {stats.map((s) => (
        <div key={s.label} className="card p-4 flex items-start gap-3">
          <div className="p-2 rounded-lg bg-[#1A1F2E] shrink-0">{s.icon}</div>
          <div className="min-w-0">
            <p className="stat-label">{s.label}</p>
            <p className={`text-xl font-bold leading-tight truncate ${s.valueClass ?? 'text-gray-100'}`}>{s.value}</p>
            <p className="stat-sub mt-0.5">{s.sub}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="space-y-2 p-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex gap-3 py-2">
          <div className="skeleton h-4 w-20 rounded" />
          <div className="skeleton h-4 w-12 rounded" />
          <div className="skeleton h-4 w-16 rounded" />
          <div className="skeleton h-4 w-10 rounded" />
          <div className="skeleton h-4 w-14 rounded ml-auto" />
        </div>
      ))}
    </div>
  )
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export function Dashboard({ events }: DashboardProps) {
  const [signals,     setSignals]     = useState<Signal[]>([])
  const [loading,     setLoading]     = useState(true)
  const [closingAll,  setClosingAll]  = useState(false)
  const [closeMsg,    setCloseMsg]    = useState('')
  const [refreshing,  setRefreshing]  = useState(false)

  const today = new Date().toLocaleDateString('en-CA')
  const [selectedDate, setSelectedDate] = useState(today)
  const isToday = selectedDate === today

  const loadSignals = useCallback(async () => {
    setLoading(true)
    try {
      const s = await getSignals({ date: selectedDate })
      setSignals(s)
    } catch { /* ignore */ } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [selectedDate])

  useEffect(() => {
    loadSignals()
    if (!isToday) return
    const interval = setInterval(loadSignals, 60_000)
    return () => clearInterval(interval)
  }, [loadSignals, isToday])

  const handleCloseAll = async () => {
    if (!window.confirm('Close ALL open positions now? This places individual exit orders for each open signal on LIVE accounts.')) return
    setClosingAll(true)
    setCloseMsg('')
    try {
      const res = await postCloseAll()
      setCloseMsg(res.message
        ? res.message
        : `Closed ${res.closed}/${res.total}${res.errors.length ? ` · ${res.errors.length} error(s)` : ' ✓'}`)
      loadSignals()
    } catch (e) {
      setCloseMsg(`Error: ${String(e)}`)
    } finally {
      setClosingAll(false)
      setTimeout(() => setCloseMsg(''), 5000)
    }
  }

  const handleRefresh = () => { setRefreshing(true); loadSignals() }

  return (
    <div className="space-y-5 animate-fade-up">

      {/* ── Header row ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-100">
            {isToday ? "Today's Signals" : 'Historical Signals'}
          </h1>
          <p className="text-xs text-[#5A6478] mt-0.5">
            {isToday ? 'Auto-refreshes every 60s' : `Viewing ${selectedDate}`}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative flex items-center">
            <Calendar size={13} className="absolute left-2.5 text-[#5A6478] pointer-events-none" />
            <input
              type="date"
              value={selectedDate}
              max={today}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="input-base pl-8 pr-3 py-1.5 text-xs w-36"
            />
          </div>
          {!isToday && (
            <button onClick={() => setSelectedDate(today)} className="btn-ghost-sm">Today</button>
          )}
          <button onClick={handleRefresh} disabled={loading} className="btn-ghost-sm flex items-center gap-1.5">
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
          {isToday && (
            <button onClick={handleCloseAll} disabled={closingAll}
              className="btn-danger flex items-center gap-1.5 text-xs px-3 py-1.5">
              <AlertTriangle size={12} />
              {closingAll ? 'Closing…' : 'Close All'}
            </button>
          )}
        </div>
      </div>

      {closeMsg && (
        <div className="px-4 py-2.5 rounded-lg bg-[#FFD740]/10 border border-[#FFD740]/25 text-[#FFD740] text-xs">
          {closeMsg}
        </div>
      )}

      {/* ── Summary stats ── */}
      {!loading && signals.length > 0 && <SignalStats signals={signals} />}

      {/* ── Signals table ── */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1E2330] flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Signal Log</span>
          {!loading && <span className="text-xs text-[#5A6478]">{signals.length} signals</span>}
        </div>

        {loading ? <TableSkeleton /> : signals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-14 gap-3">
            <div className="w-10 h-10 rounded-full bg-[#1A1F2E] flex items-center justify-center">
              <Zap size={18} className="text-[#3A4255]" />
            </div>
            <p className="text-sm text-[#5A6478]">No signals for {isToday ? 'today' : selectedDate}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#1E2330]">
                  <th className="th">Symbol</th>
                  <th className="th">Dir</th>
                  <th className="th">Entry ₹</th>
                  <th className="th">Bucket</th>
                  <th className="th text-center">Score</th>
                  <th className="th">TP ₹</th>
                  <th className="th">SL ₹</th>
                  <th className="th">Status</th>
                  <th className="th-right">P&amp;L ₹</th>
                  <th className="th-right">Return %</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s: Signal, i: number) => {
                  const pnl = s.pnl_rupees
                  const ret = s.actual_return_pct
                  const pnlClass = pnl == null ? 'text-[#5A6478]' : pnl >= 0 ? 'text-[#00E676]' : 'text-[#FF5252]'
                  return (
                    <motion.tr
                      key={s.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.02 }}
                      className="tr-hover border-b border-[#1E2330] last:border-0"
                    >
                      <td className="td font-mono font-semibold text-gray-100">{s.symbol}</td>
                      <td className="td">
                        <Badge label={s.direction} variant={s.direction === 'BUY' ? 'buy' : 'sell'} />
                      </td>
                      <td className="td-mono">₹{s.entry_price.toLocaleString('en-IN')}</td>
                      <td className="td">
                        <div className="flex items-center gap-1">
                          <Clock size={10} className="text-[#5A6478]" />
                          <span className="text-xs font-mono text-[#5A6478]">{s.entry_bucket}</span>
                        </div>
                      </td>
                      <td className="td text-center">
                        <span className="inline-flex items-center justify-center w-6 h-6 rounded-md bg-[#1A1F2E] text-xs font-bold text-gray-300">
                          {s.score}
                        </span>
                      </td>
                      <td className="td-mono text-[#2979FF]">₹{s.tp_price.toLocaleString('en-IN')}</td>
                      <td className="td-mono text-[#FFD740]">₹{s.sl_price.toLocaleString('en-IN')}</td>
                      <td className="td">
                        <ExitStatusCell reason={s.exit_reason} />
                      </td>
                      <td className={`td-mono text-right ${pnlClass}`}>
                        {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toFixed(0)}`}
                      </td>
                      <td className={`td-mono text-right ${pnlClass}`}>
                        {ret == null ? '—' : `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%`}
                      </td>
                    </motion.tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Live Events feed ── */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1E2330] flex items-center gap-2">
          <span className="relative flex">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00E676] animate-pulse" />
            <span className="absolute w-1.5 h-1.5 rounded-full bg-[#00E676] opacity-40 animate-ping" />
          </span>
          <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Live Event Feed</span>
          <span className="ml-auto text-xs text-[#5A6478]">{events.length} events</span>
        </div>
        <div className="h-52 overflow-y-auto p-2 space-y-0.5 font-mono">
          {events.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-xs text-[#3A4255]">Waiting for engine events…</p>
            </div>
          ) : (
            events.slice(0, 15).map((evt) => {
              const { text, kind } = getEventInfo(evt)
              const timeStr = new Date(evt.receivedAt).toLocaleTimeString('en-IN', { hour12: false })
              const dot  = kind === 'signal' ? 'bg-[#2979FF]' : kind === 'exit' ? 'bg-[#00E676]' : 'bg-[#2A3045]'
              const col  = kind === 'signal' ? 'text-[#2979FF]' : kind === 'exit' ? 'text-[#00E676]' : 'text-[#5A6478]'
              return (
                <div key={evt.id}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-[#1A1F2E] transition-colors">
                  <span className="text-[10px] text-[#3A4255] shrink-0 w-16">{timeStr}</span>
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
                  <span className={`text-xs ${col}`}>{text}</span>
                </div>
              )
            })
          )}
        </div>
      </div>

    </div>
  )
}
