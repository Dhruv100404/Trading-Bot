// ─── Types ────────────────────────────────────────────────────────────────────

export interface MarketStatus {
  market_status: 'PRE-OPEN' | 'LIVE' | 'CLOSED'
  current_ist: string
  today: string
}

export interface Signal {
  id: number
  symbol: string
  direction: 'BUY' | 'SELL'
  entry_price: number
  entry_bucket: string
  score: number
  signals_fired: string
  tp_price: number
  sl_price: number
  quantity: number
  exit_price: number | null
  exit_bucket: string | null
  exit_reason: 'TP' | 'SL' | 'TIME' | null
  actual_return_pct: number | null
  pnl_rupees: number | null
}

export interface Snapshot {
  bucket: number
  ltp: number
  candle_open: number
  candle_high: number
  candle_low: number
  volume_cum: number
  volume_delta: number
  vwap: number
  volume_rate: number
  candle_body_ratio: number
}

export interface DailyRef {
  prev_close: number
  day_open: number
  gap_pct: number
}

export interface PerformanceRow {
  trading_date: string
  buy_signals: number
  sell_signals: number
  profitable: number
  losses: number
  avg_return_pct: number
  net_pnl: number
  capital_used: number
  roc_pct: number
}

/** Gap-15 strategy config — matches Rust Gap15Config exactly */
export interface Gap15Config {
  total_capital: number   // 50000
  leverage: number        // 5
  top_n: number           // 15
  tp_pct: number          // 3.0
  sl_pct: number          // 0.5
  exit_bucket: number     // 45
  gap_min_pct: number     // 1.5
  gap_max_pct: number     // 15.0
  price_max: number       // 1000.0
  cap_mult: number        // 2.0
}

export const DEFAULT_GAP15_CONFIG: Gap15Config = {
  total_capital: 50000,
  leverage: 5,
  top_n: 15,
  tp_pct: 3.0,
  sl_pct: 0.5,
  exit_bucket: 45,
  gap_min_pct: 1.5,
  gap_max_pct: 15.0,
  price_max: 1000.0,
  cap_mult: 2.0,
}

export interface Account {
  name: string
  client_id: string
  mode: 'PAPER' | 'LIVE'
  enabled: 0 | 1
  broker: 'DHAN' | 'ZERODHA'
}

export interface WatchlistItem {
  security_id: number
  symbol: string
  company_name: string
  tiers: string[]
  enabled: 0 | 1
  min_volume: number
}

export interface Tier {
  tier_name: string
  enabled: 0 | 1
}

