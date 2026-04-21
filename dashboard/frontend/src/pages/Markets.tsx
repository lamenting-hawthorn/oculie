import { useState, useCallback, useEffect } from 'react'
import {
  MapPin,
  Thermometer,
  TrendingUp,
  TrendingDown,
  Eye,
  CheckCircle2,
  MinusCircle,
  ToggleLeft,
  ToggleRight,
  RefreshCw,
  X,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react'
import { api, usePolling } from '../hooks/useApi'
import { useWebSocket } from '../hooks/useWebSocket'
import { ErrorState } from '../components/ErrorState'
import { timeAgo } from '../lib/format'
import type { CityMarket, CityDetail, MarketBucket } from '../types'

function formatEdge(edge: number | null): string {
  if (edge == null) return '--'
  return `${edge >= 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`
}

function formatPrice(price: number | null): string {
  if (price == null) return '--'
  return `${(price * 100).toFixed(1)}\u00A2`
}

function formatTemp(temp: number | null, unit: string): string {
  if (temp == null) return '--'
  return `${temp.toFixed(0)}${unit === 'F' ? '\u00B0F' : '\u00B0C'}`
}


function StatusBadge({ status }: { status: CityMarket['status'] }) {
  switch (status) {
    case 'watching':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-yellow)]/15 px-2.5 py-0.5 text-xs font-medium text-[var(--accent-yellow)]">
          <Eye className="h-3 w-3" />
          Watching
        </span>
      )
    case 'entered':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-green)]/15 px-2.5 py-0.5 text-xs font-medium text-[var(--accent-green)]">
          <CheckCircle2 className="h-3 w-3" />
          Entered
        </span>
      )
    case 'no_opportunity':
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--text-muted)]/15 px-2.5 py-0.5 text-xs font-medium text-[var(--text-muted)]">
          <MinusCircle className="h-3 w-3" />
          No Opportunity
        </span>
      )
  }
}

