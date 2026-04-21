import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'
import { ErrorState } from '../components/ErrorState'
import type { Settings as SettingsType } from '../types'
import {
  Sliders, DollarSign, Clock, MapPin, ToggleLeft, ToggleRight,
  MessageSquare, Save, CheckCircle, AlertTriangle, XCircle, Zap,
} from 'lucide-react'

const CITIES = [
  { key: 'cities_new_york', label: 'New York' },
  { key: 'cities_chicago', label: 'Chicago' },
  { key: 'cities_miami', label: 'Miami' },
  { key: 'cities_dallas', label: 'Dallas' },
  { key: 'cities_seattle', label: 'Seattle' },
  { key: 'cities_atlanta', label: 'Atlanta' },
  { key: 'cities_london', label: 'London' },
  { key: 'cities_seoul', label: 'Seoul' },
  { key: 'cities_shanghai', label: 'Shanghai' },
  { key: 'cities_hong_kong', label: 'Hong Kong' },
]

const SCAN_INTERVALS = [15, 30, 60]

const ALERT_TOGGLES = [
  { key: 'alert_trade_entered', label: 'Trade Entered' },
  { key: 'alert_trade_resolved', label: 'Trade Resolved' },
  { key: 'alert_daily_summary', label: 'Daily Summary' },
  { key: 'alert_errors', label: 'Errors' },
]

function Toggle({ enabled, onToggle, disabled }: { enabled: boolean; onToggle: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      className="relative focus:outline-none"
      aria-label="Toggle"
    >
      {enabled ? (
        <ToggleRight className="w-10 h-6 text-[var(--accent-green)]" />
      ) : (
        <ToggleLeft className="w-10 h-6 text-[var(--text-muted)]" />
      )}
    </button>
  )
}

