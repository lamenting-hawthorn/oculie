"""
Core trading engine for the Polymarket Weather Prediction Agent.

Connects weather forecast data (NOAA + Open-Meteo) to Polymarket
temperature markets. Identifies mispriced contracts using Kelly Criterion
sizing and executes trades with configurable risk limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from bot.database import (
    get_city_date_exposure,
    get_db,
    get_direction_exposure,
    get_open_positions,
    get_setting,
    get_total_exposure,
    insert_position,
    insert_scan_log,
    insert_trade,
)
from bot.noaa_fetcher import (
    CITY_CONFIGS as NOAA_CITIES,
    CityForecast,
    TemperatureBucket,
)
from bot.noaa_fetcher import calculate_probability_distribution as noaa_distribution
from bot.noaa_fetcher import fetch_forecasts as fetch_noaa_forecasts
from bot.config import INTL_CITIES as CONFIG_INTL_CITIES
from bot.open_meteo_fetcher import (
    CITIES as OM_CITIES,
    InternationalCityForecast,
)
from bot.open_meteo_fetcher import (
    calculate_probability_distribution as om_distribution,
)
from bot.open_meteo_fetcher import fetch_forecasts as fetch_om_forecasts
from bot.consensus import ConsensusForecast, compute_consensus
from bot.visualcrossing_fetcher import (
    VCCityForecast,
    fetch_forecasts as fetch_vc_forecasts,
)
from bot.wttr_fetcher import (
    WttrCityForecast,
    fetch_forecasts as fetch_wttr_forecasts,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# City name -> settings key mapping
# ---------------------------------------------------------------------------

CITY_SETTINGS_KEY: dict[str, str] = {
    "New York City": "cities_nyc",
    "Chicago": "cities_chicago",
    "Miami": "cities_miami",
    "Dallas": "cities_dallas",
    "Seattle": "cities_seattle",
    "Atlanta": "cities_atlanta",
    "London": "cities_london",
    "Seoul": "cities_seoul",
    "Shanghai": "cities_shanghai",
    "Hong Kong": "cities_hongkong",
}

# Which cities are US (NOAA) vs international (Open-Meteo)
# OM_CITIES now covers every configured city (US + international) so that
# Open-Meteo ensemble data is available for the consensus engine, so we
# source the international split from bot.config.INTL_CITIES instead.
US_CITIES = set(NOAA_CITIES.keys())
INTL_CITIES = set(CONFIG_INTL_CITIES)

# Market scanner returns lowercase internal keys (e.g. "new_york", "hong_kong").
# Forecast dicts are keyed by display names ("New York City", "Hong Kong").
# This mapping translates between the two so city lookups work correctly.
_MARKET_KEY_TO_DISPLAY: dict[str, str] = {
    "new_york": "New York City",
    "chicago": "Chicago",
    "miami": "Miami",
    "dallas": "Dallas",
    "seattle": "Seattle",
    "atlanta": "Atlanta",
    "london": "London",
    "seoul": "Seoul",
    "shanghai": "Shanghai",
    "hong_kong": "Hong Kong",
    "tokyo": "Tokyo",
    "wellington": "Wellington",
    "lucknow": "Lucknow",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TradeSignal(BaseModel):
    city: str
    condition_id: str
    token_id: str
    question: str
    target_date: str | None = None
    temp_low: float | None = None
    temp_high: float | None = None
    temp_unit: str | None = None
    direction: str  # "YES" or "NO"
    noaa_probability: float
    market_price: float
    edge: float
    kelly_fraction: float
    bet_size: float
    paper: bool


class TradeResult(BaseModel):
    signal: TradeSignal
    success: bool
    order_id: str | None = None
    error: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScanResult(BaseModel):
    started_at: datetime
    completed_at: datetime
    cities_scanned: int
    markets_found: int
    opportunities: list[TradeSignal]
    trades_executed: list[TradeResult]
    errors: list[str]


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Kelly fractional scaling: reduces position sizing by this multiplier
# to avoid overconfident bets (e.g., 0.25 = use 25% of Kelly, not 100%)
KELLY_FRACTION_MULTIPLIER = 0.25

# Market maker liquidity check: minimum 24h volume required to enter a position
# Shallow order books (< $100) indicate unreliable pricing and high slippage risk
MIN_MARKET_DEPTH = 100.0  # dollars

# ---------------------------------------------------------------------------
# Edge & sizing calculations
# ---------------------------------------------------------------------------


def calculate_edge(forecast_prob: float, market_price: float) -> float:
    """Return the edge as forecast probability minus market price.

    Both values are decimals in [0, 1]. A positive edge means the forecast
    assigns a higher probability than the market price implies.
    """
    return forecast_prob - market_price


def kelly_criterion(
    forecast_prob: float,
    market_price: float,
    kelly_cap: float = 0.25,
) -> float:
    """Compute the Kelly fraction for a binary market.

    Uses the formula: f* = (p * b - q) / b
    where p = forecast_prob, q = 1 - p, b = (1 / market_price) - 1.

    The result is capped at *kelly_cap* (default 25% of bankroll) for
    conservative sizing. Returns 0 if the Kelly fraction is negative
    (i.e. no edge).
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0

    p = forecast_prob
    q = 1.0 - p
    b = (1.0 / market_price) - 1.0

    if b <= 0:
        return 0.0

    kelly_f = (p * b - q) / b

    if kelly_f <= 0:
        return 0.0

    return min(kelly_f, kelly_cap)


def size_position(
    kelly_fraction: float,
    bankroll: float,
    max_bet: float,
) -> float:
    """Translate a Kelly fraction into a dollar position size.

    - Position = kelly_fraction * bankroll
    - Capped at *max_bet*
    - Rounded to 2 decimal places
    - Returns 0 if below $1 minimum
    """
    position = kelly_fraction * bankroll
    position = min(position, max_bet)
    position = round(position, 2)

    if position < 1.0:
        return 0.0
    return position


# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------


async def check_risk_limits(
    db,
    bet_size: float,
    pending_exposure: float = 0.0,
    max_total_exposure_override: float | None = None,
    account_floor_override: float | None = None,
) -> tuple[bool, str]:
    """Validate a proposed bet against configured risk limits.

    Checks:
    1. bet_size <= max_bet_size
    2. current_exposure + pending_exposure + bet_size <= max_total_exposure
    3. estimated_balance - bet_size >= account_floor

    *pending_exposure* accounts for bets already approved in the current scan
    cycle but not yet committed to the database.

    The override values are used by live trading to enforce the user's
    startup trading budget without mutating global paper-trading settings.

    Returns (True, "ok") or (False, "<reason>").
    """
    max_bet_size = float(await get_setting(db, "max_bet_size") or "50.0")
    max_total_exposure = (
        max_total_exposure_override
        if max_total_exposure_override is not None
        else float(await get_setting(db, "max_total_exposure") or "200.0")
    )
    account_floor = (
        account_floor_override
        if account_floor_override is not None
        else float(await get_setting(db, "account_floor") or "100.0")
    )

    # Check 1: individual bet size
    if bet_size > max_bet_size:
        reason = (
            f"Bet size ${bet_size:.2f} exceeds max_bet_size ${max_bet_size:.2f}"
        )
        logger.warning("Risk check FAILED: %s", reason)
        return False, reason

    # Check 2: total exposure (DB positions + pending from this cycle)
    current_exposure = await get_total_exposure(db)
    effective_exposure = current_exposure + pending_exposure
    if effective_exposure + bet_size > max_total_exposure:
        reason = (
            f"Total exposure ${effective_exposure + bet_size:.2f} "
            f"would exceed max_total_exposure ${max_total_exposure:.2f} "
            f"(db: ${current_exposure:.2f}, pending: ${pending_exposure:.2f}, new: ${bet_size:.2f})"
        )
        logger.warning("Risk check FAILED: %s", reason)
        return False, reason

    # Check 3: account floor
    # Estimate balance as max_total_exposure minus effective exposure (conservative)
    estimated_balance = max_total_exposure - effective_exposure
    if estimated_balance - bet_size < account_floor:
        reason = (
            f"Estimated balance ${estimated_balance:.2f} - bet ${bet_size:.2f} "
            f"= ${estimated_balance - bet_size:.2f} would fall below "
            f"account_floor ${account_floor:.2f}"
        )
        logger.warning("Risk check FAILED: %s", reason)
        return False, reason

    logger.info(
        "Risk check PASSED: bet=$%.2f, exposure=$%.2f/$%.2f (pending=$%.2f), floor=$%.2f",
        bet_size,
        effective_exposure + bet_size,
        max_total_exposure,
        pending_exposure,
        account_floor,
    )
    return True, "ok"


# ---------------------------------------------------------------------------
# Token selection helper
# ---------------------------------------------------------------------------


def _select_token_id(market: Any, direction: str) -> str:
    """Return the CLOB token ID for the given direction (YES or NO).

    Iterates market.tokens (list[MarketToken]) and returns the token_id whose
    outcome matches the direction. Returns "" if no matching token is found,
    which will cause execute_trade() to return an error rather than submit a
    bad order.
    """
    for token in getattr(market, "tokens", []):
        if token.outcome.upper() == direction.upper():
            return token.token_id
    logger.warning(
        "No token found for direction=%s on market %s (tokens=%s)",
        direction,
        getattr(market, "question", "?"),
        [getattr(t, "outcome", "?") for t in getattr(market, "tokens", [])],
    )
    return ""


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------


async def execute_trade(signal: TradeSignal) -> TradeResult:
    """Execute a single trade signal.

    In paper mode the trade is logged to the database but no real order is
    placed. In live mode, a limit order is submitted via py_clob_client.
    """
    db = await get_db()

    # Guard: empty token_id means _select_token_id found no matching token —
    # do not submit an order with no token.
    if not signal.token_id:
        error_msg = (
            f"No token_id for direction={signal.direction} on {signal.question} — "
            "market.tokens may be empty or have unexpected outcome labels"
        )
        logger.error(error_msg)
        return TradeResult(signal=signal, success=False, error=error_msg)

    if signal.paper:
        logger.info(
            "PAPER TRADE: %s %s on %s | edge=%.2f%% size=$%.2f price=%.4f",
            signal.direction,
            signal.question,
            signal.city,
            signal.edge * 100,
            signal.bet_size,
            signal.market_price,
        )

        trade_id = await insert_trade(
            db,
            city=signal.city,
            market_question=signal.question,
            condition_id=signal.condition_id,
            token_id=signal.token_id,
            direction=signal.direction,
            noaa_probability=signal.noaa_probability,
            market_price=signal.market_price,
            edge=signal.edge,
            bet_size=signal.bet_size,
            entry_price=signal.market_price,
            paper_trade=True,
            target_date=signal.target_date,
            temp_low=signal.temp_low,
            temp_high=signal.temp_high,
            temp_unit=signal.temp_unit,
        )

        await insert_position(
            db,
            trade_id=trade_id,
            condition_id=signal.condition_id or None,
            city=signal.city,
            market_question=signal.question,
            token_id=signal.token_id,
            direction=signal.direction,
            entry_price=signal.market_price,
            size=signal.bet_size,
            paper=True,
            target_date=signal.target_date,
            temp_low=signal.temp_low,
            temp_high=signal.temp_high,
            temp_unit=signal.temp_unit,
        )

        return TradeResult(
            signal=signal,
            success=True,
            order_id=f"paper-{trade_id}",
        )

    # --- Live trade via py_clob_client ---
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from bot.clob_client import build_clob_client

        client = build_clob_client()

        order_args = OrderArgs(
            price=signal.market_price,
            size=signal.bet_size,
            side="BUY",
            token_id=signal.token_id,
        )

        logger.info(
            "LIVE TRADE: submitting %s %s on %s | price=%.4f size=$%.2f",
            signal.direction,
            signal.question,
            signal.city,
            signal.market_price,
            signal.bet_size,
        )

        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order, OrderType.GTC)

        order_id = response.get("orderID", response.get("id", "unknown"))
        logger.info("Order submitted: %s", order_id)

        live_trade_id = await insert_trade(
            db,
            city=signal.city,
            market_question=signal.question,
            condition_id=signal.condition_id,
            token_id=signal.token_id,
            direction=signal.direction,
            noaa_probability=signal.noaa_probability,
            market_price=signal.market_price,
            edge=signal.edge,
            bet_size=signal.bet_size,
            entry_price=signal.market_price,
            paper_trade=False,
            target_date=signal.target_date,
            temp_low=signal.temp_low,
            temp_high=signal.temp_high,
            temp_unit=signal.temp_unit,
        )

        await insert_position(
            db,
            trade_id=live_trade_id,
            condition_id=signal.condition_id or None,
            city=signal.city,
            market_question=signal.question,
            token_id=signal.token_id,
            direction=signal.direction,
            entry_price=signal.market_price,
            size=signal.bet_size,
            paper=False,
            target_date=signal.target_date,
            temp_low=signal.temp_low,
            temp_high=signal.temp_high,
            temp_unit=signal.temp_unit,
        )

        return TradeResult(
            signal=signal,
            success=True,
            order_id=str(order_id),
        )

    except (ImportError, ModuleNotFoundError):
        error_msg = "py_clob_client not installed — cannot execute live trades"
        logger.error(error_msg)
        return TradeResult(signal=signal, success=False, error=error_msg)
    except KeyError as exc:
        error_msg = f"Missing environment variable: {exc}"
        logger.error(error_msg)
        return TradeResult(signal=signal, success=False, error=error_msg)
    except Exception as exc:
        error_msg = f"Order submission failed: {exc}"
        logger.error(error_msg, exc_info=True)
        return TradeResult(signal=signal, success=False, error=error_msg)


