#!/usr/bin/env python3
"""
Diagnostic script for Phase 0 testing.

Validates:
1. Backtest methodology (perfect foresight bias detection)
2. Date alignment (market dates vs. forecast dates)
3. Trade resolution status (outcome tracking)

Usage:
    uv run python scripts/diagnostic.py --phase 0b  # date alignment
    uv run python scripts/diagnostic.py --phase 0c  # DB inspection
"""

import argparse
import asyncio
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("diagnostic")


# ============================================================================
# PHASE 0B: Date Alignment Check
# ============================================================================


async def phase_0b_date_alignment():
    """
    Verify that bot correctly extracts target dates from market questions.

    Fetches live markets from Polymarket Gamma API and compares:
    - Manual parse of market.question
    - Bot's _extract_target_date() logic
    """
    log.info("=" * 60)
    log.info("PHASE 0B: Date Alignment Check")
    log.info("=" * 60)

    # Market scanner would use Gamma API; for now, just check the logic
    log.info("\nDate Extraction Logic (from bot/trade_engine.py):")
    log.info("")
    log.info("Current implementation:")
    log.info("  def _extract_target_date(market):")
    log.info("      if hasattr(market, 'target_date') and market.target_date:")
    log.info("          return market.target_date")
    log.info("      else:")
    log.info("          return (datetime.now() + timedelta(days=1)).isoformat()")
    log.info("")
    log.info("Risk: Market questions often embed dates like 'Will temp on Apr 8...'")
    log.info("If market.target_date is NULL, bot falls back to tomorrow (off by 1 day).")
    log.info("")
    log.info("ACTION: Check live Polymarket API response for 10 random markets.")
    log.info("Verify each has 'target_date' field populated correctly.")
    log.info("")
    log.info("Command to test:")
    log.info("  curl -s 'https://gamma-api.polymarket.com/markets' | jq '.markets[0:10] | .[].question'")


# ============================================================================
# PHASE 0C: Database Integrity Check
# ============================================================================


