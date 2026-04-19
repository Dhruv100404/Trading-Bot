import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { getPerformance, type PerformanceRow } from '../api'
import { TrendingUp, TrendingDown, BarChart2, Target, AlertCircle, type LucideIcon } from 'lucide-react'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtPct(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function fmtRupee(n: number): string {
  return `${n >= 0 ? '+' : '-'}₹${Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function pnlClass(n: number) {
  return n > 0 ? 'text-[#00E676]' : n < 0 ? 'text-[#FF5252]' : 'text-[#5A6478]'
}

// ─── Sub-components ───────────────────────────────────────────────────────────

interface StatCardProps {
  label: string
  value: string
  sub?: string
  positive?: boolean | null
  Icon: LucideIcon
  iconClass?: string
}

function StatCard({ label, value, sub, positive, Icon, iconClass }: StatCardProps) {
  const valClass =
    positive === true  ? 'text-[#00E676]' :
    positive === false ? 'text-[#FF5252]' :
    'text-gray-100'

  return (
    <div className="card p-5 flex items-start gap-4">
      <div className="p-2.5 rounded-xl bg-[#1A1F2E] shrink-0">
        <Icon size={16} className={iconClass ?? 'text-[#2979FF]'} />
      </div>
      <div className="min-w-0">
        <p className="stat-label">{label}</p>
        <p className={`text-2xl font-bold leading-tight truncate mt-1 ${valClass}`}>{value}</p>
        {sub && <p className="stat-sub mt-1">{sub}</p>}
      </div>
    </div>
  )
}

function PageSkeleton() {
  return (
    <div className="space-y-5 animate-fade-up">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card p-5 flex gap-3">
            <div className="skeleton w-10 h-10 rounded-xl" />
            <div className="flex-1 space-y-2 pt-1">
              <div className="skeleton h-3 w-16 rounded" />
              <div className="skeleton h-7 w-24 rounded" />
            </div>
          </div>
        ))}
      </div>
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1E2330]">
          <div className="skeleton h-4 w-32 rounded" />
        </div>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex gap-4 px-4 py-3 border-b border-[#1E2330]">
            <div className="skeleton h-4 w-24 rounded" />
            <div className="skeleton h-4 w-10 rounded ml-auto" />
            <div className="skeleton h-4 w-10 rounded" />
            <div className="skeleton h-4 w-14 rounded" />
            <div className="skeleton h-4 w-20 rounded" />
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function Performance() {
  const [rows,    setRows]    = useState<PerformanceRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    getPerformance()
      .then(setRows)
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <PageSkeleton />

  if (error) {
    return (
      <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-[#FF5252]/10 border border-[#FF5252]/25 text-[#FF5252] text-sm">
        <AlertCircle size={14} />
        {error}
      </div>
    )
  }

  // Summary calcs
  const totalSignals    = rows.reduce((s: number, r: PerformanceRow) => s + r.buy_signals + r.sell_signals, 0)
  const totalProfitable = rows.reduce((s: number, r: PerformanceRow) => s + r.profitable, 0)
  const totalLosses     = rows.reduce((s: number, r: PerformanceRow) => s + r.losses, 0)
  const netPnl          = rows.reduce((s: number, r: PerformanceRow) => s + r.net_pnl, 0)
  const winRate = totalProfitable + totalLosses > 0
    ? (totalProfitable / (totalProfitable + totalLosses)) * 100
    : null
  const avgReturn = rows.length > 0
    ? rows.reduce((s: number, r: PerformanceRow) => s + r.avg_return_pct, 0) / rows.length
    : null

  return (
    <div className="space-y-5 animate-fade-up">
      <div>
        <h1 className="text-lg font-semibold text-gray-100">Performance</h1>
        <p className="text-xs text-[#5A6478] mt-0.5">{rows.length} trading days tracked</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Total Signals"
          value={String(totalSignals)}
          sub={`${rows.length} days`}
          Icon={BarChart2}
        />
        <StatCard
          label="Win Rate"
          value={winRate !== null ? `${winRate.toFixed(1)}%` : '—'}
          sub={`${totalProfitable}W / ${totalLosses}L`}
          positive={winRate !== null ? winRate >= 50 : null}
          Icon={Target}
        />
        <StatCard
          label="Net P&L"
          value={netPnl === 0 ? '₹0' : fmtRupee(netPnl)}
          positive={netPnl > 0 ? true : netPnl < 0 ? false : null}
          Icon={netPnl >= 0 ? TrendingUp : TrendingDown}
          iconClass={netPnl >= 0 ? 'text-[#00E676]' : 'text-[#FF5252]'}
        />
        <StatCard
          label="Avg Return"
          value={avgReturn !== null ? fmtPct(avgReturn) : '—'}
          sub="per signal"
          positive={avgReturn !== null ? avgReturn > 0 : null}
          Icon={BarChart2}
        />
      </div>

      {/* Table */}
      {rows.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-16 gap-3">
          <div className="w-12 h-12 rounded-full bg-[#1A1F2E] flex items-center justify-center">
            <BarChart2 size={20} className="text-[#3A4255]" />
          </div>
          <p className="text-sm text-[#5A6478]">No performance data yet.</p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E2330]">
            <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Daily Breakdown</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#1E2330]">
                  <th className="th">Date</th>
                  <th className="th text-center">BUY</th>
                  <th className="th text-center">SELL</th>
                  <th className="th text-center">Win</th>
                  <th className="th text-center">Loss</th>
                  <th className="th-right">Avg Ret</th>
                  <th className="th-right">Net P&amp;L</th>
                  <th className="th-right">Capital</th>
                  <th className="th-right">ROC %</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row: PerformanceRow, i: number) => (
                  <motion.tr
                    key={row.trading_date}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.015 }}
                    className="tr-hover border-b border-[#1E2330] last:border-0"
                  >
                    <td className="td font-mono text-gray-300">{row.trading_date}</td>
                    <td className="td text-center font-semibold text-[#00E676]">{row.buy_signals}</td>
                    <td className="td text-center font-semibold text-[#FF5252]">{row.sell_signals}</td>
                    <td className="td text-center text-[#00E676]">{row.profitable}</td>
                    <td className="td text-center text-[#FF5252]">{row.losses}</td>
                    <td className={`td-mono text-right ${pnlClass(row.avg_return_pct)}`}>
                      {fmtPct(row.avg_return_pct)}
                    </td>
                    <td className={`td-mono text-right font-semibold ${pnlClass(row.net_pnl)}`}>
                      {fmtRupee(row.net_pnl)}
                    </td>
                    <td className="td-mono text-right text-[#5A6478]">
                      ₹{row.capital_used.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </td>
                    <td className={`td-mono text-right font-semibold ${pnlClass(row.roc_pct)}`}>
                      {fmtPct(row.roc_pct)}
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
