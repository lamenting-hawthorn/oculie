"""
FastAPI backend for the Polymarket Weather Prediction Agent dashboard.

Provides REST + WebSocket endpoints for the React frontend.

Run with:
    uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
"""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from bot.database import (
    get_db,
    get_alerts,
    get_calibration_buckets,
    get_open_positions,
    get_pnl_by_city,
    get_pnl_summary,
    get_setting,
    get_total_exposure,
    get_trade_history,
    init_db,
    set_setting,
)
from bot.reporter import send_test_alert
from bot.trade_engine import run_scan_cycle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP Basic Auth
# ---------------------------------------------------------------------------

security = HTTPBasic(auto_error=False)

_auth_configured: bool | None = None  # lazily evaluated


def verify_auth(credentials: HTTPBasicCredentials | None = Depends(security)):
    """Validate HTTP Basic credentials for write endpoints.

    If DASHBOARD_USERNAME / DASHBOARD_PASSWORD env vars are not set, auth is
    skipped entirely (warn once and allow through) to preserve the local-dev
    experience.
    """
    username = os.environ.get("DASHBOARD_USERNAME", "")
    password = os.environ.get("DASHBOARD_PASSWORD", "")

    if not username or not password:
        logger.warning(
            "DASHBOARD_USERNAME / DASHBOARD_PASSWORD are not configured — "
            "write endpoints are unprotected. Set both env vars to enable auth."
        )
        return  # auth not configured, allow through

    # Auth is configured but no credentials were provided
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Polymarket Weather Agent Dashboard"'},
        )

    ok = secrets.compare_digest(
        credentials.username.encode(), username.encode()
    ) and secrets.compare_digest(
        credentials.password.encode(), password.encode()
    )

    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Polymarket Weather Agent Dashboard"'},
        )


# ---------------------------------------------------------------------------
# City metadata
# ---------------------------------------------------------------------------

CITIES = [
    {"name": "nyc", "display_name": "New York City", "setting_key": "cities_nyc", "unit": "F"},
    {"name": "chicago", "display_name": "Chicago", "setting_key": "cities_chicago", "unit": "F"},
    {"name": "miami", "display_name": "Miami", "setting_key": "cities_miami", "unit": "F"},
    {"name": "dallas", "display_name": "Dallas", "setting_key": "cities_dallas", "unit": "F"},
    {"name": "seattle", "display_name": "Seattle", "setting_key": "cities_seattle", "unit": "F"},
    {"name": "atlanta", "display_name": "Atlanta", "setting_key": "cities_atlanta", "unit": "F"},
    {"name": "london", "display_name": "London", "setting_key": "cities_london", "unit": "C"},
    {"name": "seoul", "display_name": "Seoul", "setting_key": "cities_seoul", "unit": "C"},
    {"name": "shanghai", "display_name": "Shanghai", "setting_key": "cities_shanghai", "unit": "C"},
    {"name": "hongkong", "display_name": "Hong Kong", "setting_key": "cities_hongkong", "unit": "C"},
]

CITY_BY_NAME = {c["name"]: c for c in CITIES}


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.active_connections.remove(conn)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    logger.info("Database initialised")
    yield
    # Shutdown (nothing to tear down)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Polymarket Weather Prediction Agent Dashboard API",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Live markets cache — populated by POST /api/scan/trigger
# ---------------------------------------------------------------------------

# Keyed by city internal name; each value holds forecast_temp, market_price, edge
_markets_cache: dict[str, dict] = {}


