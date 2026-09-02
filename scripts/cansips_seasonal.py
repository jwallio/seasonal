#!/usr/bin/env python3
"""Fetch and render CanSIPS v3 seasonal products.

CanSIPS v3 publishes 40-member global GRIB2 files through the ECCC MSC
Datamart. This adapter computes member-aware forecast means, subtracts the
matching 1991-2020 hindcast climatology, and sends the resulting fields through
the shared operational seasonal renderer used by the other model adapters.
The snowfall product derives liquid-water equivalent from paired 2-m
temperature and precipitation-rate members because CanSIPS does not publish a
native snowfall field.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CONUS_PRECIP_REGION,
    CONUS_STATE_NAMES,
    CFSv2Error,
    CONUS_REGION,
    DEFAULT_REGION,
    PRECIP_ANOMALY_PALETTE,
    PRECIP_ANOMALY_TICKS,
    SNOWFALL_ANOMALY_MAX_IN,
    SNOWFALL_ANOMALY_MIN_IN,
    SNOWFALL_ANOMALY_PALETTE,
    SNOWFALL_ANOMALY_TICK_DECIMALS,
    SNOWFALL_ANOMALY_TICK_FORMAT,
    SNOWFALL_ANOMALY_TICKS,
    SNOWFALL_MONTHLY_ANOMALY_MAX_IN,
    SNOWFALL_MONTHLY_ANOMALY_MIN_IN,
    SNOWFALL_MONTHLY_ANOMALY_PALETTE,
    SNOWFALL_MONTHLY_ANOMALY_TICKS,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    Grid,
    NORTHERN_HEMISPHERE_REGION,
    download_file,
    ensure_border_files,
    mean_grids,
    read_grid_csv,
    read_grid_state,
    relative_path,
    render_map,
    seasonal_period_label,
    subtract_grids,
    sum_grids,
    target_period,
    write_grid_state,
)
from seasonal_products import is_retired_product


CANSIPS_ROOT = "https://dd.weather.gc.ca/today/model_cansips/100km/"
CANSIPS_FORECAST_ROOT = urljoin(CANSIPS_ROOT, "forecast/")
CANSIPS_HINDCAST_ROOT = urljoin(CANSIPS_ROOT, "hindcast/")
CANSIPS_README_URL = "https://eccc-msc.github.io/open-data/msc-data/nwp_cansips/readme_cansips-datamart_en/"
CANSIPS_GRID_SHAPE = (360, 180)
CANSIPS_ENSEMBLE_MEMBERS = 40
CANSIPS_HINDCAST_START = 1991
CANSIPS_HINDCAST_END = 2020
CANSIPS_MEAN_RECORD = 3
CANSIPS_DEFAULT_REGION = DEFAULT_REGION
CANSIPS_DOWNLOAD_ATTEMPTS = 4
CANSIPS_DOWNLOAD_TIMEOUT = (60, 600)
CANSIPS_REQUEST_DELAY = 1.0

# CanSIPS does not publish a native snowfall field. The derived product uses
# member-level total precipitation and a two-level temperature phase gate.
# Dai (2008) land snow-frequency fits, expressed as (a, b, c, d) in
# F(T) = a * [tanh(b * (T - c)) - d], with F in percent.  The monthly
# product selects the appropriate season; DJF is the production winter fit.
SNOWFALL_DAI_LAND_PARAMS_BY_SEASON = {
    "ANN": (-48.2292, 0.7205, 1.1662, 1.0223),
    "DJF": (-48.2372, 0.7449, 1.0919, 1.0209),
    "MAM": (-48.2493, 0.6634, 1.3388, 1.0270),
    "JJA": (-46.4000, 0.7013, 0.8362, 1.0217),
    "SON": (-48.3251, 0.7798, 1.1502, 1.0180),
}
SNOWFALL_DAI_LAND_DJF_PARAMS = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON["DJF"]

MSLP_ANOMALY_TICKS = list(range(-10, 11))
SSH_ANOMALY_TICKS = [round(-0.50 + index * 0.10, 2) for index in range(11)]
SSH_ANOMALY_PALETTE = [
    "#24527a", "#3d83a6", "#539cb8", "#70b2c6", "#95c4d3",
    "#e1e4e7", "#f2cecd", "#eaaaa8", "#d3686c", "#a1384a",
]

PRODUCT_Z500_ANOMALY = "500mb_height_anomaly"
PRODUCT_Z500_ANOMALY_NH = "500mb_height_anomaly_nh"
PRODUCT_850MB_TEMPERATURE_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_2M_TEMPERATURE_ANOMALY = "2m_temperature_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_SNOWFALL_ANOMALY = "snowfall_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY = "sea_surface_height_anomaly"
PRODUCT_ALL = "all"
PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    PRODUCT_Z500_ANOMALY: {
        "name": PRODUCT_Z500_ANOMALY,
        "source_var": "GeopotentialHeight",
        "level": "ISBL-0500",
        "state_tag": "z500",
        "id_token": "z500a",
        "title": "CanSIPS v3 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "CanSIPS v3 500-mb Geopotential Height (m)",
        "field": "z500_anomaly",
        "raw_field": "GeopotentialHeight at 500 hPa",
        "raw_units": "m",
        "units": "m",
        "seasonal_units": "m",
        "height_contours": True,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -100.0,
        "anomaly_max": 100.0,
        "anomaly_ticks": ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam",
    },
    PRODUCT_850MB_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_850MB_TEMPERATURE_ANOMALY,
        "source_var": "AirTemp",
        "level": "ISBL-0850",
        "state_tag": "t850",
        "id_token": "t850a",
        "title": "CanSIPS v3 850-mb Temperature Anomaly (°C)",
        "absolute_title": "CanSIPS v3 850-mb Temperature (°C)",
        "field": "temperature_850mb_anomaly",
        "raw_field": "AirTemp at 850 hPa",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "height_contours": False,
        "region": CONUS_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  850-mb temperature anomaly (°C)",
    },
    PRODUCT_2M_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "source_var": "AirTemp",
        "level": "AGL-2m",
        "state_tag": "t2m",
        "id_token": "t2ma",
        "title": "CanSIPS v3 2-m Temperature Anomaly (°C)",
        "absolute_title": "CanSIPS v3 2-m Temperature (°C)",
        "field": "temperature_2m_anomaly",
        "raw_field": "AirTemp at 2 m",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "height_contours": False,
        "region": CONUS_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "source_var": "PrecipRate",
        "level": "Sfc",
        "state_tag": "prate",
        "id_token": "prcpa",
        "title": "CanSIPS v3 Precipitation Anomaly (in)",
        "absolute_title": "CanSIPS v3 Precipitation (in)",
        "field": "precipitation_anomaly",
        "raw_field": "PrecipRate at the surface",
        "raw_units": "kg m-2 s-1",
        "units": "in",
        "seasonal_units": "in",
        "height_contours": False,
        "region": CONUS_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "sum",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": PRECIP_ANOMALY_TICKS,
        "anomaly_palette": PRECIP_ANOMALY_PALETTE,
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "PrecipRate multiplied by calendar-month seconds, converted from millimetres to inches",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Precipitation anomaly (in)",
    },
    PRODUCT_SNOWFALL_ANOMALY: {
        "name": PRODUCT_SNOWFALL_ANOMALY,
        "source_var": "derived",
        "level": "",
        "state_tag": "snowfall_estimate_dai_t850",
        "id_token": "snowfalla",
        "title": "CanSIPS v3 Derived Snowfall Departure",
        "absolute_title": "CanSIPS v3 Derived Snowfall Estimate",
        "field": "snowfall_anomaly",
        "raw_field": "Derived from 2-m/850-hPa AirTemp and surface PrecipRate",
        "raw_units": "K; K; kg m-2 s-1",
        "units": "in",
        "seasonal_units": "in",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total",
        "seasonal_reducer": "sum",
        "anomaly_min": SNOWFALL_ANOMALY_MIN_IN,
        "anomaly_max": SNOWFALL_ANOMALY_MAX_IN,
        "anomaly_ticks": SNOWFALL_ANOMALY_TICKS,
        "anomaly_palette": SNOWFALL_ANOMALY_PALETTE,
        "anomaly_tick_decimals": SNOWFALL_ANOMALY_TICK_DECIMALS,
        "anomaly_tick_format": SNOWFALL_ANOMALY_TICK_FORMAT,
        "monthly_anomaly_min": SNOWFALL_MONTHLY_ANOMALY_MIN_IN,
        "monthly_anomaly_max": SNOWFALL_MONTHLY_ANOMALY_MAX_IN,
        "monthly_anomaly_ticks": SNOWFALL_MONTHLY_ANOMALY_TICKS,
        "monthly_anomaly_palette": SNOWFALL_MONTHLY_ANOMALY_PALETTE,
        "monthly_anomaly_endpoint_labels": {"minimum": "≤−2.0", "maximum": "≥+2.0"},
        "map_domain": "land",
        "fit_frame_to_domain": True,
        "domain_frame_padding_fraction": 0.012,
        "mask_states": list(CONUS_STATE_NAMES),
        "border_files": ("us-states.geojson",),
        "anomaly_endpoint_labels": {"minimum": "≤−4.0", "maximum": "≥+4.0"},
        "derived_product": True,
        "source_variables": ["AirTemp at AGL-2m", "AirTemp at ISBL-0850", "PrecipRate at Sfc"],
        "conversion_kind": "derived_snowfall_lwe",
        "conversion": (
            "For each of 40 members, convert total PrecipRate to the "
            "calendar-month liquid-water total, apply the season-appropriate "
            "Dai (2008) land snow-frequency curve (DJF for December-February) "
            "to the warmer of monthly mean 2-m and 850-hPa "
            "temperature, then average members; seasonal values sum the monthly "
            "liquid-water-equivalent estimates"
        ),
        "header_detail": (
            "{source_label}  •  Derived snowfall liquid-water equivalent (in)  •  "
            "2-m + 850-hPa temperature phase gate + precipitation  •  CONUS domain"
        ),
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "source_var": "Pressure",
        "level": "MSL",
        "state_tag": "mslp",
        "id_token": "mslpa",
        "title": "CanSIPS v3 Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "CanSIPS v3 Mean Sea-Level Pressure (hPa)",
        "field": "mslp_anomaly",
        "raw_field": "Pressure at mean sea level",
        "raw_units": "Pa",
        "units": "hPa",
        "seasonal_units": "hPa",
        "height_contours": False,
        "region": CONUS_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": MSLP_ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "Pressure divided by 100 to convert Pa to hPa",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
    },
    PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY: {
        "name": PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY,
        "source_var": "SeaSfcHeight-Geoid",
        "level": "",
        "state_tag": "ssh",
        "id_token": "ssha",
        "title": "CanSIPS v3 Sea-Surface Height Anomaly (m)",
        "absolute_title": "CanSIPS v3 Sea-Surface Height (m)",
        "field": "sea_surface_height_anomaly",
        "raw_field": "Sea-surface height relative to geoid",
        "raw_units": "m",
        "units": "m",
        "seasonal_units": "m",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -0.50,
        "anomaly_max": 0.50,
        "anomaly_ticks": SSH_ANOMALY_TICKS,
        "anomaly_tick_decimals": 2,
        "anomaly_palette": SSH_ANOMALY_PALETTE,
        "map_domain": "ocean",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Sea-surface height anomaly (m)",
    },
}

PRODUCT_SPECS[PRODUCT_Z500_ANOMALY_NH] = {
    **PRODUCT_SPECS[PRODUCT_Z500_ANOMALY],
    "name": PRODUCT_Z500_ANOMALY_NH,
    "id_token": "z500a-nh",
    "region": NORTHERN_HEMISPHERE_REGION,
    "projection": "north_polar_stereographic",
    "projection_central_longitude": 0.0,
    "title": "CanSIPS v3 Northern Hemisphere 500-mb Geopotential Height & Anomaly (m)",
    "absolute_title": "CanSIPS v3 Northern Hemisphere 500-mb Geopotential Height (m)",
    "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam  •  Northern Hemisphere",
}

Z500_PRODUCTS = frozenset({PRODUCT_Z500_ANOMALY, PRODUCT_Z500_ANOMALY_NH})

PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_Z500_ANOMALY_NH: "500-mb Height Anomaly · Northern Hemisphere",
    PRODUCT_850MB_TEMPERATURE_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_2M_TEMPERATURE_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "Precipitation Anomaly",
    PRODUCT_SNOWFALL_ANOMALY: "Derived Snowfall Water-Equivalent Departure",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
    PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY: "Sea-Surface Height Anomaly",
}


class CanSIPSError(CFSv2Error):
    """A user-actionable CanSIPS source, decode, or rendering error."""


def get_product_spec(product: str) -> dict[str, Any]:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        raise CanSIPSError(
            f"unsupported CanSIPS product {product!r}; choose from {', '.join(PRODUCT_SPECS)}"
        ) from exc


def selected_products(product: str) -> list[dict[str, Any]]:
    if product == PRODUCT_ALL:
        return list(PRODUCT_SPECS.values())
    return [get_product_spec(product)]


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def month_after(year: int, month: int, lead_months: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + lead_months
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead_months: int) -> str:
    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(init_date.year, init_date.month, lead_months)
    return f"{year:04d}{month:02d}"


def parse_init(value: str) -> str:
    if value.lower() == "latest":
        return discover_latest_init()
    if re.fullmatch(r"\d{6}", value):
        value = f"{value}0100"
    if not re.fullmatch(r"\d{10}", value):
        raise CanSIPSError("--init must be YYYYMM, YYYYMM0100, or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d%H")
    except ValueError as exc:
        raise CanSIPSError(f"invalid CanSIPS initialization: {value}") from exc
    if parsed.day != 1 or parsed.hour != 0:
        raise CanSIPSError("CanSIPS initialization must be the first day of the month at 00Z")
    return value


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise CanSIPSError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise CanSIPSError(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise CanSIPSError(f"{label} cannot be empty")
    return result


def discover_latest_init() -> str:
    """Select the newest monthly forecast directory from the official index."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - minimal environments only
        raise CanSIPSError("requests is required when --init latest is used") from exc
    try:
        response = requests.get(CANSIPS_FORECAST_ROOT, timeout=(20, 60))
        response.raise_for_status()
        years = sorted(set(re.findall(r'href="(20\d{2})/"', response.text)), reverse=True)
        for year in years:
            month_url = urljoin(CANSIPS_FORECAST_ROOT, f"{year}/")
            month_response = requests.get(month_url, timeout=(20, 60))
            month_response.raise_for_status()
            months = sorted(
                set(re.findall(r'href="(\d{2})/"', month_response.text)),
                reverse=True,
            )
            if months:
                return f"{year}{months[0]}0100"
    except Exception as exc:
        raise CanSIPSError(f"could not read the CanSIPS forecast index: {exc}") from exc
    raise CanSIPSError("the CanSIPS Datamart listed no forecast initialization")


