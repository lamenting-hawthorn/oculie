# Oculie

> A Polymarket weather prediction trading bot — fetches real-world forecasts, finds mispriced temperature markets, and trades the edge using the Kelly Criterion.

Starts in **paper trading mode** by default. No real money moves until you explicitly turn it on.

---

## How it works

1. Every 30 minutes Oculie fetches weather forecasts from multiple sources and builds a consensus probability distribution over tomorrow's temperature
2. It scans Polymarket's Gamma API for active temperature markets (e.g. *"Will the high in Seoul be 18°C or higher on April 22?"*)
3. For each market it computes **edge** = forecast probability − market-implied price
4. If edge ≥ threshold (default 3%), it sizes a position using the **Kelly Criterion** (capped at 25% of full Kelly) and executes the trade
5. Open positions are polled every 5 minutes; a 30% adverse-move triggers an early exit
6. Results are logged to SQLite, visible in a React dashboard, and optionally sent to Telegram/WhatsApp

---

## Architecture

```
Scheduler (APScheduler, every 30 min)
  │
  ├── MarketScanner      ← Polymarket Gamma API — active temperature markets
  │
  ├── Consensus Engine
  │     ├── NOAA NWS          (US cities — free, no auth)
  │     ├── Open-Meteo        (international — free, 31-member ensemble)
  │     ├── Visual Crossing   (optional 4th source, API key required)
  │     └── wttr.in           (fallback)
  │
  ├── TradeEngine        ← edge calc → Kelly sizing → entry guard checks
  ├── CLOBClient         ← py-clob-client → live Polymarket order placement
  ├── Resolver           ← polls open positions; 30% stop-loss early exit
  └── Reporter           ← Telegram + WhatsApp alerts; Telegram command polling

Dashboard
  ├── FastAPI backend    (HTTP Basic Auth on write endpoints)
  └── React 18 + Vite + Tailwind SPA
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a full breakdown of each component.

---

## Cities monitored

| City | Country | Forecast source | Primary model |
|------|---------|----------------|---------------|
| New York City | US | NOAA NWS | GFS Seamless + HRRR |
| Chicago | US | NOAA NWS | GFS Seamless + HRRR |
| Miami | US | NOAA NWS | GFS Seamless + HRRR |
| Dallas | US | NOAA NWS | GFS Seamless + HRRR |
| Seattle | US | NOAA NWS | GFS Seamless + HRRR |
| Atlanta | US | NOAA NWS | GFS Seamless + HRRR |
| London | UK | Open-Meteo | UKMO Seamless |
| Seoul | South Korea | Open-Meteo | KMA Seamless |
| Shanghai | China | Open-Meteo | CMA GRAPES Global |
| Hong Kong | China | Open-Meteo | CMA GRAPES Global |
| Tokyo | Japan | Open-Meteo | JMA Seamless |

---

## Weather sources

| Source | Coverage | Auth required | Notes |
|--------|----------|---------------|-------|
| [NOAA NWS](https://www.weather.gov/) | US cities | None | Free government API; GFS + HRRR ensemble |
| [Open-Meteo](https://open-meteo.com/) | International | None | Free; 31-member GFS ensemble for probability |
| [Visual Crossing](https://www.visualcrossing.com/) | Global | API key (optional) | 4th consensus source; improves accuracy |
| [wttr.in](https://wttr.in/) | Fallback | None | Public API; used when other sources fail |

---

## Performance

**Baseline** (before any fixes): 64 trades, 0% win rate, **-$937.55 P&L**

The original system entered same-day markets with zero forecast advantage, sized NO-side positions without Bayesian dampening, and used raw (overconfident) Kelly probabilities.

| Phase | Key changes | Trades | Win rate | P&L |
|-------|-------------|--------|----------|-----|
| Baseline | — | 64 | 0% | -$937.55 |
| Phase A | Same-day filter · NO dampening · Kelly prob fix · price floor | 31 | 96.8% | +$1,450.00 |
| Phase B | Fractional Kelly (0.25×) · 4h forecast drift guard · $100 liquidity floor | 30 | **100%** | **+$1,500.00** |

*Validated on NOAA historical data (April 2–6, 2026) with 0.50 baseline simulated Polymarket prices. Live performance will vary.*

Full validation methodology: [`docs/VALIDATION_PHASE_A.md`](docs/VALIDATION_PHASE_A.md) · [`docs/VALIDATION_PHASE_B.md`](docs/VALIDATION_PHASE_B.md)

---

## Quick start

### Prerequisites
- Python 3.11+ with [`uv`](https://github.com/astral-sh/uv): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Node.js 18+

### Setup
```bash
git clone https://github.com/your-username/oculie.git
cd oculie

