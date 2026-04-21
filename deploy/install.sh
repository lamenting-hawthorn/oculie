#!/usr/bin/env bash
# install.sh — Installs OpenClaw Weather trading system as persistent macOS launchd services.
# Copies the bot and dashboard plists to ~/Library/LaunchAgents/ and loads them so they
# start immediately and auto-restart on crash or reboot.

set -euo pipefail

PROJECT_DIR="/Users/raghav/freelance/openclaw-weather"
DEPLOY_DIR="$PROJECT_DIR/deploy"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
DATA_DIR="$PROJECT_DIR/data"

echo "==> Creating data directory (if it does not exist)..."
mkdir -p "$DATA_DIR"

echo "==> Copying plists to $LAUNCH_AGENTS_DIR..."
cp "$DEPLOY_DIR/com.openclaw.bot.plist"       "$LAUNCH_AGENTS_DIR/com.openclaw.bot.plist"
cp "$DEPLOY_DIR/com.openclaw.dashboard.plist" "$LAUNCH_AGENTS_DIR/com.openclaw.dashboard.plist"

echo "==> Loading services with launchctl..."
launchctl load "$LAUNCH_AGENTS_DIR/com.openclaw.bot.plist"
launchctl load "$LAUNCH_AGENTS_DIR/com.openclaw.dashboard.plist"

echo ""
echo "==> Service status:"
launchctl list | grep openclaw || echo "(no openclaw services found — check for errors above)"

echo ""
echo "Done. Both services will start on login and auto-restart within 30 seconds if they crash."
echo "Logs are written to $DATA_DIR/"
