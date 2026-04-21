"""
Historical temperature lookup via Weather Underground for calibration.

Polymarket temperature markets resolve using Wunderground station data.
After resolution, this fetcher retrieves the actual high temperature to
compare against our forecast — feeding the calibration dashboard.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT_SECONDS = 15.0

API_BASE = "https://api.weather.com/v1/location"
WU_HISTORY_BASE = "https://www.wunderground.com/history/daily"
FALLBACK_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

_API_KEY_PATTERN = re.compile(r"apiKey[\"':= ]+([a-f0-9]{32})")


@dataclass
class HistoricalHigh:
    station: str
    date: str
    max_temp_c: float | None
    max_temp_f: float | None
    source: str


def _country_from_path(wunderground_path: str) -> str:
    """Extract the 2-letter country code from a Wunderground history path.

    Example: "us/ny/new-york-city/KLGA" -> "US"
             "kr/incheon/RKSI"          -> "KR"
    """
    parts = [p for p in wunderground_path.strip("/").split("/") if p]
    if not parts:
        raise ValueError(f"Empty wunderground_path: {wunderground_path!r}")
    return parts[0].upper()


def _f_to_c(f: float) -> float:
    return round((f - 32.0) * 5.0 / 9.0, 2)


async def _extract_api_key(
    client: httpx.AsyncClient, wunderground_path: str, date: str
) -> str:
    """Scrape the current Wunderground API key from the history HTML page.

    Falls back to a known-good key if extraction fails.
    """
    url = f"{WU_HISTORY_BASE}/{wunderground_path}/date/{date}"
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        match = _API_KEY_PATTERN.search(resp.text)
        if match:
            return match.group(1)
        logger.warning("Could not extract apiKey from %s — using fallback", url)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("Failed to fetch %s for apiKey extraction: %s", url, exc)
    return FALLBACK_API_KEY


async def fetch_historical_high(
    station_icao: str,
    date: str,
    wunderground_path: str,
    client: httpx.AsyncClient | None = None,
) -> HistoricalHigh | None:
    """Fetch the max temp recorded at *station_icao* on *date*.

    Parameters
    ----------
    station_icao:
        ICAO station code, e.g. "KLGA".
    date:
        Target date as "YYYY-MM-DD".
    wunderground_path:
        Station path on wunderground.com history URLs, e.g.
        "us/ny/new-york-city/KLGA" or "kr/incheon/RKSI".
    client:
        Optional httpx.AsyncClient. A fresh one is created if omitted.

    Returns
    -------
    HistoricalHigh or None. Returns None if the date is not yet resolved
    (no observations, API error, or all temps null).
    """
    try:
        country = _country_from_path(wunderground_path)
    except ValueError as exc:
        logger.warning("Invalid wunderground_path %r: %s", wunderground_path, exc)
        return None

    compact_date = date.replace("-", "")
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    try:
        api_key = await _extract_api_key(client, wunderground_path, date)

        api_url = (
            f"{API_BASE}/{station_icao}:9:{country}/observations/historical.json"
            f"?apiKey={api_key}&units=e&startDate={compact_date}&endDate={compact_date}"
        )
        logger.info(
            "Fetching historical high for %s on %s (country=%s)",
            station_icao, date, country,
        )
        try:
            resp = await client.get(api_url, headers={"User-Agent": USER_AGENT})
        except httpx.RequestError as exc:
            logger.warning("HTTP error fetching %s: %s", api_url, exc)
            return None

        if resp.status_code == 400:
            logger.info(
                "No data for %s on %s yet (status 400 — not resolved)",
                station_icao, date,
            )
            return None
        if resp.status_code != 200:
            logger.warning(
                "Unexpected status %d for %s on %s",
                resp.status_code, station_icao, date,
            )
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning("Failed to parse JSON for %s on %s: %s",
                           station_icao, date, exc)
            return None

        if not data.get("observations"):
            logger.info("No observations for %s on %s", station_icao, date)
            return None

        observations = data["observations"]

        summary_maxes = [
            o["max_temp"] for o in observations
            if o.get("max_temp") is not None
        ]
        if summary_maxes:
            max_f = float(max(summary_maxes))
        else:
            hourly_temps = [
                o["temp"] for o in observations
                if o.get("temp") is not None
            ]
            if not hourly_temps:
                logger.info(
                    "All temp fields null for %s on %s — not finalized",
                    station_icao, date,
                )
                return None
            max_f = float(max(hourly_temps))

        return HistoricalHigh(
            station=station_icao,
            date=date,
            max_temp_f=max_f,
            max_temp_c=_f_to_c(max_f),
            source="api.weather.com",
        )
    finally:
        if owns_client:
            await client.aclose()


async def _main() -> None:
    import asyncio  # noqa: F401

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    result = await fetch_historical_high(
        "KLGA", "2026-04-15", "us/ny/new-york-city/KLGA"
    )
    print(result)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
