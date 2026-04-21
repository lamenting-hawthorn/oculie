"""
Fetch and parse NOAA/NWS forecast data for US cities.

The NOAA Weather API (api.weather.gov) is free, requires no API key,
but mandates a descriptive User-Agent header.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = "Polymarket Weather Agent-Weather-Bot/1.0 (contact@openclaw.dev)"
BASE_URL = "https://api.weather.gov/gridpoints"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

# ---------------------------------------------------------------------------
# City grid-point configurations
# ---------------------------------------------------------------------------

CITY_CONFIGS: dict[str, dict[str, Any]] = {
    "New York City": {"office": "OKX", "gridX": 37, "gridY": 39},
    "Chicago":       {"office": "LOT", "gridX": 66, "gridY": 77},
    "Miami":         {"office": "MFL", "gridX": 106, "gridY": 51},
    "Dallas":        {"office": "FWD", "gridX": 87, "gridY": 107},
    "Seattle":       {"office": "SEW", "gridX": 124, "gridY": 61},
    "Atlanta":       {"office": "FFC", "gridX": 50, "gridY": 82},
}

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ForecastPeriod(BaseModel):
    name: str
    temperature: int
    temperatureUnit: str = "F"
    shortForecast: str
    detailedForecast: str
    startTime: str
    endTime: str
    isDaytime: bool


class CityForecast(BaseModel):
    city: str
    station: str
    periods: list[ForecastPeriod]
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TemperatureBucket(BaseModel):
    low: float
    high: float
    probability: float


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _fetch_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """GET *url* with exponential-backoff retries. Returns parsed JSON."""
    backoff = INITIAL_BACKOFF
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Attempt %d/%d  GET %s", attempt, max_retries, url)
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            logger.warning(
                "Request failed (attempt %d/%d): %s", attempt, max_retries, exc
            )
            if attempt < max_retries:
                logger.info("Retrying in %.1f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff *= 2

    raise RuntimeError(
        f"All {max_retries} attempts failed for {url}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------


async def _fetch_city_forecast(
    client: httpx.AsyncClient,
    city: str,
    config: dict[str, Any],
) -> CityForecast:
    """Fetch the 7-day forecast for a single city."""
    office = config["office"]
    grid_x = config["gridX"]
    grid_y = config["gridY"]
    url = f"{BASE_URL}/{office}/{grid_x},{grid_y}/forecast"

    logger.info("Fetching forecast for %s from %s", city, url)
    data = await _fetch_with_retries(client, url)

    raw_periods = data.get("properties", {}).get("periods", [])
    periods = [
        ForecastPeriod(
            name=p["name"],
            temperature=p["temperature"],
            temperatureUnit=p.get("temperatureUnit", "F"),
            shortForecast=p["shortForecast"],
            detailedForecast=p.get("detailedForecast", ""),
            startTime=p["startTime"],
            endTime=p["endTime"],
            isDaytime=p["isDaytime"],
        )
        for p in raw_periods
    ]

    logger.info("Parsed %d forecast periods for %s", len(periods), city)
    return CityForecast(
        city=city,
        station=f"{office}/{grid_x},{grid_y}",
        periods=periods,
    )


async def fetch_forecasts(
    cities: list[str] | None = None,
) -> dict[str, CityForecast]:
    """
    Fetch forecasts for the requested cities (default: all configured cities).

    Parameters
    ----------
    cities:
        City names to fetch. ``None`` means all cities in ``CITY_CONFIGS``.

    Returns
    -------
    dict mapping city name -> ``CityForecast``
    """
    if cities is None:
        cities = list(CITY_CONFIGS.keys())

    unknown = [c for c in cities if c not in CITY_CONFIGS]
    if unknown:
        raise ValueError(f"Unknown city/cities: {unknown}")

    results: dict[str, CityForecast] = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        tasks = {
            city: asyncio.create_task(
                _fetch_city_forecast(client, city, CITY_CONFIGS[city])
            )
            for city in cities
        }
        for city, task in tasks.items():
            try:
                results[city] = await task
            except Exception:
                logger.exception("Failed to fetch forecast for %s", city)

    return results


# ---------------------------------------------------------------------------
# Probability distribution
# ---------------------------------------------------------------------------


def _days_out(forecast: CityForecast, target_date: str) -> int:
    """Return how many days *target_date* is from the forecast fetch time."""
    target = datetime.strptime(target_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )
    delta = (target.date() - forecast.fetched_at.date()).days
    return max(delta, 0)


def calculate_probability_distribution(
    forecast: CityForecast,
    target_date: str,
    ensemble_sigma: float | None = None,
) -> list[TemperatureBucket]:
    """
    Build a discrete probability distribution over 5 degF buckets for the
    daytime high on *target_date*.

    Uncertainty is modelled as a normal distribution centred on the NWS point
    forecast with standard deviation that grows with lead time:
      - Day 0-1: 3 degF
      - Day 2+:  5 degF

    Parameters
    ----------
    forecast:
        A ``CityForecast`` previously fetched from the NWS API.
    target_date:
        ISO date string, e.g. ``"2026-03-25"``.
    ensemble_sigma:
        Optional measured spread (in degF) from an external ensemble forecast.
        When provided (and positive), it overrides the hardcoded lead-time
        sigma — useful when a more accurate uncertainty estimate is available
        from an ensemble forecast (e.g., Open-Meteo GFS spread). This yields a
        regime-aware distribution (tight for stable ridges, wide for frontal
        passages) instead of a one-size-fits-all parametric value.

    Returns
    -------
    List of ``TemperatureBucket`` objects covering the plausible range.
    """
    # Find relevant period(s) for target_date (prefer daytime high)
    matching_periods: list[ForecastPeriod] = []
    for period in forecast.periods:
        period_date = period.startTime[:10]  # "YYYY-MM-DD"
        if period_date == target_date:
            matching_periods.append(period)

    if not matching_periods:
        # Fall back to the nearest available date
        all_dates = sorted({p.startTime[:10] for p in forecast.periods})
        if not all_dates:
            logger.warning("No forecast periods at all for %s", forecast.city)
            return []
        before = [d for d in all_dates if d <= target_date]
        nearest_date = before[-1] if before else all_dates[-1]
        logger.warning(
            "No forecast periods for %s on %s — using nearest date %s instead",
            forecast.city, target_date, nearest_date,
        )
        matching_periods = [p for p in forecast.periods if p.startTime[:10] == nearest_date]

    # Prefer the daytime period; fall back to whatever is available
    daytime = [p for p in matching_periods if p.isDaytime]
    chosen = daytime[0] if daytime else matching_periods[0]
    point_temp = chosen.temperature

    days = _days_out(forecast, target_date)
    if ensemble_sigma is not None and ensemble_sigma > 0:
        std_dev = ensemble_sigma
    else:
        std_dev = 3.0 if days <= 1 else 5.0
    logger.info(
        "City=%s target=%s temp=%d°F days_out=%d std=%.2f (source=%s)",
        forecast.city,
        target_date,
        int(point_temp),
        days,
        std_dev,
        "ensemble" if ensemble_sigma is not None else "parametric",
    )

    dist = norm(loc=point_temp, scale=std_dev)

    # Build 5°F buckets spanning +/- 4 standard deviations
    bucket_size = 5
    low_bound = bucket_size * math.floor((point_temp - 4 * std_dev) / bucket_size)
    high_bound = bucket_size * math.ceil((point_temp + 4 * std_dev) / bucket_size)

    buckets: list[TemperatureBucket] = []
    current = low_bound
    while current < high_bound:
        prob = float(dist.cdf(current + bucket_size) - dist.cdf(current))
        if prob > 1e-6:
            buckets.append(
                TemperatureBucket(
                    low=current, high=current + bucket_size, probability=round(prob, 6)
                )
            )
        current += bucket_size

    # Normalize so probabilities sum to exactly 1.0
    # (matches Open-Meteo's explicit normalization)
    total = sum(b.probability for b in buckets)
    if total > 0 and abs(total - 1.0) > 1e-6:
        for b in buckets:
            b.probability = round(b.probability / total, 6)

    return buckets


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    logger.info("Fetching forecasts for all configured cities ...")
    forecasts = await fetch_forecasts()

    for city, fc in forecasts.items():
        print(f"\n{'=' * 60}")
        print(f"  {city}  (station {fc.station})")
        print(f"  Fetched at {fc.fetched_at.isoformat()}")
        print(f"{'=' * 60}")
        for p in fc.periods[:4]:  # first 4 periods for brevity
            print(
                f"  {p.name:<20s}  {p.temperature:>3d}°{p.temperatureUnit}  "
                f"{p.shortForecast}"
            )

        # Show probability distribution for the first daytime period's date
        daytime_periods = [p for p in fc.periods if p.isDaytime]
        if daytime_periods:
            target = daytime_periods[0].startTime[:10]
            buckets = calculate_probability_distribution(fc, target)
            if buckets:
                print(f"\n  Probability distribution for {target}:")
                for b in buckets:
                    bar = "#" * int(b.probability * 80)
                    print(
                        f"    {b.low:>3d}–{b.high:<3d}°F  "
                        f"{b.probability:6.2%}  {bar}"
                    )


if __name__ == "__main__":
    asyncio.run(_main())