# ---------------------------------------------------------------------------
# Forecast-to-market matching
# ---------------------------------------------------------------------------


def match_forecast_to_market(
    market: Any,
    us_forecasts: dict[str, CityForecast],
    intl_forecasts: dict[str, InternationalCityForecast],
    wttr_forecasts: dict[str, WttrCityForecast] | None = None,
    vc_forecasts: dict[str, VCCityForecast] | None = None,
    *,
    require_agreement: bool = True,
) -> float | None:
    """Find the forecast probability that corresponds to a market's temperature range.

    Parameters
    ----------
    market:
        A ``TemperatureMarket`` from the market scanner with attributes
        ``city``, ``temp_low``, ``temp_high``.
    us_forecasts:
        NOAA forecasts keyed by city name.
    intl_forecasts:
        Open-Meteo forecasts keyed by city display name.
    wttr_forecasts:
        Optional wttr.in forecasts keyed by city display name (third source
        for the consensus engine).
    require_agreement:
        When True, returns None whenever the multi-source consensus reports
        ``sources_agree == False``.

    Returns
    -------
    The summed probability of forecast buckets overlapping the market range,
    or ``None`` if no matching forecast is available or sources disagree.
    """
    # Market scanner uses internal lowercase keys ("new_york", "hong_kong").
    # Translate to the display name used as forecast dict keys.
    city = _MARKET_KEY_TO_DISPLAY.get(market.city, market.city)
    temp_low = market.temp_low
    temp_high = market.temp_high

    # Determine the target date from the market question or use tomorrow
    # Markets typically reference the next day's high temperature
    target_date = _extract_target_date(market)

    # VALIDATION: Reject same-day markets (zero lead time)
    days_out = (datetime.fromisoformat(target_date).date() - datetime.now(timezone.utc).date()).days
    if days_out < 1:
        logger.debug(
            "Rejecting same-day market for %s (target_date=%s, today=%s)",
            market.question, target_date, datetime.now(timezone.utc).date()
        )
        return None

    noaa_fc = us_forecasts.get(city) if city in US_CITIES else None
    om_fc = intl_forecasts.get(city)
    wttr_fc = (wttr_forecasts or {}).get(city)
    vc_fc = (vc_forecasts or {}).get(city)

    # Forecast freshness — skip stale data from any of the available sources.
    now = datetime.now(timezone.utc)
    for source_name, fc in (("NOAA", noaa_fc), ("Open-Meteo", om_fc)):
        if fc is None:
            continue
        forecast_age = (now - fc.fetched_at).total_seconds() / 3600.0
        if forecast_age > 4.0:
            logger.info(
                "Skipping market %s — %s forecast age=%.1fh (> 4h threshold)",
                market.question, source_name, forecast_age,
            )
            return None

    if noaa_fc is None and om_fc is None and wttr_fc is None and vc_fc is None:
        logger.debug("No forecast data available for city: %s", city)
        return None

    noaa_sigma = 3.0 if days_out <= 1 else 5.0
    consensus = compute_consensus(
        city,
        target_date,
        noaa=noaa_fc,
        open_meteo=om_fc,
        wttr=wttr_fc,
        visualcrossing=vc_fc,
        noaa_sigma=noaa_sigma,
    )

    if consensus is None:
        logger.debug(
            "No consensus available for %s on %s — skipping",
            city, target_date,
        )
        return None

    if require_agreement and not consensus.sources_agree:
        logger.info(
            "Consensus disagreement for %s on %s (spread=%.2f°%s, sources=%d) — skipping",
            city, target_date, consensus.source_spread, consensus.unit, consensus.source_count,
        )
        return None

    buckets = build_probability_distribution(city, target_date, consensus, noaa_fc, om_fc)
    if not buckets:
        return None

    return _sum_bucket_probability(buckets, temp_low, temp_high)