cp .env.example .env       # fill in API keys (optional for paper mode)
make install               # installs Python deps, Node deps, builds frontend, initialises DB
```

### Run
```bash
make dev                   # starts bot + API (:8000) + dashboard (:5173) with hot-reload
```

Or use the standalone launcher:
```bash
bash start.sh              # starts everything, opens browser, streams logs
```

The dashboard opens at **http://localhost:5173**. Press `Ctrl+C` to stop.

Paper mode is on by default — no real trades until you disable it in Settings.

---

## Environment variables

Copy `.env.example` → `.env` and fill in what you need:

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Live trading only | Ethereum private key (MetaMask/EOA) |
| `POLYMARKET_FUNDER` | Live trading only | Wallet address holding USDC |
| `POLYMARKET_API_KEY` / `SECRET` / `PASSPHRASE` | Live trading only | Derived from private key if blank |
| `POLYMARKET_SIGNATURE_TYPE` | Optional | 0=EOA (default), 1=Magic wallet, 2=Browser proxy |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Optional | Trade alerts and bot commands |
| `WHATSAPP_PHONE_ID` / `ACCESS_TOKEN` / `RECIPIENT` | Optional | WhatsApp alerts |
| `DASHBOARD_USERNAME` + `DASHBOARD_PASSWORD` | Recommended | HTTP Basic Auth on write endpoints |
| `DASHBOARD_HOST` + `DASHBOARD_PORT` | Optional | Defaults to `127.0.0.1:8000` |
| `PAPER_MODE` | Default `true` | Set `false` only after reviewing paper results |
| `VISUALCROSSING_API_KEY` | Optional | Enables Visual Crossing as 4th forecast source |
| `NOAA_CDO_TOKEN` | Backtesting only | Required for `scripts/backtest_v2.py` |

> Weather data from NOAA NWS and Open-Meteo is **free and requires no API key**.

---

## Dashboard

| Screen | What it shows |
|--------|--------------|
| **Overview** | Bot status, wallet balance, P&L, open positions, countdown to next scan |
| **Markets** | Each city: forecast probability vs market price vs edge |
| **Trade History** | Full trade log with filters, win/loss stats, cumulative P&L chart |
| **Settings** | Sliders for threshold, bet limits, scan interval; paper mode toggle |
| **Alerts** | Notification log; test alert button |

---

## Probability model

**US cities** — NOAA point forecasts are fit to a normal distribution with horizon-scaled uncertainty (σ = 3°F for day 0–1, σ = 5°F for day 2+). Probabilities are computed by integrating the normal CDF across 5°F buckets matching each market's temperature range.

**International cities** — Open-Meteo's ensemble API returns 31 independent model runs. The fraction of members in each 1°C bucket gives a data-driven empirical distribution. Falls back to a point-mass at the deterministic p50 if ensemble data is unavailable.

---

## Default risk limits

All adjustable via the Settings screen:

| Limit | Default |
|-------|---------|
| Entry threshold (min edge) | 3% |
| Max bet per trade | $50 |
| Max total open exposure | $200 |
| Account floor (auto-pause) | $100 |
| Kelly multiplier | 0.25× (fractional Kelly) |
| Scan interval | 30 min |

---

## Commands

```bash
make install          # first-time setup
make dev              # bot + backend + frontend (with hot-reload)
make test             # run pytest suite
make logs             # tail -f data/bot.log
make simulate         # synthetic paper trading simulation

# Backtest on real NOAA historical data
uv run python scripts/backtest_v2.py --start 2024-01-01 --end 2024-12-31
uv run python scripts/backtest_v2.py --city nyc --start 2024-06-01 --end 2024-08-31

# Lint / format
uv run ruff check .
uv run ruff format .
```

### Telegram optional dependency

```bash
uv sync --extra telegram
```

---

## Telegram commands

Once `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, message the bot directly:

| Command | Effect |
|---------|--------|
| `force-cycle` | Run a full scan immediately |
| `pause` | Skip upcoming scan cycles |
| `resume` | Resume after a pause |
| `paper-on` / `paper-off` | Toggle paper mode |
| `set-threshold <value>` | e.g. `set-threshold 0.05` |
| `set-max-bet <value>` | e.g. `set-max-bet 25` |
| `kill-switch` | Halt all trading |

---

## Deployment

### macOS (launchd)

Runs the bot and API as persistent background services with auto-restart:

```bash
make start            # install and load launchd services
make stop             # unload services
make status           # show PID, exit code, last 20 log lines
```

See [`deploy/README.md`](deploy/README.md) for details.

### Docker / VPS

```bash
cp .env.example .env && nano .env
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f
```

nginx proxies `/api/*` to the FastAPI backend and serves the React SPA as static files.

---

## Going live

1. Run in paper mode for at least 48 hours and review Trade History
2. Add Polymarket API credentials to `.env`
3. Settings → toggle Paper Trading **OFF** → confirm (blocked until credentials are set)
4. Monitor the first 24 hours via dashboard and Telegram alerts

---

## Security

- **Never commit `.env`** — it's gitignored and contains your private keys
- Use `.env.example` as your template; it has no real values
- The repo includes a pre-commit hook that blocks accidental `.env` commits
- Paper mode is the default — live trading requires an explicit opt-in

---

## License

MIT — see [`LICENSE`](LICENSE)
