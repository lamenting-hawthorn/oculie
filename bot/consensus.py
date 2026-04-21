"""
Multi-source weather consensus for trade gating.

Blends NOAA (US only), Open-Meteo ensemble (GFS + optional ECMWF, all cities),
and wttr.in point forecasts into a single ConsensusForecast per city/date.

A `sources_agree` flag is adaptive to source count:
  - 3+ sources: disagreement threshold = 3 deg F (1.67 deg C)
  - 2 sources:  disagreement threshold = 5 deg F (2.78 deg C)
  - 1 source:   sources_agree = False (single point of failure)

The trade engine gates on this flag to avoid betting when the underlying
forecasts diverge -- e.g., the Chicago storm-suppression case where NOAA said
70F but Open-Meteo and wttr.in said 78-81F.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from bot.config import CITY_CONFIGS
from bot.noaa_fetcher import CityForecast as NoaaForecast
from bot.open_meteo_fetcher import InternationalCityForecast as OmForecast
from bot.visualcrossing_fetcher import VCCityForecast
from bot.wttr_fetcher import WttrCityForecast

logger = logging.getLogger(__name__)

# Default sigma assumed for point-only sources (wttr.in) so they contribute
# partial weight to the inverse-variance blend instead of dominating.
_WTTR_DEFAULT_SIGMA_F = 3.0
_WTTR_DEFAULT_SIGMA_C = 1.67


@dataclass
class SourceEstimate:
    source: str          # "noaa" | "open_meteo_gfs" | "open_meteo_ecmwf" | "wttr"
    point_estimate: float
    std_dev: float | None  # None for point-only sources like wttr
    unit: str            # "F" or "C"


@dataclass
class ConsensusForecast:
    city: str
    target_date: str
    unit: str
    point_estimate: float     # Inverse-variance weighted mean
    std_dev: float            # Blended sigma
    source_spread: float      # max point - min point (same unit)
    source_count: int
    sources_agree: bool
    per_source: list[SourceEstimate] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _disagreement_threshold(source_count: int, unit: str) -> float:
    """Adaptive threshold scaling with source count."""
    if source_count >= 3:
        return 3.0 if unit == "F" else 1.67
    return 5.0 if unit == "F" else 2.78


def _city_unit(city: str) -> str:
    """Return 'F' or 'C' for *city* per CITY_CONFIGS, defaulting to 'C'."""
    cfg = CITY_CONFIGS.get(city, {})
    return "F" if cfg.get("temp_unit") == "fahrenheit" else "C"


def _f_to_c(temp: float) -> float:
    return (temp - 32.0) * 5.0 / 9.0


def _c_to_f(temp: float) -> float:
    return temp * 9.0 / 5.0 + 32.0


def _convert(temp: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return temp
    if from_unit == "F" and to_unit == "C":
        return _f_to_c(temp)
    return _c_to_f(temp)


def _extract_noaa(
    forecast: NoaaForecast,
    target_date: str,
    target_unit: str,
    noaa_sigma: float | None,
) -> SourceEstimate | None:
    """Pick the daytime period matching target_date and emit a SourceEstimate."""
    matching = [p for p in forecast.periods if p.startTime[:10] == target_date]
    if not matching:
        return None
    daytime = [p for p in matching if p.isDaytime]
    chosen = daytime[0] if daytime else matching[0]

    src_unit = chosen.temperatureUnit or "F"
    point = _convert(float(chosen.temperature), src_unit, target_unit)

    # Default NOAA sigma scales with lead time when caller did not supply one.
    if noaa_sigma is None:
        try:
            target = datetime.strptime(target_date, "%Y-%m-%d").date()
            days_out = max((target - forecast.fetched_at.date()).days, 0)
        except Exception:
            days_out = 1
        sigma_native = 3.0 if days_out <= 1 else 5.0
    else:
        sigma_native = noaa_sigma

    # noaa_sigma is supplied in F; convert to target unit by scale only.
    sigma = sigma_native if target_unit == "F" else sigma_native * 5.0 / 9.0

    return SourceEstimate(
        source="noaa",
        point_estimate=point,
        std_dev=sigma,
        unit=target_unit,
    )


def _extract_open_meteo(
    forecast: OmForecast,
    target_date: str,
    target_unit: str,
) -> list[SourceEstimate]:
    """Emit GFS + (optional) ECMWF SourceEstimates for target_date."""
    day_match = next(
        (d for d in forecast.forecast_days if d.date == target_date),
        None,
    )
    if day_match is None:
        return []

    estimates: list[SourceEstimate] = []
    src_unit = forecast.unit  # "F" or "C"

    gfs_members = day_match.ensemble_temp_max
    if gfs_members:
        gfs_median = float(np.median(gfs_members))
        gfs_sigma = float(np.std(gfs_members, ddof=0))
        estimates.append(
            SourceEstimate(
                source="open_meteo_gfs",
                point_estimate=_convert(gfs_median, src_unit, target_unit),
                std_dev=gfs_sigma if src_unit == target_unit else gfs_sigma * (5.0 / 9.0 if src_unit == "F" else 9.0 / 5.0),
                unit=target_unit,
            )
        )
    elif day_match.temp_max_p50:
        # Fall back to the deterministic p50 with no sigma if ensemble missing.
        estimates.append(
            SourceEstimate(
                source="open_meteo_gfs",
                point_estimate=_convert(float(day_match.temp_max_p50), src_unit, target_unit),
                std_dev=None,
                unit=target_unit,
            )
        )

    ecmwf_members = day_match.ensemble_temp_max_ecmwf
    if ecmwf_members:
        ecmwf_median = float(np.median(ecmwf_members))
        ecmwf_sigma = float(np.std(ecmwf_members, ddof=0))
        estimates.append(
            SourceEstimate(
                source="open_meteo_ecmwf",
                point_estimate=_convert(ecmwf_median, src_unit, target_unit),
                std_dev=ecmwf_sigma if src_unit == target_unit else ecmwf_sigma * (5.0 / 9.0 if src_unit == "F" else 9.0 / 5.0),
                unit=target_unit,
            )
        )

    return estimates


def _extract_wttr(
    forecast: WttrCityForecast,
    target_date: str,
    target_unit: str,
) -> SourceEstimate | None:
    """Pull the matching day's max temp in the target unit."""
    day_match = next(
        (d for d in forecast.forecast_days if d.date == target_date),
        None,
    )
    if day_match is None:
        return None

    point = day_match.max_temp_f if target_unit == "F" else day_match.max_temp_c
    return SourceEstimate(
        source="wttr",
        point_estimate=float(point),
        std_dev=None,
        unit=target_unit,
    )