def _extract_target_date(market: Any) -> str:
    """Best-effort extraction of the target date from a market object.

    Falls back to tomorrow's date if the market doesn't carry a date field.
    """
    # If the market carries an explicit date attribute, use it
    if hasattr(market, "target_date") and market.target_date:
        return market.target_date

    # Otherwise use tomorrow (most weather markets resolve next-day)
    from datetime import timedelta

    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return tomorrow.isoformat()


def _sum_bucket_probability(
    buckets: list[TemperatureBucket],
    temp_low: float,
    temp_high: float,
) -> float:
    """Sum the probability mass of buckets overlapping [temp_low, temp_high]."""
    total = 0.0
    for bucket in buckets:
        # A bucket overlaps if it is not entirely below or entirely above
        if bucket.high <= temp_low or bucket.low >= temp_high:
            continue
        total += bucket.probability
    return total


# ---------------------------------------------------------------------------
# Consensus-aware distribution + lead-time thresholds
# ---------------------------------------------------------------------------


def get_thresholds_for_lead_time(days_out: int) -> tuple[float, float]:
    """Return (yes_threshold, no_threshold) scaled by days out."""
    if days_out <= 0:
        return (0.10, 0.05)
    if days_out == 1:
        return (0.15, 0.08)
    return (0.25, 0.12)


def _has_om_ensemble(om_forecast: InternationalCityForecast | None, target_date: str) -> bool:
    """Return True if Open-Meteo has ensemble members for *target_date*."""
    if om_forecast is None:
        return False
    for day in om_forecast.forecast_days:
        if day.date == target_date:
            return bool(day.ensemble_temp_max)
    return False


def build_probability_distribution(
    city: str,
    target_date: str,
    consensus: ConsensusForecast,
    noaa_forecast: CityForecast | None,
    open_meteo_forecast: InternationalCityForecast | None,
) -> list[TemperatureBucket]:
    """Build a unified probability distribution for this city/date.

    Prefers the Open-Meteo ensemble (data-driven empirical distribution)
    when available. Falls back to NOAA parametric with ensemble sigma
    from the consensus when available.
    """
    if _has_om_ensemble(open_meteo_forecast, target_date):
        try:
            return om_distribution(open_meteo_forecast, target_date)
        except Exception:
            logger.warning(
                "Open-Meteo distribution failed for %s on %s — falling back",
                city, target_date, exc_info=True,
            )

    if noaa_forecast is not None:
        # NOAA expects the override sigma in degF. Convert if the consensus
        # was computed in Celsius.
        sigma = consensus.std_dev
        if consensus.unit == "C":
            sigma = sigma * 9.0 / 5.0
        try:
            return noaa_distribution(noaa_forecast, target_date, ensemble_sigma=sigma)
        except Exception:
            logger.warning(
                "NOAA distribution failed for %s on %s",
                city, target_date, exc_info=True,
            )
            return []

    if open_meteo_forecast is not None:
        try:
            return om_distribution(open_meteo_forecast, target_date)
        except Exception:
            logger.warning(
                "Open-Meteo fallback distribution failed for %s on %s",
                city, target_date, exc_info=True,
            )

    return []


# ---------------------------------------------------------------------------
# Cross-market probability normalization
# ---------------------------------------------------------------------------


def _extract_target_date_from_question(question: str) -> str:
    """Extract target date from a market question string for grouping purposes.

    Falls back to 'unknown' if no date pattern is found.
    """
    import re

    # Match patterns like "April 14" or "March 25, 2026"
    month_map = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    pattern = (
        r"(?:on\s+|for\s+)?("
        + "|".join(month_map.keys())
        + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?"
    )
    match = re.search(pattern, question.lower())
    if match:
        month = month_map[match.group(1)]
        day = match.group(2).zfill(2)
        year = int(match.group(3)) if match.group(3) else datetime.now(timezone.utc).year
        return f"{year}-{month}-{day}"
    return "unknown"


async def _count_city_date_positions(db, city: str, target_date: str) -> int:
    """Count open positions for a city matching a target date."""
    positions = await get_open_positions(db)
    count = 0
    for p in positions:
        if p.get("city") != city:
            continue
        # Check if the market question mentions this target date
        q = p.get("market_question", "")
        extracted = _extract_target_date_from_question(q)
        if extracted == target_date:
            count += 1
    return count


def _normalize_market_group(
    market_probs: list[tuple[Any, float]],
) -> list[tuple[Any, float]]:
    """Normalize forecast probabilities for mutually-exclusive markets.

    For the same city + target_date, temperature buckets are mutually exclusive
    (the actual temp lands in exactly one bucket). If raw probabilities sum
    to >1.0, normalize them proportionally so the group sums to 1.0.
    """
    total = sum(p for _, p in market_probs)
    if total <= 1.0:
        return market_probs
    logger.info(
        "Normalizing market group: %d markets, raw sum=%.3f -> 1.0",
        len(market_probs),
        total,
    )
    return [(m, p / total) for m, p in market_probs]


def _direction_cap_reason(
    *,
    direction: str,
    bet_size: float,
    existing_yes: float,
    existing_no: float,
    pending_yes: float,
    pending_no: float,
    max_direction_ratio: float,
) -> str | None:
    """Return a skip reason if the trade would breach the direction cap."""
    if max_direction_ratio <= 0 or max_direction_ratio >= 1:
        return None

    total_before = existing_yes + existing_no + pending_yes + pending_no
    if total_before <= 0:
        # Bootstrap an empty portfolio; subsequent same-direction additions are capped.
        return None

    total_after = total_before + bet_size
    if direction.upper() == "YES":
        side_after = existing_yes + pending_yes + bet_size
    else:
        side_after = existing_no + pending_no + bet_size

    ratio = side_after / total_after if total_after > 0 else 0.0
    if ratio <= max_direction_ratio:
        return None

    return (
        f"{direction.upper()} direction ratio {ratio * 100:.1f}% "
        f"> {max_direction_ratio * 100:.1f}% cap"
    )


