import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Globe,
  History,
  Settings as SettingsIcon,
  Bell,
} from 'lucide-react'
import Overview from './pages/Overview'
import Markets from './pages/Markets'
import TradeHistory from './pages/TradeHistory'
import Settings from './pages/Settings'
import Alerts from './pages/Alerts'
import { ErrorBoundary } from './components/ErrorBoundary'

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/markets', label: 'Markets', icon: Globe },
  { to: '/trades', label: 'Trade History', icon: History },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/alerts', label: 'Alerts', icon: Bell },
]

function Sidebar() {
  return (
    <aside className="w-64 h-screen fixed left-0 top-0 bg-[var(--bg-secondary)] border-r border-[var(--border)] flex flex-col p-4 z-10">
      <div className="mb-8 px-4 pt-2">
        <h1 className="text-xl font-bold text-[var(--text-primary)]">Polymarket</h1>
        <p className="text-xs text-[var(--text-muted)] mt-1">Weather Prediction Agent</p>
      </div>
      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-[var(--accent-blue)] text-white'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]'
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="ml-64 flex-1 p-8">
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/markets" element={<Markets />} />
              <Route path="/trades" element={<TradeHistory />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/alerts" element={<Alerts />} />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </BrowserRouter>
  )
}
