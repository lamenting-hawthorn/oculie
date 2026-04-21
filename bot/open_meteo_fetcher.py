"""
Open-Meteo forecast fetcher for weather cities.

Uses two Open-Meteo APIs (no API key required):
  • https://api.open-meteo.com/v1/forecast  — deterministic daily high
  • https://ensemble-api.open-meteo.com/v1/ensemble — GFS (31) and
    optional ECMWF (50) ensemble members

Probability distributions are derived by counting how many ensemble members
fall in each temperature bucket, giving a data-driven alternative to the
earlier normal-distribution approximation.

The public interface:
    fetch_forecasts(cities, *, include_ecmwf=False)
        → dict[str, InternationalCityForecast]
    calculate_probability_distribution(forecast, target_date)
        → list[TemperatureBucket]
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from bot.config import CITY_CONFIGS
from bot.noaa_fetcher import TemperatureBucket as TemperatureBucket  # re-export shared type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

ECMWF_ENSEMBLE_MODEL = "ecmwf_ifs025"

# Cities this fetcher can serve — sourced from the single source of truth
# in bot.config.CITY_CONFIGS. Includes both US and international cities so
# the consensus engine can access ensemble spreads for every location.
CITIES: dict[str, dict] = {name: cfg for name, cfg in CITY_CONFIGS.items()}

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EnsembleForecastDay(BaseModel):
    date: str  # YYYY-MM-DD
    temp_max_p50: float  # deterministic high from primary forecast API
    ensemble_temp_max: list[float]  # GFS ensemble members (typically 31)
    ensemble_temp_max_ecmwf: list[float] | None = None  # ECMWF members (50), if requested


class InternationalCityForecast(BaseModel):
    city: str
    unit: str = "C"  # "C" or "F"
    forecast_days: list[EnsembleForecastDay]
    fetched_at: datetime


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    label: str = "",
) -> dict[str, Any]:
    """GET *url* with exponential-backoff retries. Returns parsed JSON dict."""
    backoff = INITIAL_BACKOFF
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("Attempt %d/%d %s %s", attempt, MAX_RETRIES, label, url)
            resp = await client.get(url, params=params, timeout=20.0)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            logger.warning(
                "%s request failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, MAX_RETRIES, exc, backoff,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2

    raise RuntimeError(
        f"All {MAX_RETRIES} attempts failed for {label}: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Per-city fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_deterministic(
    client: httpx.AsyncClient,
    city: str,
    cfg: dict,
) -> dict[str, float]:
    """Fetch deterministic daily max temperature from the main forecast API.

    Returns a dict of {date_str: temp_max}.
    """
    params = {
        "latitude": cfg["latitude"],
        "longitude": cfg["longitude"],
        "daily": "temperature_2m_max",
        "forecast_days": 2,
        "timezone": "auto",
        "temperature_unit": cfg["temp_unit"],
    }
    data = await _get_with_retries(client, FORECAST_URL, params, label=f"forecast/{city}")
    daily = data.get("daily", {})
    dates: list[str] = daily.get("time", [])
    temps: list[float | None] = daily.get("temperature_2m_max", [])

    result: dict[str, float] = {}
    for date, temp in zip(dates, temps):
        if temp is not None:
            result[date] = float(temp)

    logger.info("Deterministic forecast for %s: %s", city, result)
    return result


async def _fetch_ensemble(
    client: httpx.AsyncClient,
    city: str,
    cfg: dict,
    model: str,
) -> dict[str, list[float]]:
    """Fetch ensemble daily max temperatures for a specific ensemble *model*.

    Returns a dict of {date_str: [member_temp, ...]}.
    """
    params = {
        "latitude": cfg["latitude"],
        "longitude": cfg["longitude"],
        "daily": "temperature_2m_max",
        "models": model,
        "forecast_days": 2,
        "temperature_unit": cfg["temp_unit"],
    }
    data = await _get_with_retries(
        client, ENSEMBLE_URL, params, label=f"ensemble/{model}/{city}"
    )
    daily = data.get("daily", {})
    dates: list[str] = daily.get("time", [])

    member_keys = sorted(
        k for k in daily if k.startswith("temperature_2m_max_member")
    )

    if not member_keys:
        logger.warning(
            "No ensemble members found for %s (%s) — using empty list", city, model
        )
        return {d: [] for d in dates}

    result: dict[str, list[float]] = {d: [] for d in dates}
    for key in member_keys:
        member_vals: list[float | None] = daily[key]
        for date, val in zip(dates, member_vals):
            if val is not None:
                result[date].append(float(val))

    logger.info(
        "Ensemble (%s) for %s: %d dates × %d members",
        model, city, len(dates), len(member_keys),
    )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_forecasts(
    cities: list[str] | None = None,
    *,
    include_ecmwf: bool = False,
) -> dict[str, InternationalCityForecast]:
    """Fetch Open-Meteo deterministic + ensemble forecasts for the given cities.

    Parameters
    ----------
    cities:
        City names to fetch. If *None*, fetches all configured cities.
    include_ecmwf:
        If True, also fetch the ECMWF (``ecmwf_ifs025``) ensemble
        (~50 members) alongside the primary GFS ensemble and expose it
        via ``EnsembleForecastDay.ensemble_temp_max_ecmwf``.

    Returns
    -------
    dict mapping city name -> InternationalCityForecast
    """
    targets = cities or list(CITIES.keys())
    unknown = [c for c in targets if c not in CITIES]
    if unknown:
        raise ValueError(f"Unknown city names for Open-Meteo fetcher: {unknown}")

    results: dict[str, InternationalCityForecast] = {}

    async with httpx.AsyncClient() as client:
        for city in targets:
            cfg = CITIES[city]
            logger.info("Fetching Open-Meteo data for %s", city)

            # Use a dedicated ensemble_model if defined (some regional models
            # like ukmo_seamless / kma_seamless are deterministic-only; fall
            # back to the primary_model which for US cities is gfs_seamless).
            gfs_model = cfg.get("ensemble_model") or cfg["primary_model"]

            try:
                tasks: list[Any] = [
                    _fetch_deterministic(client, city, cfg),
                    _fetch_ensemble(client, city, cfg, gfs_model),
                ]
                if include_ecmwf:
                    tasks.append(
                        _fetch_ensemble(client, city, cfg, ECMWF_ENSEMBLE_MODEL)
                    )
                fetched = await asyncio.gather(*tasks)
            except Exception as exc:
                logger.error("Failed to fetch Open-Meteo data for %s: %s", city, exc)
                continue

            det: dict[str, float] = fetched[0]
            ens: dict[str, list[float]] = fetched[1]
            ecmwf: dict[str, list[float]] | None = fetched[2] if include_ecmwf else None

            all_dates = sorted(set(det) | set(ens) | (set(ecmwf) if ecmwf else set()))
            forecast_days: list[EnsembleForecastDay] = []
            for date in all_dates:
                temp_p50 = det.get(date, 0.0)
                members = ens.get(date, [])
                ecmwf_members = ecmwf.get(date, []) if ecmwf is not None else None
                forecast_days.append(
                    EnsembleForecastDay(
                        date=date,
                        temp_max_p50=temp_p50,
                        ensemble_temp_max=members,
                        ensemble_temp_max_ecmwf=ecmwf_members,
                    )
                )

            unit = "F" if cfg["temp_unit"] == "fahrenheit" else "C"
            results[city] = InternationalCityForecast(
                city=city,
                unit=unit,
                forecast_days=forecast_days,
                fetched_at=datetime.now(timezone.utc),
            )

    logger.info("Open-Meteo fetch complete for %d cities", len(results))
    return results


# ---------------------------------------------------------------------------
# Probability distribution
# ---------------------------------------------------------------------------


def calculate_probability_distribution(
    forecast: InternationalCityForecast,
    target_date: str,
) -> list[TemperatureBucket]:
    """Build a temperature probability distribution for *target_date*.

    Uses ensemble member counts to derive the probability of each temperature
    bucket, giving a data-driven distribution rather than a parametric one.

    If the ensemble is unavailable (empty member list), falls back to a
    uniform spike at the deterministic p50 temperature.

    Bucket width is 1° (°C or °F) so the distribution maps cleanly onto
    any Polymarket range bucket.

    Parameters
    ----------
    forecast:
        InternationalCityForecast returned by fetch_forecasts().
    target_date:
        ISO date string (YYYY-MM-DD).

    Returns
    -------
    list[TemperatureBucket] sorted by ascending temperature, probabilities
    summing to 1.0.
    """
    day_match: EnsembleForecastDay | None = None
    for day in forecast.forecast_days:
        if day.date == target_date:
            day_match = day
            break

    if day_match is None:
        available = sorted(forecast.forecast_days, key=lambda d: d.date)
        if not available:
            raise ValueError(
                f"No forecast data at all for {forecast.city}"
            )
        before = [d for d in available if d.date <= target_date]
        day_match = before[-1] if before else available[-1]
        logger.warning(
            "No forecast entry for %s in %s data — using nearest date %s instead",
            target_date, forecast.city, day_match.date,
        )

    members = day_match.ensemble_temp_max
    bucket_width = CITIES.get(forecast.city, {}).get("bucket_width", 1.0)

    if not members:
        logger.warning(
            "No ensemble members for %s on %s — using p50 spike",
            forecast.city, target_date,
        )
        t = day_match.temp_max_p50
        lo = math.floor(t / bucket_width) * bucket_width
        return [TemperatureBucket(low=lo, high=lo + bucket_width, probability=1.0)]

    min_temp = min(members)
    max_temp = max(members)
    range_low = math.floor(min_temp / bucket_width) * bucket_width
    range_high = math.ceil(max_temp / bucket_width) * bucket_width

    if range_high <= range_low:
        range_high = range_low + bucket_width

    n_members = len(members)
    buckets: list[TemperatureBucket] = []

    edge = range_low
    while edge < range_high:
        lo = edge
        hi = lo + bucket_width
        count = sum(1 for m in members if lo <= m < hi)
        if hi >= range_high:
            count = sum(1 for m in members if lo <= m <= hi)
        prob = count / n_members
        if prob > 0:
            buckets.append(TemperatureBucket(low=lo, high=hi, probability=round(prob, 6)))
        edge = round(edge + bucket_width, 10)

    total = sum(b.probability for b in buckets)
    if total > 0 and abs(total - 1.0) > 1e-6:
        for b in buckets:
            b.probability = round(b.probability / total, 6)

    logger.debug(
        "Distribution for %s on %s: %d members → %d buckets",
        forecast.city, target_date, n_members, len(buckets),
    )
    return buckets


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    print("Fetching Open-Meteo forecasts for all configured cities...\n")
    forecasts = await fetch_forecasts()

    if not forecasts:
        print("No forecasts retrieved.")
        return

    for city, fc in sorted(forecasts.items()):
        print(f"{'=' * 60}")
        print(f"  {city} (unit: °{fc.unit})")
        print(f"  Fetched at: {fc.fetched_at.isoformat()}")
        for day in fc.forecast_days:
            members = day.ensemble_temp_max
            if members:
                spread = f"ensemble: {min(members):.1f}–{max(members):.1f} °{fc.unit}"
            else:
                spread = "ensemble: n/a"
            print(
                f"  [{day.date}] p50={day.temp_max_p50:.1f}°{fc.unit}  {spread}"
                f"  ({len(members)} members)"
            )

        if fc.forecast_days:
            first_date = fc.forecast_days[0].date
            buckets = calculate_probability_distribution(fc, first_date)
            print(f"\n  Probability distribution for {first_date} (1° buckets):")
            for b in buckets:
                bar = "#" * int(b.probability * 60)
                print(
                    f"    {b.low:6.1f}–{b.high:6.1f}°{fc.unit}: "
                    f"{b.probability:.4f} {bar}"
                )
        print()


if __name__ == "__main__":
    asyncio.run(_main())
