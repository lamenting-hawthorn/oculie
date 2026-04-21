"""
End-to-end lifecycle tests for the Polymarket Weather Prediction Agent.

Simulates the full trade lifecycle: signal detection -> sizing -> paper execution
-> resolution, plus risk-limit and account-floor guard rails.
"""

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bot.database import (
    get_open_positions,
    get_pnl_summary,
    get_setting,
    get_trade_history,
    init_db,
    insert_trade,
    set_setting,
    update_trade,
)
from bot.reporter import format_trade_entered, format_trade_resolved
from bot.trade_engine import (
    TradeSignal,
    calculate_edge,
    check_risk_limits,
    execute_trade,
    kelly_criterion,
    size_position,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Provide a fresh temporary database for each test, cleaned up afterwards."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_e2e_")
    os.close(fd)
    try:
        conn = await init_db(path)
        yield conn
        await conn.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# Full trade lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_trade_lifecycle(db):
    """
    Simulate: forecast says 75% -> market says 20% -> detect edge -> size
    -> execute paper trade -> resolve as won.
    """

    # Step 1: DB is already initialised via the fixture

    # Step 2: Ensure paper_mode is enabled
    paper_mode = await get_setting(db, "paper_mode")
    assert paper_mode == "true"

    # Step 3: Calculate edge
    forecast_prob = 0.75
    market_price = 0.20
    edge = calculate_edge(forecast_prob, market_price)
    assert edge == pytest.approx(0.55)

    # Step 4: Kelly fraction
    kf = kelly_criterion(forecast_prob, market_price)
    assert kf > 0, "Kelly fraction should be positive with this edge"
    assert kf <= 0.25, "Kelly fraction should respect the default 25% cap"

    # Step 5: Size the position
    bet = size_position(kf, bankroll=1000, max_bet=50)
    assert bet > 0, "Bet should be non-zero with a strong edge"
    assert bet <= 50, "Bet should respect max_bet cap"

    # Step 6: Risk limits should pass on a fresh DB
    ok, reason = await check_risk_limits(db, bet)
    assert ok is True, f"Risk limits should pass on fresh DB, got: {reason}"
    assert reason == "ok"

    # Step 7: Build a TradeSignal
    signal = TradeSignal(
        city="Miami",
        condition_id="cond-abc-123",
        token_id="tok-xyz-789",
        question="Will Miami high be 85-90F tomorrow?",
        direction="YES",
        noaa_probability=forecast_prob,
        market_price=market_price,
        edge=edge,
        kelly_fraction=kf,
        bet_size=bet,
        paper=True,
    )

    # Step 8: Execute the paper trade (mock get_db to return our temp DB)
    with patch("bot.trade_engine.get_db", new_callable=AsyncMock, return_value=db):
        result = await execute_trade(signal)

    assert result.success is True
    assert result.order_id is not None
    assert result.order_id.startswith("paper-")

    # Step 9 & 10: Verify trade appears in the database
    history = await get_trade_history(db)
    assert len(history) == 1, f"Expected 1 trade in history, got {len(history)}"

    trade_row = history[0]
    assert trade_row["city"] == "Miami"
    assert trade_row["direction"] == "YES"
    assert trade_row["edge"] == pytest.approx(0.55)
    assert trade_row["market_price"] == pytest.approx(0.20)
    assert trade_row["bet_size"] == pytest.approx(bet)

    # Step 11: Verify paper_trade flag
    assert trade_row["paper_trade"] == 1  # SQLite stores booleans as 0/1

    # Step 12: Format the trade-entered message and verify contents
    entered_msg = await format_trade_entered(trade_row, paper=True)
    assert "Miami" in entered_msg
    assert str(trade_row["edge"]) in entered_msg or "Edge" in entered_msg

    # Step 13: Simulate resolution — mark as won
    trade_id = trade_row["id"]
    pnl = bet * (1.0 - market_price)  # profit on a winning YES trade
    await update_trade(
        db,
        trade_id,
        outcome="won",
        exit_price=1.0,
        pnl=pnl,
        resolved_at=datetime.now(timezone.utc).isoformat(),
    )

    # Step 14: Verify PnL summary
    summary = await get_pnl_summary(db)
    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] == pytest.approx(pnl)

    # Step 15: Format the resolved message and verify it shows Won
    resolved_trade = (await get_trade_history(db))[0]
    resolved_msg = await format_trade_resolved(resolved_trade)
    assert "Won" in resolved_msg


# ---------------------------------------------------------------------------
# Risk limits block over-leveraged trade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_limits_block_overleveraged_trade(db):
    """
    Verify that risk controls block a trade when exposure would exceed limits.
    """

    # Step 1: DB is already initialised via the fixture

    # Step 2: Set max_total_exposure to a low value
    await set_setting(db, "max_total_exposure", "50")
    # Also set account_floor low so it doesn't interfere
    await set_setting(db, "account_floor", "0")

    # Step 3: Insert a mock open position with size=45
    await db.execute(
        "INSERT INTO positions (city, market_question, direction, entry_price, size) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Chicago", "Will Chicago high be 70-75F?", "YES", 0.30, 45),
    )
    await db.commit()

    # Step 4: Attempt risk check with bet_size=10 — should fail (45 + 10 = 55 > 50)
    ok, reason = await check_risk_limits(db, bet_size=10)

    # Step 5: Verify it returns failure with a reason
    assert ok is False
    assert "exposure" in reason.lower() or "exceed" in reason.lower()


# ---------------------------------------------------------------------------
# Account floor halts trading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_floor_halts_trading(db):
    """
    Verify trading halts when balance would drop below account floor.

    The check_risk_limits function estimates balance as:
        estimated_balance = max_total_exposure - current_exposure
    and rejects if estimated_balance - bet_size < account_floor.
    """

    # Configure tight limits: exposure cap = 200, floor = 100
    await set_setting(db, "max_total_exposure", "200")
    await set_setting(db, "account_floor", "100")
    await set_setting(db, "max_bet_size", "200")  # don't block on individual size

    # Insert existing exposure of 150, leaving estimated balance = 200 - 150 = 50
    await db.execute(
        "INSERT INTO positions (city, market_question, direction, entry_price, size) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Seattle", "Will Seattle high be 55-60F?", "YES", 0.40, 150),
    )
    await db.commit()

    # A bet of 10 would leave estimated balance at 50 - 10 = 40, below floor of 100
    ok, reason = await check_risk_limits(db, bet_size=10)
    assert ok is False
    assert "floor" in reason.lower()

    # A bet of 0 (degenerate) should still fail if balance is already under floor
    # estimated_balance = 200 - 150 = 50, 50 - 0 = 50 < 100
    ok_zero, reason_zero = await check_risk_limits(db, bet_size=0)
    assert ok_zero is False
    assert "floor" in reason_zero.lower()
