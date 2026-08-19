#!/usr/bin/env python3
"""Fetch and render CFSv2 monthly seasonal products.

This is intentionally a standalone seasonal adapter.  WeatherNext frames use
Earth Engine and forecast-hour metadata; CFSv2 seasonal frames use the NOAA
NOMADS monthly ``pgbf``/``flxf`` GRIB2 files and calendar-month lead metadata.

The production anomaly path uses a month-matched CFSv2/reforecast baseline.
The script never substitutes a WeatherNext, ERA5, or MERRA-2 climatology.
``--absolute`` is available only for source/decoder smoke tests and is labelled
as an absolute-height product in the manifest and image.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import urljoin


NOMADS_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/"
NCEI_CALIBRATION_ROOT = "https://www.ncei.noaa.gov/thredds/fileServer/model-cfs_refor_calclim_mm_9m_pgbf/"
NCEI_FLUX_CALIBRATION_ROOT = (
    "https://www.ncei.noaa.gov/thredds/fileServer/"
    "model-cfs-allfile-reforecast/calibration-climatologies/flux-1982-2010/"
)
NCEI_CALIBRATION_YEARS = "1982-2010"
NCEI_CALIBRATION_LABEL = "NCEI CFS reforecast calibration climatology; 1982-2010"
NCEI_FLUX_CALIBRATION_LABEL = "NCEI CFS reforecast flux calibration climatology; 1982-2010"
COMMON_REFERENCE_YEARS = "1991-2020"
COMMON_REFERENCE_LABEL = "Common 1991-2020 reference (CanSIPS v3 hindcast)"
COMMON_REFERENCE_FILENAME = "z500_{target}.csv.gz"
CFS_CYCLE_HOURS = (0, 6, 12, 18)
ROLLING_MEMBER_DEFAULT = 1
GRID_LON_COUNT = 360
GRID_LAT_COUNT = 181
FLUX_GRID_LON_COUNT = 384
FLUX_GRID_LAT_COUNT = 190
ANOMALY_MIN_M = -200.0
ANOMALY_MAX_M = 200.0
PRECIP_ANOMALY_MIN_IN = -8.0
PRECIP_ANOMALY_MAX_IN = 8.0
CFSV2_HEIGHT_ANOMALY_MIN_M = -100.0
CFSV2_HEIGHT_ANOMALY_MAX_M = 100.0
PRECIP_MONTHLY_ANOMALY_MIN_IN = -4.0
PRECIP_MONTHLY_ANOMALY_MAX_IN = 4.0
PRECIP_SEASONAL_ANOMALY_MIN_IN = -8.0
PRECIP_SEASONAL_ANOMALY_MAX_IN = 8.0
SWE_ANOMALY_MIN_IN = -8.0
SWE_ANOMALY_MAX_IN = 8.0
ANOMALY_PALETTE = [
    "#24527a",
    "#306b90",
    "#3d83a6",
    "#4891b0",
    "#539cb8",
    "#61a7bf",
    "#70b2c6",
    "#95c4d3",
    "#c4dce3",
    "#e1e4e7",
    "#eee0e0",
    "#f2cecd",
    "#eaaaa8",
    "#e28c8b",
    "#db797b",
    "#d3686c",
    "#ca5861",
    "#bf4856",
    "#a1384a",
    "#84283f",
]
ANOMALY_TICKS = list(range(-200, 201, 20))
PRECIP_ANOMALY_TICKS = list(range(-8, 9))
CFSV2_HEIGHT_ANOMALY_TICKS = list(range(-100, 101, 10))
PRECIP_MONTHLY_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]
PRECIP_SEASONAL_ANOMALY_TICKS = list(range(-8, 9))
PRECIP_ANOMALY_PALETTE = [
    "#7f3b08",
    "#914b0d",
    "#a6611a",
    "#bd7a2d",
    "#d0a052",
    "#dfbd7d",
    "#ead8b3",
    "#f5ead8",
    "#edf7e9",
    "#d9efd2",
    "#bfe4b6",
    "#9bd694",
    "#74c476",
    "#41ab5d",
    "#238b45",
    "#006d2c",
]
SWE_ANOMALY_TICKS = list(range(-8, 9))
SWE_ANOMALY_PALETTE = [
    "#6b2d0c",
    "#85400f",
    "#a65f1b",
    "#bd7d34",
    "#d09b57",
    "#dfbd84",
    "#ead9b8",
    "#ffffff",
    "#ffffff",
    "#b9dce8",
    "#68aec8",
    "#448fb4",
    "#2f7198",
    "#245b83",
    "#1d496f",
    "#143b5f",
]
TEMPERATURE_ANOMALY_TICKS = list(range(-8, 9))
CFSV2_TEMPERATURE_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]
TEMPERATURE_ANOMALY_PALETTE = [
    "#24527a",
    "#306b90",
    "#3d83a6",
    "#4891b0",
    "#539cb8",
    "#70b2c6",
    "#95c4d3",
    "#e1e4e7",
    "#f2cecd",
    "#eaaaa8",
    "#e28c8b",
    "#db797b",
    "#d3686c",
    "#ca5861",
    "#a1384a",
    "#84283f",
]
MSLP_ANOMALY_TICKS = list(range(-20, 21, 2))
CFSV2_MSLP_ANOMALY_TICKS = list(range(-10, 11))
MSLP_ANOMALY_PALETTE = ANOMALY_PALETTE
# A social-sized North America view: retain Alaska and all of Greenland while
# keeping the lower field in the subtropics. Border drawing applies a separate
# 14°N cutoff so South America does not appear in the frame.
DEFAULT_REGION = (-160.0, -10.0, 22.0, 85.0)
# Precipitation is a CONUS-only product: the lower 48 states remain the
# subject of the map, with a small surrounding margin for geographic context.
CONUS_PRECIP_REGION = (-128.0, -65.0, 22.0, 52.0)
# Shift the projected window slightly west so the CONUS sits at the visual
# center of the square canvas while preserving Alaska and all of Greenland.
PROJECTED_X_SHIFT_FRACTION = 0.035
DEFAULT_BORDER_URLS = (
    (
        "countries.geojson",
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
    ),
    (
        "us-states.geojson",
        "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json",
    ),
)

PRODUCT_HEIGHT_ANOMALY = "500mb_height_anomaly"
PRODUCT_HEIGHT_ABSOLUTE = "500mb_height_absolute"
PRODUCT_2M_TEMPERATURE_ANOMALY = "2m_temperature_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_SWE_ANOMALY = "snow_water_equivalent_anomaly"

# The NOMADS filenames retain the ``pgbf.`` and ``flxf.`` product prefixes.
# The FLXF monthly files are on the native CFSv2 Gaussian grid. Keep the
# source field and conversion metadata explicit so a manifest can explain
# exactly how each displayed surface product was made.
PRODUCT_SPECS = {
    PRODUCT_HEIGHT_ANOMALY: {
        "name": PRODUCT_HEIGHT_ANOMALY,
        "source_kind": "pgbf",
        "match": ":HGT:500 mb:",
        "raw_field": "HGT:500 mb",
        "raw_units": "m",
        "field": "z500_anomaly",
        "units": "m",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "hgt500",
        "state_tag": "hgt500",
        "id_token": "z500a",
        "file_token": "z500a",
        "title": "CFSv2 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "CFSv2 500-mb Geopotential Height (m)",
        "height_contours": True,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "m",
        "monthly_aggregation": "monthly forecast average",
        "anomaly_min": CFSV2_HEIGHT_ANOMALY_MIN_M,
        "anomaly_max": CFSV2_HEIGHT_ANOMALY_MAX_M,
        "anomaly_ticks": CFSV2_HEIGHT_ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
    },
    PRODUCT_HEIGHT_ABSOLUTE: {
        "name": PRODUCT_HEIGHT_ABSOLUTE,
        "source_kind": "pgbf",
        "match": ":HGT:500 mb:",
        "raw_field": "HGT:500 mb",
        "raw_units": "m",
        "field": "z500",
        "units": "m",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "hgt500",
        "state_tag": "hgt500",
        "id_token": "z500-absolute",
        "file_token": "z500",
        "title": "CFSv2 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "CFSv2 500-mb Geopotential Height (m)",
        "height_contours": True,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "m",
        "monthly_aggregation": "monthly forecast average",
    },
    PRODUCT_2M_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "source_kind": "flxf",
        "match": ":TMP:2 m above ground:",
        "raw_field": "TMP:2 m above ground",
        "raw_units": "K",
        "field": "t2m_anomaly",
        "units": "°C",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "tmp2m",
        "state_tag": "tmp2m",
        "id_token": "t2ma",
        "file_token": "t2ma",
        "title": "CFSv2 2-m Temperature Anomaly (°C)",
        "absolute_title": "CFSv2 2-m Temperature (°C)",
        "region": DEFAULT_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "°C",
        "monthly_aggregation": "monthly mean 2-m temperature",
        "anomaly_min": -4.0,
        "anomaly_max": 4.0,
        "anomaly_ticks": CFSV2_TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "conversion": "Kelvin offset cancels in forecast-minus-calibration anomalies; displayed in °C",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "source_kind": "pgbf",
        "match": ":PRES:mean sea level:",
        "raw_field": "PRES:mean sea level",
        "raw_units": "Pa",
        "field": "mslp_anomaly",
        "units": "hPa",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "mslp",
        "state_tag": "mslp",
        "id_token": "mslpa",
        "file_token": "mslpa",
        "title": "CFSv2 Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "CFSv2 Mean Sea-Level Pressure (hPa)",
        "region": DEFAULT_REGION,
        "height_contours": False,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "hPa",
        "monthly_aggregation": "monthly mean sea-level pressure",
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "PRES divided by 100 to convert Pa to hPa before calculating the anomaly",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": CFSV2_MSLP_ANOMALY_TICKS,
        "anomaly_palette": MSLP_ANOMALY_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "source_kind": "flxf",
        "match": ":PRATE:surface:",
        "raw_field": "PRATE:surface",
        "raw_units": "kg m-2 s-1",
        "field": "precipitation_anomaly",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "prate",
        # Keep inch-state files separate from the earlier mm implementation so
        # a retained rolling grid can never be reused with the wrong units.
        "state_tag": "prate_in",
        "id_token": "prate-anomaly",
        "file_token": "pratea",
        "title": "CFSv2 CONUS Precipitation Anomaly (in)",
        "absolute_title": "CFSv2 CONUS Precipitation (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "sum",
        "seasonal_aggregation": "seasonal total",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly total precipitation",
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "PRATE multiplied by calendar-month seconds, converted from mm to inches",
    },
    PRODUCT_SWE_ANOMALY: {
        "name": PRODUCT_SWE_ANOMALY,
        "source_kind": "flxf",
        "match": ":WEASD:surface:",
        "raw_field": "WEASD:surface",
        "raw_units": "kg m-2",
        "field": "snow_water_equivalent_anomaly",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "weasd",
        "state_tag": "weasd_in",
        "id_token": "swe-anomaly",
        "file_token": "swea",
        "title": "CFSv2 Snow-Water-Equivalent Anomaly (in)",
        "absolute_title": "CFSv2 Snow-Water Equivalent (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean snow-water equivalent",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly snow-water-equivalent average",
        "conversion_kind": "snow_water_equivalent_inches",
        "conversion": "WEASD divided by 25.4 to convert kg m-2/mm of liquid water equivalent to inches",
    },
}


class CFSv2Error(RuntimeError):
    """A user-actionable CFSv2 pipeline error."""


@dataclass
class Grid:
    """A longitude/latitude grid represented without a hard dependency."""

    lons: list[float]
    lats: list[float]
    values: list[list[float]]

    def assert_compatible(self, other: "Grid", label: str) -> None:
        if self.lons != other.lons or self.lats != other.lats:
            raise CFSv2Error(f"{label} grid does not match the forecast grid")


def get_product_spec(product: str) -> dict:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        available = ", ".join(sorted(PRODUCT_SPECS))
        raise CFSv2Error(f"unsupported CFSv2 product {product!r}; choose from {available}") from exc


def selected_product(args: argparse.Namespace) -> tuple[str, dict, bool]:
    product = getattr(args, "product", PRODUCT_HEIGHT_ANOMALY)
    if getattr(args, "absolute", False):
        if product not in {PRODUCT_HEIGHT_ANOMALY, PRODUCT_HEIGHT_ABSOLUTE}:
            raise CFSv2Error("--absolute is only valid with the 500mb_height_absolute product")
        product = PRODUCT_HEIGHT_ABSOLUTE
    spec = get_product_spec(product)
    return product, spec, product == PRODUCT_HEIGHT_ABSOLUTE


def anomaly_style(
    product_spec: dict,
    seasonal: bool = False,
) -> tuple[float, float, Sequence[float], Sequence[str]]:
    """Return the fixed comparable scale for one anomaly product and period."""

    if "anomaly_min" in product_spec:
        return (
            float(product_spec["anomaly_min"]),
            float(product_spec["anomaly_max"]),
            product_spec.get("anomaly_ticks", []),
            product_spec.get("anomaly_palette", ANOMALY_PALETTE),
        )
    if product_spec["name"] == PRODUCT_PRECIPITATION_ANOMALY:
        if seasonal:
            return (
                PRECIP_SEASONAL_ANOMALY_MIN_IN,
                PRECIP_SEASONAL_ANOMALY_MAX_IN,
                PRECIP_SEASONAL_ANOMALY_TICKS,
                PRECIP_ANOMALY_PALETTE,
            )
        return (
            PRECIP_MONTHLY_ANOMALY_MIN_IN,
            PRECIP_MONTHLY_ANOMALY_MAX_IN,
            PRECIP_MONTHLY_ANOMALY_TICKS,
            PRECIP_ANOMALY_PALETTE,
        )
    if product_spec["name"] == PRODUCT_SWE_ANOMALY:
        return (
            SWE_ANOMALY_MIN_IN,
            SWE_ANOMALY_MAX_IN,
            SWE_ANOMALY_TICKS,
            SWE_ANOMALY_PALETTE,
        )
    return ANOMALY_MIN_M, ANOMALY_MAX_M, ANOMALY_TICKS, ANOMALY_PALETTE


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def parse_init(value: str) -> str:
    if not re.fullmatch(r"\d{10}", value):
        raise CFSv2Error("--init must be YYYYMMDDHH or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d%H")
    except ValueError as exc:
        raise CFSv2Error(f"invalid CFSv2 initialization time: {value}") from exc
    if parsed.hour not in (0, 6, 12, 18):
        raise CFSv2Error("CFSv2 initialization hour must be 00, 06, 12, or 18")
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
            raise CFSv2Error(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise CFSv2Error(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise CFSv2Error(f"{label} cannot be empty")
    return result


def month_after(year: int, month: int, lead_months: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + lead_months
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead_months: int) -> str:
    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(init_date.year, init_date.month, lead_months)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m")
    end_year, end_month = month_after(start.year, start.month, 1)
    end = dt.datetime(end_year, end_month, 1)
    return iso_utc(start.replace(tzinfo=dt.timezone.utc)), iso_utc(end.replace(tzinfo=dt.timezone.utc))


def seasonal_period_label(first_target: str, last_target: str) -> str:
    """Use standard meteorological season shorthand for three-month windows."""

    start = dt.datetime.strptime(first_target, "%Y%m")
    end = dt.datetime.strptime(last_target, "%Y%m")
    season = {
        (12, 2): f"DJF {end.year}",
        (3, 5): f"MAM {end.year}",
        (6, 8): f"JJA {end.year}",
        (9, 11): f"SON {end.year}",
    }.get((start.month, end.month))
    if season and ((start.month == 12 and end.year == start.year + 1) or end.year == start.year):
        return season
    if start.year == end.year:
        return f"{start:%b}\u2013{end:%b %Y}"
    return f"{start:%b %Y}\u2013{end:%b %Y}"


def discover_latest_init(root: str = NOMADS_ROOT) -> str:
    """Select the newest listed cycle from the official NOMADS directory."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required when --init latest is used") from exc

    try:
        response = requests.get(root, timeout=(20, 60))
        response.raise_for_status()
    except Exception as exc:
        raise CFSv2Error(f"could not read the NOMADS CFSv2 directory: {exc}") from exc
    dates = sorted(set(re.findall(r'href="cfs\.(\d{8})/"', response.text)), reverse=True)
    if not dates:
        raise CFSv2Error("could not find a cfs.YYYYMMDD cycle in the NOMADS index")
    for date_text in dates:
        for hour in (18, 12, 6, 0):
            candidate = f"{date_text}{hour:02d}"
            if dt.datetime.strptime(candidate, "%Y%m%d%H") <= dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
                return candidate
    raise CFSv2Error("NOMADS listed no usable CFSv2 cycle")


