import { useState, useCallback, useMemo } from 'react'
import { api, usePolling } from '../hooks/useApi'
import { ErrorState } from '../components/ErrorState'
import type { Trade, PnlDataPoint } from '../types'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  ChevronDown, ChevronUp, TrendingUp, Trophy, BarChart3, Calendar,
  ArrowUpRight, ArrowDownRight, Filter, Clock,
} from 'lucide-react'

const CITIES = [
  'All', 'New York', 'Chicago', 'Miami', 'Dallas', 'Seattle',
  'Atlanta', 'London', 'Seoul', 'Shanghai', 'Hong Kong',
]

const OUTCOMES = ['All', 'Won', 'Lost', 'Pending'] as const
const DATE_RANGES = ['Today', 'This Week', 'This Month', 'All Time'] as const
const PNL_PERIODS = ['daily', 'weekly', 'alltime'] as const

type SortKey = 'created_at' | 'city' | 'direction' | 'noaa_probability' | 'market_price' | 'edge' | 'bet_size' | 'outcome' | 'pnl'
type SortDir = 'asc' | 'desc'

function outcomeBadge(outcome: string | null) {
  const lower = outcome?.toLowerCase() ?? ''
  if (lower === 'won' || lower === 'win')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-green)]/20 text-[var(--accent-green)]">Won</span>
  if (lower === 'lost' || lower === 'loss')
    return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-red)]/20 text-[var(--accent-red)]">Lost</span>
  return <span className="px-2 py-0.5 rounded text-xs font-semibold bg-[var(--accent-yellow)]/20 text-[var(--accent-yellow)]">Pending</span>
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function dateInRange(iso: string, range: string): boolean {
  if (range === 'All Time') return true
  const d = new Date(iso)
  const now = new Date()
  if (range === 'Today') {
    return d.toDateString() === now.toDateString()
  }
  if (range === 'This Week') {
    const weekAgo = new Date(now)
    weekAgo.setDate(now.getDate() - 7)
    return d >= weekAgo
  }
  if (range === 'This Month') {
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
  }
  return true
}

function TradeDetailRow({ trade }: { trade: Trade }) {
  return (
    <tr>
      <td colSpan={10} className="px-4 py-4 bg-[var(--bg-secondary)] border-b border-[var(--border)]">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-[var(--text-muted)]">Market Question</span>
            <p className="text-[var(--text-primary)] mt-1">{trade.market_question}</p>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Entry Price</span>
            <p className="text-[var(--text-primary)] mt-1">${trade.entry_price.toFixed(2)}</p>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Exit Price</span>
            <p className="text-[var(--text-primary)] mt-1">{trade.exit_price != null ? `$${trade.exit_price.toFixed(2)}` : '---'}</p>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Paper Trade</span>
            <p className="text-[var(--text-primary)] mt-1">{trade.paper_trade ? 'Yes' : 'No'}</p>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Resolved At</span>
            <p className="text-[var(--text-primary)] mt-1">{trade.resolved_at ? formatDate(trade.resolved_at) : '---'}</p>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Trade ID</span>
            <p className="text-[var(--text-primary)] mt-1">#{trade.id}</p>
          </div>
        </div>
      </td>
    </tr>
  )
}

