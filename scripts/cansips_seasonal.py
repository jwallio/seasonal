#!/usr/bin/env python3
"""Fetch and render CanSIPS v3 seasonal 500-mb height anomalies.

CanSIPS v3 publishes 40-member global GRIB2 files through the ECCC MSC
Datamart.  This adapter computes the 40-member forecast mean, subtracts the
matching 1991-2020 hindcast climatology, and sends the resulting fields through
the shared operational seasonal renderer used by CFSv2 and SEAS5.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CFSv2Error,
    DEFAULT_REGION,
    PRECIP_ANOMALY_PALETTE,
    PRECIP_ANOMALY_TICKS,
    Grid,
    download_file,
    ensure_border_files,
    mean_grids,
    read_grid_csv,
    read_grid_state,
    relative_path,
    render_map,
    seasonal_period_label,
    subtract_grids,
    target_period,
    write_grid_state,
)


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

TEMPERATURE_ANOMALY_TICKS = list(range(-8, 9))
TEMPERATURE_ANOMALY_PALETTE = [
    "#24527a", "#306b90", "#3d83a6", "#4891b0", "#539cb8", "#70b2c6",
    "#95c4d3", "#e1e4e7", "#f2cecd", "#eaaaa8", "#e28c8b", "#db797b",
    "#d3686c", "#ca5861", "#a1384a", "#84283f",
]
MSLP_ANOMALY_TICKS = list(range(-20, 21, 2))
SST_ANOMALY_TICKS = list(range(-8, 9))
SSH_ANOMALY_TICKS = [round(-0.50 + index * 0.10, 2) for index in range(11)]
SSH_ANOMALY_PALETTE = [
    "#24527a", "#3d83a6", "#539cb8", "#70b2c6", "#95c4d3",
    "#e1e4e7", "#f2cecd", "#eaaaa8", "#d3686c", "#a1384a",
]

PRODUCT_Z500_ANOMALY = "500mb_height_anomaly"
PRODUCT_850MB_TEMPERATURE_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_2M_TEMPERATURE_ANOMALY = "2m_temperature_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
PRODUCT_SST_ANOMALY = "sst_anomaly"
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
        "anomaly_min": -200.0,
        "anomaly_max": 200.0,
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
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
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
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
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
        "region": CANSIPS_DEFAULT_REGION,
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
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -20.0,
        "anomaly_max": 20.0,
        "anomaly_ticks": MSLP_ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "Pressure divided by 100 to convert Pa to hPa",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
    },
    PRODUCT_SST_ANOMALY: {
        "name": PRODUCT_SST_ANOMALY,
        "source_var": "WaterTemp",
        "level": "Sfc",
        "state_tag": "sst",
        "id_token": "ssta",
        "title": "CanSIPS v3 Sea-Surface Temperature Anomaly (°C)",
        "absolute_title": "CanSIPS v3 Sea-Surface Temperature (°C)",
        "field": "sst_anomaly",
        "raw_field": "WaterTemp at the surface",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": SST_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Sea-surface temperature anomaly (°C)",
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
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  •  {baseline_label}  •  Sea-surface height anomaly (m)",
    },
}
PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_850MB_TEMPERATURE_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_2M_TEMPERATURE_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "Precipitation Anomaly",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
    PRODUCT_SST_ANOMALY: "Sea-Surface Temperature Anomaly",
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
            payload["runs"].extend(existing["runs"])
    new_entries = run_entries if isinstance(run_entries, list) else [run_entries]
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
            f"{len(seasonal_leads)}-month seasonal mean of monthly forecast anomalies"
            if seasonal_leads
            else "monthly 40-member forecast anomaly"
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "grid": {"longitude_count": 360, "latitude_count": 180, "resolution": "1 degree", "layout": "LatLon1.0"},
        "climatology": {
            "source": "CanSIPS v3 hindcast ensemble means",
            "years": f"{args.climo_start}-{args.climo_end}",
            "initialization_month": init[4:6],
            "method": "forecast 40-member mean minus the matching-initialization-month and lead hindcast climatology",
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
            "aggregation": "monthly forecast anomaly",
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
            target_entry["source_files"] = [forecast_source]
            target_entry["baseline"] = {
                "source": baseline_label,
                "years": f"{args.climo_start}-{args.climo_end}",
                "initialization_month": init[4:6],
                "lead_month": lead,
                "ensemble_members": CANSIPS_ENSEMBLE_MEMBERS,
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
            "aggregation": f"{len(seasonal_leads)}-month seasonal mean",
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
            seasonal_anomaly = mean_grids([anomaly_grids[lead] for lead in seasonal_leads])
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
                "method": "mean of monthly forecast-minus-hindcast anomalies",
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
    wgrib2 = find_wgrib2(args.wgrib2)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    common_reference_dir = resolve_repo_path(args.common_reference_dir, repo_root) if args.common_reference_dir else None
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)
    entries: list[dict[str, Any]] = []
    failures = 0
    products = selected_products(args.product)
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