function NumberInput({ value, onChange, prefix }: { value: string; onChange: (v: string) => void; prefix?: string }) {
  return (
    <div className="flex items-center bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 focus-within:ring-1 focus-within:ring-[var(--accent-blue)]">
      {prefix && <span className="text-[var(--text-muted)] mr-1 text-sm">{prefix}</span>}
      <input
        type="number"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="bg-transparent text-[var(--text-primary)] text-sm w-full focus:outline-none"
      />
    </div>
  )
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsType>({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [showLiveWarning, setShowLiveWarning] = useState(false)
  const [apiKeysConfigured, setApiKeysConfigured] = useState(false)

  const loadSettings = useCallback(async () => {
    setLoadError(false)
    try {
      const [data, keysStatus] = await Promise.all([
        api<SettingsType>('/api/settings'),
        api<{ all_configured: boolean }>('/api/settings/api-keys'),
      ])
      setSettings(data)
      setApiKeysConfigured(keysStatus.all_configured)
    } catch {
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadSettings() }, [loadSettings])

  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 3000)
      return () => clearTimeout(t)
    }
  }, [toast])

  function update(key: string, value: string) {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  function toggleBool(key: string) {
    const current = settings[key]
    const isOn = current === 'true' || current === '1'
    update(key, isOn ? 'false' : 'true')
  }

  function isBoolOn(key: string) {
    const v = settings[key]
    return v === 'true' || v === '1'
  }

  function handlePaperToggle() {
    if (isBoolOn('paper_mode')) {
      if (!apiKeysConfigured) {
        setToast({ type: 'error', message: 'Cannot enable live trading: Polymarket API keys are not configured. Set POLYMARKET_API_KEY, SECRET, PASSPHRASE, and FUNDER in your .env file.' })
        return
      }
      setShowLiveWarning(true)
    } else {
      toggleBool('paper_mode')
    }
  }

  function confirmLiveTrading() {
    toggleBool('paper_mode')
    setShowLiveWarning(false)
  }

  async function save() {
    setSaving(true)
    try {
      const updated = await api<SettingsType>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify(settings),
      })
      setSettings(updated)
      setToast({ type: 'success', message: 'Settings saved successfully' })
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Failed to save settings'
      const isApiKeyError = detail.includes('live trading') || detail.includes('missing environment')
      setToast({ type: 'error', message: isApiKeyError ? 'Cannot enable live trading: Polymarket API keys not configured' : detail })
      if (isApiKeyError) {
        setSettings(prev => ({ ...prev, paper_mode: 'true' }))
      }
    } finally {
      setSaving(false)
    }
  }

  // entry_threshold is stored as a decimal (0.03 = 3%). Display as whole percentage.
  const entryThresholdRaw = Number(settings['entry_threshold'] || '0.03')
  const entryThresholdPct = entryThresholdRaw <= 1 ? Math.round(entryThresholdRaw * 100) : Math.round(entryThresholdRaw)
  const scanInterval = Number(settings['scan_interval_minutes'] || '30')

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-[var(--text-muted)]">
        Loading settings...
      </div>
    )
  }

  if (loadError) {
    return <ErrorState message="Failed to load settings" onRetry={loadSettings} />
  }

  return (
    <div className="space-y-6 relative">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg border ${
          toast.type === 'success'
            ? 'bg-[var(--accent-green)]/10 border-[var(--accent-green)]/30 text-[var(--accent-green)]'
            : 'bg-[var(--accent-red)]/10 border-[var(--accent-red)]/30 text-[var(--accent-red)]'
        }`}>
          {toast.type === 'success' ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
          <span className="text-sm font-medium">{toast.message}</span>
        </div>
      )}

      {/* Live Trading Warning Modal */}
      {showLiveWarning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl p-6 max-w-sm mx-4 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-[var(--accent-yellow)]" />
              <h3 className="text-lg font-semibold text-[var(--text-primary)]">Enable Live Trading?</h3>
            </div>
            <p className="text-[var(--text-secondary)] text-sm mb-6">
              Live trading will use real funds. Make sure your account is properly funded and all settings are correctly configured.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowLiveWarning(false)}
                className="px-4 py-2 rounded-lg bg-[var(--bg-hover)] text-[var(--text-secondary)] text-sm hover:text-[var(--text-primary)] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmLiveTrading}
                className="px-4 py-2 rounded-lg bg-[var(--accent-red)] text-white text-sm font-medium hover:opacity-90 transition-opacity"
              >
                Enable Live Trading
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-[var(--text-primary)]">Settings</h1>
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 bg-[var(--accent-blue)] text-white rounded-lg font-medium text-sm hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>

      {/* Paper Trading Toggle */}
      <div className="bg-[var(--bg-card)] rounded-lg p-5 border border-[var(--border)]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Zap className={`w-5 h-5 ${isBoolOn('paper_mode') ? 'text-[var(--accent-yellow)]' : 'text-[var(--accent-red)]'}`} />
            <div>
              <h2 className="text-base font-semibold text-[var(--text-primary)]">Paper Trading</h2>
              <p className="text-sm text-[var(--text-muted)]">
                {isBoolOn('paper_mode') ? 'Simulated trades only - no real money' : 'LIVE - trading with real funds'}
              </p>
            </div>
          </div>
          <Toggle enabled={isBoolOn('paper_mode')} onToggle={handlePaperToggle} />
        </div>
        {!isBoolOn('paper_mode') && (
          <div className="mt-3 flex items-center gap-2 text-[var(--accent-red)] text-xs">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>Live trading is active. Real funds will be used.</span>
          </div>
        )}
        {isBoolOn('paper_mode') && !apiKeysConfigured && (
          <div className="mt-3 flex items-center gap-2 text-[var(--text-muted)] text-xs">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>Polymarket API keys not configured. Live trading unavailable until keys are set in .env</span>
          </div>
        )}
      </div>

      {/* Trading Parameters */}
      <div className="bg-[var(--bg-card)] rounded-lg p-5 border border-[var(--border)]">
        <div className="flex items-center gap-2 mb-5">
          <Sliders className="w-5 h-5 text-[var(--accent-blue)]" />
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Trading Parameters</h2>
        </div>
        <div className="space-y-5">
          {/* Entry Threshold Slider */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm text-[var(--text-secondary)]">Entry Threshold</label>
              <span className="text-sm font-medium text-[var(--accent-blue)]">{entryThresholdPct}%</span>
            </div>
            <input
              type="range"
              min={3}
              max={40}
              step={1}
              value={entryThresholdPct}
              onChange={e => update('entry_threshold', (Number(e.target.value) / 100).toFixed(2))}
              className="w-full h-2 rounded-full appearance-none cursor-pointer accent-[var(--accent-blue)]"
              style={{
                background: `linear-gradient(to right, var(--accent-blue) ${((entryThresholdPct - 3) / 37) * 100}%, var(--bg-hover) ${((entryThresholdPct - 3) / 37) * 100}%)`,
              }}
            />
            <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
              <span>3%</span><span>40%</span>
            </div>
          </div>

          {/* Number inputs row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="text-sm text-[var(--text-secondary)] mb-2 block">
                <DollarSign className="w-3.5 h-3.5 inline mr-1" />Max Bet Size
              </label>
              <NumberInput
                value={settings['max_bet_size'] || ''}
                onChange={v => update('max_bet_size', v)}
                prefix="$"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--text-secondary)] mb-2 block">
                <DollarSign className="w-3.5 h-3.5 inline mr-1" />Max Total Exposure
              </label>
              <NumberInput
                value={settings['max_total_exposure'] || ''}
                onChange={v => update('max_total_exposure', v)}
                prefix="$"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--text-secondary)] mb-2 block">
                <DollarSign className="w-3.5 h-3.5 inline mr-1" />Account Floor
              </label>
              <NumberInput
                value={settings['account_floor'] || ''}
                onChange={v => update('account_floor', v)}
                prefix="$"
              />
            </div>
          </div>

          {/* Scan Interval */}
          <div>
            <label className="text-sm text-[var(--text-secondary)] mb-2 flex items-center gap-1">
              <Clock className="w-3.5 h-3.5" /> Scan Interval
            </label>
            <div className="flex gap-2">
              {SCAN_INTERVALS.map(mins => (
                <button
                  key={mins}
                  onClick={() => update('scan_interval_minutes', String(mins))}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    scanInterval === mins
                      ? 'bg-[var(--accent-blue)] text-white'
                      : 'bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border)]'
                  }`}
                >
                  {mins} min
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Cities */}
      <div className="bg-[var(--bg-card)] rounded-lg p-5 border border-[var(--border)]">
        <div className="flex items-center gap-2 mb-5">
          <MapPin className="w-5 h-5 text-[var(--accent-purple)]" />
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Cities</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {CITIES.map(city => (
            <div
              key={city.key}
              className="flex items-center justify-between bg-[var(--bg-secondary)] rounded-lg px-4 py-3 border border-[var(--border)]"
            >
              <span className="text-sm text-[var(--text-primary)]">{city.label}</span>
              <Toggle enabled={isBoolOn(city.key)} onToggle={() => toggleBool(city.key)} />
            </div>
          ))}
        </div>
      </div>

      {/* Messaging */}
      <div className="bg-[var(--bg-card)] rounded-lg p-5 border border-[var(--border)]">
        <div className="flex items-center gap-2 mb-5">
          <MessageSquare className="w-5 h-5 text-[var(--accent-green)]" />
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Messaging</h2>
        </div>
        <div className="space-y-5">
          {/* App selector */}
          <div>
            <label className="text-sm text-[var(--text-secondary)] mb-2 block">Messaging App</label>
            <div className="flex gap-3">
              {['telegram', 'whatsapp'].map(app => (
                <label
                  key={app}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg cursor-pointer border text-sm font-medium transition-colors ${
                    settings['messaging_app'] === app
                      ? 'border-[var(--accent-blue)] bg-[var(--accent-blue)]/10 text-[var(--accent-blue)]'
                      : 'border-[var(--border)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <input
                    type="radio"
                    name="messaging_app"
                    value={app}
                    checked={settings['messaging_app'] === app}
                    onChange={() => update('messaging_app', app)}
                    className="sr-only"
                  />
                  {app.charAt(0).toUpperCase() + app.slice(1)}
                </label>
              ))}
            </div>
          </div>

          {/* Alert toggles */}
          <div>
            <label className="text-sm text-[var(--text-secondary)] mb-3 block">Alert Types</label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {ALERT_TOGGLES.map(alert => (
                <div
                  key={alert.key}
                  className="flex items-center justify-between bg-[var(--bg-secondary)] rounded-lg px-4 py-3 border border-[var(--border)]"
                >
                  <span className="text-sm text-[var(--text-primary)]">{alert.label}</span>
                  <Toggle enabled={isBoolOn(alert.key)} onToggle={() => toggleBool(alert.key)} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Bottom save button for long pages */}
      <div className="flex justify-end">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 bg-[var(--accent-blue)] text-white rounded-lg font-medium text-sm hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
