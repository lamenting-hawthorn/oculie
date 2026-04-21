"""
Resolver module for the Polymarket Weather Prediction Agent.

Polls the Polymarket Gamma API to check whether open trade positions have
resolved, calculates P&L, closes positions in the database, and dispatches
resolution alerts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx
from pydantic import BaseModel

from bot.config import CITY_CONFIGS
from bot.database import get_db, get_open_positions, get_setting, update_trade
from bot.market_scanner import parse_market_question, parse_submarket_question
from bot.reporter import format_trade_resolved, send_alert

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
DEFAULT_EXIT_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MarketResolution(BaseModel):
    condition_id: str
    resolved: bool
    outcome: str | None = None
    resolution_price: float | None = None
    not_found: bool = False
    source: str = "gamma"


# ---------------------------------------------------------------------------
# Gamma API helper
# ---------------------------------------------------------------------------


async def check_resolution(condition_id: str) -> MarketResolution:
    """
    Fetch resolution status for a single market from the Gamma API.

    Returns a MarketResolution indicating whether the market has resolved
    and, if so, the outcome and resolution price.  On any API error the
    market is treated as still open so that no position is accidentally
    closed.
    """
    url = f"{GAMMA_BASE_URL}/markets/{condition_id}"
    backoff = INITIAL_BACKOFF

    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "Checking resolution for %s (attempt %d/%d)",
                    condition_id,
                    attempt,
                    MAX_RETRIES,
                )
                resp = await client.get(url)

                # 404 means the condition_id does not exist on Polymarket
                # (common for paper-trade mock IDs) — log and return unresolved
                if resp.status_code == 404:
                    logger.info(
                        "condition_id %s returned 404 — likely a paper-trade mock; skipping",
                        condition_id,
                    )
                    return MarketResolution(
                        condition_id=condition_id,
                        resolved=False,
                        not_found=True,
                    )

                resp.raise_for_status()
                data: dict = resp.json()

                resolved: bool = bool(data.get("resolved", False))
                outcome: str | None = data.get("outcome")  # "Yes", "No", or None

                resolution_price: float | None = None
                if resolved and outcome is not None:
                    if outcome.lower() == "yes":
                        resolution_price = 1.0
                    elif outcome.lower() == "no":
                        resolution_price = 0.0

                return MarketResolution(
                    condition_id=condition_id,
                    resolved=resolved,
                    outcome=outcome,
                    resolution_price=resolution_price,
                )

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP error checking resolution for %s (attempt %d/%d): %s",
                    condition_id,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "Request error checking resolution for %s (attempt %d/%d): %s",
                    condition_id,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
            except Exception:
                logger.exception(
                    "Unexpected error checking resolution for %s (attempt %d/%d)",
                    condition_id,
                    attempt,
                    MAX_RETRIES,
                )

            if attempt < MAX_RETRIES:
                logger.info("Retrying in %.1f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff *= 2

    # All retries exhausted — treat as unresolved to avoid data loss
    logger.error(
        "All %d resolution-check attempts failed for %s; treating as unresolved",
        MAX_RETRIES,
        condition_id,
    )
    return MarketResolution(condition_id=condition_id, resolved=False)


# ---------------------------------------------------------------------------
# CLOB current-price helper
# ---------------------------------------------------------------------------


def _display_city_name(city: str) -> str | None:
    """Return the configured display city name for an internal or display key."""
    if city in CITY_CONFIGS:
        return city

    normalized = city.lower().replace(" ", "_")
    for display in CITY_CONFIGS:
        if display.lower().replace(" ", "_") == normalized:
            return display

    aliases = {
        "new_york": "New York City",
        "nyc": "New York City",
        "hong_kong": "Hong Kong",
    }
    return aliases.get(normalized)


def _extract_position_market(position: dict) -> tuple[str | None, float | None, float | None, str | None]:
    """Extract target date and temperature bounds from stored columns or question text."""
    target_date = position.get("target_date")
    temp_low = position.get("temp_low")
    temp_high = position.get("temp_high")
    temp_unit = position.get("temp_unit")

    if target_date and temp_low is not None and temp_high is not None and temp_unit:
        return str(target_date), float(temp_low), float(temp_high), str(temp_unit).upper()

    question = position.get("market_question", "")
    parsed = parse_market_question(question)
    if parsed is not None:
        return (
            parsed.get("target_date"),
            float(parsed["temp_low"]),
            float(parsed["temp_high"]),
            str(parsed["temp_unit"]).upper(),
        )

    sub = parse_submarket_question(question)
    if sub is not None and target_date:
        return (
            str(target_date),
            float(sub["temp_low"]),
            float(sub["temp_high"]),
            str(sub["temp_unit"]).upper(),
        )

    return (
        str(target_date) if target_date else None,
        float(temp_low) if temp_low is not None else None,
        float(temp_high) if temp_high is not None else None,
        str(temp_unit).upper() if temp_unit else None,
    )


def _actual_temp_resolves_yes(actual_high: float, temp_low: float, temp_high: float) -> bool:
    """Return whether an actual high temperature satisfies the market bucket."""
    rounded_high = round(actual_high)
    if temp_low <= -900:
        return rounded_high <= round(temp_high)
    if temp_high >= 900:
        return rounded_high >= round(temp_low)
    if temp_low == temp_high:
        return rounded_high == round(temp_low)
    return round(temp_low) <= rounded_high <= round(temp_high)


async def fetch_actual_high_temperature(city: str, target_date: str) -> float | None:
    """Fetch observed daily high from Open-Meteo archive for local paper resolution."""
    display_city = _display_city_name(city)
    if display_city is None:
        logger.warning("No city config for local resolution city=%s", city)
        return None

    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("Invalid target_date for local resolution: %s", target_date)
        return None

    if target >= date.today():
        logger.debug(
            "Target date %s has not fully passed; skipping local resolution",
            target_date,
        )
        return None

    cfg = CITY_CONFIGS[display_city]
    params = {
        "latitude": cfg["latitude"],
        "longitude": cfg["longitude"],
        "start_date": target_date,
        "end_date": target_date,
        "daily": "temperature_2m_max",
        "temperature_unit": cfg["temp_unit"],
        "timezone": "auto",
    }
    url = "https://archive-api.open-meteo.com/v1/archive"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("Actual-temp fetch failed for %s on %s: %s", city, target_date, exc)
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    for day, high in zip(dates, highs):
        if day == target_date and high is not None:
            return float(high)

    logger.info("No actual high returned for %s on %s", city, target_date)
    return None


async def _is_paper_position(db, position: dict) -> bool:
    """Return whether the position belongs to a paper trade."""
    trade_id = position.get("trade_id")
    if trade_id is None:
        return False

    cursor = await db.execute("SELECT paper_trade FROM trades WHERE id = ?", (trade_id,))
    row = await cursor.fetchone()
    return bool(row and row["paper_trade"])


async def _local_paper_resolution(
    db,
    position: dict,
    gamma_resolution: MarketResolution,
) -> MarketResolution | None:
    """Resolve expired paper positions locally when Gamma cannot find the market."""
    if not gamma_resolution.not_found:
        return None

    if not await _is_paper_position(db, position):
        return None

    target_date, temp_low, temp_high, _temp_unit = _extract_position_market(position)
    if not target_date or temp_low is None or temp_high is None:
        logger.info(
            "Position %s lacks target_date/temp bounds; cannot locally resolve",
            position.get("id"),
        )
        return None

    actual_high = await fetch_actual_high_temperature(position.get("city", ""), target_date)
    if actual_high is None:
        return None

    yes_won = _actual_temp_resolves_yes(actual_high, temp_low, temp_high)
    outcome = "Yes" if yes_won else "No"
    logger.info(
        "Locally resolved paper position %s: actual_high=%.2f range=%.2f-%.2f outcome=%s",
        position.get("id"),
        actual_high,
        temp_low,
        temp_high,
        outcome,
    )
    return MarketResolution(
        condition_id=gamma_resolution.condition_id,
        resolved=True,
        outcome=outcome,
        resolution_price=1.0 if yes_won else 0.0,
        not_found=True,
        source="open_meteo_archive",
    )


async def get_current_price(token_id: str) -> float | None:
    """
    Fetch the last-trade price for a token from the Polymarket CLOB API.

    Returns the price as a float, or None if the token_id is empty, the
    endpoint returns an unexpected payload, or any network / HTTP error occurs.
    Failures are logged but never propagated — callers must handle None.
    """
    if not token_id:
        logger.debug("get_current_price: token_id is empty; skipping CLOB fetch")
        return None

    url = f"{CLOB_BASE_URL}/last-trade-price"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"token_id": token_id})
            resp.raise_for_status()
            data: dict = resp.json()
            raw = data.get("price")
            if raw is None:
                logger.warning("CLOB response for token %s missing 'price' key: %s", token_id, data)
                return None
            price = float(raw)
            logger.debug("CLOB price for token %s: %.4f", token_id, price)
            return price
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP error fetching CLOB price for token %s: %s", token_id, exc)
    except httpx.RequestError as exc:
        logger.warning("Request error fetching CLOB price for token %s: %s", token_id, exc)
    except (ValueError, TypeError) as exc:
        logger.warning("Could not parse CLOB price for token %s: %s", token_id, exc)
    except Exception:
        logger.exception("Unexpected error fetching CLOB price for token %s", token_id)
    return None


# ---------------------------------------------------------------------------
# Early-exit logic
# ---------------------------------------------------------------------------


def check_early_exit(direction: str, entry_price: float, current_price: float, exit_threshold: float) -> bool:
    """
    Return True if the position should be exited early due to an adverse price move.

    - direction=YES: exit when current_price has fallen more than exit_threshold
      below entry_price  (i.e. current_price < entry_price * (1 - exit_threshold))
    - direction=NO:  exit when current_price has risen more than exit_threshold
      above entry_price  (i.e. current_price > entry_price * (1 + exit_threshold))
    """
    if entry_price <= 0:
        return False

    if direction.upper() == "YES":
        return current_price < entry_price * (1.0 - exit_threshold)
    else:  # direction == "NO"
        return current_price > entry_price * (1.0 + exit_threshold)


# ---------------------------------------------------------------------------
# P&L calculation
# ---------------------------------------------------------------------------


def _calculate_pnl(direction: str, resolution: MarketResolution, entry_price: float, bet_size: float) -> float:
    """
    Calculate realised P&L for a closed position.

    Win condition:
      - direction == "YES" and market resolved YES
      - direction == "NO"  and market resolved NO

    In both win cases the payoff is the profit on the notional position:
        pnl = (1.0 - entry_price) * (bet_size / entry_price)

    In loss cases the entire bet is lost:
        pnl = -bet_size
    """
    if entry_price <= 0:
        logger.warning("_calculate_pnl called with entry_price=%s; returning 0", entry_price)
        return 0.0

    resolved_yes = resolution.outcome is not None and resolution.outcome.lower() == "yes"

    if direction.upper() == "YES":
        if resolved_yes:
            return (1.0 - entry_price) * (bet_size / entry_price)
        else:
            return -bet_size
    else:  # direction == "NO"
        if not resolved_yes:
            return (1.0 - entry_price) * (bet_size / entry_price)
        else:
            return -bet_size


# ---------------------------------------------------------------------------
# Core resolution loop
# ---------------------------------------------------------------------------


async def resolve_positions(db) -> list[dict]:
    """
    Iterate over all open positions, check each for resolution, and close
    any that have resolved.

    Returns a list of trade dicts for every position that was resolved
    during this run.
    """
    resolved_trades: list[dict] = []

    try:
        positions = await get_open_positions(db)
    except Exception:
        logger.exception("Failed to fetch open positions; aborting resolver run")
        return resolved_trades

    if not positions:
        logger.info("No open positions to check")
        return resolved_trades

    logger.info("Checking resolution for %d open position(s)", len(positions))

    for position in positions:
        condition_id: str | None = position.get("condition_id") or position.get("token_id")
        trade_id: int | None = position.get("trade_id")
        position_id: int = position.get("id", 0)

        if not condition_id:
            logger.warning(
                "Position %d has no condition_id or token_id; skipping",
                position_id,
            )
            continue

        try:
            resolution = await check_resolution(condition_id)
        except Exception:
            logger.exception(
                "check_resolution raised unexpectedly for condition %s; skipping",
                condition_id,
            )
            continue

        if not resolution.resolved:
            local_resolution = await _local_paper_resolution(db, position, resolution)
            if local_resolution is not None:
                resolution = local_resolution
            else:
                logger.debug("Position %d (%s) is still open", position_id, condition_id)

                # --- Fetch live price and update position; check early exit ---
                token_id: str = position.get("token_id") or ""
                current_price = await get_current_price(token_id)

                if current_price is not None:
                    direction_str: str = position.get("direction", "YES")
                    entry_price_now: float = float(position.get("entry_price", 0.0))
                    bet_size_now: float = float(position.get("size", 0.0))

                    # Unrealized P&L: mark-to-market value minus cost basis
                    if entry_price_now > 0:
                        shares = bet_size_now / entry_price_now
                        unrealized_pnl = (current_price - entry_price_now) * shares
                        if direction_str.upper() == "NO":
                            unrealized_pnl = -unrealized_pnl
                    else:
                        unrealized_pnl = 0.0

                    # Persist current_price + unrealized_pnl to the positions row
                    try:
                        await db.execute(
                            "UPDATE positions SET current_price = ?, unrealized_pnl = ? WHERE id = ?",
                            (current_price, round(unrealized_pnl, 6), position_id),
                        )
                        await db.commit()
                        logger.debug(
                            "Position %d: current_price=%.4f unrealized_pnl=%.4f",
                            position_id,
                            current_price,
                            unrealized_pnl,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to update current_price/unrealized_pnl for position %d",
                            position_id,
                        )

                    # Read exit_threshold from DB settings, adjusting for paper_mode
                    try:
                        threshold_raw = await get_setting(db, "exit_threshold")
                        exit_threshold = float(threshold_raw) if threshold_raw is not None else DEFAULT_EXIT_THRESHOLD
                    except (ValueError, TypeError):
                        exit_threshold = DEFAULT_EXIT_THRESHOLD

                    # Apply paper mode calibration: lenient (50%) in paper mode, aggressive (30%) in live
                    paper_mode_raw = await get_setting(db, "paper_mode")
                    is_paper_mode = bool(paper_mode_raw and paper_mode_raw.lower() == "true")

                    if is_paper_mode:
                        adverse_threshold = 0.50
                    else:
                        adverse_threshold = min(exit_threshold, 0.30)

                    if check_early_exit(direction_str, entry_price_now, current_price, adverse_threshold):
                        logger.info(
                            "Early exit triggered for position %d — direction=%s entry=%.4f current=%.4f threshold=%.0f%%",
                            position_id,
                            direction_str,
                            entry_price_now,
                            current_price,
                            adverse_threshold * 100,
                        )

                        trade_id_early: int | None = position.get("trade_id")
                        paper_trade: bool = bool(position.get("paper_trade", True))
                        resolved_at_early = datetime.now(timezone.utc).isoformat()

                        # P&L: use current_price as the exit price
                        if direction_str.upper() == "YES":
                            # Exiting YES at a loss: we paid entry_price per share, now worth current_price
                            pnl_early = (current_price - entry_price_now) * (bet_size_now / entry_price_now) if entry_price_now > 0 else 0.0
                        else:
                            # Exiting NO: we paid entry_price for a NO share, it "won" if market goes to 0
                            pnl_early = (entry_price_now - current_price) * (bet_size_now / entry_price_now) if entry_price_now > 0 else 0.0

                        mode_label = "paper" if paper_trade else "live"
                        logger.info(
                            "Early exit (%s): position %d pnl=%.4f",
                            mode_label,
                            position_id,
                            pnl_early,
                        )

                        # For live trades, attempt to cancel the open order on Polymarket
                        if not paper_trade:
                            order_id = position.get("order_id")
                            if order_id:
                                try:
                                    from bot.clob_client import build_clob_client, cancel_order
                                    _client = build_clob_client()
                                    cancel_order(_client, order_id)
                                except Exception:
                                    logger.exception(
                                        "Failed to cancel order %s for position %d during early exit",
                                        order_id, position_id,
                                    )

                        # Close the position row
                        try:
                            await db.execute(
                                "UPDATE positions SET closed_at = ? WHERE id = ?",
                                (resolved_at_early, position_id),
                            )
                            await db.commit()
                        except Exception:
                            logger.exception(
                                "Failed to close position %d (early exit) in database",
                                position_id,
                            )
                            continue

                        # Update the parent trade row
                        if trade_id_early is not None:
                            try:
                                await update_trade(
                                    db,
                                    trade_id_early,
                                    exit_price=current_price,
                                    outcome="exited_early",
                                    pnl=round(pnl_early, 6),
                                    resolved_at=resolved_at_early,
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to update trade %d (early exit) in database",
                                    trade_id_early,
                                )

                        # Build trade dict and send alert
                        trade_dict_early = {
                            **position,
                            "exit_price": current_price,
                            "outcome": "exited_early",
                            "pnl": round(pnl_early, 6),
                            "resolved_at": resolved_at_early,
                        }
                        try:
                            early_exit_message = (
                                f"Early exit ({mode_label}): position {position_id} "
                                f"direction={direction_str} entry={entry_price_now:.4f} "
                                f"current={current_price:.4f} "
                                f"(>{adverse_threshold:.0%} adverse move) "
                                f"pnl={pnl_early:+.4f}"
                            )
                            await send_alert(
                                db,
                                "trade_resolved",
                                early_exit_message,
                                city=position.get("city"),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to send early-exit alert for position %d",
                                position_id,
                            )

                        resolved_trades.append(trade_dict_early)

                continue

        # --- Resolved: calculate P&L and close the position ---
        direction: str = position.get("direction", "YES")
        entry_price: float = float(position.get("entry_price", 0.0))
        bet_size: float = float(position.get("size", 0.0))

        pnl = _calculate_pnl(direction, resolution, entry_price, bet_size)
        outcome_label = "won" if pnl >= 0 else "lost"
        resolved_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Position %d resolved — direction=%s outcome=%s pnl=%.4f",
            position_id,
            direction,
            resolution.outcome,
            pnl,
        )

        # Close the position row
        try:
            await db.execute(
                "UPDATE positions SET closed_at = ? WHERE id = ?",
                (resolved_at, position_id),
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to close position %d in database", position_id)
            continue

        # Update the parent trade row
        if trade_id is not None:
            try:
                await update_trade(
                    db,
                    trade_id,
                    exit_price=resolution.resolution_price,
                    outcome=outcome_label,
                    pnl=round(pnl, 6),
                    resolved_at=resolved_at,
                )
            except Exception:
                logger.exception("Failed to update trade %d in database", trade_id)

        # Build a trade dict for the alert formatter (merges position + resolution data)
        trade_dict = {
            **position,
            "exit_price": resolution.resolution_price,
            "outcome": outcome_label,
            "pnl": round(pnl, 6),
            "resolved_at": resolved_at,
        }

        # Send resolution alert
        try:
            message = await format_trade_resolved(trade_dict)
            await send_alert(
                db,
                "trade_resolved",
                message,
                city=position.get("city"),
            )
        except Exception:
            logger.exception(
                "Failed to send resolution alert for position %d",
                position_id,
            )

        resolved_trades.append(trade_dict)

    logger.info(
        "Resolver run complete: %d/%d position(s) resolved",
        len(resolved_trades),
        len(positions),
    )
    return resolved_trades


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_resolver() -> int:
    """
    Main entry point for the position resolver.

    Acquires the database connection, resolves any matured positions, and
    returns the count of positions closed during this run.  Never raises —
    all exceptions are caught and logged so the scheduler is never disrupted.
    """
    try:
        db = await get_db()
        resolved = await resolve_positions(db)
        return len(resolved)
    except Exception:
        logger.exception("run_resolver encountered a fatal error")
        return 0
