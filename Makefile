.PHONY: install dev start stop test simulate build clean logs status

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	@echo "→ Installing Python dependencies..."
	uv sync --extra dev
	@echo "→ Installing frontend dependencies..."
	cd dashboard/frontend && npm install
	@echo "→ Building frontend..."
	cd dashboard/frontend && npm run build
	@echo "→ Initialising database..."
	uv run python -m bot.database
	@echo "✓ Installation complete. Copy .env.example to .env and fill in your keys."

# ── Development ───────────────────────────────────────────────────────────────
dev:
	@echo "→ Starting dev environment (bot + backend + frontend)..."
	@trap 'kill 0' SIGINT; \
	uv run python -m bot.main & \
	uv run uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000 --reload & \
	cd dashboard/frontend && npm run dev & \
	wait

backend:
	uv run uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000 --reload --workers 1

frontend:
	cd dashboard/frontend && npm run dev

bot:
	uv run python -m bot.main

# ── Production ────────────────────────────────────────────────────────────────
start:
	@echo "→ Starting OpenClaw Weather Bot (production)..."
	bash deploy/install.sh

stop:
	@echo "→ Stopping OpenClaw Weather Bot..."
	bash deploy/uninstall.sh

status:
	bash deploy/status.sh

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	cd dashboard/frontend && npm run build

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	uv run pytest tests/ -v

test-watch:
	uv run pytest tests/ -v --tb=short -x

# ── Simulation ────────────────────────────────────────────────────────────────
simulate:
	uv run python scripts/simulate_paper.py

simulate-keep:
	uv run python scripts/simulate_paper.py --keep-db
	@echo "Simulation DB at data/simulation.db — start backend to view in dashboard."

# ── Maintenance ───────────────────────────────────────────────────────────────
logs:
	tail -f data/bot.log

logs-dashboard:
	tail -f data/dashboard.log

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dashboard/frontend/dist
	@echo "✓ Cleaned build artifacts"

clean-data:
	@echo "WARNING: This will delete all trade history."
	@read -p "Are you sure? [y/N] " ans && [ "$$ans" = "y" ] && rm -f data/*.db || echo "Aborted."
