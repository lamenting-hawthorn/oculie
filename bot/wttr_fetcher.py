"""
Tertiary weather source via wttr.in for cross-verification.

Used by the consensus engine as a sanity check — wttr.in returns a single
deterministic high temperature per day (no ensemble), so it contributes
a point estimate only, not uncertainty.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

WTTR_BASE_URL = "https://wttr.in"
FETCH_TIMEOUT_SECONDS = 15.0


@dataclass
class WttrDayForecast:
    date: str  # YYYY-MM-DD
    max_temp_f: float
    max_temp_c: float


@dataclass
class WttrCityForecast:
    city: str
    forecast_days: list[WttrDayForecast]
    fetched_at: datetime


async def fetch_forecast(
    city: str,
    client: httpx.AsyncClient | None = None,
) -> WttrCityForecast | None:
    """Fetch next 3 days of high temps for *city*. Returns None on failure."""
    # wttr.in silently drops the path segment after a space, so any multi-word
    # city (e.g. "Hong Kong") must be percent-encoded before hitting the URL.
    url = f"{WTTR_BASE_URL}/{quote(city)}"
    params = {"format": "j1"}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()

    try:
        resp = await client.get(url, params=params, timeout=FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        days: list[WttrDayForecast] = []
        for entry in data.get("weather", []):
            days.append(
                WttrDayForecast(
                    date=entry["date"],
                    max_temp_f=float(entry["maxtempF"]),
                    max_temp_c=float(entry["maxtempC"]),
                )
            )

        logger.info("wttr.in forecast for %s: %d days", city, len(days))
        return WttrCityForecast(
            city=city,
            forecast_days=days,
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("wttr.in fetch failed for %s: %s", city, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


async def fetch_forecasts(cities: list[str]) -> dict[str, WttrCityForecast]:
    """Fetch forecasts for multiple cities concurrently. Skips failures."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(fetch_forecast(city, client) for city in cities),
            return_exceptions=True,
        )

    out: dict[str, WttrCityForecast] = {}
    for city, result in zip(cities, results):
        if isinstance(result, WttrCityForecast):
            out[city] = result
        elif isinstance(result, Exception):
            logger.warning("wttr.in fetch raised for %s: %s", city, result)

    logger.info("wttr.in fetch complete for %d/%d cities", len(out), len(cities))
    return out
