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
    return urljoin(root.rstrip("/") + "/", common_refe…13722 tokens truncated…e"] = {
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
