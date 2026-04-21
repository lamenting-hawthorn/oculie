"""
Database module for the Polymarket Weather Prediction Agent.

Manages SQLite storage for trades, positions, settings, alerts, and scan logs
using aiosqlite for async operations.
"""

import logging
import os

import aiosqlite

logger = logging.getLogger(__name__)

# Module-level singleton connection
_db_connection: aiosqlite.Connection | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    market_question TEXT NOT NULL,
    condition_id TEXT,
    token_id TEXT,
    target_date TEXT,
    temp_low REAL,
    temp_high REAL,
    temp_unit TEXT,
    direction TEXT NOT NULL,
    noaa_probability REAL NOT NULL,
    market_price REAL NOT NULL,
    edge REAL NOT NULL,
    bet_size REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    outcome TEXT,
    pnl REAL,
    paper_trade BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    condition_id TEXT,
    city TEXT NOT NULL,
    market_question TEXT NOT NULL,
    token_id TEXT,
    target_date TEXT,
    temp_low REAL,
    temp_high REAL,
    temp_unit TEXT,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    size REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,
    city TEXT,
    message TEXT NOT NULL,
    channel TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    cities_scanned INTEGER DEFAULT 0,
    markets_found INTEGER DEFAULT 0,
    opportunities_found INTEGER DEFAULT 0,
    trades_executed INTEGER DEFAULT 0,
    errors TEXT
);
"""

DEFAULT_SETTINGS = {
    "entry_threshold": "0.03",
    "max_bet_size": "50.0",
    "max_total_exposure": "200.0",
    "account_floor": "100.0",
    "scan_interval_minutes": "30",
    "paper_mode": "true",
    "messaging_app": "telegram",
    "alert_trade_entered": "true",
    "alert_trade_resolved": "true",
    "alert_daily_summary": "true",
    "alert_errors": "true",
    "cities_nyc": "true",
    "cities_chicago": "true",
    "cities_miami": "true",
    "cities_dallas": "true",
    "cities_seattle": "true",
    "cities_atlanta": "true",
    "cities_london": "true",
    "cities_seoul": "true",
    "cities_shanghai": "true",
    "cities_hongkong": "true",
    "exit_threshold": "0.30",
    "telegram_update_offset": "0",
    "live_trading_budget_usdc": "",
    "max_city_date_exposure": "40.0",
    "max_trades_per_city_date": "2",
    "max_direction_ratio": "0.75",
    "require_source_agreement": "true",
}


async def init_db(db_path: str = "data/trades.db") -> aiosqlite.Connection:
    """Create tables if they don't exist, seed default settings, and return the connection."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row

    # WAL mode: better read/write concurrency for bot + FastAPI sharing the same DB file
    await db.execute("PRAGMA journal_mode=WAL")
    await db.commit()

    await db.executescript(SCHEMA_SQL)
    logger.info("Database schema initialised at %s", db_path)

    # Auto-migrate existing databases.
    migrations = [
        ("positions", "condition_id", "TEXT"),
        ("trades", "target_date", "TEXT"),
        ("trades", "temp_low", "REAL"),
        ("trades", "temp_high", "REAL"),
        ("trades", "temp_unit", "TEXT"),
        ("positions", "target_date", "TEXT"),
        ("positions", "temp_low", "REAL"),
        ("positions", "temp_high", "REAL"),
        ("positions", "temp_unit", "TEXT"),
    ]
    import sqlite3
    for table, column, column_type in migrations:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
            await db.commit()
            logger.info("Migrated %s table: added %s column", table, column)
        except sqlite3.OperationalError:
            # Column already exists — idempotent ALTER on already-migrated DBs.
            pass

    # Seed default settings (INSERT OR IGNORE so existing values aren't overwritten)
    for key, value in DEFAULT_SETTINGS.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    await db.commit()
    logger.info("Default settings seeded (%d keys)", len(DEFAULT_SETTINGS))

    return db


async def get_db(db_path: str = "data/trades.db") -> aiosqlite.Connection:
    """Return the singleton database connection, creating it if necessary."""
    global _db_connection
    if _db_connection is None:
        _db_connection = await init_db(db_path)
    return _db_connection


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

