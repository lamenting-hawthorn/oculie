#!/usr/bin/env bash
# uninstall.sh — Stops and removes the OpenClaw Weather launchd services.
# Unloads both the trading bot and dashboard from launchd and deletes their
# plist files from ~/Library/LaunchAgents/.

set -euo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "==> Unloading services from launchctl..."
launchctl unload "$LAUNCH_AGENTS_DIR/com.openclaw.bot.plist"       2>/dev/null && echo "  Unloaded com.openclaw.bot" || echo "  com.openclaw.bot was not loaded (skipping)"
launchctl unload "$LAUNCH_AGENTS_DIR/com.openclaw.dashboard.plist" 2>/dev/null && echo "  Unloaded com.openclaw.dashboard" || echo "  com.openclaw.dashboard was not loaded (skipping)"

echo "==> Removing plist files from $LAUNCH_AGENTS_DIR..."
rm -f "$LAUNCH_AGENTS_DIR/com.openclaw.bot.plist"
rm -f "$LAUNCH_AGENTS_DIR/com.openclaw.dashboard.plist"

echo ""
echo "Done. OpenClaw services have been stopped and removed."
echo "Log files in data/ are preserved — remove them manually if no longer needed."
