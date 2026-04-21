#!/usr/bin/env python3
"""
Parameter sensitivity analysis for the weather bot.

Analyzes which threshold and sigma combinations would have produced
the best win rate on the 64 trades from the last session.
"""

import csv
import json
from dataclasses import dataclass
from typing import NamedTuple


@dataclass
class Trade:
    """A single executed trade with all its details."""
    trade_id: int
    city: str
    direction: str
    entry_price: float
    market_price: float
    noaa_probability: float
    edge: float
    bet_size: float
    outcome: str  # None, "exited_early", "WIN", "LOSS"
    pnl: float | None
    resolved: bool
    winner: bool  # True if trade was profitable


class ThresholdParams(NamedTuple):
    """Entry threshold parameters."""
    yes_threshold: float
    no_threshold: float


class SigmaParams(NamedTuple):
    """Sigma (forecast dampening) parameters."""
    sigma_forecast: float  # Multiplier for forecast weight (currently fixed at 0.6)
    sigma_market: float    # Multiplier for market weight (currently fixed at 0.4)


def load_trades() -> list[Trade]:
    """Load all 64 trades from the database via CSV export."""
    trades = []
    # This is the CSV data extracted from the database
    csv_data = """id,city,direction,entry_price,market_price,noaa_probability,edge,bet_size,outcome,pnl,resolved,winner
1,hong_kong,NO,0.75,0.75,1.0,0.15,50.0,,,0,0
2,hong_kong,NO,0.7,0.7,1.0,0.18,50.0,,,0,0
3,hong_kong,NO,0.68,0.68,1.0,0.192,50.0,,,0,0
4,seattle,YES,0.0005,0.0005,0.36674,0.219744,50.0,,,0,0
5,seattle,YES,0.09,0.09,0.36674,0.166044,50.0,exited_early,-49.444444,1,0
6,seattle,NO,0.335,0.335,0.63326,0.178956,50.0,exited_early,-69.402985,1,0
7,seattle,YES,0.0335,0.0335,0.36674,0.199944,50.0,exited_early,-45.522388,1,0
8,seattle,YES,0.0695,0.0695,0.419147,0.2097882,50.0,,,0,0
9,seattle,YES,0.0135,0.0135,0.214772,0.1207632,40.81,,,0,0
10,seattle,YES,0.004,0.004,0.214772,0.1264632,42.32,exited_early,-21.16,1,0
11,seattle,YES,0.003,0.003,0.214772,0.1270632,42.48,exited_early,-14.16,1,0
12,seattle,NO,0.595,0.595,0.752493,0.0944958,50.0,,,0,0
13,seattle,YES,0.0365,0.0365,0.499967,0.2780802,50.0,,,0,0
14,seattle,YES,0.425,0.425,0.655413,0.1382478,50.0,,,0,0
15,new_york,YES,0.0005,0.0005,0.268454,0.1607724,50.0,,,0,0
16,new_york,YES,0.0005,0.0005,0.268454,0.1607724,50.0,,,0,0
17,new_york,NO,0.023,0.023,0.731546,0.4251276,50.0,,,0,0
18,new_york,YES,0.0215,0.0215,0.419147,0.2385882,50.0,exited_early,-45.348837,1,0
19,new_york,YES,0.005,0.005,0.419147,0.2484882,50.0,exited_early,-40.0,1,0
20,new_york,YES,0.001,0.001,0.214772,0.1282632,42.8,,,0,0
21,new_york,YES,0.0025,0.0025,0.214772,0.1273632,42.56,exited_early,-25.536,1,0
22,new_york,YES,0.003,0.003,0.214772,0.1270632,42.48,exited_early,-28.32,1,0
23,new_york,YES,0.155,0.155,0.400898,0.1475388,50.0,,,0,0
24,new_york,YES,0.0495,0.0495,0.320859,0.1628154,50.0,exited_early,-30.808081,1,0
25,new_york,YES,0.0145,0.0145,0.320859,0.1838154,50.0,,,0,0
26,new_york,YES,0.0305,0.0305,0.247507,0.1302042,44.77,,,0,0
27,new_york,YES,0.012,0.012,0.25246,0.144276,48.68,,,0,0
28,new_york,YES,0.0175,0.0175,0.394854,0.2264124,50.0,exited_early,-38.571429,1,0
29,dallas,YES,0.0005,0.0005,0.36674,0.219744,50.0,,,0,0
30,dallas,NO,0.335,0.335,0.63326,0.178956,50.0,,,0,0
31,dallas,YES,0.0105,0.0105,0.36674,0.213744,50.0,exited_early,-45.238095,1,0
32,dallas,YES,0.0055,0.0055,0.36674,0.216744,50.0,exited_early,-40.909091,1,0
33,dallas,YES,0.011,0.011,0.214772,0.1222632,41.21,,,0,0
34,dallas,YES,0.21,0.21,0.419147,0.1254882,50.0,,,0,0
35,dallas,YES,0.051,0.051,0.268454,0.1304724,45.83,,,0,0
36,dallas,YES,0.0155,0.0155,0.268454,0.1517724,50.0,,,0,0
37,dallas,YES,0.0155,0.0155,0.219539,0.1224234,41.45,exited_early,-14.708065,1,0
38,atlanta,NO,0.046,0.046,0.599102,0.3318612,50.0,exited_early,-76.086957,1,0
39,atlanta,YES,0.03,0.03,0.400898,0.2225388,50.0,exited_early,-45.0,1,0
40,atlanta,YES,0.007,0.007,0.320859,0.1883154,50.0,exited_early,-42.857143,1,0
41,atlanta,YES,0.004,0.004,0.320859,0.1901154,50.0,exited_early,-37.5,1,0
42,atlanta,YES,0.0015,0.0015,0.320859,0.1916154,50.0,exited_early,-16.666667,1,0
43,atlanta,YES,0.0295,0.0295,0.36674,0.202344,50.0,exited_early,-16.101695,1,0
44,atlanta,YES,0.115,0.115,0.36674,0.151044,50.0,,,0,0
45,atlanta,YES,0.0265,0.0265,0.394854,0.2210124,50.0,,,0,0
46,atlanta,YES,0.009,0.009,0.211839,0.1217034,40.94,exited_early,-13.646667,1,0
47,miami,YES,0.0005,0.0005,0.320859,0.1922154,50.0,,,0,0
48,miami,NO,0.01,0.01,0.679141,0.4014846,50.0,,,0,0
49,miami,YES,0.032,0.032,0.25246,0.132276,45.55,exited_early,-15.657812,1,0
50,miami,YES,0.075,0.075,0.419147,0.2064882,50.0,,,0,0
51,miami,YES,0.0245,0.0245,0.268454,0.1463724,50.0,,,0,0
52,miami,NO,0.59,0.59,0.780461,0.1142766,50.0,,,0,0
53,miami,NO,0.605,0.605,0.739441,0.0806646,50.0,,,0,0
54,miami,YES,0.042,0.042,0.344573,0.1815438,50.0,exited_early,-26.190476,1,0
55,chicago,YES,0.0005,0.0005,0.419147,0.2511882,50.0,,,0,0
56,chicago,NO,0.07,0.07,0.785228,0.4291368,50.0,,,0,0
57,chicago,YES,0.006,0.006,0.214772,0.1252632,42.01,exited_early,-35.008333,1,0
58,chicago,YES,0.0045,0.0045,0.214772,0.1261632,42.24,exited_early,-14.08,1,0
59,chicago,YES,0.2,0.2,0.419147,0.1314882,50.0,,,0,0
60,chicago,YES,0.0375,0.0375,0.268454,0.1385724,47.99,,,0,0
61,seoul,NO,0.86,0.86,1.0,0.084,50.0,,,0,0
62,shanghai,YES,0.0225,0.0225,1.0,0.5865,50.0,,,0,0
63,shanghai,NO,0.829,0.829,1.0,0.1026,50.0,,,0,0
64,shanghai,NO,0.255,0.255,1.0,0.447,50.0,exited_early,-40.196078,1,0"""

    reader = csv.DictReader(csv_data.strip().split('\n'))
    for row in reader:
        # Parse fields carefully
        pnl_str = row['pnl'].strip()
        outcome_str = row['outcome'].strip()

        trade = Trade(
            trade_id=int(row['id']),
            city=row['city'],
            direction=row['direction'],
            entry_price=float(row['entry_price']),
            market_price=float(row['market_price']),
            noaa_probability=float(row['noaa_probability']),
            edge=float(row['edge']),
            bet_size=float(row['bet_size']),
            outcome=outcome_str if outcome_str else None,
            pnl=float(pnl_str) if pnl_str else None,
            resolved=int(row['resolved']) == 1,
            winner=int(row['winner']) == 1,
        )
        trades.append(trade)

    return trades