async def insert_trade(db: aiosqlite.Connection, **kwargs) -> int:
    """Insert a new trade and return its row ID."""
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    values = tuple(kwargs.values())

    cursor = await db.execute(
        f"INSERT INTO trades ({columns}) VALUES ({placeholders})",
        values,
    )
    await db.commit()
    trade_id = cursor.lastrowid
    logger.info("Inserted trade %d: %s %s on %s", trade_id, kwargs.get("direction"), kwargs.get("market_question", ""), kwargs.get("city", ""))
    return trade_id


async def update_trade(db: aiosqlite.Connection, trade_id: int, **kwargs) -> None:
    """Update one or more fields on an existing trade."""
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = tuple(kwargs.values()) + (trade_id,)

    await db.execute(
        f"UPDATE trades SET {set_clause} WHERE id = ?",
        values,
    )
    await db.commit()
    logger.info("Updated trade %d: %s", trade_id, list(kwargs.keys()))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

async def get_open_positions(db: aiosqlite.Connection) -> list[dict]:
    """Return all positions that have not been closed."""
    cursor = await db.execute(
        "SELECT * FROM positions WHERE closed_at IS NULL ORDER BY opened_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def insert_position(
    db: aiosqlite.Connection,
    trade_id: int,
    condition_id: str | None,
    city: str,
    market_question: str,
    token_id: str,
    direction: str,
    entry_price: float,
    size: float,
    paper: bool,
    target_date: str | None = None,
    temp_low: float | None = None,
    temp_high: float | None = None,
    temp_unit: str | None = None,
) -> int:
    """Insert a new open position and return its row ID."""
    cursor = await db.execute(
        """
        INSERT INTO positions
            (trade_id, condition_id, city, market_question, token_id,
             target_date, temp_low, temp_high, temp_unit,
             direction, entry_price, size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_id, condition_id, city, market_question, token_id,
         target_date, temp_low, temp_high, temp_unit, direction, entry_price, size),
    )
    await db.commit()
    position_id = cursor.lastrowid
    logger.info(
        "Inserted position %d: trade_id=%d %s %s on %s size=$%.2f",
        position_id, trade_id, direction, market_question, city, size,
    )
    return position_id


async def get_total_exposure(db: aiosqlite.Connection) -> float:
    """Return the sum of sizes across all open positions."""
    cursor = await db.execute(
        "SELECT COALESCE(SUM(size), 0) AS total FROM positions WHERE closed_at IS NULL"
    )
    row = await cursor.fetchone()
    return float(row["total"])


async def get_city_date_exposure(
    db: aiosqlite.Connection, city: str, target_date: str
) -> float:
    """Return the sum of sizes for open positions matching a city and target date.

    Since the positions table doesn't store target_date directly, we match
    against the market_question text using a LIKE pattern for the date.
    """
    # Build a pattern like "%April 14%" from "2026-04-14"
    try:
        from datetime import datetime as _dt
        dt = _dt.strptime(target_date, "%Y-%m-%d")
        month_names = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        date_pattern = f"%{month_names[dt.month]} {dt.day}%"
    except (ValueError, IndexError):
        return 0.0

    cursor = await db.execute(
        """
        SELECT COALESCE(SUM(size), 0) AS total
        FROM positions
        WHERE closed_at IS NULL AND city = ? AND market_question LIKE ?
        """,
        (city, date_pattern),
    )
    row = await cursor.fetchone()
    return float(row["total"])


async def get_direction_exposure(
    db: aiosqlite.Connection, direction: str
) -> float:
    """Return the sum of sizes for open positions in a given direction (YES/NO)."""
    cursor = await db.execute(
        "SELECT COALESCE(SUM(size), 0) AS total FROM positions WHERE closed_at IS NULL AND direction = ?",
        (direction,),
    )
    row = await cursor.fetchone()
    return float(row["total"])


async def get_bot_status(db: aiosqlite.Connection) -> str:
    """Return the current bot_status setting value (defaults to 'running')."""
    cursor = await db.execute("SELECT value FROM settings WHERE key = 'bot_status'")
    row = await cursor.fetchone()
    return row["value"] if row else "running"


# ---------------------------------------------------------------------------
# Trade history
# ---------------------------------------------------------------------------

async def get_trade_history(
    db: aiosqlite.Connection,
    city: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Return trade history with optional filters."""
    query = "SELECT * FROM trades WHERE 1=1"
    params: list = []

    if city is not None:
        query += " AND city = ?"
        params.append(city)
    if outcome is not None:
        query += " AND outcome = ?"
        params.append(outcome)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_setting(db: aiosqlite.Connection, key: str) -> str | None:
    """Retrieve a single setting value by key."""
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    """Upsert a setting."""
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value),
    )
    await db.commit()
    logger.info("Setting updated: %s = %s", key, value)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

