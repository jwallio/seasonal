#!/usr/bin/env python3
"""Fetch and render ECMWF SEAS5 seasonal products from the public C3S archive.

The Planette/AWS archive stores the original SEAS5 daily ensemble fields in
Icechunk-backed Zarr stores.  This adapter keeps the source access explicit,
reduces daily/member data to calendar-month means or totals, and derives
anomalies from the matching SEAS5 hindcast initialization/target-month
climatology.  It shares WN2's operational map renderer and static manifest
contract with the CFSv2 viewer without treating the two models as the same
source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    CONUS_PRECIP_REGION,
    CFSv2Error,
    DEFAULT_REGION,
    PRECIP_ANOMALY_PALETTE,
    SWE_ANOMALY_PALETTE,
    Grid,
    ensure_border_files,
    mean_grids,
    relative_path,
    render_map,
    sum_grids,
)


AWS_BUCKET = "planette-c3s-seasonal-forecasts"
AWS_REGION = "us-east-2"
AWS_ROOT = f"s3://{AWS_BUCKET}/seas5/"
S3_LIST_URL = f"https://s3.{AWS_REGION}.amazonaws.com/{AWS_BUCKET}/"
HINDCAST_START = 1981
HINDCAST_END = 2016
GEOPOTENTIAL_GRAVITY = 9.80665
MM_TO_INCH = 1.0 / 25.4
SOURCE_LABEL = "ECMWF SEAS5 / C3S archive"
SOURCE_URL = "https://registry.opendata.aws/planette_c3s_seasonal_forecast_data/"

Z500_ANOMALY = "500mb_height_anomaly"
T2M_ANOMALY = "2m_temperature_anomaly"
PRECIP_ANOMALY = "precipitation_anomaly"
SNOWFALL_ANOMALY = "snowfall_anomaly"
SST_ANOMALY = "sst_anomaly"
MSLP_ANOMALY = "mslp_anomaly"

TEMP_PALETTE = [
    "#244f78",
    "#326d99",
    "#4f91b5",
    "#75b5ca",
    "#a7ced9",
    "#dce9eb",
    "#f8f8f4",
    "#f4d8d3",
    "#eeb2aa",
    "#df8d86",
    "#ce696b",
    "#b84857",
    "#943643",
]
MSLP_PALETTE = [
    "#315f85",
    "#4e83a3",
    "#72a6bb",
    "#a5c6cf",
    "#d9e5e6",
    "#f7f7f2",
    "#f0d9d4",
    "#dfa69f",
    "#c87974",
    "#ac4f55",
    "#8a3542",
]


PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    Z500_ANOMALY: {
        "name": Z500_ANOMALY,
        "variable": "z500",
        "field": "z500_anomaly",
        "raw_field": "z500 / geopotential",
        "raw_units": "m**2 s**-2",
        "units": "m",
        "seasonal_units": "m",
        "title": "SEAS5 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "SEAS5 500-mb Geopotential Height (m)",
        "height_contours": True,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -200.0,
        "anomaly_max": 200.0,
        "anomaly_ticks": [-200, -160, -120, -80, -40, 0, 40, 80, 120, 160, 200],
        "anomaly_palette": ANOMALY_PALETTE,
        "conversion": "geopotential divided by standard gravity to convert m² s⁻² to geopotential meters",
        "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam",
    },
    T2M_ANOMALY: {
        "name": T2M_ANOMALY,
        "variable": "t2m",
        "field": "t2m_anomaly",
        "raw_field": "t2m",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "title": "SEAS5 2-m Temperature Anomaly (°C)",
        "absolute_title": "SEAS5 2-m Temperature (°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -6.0,
        "anomaly_max": 6.0,
        "anomaly_ticks": [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
    },
    PRECIP_ANOMALY: {
        "name": PRECIP_ANOMALY,
        "variable": "pr",
        "field": "precipitation_anomaly",
        "raw_field": "pr / total precipitation",
        "raw_units": "kg m**-2 s**-1",
        "units": "in",
        "seasonal_units": "in",
        "title": "SEAS5 CONUS Precipitation Anomaly (in)",
        "absolute_title": "SEAS5 CONUS Precipitation (in)",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total",
        "seasonal_reducer": "sum",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": list(range(-8, 9)),
        "anomaly_palette": PRECIP_ANOMALY_PALETTE,
        "conversion": "daily rate multiplied by target-month seconds, converted from liquid-water millimetres to inches",
        "header_detail": "{source_label}  •  {baseline_label}  •  Precipitation accumulation (in)  •  CONUS domain",
    },
    SNOWFALL_ANOMALY: {
        "name": SNOWFALL_ANOMALY,
        "variable": "sf",
        "field": "snowfall_anomaly",
        "raw_field": "sf / snowfall",
        "raw_units": "kg m**-2 s**-1",
        "units": "in",
        "seasonal_units": "in",
        "title": "SEAS5 CONUS Snowfall Water-Equivalent Anomaly (in)",
        "absolute_title": "SEAS5 CONUS Snowfall Water Equivalent (in)",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total",
        "seasonal_reducer": "sum",
        "anomaly_min": -4.0,
        "anomaly_max": 4.0,
        "anomaly_ticks": [-4, -3, -2, -1, 0, 1, 2, 3, 4],
        "anomaly_palette": SWE_ANOMALY_PALETTE,
        "conversion": "daily snowfall liquid-water-equivalent rate multiplied by target-month seconds and converted to inches",
        "header_detail": "{source_label}  •  {baseline_label}  •  Snowfall liquid-water equivalent (in)  •  CONUS domain",
    },
    SST_ANOMALY: {
        "name": SST_ANOMALY,
        "variable": "sst",
        "field": "sst_anomaly",
        "raw_field": "sst",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "title": "SEAS5 Sea-Surface Temperature Anomaly (°C)",
        "absolute_title": "SEAS5 Sea-Surface Temperature (°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -3.0,
        "anomaly_max": 3.0,
        "anomaly_ticks": [-3, -2, -1, 0, 1, 2, 3],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  •  {baseline_label}  •  Sea-surface temperature anomaly (°C)",
    },
    MSLP_ANOMALY: {
        "name": MSLP_ANOMALY,
        "variable": "slp",
        "field": "mslp_anomaly",
        "raw_field": "slp / mean sea-level pressure",
        "raw_units": "Pa",
        "units": "hPa",
        "seasonal_units": "hPa",
        "title": "SEAS5 Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "SEAS5 Mean Sea-Level Pressure (hPa)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -20.0,
        "anomaly_max": 20.0,
        "anomaly_ticks": [-20, -15, -10, -5, 0, 5, 10, 15, 20],
        "anomaly_palette": MSLP_PALETTE,
        "conversion": "Pa divided by 100 to convert mean sea-level pressure to hPa",
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
    },
}


class SEAS5Error(CFSv2Error):
    """A user-actionable SEAS5 source or rendering error."""


def get_product_spec(product: str) -> dict[str, Any]:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        raise SEAS5Error(
            f"unsupported SEAS5 product {product!r}; choose from {', '.join(PRODUCT_SPECS)}"
        ) from exc


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def parse_init(value: str) -> str:
    if re.fullmatch(r"\d{6}", value):
        value = f"{value}01"
    if not re.fullmatch(r"\d{8}", value):
        raise SEAS5Error("--init must be YYYYMM, YYYYMMDD, or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise SEAS5Error(f"invalid SEAS5 initialization date: {value}") from exc
    if parsed.day != 1:
        raise SEAS5Error("SEAS5 initialization dates must be the first of a month")
    return f"{value}00"


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise SEAS5Error(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise SEAS5Error(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise SEAS5Error(f"{label} cannot be empty")
    return result


def parse_years(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})\s*[-:]\s*(\d{4})", value.strip())
    if not match:
        raise SEAS5Error("--climo-years must be YYYY-YYYY")
    start, end = (int(item) for item in match.groups())
    if start < HINDCAST_START or end > HINDCAST_END or start > end:
        raise SEAS5Error(
            f"--climo-years must stay inside the SEAS5 hindcast period {HINDCAST_START}-{HINDCAST_END}"
        )
    return start, end


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
        return f"{start:%b}–{end:%b %Y}"
    return f"{start:%b %Y}–{end:%b %Y}"


def store_prefix(variable: str, year: int) -> str:
    return f"seas5/sys51/{variable}/day/1latx1lon/seas5_sys51_{variable}_day_1latx1lon_{year}.zarr"


def available_years(variable: str) -> list[int]:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - environment contract
        raise SEAS5Error("requests is required for the public SEAS5 archive") from exc
    prefix = f"seas5/sys51/{variable}/day/1latx1lon/"
    try:
        response = requests.get(
            S3_LIST_URL,
            params={"list-type": "2", "prefix": prefix, "delimiter": "/"},
            timeout=(20, 60),
        )
        response.raise_for_status()
    except Exception as exc:
        raise SEAS5Error(f"could not list the public SEAS5 {variable} archive: {exc}") from exc
    years = sorted(
        {
            int(match.group(1))
            for match in re.finditer(
                rf"seas5_sys51_{re.escape(variable)}_day_1latx1lon_(\d{{4}})\.zarr/",
                response.text,
            )
        }
    )
    if not years:
        raise SEAS5Error(f"the public SEAS5 archive has no {variable} yearly stores")
    return years


def datetime64_to_init(value: np.datetime64) -> str:
    text = np.datetime_as_string(value, unit="D")
    return f"{text.replace('-', '')}00"


class SEAS5Archive:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._datasets: dict[tuple[str, int], Any] = {}
        self._years: dict[str, list[int]] = {}

    def years(self, variable: str) -> list[int]:
        if variable not in self._years:
            self._years[variable] = available_years(variable)
        return self._years[variable]

    def latest_init(self) -> str:
        variable = "z500"
        for year in reversed(self.years(variable)):
            dataset = self.open(variable, year)
            if dataset.sizes.get("init_time", 0):
                return datetime64_to_init(np.asarray(dataset.init_time.values)[-1])
        raise SEAS5Error("the public SEAS5 archive listed no usable initialization")

    def open(self, variable: str, year: int):
        key = (variable, year)
        if key in self._datasets:
            return self._datasets[key]
        try:
            import icechunk as ic
            import xarray as xr
        except ImportError as exc:  # pragma: no cover - environment contract
            raise SEAS5Error(
                "SEAS5 rendering requires xarray, zarr, icechunk, and dask[array]"
            ) from exc
        if year not in self.years(variable):
            raise SEAS5Error(f"SEAS5 {variable} archive has no store for {year}")
        prefix = store_prefix(variable, year)
        try:
            storage = ic.s3_storage(
                bucket=AWS_BUCKET,
                prefix=prefix,
                region=AWS_REGION,
                anonymous=True,
            )
            repository = ic.Repository.open(storage=storage)
            session = repository.readonly_session("main")
            dataset = xr.open_dataset(
                session.store,
                engine="zarr",
                consolidated=False,
                decode_timedelta=True,
                chunks={},
            )
        except Exception as exc:
            raise SEAS5Error(f"could not open SEAS5 {variable} {year} store: {exc}") from exc
        self._datasets[key] = dataset
        return dataset


def dataset_init_index(dataset: Any, init: str) -> int:
    wanted = np.datetime64(f"{init[:4]}-{init[4:6]}-{init[6:8]}")
    values = np.asarray(dataset.init_time.values).astype("datetime64[D]")
    matches = np.flatnonzero(values == wanted)
    if not len(matches):
        available = ", ".join(np.datetime_as_string(value, unit="D") for value in values[:5])
        raise SEAS5Error(f"SEAS5 store has no initialization {init[:8]} (starts with {available})")
    return int(matches[0])


def target_lead_indices(dataset: Any, target: str, init_index: int) -> np.ndarray:
    wanted = np.datetime64(f"{target[:4]}-{target[4:6]}")
    values = np.asarray(dataset.valid_time.values)
    if values.ndim == 2:
        values = values[init_index]
    elif dataset.sizes.get("init_time", 1) > 1:
        # Some yearly stores expose valid_time only for their newest
        # initialization even though the data variable has several
        # init_time entries.  The lead coordinate is authoritative for an
        # explicit historical initialization request.
        init_value = np.asarray(dataset.init_time.values)[init_index].astype("datetime64[ns]")
        values = init_value + np.asarray(dataset.lead.values).astype("timedelta64[ns]")
    values = values.astype("datetime64[M]")
    indices = np.flatnonzero(values == wanted)
    if not len(indices):
        raise SEAS5Error(
            f"SEAS5 store does not reach target month {target}; available valid period is "
            f"{np.datetime_as_string(values[0], unit='M')} to {np.datetime_as_string(values[-1], unit='M')}"
        )
    return indices


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    return int((dt.datetime(next_year, next_month, 1) - start).total_seconds())


def convert_values(values: np.ndarray, product: dict[str, Any], target: str) -> np.ndarray:
    variable = product["variable"]
    converted = np.asarray(values, dtype=float)
    if variable == "z500":
        return converted / GEOPOTENTIAL_GRAVITY
    if variable in {"t2m", "sst"}:
        return converted - 273.15
    if variable == "pr":
        return converted * month_seconds(target) * MM_TO_INCH
    if variable == "sf":
        return converted * month_seconds(target) * MM_TO_INCH
    if variable == "slp":
        return converted / 100.0
    raise SEAS5Error(f"no unit conversion is defined for SEAS5 variable {variable}")


def grid_from_dataset(
    dataset: Any,
    product: dict[str, Any],
    init: str,
    target: str,
) -> tuple[Grid, int]:
    init_index = dataset_init_index(dataset, init)
    lead_indices = target_lead_indices(dataset, target, init_index)
    lats = np.asarray(dataset.lat.values, dtype=float)
    lons = np.asarray(dataset.lon.values, dtype=float)
    # The archive has descending north-to-south latitudes and 0.5–359.5 or
    # -179.5–179.5 longitudes depending on the producer conversion. Normalize
    # both into the renderer's ascending [-180, 180] convention.
    lon_order = np.argsort(((lons + 180.0) % 360.0) - 180.0)
    normalized_lons = (((lons + 180.0) % 360.0) - 180.0)[lon_order]
    lat_order = np.argsort(lats)
    lat_indices = lat_order
    variable = dataset[product["variable"]]
    selected = variable.isel(
        init_time=init_index,
        lead=lead_indices,
        lat=lat_indices,
        lon=lon_order,
    )
    try:
        raw = selected.mean(dim=("number", "lead")).compute().values
    except Exception as exc:
        raise SEAS5Error(
            f"could not compute the SEAS5 {product['variable']} ensemble/month mean for {target}: {exc}"
        ) from exc
    converted = convert_values(raw, product, target)
    return Grid(
        lons=[float(value) for value in normalized_lons],
        lats=[float(value) for value in lats[lat_indices]],
        values=converted.tolist(),
    ), int(dataset.sizes.get("number", 0))


def climo_cache_path(
    cache_dir: Path,
    product: dict[str, Any],
    init_month: int,
    target: str,
    lead: int,
    years: tuple[int, int],
) -> Path:
    return cache_dir / "climo" / (
        f"{product['variable']}_{init_month:02d}_{target[4:]}_lead{lead:02d}_{years[0]}-{years[1]}.npz"
    )


def read_cached_grid(path: Path) -> Grid | None:
    if not path.exists():
        return None
    try:
        payload = np.load(path)
        return Grid(
            lons=[float(value) for value in payload["lons"]],
            lats=[float(value) for value in payload["lats"]],
            values=payload["values"].astype(float).tolist(),
        )
    except (OSError, KeyError, ValueError):
        return None


def write_cached_grid(path: Path, grid: Grid) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            lons=np.asarray(grid.lons, dtype=float),
            lats=np.asarray(grid.lats, dtype=float),
            values=np.asarray(grid.values, dtype=float),
        )
    temporary.replace(path)


def hindcast_climatology(
    archive: SEAS5Archive,
    product: dict[str, Any],
    init_month: int,
    target: str,
    lead: int,
    years: tuple[int, int],
    cache_dir: Path,
) -> tuple[Grid, list[int]]:
    cache_path = climo_cache_path(cache_dir, product, init_month, target, lead, years)
    cached = read_cached_grid(cache_path)
    if cached is not None:
        metadata_path = cache_path.with_suffix(".json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            return cached, [int(value) for value in metadata.get("years_used", [])]
        except (OSError, ValueError, TypeError):
            pass

    year_values: list[int] = []
    accumulated: np.ndarray | None = None
    template: Grid | None = None
    available = set(archive.years(product["variable"]))
    for year in range(years[0], years[1] + 1):
        if year not in available:
            continue
        try:
            dataset = archive.open(product["variable"], year)
            init_values = np.asarray(dataset.init_time.values).astype("datetime64[M]")
            init_matches = np.flatnonzero(
                np.array([int(str(value)[5:7]) for value in init_values]) == init_month
            )
            if not len(init_matches):
                continue
            hindcast_init = datetime64_to_init(np.asarray(dataset.init_time.values)[int(init_matches[0])])
            hindcast_target = target_month(hindcast_init, lead)
            monthly_grid, _ = grid_from_dataset(dataset, product, hindcast_init, hindcast_target)
        except SEAS5Error as exc:
            print(f"warning: skipping SEAS5 hindcast year {year} for {target}: {exc}", file=sys.stderr)
            continue
        array = np.asarray(monthly_grid.values, dtype=float)
        if accumulated is None:
            accumulated = np.zeros_like(array, dtype=float)
            template = monthly_grid
        accumulated += np.nan_to_num(array, nan=0.0)
        year_values.append(year)

    if accumulated is None or template is None or len(year_values) < 10:
        raise SEAS5Error(
            f"SEAS5 hindcast climatology for {product['variable']} {target} has only "
            f"{len(year_values)} usable years; at least 10 are required"
        )
    climatology = Grid(
        lons=template.lons,
        lats=template.lats,
        values=(accumulated / len(year_values)).tolist(),
    )
    write_cached_grid(cache_path, climatology)
    metadata_path = cache_path.with_suffix(".json")
    metadata_path.write_text(
        json.dumps({"years_used": year_values, "target": target, "init_month": init_month}) + "\n",
        encoding="utf-8",
    )
    return climatology, year_values


def subtract_grids(left: Grid, right: Grid) -> Grid:
    if left.lons != right.lons or left.lats != right.lats:
        raise SEAS5Error("forecast and SEAS5 climatology grids do not match")
    values = (
        np.asarray(left.values, dtype=float) - np.asarray(right.values, dtype=float)
    ).tolist()
    return Grid(lons=left.lons[:], lats=left.lats[:], values=values)


def write_manifest(
    path: Path,
    repo_root: Path,
    run_entry: dict[str, Any],
    previous_manifest: Path | None,
    retain_runs: int,
) -> None:
    if retain_runs < 1:
        raise SEAS5Error("manifest retention must keep at least one run")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "seas5_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": SOURCE_LABEL,
        "source_url": SOURCE_URL,
        "archive_root": AWS_ROOT,
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
            raise SEAS5Error(f"could not read existing SEAS5 manifest {existing_path}: {exc}") from exc
        if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
            payload["runs"].extend(existing["runs"])
    unique_runs: dict[str, dict[str, Any]] = {}
    for run in payload["runs"]:
        if isinstance(run, dict) and run.get("id"):
            unique_runs[str(run["id"])] = run
    unique_runs[str(run_entry["id"])] = run_entry
    payload["runs"] = sorted(
        unique_runs.values(),
        key=lambda item: (str(item.get("init_utc", "")), str(item.get("generated_utc", "")), str(item.get("id", ""))),
        reverse=True,
    )[:retain_runs]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=tuple(PRODUCT_SPECS), default=Z500_ANOMALY)
    parser.add_argument("--init", default="latest", help="SEAS5 initialization as YYYYMM, YYYYMMDD, or latest")
    parser.add_argument("--lead-months", default="4,5,6", help="comma-separated target leads")
    parser.add_argument("--seasonal-window", default="4,5,6", help="consecutive target leads for the seasonal map")
    parser.add_argument("--climo-years", default="1981-2016", help="SEAS5 hindcast climatology years")
    parser.add_argument("--cache-dir", default=".cache/seas5")
    parser.add_argument("--output-dir", default="public/seasonal/seas5")
    parser.add_argument("--manifest", default="public/seasonal/seas5_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-runs", type=int, default=4)
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    parser.add_argument("--absolute", action="store_true", help="render the raw 500-mb field for a source smoke test")
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    product = get_product_spec(args.product)
    archive = SEAS5Archive(resolve_repo_path(args.cache_dir, repo_root))
    init = archive.latest_init() if args.init == "latest" else parse_init(args.init)
    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    leads = parse_int_list(args.lead_months, "lead months", 1, 7)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 1, 7) if args.seasonal_window else []
    if seasonal_leads:
        expected = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected:
            raise SEAS5Error("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    climo_years = parse_years(args.climo_years)
    if args.absolute and args.product != Z500_ANOMALY:
        raise SEAS5Error("--absolute is only supported for the 500-mb field")
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)
    run_id = f"seas5-{init}-{args.product}"
    run_entry: dict[str, Any] = {
        "id": run_id,
        "source": SOURCE_LABEL,
        "source_url": SOURCE_URL,
        "archive_root": AWS_ROOT,
        "model": "ECMWF SEAS5",
        "product": args.product,
        "variable": product["variable"],
        "init_utc": iso_utc(init_date),
        "statistic": "ensemble_mean",
        "ensemble_scope": "SEAS5 forecast ensemble",
        "ensemble_members": None,
        "aggregation": (
            f"{len(seasonal_leads)}-month {product['seasonal_reducer']} of daily ensemble means"
            if seasonal_leads
            else "calendar-month daily ensemble mean"
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "conversion": product["conversion"],
        "climatology": {
            "source": "SEAS5 hindcasts",
            "years_requested": f"{climo_years[0]}-{climo_years[1]}",
            "method": "matching initialization month and target calendar month; ensemble and daily means",
        },
        "border_sources": [] if args.no_borders else [{"name": path.name} for path in border_paths],
        "targets": [],
        "status": "planned",
    }
    try:
        run_entry["archive_latest_init"] = archive.latest_init()
        run_entry["archive_years"] = archive.years(product["variable"])
        archive_date = dt.datetime.strptime(run_entry["archive_latest_init"], "%Y%m%d%H").replace(
            tzinfo=dt.timezone.utc
        )
        archive_age_days = max(0, (dt.datetime.now(dt.timezone.utc) - archive_date).days)
        run_entry["archive_age_days"] = archive_age_days
        if archive_age_days > 62:
            run_entry["source_warning"] = (
                f"The newest SEAS5 initialization currently available in the public archive is "
                f"{run_entry['archive_latest_init']}; it is {archive_age_days} days old."
            )
    except SEAS5Error as exc:
        raise SEAS5Error(f"could not establish SEAS5 archive metadata: {exc}") from exc

    forecast_grids: dict[int, Grid] = {}
    baseline_grids: dict[int, Grid] = {}
    target_entries: dict[int, dict[str, Any]] = {}
    failures = 0
    used_climo_years: set[int] = set()
    for lead in leads:
        target = target_month(init, lead)
        valid_start, valid_end = target_period(target)
        target_entry: dict[str, Any] = {
            "id": f"{run_id}-lead{lead:02d}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": lead,
            "target_month": target,
            "aggregation": "monthly total" if product["monthly_reducer"] == "total" else "monthly mean",
            "field": product["field"],
            "units": product["units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "source_store_year": int(init[:4]),
            "status": "planned",
        }
        try:
            dataset = archive.open(product["variable"], int(init[:4]))
            forecast, member_count = grid_from_dataset(dataset, product, init, target)
            forecast_grids[lead] = forecast
            target_entry["ensemble_members"] = member_count
            run_entry["ensemble_members"] = member_count
            target_entry["status"] = "decoded"
            if args.decode_only:
                run_entry["targets"].append(target_entry)
                target_entries[lead] = target_entry
                continue
            years_used: list[int] = []
            if args.absolute:
                anomaly = forecast
            else:
                baseline, years_used = hindcast_climatology(
                    archive,
                    product,
                init_date.month,
                target,
                lead,
                climo_years,
                cache_dir,
                )
                baseline_grids[lead] = baseline
                used_climo_years.update(years_used)
                anomaly = subtract_grids(forecast, baseline)
            output_path = output_dir / init[:8] / f"seas5_{product['variable']}_{target}.jpg"
            render_map(
                anomaly if not args.absolute else forecast,
                init,
                target,
                lead,
                list(range(member_count)),
                output_path,
                anomaly=not args.absolute,
                baseline_label=f"SEAS5 hindcast climatology; {climo_years[0]}-{climo_years[1]}",
                border_paths=border_paths,
                height_grid=forecast if product["height_contours"] else None,
                product_spec={**product, "source_label": SOURCE_LABEL},
            )
            target_entry["baseline"] = (
                {"status": "not_applicable", "reason": "absolute source smoke output"}
                if args.absolute
                else {
                    "source": "SEAS5 hindcasts",
                    "years_requested": f"{climo_years[0]}-{climo_years[1]}",
                    "years_used": years_used,
                }
            )
            target_entry["image"] = relative_path(output_path, repo_root)
            target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"SEAS5 target {target} lead {lead} failed: {exc}", file=sys.stderr)
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
            "aggregation": f"{len(seasonal_leads)}-month {product['seasonal_reducer']}",
            "field": product["field"],
            "units": product["seasonal_units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "monthly_leads": seasonal_leads,
            "status": "planned",
        }
        try:
            if any(lead not in forecast_grids for lead in seasonal_leads):
                raise SEAS5Error("seasonal window is missing one or more forecast grids")
            if not args.absolute and any(lead not in baseline_grids for lead in seasonal_leads):
                raise SEAS5Error("seasonal window is missing one or more climatology grids")
            combine = sum_grids if product["seasonal_reducer"] == "sum" else mean_grids
            seasonal_forecast = combine([forecast_grids[lead] for lead in seasonal_leads])
            if args.absolute:
                seasonal_anomaly = seasonal_forecast
            else:
                seasonal_baseline = combine([baseline_grids[lead] for lead in seasonal_leads])
                seasonal_anomaly = subtract_grids(seasonal_forecast, seasonal_baseline)
            output_path = output_dir / init[:8] / f"seas5_{product['variable']}_{first_target}-{last_target}.jpg"
            render_map(
                seasonal_anomaly if not args.absolute else seasonal_forecast,
                init,
                first_target,
                f"{first_lead}–{last_lead}",
                list(range(int(run_entry.get("ensemble_members") or 0))),
                output_path,
                anomaly=not args.absolute,
                baseline_label=f"SEAS5 hindcast climatology; {climo_years[0]}-{climo_years[1]}",
                border_paths=border_paths,
                period_label=seasonal_period_label(first_target, last_target),
                ensemble_label=f"{run_entry.get('ensemble_members') or '—'}-member mean",
                height_grid=seasonal_forecast if product["height_contours"] else None,
                product_spec={**product, "source_label": SOURCE_LABEL},
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            seasonal_entry["baseline"] = (
                {"status": "not_applicable", "reason": "absolute source smoke output"}
                if args.absolute
                else {
                    "source": "SEAS5 hindcasts",
                    "years_requested": f"{climo_years[0]}-{climo_years[1]}",
                    "years_used": sorted(used_climo_years),
                }
            )
            seasonal_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            seasonal_entry["status"] = "failed"
            seasonal_entry["error"] = str(exc)
            print(f"SEAS5 seasonal window {first_target}-{last_target} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(seasonal_entry)

    statuses = [target.get("status") for target in run_entry["targets"]]
    run_entry["climatology"]["years_used"] = sorted(used_climo_years)
    run_entry["status"] = "failed" if failures and not any(status != "failed" for status in statuses) else (
        "partial" if failures else ("decoded" if args.decode_only else "rendered")
    )
    run_entry["output_dir"] = relative_path(output_dir, repo_root)
    previous = resolve_repo_path(args.previous_manifest, repo_root) if args.previous_manifest else None
    write_manifest(manifest_path, repo_root, run_entry, previous, args.retain_runs)
    print(f"wrote SEAS5 manifest: {manifest_path}")
    return 2 if failures else 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except SEAS5Error as exc:
        print(f"SEAS5 ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
