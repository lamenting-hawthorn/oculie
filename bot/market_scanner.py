"""
Query the Polymarket Gamma API for active weather/temperature prediction
markets and map them to city + temperature buckets.

The Gamma API (https://gamma-api.polymarket.com) exposes public market and
event data without authentication.  This module discovers temperature markets,
parses their question text, and groups them by city so downstream code can
compare Polymarket-implied probabilities against NOAA/Wunderground forecasts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
REQUEST_DELAY = 0.25  # seconds between successive API calls (rate limiting)

# ---------------------------------------------------------------------------
# City name mapping  (Polymarket display name -> internal key)
# ---------------------------------------------------------------------------

CITY_NAME_MAP: dict[str, str] = {
    "New York City": "new_york",
    "NYC": "new_york",
    "New York": "new_york",
    "Chicago": "chicago",
    "Miami": "miami",
    "Dallas": "dallas",
    "Seattle": "seattle",
    "Atlanta": "atlanta",
    "London": "london",
    "Seoul": "seoul",
    "Shanghai": "shanghai",
    "Hong Kong": "hong_kong",
    "Wellington": "wellington",
    "Lucknow": "lucknow",
}

# Maps the internal city key used in TemperatureMarket back to the
# CITY_CONFIGS display key so we can look up the expected station_name.
_INTERNAL_TO_CONFIG_KEY: dict[str, str] = {
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
}

STATION_RE = re.compile(r"at the (.+?) (?:Station|station)")


def extract_station_name(description: str | None) -> str | None:
    """Return the station name Polymarket will resolve against, or None if not found."""
    if not description:
        return None
    m = STATION_RE.search(description)
    return m.group(1).strip() if m else None

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MarketToken(BaseModel):
    token_id: str
    outcome: str  # "Yes" or "No"
    price: float


class TemperatureMarket(BaseModel):
    condition_id: str
    question: str
    city: str  # internal key, e.g. "new_york"
    temp_low: float
    temp_high: float
    temp_unit: str = "F"  # "F" or "C"
    target_date: str  # ISO date, e.g. "2026-03-25"
    tokens: list[MarketToken] = Field(default_factory=list)
    yes_price: float = 0.0
    no_price: float = 0.0
    volume: float = 0.0
    active: bool = True
    station_name: str | None = None


class CityMarkets(BaseModel):
    city: str  # internal key
    markets: list[TemperatureMarket] = Field(default_factory=list)
    event_slug: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Question parser
# ---------------------------------------------------------------------------

# Regex patterns for common Polymarket temperature question formats:
#   "Will the high temperature in New York City be between 40°F and 45°F on March 25?"
#   "Will the high in NYC be 40-45°F on March 25, 2026?"
#   "Will the high temperature in Dallas be above 90°F on April 1?"
#   "Will the high temperature in London be between 10°C and 15°C on March 25?"

_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_CITY_PATTERN = "|".join(
    re.escape(name) for name in sorted(CITY_NAME_MAP, key=len, reverse=True)
)

# Pattern: "between X°F and Y°F" or "X-Y°F" or "X°F and Y°F"
_RANGE_RE = re.compile(
    rf"(?:in|for)\s+(?P<city>{_CITY_PATTERN})"
    r".*?"
    r"(?:between\s+)?"
    r"(?P<low>-?\d+(?:\.\d+)?)\s*°?\s*(?:F|C)?"
    r"\s*(?:and|[-–—to]+)\s*"
    r"(?P<high>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])",
    re.IGNORECASE,
)

# Pattern for "above X°F" (open-ended upper bound)
_ABOVE_RE = re.compile(
    rf"(?:in|for)\s+(?P<city>{_CITY_PATTERN})"
    r".*?"
    r"(?:above|over|at\s+least|>=?)\s+"
    r"(?P<low>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])",
    re.IGNORECASE,
)

# Pattern for "X°F or higher" — number-first above (actual Polymarket format)
_ABOVE_NUM_FIRST_RE = re.compile(
    rf"(?:in|for)\s+(?P<city>{_CITY_PATTERN})"
    r".*?"
    r"(?P<low>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])\s+or\s+(?:higher|above|more)",
    re.IGNORECASE,
)

# Pattern for "below X°F" (open-ended lower bound)
_BELOW_RE = re.compile(
    rf"(?:in|for)\s+(?P<city>{_CITY_PATTERN})"
    r".*?"
    r"(?:below|under|at\s+most|<=?)\s+"
    r"(?P<high>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])",
    re.IGNORECASE,
)

# Pattern for "X°F or below" — number-first below (actual Polymarket format)
_BELOW_NUM_FIRST_RE = re.compile(
    rf"(?:in|for)\s+(?P<city>{_CITY_PATTERN})"
    r".*?"
    r"(?P<high>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])\s+or\s+(?:lower|below|less)",
    re.IGNORECASE,
)

# Date pattern: "March 25" or "March 25, 2026" or "3/25/2026"
_DATE_TEXT_RE = re.compile(
    r"(?:on|for)\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})"
    r"(?:\s*,?\s*(?P<year>\d{4}))?",
    re.IGNORECASE,
)
_DATE_NUMERIC_RE = re.compile(
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})"
)

# ---------------------------------------------------------------------------
# Sub-market question regex patterns
# (for nested markets whose question is just a temperature range, e.g. "58°F or higher")
# ---------------------------------------------------------------------------

# "58°F or higher" / "15°C or higher"
_SUB_ABOVE_RE = re.compile(
    r"^(?P<low>-?\d+(?:\.\d+)?)\s*°?\s*(?P<unit>[FC])\s+or\s+(?:higher|above|more)",
    re.IGNORECASE,
)
# "33°C or below" / "56°F or lower"
_SUB_BELOW_RE = re.compile(
    r"^(?P<high>-?\d+(?:\.\d+)?)\s*°?\s*(?P<unit>[FC])\s+or\s+(?:lower|below|less)",
    re.IGNORECASE,
)
# "56-57°F" / "14-15°C"
_SUB_RANGE_RE = re.compile(
    r"^(?P<low>-?\d+(?:\.\d+)?)\s*[-–]\s*(?P<high>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])",
    re.IGNORECASE,
)
# "14°C" or "58°F" (exact single value — treat as a point range)
_SUB_EXACT_RE = re.compile(
    r"^(?P<temp>-?\d+(?:\.\d+)?)\s*°\s*(?P<unit>[FC])\s*$",
    re.IGNORECASE,
)

# Event title pattern: "Highest temperature in [City] on [Date]?"
_EVENT_TITLE_CITY_RE = re.compile(
    rf"(?:highest\s+temperature\s+in\s+)(?P<city>{_CITY_PATTERN})(?:\s+on\s+)",
    re.IGNORECASE,
)


def _parse_date_from_question(question: str) -> str | None:
    """Extract an ISO date string from the question text."""
    m = _DATE_TEXT_RE.search(question)
    if m:
        month_str = m.group("month").lower()
        month = _MONTH_MAP.get(month_str)
        if month is None:
            return None
        day = int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else datetime.now(timezone.utc).year
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = _DATE_NUMERIC_RE.search(question)
    if m:
        try:
            return datetime(
                int(m.group("year")), int(m.group("month")), int(m.group("day"))
            ).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def parse_market_question(question: str) -> dict | None:
    """
    Parse a Polymarket temperature-market question.

    Returns a dict with keys: city, temp_low, temp_high, temp_unit, target_date.
    Returns ``None`` if the question is not a recognisable temperature market.
    """
    target_date = _parse_date_from_question(question)
    if target_date is None:
        return None

    # Try range pattern first ("between X and Y")
    m = _RANGE_RE.search(question)
    if m:
        city_raw = m.group("city")
        city = CITY_NAME_MAP.get(city_raw)
        if city is None:
            return None
        return {
            "city": city,
            "temp_low": float(m.group("low")),
            "temp_high": float(m.group("high")),
            "temp_unit": m.group("unit").upper(),
            "target_date": target_date,
        }

    # "above X" pattern — use a very high upper bound
    m = _ABOVE_RE.search(question)
    if m:
        city_raw = m.group("city")
        city = CITY_NAME_MAP.get(city_raw)
        if city is None:
            return None
        return {
            "city": city,
            "temp_low": float(m.group("low")),
            "temp_high": 999.0,
            "temp_unit": m.group("unit").upper(),
            "target_date": target_date,
        }

    # "X°F or higher" pattern (number-first, actual Polymarket format)
    m = _ABOVE_NUM_FIRST_RE.search(question)
    if m:
        city_raw = m.group("city")
        city = CITY_NAME_MAP.get(city_raw)
        if city is None:
            return None
        return {
            "city": city,
            "temp_low": float(m.group("low")),
            "temp_high": 999.0,
            "temp_unit": m.group("unit").upper(),
            "target_date": target_date,
        }

    # "below X" pattern — use a very low lower bound
    m = _BELOW_RE.search(question)
    if m:
        city_raw = m.group("city")
        city = CITY_NAME_MAP.get(city_raw)
        if city is None:
            return None
        return {
            "city": city,
            "temp_low": -999.0,
            "temp_high": float(m.group("high")),
            "temp_unit": m.group("unit").upper(),
            "target_date": target_date,
        }

    # "X°F or below" pattern (number-first, actual Polymarket format)
    m = _BELOW_NUM_FIRST_RE.search(question)
    if m:
        city_raw = m.group("city")
        city = CITY_NAME_MAP.get(city_raw)
        if city is None:
            return None
        return {
            "city": city,
            "temp_low": -999.0,
            "temp_high": float(m.group("high")),
            "temp_unit": m.group("unit").upper(),
            "target_date": target_date,
        }

    return None


def parse_submarket_question(question: str) -> dict | None:
    """
    Parse a Polymarket sub-market temperature question that contains only a
    temperature range (no city or date), e.g. "58°F or higher", "56-57°F",
    "33°C or below", "14°C".

    Returns a dict with keys: temp_low, temp_high, temp_unit.
    Returns ``None`` if the question is not a recognisable sub-market format.
    """
    q = question.strip()

    m = _SUB_ABOVE_RE.match(q)
    if m:
        return {
            "temp_low": float(m.group("low")),
            "temp_high": 999.0,
            "temp_unit": m.group("unit").upper(),
        }

    m = _SUB_BELOW_RE.match(q)
    if m:
        return {
            "temp_low": -999.0,
            "temp_high": float(m.group("high")),
            "temp_unit": m.group("unit").upper(),
        }

    m = _SUB_RANGE_RE.match(q)
    if m:
        return {
            "temp_low": float(m.group("low")),
            "temp_high": float(m.group("high")),
            "temp_unit": m.group("unit").upper(),
        }

    m = _SUB_EXACT_RE.match(q)
    if m:
        temp = float(m.group("temp"))
        return {
            "temp_low": temp,
            "temp_high": temp,
            "temp_unit": m.group("unit").upper(),
        }

    return None


def parse_event_title(title: str) -> dict | None:
    """
    Parse a Polymarket event title of the form
    "Highest temperature in [City] on [Date]?"

    Returns a dict with keys: city, target_date.
    Returns ``None`` if the title is not a recognisable weather event title.
    """
    m = _EVENT_TITLE_CITY_RE.search(title)
    if not m:
        return None

    city_raw = m.group("city")
    city = CITY_NAME_MAP.get(city_raw)
    if city is None:
        return None

    target_date = _parse_date_from_question(title)
    if target_date is None:
        return None

    return {"city": city, "target_date": target_date}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _fetch_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any] | list[Any]:
    """GET *url* with exponential-backoff retries.  Returns parsed JSON."""
    backoff = INITIAL_BACKOFF
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Attempt %d/%d  GET %s  params=%s", attempt, max_retries, url, params)
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            logger.warning("Request failed (attempt %d/%d): %s", attempt, max_retries, exc)
            if attempt < max_retries:
                logger.info("Retrying in %.1f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff *= 2

    raise RuntimeError(f"All {max_retries} attempts failed for {url}") from last_exc


# ---------------------------------------------------------------------------
# Gamma API helpers
# ---------------------------------------------------------------------------


def _extract_tokens(market_data: dict[str, Any]) -> list[MarketToken]:
    """Pull MarketToken objects from a Gamma market dict.

    Supports two response shapes:
    - Old shape: ``tokens`` list with token_id/outcome/price dicts
    - New shape: parallel ``clobTokenIds``, ``outcomes``, ``outcomePrices`` lists
    """
    tokens: list[MarketToken] = []

    # New shape (actual Polymarket API response)
    # All three fields may arrive as JSON strings or native lists.
    clob_ids_raw = market_data.get("clobTokenIds") or []
    outcomes = market_data.get("outcomes")
    outcome_prices = market_data.get("outcomePrices")
    if clob_ids_raw and outcomes and outcome_prices:
        try:
            import json as _json
            clob_ids = clob_ids_raw if isinstance(clob_ids_raw, list) else _json.loads(clob_ids_raw)
            outcomes_list = outcomes if isinstance(outcomes, list) else _json.loads(outcomes)
            prices_list = outcome_prices if isinstance(outcome_prices, list) else _json.loads(outcome_prices)
            for token_id, outcome, price in zip(clob_ids, outcomes_list, prices_list):
                tokens.append(MarketToken(
                    token_id=str(token_id),
                    outcome=str(outcome),
                    price=float(price),
                ))
            return tokens
        except Exception:
            pass  # fall through to old shape

    # Old shape fallback
    for tok in market_data.get("tokens") or []:
        tokens.append(
            MarketToken(
                token_id=str(tok.get("token_id", "")),
                outcome=tok.get("outcome", ""),
                price=float(tok.get("price", 0.0)),
            )
        )
    return tokens


def _extract_yes_no_prices(tokens: list[MarketToken]) -> tuple[float, float]:
    """Return (yes_price, no_price) from token list."""
    yes_price = 0.0
    no_price = 0.0
    for t in tokens:
        if t.outcome.lower() == "yes":
            yes_price = t.price
        elif t.outcome.lower() == "no":
            no_price = t.price
    return yes_price, no_price


def _raw_market_to_temperature_market(
    raw: dict[str, Any],
    event_context: dict[str, Any] | None = None,  # {"city": ..., "target_date": ...}
) -> TemperatureMarket | None:
    """Convert a raw Gamma market dict into a TemperatureMarket.

    *event_context* carries the city + target_date extracted from the parent
    event title. The sub-market question is parsed for the temperature range;
    if sub-market parsing fails, falls back to ``parse_market_question()`` for
    mixed-format events.
    """
    question = raw.get("question", "")

    sub = parse_submarket_question(question)
    if sub is not None and event_context is not None:
        parsed = {
            "city": event_context["city"],
            "target_date": event_context["target_date"],
            "temp_low": sub["temp_low"],
            "temp_high": sub["temp_high"],
            "temp_unit": sub["temp_unit"],
        }
    else:
        parsed = parse_market_question(question)
        if parsed is None:
            return None

    tokens = _extract_tokens(raw)
    yes_price, no_price = _extract_yes_no_prices(tokens)

    description = raw.get("description", "")
    actual_station = extract_station_name(description)
    if actual_station:
        from bot.config import CITY_CONFIGS
        config_key = _INTERNAL_TO_CONFIG_KEY.get(parsed["city"])
        expected = CITY_CONFIGS.get(config_key, {}).get("station_name") if config_key else None
        if expected:
            exp_l = expected.lower()
            act_l = actual_station.lower()
            if exp_l not in act_l and act_l not in exp_l:
                logger.warning(
                    "Station mismatch for %s: market resolves on %r, config targets %r",
                    parsed["city"], actual_station, expected,
                )

    return TemperatureMarket(
        condition_id=str(raw.get("conditionId", raw.get("condition_id", ""))),
        question=question,
        city=parsed["city"],
        temp_low=parsed["temp_low"],
        temp_high=parsed["temp_high"],
        temp_unit=parsed["temp_unit"],
        target_date=parsed["target_date"],
        tokens=tokens,
        yes_price=yes_price,
        no_price=no_price,
        volume=float(raw.get("volume", 0.0)),
        active=bool(raw.get("active", True)),
        station_name=actual_station,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def scan_markets(
    cities: list[str] | None = None,
) -> dict[str, CityMarkets]:
    """
    Query the Gamma API for active temperature/weather markets and group
    them by city.

    Parameters
    ----------
    cities:
        Internal city keys to include (e.g. ``["new_york", "chicago"]``).
        ``None`` means accept all recognised cities.

    Returns
    -------
    dict mapping internal city key -> ``CityMarkets``.
    """
    city_filter: set[str] | None = set(cities) if cities else None

    seen_condition_ids: set[str] = set()
    all_temperature_markets: list[TemperatureMarket] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Primary path: paginate /events?tag_slug=temperature&active=true&closed=false
        # This is the only reliable Gamma API filter for daily temperature markets.
        offset = 0
        page_size = 100
        while True:
            params: dict[str, Any] = {
                "tag_slug": "temperature",
                "active": "true",
                "closed": "false",
                "limit": page_size,
                "offset": offset,
            }
            logger.info("Fetching temperature events (offset=%d)", offset)
            raw_events = await _fetch_with_retries(
                client, f"{GAMMA_BASE_URL}/events", params=params
            )
            if not isinstance(raw_events, list):
                raw_events = raw_events.get("data", raw_events.get("events", []))

            logger.info("Page returned %d events", len(raw_events))

            for event in raw_events:
                event_title = event.get("title", "")
                # Full market questions contain city+date+range — parse them directly.
                # parse_event_title is used only as a fallback for sub-market questions.
                event_ctx = parse_event_title(event_title)
                for raw in event.get("markets", []):
                    tm = _raw_market_to_temperature_market(raw, event_context=event_ctx)
                    if tm and tm.condition_id not in seen_condition_ids:
                        if city_filter is None or tm.city in city_filter:
                            seen_condition_ids.add(tm.condition_id)
                            all_temperature_markets.append(tm)

            if len(raw_events) < page_size:
                break  # last page
            offset += page_size
            await asyncio.sleep(REQUEST_DELAY)

    # 3. Group by city
    logger.info("Found %d unique temperature markets total", len(all_temperature_markets))
    grouped: dict[str, CityMarkets] = {}
    for tm in all_temperature_markets:
        if tm.city not in grouped:
            grouped[tm.city] = CityMarkets(city=tm.city)
        grouped[tm.city].markets.append(tm)

    # Sort each city's markets by temp_low for readability
    for cm in grouped.values():
        cm.markets.sort(key=lambda m: (m.target_date, m.temp_low))

    return grouped


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    logger.info("Scanning Polymarket for temperature markets ...")
    city_markets = await scan_markets()

    if not city_markets:
        print("\nNo temperature markets found.")
        return

    for city_key, cm in sorted(city_markets.items()):
        print(f"\n{'=' * 60}")
        print(f"  {city_key}  ({len(cm.markets)} market(s))")
        print(f"  Fetched at {cm.fetched_at.isoformat()}")
        print(f"{'=' * 60}")
        for mkt in cm.markets:
            high_display = f"{mkt.temp_high:.0f}" if mkt.temp_high < 900 else "+"
            print(
                f"  [{mkt.target_date}]  "
                f"{mkt.temp_low:.0f}–{high_display}°{mkt.temp_unit}  "
                f"YES={mkt.yes_price:.3f}  NO={mkt.no_price:.3f}  "
                f"vol={mkt.volume:,.0f}"
            )
            print(f"    Q: {mkt.question}")
            print(f"    condition_id: {mkt.condition_id}")


if __name__ == "__main__":
    asyncio.run(_main())
