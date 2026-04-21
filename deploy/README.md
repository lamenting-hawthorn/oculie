# Polymarket Weather Prediction Agent — Process Management

This directory contains macOS launchd configuration and helper scripts for running the
Polymarket Weather Prediction Agent as persistent background services.

## What's included

| File | Purpose |
|------|---------|
| `com.openclaw.bot.plist` | launchd service definition for the trading bot (`bot.main`) |
| `com.openclaw.dashboard.plist` | launchd service definition for the FastAPI backend (`dashboard.backend.app`) |
| `install.sh` | Copies plists to `~/Library/LaunchAgents/` and loads both services |
| `uninstall.sh` | Unloads both services and removes plist files from LaunchAgents |
| `status.sh` | Shows launchd run state and tails recent lines from the bot log |

## How it works

Both services are managed by macOS launchd with:
- **RunAtLoad: true** — starts the service immediately when loaded (and on every login)
- **KeepAlive: true** — restarts the process if it exits for any reason
- **ThrottleInterval: 30** — waits 30 seconds before restarting after a crash

Logs are written to `data/` in the project root:
- `data/bot.log` / `data/bot-error.log`
- `data/dashboard.log` / `data/dashboard-error.log`

## Install

```bash
bash deploy/install.sh
```

This creates `data/` if needed, copies the plists, and loads the services. Both processes
start immediately and will restart automatically on crash or reboot.

## Check status

```bash
bash deploy/status.sh
```

Shows the launchd PID and exit code for each service, plus the last 20 lines of the bot log.

## Uninstall

```bash
bash deploy/uninstall.sh
```

Stops both services and removes the plist files from `~/Library/LaunchAgents/`. Log files
in `data/` are preserved.

## Frontend note

The React frontend is not managed here. It must be built separately and served either:
- As a static build via `npm run build`, with the output served by the FastAPI app as
  static files, **or**
- Via a separate dev server (`npm run dev`) during development.

Refer to the dashboard package configuration for how static files are mounted on the
FastAPI app.