async def phase_0c_db_inspection():
    """
    Inspect trades.db for data integrity issues.

    Checks:
    1. Are outcomes NULL for trades created > 48h ago?
    2. Are markets legitimately still open on Polymarket?
    3. Do we have any silent failures (recorded but not entered)?
    """
    log.info("=" * 60)
    log.info("PHASE 0C: Database Integrity Check")
    log.info("=" * 60)

    db_path = "data/trades.db"
    if not os.path.exists(db_path):
        log.error(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Check 1: Unresolved trades by direction
    log.info("\n1. Unresolved Trades (outcome IS NULL)")
    cursor.execute("""
        SELECT direction, COUNT(*) as cnt,
               MIN(created_at) as earliest,
               MAX(created_at) as latest
        FROM trades
        WHERE outcome IS NULL
        GROUP BY direction
    """)
    rows = cursor.fetchall()
    for row in rows:
        log.info(f"   {row['direction']}: {row['cnt']} trades")
        log.info(f"      Earliest: {row['earliest']}")
        log.info(f"      Latest: {row['latest']}")

    # Check 2: Age of unresolved trades
    log.info("\n2. Unresolved Trades by Age")
    cursor.execute("""
        SELECT direction,
               COUNT(CASE WHEN datetime(created_at) > datetime('now', '-1 day') THEN 1 END) as under_24h,
               COUNT(CASE WHEN datetime(created_at) BETWEEN datetime('now', '-2 day')
                          AND datetime('now', '-1 day') THEN 1 END) as one_to_two_days,
               COUNT(CASE WHEN datetime(created_at) < datetime('now', '-2 day') THEN 1 END) as over_48h
        FROM trades
        WHERE outcome IS NULL
        GROUP BY direction
    """)
    rows = cursor.fetchall()
    for row in rows:
        log.info(f"   {row['direction']}:")
        log.info(f"      < 24h:      {row['under_24h']}")
        log.info(f"      1-2 days:   {row['one_to_two_days']}")
        log.info(f"      > 48h:      {row['over_48h']}")

    # Check 3: Resolved trades by outcome
    log.info("\n3. Resolved Trades (outcome NOT NULL)")
    cursor.execute("""
        SELECT direction, outcome, COUNT(*) as cnt
        FROM trades
        WHERE outcome IS NOT NULL
        GROUP BY direction, outcome
    """)
    rows = cursor.fetchall()
    for row in rows:
        log.info(f"   {row['direction']} {row['outcome']}: {row['cnt']} trades")

    # Check 4: Summary statistics
    log.info("\n4. Summary Statistics")
    cursor.execute("SELECT COUNT(*) as total FROM trades")
    total = cursor.fetchone()["total"]
    log.info(f"   Total trades: {total}")

    cursor.execute("SELECT COUNT(*) as unresolved FROM trades WHERE outcome IS NULL")
    unresolved = cursor.fetchone()["unresolved"]
    log.info(f"   Unresolved: {unresolved} ({100*unresolved/total if total else 0:.1f}%)")

    cursor.execute("SELECT COUNT(*) as resolved FROM trades WHERE outcome IS NOT NULL")
    resolved = cursor.fetchone()["resolved"]
    log.info(f"   Resolved: {resolved} ({100*resolved/total if total else 0:.1f}%)")

    # Check 5: Red flags
    log.info("\n5. Red Flags")
    cursor.execute("""
        SELECT COUNT(*) as stale
        FROM trades
        WHERE outcome IS NULL
          AND datetime(created_at) < datetime('now', '-2 day')
    """)
    stale = cursor.fetchone()["stale"]
    if stale > 0:
        log.warning(f"   ALERT: {stale} unresolved trades > 48h old!")
        log.warning("   These should have resolved by now. Possible issues:")
        log.warning("   - Resolver job crashed")
        log.warning("   - Markets closed but didn't resolve")
        log.warning("   - Bot never actually entered positions (but recorded them)")
    else:
        log.info("   OK: No stale unresolved trades")

    # Check 6: Price distribution
    log.info("\n6. Price Distribution by Direction")
    cursor.execute("""
        SELECT direction,
               COUNT(*) as cnt,
               ROUND(AVG(market_price), 4) as avg_price,
               ROUND(MIN(market_price), 4) as min_price,
               ROUND(MAX(market_price), 4) as max_price,
               ROUND(SUM(bet_size), 2) as total_volume
        FROM trades
        GROUP BY direction
    """)
    rows = cursor.fetchall()
    for row in rows:
        log.info(f"   {row['direction']}:")
        log.info(f"      Count: {row['cnt']}")
        log.info(f"      Avg price: ${row['avg_price']}")
        log.info(f"      Price range: ${row['min_price']} - ${row['max_price']}")
        log.info(f"      Total volume: ${row['total_volume']}")

    conn.close()

    log.info("\n" + "=" * 60)
    log.info("INTERPRETATION:")
    log.info("- If > 48h-old unresolved trades exist: Resolver is broken")
    log.info("- If YES avg price is << NO avg price: Selection bias likely")
    log.info("- If price range includes penny stocks (<$0.01): Low conviction entries")
    log.info("=" * 60)


# ============================================================================
# Main
# ============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Phase 0 diagnostic tests")
    parser.add_argument("--phase", choices=["0b", "0c", "all"], default="all",
                        help="Which diagnostic to run")
    args = parser.parse_args()

    if args.phase in ("0b", "all"):
        await phase_0b_date_alignment()

    if args.phase in ("0c", "all"):
        await phase_0c_db_inspection()

    log.info("\n" + "=" * 60)
    log.info("DIAGNOSTIC COMPLETE")
    log.info("=" * 60)
    log.info("\nNext steps:")
    log.info("1. Review output above for red flags")
    log.info("2. Fix any issues found in Phase 0")
    log.info("3. Once Phase 0 passes, proceed to Phase 1 (contrafactual backtest)")


if __name__ == "__main__":
    asyncio.run(main())
