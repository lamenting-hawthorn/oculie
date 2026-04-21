#!/usr/bin/env python3
"""
Paper Trading Simulation for the Polymarket Weather Prediction Agent.

Simulates multiple scan cycles with mock data to verify the paper trading
system works end-to-end without hitting real APIs (NOAA, Polymarket, etc.).

Usage:
    python scripts/simulate_paper.py
    python scripts/simulate_paper.py --keep-db
    uv run python scripts/simulate_paper.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the project root is on sys.path so `bot.*` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot.database import (
    get_pnl_summary,
    get_trade_history,
    init_db,
    insert_alert,
    insert_scan_log,
    insert_trade,
    set_setting,
    update_trade,
)
from bot.reporter import format_daily_summary, format_trade_entered, format_trade_resolved
from bot.trade_engine import calculate_edge, kelly_criterion, size_position

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_DB_PATH = str(PROJECT_ROOT / "data" / "simulation.db")
NUM_CYCLES = 10
BANKROLL = 200.0  # matches default max_total_exposure
MAX_BET = 50.0
ENTRY_THRESHOLD = 0.15

# City configs for mock data generation
CITY_CONFIGS = {
    "New York City": {"temp_range": (35, 85), "unit": "F", "bucket_size": 5},
    "Chicago":       {"temp_range": (35, 85), "unit": "F", "bucket_size": 5},
    "London":        {"temp_range": (5, 30),  "unit": "C", "bucket_size": 3},
}

# ANSI color codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_MAGENTA = "\033[95m"
C_CYAN = "\033[96m"
C_WHITE = "\033[97m"


# ---------------------------------------------------------------------------
# Mock data generators
# ---------------------------------------------------------------------------


def _random_forecast_temp(city: str) -> int:
    """Generate a realistic forecast temperature for the given city."""
    low, high = CITY_CONFIGS[city]["temp_range"]
    return random.randint(low, high)


def _generate_mock_markets(
    city: str, forecast_temp: int, num_markets: int = 3
) -> list[dict]:
    """
    Generate mock temperature markets for a city.

    Some markets are designed to have edge (forecast probability high, market
    price low) and some are not, to exercise both paths.
    """
    cfg = CITY_CONFIGS[city]
    bucket = cfg["bucket_size"]
    unit = cfg["unit"]
    markets = []

    for i in range(num_markets):
        # Create a temperature bucket around the forecast
        offset = random.choice([-2, -1, 0, 0, 1, 1, 2]) * bucket
        temp_low = forecast_temp + offset
        temp_high = temp_low + bucket

        # Simulate a "forecast probability" for this bucket
        # Buckets near the forecast temp get higher probability
        distance = abs(offset) / bucket
        if distance == 0:
            forecast_prob = random.uniform(0.35, 0.65)
        elif distance <= 1:
            forecast_prob = random.uniform(0.15, 0.40)
        else:
            forecast_prob = random.uniform(0.05, 0.20)

        # Market price: sometimes mispriced (creating edge), sometimes fair
        if random.random() < 0.4:
            # Mispriced -- market undervalues the probability
            market_price = max(0.10, forecast_prob - random.uniform(0.15, 0.35))
        else:
            # Fairly priced or overpriced
            market_price = min(0.60, forecast_prob + random.uniform(-0.05, 0.10))

        market_price = round(max(0.10, min(0.60, market_price)), 3)
        forecast_prob = round(forecast_prob, 4)

        target_date = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        question = (
            f"Will the high temperature in {city} be between "
            f"{temp_low}{unit} and {temp_high}{unit} on {target_date}?"
        )

        markets.append({
            "city": city,
            "question": question,
            "condition_id": f"sim-{uuid.uuid4().hex[:12]}",
            "token_id": f"tok-{uuid.uuid4().hex[:12]}",
            "temp_low": temp_low,
            "temp_high": temp_high,
            "unit": unit,
            "forecast_prob": forecast_prob,
            "market_price": market_price,
            "target_date": target_date,
        })

    return markets


# ---------------------------------------------------------------------------
# Simulation logic
# ---------------------------------------------------------------------------


async def run_simulation(keep_db: bool = False) -> None:
    """Run the full paper trading simulation."""

    # ------------------------------------------------------------------
    # Step 1: Initialize fresh database
    # ------------------------------------------------------------------
    if os.path.exists(SIM_DB_PATH):
        os.remove(SIM_DB_PATH)
        print(f"{C_DIM}Removed old simulation DB{C_RESET}")

    db = await init_db(SIM_DB_PATH)
    print(f"{C_GREEN}{C_BOLD}Database initialized at {SIM_DB_PATH}{C_RESET}")

    # ------------------------------------------------------------------
    # Step 2: Ensure paper_mode is on
    # ------------------------------------------------------------------
    await set_setting(db, "paper_mode", "true")
    await set_setting(db, "entry_threshold", str(ENTRY_THRESHOLD))
    await set_setting(db, "max_bet_size", str(MAX_BET))
    await set_setting(db, "max_total_exposure", str(BANKROLL))
    print(f"{C_CYAN}Paper mode enabled | threshold={ENTRY_THRESHOLD} | "
          f"max_bet=${MAX_BET} | bankroll=${BANKROLL}{C_RESET}\n")

    all_trade_ids: list[int] = []
    total_markets_scanned = 0
    total_opportunities = 0

    # ------------------------------------------------------------------
    # Step 3: Run simulated scan cycles
    # ------------------------------------------------------------------
    for cycle in range(1, NUM_CYCLES + 1):
        started_at = datetime.now(timezone.utc)
        cycle_trades = 0
        cycle_opportunities = 0
        cycle_markets = 0
        errors: list[str] = []

        print(f"{C_BOLD}{C_BLUE}{'=' * 65}{C_RESET}")
        print(f"{C_BOLD}{C_BLUE}  SCAN CYCLE {cycle}/{NUM_CYCLES}{C_RESET}")
        print(f"{C_BOLD}{C_BLUE}{'=' * 65}{C_RESET}")

        for city in CITY_CONFIGS:
            # (a) Generate random mock weather forecast
            forecast_temp = _random_forecast_temp(city)
            unit = CITY_CONFIGS[city]["unit"]
            print(f"  {C_CYAN}{city:<20s}{C_RESET} forecast: {C_BOLD}{forecast_temp}{unit}{C_RESET}")

            # (b) Generate random mock market prices
            markets = _generate_mock_markets(city, forecast_temp, num_markets=random.randint(2, 4))
            cycle_markets += len(markets)

            for mkt in markets:
                forecast_prob = mkt["forecast_prob"]
                market_price = mkt["market_price"]

                # (c) Calculate edge
                edge = calculate_edge(forecast_prob, market_price)

                edge_str = f"{edge:+.2%}"
                if edge >= ENTRY_THRESHOLD:
                    edge_color = C_GREEN
                else:
                    edge_color = C_DIM

                print(
                    f"    {mkt['temp_low']}-{mkt['temp_high']}{mkt['unit']}  "
                    f"prob={forecast_prob:.3f}  mkt={market_price:.3f}  "
                    f"edge={edge_color}{edge_str}{C_RESET}",
                    end="",
                )

                # (d) Check threshold
                if edge < ENTRY_THRESHOLD:
                    print(f"  {C_DIM}-- skip{C_RESET}")
                    continue

                cycle_opportunities += 1

                # Size with Kelly
                kf = kelly_criterion(forecast_prob, market_price)
                bet = size_position(kf, BANKROLL, MAX_BET)

                if bet <= 0:
                    print(f"  {C_YELLOW}-- kelly too small (kf={kf:.4f}){C_RESET}")
                    continue

                # (e) Insert paper trade into DB
                trade_id = await insert_trade(
                    db,
                    city=city,
                    market_question=mkt["question"],
                    condition_id=mkt["condition_id"],
                    token_id=mkt["token_id"],
                    direction="YES",
                    noaa_probability=forecast_prob,
                    market_price=market_price,
                    edge=round(edge, 4),
                    bet_size=bet,
                    entry_price=market_price,
                    paper_trade=True,
                )
                all_trade_ids.append(trade_id)
                cycle_trades += 1

                print(f"  {C_GREEN}{C_BOLD}TRADE #{trade_id}{C_RESET}"
                      f"  kelly={kf:.3f}  bet=${bet:.2f}")

                # (f) Format and print alert message
                trade_dict = {
                    "city": city,
                    "market_question": mkt["question"],
                    "noaa_probability": round(forecast_prob * 100, 1),
                    "market_price": round(market_price * 100, 1),
                    "edge": round(edge * 100, 1),
                    "bet_size": bet,
                }
                alert_msg = await format_trade_entered(trade_dict, paper=True)
                print(f"      {C_MAGENTA}{alert_msg}{C_RESET}")

                # Insert alert record
                await insert_alert(
                    db,
                    alert_type="paper_signal",
                    city=city,
                    message=alert_msg,
                    channel="simulation",
                )

        # (g) Insert scan log entry
        completed_at = datetime.now(timezone.utc)
        await insert_scan_log(
            db,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            cities_scanned=len(CITY_CONFIGS),
            markets_found=cycle_markets,
            opportunities_found=cycle_opportunities,
            trades_executed=cycle_trades,
            errors=json.dumps(errors) if errors else None,
        )

        total_markets_scanned += cycle_markets
        total_opportunities += cycle_opportunities

        duration_ms = (completed_at - started_at).total_seconds() * 1000
        print(
            f"\n  {C_DIM}Cycle {cycle} complete: "
            f"{cycle_markets} markets, {cycle_opportunities} opportunities, "
            f"{cycle_trades} trades, {duration_ms:.0f}ms{C_RESET}\n"
        )

    # ------------------------------------------------------------------
    # Step 4: Resolve trades randomly as won/lost
    # ------------------------------------------------------------------
    print(f"\n{C_BOLD}{C_YELLOW}{'=' * 65}{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}  RESOLVING TRADES{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}{'=' * 65}{C_RESET}\n")

    wins = 0
    losses = 0
    total_pnl = 0.0
    resolved_at = datetime.now(timezone.utc).isoformat()

    # Fetch all trades to get their details
    all_trades = await get_trade_history(db, limit=1000)
    trade_lookup = {t["id"]: t for t in all_trades}

    for trade_id in all_trade_ids:
        trade = trade_lookup.get(trade_id)
        if trade is None:
            continue

        entry_price = trade["entry_price"]
        bet_size = trade["bet_size"]
        city = trade["city"]
        question = trade["market_question"]

        # Randomly resolve: ~55% win rate for realistic simulation
        won = random.random() < 0.55

        if won:
            exit_price = 1.0
            pnl = round((1.0 - entry_price) * bet_size / entry_price, 2)
            outcome = "won"
            wins += 1
            icon = f"{C_GREEN}WIN {C_RESET}"
        else:
            exit_price = 0.0
            pnl = round(-bet_size, 2)
            outcome = "lost"
            losses += 1
            icon = f"{C_RED}LOSS{C_RESET}"

        total_pnl += pnl

        await update_trade(
            db,
            trade_id,
            exit_price=exit_price,
            outcome=outcome,
            pnl=pnl,
            resolved_at=resolved_at,
        )

        # Format and print resolved message
        resolved_dict = {
            "city": city,
            "market_question": question,
            "entry_price": entry_price,
            "pnl": pnl,
            "outcome": outcome,
        }
        resolved_msg = await format_trade_resolved(resolved_dict)
        pnl_color = C_GREEN if pnl >= 0 else C_RED
        print(
            f"  {icon} Trade #{trade_id:<4d} {city:<20s} "
            f"entry={entry_price:.3f}  "
            f"pnl={pnl_color}{pnl:+.2f}{C_RESET}  "
            f"{C_DIM}{question[:50]}...{C_RESET}"
        )

    # ------------------------------------------------------------------
    # Step 5: Print final summary
    # ------------------------------------------------------------------
    print(f"\n{C_BOLD}{C_WHITE}{'=' * 65}{C_RESET}")
    print(f"{C_BOLD}{C_WHITE}  SIMULATION RESULTS{C_RESET}")
    print(f"{C_BOLD}{C_WHITE}{'=' * 65}{C_RESET}\n")

    total_trades = len(all_trade_ids)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    pnl_color = C_GREEN if total_pnl >= 0 else C_RED

    print(f"  {C_BOLD}Scan cycles:       {C_RESET}{NUM_CYCLES}")
    print(f"  {C_BOLD}Markets scanned:   {C_RESET}{total_markets_scanned}")
    print(f"  {C_BOLD}Opportunities:     {C_RESET}{total_opportunities}")
    print(f"  {C_BOLD}Total trades:      {C_RESET}{total_trades}")
    print(f"  {C_BOLD}Wins:              {C_GREEN}{wins}{C_RESET}")
    print(f"  {C_BOLD}Losses:            {C_RED}{losses}{C_RESET}")
    print(f"  {C_BOLD}Win rate:          {C_RESET}{win_rate:.1f}%")
    print(f"  {C_BOLD}Total P&L:         {pnl_color}${total_pnl:+.2f}{C_RESET}")

    # DB-level PnL summary for cross-check
    db_summary = await get_pnl_summary(db)
    print(f"\n  {C_DIM}DB PnL summary: {db_summary}{C_RESET}")

    # Format and print daily summary message
    print(f"\n  {C_BOLD}Daily Summary Message:{C_RESET}")
    daily_msg = await format_daily_summary(db)
    print(f"  {C_CYAN}{daily_msg}{C_RESET}")

    # ------------------------------------------------------------------
    # Step 6: Clean up
    # ------------------------------------------------------------------
    await db.close()

    if keep_db:
        print(f"\n{C_GREEN}Database preserved at {SIM_DB_PATH}{C_RESET}")
        print(f"{C_DIM}Use for dashboard testing: uv run python -m bot.dashboard{C_RESET}")
    else:
        os.remove(SIM_DB_PATH)
        print(f"\n{C_DIM}Simulation database cleaned up.{C_RESET}")
        print(f"{C_DIM}Use --keep-db to preserve it for dashboard testing.{C_RESET}")

    print(f"\n{C_GREEN}{C_BOLD}Simulation complete.{C_RESET}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper trading simulation for the Polymarket Weather Prediction Agent."
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        default=False,
        help="Preserve the simulation database (data/simulation.db) for dashboard testing.",
    )
    args = parser.parse_args()
    asyncio.run(run_simulation(keep_db=args.keep_db))


if __name__ == "__main__":
    main()