def find_wgrib2(explicit: str) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CFSV2_WGRIB2"):
        candidates.append(os.environ["CFSV2_WGRIB2"])
    found = shutil.which("wgrib2")
    if found:
        candidates.append(found)
    candidates.append(r"C:\wgrib2\wgrib2.exe")
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    raise CFSv2Error(
        "wgrib2 was not found; install it or set CFSV2_WGRIB2/--wgrib2 to the executable path"
    )


def cfs_file_url(init: str, member: int, target: str, source_kind: str = "pgbf") -> str:
    date_text, hour_text = init[:8], init[8:]
    filename = f"{source_kind}.{member:02d}.{init}.{target}.avrg.grib.grb2"
    return urljoin(
        NOMADS_ROOT,
        f"cfs.{date_text}/{hour_text}/monthly_grib_{member:02d}/{filename}",
    )


def cached_source_path(
    cache_dir: Path,
    init: str,
    member: int,
    target: str,
    source_kind: str = "pgbf",
) -> Path:
    filename = Path(cfs_file_url(init, member, target, source_kind)).name
    return cache_dir / init / f"member_{member:02d}" / filename


def ncei_calibration_url(init: str, lead: int, source_kind: str = "pgbf") -> str:
    month, day, hour = init[4:6], init[6:8], init[8:]
    filename = f"{source_kind}.{month}.{day}.{hour}.l{lead:02d}.fclm.{NCEI_CALIBRATION_YEARS.replace('-', '.')}.grb2"
    root = NCEI_FLUX_CALIBRATION_ROOT if source_kind == "flxf" else NCEI_CALIBRATION_ROOT
    return urljoin(root, f"{month}/{filename}")


def cached_calibration_path(
    cache_dir: Path,
    init: str,
    lead: int,
    source_kind: str = "pgbf",
) -> Path:
    return cache_dir / "calibration" / source_kind / init / Path(
        ncei_calibration_url(init, lead, source_kind)
    ).name


def rolling_cycle_inits(end_init: str, cycle_count: int) -> list[str]:
    """Return the most recent six-hourly cycles, oldest first."""

    if cycle_count < 1:
        raise CFSv2Error("rolling cycle count must be positive")
    end_date = dt.datetime.strptime(end_init, "%Y%m%d%H")
    return [
        (end_date - dt.timedelta(hours=6 * offset)).strftime("%Y%m%d%H")
        for offset in range(cycle_count - 1, -1, -1)
    ]


def lead_for_target(init: str, target: str) -> int:
    """Find the monthly lead that reaches a fixed target month."""

    for lead in range(1, 10):
        if target_month(init, lead) == target:
            return lead
    raise CFSv2Error(f"CFSv2 cycle {init} has no 1-9 month lead for target {target}")


def rolling_state_path(
    state_dir: Path,
    init: str,
    member: int,
    target: str,
    state_tag: str = "hgt500",
) -> Path:
    if state_tag == "hgt500":
        # Preserve the original height-state layout so existing rolling cache
        # entries remain usable after adding the FLXF product.
        return state_dir / target / f"hgt500.{init}.m{member:02d}.csv.gz"
    return state_dir / state_tag / target / f"{state_tag}.{init}.m{member:02d}.csv.gz"