def would_trade_be_entered(
    trade: Trade,
    threshold_params: ThresholdParams,
) -> bool:
    """
    Determine if a trade would have been entered under the given threshold parameters.

    Uses the same logic as trade_engine.py:
    - YES side needs edge >= entry_threshold_yes
    - NO side needs edge >= entry_threshold_no
    - Pick the side with better edge (if both qualify, YES wins ties)
    """
    yes_threshold, no_threshold = threshold_params.yes_threshold, threshold_params.no_threshold

    # The trade was already entered with its recorded edge and direction
    # We check if this edge would still meet the threshold under new params
    if trade.direction == "YES":
        return trade.edge >= yes_threshold
    else:  # NO
        return trade.edge >= no_threshold


def evaluate_parameters(
    trades: list[Trade],
    threshold_params: ThresholdParams,
) -> dict:
    """
    Evaluate how many trades would be entered and their win rate
    under a given parameter set.

    Returns:
    {
        'params': (yes_threshold, no_threshold),
        'trades_entered': int,
        'trades_with_outcome': int,
        'wins': int,
        'losses': int,
        'win_rate': float,
        'avg_pnl_per_trade': float,
        'total_pnl': float,
        'avg_bet_size': float,
    }
    """
    entered_trades = [t for t in trades if would_trade_be_entered(t, threshold_params)]

    if not entered_trades:
        return {
            'params': threshold_params,
            'trades_entered': 0,
            'trades_with_outcome': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': None,
            'avg_pnl_per_trade': None,
            'total_pnl': 0.0,
            'avg_bet_size': 0.0,
            'total_exposure': 0.0,
        }

    # Among entered trades, count how many have outcomes (were resolved)
    resolved_trades = [t for t in entered_trades if t.resolved and t.pnl is not None]

    if resolved_trades:
        wins = sum(1 for t in resolved_trades if t.winner)
        losses = len(resolved_trades) - wins
        win_rate = wins / len(resolved_trades)
        total_pnl = sum(t.pnl for t in resolved_trades)
        avg_pnl = total_pnl / len(resolved_trades)
    else:
        wins = 0
        losses = 0
        win_rate = None
        total_pnl = 0.0
        avg_pnl = None

    avg_bet_size = sum(t.bet_size for t in entered_trades) / len(entered_trades)
    total_exposure = sum(t.bet_size for t in entered_trades)

    return {
        'params': threshold_params,
        'trades_entered': len(entered_trades),
        'trades_with_outcome': len(resolved_trades),
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'avg_pnl_per_trade': avg_pnl,
        'total_pnl': total_pnl,
        'avg_bet_size': avg_bet_size,
        'total_exposure': total_exposure,
    }


