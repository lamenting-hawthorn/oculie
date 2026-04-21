import { useState, useCallback } from 'react'
import { api, usePolling } from '../hooks/useApi'
import { ErrorState } from '../components/ErrorState'
import type { Alert } from '../types'
import {
  Bell, Send, CheckCircle, XCircle, AlertTriangle, Info,
  MessageSquare, RefreshCw,
} from 'lucide-react'

const ALERT_TYPES = ['All', 'Trade', 'Daily Summary', 'Error', 'System'] as const

function typeToApiValue(type: string): string {
  if (type === 'All') return ''
  if (type === 'Daily Summary') return 'daily_summary'
  return type.toLowerCase()
}

function typeBadge(alertType: string) {
  const lower = alertType.toLowerCase()
  if (lower === 'trade')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]">Trade</span>
  if (lower === 'daily_summary' || lower === 'daily summary')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-purple)]/20 text-[var(--accent-purple)]">Daily Summary</span>
  if (lower === 'error')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-red)]/20 text-[var(--accent-red)]">Error</span>
  if (lower === 'system')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-yellow)]/20 text-[var(--accent-yellow)]">System</span>
  return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--bg-hover)] text-[var(--text-muted)]">{alertType}</span>
}

function typeIcon(alertType: string) {
  const lower = alertType.toLowerCase()
  if (lower === 'trade') return <MessageSquare className="w-4 h-4 text-[var(--accent-blue)]" />
  if (lower === 'daily_summary' || lower === 'daily summary') return <Info className="w-4 h-4 text-[var(--accent-purple)]" />
  if (lower === 'error') return <AlertTriangle className="w-4 h-4 text-[var(--accent-red)]" />
  if (lower === 'system') return <Bell className="w-4 h-4 text-[var(--accent-yellow)]" />
  return <Bell className="w-4 h-4 text-[var(--text-muted)]" />
}

function formatTimestamp(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)

  let relative: string
  if (diffMins < 1) relative = 'just now'
  else if (diffMins < 60) relative = `${diffMins}m ago`
  else if (diffHours < 24) relative = `${diffHours}h ago`
  else relative = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

  const full = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })

  return { relative, full }
}

export default function Alerts() {
  const [typeFilter, setTypeFilter] = useState('All')
  const [testStatus, setTestStatus] = useState<'idle' | 'sending' | 'success' | 'error'>('idle')

  const fetchAlerts = useCallback(() => {
    const params = new URLSearchParams({ limit: '50' })
    const apiType = typeToApiValue(typeFilter)
    if (apiType) params.set('alert_type', apiType)
    return api<Alert[]>(`/api/alerts?${params}`)
  }, [typeFilter])

  const { data: alerts, error, loading, refresh } = usePolling(fetchAlerts, 10000)

  async function sendTestAlert() {
    setTestStatus('sending')
    try {
      const result = await api<{ success: boolean }>('/api/alerts/test', { method: 'POST' })
      setTestStatus(result.success ? 'success' : 'error')
    } catch {
      setTestStatus('error')
    }
    setTimeout(() => setTestStatus('idle'), 3000)
  }

  if (error && !alerts) {
    return <ErrorState message="Failed to load alerts" onRetry={refresh} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <h1 className="text-2xl font-bold text-[var(--text-primary)]">Alerts</h1>
        <div className="flex items-center gap-3">
          <button
            onClick={refresh}
            className="flex items-center gap-2 px-3 py-2 bg-[var(--bg-card)] text-[var(--text-secondary)] rounded-lg text-sm border border-[var(--border)] hover:text-[var(--text-primary)] transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={sendTestAlert}
            disabled={testStatus === 'sending'}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              testStatus === 'success'
                ? 'bg-[var(--accent-green)] text-white'
                : testStatus === 'error'
                  ? 'bg-[var(--accent-red)] text-white'
                  : 'bg-[var(--accent-blue)] text-white hover:opacity-90'
            } disabled:opacity-50`}
          >
            {testStatus === 'idle' && <><Send className="w-4 h-4" /> Send Test Alert</>}
            {testStatus === 'sending' && <><RefreshCw className="w-4 h-4 animate-spin" /> Sending...</>}
            {testStatus === 'success' && <><CheckCircle className="w-4 h-4" /> Sent!</>}
            {testStatus === 'error' && <><XCircle className="w-4 h-4" /> Failed</>}
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm text-[var(--text-muted)]">Filter by type:</span>
          {ALERT_TYPES.map(type => (
            <button
              key={type}
              onClick={() => setTypeFilter(type)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                typeFilter === type
                  ? 'bg-[var(--accent-blue)] text-white'
                  : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border)]'
              }`}
            >
              {type}
            </button>
          ))}
        </div>
      </div>

      {/* Message Log */}
      <div className="space-y-3">
        {loading ? (
          <div className="bg-[var(--bg-card)] rounded-lg p-12 border border-[var(--border)] text-center text-[var(--text-muted)]">
            Loading alerts...
          </div>
        ) : !alerts || alerts.length === 0 ? (
          <div className="bg-[var(--bg-card)] rounded-lg p-12 border border-[var(--border)] text-center">
            <Bell className="w-8 h-8 text-[var(--text-muted)] mx-auto mb-3" />
            <p className="text-[var(--text-muted)]">No alerts found</p>
          </div>
        ) : (
          alerts.map(alert => {
            const ts = formatTimestamp(alert.sent_at)
            return (
              <div
                key={alert.id}
                className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)] hover:border-[var(--bg-hover)] transition-colors"
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 shrink-0">{typeIcon(alert.alert_type)}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      {typeBadge(alert.alert_type)}
                      {alert.city && (
                        <span className="text-xs text-[var(--text-muted)] bg-[var(--bg-secondary)] px-2 py-0.5 rounded">
                          {alert.city}
                        </span>
                      )}
                      <span className="text-xs text-[var(--text-muted)] bg-[var(--bg-secondary)] px-2 py-0.5 rounded">
                        {alert.channel}
                      </span>
                    </div>
                    <p className="text-sm text-[var(--text-primary)] mt-1">{alert.message}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs text-[var(--text-muted)]" title={ts.full}>{ts.relative}</p>
                    <p className="text-xs text-[var(--text-muted)] mt-0.5 hidden sm:block">{ts.full}</p>
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Auto-refresh indicator */}
      <div className="text-center">
        <span className="text-xs text-[var(--text-muted)]">
          Auto-refreshing every 10 seconds
        </span>
      </div>
    </div>
  )
}
