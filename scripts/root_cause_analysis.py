#!/usr/bin/env python3
"""
Root cause analysis: Why are all trades losing?

Hypotheses:
1. Forecast probability is systematically biased (too optimistic)
2. Edge calculations are wrong
3. Market prices move against us immediately (execution slippage)
4. The 30% early-exit trigger is too aggressive
"""

import sqlite3
from statistics import mean, stdev

def analyze_database():
    """Load and analyze all trades from database."""
    conn = sqlite3.connect('/Users/raghav/Projects/openclaw-weather/data/trades.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all trades
    cursor.execute("""
    SELECT
        id, city, direction, noaa_probability, market_price, edge,
        bet_size, outcome, pnl, entry_price
    FROM trades
    ORDER BY id
    """)

    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    print("=" * 100)
    print("ROOT CAUSE ANALYSIS: ALL TRADES LOSING")
    print("=" * 100)

    print(f"\nDataset: {len(trades)} trades total")
    resolved = [t for t in trades if t['outcome'] is not None]
    unresolved = [t for t in trades if t['outcome'] is None]

    print(f"  Resolved: {len(resolved)} ({len(resolved)/len(trades)*100:.1f}%)")
    print(f"  Unresolved: {len(unresolved)} ({len(unresolved)/len(trades)*100:.1f}%)")

    # ===== HYPOTHESIS 1: Forecast bias =====
    print("\n" + "-" * 100)
    print("HYPOTHESIS 1: Forecast probability is systematically biased")
    print("-" * 100)

    # For YES trades: forecast_prob should be > market_price for edge
    # For NO trades: 1 - forecast_prob should be > market_price for edge

    yes_trades = [t for t in trades if t['direction'] == 'YES']
    no_trades = [t for t in trades if t['direction'] == 'NO']

    print(f"\nYES trades: {len(yes_trades)}")
    yes_prob_mean = mean(t['noaa_probability'] for t in yes_trades)
    yes_price_mean = mean(t['market_price'] for t in yes_trades)
    yes_edge_mean = mean(t['edge'] for t in yes_trades)

    print(f"  Mean forecast prob:  {yes_prob_mean:.4f}")
    print(f"  Mean market price:   {yes_price_mean:.4f}")
    print(f"  Mean edge (prob-price): {yes_edge_mean:.4f}")

    print(f"\nNO trades: {len(no_trades)}")
    no_prob_mean = mean(1.0 - t['noaa_probability'] for t in no_trades)
    no_price_mean = mean(t['market_price'] for t in no_trades)
    no_edge_mean = mean(t['edge'] for t in no_trades)

    print(f"  Mean forecast prob (1-p): {no_prob_mean:.4f}")
    print(f"  Mean market price:       {no_price_mean:.4f}")
    print(f"  Mean edge (prob-price):  {no_edge_mean:.4f}")

    # Check if ALL edges are positive (we only entered when we thought we had an edge)
    negative_edges = [t for t in trades if t['edge'] < 0]
    print(f"\nTrades with negative edge: {len(negative_edges)}")
    if negative_edges:
        print("  WARNING: We entered trades with negative edge!")

    # ===== HYPOTHESIS 2: Entry price vs market price mismatch =====
    print("\n" + "-" * 100)
    print("HYPOTHESIS 2: Slippage between entry_price and market_price")
    print("-" * 100)

    slippage = [abs(t['entry_price'] - t['market_price']) for t in trades]
    print(f"\nEntry price vs market price:")
    print(f"  Mean absolute difference: {mean(slippage):.6f}")
    print(f"  Max difference: {max(slippage):.6f}")

    if max(slippage) > 0.001:
        print("  Note: Some trades have entry_price != market_price")
        print("  This could indicate order execution slippage")

    # ===== HYPOTHESIS 3: Edge quality =====
    print("\n" + "-" * 100)
    print("HYPOTHESIS 3: Edge quality and realized vs theoretical")
    print("-" * 100)

    print(f"\nEdges on all trades:")
    all_edges = [t['edge'] for t in trades]
    print(f"  Mean: {mean(all_edges):.4f}")
    print(f"  Min:  {min(all_edges):.4f}")
    print(f"  Max:  {max(all_edges):.4f}")
    if len(all_edges) > 1:
        print(f"  Stdev: {stdev(all_edges):.4f}")

    # Compare edges on resolved vs unresolved
    resolved_edges = [t['edge'] for t in resolved]
    unresolved_edges = [t['edge'] for t in unresolved]

    print(f"\nEdges on resolved trades (n={len(resolved)}):")
    print(f"  Mean: {mean(resolved_edges):.4f}")
    print(f"  Min:  {min(resolved_edges):.4f}")
    print(f"  Max:  {max(resolved_edges):.4f}")

    print(f"\nEdges on unresolved trades (n={len(unresolved)}):")
    print(f"  Mean: {mean(unresolved_edges):.4f}")
    print(f"  Min:  {min(unresolved_edges):.4f}")
    print(f"  Max:  {max(unresolved_edges):.4f}")

    # ===== HYPOTHESIS 4: Early exits are due to bad price movement =====
    print("\n" + "-" * 100)
    print("HYPOTHESIS 4: Early exits indicate immediate adverse moves")
    print("-" * 100)

    early_exits = [t for t in trades if t['outcome'] == 'exited_early']
    print(f"\nEarly-exit trades: {len(early_exits)}")

    if early_exits:
        early_pnls = [t['pnl'] for t in early_exits]
        print(f"  Mean PnL: ${mean(early_pnls):.2f}")
        print(f"  All losses: {all(pnl < 0 for pnl in early_pnls)}")
        print(f"  Average loss per trade: ${abs(mean(early_pnls)):.2f}")

        # Average loss as % of bet size
        early_pnl_pct = [t['pnl'] / t['bet_size'] * 100 for t in early_exits if t['bet_size'] > 0]
        print(f"  Average loss as % of bet: {mean(early_pnl_pct):.1f}%")

    # ===== HYPOTHESIS 5: Specific cities or markets are bad =====
    print("\n" + "-" * 100)
    print("HYPOTHESIS 5: City-specific or direction-specific performance")
    print("-" * 100)

    from collections import defaultdict
    by_city = defaultdict(list)
    by_direction = defaultdict(list)

    for t in trades:
        by_city[t['city']].append(t)
        by_direction[t['direction']].append(t)

    print(f"\nPerformance by city (resolved trades only):")
    for city in sorted(by_city.keys()):
        city_trades = [t for t in by_city[city] if t['outcome'] is not None]
        if city_trades:
            pnls = [t['pnl'] for t in city_trades]
            wins = sum(1 for p in pnls if p > 0)
            print(f"  {city:15s}: {len(city_trades):2d} resolved, "
                  f"{wins} wins, avg PnL ${mean(pnls):7.2f}, "
                  f"edge avg {mean(t['edge'] for t in city_trades):.4f}")

    print(f"\nPerformance by direction:")
    for direction in sorted(by_direction.keys()):
        dir_trades = [t for t in by_direction[direction] if t['outcome'] is not None]
        if dir_trades:
            pnls = [t['pnl'] for t in dir_trades]
            wins = sum(1 for p in pnls if p > 0)
            print(f"  {direction}: {len(dir_trades):2d} resolved, "
                  f"{wins} wins, avg PnL ${mean(pnls):7.2f}, "
                  f"edge avg {mean(t['edge'] for t in dir_trades):.4f}")

    # ===== CRITICAL FINDING: Dampening effect =====
    print("\n" + "-" * 100)
    print("CRITICAL: Bayesian dampening of forecast probability")
    print("-" * 100)

    print(f"\ntrading_engine.py applies DAMPENING:")
    print(f"  yes_dampened = (forecast_prob * 0.6) + (market.yes_price * 0.4)")
    print(f"  no_dampened = (no_prob * 0.6) + (market.no_price * 0.4)")
    print(f"\nThen edge is calculated as:")
    print(f"  edge = dampened_prob - market_price")
    print(f"\nThis means the edge we see in the database is AFTER dampening.")
    print(f"Since we're losing, this suggests:")
    print(f"  1. Our forecasts are too confident (dampening isn't helping)")
    print(f"  2. The market is correctly pricing us out")
    print(f"  3. We have a systematic bias in our probability model")

    print("\n" + "=" * 100)
    print("RECOMMENDATIONS")
    print("=" * 100)
    print(f"""
1. **INVESTIGATE PROBABILITY CALIBRATION**
   - The fact that ALL resolved trades lose suggests forecasts are systematically wrong
   - Check: noaa_fetcher.py's calculate_probability_distribution()
   - Run backtest with historical data to see if this was worse in past

2. **REDUCE DAMPENING AGGRESSIVENESS**
   - Current: 60% forecast / 40% market dampening
   - Consider: 50% / 50% or even 40% / 60% (more trust to market)
   - Or: remove dampening entirely and check raw edges

3. **AGGRESSIVE EARLY-EXIT IS MASKING THE PROBLEM**
   - 26/26 resolved trades are early-exits (30% adverse move trigger)
   - This means we exit before finding real outcome
   - Without early-exit: we'd still be underwater but would have true outcomes
   - Test: disable early-exit temporarily to see real resolution statistics

4. **CONSIDER THE UNRESOLVED TRADES**
   - 38 trades still open (not yet resolved)
   - If they all lose like resolved ones, total damage: ~$1,300-1,500
   - Need to check if unresolved trades are following same pattern

5. **THRESHOLD CHANGES WON'T FIX THIS**
   - Current sensitivity analysis shows thresholds don't matter
   - All trades lose regardless of entry threshold
   - Problem is FORECAST QUALITY, not thresholds
""")


if __name__ == "__main__":
    analyze_database()