async def insert_alert(db: aiosqlite.Connection, **kwargs) -> int:
    """Insert an alert record and return its ID."""
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    values = tuple(kwargs.values())

    cursor = await db.execute(
        f"INSERT INTO alerts ({columns}) VALUES ({placeholders})",
        values,
    )
    await db.commit()
    alert_id = cursor.lastrowid
    logger.info("Inserted alert %d: %s", alert_id, kwargs.get("alert_type", ""))
    return alert_id


async def get_alerts(
    db: aiosqlite.Connection,
    alert_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent alerts, optionally filtered by type."""
    if alert_type is not None:
        cursor = await db.execute(
            "SELECT * FROM alerts WHERE alert_type = ? ORDER BY sent_at DESC LIMIT ?",
            (alert_type, limit),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM alerts ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Scan log
# ---------------------------------------------------------------------------

async def insert_scan_log(db: aiosqlite.Connection, **kwargs) -> int:
    """Insert a scan-log entry and return its ID."""
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    values = tuple(kwargs.values())

    cursor = await db.execute(
        f"INSERT INTO scan_log ({columns}) VALUES ({placeholders})",
        values,
    )
    await db.commit()
    scan_id = cursor.lastrowid
    logger.info("Inserted scan_log %d", scan_id)
    return scan_id


# ---------------------------------------------------------------------------
# PnL summary
# ---------------------------------------------------------------------------

async def get_pnl_summary(db: aiosqlite.Connection) -> dict:
    """Return an aggregate PnL summary across all resolved trades."""
    cursor = await db.execute(
        """
        SELECT
            COALESCE(SUM(pnl), 0)                                          AS total_pnl,
            COALESCE(SUM(CASE WHEN DATE(resolved_at) = DATE('now') THEN pnl ELSE 0 END), 0) AS today_pnl,
            COUNT(*)                                                        AS total_trades,
            SUM(CASE WHEN outcome = 'won'  THEN 1 ELSE 0 END)              AS wins,
            SUM(CASE WHEN outcome = 'lost' THEN 1 ELSE 0 END)              AS losses
        FROM trades
        WHERE outcome IN ('won', 'lost')
        """
    )
    row = await cursor.fetchone()

    total_trades = row["total_trades"] or 0
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    return {
        "total_pnl": float(row["total_pnl"]),
        "today_pnl": float(row["today_pnl"]),
        "win_rate": round(win_rate, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
    }


# ---------------------------------------------------------------------------
# Enhanced reporting (per-city P&L, calibration, exposure breakdown)
# ---------------------------------------------------------------------------


async def get_pnl_by_city(db: aiosqlite.Connection) -> list[dict]:
    """Return P&L breakdown grouped by city."""
    cursor = await db.execute(
        """
        SELECT
            city,
            COUNT(*) AS trades,
            SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN outcome = 'lost' THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl), 0) AS total_pnl,
            COALESCE(SUM(bet_size), 0) AS total_stake
        FROM trades
        WHERE outcome IN ('won', 'lost')
        GROUP BY city
        ORDER BY total_pnl DESC
        """
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_calibration_buckets(db: aiosqlite.Connection) -> list[dict]:
    """Return calibration data: predicted vs actual win rate by probability bucket.

    Groups resolved trades into 10% probability buckets based on the
    noaa_probability (the model's dampened probability at entry).
    """
    cursor = await db.execute(
        """
        SELECT
            CAST(noaa_probability * 10 AS INTEGER) AS bucket_idx,
            COUNT(*) AS count,
            SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END) AS wins,
            AVG(noaa_probability) AS avg_predicted
        FROM trades
        WHERE outcome IN ('won', 'lost')
        GROUP BY bucket_idx
        ORDER BY bucket_idx
        """
    )
    rows = await cursor.fetchall()
    results = []
    for row in rows:
        row_dict = dict(row)
        bucket_idx = row_dict["bucket_idx"]
        count = row_dict["count"]
        wins = row_dict["wins"]
        results.append({
            "bucket": f"{bucket_idx * 10}-{(bucket_idx + 1) * 10}%",
            "count": count,
            "wins": wins,
            "predicted_avg": round(row_dict["avg_predicted"], 3),
            "actual_win_rate": round(wins / count, 3) if count > 0 else 0.0,
        })
    return results


async def get_brier_score(db: aiosqlite.Connection) -> float | None:
    """Compute Brier score across all resolved trades.

    Brier = mean((predicted - actual)^2) where actual = 1 for win, 0 for loss.
    Lower is better; 0.25 = coin-flip baseline.
    """
    cursor = await db.execute(
        """
        SELECT
            noaa_probability,
            CASE WHEN outcome = 'won' THEN 1.0 ELSE 0.0 END AS actual
        FROM trades
        WHERE outcome IN ('won', 'lost')
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return None
    total = sum((row["noaa_probability"] - row["actual"]) ** 2 for row in rows)
    return round(total / len(rows), 4)