export interface VolumeGroup {
  group_name: string
  enabled: 0 | 1
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// ─── Status ───────────────────────────────────────────────────────────────────

export async function getStatus(): Promise<MarketStatus> {
  return apiFetch<MarketStatus>('/api/status')
}

// ─── Signals ──────────────────────────────────────────────────────────────────

export async function getSignals(params?: { date?: string; symbol?: string }): Promise<Signal[]> {
  const q = new URLSearchParams()
  if (params?.date) q.set('date', params.date)
  if (params?.symbol) q.set('symbol', params.symbol)
  const qs = q.toString() ? `?${q.toString()}` : ''
  const data = await apiFetch<{ signals: Signal[] }>(`/api/signals${qs}`)
  return data.signals || []
}

// ─── Snapshots ────────────────────────────────────────────────────────────────

export async function getSnapshots(params?: { symbol?: string; date?: string }): Promise<Snapshot[]> {
  const q = new URLSearchParams()
  if (params?.symbol) q.set('symbol', params.symbol)
  if (params?.date) q.set('date', params.date)
  const qs = q.toString() ? `?${q.toString()}` : ''
  const data = await apiFetch<{ snapshots: Snapshot[] }>(`/api/snapshots${qs}`)
  return data.snapshots
}

export interface SnapshotWithSymbol extends Snapshot {
  symbol: string
  trading_date: string
}

export async function getSnapshotsBulk(from: string, to: string): Promise<SnapshotWithSymbol[]> {
  const q = new URLSearchParams({ from, to })
  const data = await apiFetch<{
    snapshots?: SnapshotWithSymbol[];
    snapshots_compact?: { meta: { name: string }[]; data: any[][] };
    error?: string;
  }>(`/api/snapshots/bulk?${q.toString()}`)
  if (data.error) throw new Error(data.error)

  if (data.snapshots_compact?.data) {
    const cols = data.snapshots_compact.meta.map(m => m.name)
    return data.snapshots_compact.data.map(row => {
      const obj: any = {}
      for (let i = 0; i < cols.length; i++) obj[cols[i]] = row[i]
      return obj as SnapshotWithSymbol
    })
  }

  return data.snapshots ?? []
}

// ─── Daily Ref ────────────────────────────────────────────────────────────────

export interface DailyRefWithSymbol extends DailyRef {
  symbol: string
  trading_date: string
}

export async function getDailyRef(params: { symbol: string; date: string }): Promise<DailyRef | null> {
  const q = new URLSearchParams({ symbol: params.symbol, date: params.date })
  const data = await apiFetch<{ daily_ref: DailyRef | null }>(`/api/daily_ref?${q.toString()}`)
  return data.daily_ref
}

export async function getDailyRefBulk(from: string, to: string): Promise<DailyRefWithSymbol[]> {
  const q = new URLSearchParams({ from, to })
  const data = await apiFetch<{ daily_refs?: DailyRefWithSymbol[]; error?: string }>(`/api/daily_ref/bulk?${q.toString()}`)
  if (data.error) throw new Error(data.error)
  return data.daily_refs ?? []
}

// ─── Performance ──────────────────────────────────────────────────────────────

export async function getPerformance(): Promise<PerformanceRow[]> {
  const data = await apiFetch<{ performance: Record<string, unknown>[] }>('/api/performance')
  return (data.performance ?? []).map((r) => ({
    trading_date: String(r.trading_date ?? ''),
    buy_signals: Number(r.buy_signals ?? 0),
    sell_signals: Number(r.sell_signals ?? 0),
    profitable: Number(r.profitable ?? 0),
    losses: Number(r.losses ?? 0),
    avg_return_pct: Number(r.avg_return_pct ?? 0),
    net_pnl: Number(r.net_pnl ?? 0),
    capital_used: Number(r.capital_used ?? 0),
    roc_pct: Number(r.roc_pct ?? 0),
  }))
}

// ─── Gap15 Config ─────────────────────────────────────────────────────────────

export async function getConfig(): Promise<Gap15Config> {
  return apiFetch<Gap15Config>('/api/config')
}

export async function putConfig(config: Gap15Config): Promise<Gap15Config> {
  return apiFetch<Gap15Config>('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

// ─── Accounts ─────────────────────────────────────────────────────────────────

export async function getAccounts(): Promise<Account[]> {
  const data = await apiFetch<{ accounts: Account[] }>('/api/accounts')
  return data.accounts
}

export type AccountHealth = Record<string, { ok: boolean; error: string }>

export async function getAccountHealth(): Promise<AccountHealth> {
  const data = await apiFetch<{ health: AccountHealth }>('/api/accounts/health')
  return data.health
}

export async function postAccount(payload: { name: string; client_id: string; access_token: string; broker?: string; api_key?: string; api_secret?: string }): Promise<void> {
  await apiFetch<unknown>('/api/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function patchAccount(
  clientId: string,
  patch: { mode?: 'PAPER' | 'LIVE'; enabled?: 0 | 1 }
): Promise<void> {
  await apiFetch<unknown>(`/api/accounts/${clientId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export async function deleteAccount(clientId: string): Promise<void> {
  await apiFetch<unknown>(`/api/accounts/${clientId}`, { method: 'DELETE' })
}

// ─── Watchlist ────────────────────────────────────────────────────────────────

export async function getWatchlist(enabled?: 0 | 1): Promise<WatchlistItem[]> {
  const qs = enabled !== undefined ? `?enabled=${enabled}` : ''
  const data = await apiFetch<{ watchlist: WatchlistItem[] }>(`/api/watchlist${qs}`)
  return data.watchlist
}

export async function patchWatchlistItem(
  securityId: number,
  patch: { enabled?: 0 | 1; min_volume?: number }
): Promise<void> {
  await apiFetch<unknown>(`/api/watchlist/${securityId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export async function getTiers(): Promise<Tier[]> {
  const data = await apiFetch<{ tiers: Tier[] }>('/api/watchlist/tiers')
  return data.tiers
}

export async function patchTier(name: string, enabled: 0 | 1): Promise<void> {
  await apiFetch<unknown>(`/api/watchlist/tiers/${name}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

export async function getVolumeGroups(): Promise<VolumeGroup[]> {
  const data = await apiFetch<{ volume_groups: VolumeGroup[] }>('/api/watchlist/volume-groups')
  return data.volume_groups
}

export async function patchVolumeGroup(name: string, enabled: 0 | 1): Promise<void> {
  await apiFetch<unknown>(`/api/watchlist/volume-groups/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

// ─── Close All ────────────────────────────────────────────────────────────────

export interface CloseAllResult {
  ok: boolean
  closed: number
  total: number
  errors: string[]
  message?: string
}

export async function postCloseAll(): Promise<CloseAllResult> {
  return apiFetch<CloseAllResult>('/api/close-all', { method: 'POST' })
}
