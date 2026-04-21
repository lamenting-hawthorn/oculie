# Oculie — Architecture

## System Overview

Oculie is an event-driven trading agent that runs on a 30-minute scheduler. Each cycle it fetches live weather forecasts, computes a probability distribution over temperature outcomes, finds mispriced Polymarket contracts, and enters positions sized by the Kelly Criterion.

## Component Map

```
Scheduler (APScheduler, every 30 min)
  │
  ├── MarketScanner (bot/market_scanner.py)
  │     └── Gamma API → list of active temperature markets
  │
  ├── Consensus Engine (bot/consensus.py)
  │     ├── NOAA NWS fetcher       → US cities (free, no auth)
  │     ├── Open-Meteo fetcher     → international cities (free, 31-member ensemble)
  │     ├── Visual Crossing        → optional 4th source (API key required)
  │     └── wttr.in / Wunderground → fallback sources
  │
  ├── TradeEngine (bot/trade_engine.py)
  │     ├── edge = forecast_prob − market_price
  │     ├── Kelly sizing (capped at 25% of full Kelly via 0.25× multiplier)
  │     └── Guards: same-day filter, price floor $0.01, 4h forecast drift, $100 volume minimum
  │
  ├── CLOBClient (bot/clob_client.py)
  │     └── py-clob-client → live order placement on Polymarket
  │
  ├── Resolver (bot/resolver.py)
  │     └── Polls open positions every 5 min; 30% adverse-move early exit
  │
  └── Reporter (bot/reporter.py)
        └── Telegram + WhatsApp alerts; Telegram command polling (10s)

Database (bot/database.py)
  └── aiosqlite → trades, positions, settings, alerts tables

Dashboard
  ├── FastAPI backend (dashboard/backend/app.py) — HTTP Basic Auth on write endpoints
  └── React 18 + Vite + Tailwind SPA (dashboard/frontend/src/)
        ├── Overview   — bot status, balance, P&L, open positions, next scan countdown
        ├── Markets    — city-by-city forecast vs market price and edge
        ├── Trade History — full log, win/loss stats, cumulative P&L chart
        ├── Settings   — threshold, bet limits, scan interval, paper mode toggle
        └── Alerts     — notification log, test alert button
```

## Probability Model

**US cities (NOAA NWS)**
NOAA point forecasts are fit to a normal distribution with horizon-scaled uncertainty:
- σ = 3°F for day 0–1
- σ = 5°F for day 2+

Probabilities are computed by integrating the normal CDF across 5°F buckets matching each market's temperature range.

**International cities (Open-Meteo)**
Open-Meteo's ensemble API returns 31 independent model runs. The fraction of members falling in each 1°C bucket gives a data-driven empirical distribution — no parametric assumptions. Falls back to a point-mass at the deterministic p50 if the ensemble is unavailable.

## Trade Execution Guards

| Guard | Threshold | Purpose |
|-------|-----------|---------|
| Same-day filter | days_out < 1 | Zero forecast advantage on day-of markets |
| Price floor | dampened price < $0.01 | Filters stale Gamma API quotes |
| Forecast drift | age > 4 hours | Eliminates stale point forecasts |
| Liquidity check | 24h volume < $100 | Avoids shallow order books |
| NO dampening | Bayesian 60/40 blend | Prevents false NO edges from stale prices |
| Kelly cap | 0.25× full Kelly | Limits position size on uncertain edges |
