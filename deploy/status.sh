#!/usr/bin/env bash
# status.sh — Shows the current run state of the OpenClaw Weather launchd services
# and prints the most recent lines from the trading bot log.

set -euo pipefail

PROJECT_DIR="/Users/raghav/freelance/openclaw-weather"
BOT_LOG="$PROJECT_DIR/data/bot.log"

echo "==> Launchd service status (PID  LastExit  Label):"
launchctl list | grep openclaw || echo "  No openclaw services are currently loaded."

echo ""
echo "==> Recent bot log (last 20 lines from $BOT_LOG):"
if [ -f "$BOT_LOG" ]; then
    tail -n 20 "$BOT_LOG"
else
    echo "  Log file not found — the bot may not have started yet."
fi
