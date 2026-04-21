#!/usr/bin/env bash
# vps-setup.sh — One-time VPS setup for OpenClaw Weather Market Trader
# Tested on Ubuntu 22.04 LTS. Run as root or sudo.
#
# Usage:
#   ssh root@<your-vps>
#   git clone https://github.com/lamenting-hawthorn/openclaw-weather.git
#   cd openclaw-weather
#   bash deploy/vps-setup.sh

set -euo pipefail

echo "==> Installing Docker + Docker Compose..."
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-plugin git curl

systemctl enable docker
systemctl start docker

echo "==> Configuring firewall (UFW)..."
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw --force enable

echo "==> Setting up project..."
PROJECT_DIR="$(git rev-parse --show-toplevel)"
cd "$PROJECT_DIR"

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "  ⚠  .env created from .env.example — fill in your API keys:"
    echo "     nano .env"
    echo ""
fi

echo "==> Starting services..."
docker compose -f deploy/docker-compose.yml up -d --build

echo ""
echo "==> Done! Services are starting."
echo "    Dashboard: http://$(curl -s ifconfig.me)"
echo ""
echo "    Check logs: docker compose -f deploy/docker-compose.yml logs -f"
echo "    Stop:       docker compose -f deploy/docker-compose.yml down"
echo ""
echo "    To add HTTPS: install certbot, get a cert, then uncomment the"
echo "    HTTPS block in deploy/nginx.conf and restart."