_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_dashboard_url = os.environ.get("DASHBOARD_URL", "").strip()
if _dashboard_url:
    _cors_origins.append(_dashboard_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------


async def _build_status_payload(db) -> dict:
    """Build the status response dict from current bot settings."""
    status = await get_setting(db, "bot_status") or "stopped"
    paper_mode = await get_setting(db, "paper_mode")
    interval_val = await get_setting(db, "scan_interval_minutes")
    return {
        "status": status,
        "paper_mode": paper_mode is None or paper_mode.lower() == "true",
        "started_at": await get_setting(db, "bot_started_at"),
        "next_scan": await get_setting(db, "next_scan"),
        "scan_interval": int(interval_val) if interval_val else 30,
    }


@app.get("/api/status")
async def get_status():
    """Return current bot status."""
    try:
        return await _build_status_payload(await get_db())
    except Exception as exc:
        logger.exception("Error fetching status")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/status/toggle", dependencies=[Depends(verify_auth)])
async def toggle_status():
    """Toggle bot between running and stopped."""
    try:
        db = await get_db()
        current = await get_setting(db, "bot_status") or "stopped"

        if current == "running":
            new_status = "stopped"
            await set_setting(db, "bot_status", new_status)
        else:
            new_status = "running"
            await set_setting(db, "bot_status", new_status)
            await set_setting(db, "bot_started_at", datetime.now(timezone.utc).isoformat())

        await manager.broadcast({"type": "status_changed", "data": {"status": new_status}})

        return await _build_status_payload(db)
    except Exception as exc:
        logger.exception("Error toggling status")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/account")
async def get_account():
    """Return account summary."""
    try:
        db = await get_db()
        pnl = await get_pnl_summary(db)
        positions = await get_open_positions(db)
        exposure = await get_total_exposure(db)

        # Count today's trades
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        all_trades = await get_trade_history(db, limit=10000)
        trades_today = sum(
            1
            for t in all_trades
            if t.get("created_at") and str(t["created_at"])[:10] == today_str
        )

        # Balance from settings or default 1000.0
        balance_val = await get_setting(db, "balance")
        balance = float(balance_val) if balance_val else 1000.0

        today_pnl = pnl["today_pnl"]
        today_pnl_pct = (today_pnl / balance * 100) if balance > 0 else 0.0

        return {
            "balance": round(balance, 2),
            "today_pnl": round(today_pnl, 2),
            "today_pnl_pct": round(today_pnl_pct, 2),
            "alltime_pnl": round(pnl["total_pnl"], 2),
            "total_trades": pnl["total_trades"],
            "trades_today": trades_today,
            "win_rate": pnl["win_rate"],
            "open_positions_count": len(positions),
            "total_exposure": round(exposure, 2),
        }
    except Exception as exc:
        logger.exception("Error fetching account summary")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/positions")
async def get_positions():
    """Return open positions."""
    try:
        db = await get_db()
        positions = await get_open_positions(db)
        return positions
    except Exception as exc:
        logger.exception("Error fetching positions")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/trades")
async def get_trades(
    city: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Return trade history with optional filters."""
    try:
        db = await get_db()
        trades = await get_trade_history(db, city=city, outcome=outcome, limit=limit, offset=offset)
        return trades
    except Exception as exc:
        logger.exception("Error fetching trades")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/trades/pnl")
async def get_trades_pnl(
    period: str = Query(default="alltime"),
):
    """Return cumulative P&L data points."""
    try:
        db = await get_db()

        # Query all resolved trades ordered by resolved_at
        query = """
            SELECT resolved_at, pnl
            FROM trades
            WHERE outcome IN ('won', 'lost') AND resolved_at IS NOT NULL
            ORDER BY resolved_at
        """
        cursor = await db.execute(query)
        rows = await cursor.fetchall()

        cumulative = 0.0
        result = []
        for row in rows:
            cumulative += float(row["pnl"] or 0)
            result.append({
                "date": str(row["resolved_at"])[:10] if row["resolved_at"] else None,
                "cumulative_pnl": round(cumulative, 2),
            })

        return result
    except Exception as exc:
        logger.exception("Error fetching PnL data")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/trades/pnl/by-city")
async def get_trades_pnl_by_city():
    """Return P&L and trade stats grouped by city."""
    try:
        db = await get_db()
        rows = await get_pnl_by_city(db)
        result = []
        for row in rows:
            trades = row.get("trades", 0) or 0
            wins = row.get("wins", 0) or 0
            stake = float(row.get("total_stake", 0) or 0)
            pnl = float(row.get("total_pnl", 0) or 0)
            result.append({
                "city": row.get("city"),
                "trades": trades,
                "wins": wins,
                "losses": row.get("losses", 0) or 0,
                "win_rate": round(wins / trades, 3) if trades else 0.0,
                "total_stake": round(stake, 2),
                "total_pnl": round(pnl, 2),
                "roi_pct": round((pnl / stake * 100), 2) if stake else 0.0,
            })
        return result
    except Exception as exc:
        logger.exception("Error fetching P&L by city")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/calibration/by-city")
async def get_calibration_by_city():
    """Return model calibration buckets (predicted vs. actual win rate)."""
    try:
        db = await get_db()
        return await get_calibration_buckets(db)
    except Exception as exc:
        logger.exception("Error fetching calibration")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/markets")
async def get_markets():
    """Return list of city market info."""
    try:
        db = await get_db()
        positions = await get_open_positions(db)

        result = []
        for city_info in CITIES:
            enabled_val = await get_setting(db, city_info["setting_key"])
            enabled = enabled_val is None or enabled_val.lower() == "true"

            has_position = any(
                p.get("city", "").lower().replace(" ", "") == city_info["name"]
                or p.get("city") == city_info["display_name"]
                for p in positions
            )
            status = "entered" if has_position else ("watching" if enabled else "no_opportunity")

            cached = _markets_cache.get(city_info["name"], {})
            result.append({
                "name": city_info["name"],
                "display_name": city_info["display_name"],
                "enabled": enabled,
                "status": status,
                "unit": city_info["unit"],
                "forecast_temp": cached.get("forecast_temp"),
                "market_price": cached.get("market_price"),
                "edge": cached.get("edge"),
            })

        return result
    except Exception as exc:
        logger.exception("Error fetching markets")
        raise HTTPException(status_code=500, detail=str(exc))


def _find_city(city: str) -> dict | None:
    """Look up a city entry in CITIES by name or display_name (case-insensitive)."""
    needle = city.lower()
    for c in CITIES:
        if c["name"].lower() == needle or c["display_name"].lower().replace(" ", "") == needle:
            return c
    return None


@app.post("/api/markets/{city}/toggle", dependencies=[Depends(verify_auth)])
async def toggle_market(city: str):
    """Toggle a city market between enabled and disabled."""
    try:
        city_info = _find_city(city)
        if city_info is None:
            raise HTTPException(status_code=404, detail=f"City not found: {city}")

        db = await get_db()
        current = await get_setting(db, city_info["setting_key"])
        new_value = "false" if (current and current.lower() == "true") else "true"
        await set_setting(db, city_info["setting_key"], new_value)

        return {"city": city_info["name"], "enabled": new_value == "true"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error toggling market for %s", city)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/markets/{city}/detail")
async def get_market_detail(city: str):
    """Return city info with temperature buckets (placeholder)."""
    try:
        city_info = _find_city(city)
        if city_info is None:
            raise HTTPException(status_code=404, detail=f"City not found: {city}")

        db = await get_db()
        enabled_val = await get_setting(db, city_info["setting_key"])
        enabled = enabled_val is None or enabled_val.lower() == "true"

        city_positions = [
            p for p in await get_open_positions(db)
            if p.get("city", "").lower().replace(" ", "") == city_info["name"]
            or p.get("city") == city_info["display_name"]
        ]

        return {
            "name": city_info["name"],
            "display_name": city_info["display_name"],
            "enabled": enabled,
            "unit": city_info["unit"],
            "buckets": [],
            "positions": city_positions,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error fetching market detail for %s", city)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/settings")
async def get_settings():
    """Return all settings as key-value dict."""
    try:
        db = await get_db()
        cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    except Exception as exc:
        logger.exception("Error fetching settings")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/settings/api-keys")
async def get_api_keys_status():
    """Check whether Polymarket API keys are configured in the environment."""
    required = ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER"]
    keys = {k: bool(os.environ.get(k, "").strip()) for k in required}
    return {"all_configured": all(keys.values()), "keys": keys}


@app.put("/api/settings", dependencies=[Depends(verify_auth)])
async def update_settings(body: dict[str, str]):
    """Update one or more settings."""
    try:
        # Block disabling paper mode without API keys
        paper_val = body.get("paper_mode", "").lower()
        if paper_val in ("false", "0"):
            required = ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER"]
            missing = [k for k in required if not os.environ.get(k, "").strip()]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot enable live trading: missing environment variables: {', '.join(missing)}",
                )

        db = await get_db()
        for key, value in body.items():
            await set_setting(db, key, str(value))

        cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error updating settings")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/alerts")
async def get_alerts_endpoint(
    alert_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Return alert history."""
    try:
        db = await get_db()
        alerts = await get_alerts(db, alert_type=alert_type, limit=limit)
        return alerts
    except Exception as exc:
        logger.exception("Error fetching alerts")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/alerts/test", dependencies=[Depends(verify_auth)])
async def post_test_alert():
    """Send a test alert."""
    try:
        db = await get_db()
        success = await send_test_alert(db)
        return {"success": success}
    except Exception as exc:
        logger.exception("Error sending test alert")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/scan/trigger", dependencies=[Depends(verify_auth)])
async def trigger_scan():
    """Manually trigger one scan cycle."""
    try:
        result = await run_scan_cycle()

        # Populate live markets cache from scan opportunities
        for signal in getattr(result, "opportunities", []):
            city_key = signal.city.lower().replace(" ", "")
            _markets_cache[city_key] = {
                "forecast_temp": round(signal.noaa_probability * 100, 1),
                "market_price": round(signal.market_price, 4),
                "edge": round(signal.edge, 4),
            }

        summary = {
            "started_at": result.started_at.isoformat() if hasattr(result, "started_at") else None,
            "completed_at": result.completed_at.isoformat() if hasattr(result, "completed_at") else None,
            "cities_scanned": getattr(result, "cities_scanned", 0),
            "markets_found": getattr(result, "markets_found", 0),
            "opportunities": len(getattr(result, "opportunities", [])),
            "trades_executed": len(getattr(result, "trades_executed", [])),
            "errors": getattr(result, "errors", []),
        }

        await manager.broadcast({"type": "scan_completed", "data": summary})
        return summary
    except Exception as exc:
        logger.exception("Error triggering scan")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial status on connect
        db = await get_db()
        status = await get_setting(db, "bot_status") or "stopped"
        positions = await get_open_positions(db)
        await websocket.send_json({
            "type": "initial",
            "data": {
                "status": status,
                "positions": positions,
            },
        })

        # Keep connection alive, listen for client messages
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