def file_name(init: str, lead: int, hindcast: bool, product_spec: dict[str, Any] | None = None) -> str:
    product = product_spec or PRODUCT_SPECS[PRODUCT_Z500_ANOMALY]
    prefix = f"{init[:6]}_MSC_CanSIPS{'-Hindcast' if hindcast else ''}"
    level = f"_{product['level']}" if product.get("level") else ""
    return f"{prefix}_{product['source_var']}{level}_LatLon1.0_P{lead:02d}M.grib2"


def source_url(
    init: str,
    lead: int,
    hindcast: bool,
    product_spec: dict[str, Any] | None = None,
) -> str:
    root = CANSIPS_HINDCAST_ROOT if hindcast else CANSIPS_FORECAST_ROOT
    return urljoin(root, f"{init[:4]}/{init[4:6]}/{file_name(init, lead, hindcast, product_spec)}")


def cache_paths(
    cache_dir: Path,
    init: str,
    lead: int,
    hindcast: bool,
    product_spec: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    product = product_spec or PRODUCT_SPECS[PRODUCT_Z500_ANOMALY]
    kind = "hindcast" if hindcast else "forecast"
    name = file_name(init, lead, hindcast, product)
    raw_path = cache_dir / "raw" / kind / init[:6] / name
    state_path = cache_dir / "means" / kind / init[:6] / f"{product['state_tag']}_lead{lead:02d}.csv.gz"
    return raw_path, state_path


def transform_grid(grid: Grid, transform: Callable[[float], float]) -> Grid:
    return Grid(
        grid.lons[:],
        grid.lats[:],
        [[transform(value) for value in row] for row in grid.values],
    )


def monthly_precipitation_total_inches(grid: Grid, target: str) -> Grid:
    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    end = dt.datetime(next_year, next_month, 1)
    seconds = (end - start).total_seconds()
    return transform_grid(grid, lambda value: value * seconds / 25.4)


def prepare_product_grid(grid: Grid, product_spec: dict[str, Any], target: str) -> Grid:
    conversion_kind = product_spec.get("conversion_kind")
    if conversion_kind == "monthly_precipitation_total_inches":
        return monthly_precipitation_total_inches(grid, target)
    if conversion_kind == "pascals_to_hectopascals":
        return transform_grid(grid, lambda value: value / 100.0)
    return grid


def snowfall_fraction_from_temperature_c(temperature_c: float, season: str = "DJF") -> float:
    """Return the Dai (2008) land snow fraction for mean temperature.

    Dai's fitted snow-frequency curve is expressed as a percentage.  It is a
    precipitation-phase estimate, not a snow-depth or snow-to-liquid ratio.
    The requested seasonal land parameters retain a small snow fraction above
    freezing instead of imposing an artificial hard cutoff.  The default is
    DJF for compatibility with callers that only pass a temperature.
    """

    if not math.isfinite(temperature_c):
        return math.nan
    try:
        coefficient, slope, midpoint, offset = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON[season]
    except KeyError as exc:
        raise CanSIPSError(
            f"unsupported snowfall phase season {season!r}; choose from "
            f"{', '.join(SNOWFALL_DAI_LAND_PARAMS_BY_SEASON)}"
        ) from exc
    fraction = coefficient * (math.tanh(slope * (temperature_c - midpoint)) - offset) / 100.0
    return max(0.0, min(1.0, fraction))


def snowfall_phase_season(target: str) -> str:
    """Return the Dai seasonal fit name for a YYYYMM target month."""

    month = dt.datetime.strptime(target, "%Y%m").month
    return {
        12: "DJF",
        1: "DJF",
        2: "DJF",
        3: "MAM",
        4: "MAM",
        5: "MAM",
        6: "JJA",
        7: "JJA",
        8: "JJA",
        9: "SON",
        10: "SON",
        11: "SON",
    }[month]


def _decode_cfgrib_members(
    path: Path,
    expected_variables: tuple[str, ...],
    label: str,
) -> tuple[list[float], list[float], Any, str]:
    """Decode one 40-member CanSIPS field with its member dimension intact."""

    try:
        import cfgrib
        import numpy as np
    except ImportError as exc:  # pragma: no cover - CI installs requirements.txt
        raise CanSIPSError(
            f"{label} decoding requires cfgrib and eccodes; install requirements.txt"
        ) from exc

    datasets: list[Any] = []
    try:
        try:
            datasets = list(
                cfgrib.open_datasets(
                    str(path),
                    backend_kwargs={"indexpath": ""},
                )
            )
        except Exception as exc:
            raise CanSIPSError(f"could not open CanSIPS {label} file {path.name}: {exc}") from exc

        selected = None
        variable_name = ""
        for dataset in datasets:
            for candidate in expected_variables:
                if candidate in dataset.data_vars:
                    selected = dataset[candidate]
                    variable_name = candidate
                    break
            if selected is not None:
                break
        if selected is None:
            available = sorted({name for dataset in datasets for name in dataset.data_vars})
            raise CanSIPSError(
                f"CanSIPS {label} file {path.name} has no expected variable "
                f"{expected_variables}; found {available}"
            )

        required_dimensions = {"number", "latitude", "longitude"}
        if not required_dimensions.issubset(set(selected.dims)):
            raise CanSIPSError(
                f"CanSIPS {label} variable {variable_name} is missing one of "
                f"the member/latitude/longitude dimensions"
            )
        selected = selected.transpose("number", "latitude", "longitude")
        values = np.asarray(selected.values, dtype=float).copy()
        member_numbers = np.asarray(selected.coords["number"].values)
        lats = np.asarray(selected.coords["latitude"].values, dtype=float).copy()
        lons = np.asarray(selected.coords["longitude"].values, dtype=float).copy()

        expected_members = np.arange(1, CANSIPS_ENSEMBLE_MEMBERS + 1)
        if member_numbers.shape != expected_members.shape or not np.array_equal(
            member_numbers.astype(int), expected_members
        ):
            raise CanSIPSError(
                f"CanSIPS {label} file {path.name} does not contain members 1-"
                f"{CANSIPS_ENSEMBLE_MEMBERS}"
            )
        if values.shape != (CANSIPS_ENSEMBLE_MEMBERS, *CANSIPS_GRID_SHAPE[::-1]):
            raise CanSIPSError(
                f"CanSIPS {label} file {path.name} has decoded shape {values.shape}; "
                f"expected {(CANSIPS_ENSEMBLE_MEMBERS, *CANSIPS_GRID_SHAPE[::-1])}"
            )
        if lons.size != CANSIPS_GRID_SHAPE[0] or lats.size != CANSIPS_GRID_SHAPE[1]:
            raise CanSIPSError(
                f"CanSIPS {label} file {path.name} has unexpected coordinate lengths "
                f"({lons.size}, {lats.size})"
            )
        if not np.isfinite(values).any():
            raise CanSIPSError(f"CanSIPS {label} file {path.name} contains no finite values")

        # The Datamart uses 0.5..359.5E. Normalize to the shared -180..180
        # convention before returning the grid to the common renderer.
        normalized_lons = ((lons + 180.0) % 360.0) - 180.0
        lon_order = np.argsort(normalized_lons)
        lat_order = np.argsort(lats)
        normalized_lons = normalized_lons[lon_order]
        lats = lats[lat_order]
        values = values[:, lat_order, :][:, :, lon_order]
        if np.any(np.diff(normalized_lons) <= 0.0) or np.any(np.diff(lats) <= 0.0):
            raise CanSIPSError(f"CanSIPS {label} coordinates are not strictly increasing")
        return normalized_lons.tolist(), lats.tolist(), values, variable_name
    finally:
        for dataset in datasets:
            try:
                dataset.close()
            except Exception:
                pass


def derive_snowfall_lwe_grid(
    temperature_members: Any,
    precipitation_members: Any,
    lons: list[float],
    lats: list[float],
    target: str,
    temperature_850_members: Any | None = None,
) -> tuple[Grid, dict[str, Any]]:
    """Derive member-mean monthly snowfall liquid-water equivalent in inches.

    The production path supplies paired 2-m and 850-hPa temperatures.  The
    warmer level is used as a conservative warm-layer gate before applying the
    target-month's Dai (2008) land phase curve.  ``temperature_850_members`` remains
    optional only for compatibility with older callers; those calls use the
    2-m field alone and are explicitly marked in diagnostics.
    """

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - CI installs requirements.txt
        raise CanSIPSError("CanSIPS snowfall derivation requires numpy") from exc

    temperatures = np.asarray(temperature_members, dtype=float)
    temperatures_850 = (
        temperatures
        if temperature_850_members is None
        else np.asarray(temperature_850_members, dtype=float)
    )
    precipitation = np.asarray(precipitation_members, dtype=float)
    expected_shape = (CANSIPS_ENSEMBLE_MEMBERS, len(lats), len(lons))
    if (
        temperatures.shape != expected_shape
        or temperatures_850.shape != expected_shape
        or precipitation.shape != expected_shape
    ):
        raise CanSIPSError(
            "CanSIPS snowfall inputs must all have shape "
            f"{expected_shape}; got 2-m {temperatures.shape}, "
            f"850-hPa {temperatures_850.shape}, precipitation {precipitation.shape}"
        )

    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    seconds = (dt.datetime(next_year, next_month, 1) - start).total_seconds()
    phase_season = snowfall_phase_season(target)
    valid = (
        np.isfinite(temperatures)
        & np.isfinite(temperatures_850)
        & np.isfinite(precipitation)
    )
    temperature_2m_c = temperatures - 273.15
    temperature_850_c = temperatures_850 - 273.15
    phase_temperature_c = np.maximum(temperature_2m_c, temperature_850_c)
    coefficient, slope, midpoint, offset = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON[phase_season]
    snow_fraction = np.clip(
        coefficient * (np.tanh(slope * (phase_temperature_c - midpoint)) - offset) / 100.0,
        0.0,
        1.0,
    )
    precipitation_inches = np.maximum(precipitation, 0.0) * seconds / 25.4
    member_lwe = np.where(valid, precipitation_inches * snow_fraction, np.nan)
    valid_counts = np.sum(np.isfinite(member_lwe), axis=0)
    totals = np.nansum(member_lwe, axis=0)
    means = np.divide(
        totals,
        valid_counts,
        out=np.full(valid_counts.shape, np.nan, dtype=float),
        where=valid_counts > 0,
    )
    diagnostics = {
        "valid_member_count_min": int(valid_counts.min()),
        "valid_member_count_max": int(valid_counts.max()),
        "valid_member_fraction_min": round(float(valid_counts.min() / CANSIPS_ENSEMBLE_MEMBERS), 4),
        "snow_fraction": {
            "method": "Dai_2008_land_seasonal_hyperbolic_tangent",
            "season": phase_season,
            "parameters": {
                "a_percent": coefficient,
                "b_per_c": slope,
                "c_c": midpoint,
                "d": offset,
            },
            "phase_temperature": (
                "max(2-m, 850-hPa)"
                if temperature_850_members is not None
                else "2-m legacy fallback"
            ),
        },
        "calendar_month_seconds": int(seconds),
    }
    return Grid(list(lons), list(lats), means.tolist()), diagnostics


def snowfall_input_paths(
    cache_dir: Path,
    init: str,
    lead: int,
    hindcast: bool,
) -> tuple[Path, Path, Path, Path]:
    temperature_2m_raw, _ = cache_paths(
        cache_dir,
        init,
        lead,
        hindcast,
        PRODUCT_SPECS[PRODUCT_2M_TEMPERATURE_ANOMALY],
    )
    temperature_850_raw, _ = cache_paths(
        cache_dir,
        init,
        lead,
        hindcast,
        PRODUCT_SPECS[PRODUCT_850MB_TEMPERATURE_ANOMALY],
    )
    precipitation_raw, _ = cache_paths(
        cache_dir,
        init,
        lead,
        hindcast,
        PRODUCT_SPECS[PRODUCT_PRECIPITATION_ANOMALY],
    )
    kind = "hindcast" if hindcast else "forecast"
    state_path = (
        cache_dir
        / "means"
        / kind
        / init[:6]
        # Version the retained grid because the prior implementation used a
        # different phase curve and did not include the 850-hPa field.
        / f"snowfall_estimate_dai_t850_lead{lead:02d}.csv.gz"
    )
    return temperature_2m_raw, temperature_850_raw, precipitation_raw, state_path


def snowfall_input_urls(init: str, lead: int, hindcast: bool) -> tuple[str, str, str]:
    return (
        source_url(
            init,
            lead,
            hindcast,
            PRODUCT_SPECS[PRODUCT_2M_TEMPERATURE_ANOMALY],
        ),
        source_url(
            init,
            lead,
            hindcast,
            PRODUCT_SPECS[PRODUCT_850MB_TEMPERATURE_ANOMALY],
        ),
        source_url(
            init,
            lead,
            hindcast,
            PRODUCT_SPECS[PRODUCT_PRECIPITATION_ANOMALY],
        ),
    )


def load_snowfall_estimate(
    init: str,
    lead: int,
    hindcast: bool,
    cache_dir: Path,
    repo_root: Path,
    request_delay: float,
    last_request: float,
    target: str | None = None,
    force: bool = False,
    cleanup_inputs: bool = False,
) -> tuple[Grid, dict[str, Any], float]:
    """Load or derive one CanSIPS member-mean snowfall LWE field."""

    product = PRODUCT_SPECS[PRODUCT_SNOWFALL_ANOMALY]
    target = target or target_month(init, lead)
    temperature_2m_raw, temperature_850_raw, precipitation_raw, state_path = snowfall_input_paths(
        cache_dir, init, lead, hindcast
    )
    temperature_2m_url, temperature_850_url, precipitation_url = snowfall_input_urls(
        init, lead, hindcast
    )
    source_files = [
        {
            "initialization": init,
            "lead_month": lead,
            "product": PRODUCT_SNOWFALL_ANOMALY,
            "source_field": PRODUCT_SPECS[PRODUCT_2M_TEMPERATURE_ANOMALY]["raw_field"],
            "url": temperature_2m_url,
            "cache_file": relative_path(temperature_2m_raw, repo_root),
            "raw_units": PRODUCT_SPECS[PRODUCT_2M_TEMPERATURE_ANOMALY]["raw_units"],
        },
        {
            "initialization": init,
            "lead_month": lead,
            "product": PRODUCT_SNOWFALL_ANOMALY,
            "source_field": PRODUCT_SPECS[PRODUCT_850MB_TEMPERATURE_ANOMALY]["raw_field"],
            "url": temperature_850_url,
            "cache_file": relative_path(temperature_850_raw, repo_root),
            "raw_units": PRODUCT_SPECS[PRODUCT_850MB_TEMPERATURE_ANOMALY]["raw_units"],
        },
        {
            "initialization": init,
            "lead_month": lead,
            "product": PRODUCT_SNOWFALL_ANOMALY,
            "source_field": PRODUCT_SPECS[PRODUCT_PRECIPITATION_ANOMALY]["raw_field"],
            "url": precipitation_url,
            "cache_file": relative_path(precipitation_raw, repo_root),
            "raw_units": PRODUCT_SPECS[PRODUCT_PRECIPITATION_ANOMALY]["raw_units"],
        },
    ]
    metadata = {
        "initialization": init,
        "lead_month": lead,
        "product": PRODUCT_SNOWFALL_ANOMALY,
        "source_field": product["raw_field"],
        "source_variables": product["source_variables"],
        "source_urls": [temperature_2m_url, temperature_850_url, precipitation_url],
        "cache_file": relative_path(state_path, repo_root),
        "storage": "retained_40_member_derived_grid",
        "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
        "derivation": product["conversion"],
        "source_files": source_files,
    }
    if state_path.exists() and state_path.stat().st_size > 0 and not force:
        if cleanup_inputs:
            temperature_2m_raw.unlink(missing_ok=True)
            temperature_850_raw.unlink(missing_ok=True)
            precipitation_raw.unlink(missing_ok=True)
        metadata["downloaded"] = False
        metadata["storage"] = "retained_40_member_derived_grid"
        return read_grid_state(state_path), metadata, last_request

    temperature_downloaded, last_request = download_file(
        temperature_2m_url,
        temperature_2m_raw,
        max(CANSIPS_REQUEST_DELAY, request_delay),
        last_request,
        attempts=CANSIPS_DOWNLOAD_ATTEMPTS,
        timeout=CANSIPS_DOWNLOAD_TIMEOUT,
    )
    temperature_850_downloaded, last_request = download_file(
        temperature_850_url,
        temperature_850_raw,
        max(CANSIPS_REQUEST_DELAY, request_delay),
        last_request,
        attempts=CANSIPS_DOWNLOAD_ATTEMPTS,
        timeout=CANSIPS_DOWNLOAD_TIMEOUT,
    )
    precipitation_downloaded, last_request = download_file(
        precipitation_url,
        precipitation_raw,
        max(CANSIPS_REQUEST_DELAY, request_delay),
        last_request,
        attempts=CANSIPS_DOWNLOAD_ATTEMPTS,
        timeout=CANSIPS_DOWNLOAD_TIMEOUT,
    )
    temperature_lons, temperature_lats, temperature_members, temperature_variable = _decode_cfgrib_members(
        temperature_2m_raw,
        ("avg_2t", "t2m", "2t"),
        "2-m temperature",
    )
    temperature_850_lons, temperature_850_lats, temperature_850_members, temperature_850_variable = _decode_cfgrib_members(
        temperature_850_raw,
        ("avg_t", "t850", "t"),
        "850-hPa temperature",
    )
    precipitation_lons, precipitation_lats, precipitation_members, precipitation_variable = _decode_cfgrib_members(
        precipitation_raw,
        ("prate", "precipitation_rate"),
        "precipitation rate",
    )
    if (
        temperature_lons != temperature_850_lons
        or temperature_lats != temperature_850_lats
        or temperature_lons != precipitation_lons
        or temperature_lats != precipitation_lats
    ):
        raise CanSIPSError("CanSIPS snowfall input grids do not share coordinates")
    grid, diagnostics = derive_snowfall_lwe_grid(
        temperature_members,
        precipitation_members,
        temperature_lons,
        temperature_lats,
        target,
        temperature_850_members=temperature_850_members,
    )
    write_grid_state(grid, state_path)
    metadata.update(
        {
            "downloaded": bool(
                temperature_downloaded or temperature_850_downloaded or precipitation_downloaded
            ),
            "storage": "decoded_40_member_derived_grid",
            "decoded_variables": [
                temperature_variable,
                temperature_850_variable,
                precipitation_variable,
            ],
            "diagnostics": diagnostics,
        }
    )
    for source_file, downloaded in zip(
        metadata["source_files"],
        (temperature_downloaded, temperature_850_downloaded, precipitation_downloaded),
    ):
        source_file["downloaded"] = bool(downloaded)
    if cleanup_inputs:
        temperature_2m_raw.unlink(missing_ok=True)
        temperature_850_raw.unlink(missing_ok=True)
        precipitation_raw.unlink(missing_ok=True)
        temperature_2m_raw.with_name(temperature_2m_raw.name + ".part").unlink(missing_ok=True)
        temperature_850_raw.with_name(temperature_850_raw.name + ".part").unlink(missing_ok=True)
        precipitation_raw.with_name(precipitation_raw.name + ".part").unlink(missing_ok=True)
    return grid, metadata, last_request


def snowfall_hindcast_climatology(
    init: str,
    lead: int,
    climo_start: int,
    climo_end: int,
    cache_dir: Path,
    repo_root: Path,
    request_delay: float,
    last_request: float,
    force: bool = False,
    cleanup_inputs: bool = False,
) -> tuple[Grid, list[dict[str, Any]], float]:
    grids: list[Grid] = []
    sources: list[dict[str, Any]] = []
    for year in range(climo_start, climo_end + 1):
        hindcast_init = f"{year}{init[4:6]}0100"
        target = target_month(hindcast_init, lead)
        grid, source, last_request = load_snowfall_estimate(
            hindcast_init,
            lead,
            True,
            cache_dir,
            repo_root,
            request_delay,
            last_request,
            target,
            force,
            cleanup_inputs,
        )
        grids.append(grid)
        sources.append(source)
    return mean_grids(grids), sources, last_request


def run_wgrib2(command: list[str], label: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "wgrib2 failed").strip()
        raise CanSIPSError(f"wgrib2 failed for {label}: {detail[-1000:]}")
    return result.stdout


def validate_member_inventory(grib_path: Path, wgrib2: str) -> None:
    inventory = run_wgrib2([wgrib2, str(grib_path), "-s"], grib_path.name)
    member_numbers = sorted(
        {int(match) for match in re.findall(r"MM-ENS=(\d+)", inventory)}
    )
    expected = list(range(1, CANSIPS_ENSEMBLE_MEMBERS + 1))
    if member_numbers != expected:
        raise CanSIPSError(
            f"{grib_path.name} contains ensemble records {member_numbers[:5]}..."
            f" rather than all {CANSIPS_ENSEMBLE_MEMBERS} CanSIPS members"
        )


def load_ensemble_mean(
    init: str,
    lead: int,
    hindcast: bool,
    cache_dir: Path,
    repo_root: Path,
    wgrib2: str,
    request_delay: float,
    last_request: float,
    product_spec: dict[str, Any] | None = None,
    target: str | None = None,
    force: bool = False,
) -> tuple[Grid, dict[str, Any], float]:
    product = product_spec or PRODUCT_SPECS[PRODUCT_Z500_ANOMALY]
    target = target or target_month(init, lead)
    raw_path, state_path = cache_paths(cache_dir, init, lead, hindcast, product)
    url = source_url(init, lead, hindcast, product)
    if state_path.exists() and state_path.stat().st_size > 0 and not force:
        return read_grid_state(state_path), {
            "initialization": init,
            "lead_month": lead,
            "product": product["name"],
            "source_field": product["raw_field"],
            "url": url,
            "cache_file": relative_path(state_path, repo_root),
            "storage": "retained_ensemble_mean_grid",
            "downloaded": False,
            "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
        }, last_request

    downloaded, last_request = download_file(
        url,
        raw_path,
        max(CANSIPS_REQUEST_DELAY, request_delay),
        last_request,
        attempts=CANSIPS_DOWNLOAD_ATTEMPTS,
        timeout=CANSIPS_DOWNLOAD_TIMEOUT,
    )
    validate_member_inventory(raw_path, wgrib2)
    mean_path = raw_path.with_name(raw_path.name + ".ensmean.grib2")
    mean_part = mean_path.with_name(mean_path.name + ".part")
    csv_path = mean_path.with_name(mean_path.name + ".csv")
    csv_part = csv_path.with_name(csv_path.name + ".part")
    mean_part.unlink(missing_ok=True)
    csv_part.unlink(missing_ok=True)
    run_wgrib2(
        [wgrib2, str(raw_path), "-ens_processing", str(mean_part), "ave"],
        raw_path.name,
    )
    if not mean_part.exists() or mean_part.stat().st_size == 0:
        raise CanSIPSError(f"wgrib2 did not produce an ensemble mean for {raw_path.name}")
    mean_part.replace(mean_path)
    run_wgrib2(
        [wgrib2, str(mean_path), "-d", str(CANSIPS_MEAN_RECORD), "-csv", str(csv_part)],
        mean_path.name,
    )
    grid = read_grid_csv(csv_part, expected_shape=CANSIPS_GRID_SHAPE)
    grid = prepare_product_grid(grid, product, target)
    write_grid_state(grid, state_path)
    csv_part.unlink(missing_ok=True)
    mean_path.unlink(missing_ok=True)
    raw_path.unlink(missing_ok=True)
    return grid, {
        "initialization": init,
        "lead_month": lead,
        "product": product["name"],
        "source_field": product["raw_field"],
        "url": url,
        "cache_file": relative_path(state_path, repo_root),
        "storage": "decoded_ensemble_mean_grid",
        "downloaded": downloaded,
        "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
    }, last_request


def hindcast_climatology(
    init: str,
    lead: int,
    climo_start: int,
    climo_end: int,
    cache_dir: Path,
    repo_root: Path,
    wgrib2: str,
    request_delay: float,
    last_request: float,
    product_spec: dict[str, Any] | None = None,
    force: bool = False,
) -> tuple[Grid, list[dict[str, Any]], float]:
    grids: list[Grid] = []
    sources: list[dict[str, Any]] = []
    for year in range(climo_start, climo_end + 1):
        hindcast_init = f"{year}{init[4:6]}0100"
        grid, source, last_request = load_ensemble_mean(
            hindcast_init,
            lead,
            True,
            cache_dir,
            repo_root,
            wgrib2,
            request_delay,
            last_request,
            product_spec,
            target_month(init, lead),
            force,
        )
        grids.append(grid)
        sources.append(source)
    return mean_grids(grids), sources, last_request


def write_manifest(
    path: Path,
    repo_root: Path,
    run_entries: list[dict[str, Any]] | dict[str, Any],
    previous_manifest: Path | None,
    retain_runs: int,
) -> None:
    if retain_runs < 1:
        raise CanSIPSError("manifest retention must keep at least one run")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "cansips_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "ECCC MSC CanSIPS v3 / Datamart",
        "source_url": CANSIPS_README_URL,
        "source_urls": [CANSIPS_README_URL, CANSIPS_FORECAST_ROOT, CANSIPS_HINDCAST_ROOT],
        "product_labels": PRODUCT_LABELS,
        "retention": {"max_runs": retain_runs, "history_runs": max(0, retain_runs - 1)},
        "runs": [],
    }
    existing_paths = [path]
    if previous_manifest and previous_manifest.resolve() != path.resolve():
        existing_paths.append(previous_manifest)
    for existing_path in existing_paths:
        if not existing_path.exists():
            continue
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CanSIPSError(f"could not read existing CanSIPS manifest {existing_path}: {exc}") from exc
        if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
            payload["runs"].extend(
                run for run in existing["runs"]
                if isinstance(run, dict) and not is_retired_product(run.get("product"))
            )
    new_entries = run_entries if isinstance(run_entries, list) else [run_entries]
    new_entries = [
        run for run in new_entries
        if isinstance(run, dict) and not is_retired_product(run.get("product"))
    ]
    incoming_ids = {str(run_entry["id"]) for run_entry in new_entries}

    # The first CanSIPS implementation used a z500-only run id. Replace that
    # legacy entry when the same initialization is regenerated in the new
    # product-aware format instead of showing duplicate runs in the viewer.
    def product_init_key(run: dict[str, Any]) -> tuple[str, str]:
        product = str(run.get("product", ""))
        # Migrate the original z500-only manifest shape, which had no
        # product field and used a bare cansips-{init} id.
        if not product and str(run.get("id", "")).startswith("cansips-"):
            product = PRODUCT_Z500_ANOMALY
        return product or "unknown", str(run.get("init_utc", ""))

    incoming_product_inits = {product_init_key(run_entry) for run_entry in new_entries}
    unique_runs: dict[str, dict[str, Any]] = {}
    for run in payload["runs"]:
        if not isinstance(run, dict) or not run.get("id"):
            continue
        run_id = str(run["id"])
        if run_id not in incoming_ids and product_init_key(run) in incoming_product_inits:
            continue
        unique_runs[run_id] = run
    for run_entry in new_entries:
        unique_runs[str(run_entry["id"])] = run_entry
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in unique_runs.values():
        grouped.setdefault(str(entry.get("product", "unknown")), []).append(entry)
    retained: list[dict[str, Any]] = []
    for entries in grouped.values():
        retained.extend(sorted(
            entries,
            key=lambda item: (
                str(item.get("init_utc", "")),
                str(item.get("generated_utc", "")),
                str(item.get("id", "")),
            ),
            reverse=True,
        )[:retain_runs])
    payload["runs"] = sorted(
        retained,
        key=lambda item: (str(item.get("init_utc", "")), str(item.get("generated_utc", "")), str(item.get("id", ""))),
        reverse=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=(PRODUCT_ALL, *PRODUCT_SPECS), default=PRODUCT_ALL)
    parser.add_argument("--init", default="latest", help="CanSIPS initialization as YYYYMM, YYYYMM0100, or latest")
    parser.add_argument("--lead-months", default="4,5,6", help="comma-separated target leads; DJF default is 4,5,6")
    parser.add_argument("--seasonal-window", default="4,5,6", help="consecutive leads for the seasonal aggregate")
    parser.add_argument("--climo-start", type=int, default=CANSIPS_HINDCAST_START)
    parser.add_argument("--climo-end", type=int, default=CANSIPS_HINDCAST_END)
    parser.add_argument("--cache-dir", default=".cache/cansips")
    parser.add_argument("--output-dir", default="public/seasonal/cansips")
    parser.add_argument("--manifest", default="public/seasonal/cansips_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-runs", type=int, default=4)
    parser.add_argument("--common-reference-dir", type=Path, default="public/seasonal/common_reference/1991-2020", help="output directory for the shared 1991-2020 500-mb reference grids")
    parser.add_argument("--wgrib2", default="", help="path to wgrib2; CANSIPS_WGRIB2 is also honored")
    parser.add_argument("--request-delay", type=float, default=0.25)
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    parser.add_argument("--force-decode", action="store_true")
    return parser


