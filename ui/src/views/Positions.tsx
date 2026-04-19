import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { RefreshCw, TrendingUp, TrendingDown, Activity, AlertCircle, KeyRound } from 'lucide-react'

interface Position {
  tradingSymbol: string
  securityId: string
  positionType: 'LONG' | 'SHORT' | 'CLOSED'
  exchangeSegment: string
  productType: string
  buyAvg: number
  buyQty: number
  sellAvg: number
  sellQty: number
  netQty: number
  realizedProfit: number
  unrealizedProfit: number
  costPrice: number
  dayBuyValue: number
  daySellValue: number
}

interface Balance {
  availabelBalance?: number
  sodLimit?: number
  utilizedAmount?: number
  withdrawableBalance?: number
}

interface AccountPositions {
  client_id: string
  name: string
  broker?: 'DHAN' | 'ZERODHA'
  balance: Balance
  positions: Position[]
  error?: string | null
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function PnlCell({ value }: { value: number }) {
  const isPos = value >= 0
  const cls = value === 0 ? 'text-[#5A6478]' : isPos ? 'text-[#00E676]' : 'text-[#FF5252]'
  return (
    <span className={`font-mono font-medium ${cls}`}>
      {value === 0 ? '₹0' : `${isPos ? '+' : ''}₹${Math.abs(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
    </span>
  )
}

function EmptyPositions() {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="w-12 h-12 rounded-full bg-[#1A1F2E] flex items-center justify-center">
        <Activity size={20} className="text-[#3A4255]" />
      </div>
      <p className="text-sm text-[#5A6478]">No positions today</p>
    </div>
  )
}

function AccountError({ error, broker }: { error: string; broker: string }) {
  const isTokenError = error.toLowerCase().includes('token') || error.toLowerCase().includes('auth')
    || error.toLowerCase().includes('session') || error.includes('403') || error.includes('401')
  return (
    <div className="mx-5 mb-4 rounded-lg bg-[#FF5252]/8 border border-[#FF5252]/20 px-4 py-3">
      <div className="flex items-start gap-2">
        {isTokenError ? (
          <KeyRound size={14} className="text-amber-400 mt-0.5 shrink-0" />
        ) : (
          <AlertCircle size={14} className="text-[#FF5252] mt-0.5 shrink-0" />
        )}
        <div className="min-w-0">
          {isTokenError ? (
            <>
              <p className="text-xs font-semibold text-amber-400">Access token expired</p>
              <p className="text-[11px] text-[#5A6478] mt-0.5">
                {broker === 'ZERODHA'
                  ? 'Zerodha tokens expire daily ~6 AM. Re-login via Kite and update the token in Accounts.'
                  : 'Dhan token has expired. Generate a new token from the Dhan dashboard and update in Accounts.'}
              </p>
            </>
          ) : (
            <>
              <p className="text-xs font-semibold text-[#FF5252]">API Error</p>
              <p className="text-[11px] text-[#5A6478] mt-0.5 break-all">{error}</p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function AccountCard({ account }: { account: AccountPositions }) {
  const { positions, error, broker = 'DHAN' } = account
  const open   = positions.filter((p) => p.netQty !== 0)
  const closed = positions.filter((p) => p.netQty === 0)
  const totalRealized   = positions.reduce((s, p) => s + p.realizedProfit, 0)
  const totalUnrealized = open.reduce((s, p) => s + p.unrealizedProfit, 0)
  const totalPnl = totalRealized + totalUnrealized
  const isPos = totalPnl >= 0
  const hasError = !!error

  return (
    <div className={`card overflow-hidden ${hasError && positions.length === 0 ? 'border-[#FF5252]/20' : ''}`}>
      {/* Account header */}
      <div className="px-5 py-4 border-b border-[#1E2330] flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div className="min-w-0">
            <p className="font-semibold text-gray-100 truncate">{account.name}</p>
            <p className="text-xs text-[#5A6478] font-mono mt-0.5">{account.client_id}</p>
          </div>
          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider shrink-0 ${
            broker === 'ZERODHA'
              ? 'bg-purple-500/10 text-purple-400 border border-purple-500/30'
              : 'bg-blue-500/10 text-blue-400 border border-blue-500/30'
          }`}>
            {broker}
          </span>
        </div>

        {/* Day P&L badge — only show if we have data */}
        {(positions.length > 0 || !hasError) && (
          <div className={`flex items-center gap-2 px-4 py-2 rounded-lg border ${
            isPos
              ? 'bg-[#00E676]/8 border-[#00E676]/20'
              : 'bg-[#FF5252]/8 border-[#FF5252]/20'
          }`}>
            {isPos
              ? <TrendingUp   size={14} className="text-[#00E676]" />
              : <TrendingDown size={14} className="text-[#FF5252]" />
            }
            <div>
              <p className="text-[10px] text-[#5A6478] uppercase tracking-wider">Day P&amp;L</p>
              <p className={`text-base font-bold font-mono leading-tight ${isPos ? 'text-[#00E676]' : 'text-[#FF5252]'}`}>
                {isPos ? '+' : ''}₹{Math.abs(totalPnl).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Per-account error banner */}
      {hasError && (
        <div className="pt-4">
          <AccountError error={error!} broker={broker} />
        </div>
      )}

      {/* Summary strip — only show if we have positions */}
      {positions.length > 0 && (
        <div className="px-5 py-2.5 bg-[#0D0F14]/60 border-b border-[#1E2330] flex flex-wrap gap-5 text-xs">
          <span className="text-[#5A6478]">
            Open: <span className="text-gray-200 font-semibold">{open.length}</span>
          </span>
          <span className="text-[#5A6478]">
            Closed: <span className="text-gray-200 font-semibold">{closed.length}</span>
          </span>
          <span className="text-[#5A6478]">
            Realized: <PnlCell value={totalRealized} />
          </span>
          <span className="text-[#5A6478]">
            Unrealized: <PnlCell value={totalUnrealized} />
          </span>
        </div>
      )}

      {/* Open positions */}
      {open.length > 0 && (
        <div className="px-5 py-3">
          <p className="text-[10px] font-bold text-[#FFD740] uppercase tracking-widest mb-2">
            Open Positions ({open.length})
          </p>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#1E2330]">
                  <th className="th text-left pb-2">Symbol</th>
                  <th className="th text-right pb-2">Qty</th>
                  <th className="th text-right pb-2">Avg Cost</th>
                  <th className="th text-right pb-2">Unrealized</th>
                  <th className="th text-right pb-2">Side</th>
                </tr>
              </thead>
              <tbody>
                {open.map((p) => (
                  <tr key={p.securityId} className="tr-hover border-b border-[#1E2330] last:border-0">
                    <td className="td font-semibold text-gray-100">{p.tradingSymbol}</td>
                    <td className={`td-mono text-right ${p.netQty > 0 ? 'text-[#00E676]' : 'text-[#FF5252]'}`}>
                      {p.netQty > 0 ? '+' : ''}{p.netQty}
                    </td>
                    <td className="td-mono text-right text-gray-400">
                      ₹{p.costPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </td>
                    <td className="td text-right">
                      <PnlCell value={p.unrealizedProfit} />
                    </td>
                    <td className="td text-right">
                      <span className={`inline-flex px-2 py-0.5 rounded-md text-[10px] font-bold uppercase ${
                        p.netQty > 0
                          ? 'bg-[#00E676]/10 text-[#00E676] border border-[#00E676]/25'
                          : 'bg-[#FF5252]/10 text-[#FF5252] border border-[#FF5252]/25'
                      }`}>
                        {p.netQty > 0 ? 'LONG' : 'SHORT'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Closed positions */}
      {closed.length > 0 && (
        <div className={`px-5 py-3 ${open.length > 0 ? 'border-t border-[#1E2330]' : ''}`}>
          <p className="text-[10px] font-bold text-[#5A6478] uppercase tracking-widest mb-2">
            Closed Today ({closed.length})
          </p>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#1E2330]">
                  <th className="th text-left pb-2">Symbol</th>
                  <th className="th text-right pb-2">Buy Avg</th>
                  <th className="th text-right pb-2">Sell Avg</th>
                  <th className="th text-right pb-2">Qty</th>
                  <th className="th text-right pb-2">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {[...closed].sort((a, b) => b.realizedProfit - a.realizedProfit).map((p) => (
                  <tr key={p.securityId} className="tr-hover border-b border-[#1E2330] last:border-0">
                    <td className="td text-gray-300">{p.tradingSymbol}</td>
                    <td className="td-mono text-right text-[#5A6478]">
                      ₹{p.buyAvg.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </td>
                    <td className="td-mono text-right text-[#5A6478]">
                      ₹{p.sellAvg.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </td>
                    <td className="td-mono text-right text-[#5A6478]">{p.buyQty}</td>
                    <td className="td text-right">
                      <PnlCell value={p.realizedProfit} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {positions.length === 0 && !hasError && <EmptyPositions />}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function Positions() {
  const [data,        setData]        = useState<AccountPositions[]>([])
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState('')
  const [lastRefresh, setLastRefresh] = useState('')
  const [refreshing,  setRefreshing]  = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const res  = await fetch('/api/positions')
      const json = await res.json()
      setData(json.accounts || [])
      setLastRefresh(new Date().toLocaleTimeString('en-IN'))
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  // Count accounts with errors
  const errorCount = data.filter(a => a.error).length

  // ── Loading skeleton ──
  if (loading) {
    return (
      <div className="space-y-4 animate-fade-up">
        <div className="flex items-center justify-between mb-2">
          <div className="skeleton h-6 w-36 rounded" />
          <div className="skeleton h-8 w-20 rounded" />
        </div>
        {[0, 1].map((i) => (
          <div key={i} className="card p-5 space-y-3">
            <div className="flex items-center justify-between">
              <div className="skeleton h-5 w-32 rounded" />
              <div className="skeleton h-8 w-28 rounded" />
            </div>
            <div className="space-y-2 pt-2">
              {[0, 1, 2].map((j) => (
                <div key={j} className="flex gap-4">
                  <div className="skeleton h-4 w-20 rounded" />
                  <div className="skeleton h-4 w-14 rounded ml-auto" />
                  <div className="skeleton h-4 w-16 rounded" />
                  <div className="skeleton h-4 w-16 rounded" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-5 animate-fade-up">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-100">Live Positions</h1>
          <div className="flex items-center gap-3 mt-0.5">
            {lastRefresh && (
              <p className="text-xs text-[#5A6478]">Last updated {lastRefresh}</p>
            )}
            {errorCount > 0 && (
              <span className="text-xs text-amber-400 flex items-center gap-1">
                <AlertCircle size={11} />
                {errorCount} account{errorCount > 1 ? 's' : ''} with issues
              </span>
            )}
          </div>
        </div>
        <button
          onClick={load}
          disabled={refreshing}
          className="btn-ghost flex items-center gap-1.5 text-xs"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Global fetch error (network down, engine unreachable) */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-[#FF5252]/10 border border-[#FF5252]/25 text-[#FF5252] text-sm">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {/* Accounts */}
      {data.length === 0 && !error ? (
        <div className="card flex flex-col items-center justify-center py-16 gap-3">
          <div className="w-12 h-12 rounded-full bg-[#1A1F2E] flex items-center justify-center">
            <Activity size={20} className="text-[#3A4255]" />
          </div>
          <p className="text-sm text-[#5A6478]">No accounts found. Add accounts in the Accounts tab.</p>
        </div>
      ) : (
        <motion.div
          className="space-y-4"
          initial="hidden"
          animate="visible"
          variants={{ visible: { transition: { staggerChildren: 0.06 } } }}
        >
          {data.map((acc: AccountPositions) => (
            <motion.div
              key={acc.client_id}
              variants={{ hidden: { opacity: 0, y: 8 }, visible: { opacity: 1, y: 0 } }}
            >
              <AccountCard account={acc} />
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  )
}
