#!/usr/bin/env python3
"""
Live trading run — 5 minutes, $15 max budget.

Optimized from past paper trading logs:
- Raised edge thresholds (YES≥0.20, NO≥0.12) to avoid low-conviction losses
- Smaller max bet ($5) to protect limited budget
- Tighter Kelly scaling (0.20) for conservative sizing
- All cities enabled (Seoul/intl were most profitable historically)

Usage:
    uv run python scripts/live_5min.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env from project root (fallback to parent dirs)
ENV_CANDIDATES = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT.parent / ".env",
    Path("/Users/raghav/Projects/openclaw-weather/.env"),
]
for env_path in ENV_CANDIDATES:
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)
        break

os.makedirs(PROJECT_ROOT / "data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("live_5min")

import bot.database as db_mod
from bot.database import (
    get_pnl_summary,
    get_exposure_summary,
    get_trade_history,
    init_db,
    set_setting,
)
from bot.trade_engine import run_scan_cycle


# ── Configuration ──────────────────────────────────────────────────────────
DURATION_MINUTES = 5
SCAN_INTERVAL_SECONDS = 60   # scan every 60s for 5 min run
LIVE_BUDGET_USDC = 15.0
MAX_BET_SIZE = 5.0           # small bets to protect budget
DB_PATH = str(PROJECT_ROOT / "data" / "live_trading.db")

# All cities enabled (Seoul/intl historically most profitable)
ALL_CITY_SETTINGS = {
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
}


async def main():
    logger.info("=" * 65)
    logger.info("  LIVE TRADING — $%.2f budget, %d minute run", LIVE_BUDGET_USDC, DURATION_MINUTES)
    logger.info("=" * 65)

    # Verify credentials exist
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    funder = os.environ.get("POLYMARKET_FUNDER", "")
    if not pk or not funder:
        logger.error("POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER must be set!")
        logger.error("Checked .env paths: %s", [str(p) for p in ENV_CANDIDATES])
        sys.exit(1)
    logger.info("Credentials loaded (funder=%s...%s)", funder[:6], funder[-4:])

    # Check CLOB balance before starting
    try:
        from bot.clob_client import build_clob_client, get_usdc_balance
        client = build_clob_client()
        balance = get_usdc_balance(client)
        logger.info("CLOB USDC balance: $%.2f", balance)
        if balance < LIVE_BUDGET_USDC:
            logger.warning(
                "Balance $%.2f < budget $%.2f — trades will be sized to available balance",
                balance, LIVE_BUDGET_USDC,
            )
    except Exception as exc:
        logger.error("Failed to connect to CLOB: %s", exc)
        sys.exit(1)

    # Initialize database
    db = await init_db(DB_PATH)
    db_mod._db_connection = db
    logger.info("Database: %s", DB_PATH)

    # ── Settings optimized from past profitable trades ──
    # LIVE mode (not paper!)
    await set_setting(db, "paper_mode", "false")

    # Budget and exposure
    await set_setting(db, "live_trading_budget_usdc", str(LIVE_BUDGET_USDC))
    await set_setting(db, "max_bet_size", str(MAX_BET_SIZE))
    await set_setting(db, "max_total_exposure", str(LIVE_BUDGET_USDC))
    await set_setting(db, "account_floor", "0.0")  # use full budget

    # Higher thresholds from past log analysis:
    # YES winners had avg edge 0.34, losers 0.19 → raise YES threshold
    # NO winners had avg edge 0.11 → keep NO threshold moderate
    await set_setting(db, "entry_threshold_yes", "0.20")
    await set_setting(db, "entry_threshold_no", "0.10")

    # Portfolio risk controls
    await set_setting(db, "max_city_date_exposure", str(LIVE_BUDGET_USDC))  # one city can use full budget
    await set_setting(db, "max_trades_per_city_date", "2")
    await set_setting(db, "max_direction_ratio", "0.80")

    for key, val in ALL_CITY_SETTINGS.items():
        await set_setting(db, key, val)

    logger.info("LIVE mode ON | Budget=$%.2f | Max bet=$%.2f", LIVE_BUDGET_USDC, MAX_BET_SIZE)
    logger.info("Thresholds: YES≥0.20 NO≥0.10 | Kelly scale=0.25")
    logger.info("Scan interval: %ds | Duration: %d min", SCAN_INTERVAL_SECONDS, DURATION_MINUTES)

    start_time = time.time()
    end_time = start_time + DURATION_MINUTES * 60
    cycle_num = 0
    all_results = []

    while time.time() < end_time:
        cycle_num += 1
        remaining = (end_time - time.time()) / 60
        logger.info("")
        logger.info("=" * 65)
        logger.info("  SCAN CYCLE %d  (%.1f min remaining)", cycle_num, remaining)
        logger.info("=" * 65)

        try:
            result = await run_scan_cycle()
            all_results.append(result)

            logger.info(
                "Cycle %d: cities=%d markets=%d opportunities=%d trades=%d errors=%d (%.1fs)",
                cycle_num,
                result.cities_scanned,
                result.markets_found,
                len(result.opportunities),
                len(result.trades_executed),
                len(result.errors),
                (result.completed_at - result.started_at).total_seconds(),
            )

            for sig in result.opportunities:
                logger.info(
                    "  -> %s %s: %s | prob=%.3f price=%.3f edge=%+.2f%% bet=$%.2f",
                    sig.direction, sig.city, sig.question[:60],
                    sig.noaa_probability, sig.market_price,
                    sig.edge * 100, sig.bet_size,
                )

            for tr in result.trades_executed:
                status = "OK" if tr.success else f"FAIL: {tr.error}"
                logger.info(
                    "  TRADE: %s %s $%.2f @ %.3f — %s (order=%s)",
                    tr.signal.direction, tr.signal.city,
                    tr.signal.bet_size, tr.signal.market_price,
                    status, tr.order_id or "n/a",
                )

            for err in result.errors:
                logger.warning("  ERROR: %s", err[:120])

        except Exception as exc:
            logger.error("Scan cycle %d crashed: %s", cycle_num, exc, exc_info=True)
            all_results.append(None)

        if time.time() < end_time:
            wait = min(SCAN_INTERVAL_SECONDS, end_time - time.time())
            if wait > 0:
                logger.info("Sleeping %.0fs until next cycle...", wait)
                await asyncio.sleep(wait)

    # ── Final Summary ──
    elapsed = time.time() - start_time
    total_trades = sum(len(r.trades_executed) for r in all_results if r)
    total_opps = sum(len(r.opportunities) for r in all_results if r)
    total_errors = sum(len(r.errors) for r in all_results if r)
    successful_trades = sum(
        sum(1 for t in r.trades_executed if t.success) for r in all_results if r
    )

    trades = await get_trade_history(db, limit=1000)
    exposure = await get_exposure_summary(db)

    print("\n" + "=" * 65)
    print("  LIVE TRADING — FINAL RESULTS")
    print("=" * 65)
    print(f"  Duration:          {elapsed / 60:.1f} minutes")
    print(f"  Scan cycles:       {cycle_num}")
    print(f"  Opportunities:     {total_opps}")
    print(f"  Trades placed:     {total_trades} ({successful_trades} successful)")
    print(f"  Errors:            {total_errors}")
    print(f"\n  Open exposure:     ${exposure['open_exposure']:.2f}")
    print(f"  Open positions:    {exposure['open_positions']}")

    if trades:
        live_trades = [t for t in trades if not t.get("paper_trade", True)]
        print(f"\n  LIVE TRADES:")
        for t in live_trades:
            status = t.get("outcome", "open") or "open"
            pnl_str = f"pnl=${t['pnl']:+.2f}" if t.get("pnl") is not None else "pnl=pending"
            print(
                f"    #{t['id']:<4d} {t.get('direction', '?'):3s} {t['city']:<20s} "
                f"edge={t.get('edge', 0):+.4f}  bet=${t.get('bet_size', 0):.2f}  "
                f"price={t.get('entry_price', 0):.3f}  [{status}] {pnl_str}"
            )
            print(f"          {t.get('market_question', '?')[:70]}")

    # Check final balance
    try:
        final_balance = get_usdc_balance(client)
        print(f"\n  Starting balance:  ${balance:.2f}")
        print(f"  Final balance:     ${final_balance:.2f}")
        print(f"  Delta:             ${final_balance - balance:+.2f}")
    except Exception:
        pass

    print(f"\n  Database saved: {DB_PATH}")
    print("=" * 65 + "\n")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
