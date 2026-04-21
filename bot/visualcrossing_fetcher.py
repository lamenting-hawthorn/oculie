"""
Visual Crossing weather source for cross-verification.

Provides a 4th independent forecast source for the consensus engine.
Like wttr.in, it returns a single deterministic high temperature per day
(no ensemble), contributing a point estimate only.

Requires a free API key from https://www.visualcrossing.com/ set as
VISUALCROSSING_API_KEY in the environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

VC_BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
FETCH_TIMEOUT_SECONDS = 15.0


@dataclass
class VCDayForecast:
    date: str  # YYYY-MM-DD
    max_temp_f: float
    max_temp_c: float


@dataclass
class VCCityForecast:
    city: str
    forecast_days: list[VCDayForecast]
    fetched_at: datetime


def _get_api_key() -> str | None:
    key = os.environ.get("VISUALCROSSING_API_KEY", "").strip()
    return key if key else None


async def fetch_forecast(
    city: str,
    client: httpx.AsyncClient | None = None,
) -> VCCityForecast | None:
    """Fetch next 3 days of high temps for *city*. Returns None on failure."""
    api_key = _get_api_key()
    if not api_key:
        logger.debug("VISUALCROSSING_API_KEY not set; skipping Visual Crossing fetch")
        return None

    url = f"{VC_BASE_URL}/{city}/next3days"
    params = {
        "unitGroup": "us",
        "include": "days",
        "key": api_key,
        "contentType": "json",
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()

    try:
        resp = await client.get(url, params=params, timeout=FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        days: list[VCDayForecast] = []
        for entry in data.get("days", []):
            temp_max_f = float(entry.get("tempmax", 0.0))
            temp_max_c = (temp_max_f - 32.0) * 5.0 / 9.0
            days.append(
                VCDayForecast(
                    date=entry["datetime"],
                    max_temp_f=temp_max_f,
                    max_temp_c=round(temp_max_c, 2),
                )
            )

        logger.info("Visual Crossing forecast for %s: %d days", city, len(days))
        return VCCityForecast(
            city=city,
            forecast_days=days,
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.warning("Visual Crossing fetch failed for %s: %s", city, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


async def fetch_forecasts(cities: list[str]) -> dict[str, VCCityForecast]:
    """Fetch forecasts for multiple cities concurrently. Skips failures."""
    if not _get_api_key():
        logger.info("VISUALCROSSING_API_KEY not set; skipping all Visual Crossing fetches")
        return {}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(fetch_forecast(city, client) for city in cities),
            return_exceptions=True,
        )

    out: dict[str, VCCityForecast] = {}
    for city, result in zip(cities, results):
        if isinstance(result, VCCityForecast):
            out[city] = result
        elif isinstance(result, Exception):
            logger.warning("Visual Crossing fetch raised for %s: %s", city, result)

    logger.info("Visual Crossing fetch complete for %d/%d cities", len(out), len(cities))
    return out
