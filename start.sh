#!/bin/bash
# Polymarket Weather Prediction Agent — single-command dev launcher
# Usage: bash start.sh
# Starts the bot, FastAPI backend, and React frontend in one terminal.
# Press Ctrl+C to stop everything.

set -e
cd "$(dirname "$0")"

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║   Polymarket Weather Prediction Agent         ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# Init DB if it doesn't exist
if [ ! -f "data/trades.db" ]; then
  echo "  → Initialising database..."
  uv run python -m bot.database
  echo "  ✓ Database ready"
  echo ""
fi

# Kill all child processes on Ctrl+C
trap 'echo ""; echo "  Stopping all services..."; kill 0' SIGINT

# Start bot
echo "  → Starting bot..."
uv run python -m bot.main > data/bot.log 2>&1 &
BOT_PID=$!

# Start backend and wait for it to be ready
echo "  → Starting API server..."
uv run uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000 --workers 1 > data/dashboard.log 2>&1 &
BACKEND_PID=$!

# Wait for backend to be accepting connections (max 10s)
for i in $(seq 1 20); do
  if curl -s http://127.0.0.1:8000/api/status > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# Start frontend
echo "  → Starting frontend..."
cd dashboard/frontend && npm run dev --silent > /dev/null 2>&1 &
FRONTEND_PID=$!
cd - > /dev/null

# Wait for Vite to bind (max 8s)
for i in $(seq 1 16); do
  if curl -s http://localhost:5173 > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  All services running                       │"
echo "  │                                             │"
echo "  │  Dashboard  →  http://localhost:5173        │"
echo "  │  API        →  http://localhost:8000        │"
echo "  │                                             │"
echo "  │  Logs: tail -f data/bot.log                 │"
echo "  │  Press Ctrl+C to stop                       │"
echo "  └─────────────────────────────────────────────┘"
echo ""

# Auto-open browser (macOS)
if command -v open > /dev/null 2>&1; then
  open http://localhost:5173
fi

# Stream all logs to terminal
tail -f data/bot.log data/dashboard.log
