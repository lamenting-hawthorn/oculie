import { useEffect, useRef, useCallback, useState } from 'react'

export function useWebSocket(url?: string) {
  const wsUrl = url ?? `ws://${window.location.host}/ws`
  const wsRef = useRef<WebSocket | null>(null)
  const [lastMessage, setLastMessage] = useState<Record<string, unknown> | null>(null)
  const [connected, setConnected] = useState(false)
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        reconnectRef.current = setTimeout(connect, 3000)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (event) => {
        try {
          setLastMessage(JSON.parse(event.data))
        } catch {
          /* ignore non-JSON */
        }
      }
    }

    connect()

    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [wsUrl])

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(JSON.stringify(data))
  }, [])

  return { lastMessage, connected, send }
}
