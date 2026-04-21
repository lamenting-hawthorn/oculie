export interface BotStatus {
  status: 'running' | 'stopped'
  paper_mode: boolean
  started_at: string | null
  next_scan: string | null
  scan_interval: number
}

export interface AccountSummary {
  balance: number
  today_pnl: number
  today_pnl_pct: number
  alltime_pnl: number
  total_trades: number
  trades_today: number
  win_rate: number
  open_positions_count: number
  total_exposure: number
}

export interface Position {
  id: number
  city: string
  market_question: string
  direction: string
  entry_price: number
  current_price: number | null
  size: number
  unrealized_pnl: number | null
}

export interface Trade {
  id: number
  city: string
  market_question: string
  direction: string | null
  noaa_probability: number
  market_price: number
  edge: number
  bet_size: number
  entry_price: number
  exit_price: number | null
  outcome: string | null
  pnl: number | null
  paper_trade: boolean
  created_at: string
  resolved_at: string | null
}

export interface CityMarket {
  name: string
  display_name: string
  enabled: boolean
  forecast_temp: number | null
  market_price: number | null
  edge: number | null
  status: 'watching' | 'entered' | 'no_opportunity'
  last_refresh: string | null
  unit: string
}

export interface MarketBucket {
  temp_low: number
  temp_high: number
  noaa_prob: number
  market_price: number
  edge: number
}

export interface CityDetail {
  city: CityMarket
  buckets: MarketBucket[]
}

export interface Alert {
  id: number
  alert_type: string
  city: string | null
  message: string
  channel: string
  sent_at: string
}

export interface PnlDataPoint {
  date: string
  cumulative_pnl: number
}

export interface Settings {
  [key: string]: string
}