def find_wgrib2(explicit: str) -> str:
    import os
    import shutil

    candidates = [explicit] if explicit else []
    if os.environ.get("CANSIPS_WGRIB2"):
        candidates.append(os.environ["CANSIPS_WGRIB2"])
    if shutil.which("wgrib2"):
        candidates.append(shutil.which("wgrib2") or "")
    candidates.extend([r"C:\wgrib2\wgrib2.exe", "/usr/local/bin/wgrib2", "/usr/bin/wgrib2"])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise CanSIPSError("wgrib2 was not found; install it or set CANSIPS_WGRIB2/--wgrib2")


def render_product_run(
    args: argparse.Namespace,
    product: dict[str, Any],
    init: str,
    leads: list[int],
    seasonal_leads: list[int],
    wgrib2: str,
    cache_dir: Path,
    output_dir: Path,
    border_paths: list[Path],
    common_reference_dir: Path | None,
) -> tuple[dict[str, Any], int]:
    repo_root = Path(__file__).resolve().parents[1]
    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    run_id = f"cansips-{init}-{product['name']}"
    baseline_label = f"CanSIPS v3 hindcast climatology; {args.climo_start}-{args.climo_end}"
    climatology_method = (
        "forecast 40-member derived snowfall LWE mean minus the matching-"
        "initialization-month and lead hindcast derived snowfall LWE climatology"
        if product["name"] == PRODUCT_SNOWFALL_ANOMALY
        else "forecast 40-member mean minus the matching-initialization-month and lead hindcast climatology"
    )
    common_reference_enabled = (
        product["name"] == PRODUCT_Z500_ANOMALY
        and args.climo_start == CANSIPS_HINDCAST_START
        and args.climo_end == CANSIPS_HINDCAST_END
        and common_reference_dir is not None
    )
    run_entry: dict[str, Any] = {
        "id": run_id,
        "source": "ECCC MSC CanSIPS v3 / Datamart",
        "source_url": CANSIPS_README_URL,
        "source_urls": [CANSIPS_FORECAST_ROOT, CANSIPS_HINDCAST_ROOT, CANSIPS_README_URL],
        "model": "CanSIPS v3",
        "product": product["name"],
        "init_utc": iso_utc(init_date),
        "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
        "ensemble_scope": "40-member CanSIPS v3 blend",
        "member_groups": [
            {"model": "GEM5.2-NEMO", "members": "1-20", "count": 20},
            {"model": "CanESM5", "members": "21-40", "count": 20},
        ],
        "statistic": "ensemble_mean",
        "aggregation": (
            f"{len(seasonal_leads)}-month seasonal "
            f"{'total' if product.get('seasonal_reducer') == 'sum' else 'mean'} "
            "of monthly forecast anomalies"
            if seasonal_leads
            else (
                "monthly 40-member forecast anomaly total"
                if product.get("monthly_reducer") == "total"
                else "monthly 40-member forecast anomaly"
            )
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "conversion": product.get("conversion"),
        "source_variables": product.get("source_variables"),
        "grid": {"longitude_count": 360, "latitude_count": 180, "resolution": "1 degree", "layout": "LatLon1.0"},
        "climatology": {
            "source": "CanSIPS v3 hindcast ensemble means",
            "years": f"{args.climo_start}-{args.climo_end}",
            "initialization_month": init[4:6],
            "method": climatology_method,
        },
        "border_sources": [] if args.no_borders else [{"name": path.name} for path in border_paths],
        "targets": [],
        "status": "planned",
    }
    if common_reference_enabled:
        run_entry["comparison_reference"] = {
            "id": "common_1991_2020",
            "label": "Common 1991-2020 reference (CanSIPS v3 hindcast)",
            "years": "1991-2020",
            "source": baseline_label,
            "directory": relative_path(common_reference_dir, repo_root),
        }
    forecast_grids: dict[int, Grid] = {}
    anomaly_grids: dict[int, Grid] = {}
    target_entries: dict[int, dict[str, Any]] = {}
    failures = 0
    last_request = 0.0
    for lead in leads:
        target = target_month(init, lead)
        valid_start, valid_end = target_period(target)
        target_entry: dict[str, Any] = {
            "id": f"{run_id}-lead{lead:02d}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": lead,
            "target_month": target,
            "aggregation": (
                "monthly forecast anomaly total"
                if product.get("monthly_reducer") == "total"
                else "monthly forecast anomaly"
            ),
            "field": product["field"],
            "units": product["units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
            "source_files": [],
            "status": "planned",
        }
        try:
            if product["name"] == PRODUCT_SNOWFALL_ANOMALY:
                forecast, forecast_source, last_request = load_snowfall_estimate(
                    init,
                    lead,
                    False,
                    cache_dir,
                    repo_root,
                    args.request_delay,
                    last_request,
                    target,
                    args.force_decode,
                    True,
                )
                climatology, hindcast_sources, last_request = snowfall_hindcast_climatology(
                    init,
                    lead,
                    args.climo_start,
                    args.climo_end,
                    cache_dir,
                    repo_root,
                    args.request_delay,
                    last_request,
                    args.force_decode,
                    True,
                )
            else:
                forecast, forecast_source, last_request = load_ensemble_mean(
                    init,
                    lead,
                    False,
                    cache_dir,
                    repo_root,
                    wgrib2,
                    args.request_delay,
                    last_request,
                    product,
                    target,
                    args.force_decode,
                )
                climatology, hindcast_sources, last_request = hindcast_climatology(
                    init, lead, args.climo_start, args.climo_end, cache_dir, repo_root,
                    wgrib2, args.request_delay, last_request, product, args.force_decode,
                )
            anomaly = subtract_grids(forecast, climatology)
            forecast_grids[lead] = forecast
            anomaly_grids[lead] = anomaly
            common_reference_file = None
            if common_reference_enabled:
                common_reference_file = common_reference_dir / f"z500_{target}.csv.gz"
                write_grid_state(climatology, common_reference_file)
            target_entry["source_files"] = forecast_source.get("source_files", [forecast_source])
            target_entry["baseline"] = {
                "source": baseline_label,
                "years": f"{args.climo_start}-{args.climo_end}",
                "initialization_month": init[4:6],
                "lead_month": lead,
                "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
                "method": climatology_method,
                "files": hindcast_sources,
            }
            target_entry["ensemble_complete"] = True
            target_entry["status"] = "decoded"
            if not args.decode_only:
                output_path = output_dir / init[:8] / f"cansips_{product['id_token']}_{target}.jpg"
                render_map(
                    anomaly,
                    init,
                    target,
                    lead,
                    list(range(1, CANSIPS_ENSEMBLE_MEMBERS + 1)),
                    output_path,
                    anomaly=True,
                    baseline_label=baseline_label,
                    border_paths=border_paths,
                    ensemble_label="40-member blend",
                    height_grid=forecast if product["height_contours"] else None,
                    product_spec=product,
                )
                target_entry["image"] = relative_path(output_path, repo_root)
                target_entry["status"] = "rendered"
                if common_reference_enabled:
                    target_entry["comparison"] = {
                        "common_1991_2020": {
                            "image": target_entry["image"],
                            "status": "rendered",
                            "baseline": {
                                "label": baseline_label,
                                "years": "1991-2020",
                                "source": "CanSIPS v3 hindcast climatology",
                                "file": relative_path(common_reference_file, repo_root),
                            },
                        }
                    }
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CanSIPS target {target} lead {lead} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(target_entry)
        target_entries[lead] = target_entry

    if seasonal_leads and not args.decode_only:
        first_lead, last_lead = seasonal_leads[0], seasonal_leads[-1]
        first_target, last_target = target_month(init, first_lead), target_month(init, last_lead)
        seasonal_entry: dict[str, Any] = {
            "id": f"{run_id}-{first_target}-{last_target}",
            "valid_start_utc": target_period(first_target)[0],
            "valid_end_utc": target_period(last_target)[1],
            "lead_month": f"{first_lead}-{last_lead}",
            "target_month": f"{first_target}-{last_target}",
            "aggregation": (
                f"{len(seasonal_leads)}-month seasonal total"
                if product.get("seasonal_reducer") == "sum"
                else f"{len(seasonal_leads)}-month seasonal mean"
            ),
            "field": product["field"],
            "units": product["seasonal_units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
            "monthly_leads": seasonal_leads,
            "source_files": [],
            "status": "planned",
        }
        try:
            if any(lead not in anomaly_grids for lead in seasonal_leads):
                raise CanSIPSError("seasonal window is missing one or more decoded CanSIPS fields")
            seasonal_reducer = product.get("seasonal_reducer", "mean")
            seasonal_anomaly = (
                sum_grids([anomaly_grids[lead] for lead in seasonal_leads])
                if seasonal_reducer == "sum"
                else mean_grids([anomaly_grids[lead] for lead in seasonal_leads])
            )
            seasonal_height = (
                mean_grids([forecast_grids[lead] for lead in seasonal_leads])
                if product["height_contours"]
                else None
            )
            seasonal_entry["source_files"] = [
                source for lead in seasonal_leads for source in target_entries[lead].get("source_files", [])
            ]
            seasonal_entry["baseline"] = {
                "source": baseline_label,
                "years": f"{args.climo_start}-{args.climo_end}",
                "initialization_month": init[4:6],
                "lead_months": seasonal_leads,
                "method": (
                    "sum of monthly forecast-minus-hindcast anomalies"
                    if seasonal_reducer == "sum"
                    else "mean of monthly forecast-minus-hindcast anomalies"
                ),
            }
            period_label = seasonal_period_label(first_target, last_target)
            output_path = output_dir / init[:8] / f"cansips_{product['id_token']}_{first_target}-{last_target}.jpg"
            render_map(
                seasonal_anomaly,
                init,
                first_target,
                f"{first_lead}\u2013{last_lead}",
                list(range(1, CANSIPS_ENSEMBLE_MEMBERS + 1)),
                output_path,
                anomaly=True,
                baseline_label=baseline_label,
                border_paths=border_paths,
                period_label=period_label,
                ensemble_label="40-member blend",
                height_grid=seasonal_height,
                product_spec=product,
                seasonal=True,
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            seasonal_entry["status"] = "rendered"
            if common_reference_enabled:
                seasonal_entry["comparison"] = {
                    "common_1991_2020": {
                        "image": seasonal_entry["image"],
                        "status": "rendered",
                        "baseline": {
                            "label": baseline_label,
                            "years": "1991-2020",
                            "source": "CanSIPS v3 hindcast climatology",
                            "files": [
                                relative_path(
                                    common_reference_dir / f"z500_{target_month(init, lead)}.csv.gz",
                                    repo_root,
                                )
                                for lead in seasonal_leads
                            ],
                        },
                    }
                }
        except Exception as exc:
            failures += 1
            seasonal_entry["status"] = "failed"
            seasonal_entry["error"] = str(exc)
            print(f"CanSIPS seasonal window {first_target}-{last_target} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(seasonal_entry)

    statuses = [target["status"] for target in run_entry["targets"]]
    run_entry["status"] = "failed" if failures and not any(status != "failed" for status in statuses) else (
        "partial" if failures else ("decoded" if args.decode_only else "rendered")
    )
    run_entry["output_dir"] = relative_path(output_dir, repo_root)
    run_entry["generated_utc"] = iso_utc(dt.datetime.now(dt.timezone.utc))
    return run_entry, failures


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    init = parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 0, 11)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 0, 11) if args.seasonal_window else []
    if seasonal_leads:
        expected = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected:
            raise CanSIPSError("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    if args.climo_start < CANSIPS_HINDCAST_START or args.climo_end > CANSIPS_HINDCAST_END or args.climo_start > args.climo_end:
        raise CanSIPSError(
            f"climatology years must stay inside {CANSIPS_HINDCAST_START}-{CANSIPS_HINDCAST_END}"
        )
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    common_reference_dir = resolve_repo_path(args.common_reference_dir, repo_root) if args.common_reference_dir else None
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)
    entries: list[dict[str, Any]] = []
    failures = 0
    products = selected_products(args.product)
    wgrib2 = (
        find_wgrib2(args.wgrib2)
        if any(product["name"] != PRODUCT_SNOWFALL_ANOMALY for product in products)
        else ""
    )
    for product in products:
        entry, product_failures = render_product_run(
            args,
            product,
            init,
            leads,
            seasonal_leads,
            wgrib2,
            cache_dir,
            output_dir,
            border_paths,
            common_reference_dir,
        )
        entries.append(entry)
        failures += product_failures
    previous = resolve_repo_path(args.previous_manifest, repo_root) if args.previous_manifest else None
    write_manifest(manifest_path, repo_root, entries, previous, args.retain_runs)
    print(f"wrote CanSIPS manifest: {manifest_path} ({len(entries)} product run{'s' if len(entries) != 1 else ''})")
    return 2 if failures else 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except CanSIPSError as exc:
        print(f"CanSIPS ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
