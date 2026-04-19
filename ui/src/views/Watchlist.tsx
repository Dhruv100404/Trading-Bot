import { useState, useEffect, useCallback, useRef } from 'react'
import { motion } from 'framer-motion'
import {
  getWatchlist, getTiers, patchWatchlistItem, patchTier,
  getVolumeGroups, patchVolumeGroup,
  type WatchlistItem, type Tier, type VolumeGroup,
} from '../api'
import { Toggle } from '../components/Toggle'
import { Search, AlertCircle, Layers, SlidersHorizontal, TrendingUp } from 'lucide-react'

const TIER_TABS = [
  'All', 'Tier1', 'Tier2', 'F&O', 'Nifty50',
  'Nifty500', 'Margin4x', 'Liquid5L', 'NSEActive', 'AllNSE',
] as const
type TierTab = (typeof TIER_TABS)[number]

// ─── Volume input (blur-to-save) ──────────────────────────────────────────────

function VolInput({ item, onSave }: { item: WatchlistItem; onSave: (id: number, vol: number) => Promise<void> }) {
  const [value,       setValue]       = useState(String(item.min_volume))
  const lastSavedRef                  = useRef(String(item.min_volume))

  const handleBlur = async () => {
    const num = Number(value)
    if (isNaN(num) || value === lastSavedRef.current) return
    try {
      await onSave(item.security_id, num)
      lastSavedRef.current = value
    } catch {
      setValue(lastSavedRef.current)
    }
  }

  return (
    <input
      type="number"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={handleBlur}
      className="w-24 input-sm text-right"
    />
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function Watchlist() {
  const [items,           setItems]           = useState<WatchlistItem[]>([])
  const [tiers,           setTiers]           = useState<Tier[]>([])
  const [volumeGroups,    setVolumeGroups]    = useState<VolumeGroup[]>([])
  const [activeTab,       setActiveTab]       = useState<TierTab>('All')
  const [showEnabledOnly, setShowEnabledOnly] = useState(false)
  const [search,          setSearch]          = useState('')
  const [loading,         setLoading]         = useState(true)
  const [error,           setError]           = useState('')

  const load = useCallback(async () => {
    try {
      const [w, t, vg] = await Promise.all([getWatchlist(), getTiers(), getVolumeGroups()])
      setItems(w)
      setTiers(t)
      setVolumeGroups(vg)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleTierToggle = async (tier: Tier) => {
    const newEnabled: 0 | 1 = tier.enabled === 1 ? 0 : 1
    try {
      await patchTier(tier.tier_name, newEnabled)
      setTiers((prev: Tier[]) => prev.map((t) => t.tier_name === tier.tier_name ? { ...t, enabled: newEnabled } : t))
    } catch (e: unknown) { setError(String(e)) }
  }

  const handleVolumeGroupToggle = async (group: VolumeGroup) => {
    const newEnabled: 0 | 1 = group.enabled === 1 ? 0 : 1
    try {
      await patchVolumeGroup(group.group_name, newEnabled)
      setVolumeGroups((prev) => prev.map((g) => g.group_name === group.group_name ? { ...g, enabled: newEnabled } : g))
    } catch (e: unknown) { setError(String(e)) }
  }

  const handleItemEnabledToggle = async (item: WatchlistItem) => {
    const newEnabled: 0 | 1 = item.enabled === 1 ? 0 : 1
    try {
      await patchWatchlistItem(item.security_id, { enabled: newEnabled })
      setItems((prev: WatchlistItem[]) =>
        prev.map((i) => i.security_id === item.security_id ? { ...i, enabled: newEnabled } : i))
    } catch (e: unknown) { setError(String(e)) }
  }

  const handleVolSave = async (secId: number, vol: number) => {
    await patchWatchlistItem(secId, { min_volume: vol })
    setItems((prev: WatchlistItem[]) => prev.map((i) => i.security_id === secId ? { ...i, min_volume: vol } : i))
  }

  // Filtering
  const tierFiltered = activeTab === 'All' ? items : items.filter((i) => i.tiers.includes(activeTab))
  const enabledFiltered = showEnabledOnly ? tierFiltered.filter((i) => i.enabled === 1) : tierFiltered
  const filteredItems = search.trim()
    ? enabledFiltered.filter((i) =>
        i.symbol.toLowerCase().includes(search.toLowerCase()) ||
        i.company_name.toLowerCase().includes(search.toLowerCase()))
    : enabledFiltered

  const marginTier    = tiers.find((t) => t.tier_name === 'Margin4x')
  const marginCount   = items.filter((i) => i.tiers.includes('Margin4x')).length
  const marginEnabled = items.filter((i) => i.tiers.includes('Margin4x') && i.enabled === 1).length
  const isMarginOn    = marginTier?.enabled === 1

  if (loading) {
    return (
      <div className="space-y-4 animate-fade-up">
        <div className="skeleton h-7 w-28 rounded" />
        <div className="card p-4 flex gap-4">
          {[0,1,2,3,4].map((i) => <div key={i} className="skeleton h-8 w-20 rounded-lg" />)}
        </div>
        <div className="card overflow-hidden">
          {[0,1,2,3,4,5,6].map((i) => (
            <div key={i} className="flex gap-4 px-4 py-3 border-b border-[#1E2330]">
              <div className="skeleton h-4 w-20 rounded" />
              <div className="skeleton h-4 w-40 rounded" />
              <div className="skeleton h-4 w-16 rounded ml-auto" />
              <div className="skeleton h-5 w-9 rounded-full" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4 animate-fade-up">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-100">Watchlist</h1>
        <span className="text-xs text-[#5A6478]">{items.length} symbols total</span>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[#FF5252]/10 border border-[#FF5252]/25 text-[#FF5252] text-sm">
          <AlertCircle size={13} /> {error}
        </div>
      )}

      {/* Margin4x quick-action */}
      {marginTier && (
        <div className="card px-5 py-3.5 flex items-center gap-4">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-gray-200">Margin 4-10x Stocks</p>
            <p className="text-xs text-[#5A6478] mt-0.5">
              {marginCount} stocks · {marginEnabled} active
            </p>
          </div>
          <button
            type="button"
            onClick={async () => { await handleTierToggle(marginTier); await load() }}
            className={isMarginOn ? 'btn-danger text-xs py-1.5 px-4' : 'btn-primary text-xs py-1.5 px-4'}
          >
            {isMarginOn ? `Disable All (${marginCount})` : `Enable All (${marginCount})`}
          </button>
        </div>
      )}

      {/* Tier toggles + Cap Group Filter */}
      <div className="card px-5 py-4">
        <div className="flex items-center gap-2 mb-3">
          <Layers size={13} className="text-[#5A6478]" />
          <span className="text-[10px] font-semibold text-[#5A6478] uppercase tracking-widest">Tier Toggles</span>
        </div>
        <div className="flex flex-wrap gap-3 mb-4">
          {tiers.map((tier) => (
            <div key={tier.tier_name} className="flex items-center gap-2">
              <span className={`text-xs font-medium ${tier.enabled === 1 ? 'text-gray-300' : 'text-[#5A6478]'}`}>
                {tier.tier_name}
              </span>
              <Toggle checked={tier.enabled === 1} onChange={() => handleTierToggle(tier)} />
            </div>
          ))}
        </div>

        <div className="border-t border-[#1E2330] pt-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={13} className="text-[#5A6478]" />
            <span className="text-[10px] font-semibold text-[#5A6478] uppercase tracking-widest">Cap Group Filter</span>
            <span className="ml-auto text-[10px] text-[#3A4255]">Enables stocks by daily volume group</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { name: 'MEGA', label: 'Mega Cap', desc: '>100cr/day vol' },
              { name: 'LARGE', label: 'Large Cap', desc: '10–100cr/day vol' },
              { name: 'MID', label: 'Mid Cap', desc: '1–10cr/day vol' },
              { name: 'SMALL', label: 'Small Cap', desc: '10L–1cr/day vol' },
            ].map(({ name, label, desc }) => {
              const group = volumeGroups.find((g) => g.group_name === name)
              const enabled = group?.enabled === 1
              return (
                <div key={name} className={`rounded-xl border p-3.5 transition-all ${enabled ? 'border-[#2979FF]/30 bg-[#2979FF]/5' : 'border-[#1E2330] bg-transparent'}`}>
                  <div className="flex items-center justify-between mb-2">
                    <span className={`text-xs font-bold ${enabled ? 'text-[#2979FF]' : 'text-[#5A6478]'}`}>{label}</span>
                    <Toggle checked={enabled} onChange={() => group && handleVolumeGroupToggle(group)} />
                  </div>
                  <p className="text-[10px] text-[#3A4255]">{desc}</p>
                  {!group && <p className="text-[10px] text-[#FF5252] mt-1">Not in DB</p>}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Tier filter tabs + search + enabled-only */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Tabs */}
        <div className="flex flex-wrap gap-1 flex-1">
          {TIER_TABS.map((tab) => {
            const count = tab === 'All'
              ? items.length
              : items.filter((i) => i.tiers.includes(tab)).length
            return (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={`relative px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 ${
                  activeTab === tab
                    ? 'bg-[#2979FF]/10 text-[#2979FF] border border-[#2979FF]/30'
                    : 'text-[#5A6478] hover:text-gray-200 hover:bg-[#141720] border border-transparent'
                }`}
              >
                {tab}
                <span className={`ml-1.5 text-[10px] ${activeTab === tab ? 'text-[#2979FF]/70' : 'text-[#3A4255]'}`}>
                  {count}
                </span>
              </button>
            )
          })}
        </div>

        {/* Search */}
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#5A6478] pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search symbol…"
            className="input-sm pl-7 w-36"
          />
        </div>

        {/* Enabled-only toggle */}
        <button
          type="button"
          onClick={() => setShowEnabledOnly((v) => !v)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
            showEnabledOnly
              ? 'bg-[#00E676]/10 text-[#00E676] border-[#00E676]/30'
              : 'text-[#5A6478] border-transparent hover:bg-[#141720] hover:text-gray-200'
          }`}
        >
          <SlidersHorizontal size={11} />
          Active only
          <span className="ml-0.5 text-[10px]">
            ({enabledFiltered.filter((i) => i.enabled === 1).length})
          </span>
        </button>
      </div>

      {/* Stock table */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1E2330] flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
            {activeTab === 'All' ? 'All Symbols' : activeTab}
          </span>
          <span className="text-xs text-[#5A6478]">{filteredItems.length} shown</span>
        </div>

        {filteredItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <Layers size={20} className="text-[#3A4255]" />
            <p className="text-sm text-[#5A6478]">No symbols match your filters.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#1E2330]">
                  <th className="th">Symbol</th>
                  <th className="th">Company</th>
                  <th className="th">Tiers</th>
                  <th className="th-right">Min Vol</th>
                  <th className="th-right">Active</th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.map((item: WatchlistItem, i: number) => (
                  <motion.tr
                    key={item.security_id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(i * 0.01, 0.3) }}
                    className="tr-hover border-b border-[#1E2330] last:border-0"
                  >
                    <td className="td font-mono font-semibold text-gray-100">{item.symbol}</td>
                    <td className="td text-[#5A6478] text-xs max-w-[220px] truncate">{item.company_name}</td>
                    <td className="td">
                      <div className="flex flex-wrap gap-1">
                        {item.tiers.map((t) => (
                          <span key={t}
                            className="px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-[#1A1F2E] text-[#5A6478] border border-[#2A3045]">
                            {t}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="td text-right">
                      <VolInput item={item} onSave={handleVolSave} />
                    </td>
                    <td className="td text-right">
                      <Toggle checked={item.enabled === 1} onChange={() => handleItemEnabledToggle(item)} />
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
