"""
Per-city configuration for the Polymarket Weather Prediction Agent.

Centralises forecast model selection, temperature units, and geographic
coordinates so the fetchers stay free of per-city hardcoding.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# City configuration
# ---------------------------------------------------------------------------
# Keys must match the city names used everywhere else in the codebase
# (CITY_SETTINGS_KEY in trade_engine, CITY_NAME_MAP in market_scanner).
#
# Fields:
#   latitude / longitude   - decimal degrees, used by Open-Meteo APIs
#   primary_model          - Open-Meteo ensemble model (best for the region)
#   secondary_model        - Optional secondary ensemble model (or None)
#   temp_unit              - "fahrenheit" or "celsius"
#                            Polymarket resolves NYC in °F; all intl in °C.
#   bucket_width           - Degree-width for probability histogram bins
#                            (1°F for Fahrenheit cities, 1°C for Celsius cities)

CITY_CONFIGS: dict[str, dict] = {
    # ── US cities (primary forecast via NOAA; Open-Meteo config kept for
    #    potential ensemble overlay or future migration) ──────────────────
    "New York City": {
        "latitude": 40.7769,
        "longitude": -73.8740,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KLGA",
        "station_name": "LaGuardia",
        "wunderground_path": "us/ny/new-york-city/KLGA",
    },
    "Chicago": {
        "latitude": 41.9742,
        "longitude": -87.9073,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KORD",
        "station_name": "O'Hare Intl",
        "wunderground_path": "us/il/chicago/KORD",
    },
    "Miami": {
        "latitude": 25.7959,
        "longitude": -80.2870,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KMIA",
        "station_name": "Miami Intl",
        "wunderground_path": "us/fl/miami/KMIA",
    },
    "Dallas": {
        "latitude": 32.8471,
        "longitude": -96.8518,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KDAL",
        "station_name": "Love Field",
        "wunderground_path": "us/tx/dallas/KDAL",
    },
    "Seattle": {
        "latitude": 47.4502,
        "longitude": -122.3088,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KSEA",
        "station_name": "Sea-Tac",
        "wunderground_path": "us/wa/seatac/KSEA",
    },
    "Atlanta": {
        "latitude": 33.6407,
        "longitude": -84.4277,
        "primary_model": "gfs_seamless",
        "secondary_model": "hrrr",
        "temp_unit": "fahrenheit",
        "bucket_width": 1.0,
        "station_icao": "KATL",
        "station_name": "Hartsfield-Jackson",
        "wunderground_path": "us/ga/atlanta/KATL",
    },
    # ── International cities (fetched via Open-Meteo) ────────────────────
    "London": {
        "latitude": 51.5050,
        "longitude": 0.0554,
        "primary_model": "ukmo_seamless",   # deterministic forecast
        "ensemble_model": "gfs_seamless",   # ukmo has no ensemble; use GFS global
        "secondary_model": None,
        "temp_unit": "celsius",
        "bucket_width": 1.0,
        "station_icao": "EGLC",
        "station_name": "London City",
        "wunderground_path": "gb/london/EGLC",
    },
    "Seoul": {
        "latitude": 37.4602,
        "longitude": 126.4407,
        "primary_model": "kma_seamless",    # deterministic forecast
        "ensemble_model": "gfs_seamless",   # kma has no ensemble; use GFS global
        "secondary_model": None,
        "temp_unit": "celsius",
        "bucket_width": 1.0,
        "station_icao": "RKSI",
        "station_name": "Incheon Intl",
        "wunderground_path": "kr/incheon/RKSI",
    },
    "Shanghai": {
        "latitude": 31.1443,
        "longitude": 121.8083,
        "primary_model": "cma_grapes_global",   # deterministic forecast
        "ensemble_model": "gfs_seamless",        # cma has no ensemble; use GFS global
        "secondary_model": None,
        "temp_unit": "celsius",
        "bucket_width": 1.0,
        "station_icao": "ZSPD",
        "station_name": "Pudong Intl",
        "wunderground_path": "cn/shanghai/ZSPD",
    },
    "Hong Kong": {
        "latitude": 22.3080,
        "longitude": 113.9185,
        "primary_model": "cma_grapes_global",   # deterministic forecast
        "ensemble_model": "gfs_seamless",        # cma has no ensemble; use GFS global
        "secondary_model": None,
        "temp_unit": "celsius",
        "bucket_width": 1.0,
        "station_icao": "VHHH",
        "station_name": "HKIA",
        "wunderground_path": "hk/chek-lap-kok/VHHH",
    },
    "Tokyo": {
        "latitude": 35.6762,
        "longitude": 139.6503,
        "primary_model": "jma_seamless",
        "secondary_model": None,
        "temp_unit": "celsius",
        "bucket_width": 1.0,
    },
}

# Convenience sets
INTL_CITIES: set[str] = {
    "London", "Seoul", "Shanghai", "Hong Kong", "Tokyo"
}
