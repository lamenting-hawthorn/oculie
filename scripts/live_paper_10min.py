#!/usr/bin/env python3
"""
Live paper trading run — 10 minutes of scan cycles with real data.

Fetches live weather forecasts (NOAA + Open-Meteo) and live Polymarket
prices. Runs in paper mode (no real orders). Outputs results at the end.

Usage:
    uv run python scripts/live_paper_10min.py
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

os.makedirs(PROJECT_ROOT / "data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("live_paper")

import bot.database as db_mod
from bot.database import (
    get_pnl_summary,
    get_pnl_by_city,
    get_calibration_buckets,
    get_brier_score,
    get_exposure_summary,
    get_trade_history,
    init_db,
    set_setting,
)
from bot.trade_engine import run_scan_cycle


DURATION_MINUTES = 10
SCAN_INTERVAL_SECONDS = 120  # scan every 2 minutes
DB_PATH = str(PROJECT_ROOT / "data" / "live_paper.db")

# All cities enabled
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
    logger.info("  LIVE PAPER TRADING — %d minute run", DURATION_MINUTES)
    logger.info("=" * 65)

    # Initialize database and set as singleton so run_scan_cycle uses it
    db = await init_db(DB_PATH)
    db_mod._db_connection = db
    logger.info("Database: %s", DB_PATH)

    # Configure paper mode and enable all cities
    await set_setting(db, "paper_mode", "true")
    await set_setting(db, "entry_threshold_yes", "0.12")
    await set_setting(db, "entry_threshold_no", "0.08")
    await set_setting(db, "max_bet_size", "50.0")
    await set_setting(db, "max_total_exposure", "200.0")
    await set_setting(db, "account_floor", "100.0")
    await set_setting(db, "max_city_date_exposure", "40.0")
    await set_setting(db, "max_trades_per_city_date", "2")
    await set_setting(db, "max_direction_ratio", "0.75")

    for key, val in ALL_CITY_SETTINGS.items():
        await set_setting(db, key, val)

    logger.info("Paper mode ON | All cities enabled")
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
                "Cycle %d result: cities=%d markets=%d opportunities=%d trades=%d errors=%d (%.1fs)",
                cycle_num,
                result.cities_scanned,
                result.markets_found,
                len(result.opportunities),
                len(result.trades_executed),
                len(result.errors),
                (result.completed_at - result.started_at).total_seconds(),
            )

            if result.opportunities:
                for sig in result.opportunities:
                    logger.info(
                        "  -> %s %s: %s | prob=%.3f price=%.3f edge=%+.2f%% bet=$%.2f",
                        sig.direction,
                        sig.city,
                        sig.question[:60],
                        sig.noaa_probability,
                        sig.market_price,
                        sig.edge * 100,
                        sig.bet_size,
                    )

            if result.errors:
                for err in result.errors:
                    logger.warning("  ERROR: %s", err[:120])

        except Exception as exc:
            logger.error("Scan cycle %d crashed: %s", cycle_num, exc, exc_info=True)
            all_results.append(None)

        # Wait for next cycle (unless time is up)
        if time.time() < end_time:
            wait = min(SCAN_INTERVAL_SECONDS, end_time - time.time())
            if wait > 0:
                logger.info("Sleeping %.0fs until next cycle...", wait)
                await asyncio.sleep(wait)

    # ---- Final Summary ----
    elapsed = time.time() - start_time
    total_cities = sum(r.cities_scanned for r in all_results if r)
    total_markets = sum(r.markets_found for r in all_results if r)
    total_opps = sum(len(r.opportunities) for r in all_results if r)
    total_trades = sum(len(r.trades_executed) for r in all_results if r)
    total_errors = sum(len(r.errors) for r in all_results if r)
    successful_trades = sum(
        sum(1 for t in r.trades_executed if t.success) for r in all_results if r
    )

    # Collect all unique opportunities
    all_signals = []
    for r in all_results:
        if r:
            all_signals.extend(r.opportunities)

    # Get trade history and enhanced reports from DB
    trades = await get_trade_history(db, limit=1000)
    pnl_summary = await get_pnl_summary(db)
    exposure = await get_exposure_summary(db)
    city_pnl = await get_pnl_by_city(db)
    calibration = await get_calibration_buckets(db)
    brier = await get_brier_score(db)

    print("\n" + "=" * 65)
    print("  LIVE PAPER TRADING — FINAL RESULTS")
    print("=" * 65)
    print(f"  Duration:          {elapsed / 60:.1f} minutes")
    print(f"  Scan cycles:       {cycle_num}")
    print(f"  Cities scanned:    {total_cities} (across all cycles)")
    print(f"  Markets found:     {total_markets}")
    print(f"  Opportunities:     {total_opps}")
    print(f"  Trades entered:    {total_trades} ({successful_trades} successful)")
    print(f"  Errors:            {total_errors}")

    # --- Exposure & P&L Summary ---
    print(f"\n  {'—' * 56}")
    print("  EXPOSURE & P&L SUMMARY:")
    print(f"    Realized P&L:         ${exposure['realized_pnl']:+.2f}  ({exposure['resolved_trades']} resolved)")
    print(f"    Unrealized P&L:       ${exposure['unrealized_pnl']:+.2f}  ({exposure['open_positions']} open)")
    print(f"    Open exposure:        ${exposure['open_exposure']:.2f}")
    print(f"    Worst-case loss:      ${exposure['worst_case_loss']:.2f}  (if all open trades lose)")
    dir_info = exposure.get("direction_breakdown", {})
    for d, info in dir_info.items():
        print(f"    {d} exposure:          ${info['exposure']:.2f}  ({info['count']} positions)")
    if exposure['open_exposure'] > 0 and dir_info:
        yes_exp = dir_info.get("YES", {}).get("exposure", 0)
        print(f"    YES ratio:            {yes_exp / exposure['open_exposure'] * 100:.0f}%")

    # --- P&L excluding top winners ---
    if trades:
        resolved = [t for t in trades if t.get("pnl") is not None]
        if resolved:
            pnls_sorted = sorted([t["pnl"] for t in resolved], reverse=True)
            total_pnl = sum(pnls_sorted)
            if len(pnls_sorted) >= 1:
                print(f"\n    P&L excl. top 1 winner:  ${total_pnl - pnls_sorted[0]:+.2f}")
            if len(pnls_sorted) >= 2:
                print(f"    P&L excl. top 2 winners: ${total_pnl - pnls_sorted[0] - pnls_sorted[1]:+.2f}")

    # --- Per-city P&L ---
    if city_pnl:
        print(f"\n  {'—' * 56}")
        print("  P&L BY CITY:")
        print(f"    {'City':<20s} {'Trades':>6s} {'W/L':>7s} {'P&L':>10s} {'Stake':>10s}")
        for c in city_pnl:
            print(
                f"    {c['city']:<20s} {c['trades']:>6d} "
                f"{c['wins']:>2d}/{c['losses']:<4d} "
                f"${c['total_pnl']:>+9.2f} ${c['total_stake']:>9.2f}"
            )

    # --- Calibration ---
    if calibration:
        print(f"\n  {'—' * 56}")
        print("  CALIBRATION (predicted vs actual win rate):")
        print(f"    {'Bucket':<12s} {'Count':>6s} {'Predicted':>10s} {'Actual':>10s} {'Delta':>8s}")
        for b in calibration:
            delta = b["actual_win_rate"] - b["predicted_avg"]
            print(
                f"    {b['bucket']:<12s} {b['count']:>6d} "
                f"{b['predicted_avg']:>9.1%} {b['actual_win_rate']:>9.1%} "
                f"{delta:>+7.1%}"
            )
        if brier is not None:
            print(f"\n    Brier score: {brier:.4f}  (0 = perfect, 0.25 = coin flip)")

    # --- Opportunities ---
    if all_signals:
        print(f"\n  {'—' * 56}")
        print("  ALL OPPORTUNITIES FOUND:")
        seen = set()
        for sig in all_signals:
            key = (sig.city, sig.question, sig.direction)
            if key in seen:
                continue
            seen.add(key)
            print(
                f"    {sig.direction:3s} {sig.city:<20s} "
                f"edge={sig.edge:+.2%}  bet=${sig.bet_size:.2f}  "
                f"prob={sig.noaa_probability:.3f} vs price={sig.market_price:.3f}"
            )
            print(f"        {sig.question[:80]}")

    # --- Individual trades ---
    if trades:
        print(f"\n  {'—' * 56}")
        print("  PAPER TRADES IN DATABASE:")
        for t in trades:
            status = t.get("outcome", "open") or "open"
            pnl_str = f"pnl=${t['pnl']:+.2f}" if t.get("pnl") is not None else "pnl=pending"
            print(
                f"    #{t['id']:<4d} {t.get('direction', '?'):3s} {t['city']:<20s} "
                f"edge={t.get('edge', 0):+.4f}  bet=${t.get('bet_size', 0):.2f}  "
                f"price={t.get('entry_price', 0):.3f}  [{status}] {pnl_str}"
            )
            print(f"          {t.get('market_question', '?')[:70]}")

    print(f"\n  DB PnL summary: {pnl_summary}")
    print(f"\n  Database saved: {DB_PATH}")
    print("=" * 65 + "\n")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