async def _select_portfolio_opportunities(
    db,
    raw_opportunities: list[TradeSignal],
    *,
    bankroll: float,
    max_bet_size: float,
    max_total_exposure: float | None = None,
    account_floor: float | None = None,
    max_city_date_exposure: float,
    max_trades_per_city_date: int,
    max_direction_ratio: float,
) -> list[TradeSignal]:
    """Choose final trades after applying portfolio-level risk controls."""
    selected: list[TradeSignal] = []
    pending_total_exposure = 0.0

    existing_yes_exposure = await get_direction_exposure(db, "YES")
    existing_no_exposure = await get_direction_exposure(db, "NO")

    pending_city_date_exposure: dict[str, float] = defaultdict(float)
    pending_city_date_trades: dict[str, int] = defaultdict(int)
    pending_yes = 0.0
    pending_no = 0.0

    for signal in sorted(raw_opportunities, key=lambda s: s.edge, reverse=True):
        target_date = signal.target_date or _extract_target_date_from_question(signal.question)
        group_key = f"{signal.city}|{target_date}"

        effective_bankroll = max(0.0, bankroll - pending_total_exposure)
        bet = size_position(signal.kelly_fraction, effective_bankroll, max_bet_size)
        if bet <= 0:
            logger.debug(
                "Skipping %s — position too small after portfolio sizing",
                signal.question,
            )
            continue

        ok, reason = await check_risk_limits(
            db,
            bet,
            pending_total_exposure,
            max_total_exposure_override=max_total_exposure,
            account_floor_override=account_floor,
        )
        if not ok:
            logger.info("Skipping %s — risk limit: %s", signal.question, reason)
            continue

        existing_cd_trades = await _count_city_date_positions(
            db, signal.city, target_date
        )
        if (
            existing_cd_trades + pending_city_date_trades[group_key]
            >= max_trades_per_city_date
        ):
            logger.info(
                "Skipping %s — city/date trade cap reached (%d+%d >= %d)",
                signal.question,
                existing_cd_trades,
                pending_city_date_trades[group_key],
                max_trades_per_city_date,
            )
            continue

        existing_cd_exposure = await get_city_date_exposure(db, signal.city, target_date)
        if (
            existing_cd_exposure
            + pending_city_date_exposure[group_key]
            + bet
            > max_city_date_exposure
        ):
            logger.info(
                "Skipping %s — city/date exposure cap ($%.2f + $%.2f + $%.2f > $%.2f)",
                signal.question,
                existing_cd_exposure,
                pending_city_date_exposure[group_key],
                bet,
                max_city_date_exposure,
            )
            continue

        direction_reason = _direction_cap_reason(
            direction=signal.direction,
            bet_size=bet,
            existing_yes=existing_yes_exposure,
            existing_no=existing_no_exposure,
            pending_yes=pending_yes,
            pending_no=pending_no,
            max_direction_ratio=max_direction_ratio,
        )
        if direction_reason is not None:
            logger.info("Skipping %s — %s", signal.question, direction_reason)
            continue

        final_signal = signal.model_copy(update={"bet_size": bet})
        selected.append(final_signal)
        pending_total_exposure += bet
        pending_city_date_exposure[group_key] += bet
        pending_city_date_trades[group_key] += 1
        if signal.direction.upper() == "YES":
            pending_yes += bet
        else:
            pending_no += bet

    return selected


# ---------------------------------------------------------------------------
# Main scan cycle
# ---------------------------------------------------------------------------


async def _get_enabled_cities(db) -> tuple[list[str], list[str]]:
    """Return (enabled_us_cities, enabled_intl_cities) based on settings."""
    enabled_us: list[str] = []
    enabled_intl: list[str] = []

    for city, setting_key in CITY_SETTINGS_KEY.items():
        val = await get_setting(db, setting_key)
        if val and val.lower() == "true":
            if city in US_CITIES:
                enabled_us.append(city)
            elif city in INTL_CITIES:
                enabled_intl.append(city)

    return enabled_us, enabled_intl


def _parse_positive_usdc(raw: str | None) -> float | None:
    """Parse a positive USDC amount from DB settings."""
    if raw is None:
        return None
    normalized = raw.strip().replace("$", "")
    if not normalized:
        return None
    try:
        amount = float(normalized)
    except ValueError:
        return None
    return amount if amount > 0 else None


