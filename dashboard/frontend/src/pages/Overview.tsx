import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Activity,
  Power,
  TrendingUp,
  TrendingDown,
  DollarSign,
  BarChart3,
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  CircleDot,
  FileText,
  Zap,
} from 'lucide-react'
import { api, usePolling } from '../hooks/useApi'
import { useWebSocket } from '../hooks/useWebSocket'
import { ErrorState } from '../components/ErrorState'
import { timeAgo } from '../lib/format'
import type { BotStatus, AccountSummary, Position, Trade } from '../types'

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  }).format(value)
}

function formatPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatPnl(value: number): string {
  return `${value >= 0 ? '+' : ''}${formatCurrency(value)}`
}

function pnlColor(value: number): string {
  if (value > 0) return 'text-[var(--accent-green)]'
  if (value < 0) return 'text-[var(--accent-red)]'
  return 'text-[var(--text-muted)]'
}


function CountdownTimer({ target }: { target: string | null }) {
  const [remaining, setRemaining] = useState('')

  useEffect(() => {
    if (!target) {
      setRemaining('--:--')
      return
    }

    function update() {
      const diff = new Date(target!).getTime() - Date.now()
      if (diff <= 0) {
        setRemaining('Scanning...')
        return
      }
      const mins = Math.floor(diff / 60000)
      const secs = Math.floor((diff % 60000) / 1000)
      setRemaining(`${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`)
    }

    update()
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [target])

  return <span className="font-mono text-2xl text-[var(--text-primary)]">{remaining}</span>
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (outcome === 'won')
    return (
      <span className="inline-flex items-center rounded-full bg-[var(--accent-green)]/15 px-2 py-0.5 text-xs font-medium text-[var(--accent-green)]">
        Won
      </span>
    )
  if (outcome === 'lost')
    return (
      <span className="inline-flex items-center rounded-full bg-[var(--accent-red)]/15 px-2 py-0.5 text-xs font-medium text-[var(--accent-red)]">
        Lost
      </span>
    )
  return (
    <span className="inline-flex items-center rounded-full bg-[var(--accent-yellow)]/15 px-2 py-0.5 text-xs font-medium text-[var(--accent-yellow)]">
      Pending
    </span>
  )
}

export default function Overview() {
  const [toggling, setToggling] = useState(false)

  const fetchStatus = useCallback(() => api<BotStatus>('/api/status'), [])
  const fetchAccount = useCallback(() => api<AccountSummary>('/api/account'), [])
  const fetchPositions = useCallback(() => api<Position[]>('/api/positions'), [])
  const fetchTrades = useCallback(() => api<Trade[]>('/api/trades?limit=10'), [])

  const { data: status, error: statusError, refresh: refreshStatus } = usePolling(fetchStatus, 5000)
  const { data: account, error: accountError } = usePolling(fetchAccount, 5000)
  const { data: positions } = usePolling(fetchPositions, 5000)
  const { data: trades } = usePolling(fetchTrades, 5000)
  const { lastMessage } = useWebSocket()

  // React to WebSocket messages
  useEffect(() => {
    if (lastMessage) {
      refreshStatus()
    }
  }, [lastMessage, refreshStatus])

  const handleToggle = async () => {
    setToggling(true)
    try {
      await api<BotStatus>('/api/status/toggle', { method: 'POST' })
      await refreshStatus()
    } finally {
      setToggling(false)
    }
  }

  const isRunning = status?.status === 'running'

  const statsCards = useMemo(() => {
    if (!account) return []
    return [
      {
        label: 'Total Balance',
        value: formatCurrency(account.balance),
        icon: DollarSign,
        color: 'text-[var(--accent-blue)]',
      },
      {
        label: "Today's P&L",
        value: formatPnl(account.today_pnl),
        sub: formatPercent(account.today_pnl_pct),
        icon: account.today_pnl >= 0 ? TrendingUp : TrendingDown,
        color: pnlColor(account.today_pnl),
      },
      {
        label: 'All-Time P&L',
        value: formatPnl(account.alltime_pnl),
        icon: BarChart3,
        color: pnlColor(account.alltime_pnl),
      },
      {
        label: 'Trades Today',
        value: String(account.trades_today),
        sub: `${account.win_rate.toFixed(0)}% win rate`,
        icon: Activity,
        color: 'text-[var(--accent-purple)]',
      },
    ]
  }, [account])

  if (statusError && !status) {
    return <ErrorState message="Cannot reach the backend. Is the bot running?" onRetry={refreshStatus} />
  }

  return (
    <div className="space-y-6">
      {accountError && (
        <div className="rounded-lg border border-[var(--accent-red)]/30 bg-[var(--accent-red)]/10 px-4 py-2 text-xs text-[var(--accent-red)]">
          Account data unavailable — {accountError.message}
        </div>
      )}
      {/* Top Row: Bot Status + Next Scan */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Bot Status */}
        <div className="col-span-1 flex items-center justify-between rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-5 lg:col-span-2">
          <div className="flex items-center gap-4">
            <div
              className={`flex h-14 w-14 items-center justify-center rounded-full ${
                isRunning
                  ? 'bg-[var(--accent-green)]/15 text-[var(--accent-green)]'
                  : 'bg-[var(--accent-red)]/15 text-[var(--accent-red)]'
              }`}
            >
              <Zap className="h-7 w-7" />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <span
                  className={`text-xl font-bold ${
                    isRunning ? 'text-[var(--accent-green)]' : 'text-[var(--accent-red)]'
                  }`}
                >
                  {isRunning ? 'RUNNING' : 'STOPPED'}
                </span>
                {status?.paper_mode && (
                  <span className="inline-flex items-center rounded-full bg-[var(--accent-yellow)]/15 px-2.5 py-0.5 text-xs font-medium text-[var(--accent-yellow)]">
                    PAPER MODE
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-sm text-[var(--text-muted)]">
                {status?.started_at
                  ? `Started ${timeAgo(status.started_at)}`
                  : 'Bot is offline'}
                {status && ` · Scan interval: ${status.scan_interval}m`}
              </p>
            </div>
          </div>
          <button
            onClick={handleToggle}
            disabled={toggling}
            className={`flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 ${
              isRunning
                ? 'bg-[var(--accent-red)]/15 text-[var(--accent-red)] hover:bg-[var(--accent-red)]/25'
                : 'bg-[var(--accent-green)]/15 text-[var(--accent-green)] hover:bg-[var(--accent-green)]/25'
            }`}
          >
            <Power className="h-4 w-4" />
            {toggling ? '...' : isRunning ? 'Stop' : 'Start'}
          </button>
        </div>

        {/* Next Scan Countdown */}
        <div className="flex flex-col items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-5">
          <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
            <Clock className="h-4 w-4" />
            Next Scan
          </div>
          <div className="mt-2">
            <CountdownTimer target={status?.next_scan ?? null} />
          </div>
        </div>
      </div>

      {/* Account Summary */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {statsCards.map((card) => (
          <div
            key={card.label}
            className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-4"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
                {card.label}
              </span>
              <card.icon className={`h-4 w-4 ${card.color}`} />
            </div>
            <p className={`mt-2 text-xl font-bold ${card.color}`}>{card.value}</p>
            {card.sub && (
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">{card.sub}</p>
            )}
          </div>
        ))}
      </div>

      {/* Bottom Row: Positions + Recent Trades */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Active Positions */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)]">
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-5 py-4">
            <CircleDot className="h-4 w-4 text-[var(--accent-blue)]" />
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">
              Active Positions
            </h2>
            {positions && (
              <span className="ml-auto rounded-full bg-[var(--bg-hover)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
                {positions.length}
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            {!positions || positions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
                <CircleDot className="mb-2 h-8 w-8 opacity-30" />
                <p className="text-sm">No open positions</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--text-muted)]">
                    <th className="px-5 py-2.5">City</th>
                    <th className="px-3 py-2.5">Range</th>
                    <th className="px-3 py-2.5">Dir</th>
                    <th className="px-3 py-2.5 text-right">Entry</th>
                    <th className="px-3 py-2.5 text-right">Current</th>
                    <th className="px-3 py-2.5 text-right">Size</th>
                    <th className="px-5 py-2.5 text-right">Unreal. P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos) => (
                    <tr
                      key={pos.id}
                      className="border-b border-[var(--border)]/50 transition-colors hover:bg-[var(--bg-hover)]"
                    >
                      <td className="px-5 py-3 font-medium text-[var(--text-primary)]">
                        {pos.city}
                      </td>
                      <td className="max-w-[140px] truncate px-3 py-3 text-[var(--text-secondary)]">
                        {pos.market_question}
                      </td>
                      <td className="px-3 py-3">
                        <span
                          className={`inline-flex items-center gap-1 text-xs font-medium ${
                            pos.direction === 'YES'
                              ? 'text-[var(--accent-green)]'
                              : 'text-[var(--accent-red)]'
                          }`}
                        >
                          {pos.direction === 'YES' ? (
                            <ArrowUpRight className="h-3 w-3" />
                          ) : (
                            <ArrowDownRight className="h-3 w-3" />
                          )}
                          {pos.direction}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-[var(--text-secondary)]">
                        {pos.entry_price.toFixed(2)}
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-[var(--text-secondary)]">
                        {pos.current_price != null ? pos.current_price.toFixed(2) : '—'}
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-[var(--text-secondary)]">
                        {formatCurrency(pos.size)}
                      </td>
                      <td
                        className={`px-5 py-3 text-right font-mono font-medium ${pnlColor(
                          pos.unrealized_pnl ?? 0
                        )}`}
                      >
                        {pos.unrealized_pnl != null ? formatPnl(pos.unrealized_pnl) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Recent Trades */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)]">
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-5 py-4">
            <FileText className="h-4 w-4 text-[var(--accent-purple)]" />
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">
              Recent Trades
            </h2>
          </div>
          <div className="divide-y divide-[var(--border)]/50">
            {!trades || trades.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
                <FileText className="mb-2 h-8 w-8 opacity-30" />
                <p className="text-sm">No trades yet</p>
              </div>
            ) : (
              trades.map((trade) => (
                <div
                  key={trade.id}
                  className="flex items-center justify-between px-5 py-3 transition-colors hover:bg-[var(--bg-hover)]"
                >
                  <div className="flex items-center gap-3">
                    <span
                      className={`flex h-8 w-8 items-center justify-center rounded-lg text-xs font-bold ${
                        trade.direction?.toUpperCase() === 'YES'
                          ? 'bg-[var(--accent-green)]/15 text-[var(--accent-green)]'
                          : 'bg-[var(--accent-red)]/15 text-[var(--accent-red)]'
                      }`}
                    >
                      {trade.direction?.toUpperCase() === 'YES' ? (
                        <ArrowUpRight className="h-4 w-4" />
                      ) : (
                        <ArrowDownRight className="h-4 w-4" />
                      )}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-[var(--text-primary)]">
                        {trade.city}
                      </p>
                      <p className="text-xs text-[var(--text-muted)]">
                        Entry: {trade.entry_price.toFixed(2)}
                        {trade.paper_trade && ' · Paper'}
                        {' · '}
                        {timeAgo(trade.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <OutcomeBadge outcome={trade.outcome} />
                    {trade.pnl != null && (
                      <span
                        className={`font-mono text-sm font-medium ${pnlColor(trade.pnl)}`}
                      >
                        {formatPnl(trade.pnl)}
                      </span>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
