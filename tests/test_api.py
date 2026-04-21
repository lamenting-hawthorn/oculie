"""Tests for the FastAPI dashboard backend endpoints."""

import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

import bot.database as db_module
from bot.database import init_db
from dashboard.backend.app import app


@pytest.fixture
async def client():
    """Provide an httpx AsyncClient wired to the FastAPI app with a temp database."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_api_")
    os.close(fd)

    # Initialise a fresh DB and inject it as the module-level singleton
    conn = await init_db(path)
    original = db_module._db_connection
    db_module._db_connection = conn

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        db_module._db_connection = original
        await conn.close()
        if os.path.exists(path):
            os.unlink(path)


# --------------------------------------------------------------------------- #
# 1. GET /api/status
# --------------------------------------------------------------------------- #


async def test_get_status(client: AsyncClient):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "paper_mode" in data
    assert "scan_interval" in data


# --------------------------------------------------------------------------- #
# 2. POST /api/status/toggle
# --------------------------------------------------------------------------- #


async def test_toggle_status(client: AsyncClient):
    # Get initial status
    initial = (await client.get("/api/status")).json()
    initial_status = initial["status"]

    # Toggle
    resp = await client.post("/api/status/toggle")
    assert resp.status_code == 200
    toggled = resp.json()

    expected = "running" if initial_status == "stopped" else "stopped"
    assert toggled["status"] == expected

    # Toggle again should flip back
    resp2 = await client.post("/api/status/toggle")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == initial_status


# --------------------------------------------------------------------------- #
# 3. GET /api/account
# --------------------------------------------------------------------------- #


async def test_get_account(client: AsyncClient):
    resp = await client.get("/api/account")
    assert resp.status_code == 200
    data = resp.json()
    assert "balance" in data
    assert "today_pnl" in data
    assert "win_rate" in data


# --------------------------------------------------------------------------- #
# 4. GET /api/positions (empty)
# --------------------------------------------------------------------------- #


async def test_get_positions_empty(client: AsyncClient):
    resp = await client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------- #
# 5. GET /api/trades (empty)
# --------------------------------------------------------------------------- #


async def test_get_trades_empty(client: AsyncClient):
    resp = await client.get("/api/trades")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------- #
# 6. GET /api/trades with filters
# --------------------------------------------------------------------------- #


async def test_get_trades_with_filters(client: AsyncClient):
    resp = await client.get("/api/trades", params={"city": "new_york", "outcome": "won"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# --------------------------------------------------------------------------- #
# 7. GET /api/trades/pnl
# --------------------------------------------------------------------------- #


async def test_get_pnl_data(client: AsyncClient):
    resp = await client.get("/api/trades/pnl")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# --------------------------------------------------------------------------- #
# 8. GET /api/markets
# --------------------------------------------------------------------------- #


async def test_get_markets(client: AsyncClient):
    resp = await client.get("/api/markets")
    assert resp.status_code == 200
    markets = resp.json()
    assert isinstance(markets, list)
    assert len(markets) == 10
    # Each market should have expected keys
    for m in markets:
        assert "name" in m
        assert "display_name" in m
        assert "enabled" in m


# --------------------------------------------------------------------------- #
# 9. POST /api/markets/{city}/toggle
# --------------------------------------------------------------------------- #


async def test_toggle_city(client: AsyncClient):
    resp = await client.post("/api/markets/nyc/toggle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["city"] == "nyc"
    assert "enabled" in data


# --------------------------------------------------------------------------- #
# 10. GET /api/markets/{city}/detail
# --------------------------------------------------------------------------- #


async def test_get_city_detail(client: AsyncClient):
    resp = await client.get("/api/markets/nyc/detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "nyc"
    assert "display_name" in data
    assert "enabled" in data
    assert "buckets" in data
    assert "positions" in data


# --------------------------------------------------------------------------- #
# 11. GET /api/settings
# --------------------------------------------------------------------------- #


async def test_get_settings(client: AsyncClient):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "entry_threshold" in data
    assert "max_bet_size" in data


# --------------------------------------------------------------------------- #
# 12. PUT /api/settings
# --------------------------------------------------------------------------- #


async def test_update_settings(client: AsyncClient):
    resp = await client.put("/api/settings", json={"max_bet_size": "75"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_bet_size"] == "75"


# --------------------------------------------------------------------------- #
# 13. GET /api/alerts (empty)
# --------------------------------------------------------------------------- #


async def test_get_alerts_empty(client: AsyncClient):
    resp = await client.get("/api/alerts")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------- #
# 14. GET /api/alerts with type filter
# --------------------------------------------------------------------------- #


async def test_alerts_with_type_filter(client: AsyncClient):
    resp = await client.get("/api/alerts", params={"alert_type": "error"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
