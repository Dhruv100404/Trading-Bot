// StatusBanner is now integrated into the App.tsx top navigation.
// This file is kept for compatibility but renders nothing.
import type { MarketStatus } from '../api'

interface StatusBannerProps {
  status: MarketStatus | null
  connected: boolean
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function StatusBanner(_props: StatusBannerProps) {
  return null
}