function BucketTable({ buckets }: { buckets: MarketBucket[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--text-muted)]">
            <th className="px-4 py-2.5">Temp Range</th>
            <th className="px-4 py-2.5 text-right">NOAA Prob</th>
            <th className="px-4 py-2.5 text-right">Market Price</th>
            <th className="px-4 py-2.5 text-right">Edge</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((b, i) => {
            const edgeColor =
              b.edge > 0
                ? 'text-[var(--accent-green)]'
                : b.edge < 0
                  ? 'text-[var(--accent-red)]'
                  : 'text-[var(--text-muted)]'
            return (
              <tr
                key={i}
                className="border-b border-[var(--border)]/50 transition-colors hover:bg-[var(--bg-hover)]"
              >
                <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">
                  {b.temp_low}° - {b.temp_high}°
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-[var(--text-secondary)]">
                  {(b.noaa_prob * 100).toFixed(1)}%
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-[var(--text-secondary)]">
                  {(b.market_price * 100).toFixed(1)}\u00A2
                </td>
                <td className={`px-4 py-2.5 text-right font-mono font-medium ${edgeColor}`}>
                  <span className="inline-flex items-center gap-1">
                    {b.edge > 0 ? (
                      <ArrowUpRight className="h-3 w-3" />
                    ) : b.edge < 0 ? (
                      <ArrowDownRight className="h-3 w-3" />
                    ) : null}
                    {(b.edge * 100).toFixed(1)}%
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {buckets.length === 0 && (
        <div className="flex items-center justify-center py-8 text-sm text-[var(--text-muted)]">
          No bucket data available
        </div>
      )}
    </div>
  )
}

function DetailModal({
  cityName,
  onClose,
}: {
  cityName: string
  onClose: () => void
}) {
  const [detail, setDetail] = useState<CityDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api<CityDetail>(`/api/markets/${cityName}/detail`)
      .then(setDetail)
      .finally(() => setLoading(false))
  }, [cityName])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="mx-4 max-h-[85vh] w-full max-w-2xl overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-secondary)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-6 py-4">
          <div>
            <h2 className="text-lg font-bold text-[var(--text-primary)]">
              {detail?.city.display_name ?? cityName}
            </h2>
            {detail?.city && (
              <div className="mt-1 flex items-center gap-3 text-xs text-[var(--text-muted)]">
                <StatusBadge status={detail.city.status} />
                {detail.city.forecast_temp != null && (
                  <span>
                    Forecast: {formatTemp(detail.city.forecast_temp, detail.city.unit)}
                  </span>
                )}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="max-h-[65vh] overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <RefreshCw className="h-6 w-6 animate-spin text-[var(--text-muted)]" />
            </div>
          ) : detail ? (
            <BucketTable buckets={detail.buckets} />
          ) : (
            <div className="py-16 text-center text-sm text-[var(--text-muted)]">
              Failed to load market detail
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Markets() {
  const [selectedCity, setSelectedCity] = useState<string | null>(null)
  const [togglingCities, setTogglingCities] = useState<Set<string>>(new Set())

  const fetchMarkets = useCallback(() => api<CityMarket[]>('/api/markets'), [])
  const { data: markets, error, refresh } = usePolling(fetchMarkets, 10000)
  const { lastMessage } = useWebSocket()

  useEffect(() => {
    if (lastMessage) refresh()
  }, [lastMessage, refresh])

  const handleToggle = async (e: React.MouseEvent, cityName: string) => {
    e.stopPropagation()
    setTogglingCities((prev) => new Set(prev).add(cityName))
    try {
      await api(`/api/markets/${cityName}/toggle`, { method: 'POST' })
      await refresh()
    } finally {
      setTogglingCities((prev) => {
        const next = new Set(prev)
        next.delete(cityName)
        return next
      })
    }
  }

  if (error && !markets) {
    return <ErrorState message="Failed to load markets" onRetry={refresh} />
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">Markets</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {markets ? `${markets.length} cities monitored` : 'Loading...'}
          </p>
        </div>
        <button
          onClick={() => refresh()}
          className="flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* City Cards Grid */}
      {!markets ? (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="h-6 w-6 animate-spin text-[var(--text-muted)]" />
        </div>
      ) : markets.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--bg-card)] py-20 text-[var(--text-muted)]">
          <MapPin className="mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">No markets configured</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {markets.map((city) => {
            const edgeVal = city.edge
            const edgeColor =
              edgeVal != null && edgeVal > 0
                ? 'text-[var(--accent-green)]'
                : edgeVal != null && edgeVal < 0
                  ? 'text-[var(--accent-red)]'
                  : 'text-[var(--text-muted)]'
            const isToggling = togglingCities.has(city.name)

            return (
              <div
                key={city.name}
                onClick={() => setSelectedCity(city.name)}
                className={`group cursor-pointer rounded-xl border bg-[var(--bg-card)] p-5 transition-all hover:border-[var(--accent-blue)]/40 hover:bg-[var(--bg-hover)] ${
                  city.enabled
                    ? 'border-[var(--border)]'
                    : 'border-[var(--border)]/50 opacity-60'
                }`}
              >
                {/* Top: City name + Toggle */}
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <MapPin className="h-4 w-4 text-[var(--accent-blue)]" />
                    <h3 className="text-base font-bold text-[var(--text-primary)]">
                      {city.display_name}
                    </h3>
                  </div>
                  <button
                    onClick={(e) => handleToggle(e, city.name)}
                    disabled={isToggling}
                    className="text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)] disabled:opacity-50"
                    title={city.enabled ? 'Disable monitoring' : 'Enable monitoring'}
                  >
                    {city.enabled ? (
                      <ToggleRight className="h-6 w-6 text-[var(--accent-green)]" />
                    ) : (
                      <ToggleLeft className="h-6 w-6" />
                    )}
                  </button>
                </div>

                {/* Status Badge */}
                <div className="mt-3">
                  <StatusBadge status={city.status} />
                </div>

                {/* Data Grid */}
                <div className="mt-4 grid grid-cols-2 gap-3">
                  {/* Forecast Temp */}
                  <div>
                    <span className="text-xs text-[var(--text-muted)]">Forecast</span>
                    <div className="mt-0.5 flex items-center gap-1">
                      <Thermometer className="h-3.5 w-3.5 text-[var(--accent-yellow)]" />
                      <span className="font-mono text-sm font-medium text-[var(--text-primary)]">
                        {formatTemp(city.forecast_temp, city.unit)}
                      </span>
                    </div>
                  </div>

                  {/* Market Price */}
                  <div>
                    <span className="text-xs text-[var(--text-muted)]">Market Price</span>
                    <p className="mt-0.5 font-mono text-sm font-medium text-[var(--text-primary)]">
                      {formatPrice(city.market_price)}
                    </p>
                  </div>

                  {/* Edge */}
                  <div>
                    <span className="text-xs text-[var(--text-muted)]">Edge</span>
                    <div className="mt-0.5 flex items-center gap-1">
                      {edgeVal != null && edgeVal > 0 ? (
                        <TrendingUp className="h-3.5 w-3.5 text-[var(--accent-green)]" />
                      ) : edgeVal != null && edgeVal < 0 ? (
                        <TrendingDown className="h-3.5 w-3.5 text-[var(--accent-red)]" />
                      ) : null}
                      <span className={`font-mono text-sm font-medium ${edgeColor}`}>
                        {formatEdge(city.edge)}
                      </span>
                    </div>
                  </div>

                  {/* Last Refresh */}
                  <div>
                    <span className="text-xs text-[var(--text-muted)]">Updated</span>
                    <p className="mt-0.5 text-sm text-[var(--text-secondary)]">
                      {timeAgo(city.last_refresh)}
                    </p>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Detail Modal */}
      {selectedCity && (
        <DetailModal
          cityName={selectedCity}
          onClose={() => setSelectedCity(null)}
        />
      )}
    </div>
  )
}