def write_grid_state(grid: Grid, path: Path) -> None:
    """Persist a decoded grid compactly so it survives the 7-day NOMADS rotation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("lon", "lat", "value"))
        for lat, row in zip(grid.lats, grid.values):
            for lon, value in zip(grid.lons, row):
                writer.writerow((lon, lat, value))
    temporary.replace(path)


def read_grid_state(path: Path) -> Grid:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(path))


def download_file(
    url: str,
    destination: Path,
    request_delay: float,
    last_request: float,
    *,
    attempts: int = 1,
    timeout: tuple[int, int] = (30, 300),
) -> tuple[bool, float]:
    if destination.exists() and destination.stat().st_size > 0:
        return False, last_request
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required to download CFSv2 files") from exc

    elapsed = time.monotonic() - last_request if last_request else request_delay
    if last_request and elapsed < request_delay:
        time.sleep(request_delay - elapsed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, stream=True, timeout=timeout)
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            if partial.stat().st_size == 0:
                raise CFSv2Error(f"empty download from {url}")
            partial.replace(destination)
            return True, time.monotonic()
        except Exception:
            if partial.exists():
                partial.unlink()
            if attempt >= attempts:
                raise
            time.sleep(min(30.0, float(2 ** (attempt - 1))))
    raise AssertionError("download retry loop did not return or raise")


def common_reference_path(directory: Path, target: str) -> Path:
    return directory / COMMON_REFERENCE_FILENAME.format(target=target)


def common_reference_url(root: str, target: str) -> str:
    return urljoin(root.rstrip("/") + "/", common_reference_path(Path("."), target).name)


def load_common_reference(
    target: str,
    directory: Path | None,
    url_root: str,
    request_delay: float,
    last_request: float,
) -> tuple[Grid, Path, str, bool, float]:
    """Load the published CanSIPS 1991-2020 reference grid for a target month."""

    if directory is None and not url_root:
        raise CFSv2Error("a common-reference directory or URL is required")
    local_directory = directory or Path(".cache/common-reference")
    path = common_reference_path(local_directory, target)
    url = common_reference_url(url_root, target) if url_root else ""
    downloaded = False
    if not path.exists() or path.stat().st_size == 0:
        if not url:
            raise CFSv2Error(f"common 1991-2020 reference is missing for {target}: {path}")
        downloaded, last_request = download_file(
            url,
            path,
            request_delay,
            last_request,
            attempts=3,
            timeout=(30, 120),
        )
    try:
        grid = read_grid_state(path) if path.suffix == ".gz" else read_grid_csv(path)
    except Exception as exc:
        raise CFSv2Error(f"could not decode common 1991-2020 reference {path}: {exc}") from exc
    return grid, path, url, downloaded, last_request


def regrid_nearest(grid: Grid, lons: Sequence[float], lats: Sequence[float], label: str) -> Grid:
    """Regrid a smooth global reference field to the forecast axes by nearest point."""

    def lon_distance(left: float, right: float) -> float:
        difference = abs(left - right) % 360.0
        return min(difference, 360.0 - difference)

    lon_indices = [
        min(range(len(grid.lons)), key=lambda index: lon_distance(grid.lons[index], lon))
        for lon in lons
    ]
    lat_indices = [
        min(range(len(grid.lats)), key=lambda index: abs(grid.lats[index] - lat))
        for lat in lats
    ]
    if not lon_indices or not lat_indices:
        raise CFSv2Error(f"{label} reference grid has no usable axes")
    values = [
        [grid.values[lat_index][lon_index] for lon_index in lon_indices]
        for lat_index in lat_indices
    ]
    return Grid(list(lons), list(lats), values)


def _float_or_nan(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def _normalize_lon(value: float) -> float:
    lon = value % 360.0
    if lon > 180.0:
        lon -= 360.0
    return round(lon, 6)


def grid_from_rows(
    rows: Iterable[Sequence[str]],
    source: str,
    expected_shape: tuple[int, int] | None = None,
) -> Grid:
    points: dict[tuple[float, float], float] = {}
    for row in rows:
        if len(row) < 3:
            continue
        lon = _float_or_nan(row[-3])
        lat = _float_or_nan(row[-2])
        value = _float_or_nan(row[-1])
        if not all(math.isfinite(item) for item in (lon, lat)):
            continue
        points[(_normalize_lon(lon), round(lat, 6))] = value

    lons = sorted({lon for lon, _ in points})
    lats = sorted({lat for _, lat in points})
    if expected_shape and (len(lons), len(lats)) != expected_shape:
        expected_lons, expected_lats = expected_shape
        raise CFSv2Error(
            f"{source} did not decode the expected {expected_lons}x{expected_lats} grid "
            f"(got {len(lons)}x{len(lats)})"
        )
    if len(lons) < 2 or len(lats) < 2:
        raise CFSv2Error(f"{source} did not decode a usable longitude/latitude grid")
    values = []
    for lat in lats:
        row = []
        for lon in lons:
            if (lon, lat) not in points:
                raise CFSv2Error(f"{source} has a missing grid point at {lon},{lat}")
            row.append(points[(lon, lat)])
        values.append(row)
    return Grid(lons=lons, lats=lats, values=values)


def read_grid_csv(csv_path: Path, expected_shape: tuple[int, int] | None = None) -> Grid:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(csv_path), expected_shape)


def decode_grib(
    grib_path: Path,
    wgrib2: str,
    force: bool = False,
    match_pattern: str = ":HGT:500 mb:",
    cache_tag: str = "hgt500",
    expected_shape: tuple[int, int] | None = None,
) -> Grid:
    csv_path = grib_path.with_name(grib_path.name + f".{cache_tag}.csv")
    if force or not csv_path.exists() or csv_path.stat().st_size == 0:
        command = [wgrib2, str(grib_path), "-match", match_pattern, "-csv", str(csv_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "wgrib2 failed").strip()
            raise CFSv2Error(f"wgrib2 failed for {grib_path.name}: {detail[-800:]}")
    return read_grid_csv(csv_path, expected_shape)


def transform_grid(grid: Grid, transform: Callable[[float], float]) -> Grid:
    values = []
    for row in grid.values:
        values.append([transform(value) if math.isfinite(value) else math.nan for value in row])
    return Grid(grid.lons[:], grid.lats[:], values)


def monthly_precipitation_total_inches(grid: Grid, target: str) -> Grid:
    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    end = dt.datetime(next_year, next_month, 1)
    seconds = (end - start).total_seconds()
    return transform_grid(grid, lambda value: value * seconds / 25.4)


def snow_water_equivalent_inches(grid: Grid) -> Grid:
    """Convert WEASD from kg m-2 (equivalent to mm of liquid water) to inches."""

    return transform_grid(grid, lambda value: value / 25.4)


def prepare_product_grid(grid: Grid, product_spec: dict, target: str) -> Grid:
    conversion_kind = product_spec.get("conversion_kind")
    if conversion_kind == "monthly_precipitation_total_inches":
        return monthly_precipitation_total_inches(grid, target)
    if conversion_kind == "snow_water_equivalent_inches":
        return snow_water_equivalent_inches(grid)
    if conversion_kind == "pascals_to_hectopascals":
        return transform_grid(grid, lambda value: value / 100.0)
    return grid


def mean_grids(grids: Sequence[Grid]) -> Grid:
    if not grids:
        raise CFSv2Error("cannot average an empty CFSv2 member set")
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "ensemble member")
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        mean_row = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [sample for sample in samples if math.isfinite(sample)]
            mean_row.append(sum(finite) / len(finite) if finite else math.nan)
        values.append(mean_row)
    return Grid(first.lons[:], first.lats[:], values)


def sum_grids(grids: Sequence[Grid]) -> Grid:
    if not grids:
        raise CFSv2Error("cannot sum an empty CFSv2 grid set")
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "seasonal member")
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        sum_row = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [sample for sample in samples if math.isfinite(sample)]
            sum_row.append(sum(finite) if finite else math.nan)
        values.append(sum_row)
    return Grid(first.lons[:], first.lats[:], values)


def decode_target_ensemble(
    args: argparse.Namespace,
    init: str,
    target: str,
    members: Sequence[int],
    rolling_inits: Sequence[str],
    cache_dir: Path,
    state_dir: Path,
    wgrib2: str,
    repo_root: Path,
    last_request: float,
    product_spec: dict,
) -> tuple[Grid, list[dict], int, int, str, float]:
    """Decode either the original single-cycle ensemble or a rolling blend."""

    source_kind = product_spec["source_kind"]

    def prepare_grid(grid: Grid) -> Grid:
        return prepare_product_grid(grid, product_spec, target)

    def source_metadata() -> dict:
        metadata = {
            "product": product_spec["name"],
            "source_kind": source_kind.upper(),
            "decoded_field": product_spec["raw_field"],
            "raw_units": product_spec["raw_units"],
            "units": product_spec["units"],
        }
        if product_spec.get("conversion"):
            metadata["conversion"] = product_spec["conversion"]
        return metadata

    grids: list[Grid] = []
    source_files: list[dict] = []
    if rolling_inits:
        expected_count = len(rolling_inits)
        rolling_member = args.rolling_member
        for cycle in rolling_inits:
            cycle_lead = lead_for_target(cycle, target)
            url = cfs_file_url(cycle, rolling_member, target, source_kind)
            cache_path = cached_source_path(cache_dir, cycle, rolling_member, target, source_kind)
            state_path = rolling_state_path(
                state_dir,
                cycle,
                rolling_member,
                target,
                product_spec["state_tag"],
            )
            source_file = {
                "initialization": cycle,
                "initialization_utc": iso_utc(dt.datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
                "lead_month": cycle_lead,
                "member": rolling_member,
                "url": url,
                "cache_file": relative_path(cache_path, repo_root),
                "state_file": relative_path(state_path, repo_root),
            }
            try:
                downloaded, last_request = download_file(
                    url,
                    cache_path,
                    max(0.0, args.request_delay),
                    last_request,
                )
                grid = prepare_grid(
                    decode_grib(
                        cache_path,
                        wgrib2,
                        force=args.force_decode,
                        match_pattern=product_spec["match"],
                        cache_tag=product_spec["cache_tag"],
                        expected_shape=product_spec["grid_shape"],
                    )
                )
                write_grid_state(grid, state_path)
                if rolling_inits:
                    # The compressed decoded state is the durable rolling input;
                    # do not grow the CI cache with dozens of 25-MB GRIB2 files.
                    decoded_csv = cache_path.with_name(
                        cache_path.name + f".{product_spec['cache_tag']}.csv"
                    )
                    for temporary_source in (cache_path, decoded_csv):
                        try:
                            temporary_source.unlink()
                        except FileNotFoundError:
                            pass
                source_file.update(
                    {
                        "storage": "nomads_grib2",
                        "downloaded": downloaded,
                    }
                )
                source_file.update(source_metadata())
            except Exception as exc:
                if state_path.exists():
                    grid = read_grid_state(state_path)
                    source_file.update(
                        {
                            "storage": "retained_decoded_grid",
                            "downloaded": False,
                            "download_error": str(exc),
                        }
                    )
                    source_file.update(source_metadata())
                elif args.allow_partial_rolling:
                    source_file.update({"status": "missing", "error": str(exc)})
                    source_files.append(source_file)
                    continue
                else:
                    raise CFSv2Error(
                        f"rolling CFSv2 cycle {cycle} is unavailable and has no retained grid; "
                        "the NOMADS archive rotates after seven days, so run the scheduled job "
                        "twice daily or use --allow-partial-rolling"
                    ) from exc
            source_file["status"] = "available"
            source_files.append(source_file)
            grids.append(grid)
        if not grids:
            raise CFSv2Error("rolling CFSv2 window produced no usable member grids")
        if len(grids) < expected_count and not args.allow_partial_rolling:
            raise CFSv2Error(
                f"rolling CFSv2 window has {len(grids)} of {expected_count} members; "
                "use --allow-partial-rolling only for an explicitly incomplete product"
            )
        label = f"{len(grids)}/{expected_count}-cycle rolling mean"
        return mean_grids(grids), source_files, len(grids), expected_count, label, last_request

    for member in members:
        url = cfs_file_url(init, member, target, source_kind)
        cache_path = cached_source_path(cache_dir, init, member, target, source_kind)
        downloaded, last_request = download_file(
            url,
            cache_path,
            max(0.0, args.request_delay),
            last_request,
        )
        grid = prepare_grid(
            decode_grib(
                cache_path,
                wgrib2,
                force=args.force_decode,
                match_pattern=product_spec["match"],
                cache_tag=product_spec["cache_tag"],
                expected_shape=product_spec["grid_shape"],
            )
        )
        grids.append(grid)
        source_file = {
            "initialization": init,
            "initialization_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "lead_month": lead_for_target(init, target),
            "member": member,
            "url": url,
            "cache_file": relative_path(cache_path, repo_root),
            "downloaded": downloaded,
            "status": "available",
        }
        source_file.update(source_metadata())
        source_files.append(source_file)
    return mean_grids(grids), source_files, len(grids), len(grids), f"{len(grids)}-member mean", last_request


def subtract_grids(left: Grid, right: Grid) -> Grid:
    left.assert_compatible(right, "baseline")
    values = []
    for left_row, right_row in zip(left.values, right.values):
        values.append(
            [
                (a - b) if math.isfinite(a) and math.isfinite(b) else math.nan
                for a, b in zip(left_row, right_row)
            ]
        )
    return Grid(left.lons[:], left.lats[:], values)


def load_baseline(path: Path, wgrib2: str, product_spec: dict, target: str) -> Grid:
    suffix = path.suffix.lower()
    if suffix in {".grb2", ".grib2", ".grib"}:
        grid = decode_grib(
            path,
            wgrib2,
            match_pattern=product_spec["match"],
            cache_tag=f"{product_spec['cache_tag']}_baseline",
            expected_shape=product_spec["grid_shape"],
        )
        return prepare_product_grid(grid, product_spec, target)
    return read_grid_csv(path)


def baseline_for_target(args: argparse.Namespace, target: str, repo_root: Path) -> tuple[Path, str]:
    if args.baseline_file:
        path = resolve_repo_path(args.baseline_file, repo_root)
        if not path.exists():
            raise CFSv2Error(f"baseline file does not exist: {path}")
        return path, args.baseline_label or path.name
    if args.baseline_dir:
        directory = resolve_repo_path(args.baseline_dir, repo_root)
        product_name = getattr(args, "product", "")
        prefix = (
            "prate"
            if product_name.startswith("precipitation")
            else "weasd"
            if product_name == PRODUCT_SWE_ANOMALY
            else "tmp2m"
            if product_name == PRODUCT_2M_TEMPERATURE_ANOMALY
            else "mslp"
            if product_name == PRODUCT_MSLP_ANOMALY
            else "z500"
        )
        candidates = (
            f"{prefix}_{target}.csv",
            f"{prefix}_{target}.grb2",
            f"{prefix}_{target}.grib2",
            f"baseline_{target}.csv",
            f"baseline_{target}.grb2",
            f"{target}.csv",
            f"{target}.grb2",
        )
        for name in candidates:
            path = directory / name
            if path.exists():
                return path, args.baseline_label or name
        raise CFSv2Error(f"no baseline grid for target month {target} in {directory}")
    raise CFSv2Error(
        "anomaly rendering requires --baseline-file or --baseline-dir; "
        "use --ncei-calibration or --absolute for a clearly labelled alternative"
    )


def configured_baseline_label(args: argparse.Namespace) -> str:
    if args.baseline_label:
        return args.baseline_label
    if args.ncei_calibration:
        product = getattr(args, "product", PRODUCT_HEIGHT_ANOMALY)
        return get_product_spec(product)["baseline_label"]
    return "user-supplied CFSv2/reforecast baseline"


def _finite_values(grid: Grid) -> Iterator[float]:
    for row in grid.values:
        for value in row:
            if math.isfinite(value):
                yield value


def _geojson_rings(geometry: dict) -> Iterator[list[list[float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        if coordinates:
            yield coordinates[0]
    elif kind == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                yield polygon[0]
    elif kind == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _geojson_rings(child)


def geojson_features(payload: dict) -> Iterator[list[list[float]]]:
    if payload.get("type") == "FeatureCollection":
        for feature in payload.get("features", []):
            geometry = feature.get("geometry") or {}
            yield from _geojson_rings(geometry)
    elif payload.get("type") == "Feature":
        yield from _geojson_rings(payload.get("geometry") or {})
    else:
        yield from _geojson_rings(payload)


def land_mask_from_borders(border_paths: Sequence[Path], longitude_values, latitude_values):
    """Return a land-only mask for a projected longitude/latitude canvas."""
    try:
        import numpy as np
        from matplotlib.path import Path as MatplotlibPath
    except ImportError:  # pragma: no cover - matplotlib is required by render_map
        return None

    longitudes = np.asarray(longitude_values, dtype=float)
    latitudes = np.asarray(latitude_values, dtype=float)
    points = np.column_stack((longitudes.ravel(), latitudes.ravel()))
    land = np.zeros(points.shape[0], dtype=bool)
    country_paths = [path for path in border_paths if path.name == "countries.geojson"]
    for border_path in country_paths:
        try:
            payload = json.loads(border_path.read_text(encoding="utf-8"))
            for ring in geojson_features(payload):
                vertices = np.asarray(ring, dtype=float)
                if vertices.ndim != 2 or vertices.shape[0] < 3 or vertices.shape[1] < 2:
                    continue
                land |= MatplotlibPath(vertices[:, :2], closed=True).contains_points(points)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return land.reshape(longitudes.shape)


def ensure_border_files(args: argparse.Namespace, cache_dir: Path, repo_root: Path) -> list[Path]:
    if args.no_borders:
        return []
    if args.border_geojson:
        paths = [resolve_repo_path(item, repo_root) for item in args.border_geojson]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise CFSv2Error(f"border GeoJSON does not exist: {', '.join(missing)}")
        return paths
    try:
        import requests
    except ImportError:
        print("warning: requests unavailable; continuing without map borders", file=sys.stderr)
        return []
    border_dir = cache_dir / "borders"
    paths: list[Path] = []
    for filename, url in DEFAULT_BORDER_URLS:
        destination = border_dir / filename
        if not destination.exists() or destination.stat().st_size == 0:
            try:
                border_dir.mkdir(parents=True, exist_ok=True)
                response = requests.get(url, timeout=(20, 120))
                response.raise_for_status()
                destination.write_bytes(response.content)
            except Exception as exc:
                print(f"warning: could not download {filename}; continuing without it: {exc}", file=sys.stderr)
                continue
        paths.append(destination)
    return paths


def render_map(
    grid: Grid,
    init: str,
    target: str,
    lead: int | str,
    members: Sequence[int],
    output_path: Path,
    anomaly: bool,
    baseline_label: str,
    border_paths: Sequence[Path],
    period_label: str = "",
    seasonal: bool = False,
    ensemble_label: str = "",
    height_grid: Grid | None = None,
    region: tuple[float, float, float, float] = DEFAULT_REGION,
    product_spec: dict | None = None,
    initialization_label: str = "",
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.colors as mcolors
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:  # pragma: no cover - target installs requirements.txt
        raise CFSv2Error("rendering requires numpy and matplotlib; install requirements.txt") from exc

    product_spec = product_spec or PRODUCT_SPECS[PRODUCT_HEIGHT_ANOMALY]
    region = product_spec.get("region", region)
    if product_spec["height_contours"]:
        # Absolute products can contour their own field.  An anomaly product
        # must never use the anomaly grid as a substitute for absolute heights:
        # doing so produces lines labelled as dam that are actually anomaly
        # values.  C3S/JMA raw geopotential decoding can legitimately be
        # unavailable for a partial run, so fail closed and omit the overlay.
        if height_grid is None and not anomaly:
            height_grid = grid
        if height_grid is not None:
            height_grid.assert_compatible(grid, "height contour")
    else:
        height_grid = None
    lon_min, lon_max, lat_min, lat_max = region
    source_lons = np.asarray(grid.lons, dtype=float)
    source_lats = np.asarray(grid.lats, dtype=float)
    source_data = np.asarray(grid.values, dtype=float)
    source_height = (
        np.asarray(height_grid.values, dtype=float) / 10.0
        if height_grid is not None
        else None
    )
    if source_data.shape != (source_lats.size, source_lons.size):
        raise CFSv2Error("decoded CFSv2 grid has inconsistent latitude/longitude dimensions")
    if source_lons.size < 2 or source_lats.size < 2:
        raise CFSv2Error("decoded CFSv2 grid is too small to project")
    if np.any(np.diff(source_lons) <= 0.0) or np.any(np.diff(source_lats) <= 0.0):
        raise CFSv2Error("decoded CFSv2 grid longitude/latitude coordinates must be sorted")

    # Match the centered ECMWF-style North America Lambert Conformal Conic
    # framing: a -100° meridian center, 45° latitude origin, and broad
    # 30°/60° standard parallels.
    standard_parallel_1 = np.deg2rad(30.0)
    standard_parallel_2 = np.deg2rad(60.0)
    latitude_origin = np.deg2rad(45.0)
    central_longitude = np.deg2rad(-100.0)
    n_coefficient = np.log(np.cos(standard_parallel_1) / np.cos(standard_parallel_2)) / np.log(
            np.tan(np.pi / 4.0 + standard_parallel_2 / 2.0)
            / np.tan(np.pi / 4.0 + standard_parallel_1 / 2.0)
    )
    scale = (
        np.cos(standard_parallel_1)
        * np.tan(np.pi / 4.0 + standard_parallel_1 / 2.0) ** n_coefficient
        / n_coefficient
    )
    origin_radius = scale / np.tan(np.pi / 4.0 + latitude_origin / 2.0) ** n_coefficient

    def lcc_project(lon_values, lat_values):
        longitude = np.deg2rad(np.asarray(lon_values, dtype=float))
        latitude = np.deg2rad(np.clip(np.asarray(lat_values, dtype=float), -89.5, 89.5))
        radius = scale / np.tan(np.pi / 4.0 + latitude / 2.0) ** n_coefficient
        angle = n_coefficient * (longitude - central_longitude)
        return radius * np.sin(angle), origin_radius - radius * np.cos(angle)

    # Operational map frames use a rectangular projected window rather than
    # the bounding box of a lon/lat rectangle. Anchor the horizontal edges at
    # the projection origin and the vertical edges on the requested latitude
    # span; this keeps the map filled in all four corners and centers Greenland
    # over North America without exposing South America.
    horizontal_x, _ = lcc_project(
        np.asarray([lon_min, lon_max]),
        np.full(2, np.rad2deg(latitude_origin)),
    )
    _, bottom_y = lcc_project(
        np.asarray([np.rad2deg(central_longitude)]),
        np.asarray([lat_min]),
    )
    top_edge_lons = np.linspace(lon_min, lon_max, 240)
    _, top_edge_y = lcc_project(top_edge_lons, np.full(top_edge_lons.shape, lat_max))
    x_min, x_max = float(np.nanmin(horizontal_x)), float(np.nanmax(horizontal_x))
    y_min, y_max = float(np.nanmin(bottom_y)), float(np.nanmax(top_edge_y))
    projected_x_shift = (x_max - x_min) * PROJECTED_X_SHIFT_FRACTION
    x_min -= projected_x_shift
    x_max -= projected_x_shift
    x_pad = max(0.01, (x_max - x_min) * 0.006)
    y_pad = max(0.01, (y_max - y_min) * 0.006)

    # Resample the full global field onto a regular projected canvas. Using
    # only the source cells inside the lon/lat box leaves the corners of a
    # projected map empty; inverse projection keeps those corners data-filled.
    canvas_columns = 520
    canvas_rows = max(260, int(round(canvas_columns * (y_max - y_min) / (x_max - x_min))))
    canvas_x = np.linspace(x_min, x_max, canvas_columns)
    canvas_y = np.linspace(y_min, y_max, canvas_rows)
    canvas_x_mesh, canvas_y_mesh = np.meshgrid(canvas_x, canvas_y)

    def lcc_inverse(x_values, y_values):
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        rho = np.hypot(x_array, origin_radius - y_array)
        rho = np.where(rho == 0.0, np.finfo(float).eps, rho)
        angle = np.arctan2(x_array, origin_radius - y_array)
        latitude = 2.0 * np.arctan((scale / rho) ** (1.0 / n_coefficient)) - np.pi / 2.0
        longitude = central_longitude + angle / n_coefficient
        return np.rad2deg(longitude), np.rad2deg(latitude)

    def sample_source(field, longitude_values, latitude_values):
        # CFSv2 pressure-level files are regular 1-degree grids, while FLXF
        # files use Gaussian latitudes.  Bracket coordinates directly so both
        # grids can be resampled without inventing a regular-latitude grid.
        wrapped_longitudes = np.mod(longitude_values - source_lons[0], 360.0) + source_lons[0]
        longitude_right = np.searchsorted(source_lons, wrapped_longitudes, side="right")
        longitude_wrap = longitude_right >= source_lons.size
        lon_left = np.where(longitude_wrap, source_lons.size - 1, np.maximum(longitude_right - 1, 0))
        lon_right = np.where(longitude_wrap, 0, np.minimum(longitude_right, source_lons.size - 1))
        left_lon_value = source_lons[lon_left]
        right_lon_value = np.where(longitude_wrap, source_lons[0] + 360.0, source_lons[lon_right])
        lon_weight = np.divide(
            wrapped_longitudes - left_lon_value,
            right_lon_value - left_lon_value,
            out=np.zeros_like(wrapped_longitudes, dtype=float),
            where=(right_lon_value - left_lon_value) != 0.0,
        )

        clipped_latitudes = np.clip(latitude_values, source_lats[0], source_lats[-1])
        latitude_right = np.searchsorted(source_lats, clipped_latitudes, side="right")
        latitude_right = np.clip(latitude_right, 1, source_lats.size - 1)
        lat_left = latitude_right - 1
        lat_right = latitude_right
        left_lat_value = source_lats[lat_left]
        right_lat_value = source_lats[lat_right]
        lat_weight = np.divide(
            clipped_latitudes - left_lat_value,
            right_lat_value - left_lat_value,
            out=np.zeros_like(clipped_latitudes, dtype=float),
            where=(right_lat_value - left_lat_value) != 0.0,
        )

        values = (
            field[lat_left, lon_left] * (1.0 - lon_weight) * (1.0 - lat_weight)
            + field[lat_left, lon_right] * lon_weight * (1.0 - lat_weight)
            + field[lat_right, lon_left] * (1.0 - lon_weight) * lat_weight
            + field[lat_right, lon_right] * lon_weight * lat_weight
        )
        return values

    canvas_lons, canvas_lats = lcc_inverse(canvas_x_mesh, canvas_y_mesh)
    data = sample_source(source_data, canvas_lons, canvas_lats)
    height_data = (
        sample_source(source_height, canvas_lons, canvas_lats)
        if source_height is not None
        else None
    )

    # Match a 1080x1080 social-media footprint. Size the map box from the
    # projected bounds so the LCC geometry remains undistorted at square size.
    figure = plt.figure(figsize=(9.0, 9.0), facecolor="#f7f9fb")
    map_left = 0.035
    map_width = 0.93
    map_height = map_width * (y_max - y_min) / (x_max - x_min)
    map_top = 0.88
    map_bottom = map_top - map_height
    axes = figure.add_axes([map_left, map_bottom, map_width, map_height])
    axes.set_facecolor("#ffffff" if product_spec["name"] == PRODUCT_SWE_ANOMALY else "#edf3f5")

    # Light graticules make the projection legible without competing with the
    # height field. The map remains intentionally free of axis tick clutter.
    for longitude_line in range(math.ceil(lon_min / 20.0) * 20, math.floor(lon_max / 20.0) * 20 + 1, 20):
        line_lats = np.linspace(lat_min, lat_max, 240)
        line_x, line_y = lcc_project(np.full(line_lats.shape, longitude_line), line_lats)
        axes.plot(line_x, line_y, color="#70808a", linewidth=0.35, alpha=0.34, linestyle=(0, (1, 3)), zorder=1)
    for latitude_line in range(math.ceil(lat_min / 10.0) * 10, math.floor(lat_max / 10.0) * 10 + 1, 10):
        line_lons = np.linspace(lon_min, lon_max, 300)
        line_x, line_y = lcc_project(line_lons, np.full(line_lons.shape, latitude_line))
        axes.plot(line_x, line_y, color="#70808a", linewidth=0.35, alpha=0.34, linestyle=(0, (1, 3)), zorder=1)

    masked = np.ma.masked_invalid(data)
    if product_spec["name"] == PRODUCT_SWE_ANOMALY:
        land_mask = land_mask_from_borders(border_paths, canvas_lons, canvas_lats)
        if land_mask is not None and np.any(land_mask):
            # SWE is not applicable over open water. Mask it before contouring
            # so ocean cells reveal the intentional white map background rather
            # than a near-zero negative anomaly swatch.
            masked = np.ma.masked_where(~land_mask, masked)
    if anomaly:
        anomaly_min, anomaly_max, colorbar_ticks, palette = anomaly_style(
            product_spec,
            seasonal=seasonal,
        )
        # Use one color interval for every labelled tick-to-tick range. This
        # keeps the labels and tick marks on the actual color transitions
        # instead of drifting into the middle of adjacent swatches.
        bounds = np.asarray(colorbar_ticks, dtype=float)
        if bounds.size != len(palette) + 1:
            raise CFSv2Error("anomaly palette must have one fewer color than labelled bounds")
        cmap = mcolors.ListedColormap(palette)
        norm = mcolors.BoundaryNorm(bounds, cmap.N, clip=True)
        image = axes.contourf(
            canvas_x,
            canvas_y,
            np.ma.clip(masked, anomaly_min, anomaly_max),
            levels=bounds,
            cmap=cmap,
            norm=norm,
            antialiased=True,
        )
    else:
        finite = np.asarray(list(_finite_values(grid)), dtype=float)
        if finite.size == 0:
            raise CFSv2Error("decoded grid contains no finite values")
        vmin = float(np.nanpercentile(finite, 2))
        vmax = float(np.nanpercentile(finite, 98))
        if vmin == vmax:
            vmin -= 1.0
            vmax += 1.0
        image = axes.contourf(
            canvas_x,
            canvas_y,
            masked,
            levels=np.linspace(vmin, vmax, 17),
            cmap="viridis",
            norm=mcolors.Normalize(vmin=vmin, vmax=vmax),
            extend="both",
            antialiased=True,
        )
        colorbar_ticks = np.linspace(vmin, vmax, 7)

    # Filled anomalies show the signal; actual 500-mb heights provide the
    # synoptic structure and make the map readable like an operational
    # seasonal product. Heights are labelled in decametres (dam).
    height_masked = np.ma.masked_invalid(height_data) if height_data is not None else None
    finite_heights = np.ma.compressed(height_masked) if height_masked is not None else np.asarray([])
    if product_spec["height_contours"] and finite_heights.size > 1 and float(np.nanmax(finite_heights)) > float(np.nanmin(finite_heights)):
        contour_step = 6.0
        height_min = math.floor(float(np.nanpercentile(finite_heights, 2)) / contour_step) * contour_step
        height_max = math.ceil(float(np.nanpercentile(finite_heights, 98)) / contour_step) * contour_step
        height_levels = np.arange(height_min, height_max + contour_step * 0.5, contour_step)
        if height_levels.size > 1:
            minor_levels = np.arange(height_min, height_max + 3.0 * 0.5, 3.0)
            axes.contour(
                canvas_x,
                canvas_y,
                height_masked,
                levels=minor_levels,
                colors="#34444d",
                linewidths=0.24,
                alpha=0.38,
                linestyles="dotted",
                zorder=3,
            )
            height_lines = axes.contour(
                canvas_x,
                canvas_y,
                height_masked,
                levels=height_levels,
                colors="#1c2931",
                linewidths=0.62,
                alpha=0.84,
                zorder=4,
            )
            label_levels = height_levels[::2] if height_levels.size > 14 else height_levels
            axes.clabel(
                height_lines,
                levels=label_levels,
                inline=True,
                inline_spacing=3,
                fmt=lambda value: f"{value:.0f}",
                fontsize=7.2,
                colors="#1c2931",
            )

    def projected_ring_segments(ring):
        segments = []
        current = []
        previous_lon = None
        border_lat_min = 14.0
        for point in ring:
            if len(point) < 2:
                continue
            longitude, latitude = float(point[0]), float(point[1])
            if not math.isfinite(longitude) or not math.isfinite(latitude) or abs(latitude) >= 89.5:
                if len(current) > 1:
                    segments.append(current)
                current = []
                previous_lon = None
                continue
            if not (lon_min <= longitude <= lon_max) or latitude < border_lat_min:
                if len(current) > 1:
                    segments.append(current)
                current = []
                previous_lon = None
                continue
            if previous_lon is not None and abs(longitude - previous_lon) > 180.0:
                if len(current) > 1:
                    segments.append(current)
                current = []
            point_x, point_y = lcc_project(np.array([longitude]), np.array([latitude]))
            current.append((float(point_x[0]), float(point_y[0])))
            previous_lon = longitude
        if len(current) > 1:
            segments.append(current)
        return segments

    for border_path in border_paths:
        try:
            payload = json.loads(border_path.read_text(encoding="utf-8"))
            for ring in geojson_features(payload):
                for segment in projected_ring_segments(ring):
                    axes.plot(
                        [point[0] for point in segment],
                        [point[1] for point in segment],
                        color="#17232c",
                        linewidth=0.66,
                        alpha=0.92,
                        solid_capstyle="round",
                        zorder=5,
                    )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: could not draw borders from {border_path}: {exc}", file=sys.stderr)

    axes.set_xlim(x_min - x_pad, x_max + x_pad)
    axes.set_ylim(y_min - y_pad, y_max + y_pad)
    axes.set_aspect("equal", adjustable="box")
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(True)
        spine.set_color("#20313a")
        spine.set_linewidth(0.75)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    target_date = dt.datetime.strptime(target, "%Y%m")
    display_period = period_label or target_date.strftime("%B %Y")
    mean_label = ensemble_label or f"{len(members)}-member mean"
    title = product_spec["title"] if anomaly else product_spec["absolute_title"]
    title_text = figure.text(
        0.035,
        0.965,
        title,
        ha="left",
        va="center",
        fontsize=15.5,
        fontweight="bold",
        color="#172735",
    )
    valid_text = figure.text(
        0.965,
        0.965,
        f"Valid: {display_period}",
        ha="right",
        va="center",
        fontsize=13.0,
        fontweight="bold",
        color="#172735",
    )
    # A long calendar-month label such as "Valid: December 2026" must not
    # overlap the title and visually erase the first characters of "Valid".
    # Fit only the title when the two header artists do not leave a readable
    # gap, preserving the prominent right-aligned valid-period label.
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    title_box = title_text.get_window_extent(renderer=renderer)
    valid_box = valid_text.get_window_extent(renderer=renderer)
    available_title_width = max(1.0, valid_box.x0 - title_box.x0 - 16.0)
    if title_box.width > available_title_width:
        title_text.set_fontsize(max(12.5, 15.5 * available_title_width / title_box.width))
    init_text = initialization_label or f"Init {init_date:%d %b %Y %HZ}"
    figure.text(
        0.035,
        0.925,
        f"{init_text}  •  Lead {lead}  •  {mean_label}",
        ha="left",
        va="center",
        fontsize=10.5,
        color="#42515d",
    )
    source_label = product_spec.get("source_label", "NOAA CFSv2 / NOMADS")
    configured_header_detail = product_spec.get("header_detail", "")
    if configured_header_detail:
        header_detail = configured_header_detail.format(
            source_label=source_label,
            baseline_label=(
                "Absolute field smoke output" if not anomaly else baseline_label
            ),
        )
    elif product_spec["height_contours"]:
        header_detail = (
            f"{source_label}  •  {baseline_label}  •  Height contours in dam"
            if anomaly
            else f"{source_label}  •  Absolute field smoke output  •  Height contours in dam"
        )
    else:
        header_detail = (
            f"{source_label}  •  {baseline_label}  •  Precipitation accumulation (in)  •  CONUS domain"
        )
    if product_spec["name"] == PRODUCT_SWE_ANOMALY:
        header_detail = (
            f"{source_label}  •  {baseline_label}  •  Snow-water equivalent (in)  •  CONUS domain"
        )
    figure.text(
        0.035,
        0.899,
        header_detail,
        ha="left",
        va="center",
        fontsize=8.2,
        color="#5d6b75",
    )
    colorbar_height = 0.032
    colorbar_gap = 0.025
    colorbar_bottom = max(0.055, map_bottom - colorbar_gap - colorbar_height)
    colorbar_axes = figure.add_axes([map_left, colorbar_bottom, map_width, colorbar_height])
    colorbar_options = {"ticks": colorbar_ticks}
    if anomaly:
        colorbar_options["boundaries"] = bounds
    colorbar = figure.colorbar(
        image,
        cax=colorbar_axes,
        orientation="horizontal",
        extend="neither",
        spacing="uniform",
        drawedges=product_spec["name"] in {PRODUCT_PRECIPITATION_ANOMALY, PRODUCT_SWE_ANOMALY},
        **colorbar_options,
    )
    colorbar.set_ticks(colorbar_ticks)
    if anomaly:
        automatic_tick_decimals = (
            1 if any(not float(tick).is_integer() for tick in colorbar_ticks) else 0
        )
        tick_decimals = int(product_spec.get("anomaly_tick_decimals", automatic_tick_decimals))
        tick_format = product_spec.get("anomaly_tick_format", "signed")

        def format_anomaly_tick(value: float) -> str:
            numeric = float(value)
            if abs(numeric) < 0.5 * (10 ** -tick_decimals):
                numeric = 0.0
            if tick_format == "plain":
                return f"{numeric:.{tick_decimals}f}"
            if tick_decimals:
                return f"{numeric:+.{tick_decimals}f}" if numeric else f"{numeric:.{tick_decimals}f}"
            return f"+{int(round(numeric))}" if numeric > 0 else str(int(round(numeric)))

        colorbar.set_ticklabels(
            [format_anomaly_tick(tick) for tick in colorbar_ticks]
        )
    colorbar.ax.tick_params(
        axis="x",
        which="major",
        labelsize=10.0,
        length=5.0,
        width=0.85,
        pad=1.8,
        colors="#263640",
        direction="out",
    )
    colorbar.outline.set_edgecolor("#52636c")
    colorbar.outline.set_linewidth(0.65)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=120, facecolor=figure.get_facecolor())
    plt.close(figure)


def relative_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def manifest_product_key(run: dict) -> str:
    """Return a stable product key, including for manifests predating ``product``."""

    product = run.get("product")
    if product:
        return str(product)
    return {
        "z500_anomaly": PRODUCT_HEIGHT_ANOMALY,
        "z500": PRODUCT_HEIGHT_ABSOLUTE,
        "t2m_anomaly": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "mslp_anomaly": PRODUCT_MSLP_ANOMALY,
        "precipitation_anomaly": PRODUCT_PRECIPITATION_ANOMALY,
        "snow_water_equivalent_anomaly": PRODUCT_SWE_ANOMALY,
    }.get(str(run.get("field", "")), PRODUCT_HEIGHT_ANOMALY)


def write_manifest(
    path: Path,
    repo_root: Path,
    run_entry: dict,
    previous_manifest: Path | None = None,
    retain_runs: int = 4,
) -> None:
    if retain_runs < 1:
        raise CFSv2Error("manifest retention must keep at least one run")
    payload = {
        "schema_version": 1,
        "kind": "cfsv2_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "retention": {
            "scope": "per_product",
            "max_runs": retain_runs,
            "history_runs": max(0, retain_runs - 1),
            "max_runs_per_product": retain_runs,
            "history_runs_per_product": max(0, retain_runs - 1),
        },
        "runs": [],
    }
    existing_paths = []
    if previous_manifest and previous_manifest.resolve() != path.resolve():
        existing_paths.append(previous_manifest)
    # A manifest already assembled during this process is newer than the
    # published fallback. Load it last so sequential product renders retain
    # earlier products from the same workflow invocation.
    existing_paths.append(path)
    for existing_path in existing_paths:
        if not existing_path.exists():
            continue
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                payload.update({key: existing[key] for key in ("schema_version", "kind", "source", "source_url") if key in existing})
                payload["runs"].extend(existing["runs"])
        except (OSError, ValueError) as exc:
            raise CFSv2Error(f"could not read existing CFSv2 manifest {existing_path}: {exc}") from exc
    payload["generated_utc"] = iso_utc(dt.datetime.now(dt.timezone.utc))
    unique_runs = {}
    for run in payload["runs"]:
        if isinstance(run, dict) and run.get("id"):
            unique_runs[run["id"]] = run
    unique_runs[run_entry["id"]] = run_entry
    sorted_runs = list(unique_runs.values())
    sorted_runs.sort(
        key=lambda item: (
            str(item.get("init_utc", "")),
            str(item.get("generated_utc", "")),
            str(item.get("id", "")),
        ),
        reverse=True,
    )
    retained_counts: dict[str, int] = {}
    payload["runs"] = []
    for retained_run in sorted_runs:
        product_key = manifest_product_key(retained_run)
        if retained_counts.get(product_key, 0) >= retain_runs:
            continue
        payload["runs"].append(retained_run)
        retained_counts[product_key] = retained_counts.get(product_key, 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product",
        choices=tuple(PRODUCT_SPECS),
        default=PRODUCT_HEIGHT_ANOMALY,
        help="product to decode and render",
    )
    parser.add_argument("--init", default="latest", help="CFSv2 cycle as YYYYMMDDHH, or latest")
    parser.add_argument("--lead-months", default="1,2,3", help="comma-separated target leads, usually 1,2,3")
    parser.add_argument("--seasonal-window", default="", help="optional comma-separated leads for an additional seasonal mean, e.g. 1,2,3")
    parser.add_argument("--members", default="1,2,3,4", help="comma-separated monthly_grib member directories")
    parser.add_argument("--rolling-days", type=int, default=0, help="use a lagged initial-condition blend covering this many days; 10 gives CPC-style 40 cycles")
    parser.add_argument("--rolling-member", type=int, default=ROLLING_MEMBER_DEFAULT, help="monthly_grib member used for each rolling six-hourly cycle (default: 1)")
    parser.add_argument("--rolling-state-dir", default=".cache/cfsv2/rolling", help="retained decoded grids used after NOMADS rotates old cycles")
    parser.add_argument("--allow-partial-rolling", action="store_true", help="render with available rolling cycles when the requested window is incomplete")
    parser.add_argument("--cache-dir", default=".cache/cfsv2", help="raw GRIB2/decoder/border cache")
    parser.add_argument("--output-dir", default="public/seasonal/cfsv2", help="rendered image directory")
    parser.add_argument("--manifest", default="public/seasonal/cfsv2_manifest.json", help="seasonal manifest path")
    parser.add_argument("--previous-manifest", type=Path, help="previous published manifest used to retain older runs")
    parser.add_argument(
        "--retain-runs",
        type=int,
        default=4,
        help="number of current and historical runs to retain per product in the manifest",
    )
    parser.add_argument("--baseline-file", type=Path, help="one CFSv2/reforecast baseline CSV or GRIB2 grid")
    parser.add_argument("--baseline-dir", type=Path, help="directory containing a baseline grid for each YYYYMM target")
    parser.add_argument("--ncei-calibration", action="store_true", help="fetch the matching official NCEI CFS reforecast calibration baseline (1982-2010)")
    parser.add_argument("--baseline-label", default="", help="human-readable baseline source and period for metadata")
    parser.add_argument("--baseline-years", default="", help="optional baseline years for manifest provenance")
    parser.add_argument("--common-reference-dir", type=Path, help="cached CanSIPS 1991-2020 reference grids for the comparison view")
    parser.add_argument("--common-reference-url", default="", help="base URL for published CanSIPS 1991-2020 reference grids")
    parser.add_argument("--wgrib2", default="", help="path to wgrib2.exe; CFSV2_WGRIB2 is also honored")
    parser.add_argument("--request-delay", type=float, default=2.0, help="seconds between NOAA downloads")
    parser.add_argument("--border-geojson", action="append", type=Path, help="local GeoJSON border file; repeatable")
    parser.add_argument("--no-borders", action="store_true", help="skip optional border downloads/drawing")
    parser.add_argument("--decode-only", action="store_true", help="download/decode/average but do not render")
    parser.add_argument("--absolute", action="store_true", help="render absolute heights; never label them as anomalies")
    parser.add_argument("--force-decode", action="store_true", help="rerun wgrib2 even when a decoded CSV is cached")
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    product_name, product, absolute = selected_product(args)
    init = discover_latest_init() if args.init == "latest" else parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 1, 9)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 1, 9) if args.seasonal_window else []
    if seasonal_leads:
        expected_window = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected_window:
            raise CFSv2Error("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    members = parse_int_list(args.members, "members", 1, 4)
    if args.rolling_days < 0 or args.rolling_days > 30:
        raise CFSv2Error("--rolling-days must be between 0 and 30")
    if not 1 <= args.rolling_member <= 4:
        raise CFSv2Error("--rolling-member must be between 1 and 4")
    rolling_inits = rolling_cycle_inits(init, args.rolling_days * 4) if args.rolling_days else []
    configured_baselines = sum(
        bool(value) for value in (args.baseline_file, args.baseline_dir, args.ncei_calibration)
    )
    if configured_baselines > 1:
        raise CFSv2Error("use only one of --baseline-file, --baseline-dir, and --ncei-calibration")
    if args.ncei_calibration and args.baseline_years and args.baseline_years != NCEI_CALIBRATION_YEARS:
        raise CFSv2Error(
            f"--ncei-calibration uses the published {NCEI_CALIBRATION_YEARS} baseline"
        )
    if not absolute and not args.decode_only and configured_baselines == 0:
        raise CFSv2Error(
            "production anomaly rendering needs a CFSv2/reforecast baseline; "
            "provide --baseline-file/--baseline-dir, use --ncei-calibration, or use --absolute for smoke testing"
        )
    wgrib2 = find_wgrib2(args.wgrib2)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    state_dir = resolve_repo_path(args.rolling_state_dir, repo_root)
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    common_reference_dir = resolve_repo_path(
        args.common_reference_dir or ".cache/common-reference",
        repo_root,
    ) if (args.common_reference_dir or args.common_reference_url) else None
    cache_dir.mkdir(parents=True, exist_ok=True)
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    run_id = (
        f"cfsv2-{init}"
        if product_name == PRODUCT_HEIGHT_ANOMALY
        else f"cfsv2-{init}-{product_name}"
    )
    rolling_mode = bool(rolling_inits)
    ensemble_expected = len(rolling_inits) if rolling_mode else len(members)
    run_entry = {
        "id": run_id,
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "model": "CFSv2",
        "product": product_name,
        "source_kind": product["source_kind"].upper(),
        "init_utc": iso_utc(init_date),
        "decoder": {"tool": "wgrib2", "executable": wgrib2},
        "statistic": "ensemble_mean",
        "members": [args.rolling_member] if rolling_mode else members,
        "ensemble_members": ensemble_expected,
        "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
        "aggregation": (
            (f"{args.rolling_days}-day rolling initial-condition mean; " if rolling_mode else "")
            + (
                f"{len(seasonal_leads)}-month {product['seasonal_aggregation']}"
                if seasonal_leads
                else product.get("monthly_aggregation", "monthly forecast average")
            )
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "border_sources": (
            []
            if args.no_borders
            else (
                [{"file": relative_path(resolve_repo_path(path, repo_root), repo_root)} for path in args.border_geojson]
                if args.border_geojson
                else [{"name": name, "url": url} for name, url in DEFAULT_BORDER_URLS]
            )
        ),
        "baseline": None,
        "status": "planned",
        "targets": [],
    }
    common_reference_enabled = bool(common_reference_dir or args.common_reference_url) and product_name == PRODUCT_HEIGHT_ANOMALY
    if common_reference_enabled:
        run_entry["comparison_reference"] = {
            "id": "common_1991_2020",
            "label": COMMON_REFERENCE_LABEL,
            "years": COMMON_REFERENCE_YEARS,
            "source": "CanSIPS v3 hindcast climatology",
            "url_root": args.common_reference_url or None,
        }
    if rolling_mode:
        run_entry["rolling_window"] = {
            "days": args.rolling_days,
            "expected_cycles": len(rolling_inits),
            "cycle_interval_hours": 6,
            "member": args.rolling_member,
            "start_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[0], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "end_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[-1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "source": "lagged CFSv2 initial conditions",
        }
    if product.get("conversion"):
        run_entry["conversion"] = product["conversion"]
    if absolute:
        run_entry["baseline"] = {"status": "not_applicable", "reason": "absolute smoke output"}
    elif args.decode_only:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
            "status": "not_applied_decode_only",
        }
    elif args.ncei_calibration:
        run_entry["baseline"] = {
            "source": product["baseline_label"],
            "years": NCEI_CALIBRATION_YEARS,
            "url_root": product["baseline_root"],
            "required": True,
        }
    else:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
        }
    if rolling_mode and not absolute:
        run_entry["baseline"]["rolling_policy"] = "anchor_initialization"

    last_request = 0.0
    failures = 0
    forecast_grids: dict[int, Grid] = {}
    baseline_grids: dict[int, Grid] = {}
    target_entries_by_lead: dict[int, dict] = {}
    for lead in leads:
        target = target_month(init, lead)
        valid_start, valid_end = target_period(target)
        target_entry = {
            "id": f"cfsv2-{target}-{product['id_token']}-lead{lead:02d}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": lead,
            "target_month": target,
            "aggregation": product.get("monthly_aggregation", "monthly forecast average"),
            "field": product["field"],
            "units": product["units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "source_files": [],
            "status": "planned",
        }
        try:
            ensemble, source_files, ensemble_count, ensemble_expected_for_target, ensemble_label, last_request = decode_target_ensemble(
                args,
                init,
                target,
                members,
                rolling_inits,
                cache_dir,
                state_dir,
                wgrib2,
                repo_root,
                last_request,
                product,
            )
            target_entry["source_files"] = source_files
            target_entry["ensemble_members"] = ensemble_count
            target_entry["ensemble_expected_members"] = ensemble_expected_for_target
            target_entry["ensemble_complete"] = ensemble_count == ensemble_expected_for_target
            target_entry["ensemble_label"] = ensemble_label
            forecast_grids[lead] = ensemble
            target_entry["status"] = "partial" if ensemble_count < ensemble_expected_for_target else "decoded"
            if args.decode_only:
                run_entry["targets"].append(target_entry)
                target_entries_by_lead[lead] = target_entry
                print(f"decoded CFSv2 {target} lead {lead} from {ensemble_count}/{ensemble_expected_for_target} member(s)")
                continue

            baseline_label = "absolute field smoke output"
            anomaly_grid = ensemble
            if not absolute:
                baseline_url = None
                baseline_downloaded = False
                if args.ncei_calibration:
                    baseline_url = ncei_calibration_url(init, lead, product["source_kind"])
                    baseline_path = cached_calibration_path(cache_dir, init, lead, product["source_kind"])
                    baseline_downloaded, last_request = download_file(
                        baseline_url,
                        baseline_path,
                        max(0.0, args.request_delay),
                        last_request,
                    )
                    baseline_label = configured_baseline_label(args)
                else:
                    baseline_path, baseline_label = baseline_for_target(args, target, repo_root)
                baseline_grid = load_baseline(baseline_path, wgrib2, product, target)
                baseline_grids[lead] = baseline_grid
                anomaly_grid = subtract_grids(ensemble, baseline_grid)
                target_entry["baseline"] = {
                    "file": relative_path(baseline_path, repo_root),
                    "label": baseline_label,
                    "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                }
                if rolling_mode:
                    target_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                    target_entry["baseline"]["anchor_init"] = init
                if baseline_url:
                    target_entry["baseline"]["url"] = baseline_url
                    target_entry["baseline"]["downloaded"] = baseline_downloaded

            output_path = output_dir / init / f"cfsv2_{product['file_token']}_{target}.jpg"
            render_map(
                anomaly_grid,
                init,
                target,
                lead,
                members,
                output_path,
                anomaly=not absolute,
                baseline_label=baseline_label,
                border_paths=border_paths,
                ensemble_label=ensemble_label,
                height_grid=ensemble if product["height_contours"] else None,
                product_spec=product,
            )
            target_entry["image"] = relative_path(output_path, repo_root)
            target_entry["status"] = "partial" if not target_entry["ensemble_complete"] else "rendered"
            print(f"rendered CFSv2 {target} lead {lead}: {output_path}")
            if common_reference_enabled:
                try:
                    common_reference, reference_path, reference_url, reference_downloaded, last_request = load_common_reference(
                        target,
                        common_reference_dir,
                        args.common_reference_url,
                        max(0.0, args.request_delay),
                        last_request,
                    )
                    common_reference = regrid_nearest(
                        common_reference,
                        ensemble.lons,
                        ensemble.lats,
                        f"common reference {target}",
                    )
                    common_grid = subtract_grids(ensemble, common_reference)
                    common_output = output_dir / init / f"cfsv2_{product['file_token']}_{target}_common-1991-2020.jpg"
                    render_map(
                        common_grid,
                        init,
                        target,
                        lead,
                        members,
                        common_output,
                        anomaly=True,
                        baseline_label=COMMON_REFERENCE_LABEL,
                        border_paths=border_paths,
                        ensemble_label=ensemble_label,
                        height_grid=ensemble,
                        product_spec=product,
                    )
                    target_entry["comparison"] = {
                        "common_1991_2020": {
                            "image": relative_path(common_output, repo_root),
                            "status": "rendered",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                                "file": relative_path(reference_path, repo_root),
                                "url": reference_url or None,
                                "downloaded": reference_downloaded,
                            },
                        }
                    }
                except Exception as exc:
                    target_entry["comparison"] = {
                        "common_1991_2020": {
                            "status": "unavailable",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                            },
                            "error": str(exc),
                        }
                    }
                    print(f"CFSv2 common comparison target {target} unavailable: {exc}", file=sys.stderr)
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CFSv2 target {target} lead {lead} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(target_entry)
        target_entries_by_lead[lead] = target_entry

    if seasonal_leads and not args.decode_only:
        first_lead = seasonal_leads[0]
        last_lead = seasonal_leads[-1]
        first_target = target_month(init, first_lead)
        last_target = target_month(init, last_lead)
        seasonal_entry = {
            "id": f"cfsv2-{first_target}-{last_target}-{product['id_token']}-seasonal",
            "valid_start_utc": target_period(first_target)[0],
            "valid_end_utc": target_period(last_target)[1],
            "lead_month": f"{first_lead}-{last_lead}",
            "target_month": f"{first_target}-{last_target}",
            "aggregation": (
                f"{len(seasonal_leads)}-month {product['seasonal_aggregation']}"
            ),
            "field": product["field"],
            "units": product["seasonal_units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "monthly_leads": seasonal_leads,
            "source_files": [],
            "status": "planned",
        }
        try:
            missing_forecasts = [lead for lead in seasonal_leads if lead not in forecast_grids]
            if missing_forecasts:
                raise CFSv2Error(f"seasonal window is missing decoded lead(s): {missing_forecasts}")
            seasonal_forecast = (
                sum_grids([forecast_grids[lead] for lead in seasonal_leads])
                if product["seasonal_reducer"] == "sum"
                else mean_grids([forecast_grids[lead] for lead in seasonal_leads])
            )
            seasonal_grid = seasonal_forecast
            baseline_label = "absolute field smoke output"
            if not absolute:
                missing_baselines = [lead for lead in seasonal_leads if lead not in baseline_grids]
                if missing_baselines:
                    raise CFSv2Error(f"seasonal window is missing baseline lead(s): {missing_baselines}")
                seasonal_baseline = (
                    sum_grids([baseline_grids[lead] for lead in seasonal_leads])
                    if product["seasonal_reducer"] == "sum"
                    else mean_grids([baseline_grids[lead] for lead in seasonal_leads])
                )
                seasonal_grid = subtract_grids(seasonal_forecast, seasonal_baseline)
                baseline_label = configured_baseline_label(args)
                seasonal_entry["baseline"] = {
                    "files": [
                        target_entries_by_lead[lead]["baseline"]["file"]
                        for lead in seasonal_leads
                        if "baseline" in target_entries_by_lead.get(lead, {})
                    ],
                    "label": baseline_label,
                    "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                }
                if rolling_mode:
                    seasonal_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                    seasonal_entry["baseline"]["anchor_init"] = init
                baseline_urls = [
                    target_entries_by_lead[lead]["baseline"].get("url")
                    for lead in seasonal_leads
                    if target_entries_by_lead[lead].get("baseline", {}).get("url")
                ]
                if baseline_urls:
                    seasonal_entry["baseline"]["urls"] = baseline_urls
            else:
                seasonal_entry["baseline"] = {"status": "not_applicable", "reason": "absolute smoke output"}
            seasonal_entry["source_files"] = [
                source_file
                for lead in seasonal_leads
                for source_file in target_entries_by_lead[lead].get("source_files", [])
            ]
            seasonal_entry["ensemble_complete"] = all(
                target_entries_by_lead[lead].get("ensemble_complete", False)
                for lead in seasonal_leads
            )
            seasonal_entry["ensemble_members"] = min(
                target_entries_by_lead[lead].get("ensemble_members", 0)
                for lead in seasonal_leads
            )
            start_date = dt.datetime.strptime(first_target, "%Y%m")
            end_date = dt.datetime.strptime(last_target, "%Y%m")
            period_label = seasonal_period_label(first_target, last_target)
            output_path = output_dir / init / f"cfsv2_{product['file_token']}_{first_target}-{last_target}.jpg"
            render_map(
                seasonal_grid,
                init,
                first_target,
                f"{first_lead}\u2013{last_lead}",
                members,
                output_path,
                anomaly=not absolute,
                baseline_label=baseline_label,
                border_paths=border_paths,
                period_label=period_label,
                seasonal=True,
                ensemble_label=(
                    f"{seasonal_entry['ensemble_members']}/{ensemble_expected}-cycle rolling mean"
                    if rolling_mode
                    else f"{len(members)}-member mean"
                ),
                height_grid=seasonal_forecast if product["height_contours"] else None,
                product_spec=product,
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            seasonal_entry["status"] = "rendered" if seasonal_entry["ensemble_complete"] else "partial"
            print(f"rendered CFSv2 seasonal product {first_target}-{last_target}: {output_path}")
            if common_reference_enabled:
                try:
                    common_references = []
                    reference_files = []
                    reference_urls = []
                    for lead in seasonal_leads:
                        target = target_month(init, lead)
                        reference, reference_path, reference_url, reference_downloaded, last_request = load_common_reference(
                            target,
                            common_reference_dir,
                            args.common_reference_url,
                            max(0.0, args.request_delay),
                            last_request,
                        )
                        common_references.append(regrid_nearest(
                            reference,
                            seasonal_forecast.lons,
                            seasonal_forecast.lats,
                            f"common reference {target}",
                        ))
                        reference_files.append(relative_path(reference_path, repo_root))
                        if reference_url:
                            reference_urls.append(reference_url)
                    common_baseline = (
                        sum_grids(common_references)
                        if product["seasonal_reducer"] == "sum"
                        else mean_grids(common_references)
                    )
                    common_grid = subtract_grids(seasonal_forecast, common_baseline)
                    common_output = output_dir / init / f"cfsv2_{product['file_token']}_{first_target}-{last_target}_common-1991-2020.jpg"
                    render_map(
                        common_grid,
                        init,
                        first_target,
                        f"{first_lead}\u2013{last_lead}",
                        members,
                        common_output,
                        anomaly=True,
                        baseline_label=COMMON_REFERENCE_LABEL,
                        border_paths=border_paths,
                        period_label=period_label,
                        seasonal=True,
                        ensemble_label=(
                            f"{seasonal_entry['ensemble_members']}/{ensemble_expected}-cycle rolling mean"
                            if rolling_mode
                            else f"{len(members)}-member mean"
                        ),
                        height_grid=seasonal_forecast,
                        product_spec=product,
                    )
                    seasonal_entry["comparison"] = {
                        "common_1991_2020": {
                            "image": relative_path(common_output, repo_root),
                            "status": "rendered",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                                "files": reference_files,
                                "urls": reference_urls,
                            },
                        }
                    }
                except Exception as exc:
                    seasonal_entry["comparison"] = {
                        "common_1991_2020": {
                            "status": "unavailable",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                            },
                            "error": str(exc),
                        }
                    }
                    print(
                        f"CFSv2 common comparison seasonal window {first_target}-{last_target} unavailable: {exc}",
                        file=sys.stderr,
                    )
        except Exception as exc:
            failures += 1
            seasonal_entry["status"] = "failed"
            seasonal_entry["error"] = str(exc)
            print(f"CFSv2 seasonal window {first_target}-{last_target} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(seasonal_entry)

    statuses = [target["status"] for target in run_entry["targets"]]
    partial_targets = any(status == "partial" for status in statuses)
    if failures or partial_targets:
        run_entry["status"] = "partial" if any(status != "failed" for status in statuses) else "failed"
    elif args.decode_only:
        run_entry["status"] = "decoded"
    else:
        run_entry["status"] = "rendered"
    run_entry["output_dir"] = relative_path(output_dir, repo_root)
    previous_manifest = resolve_repo_path(args.previous_manifest, repo_root) if args.previous_manifest else None
    write_manifest(manifest_path, repo_root, run_entry, previous_manifest, args.retain_runs)
    print(f"wrote CFSv2 manifest: {manifest_path}")
    return 2 if failures else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except CFSv2Error as exc:
        print(f"CFSv2 error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