async def run_scan_cycle() -> ScanResult:
    """Execute one full scan-and-trade cycle.

    This is the main entry point, intended to be called every ~30 minutes.

    Steps:
    1. Fetch weather data from NOAA (US) and Open-Meteo (international)
    2. Scan Polymarket for active temperature markets
    3. Match each market to its forecast and compute edge
    4. Filter by entry threshold
    5. Size positions with Kelly criterion + risk limits
    6. Execute qualifying trades
    7. Log scan results to the database
    """
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []
    opportunities: list[TradeSignal] = []
    trades_executed: list[TradeResult] = []
    markets_found = 0

    db = await get_db()

    # --- Load settings ---
    max_bet_size = float(await get_setting(db, "max_bet_size") or "50.0")
    max_total_exposure = float(await get_setting(db, "max_total_exposure") or "200.0")
    paper_mode = (await get_setting(db, "paper_mode") or "true").lower() == "true"
    require_agreement = (
        (await get_setting(db, "require_source_agreement") or "true").lower() == "true"
    )
    account_floor_override: float | None = None

    logger.info(
        "Starting scan cycle: lead-time thresholds active, max_bet=$%.2f exposure_cap=$%.2f paper=%s require_agree=%s",
        max_bet_size,
        max_total_exposure,
        paper_mode,
        require_agreement,
    )

    # --- Step 1: Determine enabled cities ---
    enabled_us, enabled_intl = await _get_enabled_cities(db)
    total_cities = len(enabled_us) + len(enabled_intl)
    logger.info(
        "Enabled cities: US=%s, International=%s",
        enabled_us,
        enabled_intl,
    )

    if total_cities == 0:
        msg = "No cities enabled — skipping scan"
        logger.warning(msg)
        errors.append(msg)
        completed_at = datetime.now(timezone.utc)
        await _log_scan(db, started_at, completed_at, 0, 0, 0, 0, errors)
        return ScanResult(
            started_at=started_at,
            completed_at=completed_at,
            cities_scanned=0,
            markets_found=0,
            opportunities=[],
            trades_executed=[],
            errors=errors,
        )

    # --- Step 2: Fetch weather forecasts ---
    us_forecasts: dict[str, CityForecast] = {}
    intl_forecasts: dict[str, InternationalCityForecast] = {}
    wttr_forecasts: dict[str, WttrCityForecast] = {}
    vc_forecasts: dict[str, VCCityForecast] = {}

    all_enabled_cities = enabled_us + enabled_intl

    if enabled_us:
        try:
            us_forecasts = await fetch_noaa_forecasts(enabled_us)
            logger.info("NOAA forecasts fetched for %d cities", len(us_forecasts))
        except Exception as exc:
            msg = f"NOAA fetch failed: {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    # Open-Meteo serves both US and international cities (US for ensemble overlay).
    if all_enabled_cities:
        try:
            intl_forecasts = await fetch_om_forecasts(all_enabled_cities)
            logger.info("Open-Meteo forecasts fetched for %d cities", len(intl_forecasts))
        except Exception as exc:
            msg = f"Open-Meteo fetch failed: {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    if all_enabled_cities:
        try:
            wttr_forecasts = await fetch_wttr_forecasts(all_enabled_cities)
            logger.info("wttr.in forecasts fetched for %d cities", len(wttr_forecasts))
        except Exception as exc:
            msg = f"wttr.in fetch failed: {exc}"
            logger.warning(msg, exc_info=True)
            errors.append(msg)

    if all_enabled_cities:
        try:
            vc_forecasts = await fetch_vc_forecasts(all_enabled_cities)
            logger.info("Visual Crossing forecasts fetched for %d cities", len(vc_forecasts))
        except Exception as exc:
            msg = f"Visual Crossing fetch failed: {exc}"
            logger.warning(msg, exc_info=True)
            errors.append(msg)

    cities_scanned = len(set(us_forecasts) | set(intl_forecasts))
    if cities_scanned == 0:
        msg = "No forecasts retrieved — aborting scan"
        logger.warning(msg)
        errors.append(msg)
        completed_at = datetime.now(timezone.utc)
        await _log_scan(db, started_at, completed_at, 0, 0, 0, 0, errors)
        return ScanResult(
            started_at=started_at,
            completed_at=completed_at,
            cities_scanned=0,
            markets_found=0,
            opportunities=[],
            trades_executed=[],
            errors=errors,
        )

    # --- Step 3: Scan Polymarket for temperature markets ---
    try:
        from bot.market_scanner import scan_markets

        city_markets = await scan_markets()
        for city_name, cm in city_markets.items():
            markets_found += len(cm.markets) if hasattr(cm, "markets") else 0
        logger.info(
            "Market scan complete: %d cities, %d markets",
            len(city_markets),
            markets_found,
        )
    except ImportError:
        msg = "bot.market_scanner not available — cannot scan markets"
        logger.error(msg)
        errors.append(msg)
        completed_at = datetime.now(timezone.utc)
        await _log_scan(db, started_at, completed_at, cities_scanned, 0, 0, 0, errors)
        return ScanResult(
            started_at=started_at,
            completed_at=completed_at,
            cities_scanned=cities_scanned,
            markets_found=0,
            opportunities=[],
            trades_executed=[],
            errors=errors,
        )
    except Exception as exc:
        msg = f"Market scan failed: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)
        completed_at = datetime.now(timezone.utc)
        await _log_scan(db, started_at, completed_at, cities_scanned, 0, 0, 0, errors)
        return ScanResult(
            started_at=started_at,
            completed_at=completed_at,
            cities_scanned=cities_scanned,
            markets_found=0,
            opportunities=[],
            trades_executed=[],
            errors=errors,
        )

    # --- Step 4: Match forecasts to markets and identify opportunities ---
    # Load portfolio-level settings
    max_city_date_exposure = float(await get_setting(db, "max_city_date_exposure") or "40.0")
    max_trades_per_city_date = int(float(await get_setting(db, "max_trades_per_city_date") or "2"))
    max_direction_ratio = float(await get_setting(db, "max_direction_ratio") or "0.75")

    # Use real CLOB balance plus the user's live budget as bankroll in live mode.
    if not paper_mode:
        live_budget = _parse_positive_usdc(await get_setting(db, "live_trading_budget_usdc"))
        if live_budget is None:
            msg = "Live trading budget is not set; refusing to evaluate live trades"
            logger.error(msg)
            errors.append(msg)
            completed_at = datetime.now(timezone.utc)
            await _log_scan(
                db,
                started_at,
                completed_at,
                cities_scanned,
                markets_found,
                0,
                0,
                errors,
            )
            return ScanResult(
                started_at=started_at,
                completed_at=completed_at,
                cities_scanned=cities_scanned,
                markets_found=markets_found,
                opportunities=[],
                trades_executed=[],
                errors=errors,
            )

        try:
            from bot.clob_client import build_clob_client, get_usdc_balance
            _clob = build_clob_client()
            live_balance = get_usdc_balance(_clob)
            if live_balance <= 0:
                raise RuntimeError("CLOB balance is zero or unavailable")

            current_exposure = await get_total_exposure(db)
            max_total_exposure = min(max_total_exposure, live_budget, live_balance)
            bankroll = max(0.0, max_total_exposure - current_exposure)
            account_floor_override = 0.0
            logger.info(
                "Live bankroll from CLOB: $%.2f (wallet=$%.2f budget=$%.2f exposure_cap=$%.2f)",
                bankroll,
                live_balance,
                live_budget,
                max_total_exposure,
            )
        except Exception:
            msg = "Could not fetch live balance; refusing to evaluate live trades"
            logger.warning(msg, exc_info=True)
            errors.append(msg)
            completed_at = datetime.now(timezone.utc)
            await _log_scan(
                db,
                started_at,
                completed_at,
                cities_scanned,
                markets_found,
                0,
                0,
                errors,
            )
            return ScanResult(
                started_at=started_at,
                completed_at=completed_at,
                cities_scanned=cities_scanned,
                markets_found=markets_found,
                opportunities=[],
                trades_executed=[],
                errors=errors,
            )
    else:
        bankroll = max_total_exposure - await get_total_exposure(db)

    # --- Phase 1: Collect raw forecast probabilities for all markets ---
    # Group by (city, target_date) for cross-market normalization
    market_raw_probs: dict[tuple[str, str], list[tuple[Any, float]]] = defaultdict(list)

    for city_name, cm in city_markets.items():
        market_list = cm.markets if hasattr(cm, "markets") else []
        for market in market_list:
            try:
                forecast_prob = match_forecast_to_market(
                    market,
                    us_forecasts,
                    intl_forecasts,
                    wttr_forecasts,
                    vc_forecasts,
                    require_agreement=require_agreement,
                )
                if forecast_prob is None:
                    logger.debug(
                        "No forecast match for market: %s (%s)",
                        market.question,
                        market.city,
                    )
                    continue
                target_date = getattr(market, "target_date", None) or _extract_target_date(market)
                group_key = (market.city, target_date)
                market_raw_probs[group_key].append((market, forecast_prob))
            except Exception as exc:
                msg = f"Error matching market {getattr(market, 'question', '?')}: {exc}"
                logger.error(msg, exc_info=True)
                errors.append(msg)

    # --- Phase 2: Normalize probabilities within each city/date group ---
    normalized_markets: list[tuple[Any, float]] = []
    for group_key, group in market_raw_probs.items():
        normalized = _normalize_market_group(group)
        normalized_markets.extend(normalized)

    logger.info(
        "Forecast matching: %d markets matched, %d city/date groups",
        len(normalized_markets),
        len(market_raw_probs),
    )

    # --- Phase 3: Evaluate edge, Kelly sizing, and risk for each market ---
    # Fetch open positions once for duplicate guard
    open_positions = await get_open_positions(db)
    open_condition_ids = {
        p.get("condition_id") for p in open_positions if p.get("condition_id")
    }

    raw_opportunities: list[TradeSignal] = []  # before portfolio selection

    today = datetime.now(timezone.utc).date()

    for market, forecast_prob in normalized_markets:
        try:
            target_date_str = getattr(market, "target_date", None) or _extract_target_date(market)
            try:
                target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                days_out = max((target_date_obj - today).days, 0)
            except (ValueError, TypeError):
                days_out = 1
            entry_threshold_yes, entry_threshold_no = get_thresholds_for_lead_time(days_out)

            # Apply Bayesian dampening: trust forecast 60%, market 40%
            yes_dampened = (forecast_prob * 0.6) + (market.yes_price * 0.4)
            yes_edge = calculate_edge(yes_dampened, market.yes_price)

            # Evaluate NO side (probability of NOT being in range)
            no_prob = 1.0 - forecast_prob
            no_dampened = (no_prob * 0.6) + (market.no_price * 0.4)
            no_edge = calculate_edge(no_dampened, market.no_price)

            # VALIDATION: Skip markets with illiquid prices
            PRICE_FLOOR = 0.01
            if yes_dampened < PRICE_FLOOR or yes_dampened > (1.0 - PRICE_FLOOR):
                logger.debug("Rejecting illiquid market %s (yes_price=%f)", market.question, yes_dampened)
                continue
            if no_dampened < PRICE_FLOOR or no_dampened > (1.0 - PRICE_FLOOR):
                logger.debug("Rejecting illiquid market %s (no_price=%f)", market.question, no_dampened)
                continue

            # VALIDATION: Check market depth (minimum liquidity requirement)
            market_volume = getattr(market, "volume", 0.0)
            if market_volume < MIN_MARKET_DEPTH:
                logger.debug(
                    "Skipping market %s — insufficient depth ($%.2f < $%.2f)",
                    market.question,
                    market_volume,
                    MIN_MARKET_DEPTH,
                )
                continue

            # Pick the side with better edge
            if yes_edge >= no_edge and yes_edge >= entry_threshold_yes:
                direction = "YES"
                edge = yes_edge
                prob = yes_dampened
                price = market.yes_price
            elif no_edge > yes_edge and no_edge >= entry_threshold_no:
                direction = "NO"
                edge = no_edge
                prob = no_dampened
                price = market.no_price
            else:
                logger.debug(
                    "Insufficient edge for %s: YES=%.3f (threshold=%.3f) NO=%.3f (threshold=%.3f)",
                    market.question,
                    yes_edge,
                    entry_threshold_yes,
                    no_edge,
                    entry_threshold_no,
                )
                continue

            kf = kelly_criterion(prob, price)
            kf_scaled = kf * KELLY_FRACTION_MULTIPLIER
            bet = size_position(kf_scaled, bankroll, max_bet_size)

            if bet <= 0:
                logger.debug(
                    "Position too small after Kelly sizing: %s (kf=%.4f)",
                    market.question,
                    kf,
                )
                continue

            # Duplicate guard
            if market.condition_id in open_condition_ids:
                logger.info(
                    "Skipping %s — already have open position for condition_id=%s",
                    market.question,
                    market.condition_id,
                )
                continue

            token_id = _select_token_id(market, direction)
            target_date = getattr(market, "target_date", None) or _extract_target_date(market)
            signal = TradeSignal(
                city=market.city,
                condition_id=market.condition_id,
                token_id=token_id,
                question=market.question,
                target_date=target_date,
                temp_low=market.temp_low,
                temp_high=market.temp_high,
                temp_unit=market.temp_unit,
                direction=direction,
                noaa_probability=prob,
                market_price=price,
                edge=edge,
                kelly_fraction=kf_scaled,
                bet_size=bet,
                paper=paper_mode,
            )
            raw_opportunities.append(signal)

            logger.info(
                "CANDIDATE: %s %s %s | prob=%.3f price=%.3f edge=%.3f "
                "kelly=%.4f (scaled) max_bet=$%.2f",
                direction,
                market.city,
                market.question,
                prob,
                price,
                edge,
                kf_scaled,
                bet,
            )

        except Exception as exc:
            msg = f"Error evaluating market {getattr(market, 'question', '?')}: {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    # --- Phase 4: Portfolio selection (total exposure, city/date caps, direction balance) ---
    opportunities = await _select_portfolio_opportunities(
        db,
        raw_opportunities,
        bankroll=bankroll,
        max_bet_size=max_bet_size,
        max_total_exposure=max_total_exposure,
        account_floor=account_floor_override,
        max_city_date_exposure=max_city_date_exposure,
        max_trades_per_city_date=max_trades_per_city_date,
        max_direction_ratio=max_direction_ratio,
    )

    logger.info(
        "Portfolio selection: %d raw -> %d selected (city/date cap=$%.0f, max_trades=%d, direction_ratio=%.0f%%)",
        len(raw_opportunities),
        len(opportunities),
        max_city_date_exposure,
        max_trades_per_city_date,
        max_direction_ratio * 100,
    )

    logger.info(
        "Found %d opportunities from %d markets", len(opportunities), markets_found
    )

    # --- Step 6: Execute trades ---
    for signal in opportunities:
        try:
            result = await execute_trade(signal)
            trades_executed.append(result)
            if result.success:
                logger.info(
                    "Trade executed: %s %s %s | order=%s",
                    signal.direction,
                    signal.city,
                    signal.question,
                    result.order_id,
                )
            else:
                logger.warning(
                    "Trade failed: %s %s %s | error=%s",
                    signal.direction,
                    signal.city,
                    signal.question,
                    result.error,
                )
                errors.append(
                    f"Trade execution failed for {signal.city} "
                    f"{signal.question}: {result.error}"
                )
        except Exception as exc:
            msg = f"Unexpected error executing trade for {signal.question}: {exc}"
            logger.error(msg, exc_info=True)
            errors.append(msg)

    # --- Step 7: Log to database ---
    completed_at = datetime.now(timezone.utc)
    await _log_scan(
        db,
        started_at,
        completed_at,
        cities_scanned,
        markets_found,
        len(opportunities),
        len([t for t in trades_executed if t.success]),
        errors,
    )

    logger.info(
        "Scan cycle complete: %d cities, %d markets, %d opportunities, "
        "%d trades executed, %d errors in %.1fs",
        cities_scanned,
        markets_found,
        len(opportunities),
        len(trades_executed),
        len(errors),
        (completed_at - started_at).total_seconds(),
    )

    return ScanResult(
        started_at=started_at,
        completed_at=completed_at,
        cities_scanned=cities_scanned,
        markets_found=markets_found,
        opportunities=opportunities,
        trades_executed=trades_executed,
        errors=errors,
    )


