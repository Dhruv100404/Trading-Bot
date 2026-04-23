export interface BrokerStatus {
  provider: string
  configured: boolean
  state: 'missing' | 'invalid' | 'expired' | 'ready' | 'degraded' | string
  message: string
  credential_source: string
  client_id: string | null
  issued_at_utc: string | null
  expires_at_utc: string | null
  live_quotes: boolean
}

export interface MarketRegime {
  label: string
  tone: 'bullish' | 'neutral' | 'cautious' | string
  summary: string
  advances: number
  declines: number
  breadth_ratio: number
}

export interface SwingCandidate {
  symbol: string
  company_name: string
  setup_family: string
  bias: string
  score: number
  confidence: string
  regime_fit: number
  risk_reward: number
  last_price: number
  day_change_pct: number
  open_gap_pct: number
  distance_to_high_pct: number
  liquidity_bucket: string
  entry_zone: string
  stop_loss: number
  target_price: number
  expected_hold: string
  thesis: string
  reasons: string[]
  risks: string[]
  source: string
}

export interface SetupMix {
  family: string
  count: number
  avg_score: number
}

export interface SwingHomeResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  top_candidates: SwingCandidate[]
  scanner_count: number
  setup_mix: SetupMix[]
}

export interface SwingScannerResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  live_data: boolean
  total_candidates: number
  candidates: SwingCandidate[]
}

export interface SwingCandidateResponse {
  updated_at: string
  broker: BrokerStatus
  market_regime: MarketRegime
  candidate: SwingCandidate | null
  message: string | null
}

export interface BrokerAccountBalance {
  availabelBalance?: number
  utilizedAmount?: number
  sodLimit?: number
  withdrawableBalance?: number
}

export interface BrokerPosition {
  tradingSymbol?: string
  securityId?: string
  positionType?: string
  netQty?: number
  realizedProfit?: number
  unrealizedProfit?: number
  costPrice?: number
}

export interface BrokerAccountSnapshot {
  client_id: string
  name: string
  broker: string
  balance: BrokerAccountBalance
  positions: BrokerPosition[]
  error?: string | null
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export async function getSwingHome(): Promise<SwingHomeResponse> {
  return apiFetch<SwingHomeResponse>('/api/swing/home')
}

export async function getSwingScanner(limit = 24): Promise<SwingScannerResponse> {
  return apiFetch<SwingScannerResponse>(`/api/swing/scanner?limit=${limit}`)
}

export async function getSwingCandidate(symbol: string): Promise<SwingCandidateResponse> {
  return apiFetch<SwingCandidateResponse>(`/api/swing/candidates/${encodeURIComponent(symbol)}`)
}

export async function getBrokerStatus(): Promise<BrokerStatus> {
  return apiFetch<BrokerStatus>('/api/swing/broker-status')
}

export async function getBrokerAccounts(): Promise<BrokerAccountSnapshot[]> {
  const data = await apiFetch<{ accounts: BrokerAccountSnapshot[] }>('/api/positions')
  return data.accounts ?? []
}