export default function TradeHistory() {
  const [cityFilter, setCityFilter] = useState('All')
  const [outcomeFilter, setOutcomeFilter] = useState<string>('All')
  const [dateRange, setDateRange] = useState<string>('All Time')
  const [pnlPeriod, setPnlPeriod] = useState<string>('alltime')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('created_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const fetchTrades = useCallback(() => {
    const params = new URLSearchParams({ limit: '200', offset: '0' })
    if (cityFilter !== 'All') params.set('city', cityFilter)
    if (outcomeFilter !== 'All') params.set('outcome', outcomeFilter.toLowerCase())
    return api<Trade[]>(`/api/trades?${params}`)
  }, [cityFilter, outcomeFilter])

  const fetchPnl = useCallback(
    () => api<PnlDataPoint[]>(`/api/trades/pnl?period=${pnlPeriod}`),
    [pnlPeriod],
  )

  const { data: trades, error: tradesError, loading: tradesLoading, refresh: refreshTrades } = usePolling(fetchTrades, 15000)
  const { data: pnlData, loading: pnlLoading } = usePolling(fetchPnl, 30000)

  const filtered = useMemo(() => {
    if (!trades) return []
    return trades.filter(t => dateInRange(t.created_at, dateRange))
  }, [trades, dateRange])

  const sorted = useMemo(() => {
    const arr = [...filtered]
    arr.sort((a, b) => {
      let av: number | string = 0, bv: number | string = 0
      switch (sortKey) {
        case 'created_at': av = a.created_at; bv = b.created_at; break
        case 'city': av = a.city; bv = b.city; break
        case 'direction': av = a.direction ?? ''; bv = b.direction ?? ''; break
        case 'noaa_probability': av = a.noaa_probability; bv = b.noaa_probability; break
        case 'market_price': av = a.market_price; bv = b.market_price; break
        case 'edge': av = a.edge; bv = b.edge; break
        case 'bet_size': av = a.bet_size; bv = b.bet_size; break
        case 'outcome': av = a.outcome ?? ''; bv = b.outcome ?? ''; break
        case 'pnl': av = a.pnl ?? 0; bv = b.pnl ?? 0; break
      }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return arr
  }, [filtered, sortKey, sortDir])

  const stats = useMemo(() => {
    if (!filtered.length) return { winRate: 0, total: 0, wins: 0, losses: 0, pending: 0 }
    const resolved = filtered.filter(t => t.outcome?.toLowerCase() === 'won' || t.outcome?.toLowerCase() === 'win' || t.outcome?.toLowerCase() === 'lost' || t.outcome?.toLowerCase() === 'loss')
    const wins = resolved.filter(t => t.outcome?.toLowerCase() === 'won' || t.outcome?.toLowerCase() === 'win').length
    const losses = resolved.length - wins
    const pending = filtered.length - resolved.length
    return {
      winRate: resolved.length > 0 ? (wins / resolved.length) * 100 : 0,
      total: filtered.length,
      wins,
      losses,
      pending,
    }
  }, [filtered])

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col) return <ChevronDown className="w-3 h-3 opacity-30 inline ml-1" />
    return sortDir === 'asc'
      ? <ChevronUp className="w-3 h-3 inline ml-1 text-[var(--accent-blue)]" />
      : <ChevronDown className="w-3 h-3 inline ml-1 text-[var(--accent-blue)]" />
  }

  if (tradesError && !trades) {
    return <ErrorState message="Failed to load trade history" onRetry={refreshTrades} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-[var(--text-primary)]">Trade History</h1>
      </div>

      {/* Win Rate Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
          <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm mb-1">
            <Trophy className="w-4 h-4" /> Win Rate
          </div>
          <p className="text-2xl font-bold text-[var(--accent-green)]">{stats.winRate.toFixed(1)}%</p>
          {stats.total > 0 && stats.wins === 0 && stats.losses === 0 && (
            <p className="text-xs text-[var(--text-muted)] mt-1">No resolved trades yet</p>
          )}
        </div>
        <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
          <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm mb-1">
            <BarChart3 className="w-4 h-4" /> Total Trades
          </div>
          <p className="text-2xl font-bold text-[var(--text-primary)]">{stats.total}</p>
        </div>
        <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
          <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm mb-1">
            <ArrowUpRight className="w-4 h-4 text-[var(--accent-green)]" /> Wins
          </div>
          <p className="text-2xl font-bold text-[var(--accent-green)]">{stats.wins}</p>
        </div>
        <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
          <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm mb-1">
            <ArrowDownRight className="w-4 h-4 text-[var(--accent-red)]" /> Losses
          </div>
          <p className="text-2xl font-bold text-[var(--accent-red)]">{stats.losses}</p>
        </div>
        <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
          <div className="flex items-center gap-2 text-[var(--text-muted)] text-sm mb-1">
            <Clock className="w-4 h-4 text-[var(--accent-yellow)]" /> Pending
          </div>
          <p className="text-2xl font-bold text-[var(--accent-yellow)]">{stats.pending}</p>
        </div>
      </div>
      {stats.pending > 0 && stats.wins === 0 && stats.losses === 0 && (
        <div className="bg-[var(--accent-yellow)]/5 border border-[var(--accent-yellow)]/20 rounded-lg px-4 py-3 text-sm text-[var(--text-secondary)]">
          All {stats.pending} trades are awaiting market resolution. Win/loss stats and P&L will update once Polymarket markets close and resolve.
        </div>
      )}

      {/* P&L Chart */}
      <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-[var(--accent-green)]" />
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Cumulative P&L</h2>
          </div>
          <div className="flex gap-1">
            {PNL_PERIODS.map(p => (
              <button
                key={p}
                onClick={() => setPnlPeriod(p)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  pnlPeriod === p
                    ? 'bg-[var(--accent-blue)] text-white'
                    : 'bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {p === 'alltime' ? 'All Time' : p.charAt(0).toUpperCase() + p.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <div className="h-64">
          {pnlLoading ? (
            <div className="h-full flex items-center justify-center text-[var(--text-muted)]">Loading chart...</div>
          ) : pnlData && pnlData.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={pnlData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis
                  dataKey="date"
                  stroke="var(--text-muted)"
                  tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
                  tickFormatter={(v: string) => {
                    const d = new Date(v)
                    return `${d.getMonth() + 1}/${d.getDate()}`
                  }}
                />
                <YAxis
                  stroke="var(--text-muted)"
                  tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
                  tickFormatter={(v: number) => `$${v}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--border)',
                    borderRadius: '8px',
                    color: 'var(--text-primary)',
                  }}
                  labelFormatter={(l) => formatDate(String(l))}
                  formatter={(v) => [`$${Number(v).toFixed(2)}`, 'P&L']}
                />
                <Line
                  type="monotone"
                  dataKey="cumulative_pnl"
                  stroke="var(--accent-green)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: 'var(--accent-green)' }}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex items-center justify-center text-[var(--text-muted)]">No P&L data available</div>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="bg-[var(--bg-card)] rounded-lg p-4 border border-[var(--border)]">
        <div className="flex items-center gap-2 mb-3">
          <Filter className="w-4 h-4 text-[var(--text-muted)]" />
          <span className="text-sm font-medium text-[var(--text-secondary)]">Filters</span>
        </div>
        <div className="flex flex-wrap gap-4">
          {/* City */}
          <div>
            <label className="text-xs text-[var(--text-muted)] mb-1 block">City</label>
            <select
              value={cityFilter}
              onChange={e => setCityFilter(e.target.value)}
              className="bg-[var(--bg-secondary)] text-[var(--text-primary)] border border-[var(--border)] rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent-blue)]"
            >
              {CITIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          {/* Outcome */}
          <div>
            <label className="text-xs text-[var(--text-muted)] mb-1 block">Outcome</label>
            <div className="flex gap-1">
              {OUTCOMES.map(o => (
                <button
                  key={o}
                  onClick={() => setOutcomeFilter(o)}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    outcomeFilter === o
                      ? 'bg-[var(--accent-blue)] text-white'
                      : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>

          {/* Date Range */}
          <div>
            <label className="text-xs text-[var(--text-muted)] mb-1 block">
              <Calendar className="w-3 h-3 inline mr-1" />Date Range
            </label>
            <div className="flex gap-1">
              {DATE_RANGES.map(r => (
                <button
                  key={r}
                  onClick={() => setDateRange(r)}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    dateRange === r
                      ? 'bg-[var(--accent-blue)] text-white'
                      : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Trade Table */}
      <div className="bg-[var(--bg-card)] rounded-lg border border-[var(--border)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-[var(--text-muted)] text-left">
                {([
                  ['created_at', 'Date/Time'],
                  ['city', 'City'],
                  ['direction', 'Direction'],
                  ['noaa_probability', 'NOAA %'],
                  ['market_price', 'Market'],
                  ['edge', 'Edge'],
                  ['bet_size', 'Bet Size'],
                  ['outcome', 'Outcome'],
                  ['pnl', 'P&L'],
                ] as [SortKey, string][]).map(([key, label]) => (
                  <th
                    key={key}
                    className="px-4 py-3 font-medium cursor-pointer hover:text-[var(--text-primary)] transition-colors select-none"
                    onClick={() => toggleSort(key)}
                  >
                    {label}<SortIcon col={key} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tradesLoading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-[var(--text-muted)]">
                    Loading trades...
                  </td>
                </tr>
              ) : sorted.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-[var(--text-muted)]">
                    No trades found
                  </td>
                </tr>
              ) : (
                sorted.map(trade => (
                  <>
                    <tr
                      key={trade.id}
                      onClick={() => setExpandedId(expandedId === trade.id ? null : trade.id)}
                      className="border-b border-[var(--border)] hover:bg-[var(--bg-hover)] cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3 text-[var(--text-secondary)] whitespace-nowrap">{formatDate(trade.created_at)}</td>
                      <td className="px-4 py-3 text-[var(--text-primary)] font-medium">{trade.city}</td>
                      <td className="px-4 py-3">
                        <span className={trade.direction?.toLowerCase() === 'yes' ? 'text-[var(--accent-green)]' : 'text-[var(--accent-red)]'}>
                          {trade.direction?.toUpperCase() ?? '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-[var(--text-secondary)]">{(trade.noaa_probability * 100).toFixed(1)}%</td>
                      <td className="px-4 py-3 text-[var(--text-secondary)]">${trade.market_price.toFixed(2)}</td>
                      <td className="px-4 py-3">
                        <span className={trade.edge > 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-red)]'}>
                          {(trade.edge * 100).toFixed(1)}%
                        </span>
                      </td>
                      <td className="px-4 py-3 text-[var(--text-secondary)]">${trade.bet_size.toFixed(2)}</td>
                      <td className="px-4 py-3">{outcomeBadge(trade.outcome)}</td>
                      <td className="px-4 py-3 font-medium">
                        {trade.pnl != null ? (
                          <span className={trade.pnl >= 0 ? 'text-[var(--accent-green)]' : 'text-[var(--accent-red)]'}>
                            {trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-[var(--text-muted)]">---</span>
                        )}
                      </td>
                    </tr>
                    {expandedId === trade.id && <TradeDetailRow key={`detail-${trade.id}`} trade={trade} />}
                  </>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