async def _log_scan(
    db,
    started_at: datetime,
    completed_at: datetime,
    cities_scanned: int,
    markets_found: int,
    opportunities_found: int,
    trades_executed: int,
    errors: list[str],
) -> None:
    """Persist scan results to the scan_log table."""
    try:
        await insert_scan_log(
            db,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            cities_scanned=cities_scanned,
            markets_found=markets_found,
            opportunities_found=opportunities_found,
            trades_executed=trades_executed,
            errors=json.dumps(errors) if errors else None,
        )
    except Exception:
        logger.error("Failed to insert scan log", exc_info=True)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    async def _main() -> None:
        logger.info("Running a single scan cycle ...")
        result = await run_scan_cycle()

        print(f"\n{'=' * 60}")
        print("  SCAN CYCLE RESULTS")
        print(f"{'=' * 60}")
        print(f"  Started:       {result.started_at.isoformat()}")
        print(f"  Completed:     {result.completed_at.isoformat()}")
        duration = (result.completed_at - result.started_at).total_seconds()
        print(f"  Duration:      {duration:.1f}s")
        print(f"  Cities:        {result.cities_scanned}")
        print(f"  Markets:       {result.markets_found}")
        print(f"  Opportunities: {len(result.opportunities)}")
        print(f"  Trades:        {len(result.trades_executed)}")
        print(f"  Errors:        {len(result.errors)}")

        if result.opportunities:
            print(f"\n  {'—' * 56}")
            print("  OPPORTUNITIES:")
            for sig in result.opportunities:
                print(
                    f"    {sig.direction:3s} {sig.city:<20s} "
                    f"edge={sig.edge:+.2%}  bet=${sig.bet_size:.2f}  "
                    f"prob={sig.noaa_probability:.3f} vs price={sig.market_price:.3f}"
                )

        if result.trades_executed:
            print(f"\n  {'—' * 56}")
            print("  TRADES:")
            for tr in result.trades_executed:
                status = "OK" if tr.success else f"FAIL: {tr.error}"
                print(
                    f"    {tr.signal.direction:3s} {tr.signal.city:<20s} "
                    f"${tr.signal.bet_size:.2f}  {status}"
                )

        if result.errors:
            print(f"\n  {'—' * 56}")
            print("  ERRORS:")
            for err in result.errors:
                print(f"    - {err}")

        print()

    asyncio.run(_main())
