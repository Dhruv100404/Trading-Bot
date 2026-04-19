import { useEffect, useRef, useState, useCallback } from 'react'

export interface WsEvent {
  id: number
  type: 'signal_fired' | 'exit' | 'poll_done' | 'unknown'
  raw: Record<string, unknown>
  receivedAt: string // ISO string
}

let eventIdCounter = 0

export function useWebSocket(): { events: WsEvent[]; connected: boolean } {
  const [events, setEvents] = useState<WsEvent[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const unmounted = useRef(false)

  const connect = useCallback(() => {
    if (unmounted.current) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const ws = new WebSocket(`${protocol}//${host}/ws`)
    wsRef.current = ws

    ws.onopen = () => {
      if (!unmounted.current) setConnected(true)
    }

    ws.onmessage = (evt: MessageEvent<string>) => {
      if (unmounted.current) return
      try {
        const raw = JSON.parse(evt.data) as Record<string, unknown>
        const type = (raw.type as WsEvent['type']) ?? 'unknown'
        const event: WsEvent = {
          id: ++eventIdCounter,
          type,
          raw,
          receivedAt: new Date().toISOString(),
        }
        setEvents((prev) => [event, ...prev].slice(0, 50))
      } catch {
        // ignore non-JSON frames
      }
    }

    ws.onclose = () => {
      if (unmounted.current) return
      setConnected(false)
      wsRef.current = null
      // Auto-reconnect after 3 seconds
      reconnectTimer.current = setTimeout(() => {
        if (!unmounted.current) connect()
      }, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    unmounted.current = false
    connect()
    return () => {
      unmounted.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on intentional unmount
        wsRef.current.close()
      }
    }
  }, [connect])

  return { events, connected }
}