def main():
    """Run sensitivity analysis on threshold parameters."""
    trades = load_trades()

    print("=" * 90)
    print("PARAMETER SENSITIVITY ANALYSIS - WEATHER BOT")
    print("=" * 90)
    print(f"\nDataset: {len(trades)} trades from recent session")

    resolved = sum(1 for t in trades if t.resolved)
    winners = sum(1 for t in trades if t.winner)
    print(f"Resolved trades: {resolved} ({resolved/len(trades)*100:.1f}%)")
    if resolved > 0:
        print(f"Current win rate (all trades): {winners}/{resolved} = {winners/resolved*100:.1f}%")

    # Current parameters
    current_yes = 0.12
    current_no = 0.08

    print(f"\n--- CURRENT PARAMETERS ---")
    print(f"entry_threshold_YES:  {current_yes}")
    print(f"entry_threshold_NO:   {current_no}")

    # Define parameter combinations to test
    threshold_combos = [
        # Current (baseline)
        ThresholdParams(0.12, 0.08),

        # Lower thresholds (more trades)
        ThresholdParams(0.06, 0.04),
        ThresholdParams(0.08, 0.05),
        ThresholdParams(0.10, 0.06),

        # Higher thresholds (fewer trades, higher quality)
        ThresholdParams(0.15, 0.10),
        ThresholdParams(0.18, 0.12),
        ThresholdParams(0.20, 0.14),

        # Equal thresholds
        ThresholdParams(0.08, 0.08),
        ThresholdParams(0.10, 0.10),
        ThresholdParams(0.12, 0.12),

        # Asymmetric variants
        ThresholdParams(0.14, 0.08),
        ThresholdParams(0.10, 0.07),
        ThresholdParams(0.15, 0.09),
    ]

    results = []
    for params in threshold_combos:
        result = evaluate_parameters(trades, params)
        results.append(result)

    # Sort by win rate (descending), then by trades entered (descending)
    results_sorted = sorted(
        results,
        key=lambda r: (
            r['win_rate'] if r['win_rate'] is not None else -1,
            r['trades_entered'],
        ),
        reverse=True,
    )

    print("\n" + "=" * 90)
    print("RESULTS TABLE (sorted by win rate)")
    print("=" * 90)
    print(f"\n{'YES_THR':>8} {'NO_THR':>8} {'ENTERED':>8} {'RESOLVED':>9} {'WINS':>5} {'WIN_RATE':>10} {'AVG_PNL':>10} {'EXPOSURE':>10}")
    print("-" * 90)

    for result in results_sorted:
        yes_thr, no_thr = result['params']
        entered = result['trades_entered']
        resolved = result['trades_with_outcome']
        wins = result['wins']
        win_rate = result['win_rate']
        avg_pnl = result['avg_pnl_per_trade']
        exposure = result['total_exposure']

        win_rate_str = f"{win_rate*100:.1f}%" if win_rate is not None else "N/A"
        avg_pnl_str = f"${avg_pnl:.2f}" if avg_pnl is not None else "N/A"

        marker = " <-- CURRENT" if (yes_thr == current_yes and no_thr == current_no) else ""

        print(f"  {yes_thr:>6.2f}   {no_thr:>6.2f}     {entered:>6}      {resolved:>7}    {wins:>3}    {win_rate_str:>8}    {avg_pnl_str:>10}   ${exposure:>9.0f}{marker}")

    print("\n" + "=" * 90)
    print("ANALYSIS & RECOMMENDATIONS")
    print("=" * 90)

    # Find best overall by win rate
    best_by_wr = [r for r in results_sorted if r['win_rate'] is not None]
    if best_by_wr:
        best = best_by_wr[0]
        yes_thr, no_thr = best['params']
        print(f"\nBest by WIN RATE:")
        print(f"  Parameters:     YES={yes_thr}, NO={no_thr}")
        print(f"  Trades entered: {best['trades_entered']}")
        print(f"  Resolved:       {best['trades_with_outcome']}")
        print(f"  Win rate:       {best['win_rate']*100:.1f}% ({best['wins']}/{best['trades_with_outcome']})")
        print(f"  Avg PNL/trade:  ${best['avg_pnl_per_trade']:.2f}")
        print(f"  Total exposure: ${best['total_exposure']:.0f}")

    # Analyze trade-off: volume vs accuracy
    print(f"\n--- VOLUME VS ACCURACY TRADE-OFF ---")

    # Group by resolved trade count to see the distribution
    by_volume = {}
    for result in results:
        resolved = result['trades_with_outcome']
        if resolved not in by_volume:
            by_volume[resolved] = []
        by_volume[resolved].append(result)

    for resolved_count in sorted(by_volume.keys(), reverse=True):
        group = by_volume[resolved_count]
        if resolved_count > 0:
            best_wr = max(g['win_rate'] for g in group if g['win_rate'] is not None)
            print(f"  {resolved_count:2d} resolved trades: best win rate = {best_wr*100:.1f}%")

    # Statistical insights
    print(f"\n--- KEY INSIGHTS ---")

    all_resolved = sum(1 for t in trades if t.resolved)
    all_wins = sum(1 for t in trades if t.winner)

    print(f"\n1. CURRENT STATE (all {len(trades)} trades):")
    print(f"   - {all_resolved} trades resolved ({all_resolved/len(trades)*100:.1f}%)")
    if all_resolved > 0:
        current_wr = all_wins / all_resolved
        print(f"   - {all_wins} wins, win rate = {current_wr*100:.1f}%")
        print(f"   - This is BELOW expected 50% (neutral) - indicates systematic bias or bad thresholds")

    print(f"\n2. THRESHOLD EFFECT:")
    # Compare current vs best
    current_result = next(r for r in results if r['params'].yes_threshold == current_yes and r['params'].no_threshold == current_no)
    best_result = results_sorted[0] if results_sorted and results_sorted[0]['win_rate'] is not None else None

    if best_result and current_result['win_rate'] is not None:
        improvement = (best_result['win_rate'] - current_result['win_rate']) * 100
        volume_change = ((best_result['trades_entered'] - current_result['trades_entered']) / current_result['trades_entered'] * 100) if current_result['trades_entered'] > 0 else 0

        if improvement != 0:
            print(f"   - Recommended params improve win rate by {improvement:+.1f}pp")
        print(f"   - Trade volume would change by {volume_change:+.1f}%")

    print(f"\n3. CONFIDENCE ASSESSMENT:")

    # Count trades that would be entered under best params
    if best_result:
        entered_under_best = best_result['trades_entered']
        resolved_under_best = best_result['trades_with_outcome']

        if resolved_under_best >= 5:
            print(f"   - {resolved_under_best} resolved trades is a reasonable sample size for win rate estimation")
            print(f"   - Confidence: MODERATE (n={resolved_under_best})")
        elif resolved_under_best >= 2:
            print(f"   - {resolved_under_best} resolved trades is too small for statistical significance")
            print(f"   - Confidence: LOW (n={resolved_under_best})")
        else:
            print(f"   - No resolved trades under best parameters")
            print(f"   - Confidence: VERY LOW")

    print(f"\n4. RECOMMENDED NEXT STEPS:")
    print(f"   a) Validate recommended parameters on a new session (larger sample)")
    print(f"   b) Monitor early-exit trades - they may signal edge estimation bias")
    print(f"   c) Consider adjusting dampening (currently 60% forecast / 40% market)")
    print(f"   d) Check if specific cities have biased forecasts (NOAA vs Open-Meteo)")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    main()