def _extract_vc(
    forecast: VCCityForecast,
    target_date: str,
    target_unit: str,
) -> SourceEstimate | None:
    """Pull the matching day's max temp in the target unit."""
    day_match = next(
        (d for d in forecast.forecast_days if d.date == target_date),
        None,
    )
    if day_match is None:
        return None

    point = day_match.max_temp_f if target_unit == "F" else day_match.max_temp_c
    return SourceEstimate(
        source="visualcrossing",
        point_estimate=float(point),
        std_dev=None,
        unit=target_unit,
    )


def compute_consensus(
    city: str,
    target_date: str,
    *,
    noaa: NoaaForecast | None = None,
    open_meteo: OmForecast | None = None,
    wttr: WttrCityForecast | None = None,
    visualcrossing: VCCityForecast | None = None,
    noaa_sigma: float | None = None,
) -> ConsensusForecast | None:
    """Blend available sources into a single consensus forecast.

    Returns None if no sources provided any data for target_date.
    """
    target_unit = _city_unit(city)
    sources: list[SourceEstimate] = []

    if noaa is not None:
        try:
            est = _extract_noaa(noaa, target_date, target_unit, noaa_sigma)
            if est is not None:
                sources.append(est)
        except Exception:
            logger.exception("Failed to extract NOAA estimate for %s on %s", city, target_date)

    if open_meteo is not None:
        try:
            sources.extend(_extract_open_meteo(open_meteo, target_date, target_unit))
        except Exception:
            logger.exception("Failed to extract Open-Meteo estimates for %s on %s", city, target_date)

    if wttr is not None:
        try:
            est = _extract_wttr(wttr, target_date, target_unit)
            if est is not None:
                sources.append(est)
        except Exception:
            logger.exception("Failed to extract wttr estimate for %s on %s", city, target_date)

    if visualcrossing is not None:
        try:
            est = _extract_vc(visualcrossing, target_date, target_unit)
            if est is not None:
                sources.append(est)
        except Exception:
            logger.exception("Failed to extract Visual Crossing estimate for %s on %s", city, target_date)

    if not sources:
        logger.warning("No consensus sources available for %s on %s", city, target_date)
        return None

    # Assign a default sigma to point-only sources so they still contribute.
    default_sigma = _WTTR_DEFAULT_SIGMA_F if target_unit == "F" else _WTTR_DEFAULT_SIGMA_C
    weights: list[float] = []
    weighted_sum = 0.0
    for s in sources:
        sigma = s.std_dev if s.std_dev and s.std_dev > 0 else default_sigma
        w = 1.0 / (sigma * sigma)
        weights.append(w)
        weighted_sum += w * s.point_estimate

    total_w = sum(weights)
    point = weighted_sum / total_w
    blended_sigma = math.sqrt(1.0 / total_w)

    points = [s.point_estimate for s in sources]
    spread = max(points) - min(points)

    source_count = len(sources)
    if source_count < 2:
        sources_agree = False
    else:
        sources_agree = spread <= _disagreement_threshold(source_count, target_unit)

    logger.info(
        "Consensus %s %s: point=%.2f%s sigma=%.2f spread=%.2f sources=%d agree=%s",
        city, target_date, point, target_unit, blended_sigma, spread,
        source_count, sources_agree,
    )

    return ConsensusForecast(
        city=city,
        target_date=target_date,
        unit=target_unit,
        point_estimate=point,
        std_dev=blended_sigma,
        source_spread=spread,
        source_count=source_count,
        sources_agree=sources_agree,
        per_source=sources,
    )