async def get_exposure_summary(db: aiosqlite.Connection) -> dict:
    """Return a breakdown of open/closed exposure and P&L."""
    # Open positions
    open_cursor = await db.execute(
        """
        SELECT
            COUNT(*) AS open_count,
            COALESCE(SUM(size), 0) AS open_exposure,
            COALESCE(SUM(unrealized_pnl), 0) AS unrealized_pnl
        FROM positions WHERE closed_at IS NULL
        """
    )
    open_row = await open_cursor.fetchone()

    # Realized P&L
    realized_cursor = await db.execute(
        """
        SELECT
            COUNT(*) AS resolved_count,
            COALESCE(SUM(pnl), 0) AS realized_pnl,
            COALESCE(SUM(bet_size), 0) AS resolved_stake
        FROM trades WHERE outcome IN ('won', 'lost')
        """
    )
    realized_row = await realized_cursor.fetchone()

    # Direction breakdown
    dir_cursor = await db.execute(
        """
        SELECT
            direction,
            COUNT(*) AS count,
            COALESCE(SUM(size), 0) AS exposure
        FROM positions WHERE closed_at IS NULL
        GROUP BY direction
        """
    )
    dir_rows = await dir_cursor.fetchall()
    direction_breakdown = {row["direction"]: {"count": row["count"], "exposure": float(row["exposure"])} for row in dir_rows}

    return {
        "open_positions": open_row["open_count"] or 0,
        "open_exposure": float(open_row["open_exposure"]),
        "unrealized_pnl": float(open_row["unrealized_pnl"]),
        "resolved_trades": realized_row["resolved_count"] or 0,
        "realized_pnl": float(realized_row["realized_pnl"]),
        "resolved_stake": float(realized_row["resolved_stake"]),
        "worst_case_loss": float(open_row["open_exposure"]) * -1,
        "direction_breakdown": direction_breakdown,
    }


# ---------------------------------------------------------------------------
# Standalone initialisation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    async def _main():
        db = await init_db()
        logger.info("Database ready.")

        # Quick verification
        settings_cursor = await db.execute("SELECT COUNT(*) AS cnt FROM settings")
        settings_row = await settings_cursor.fetchone()
        logger.info("Settings count: %d", settings_row["cnt"])

        summary = await get_pnl_summary(db)
        logger.info("PnL summary: %s", summary)

        await db.close()

    asyncio.run(_main())
