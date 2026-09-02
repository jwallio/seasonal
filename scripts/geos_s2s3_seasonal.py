#!/usr/bin/env python3
"""Render numerical NASA GEOS-S2S-3 seasonal anomaly guidance.

NASA's NCCS data share publishes monthly NetCDF forecasts as a 40-member
lag/burst package. Ten selected members continue through month nine. This
adapter forms the available-member mean for each target, subtracts NASA's
lead-matched provider drift climatology, and uses the shared seasonal renderer.

The public long-range archive currently named ``z500`` is validated strictly.
It is rejected unless the NetCDF pressure coordinate is exactly 500 hPa; this
prevents the current 200-hPa extraction from being published as a 500-mb map.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CONUS_REGION,
    CONUS_PRECIP_REGION,
    DEFAULT_REGION,
    Grid,
    MSLP_ANOMALY_PALETTE,
    MSLP_ANOMALY_TICKS,
    NORTHERN_HEMISPHERE_REGION,
    PRECIP_ANOMALY_PALETTE,
    PRECIP_ANOMALY_TICKS,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    download_file,
    ensure_border_files,
    mean_grids,
    prepare_product_grid,
    relative_path,
    render_map,
    seasonal_period_label,
    subtract_grids,
    sum_grids,
)
from seasonal_products import is_retired_product


NASA_DATA_ROOT = "https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/"
NASA_NRT_ROOT = urljoin(NASA_DATA_ROOT, "NRT/APCN/")
NASA_DRIFT_ROOT = urljoin(NASA_DATA_ROOT, "Drift/for_APCN/")
NASA_PRIMER_URL = urljoin(NASA_DATA_ROOT, "GEOS-S2S-3-primer.pdf")
NASA_ATMRIVER_ROOT = urljoin(NASA_DATA_ROOT, "NRT/AtmRiver/")
NASA_HISTORY_CONFIG_URL = (
    "https://github.com/GEOS-ESM/GEOS-S2S-3/blob/main/"
    "src/Applications/GEOSgcm_App/HISTORY.AOGCM-S2Sv3.rc.tmpl"
)

EXPECTED_TOTAL_MEMBERS = 40
EXPECTED_LONG_RANGE_MEMBERS = 10
MAX_LEAD = 8
DRIFT_LABEL = "NASA GEOS-S2S-3 provider drift climatology"

PRODUCT_Z500_ANOMALY = "500mb_height_anomaly"
PRODUCT_Z500_ANOMALY_NH = "500mb_height_anomaly_nh"
PRODUCT_T850_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_T2M_ANOMALY = "2m_temperature_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    PRODUCT_Z500_ANOMALY: {
        "name": PRODUCT_Z500_ANOMALY,
        "archive_token": "z500",
        "forecast_variable": "H",
        "drift_variable": "z500",
        "expected_units": ("m",),
        "expected_level": 500.0,
        "id_token": "z500a",
        "title": "GEOS-S2S-3 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "GEOS-S2S-3 500-mb Geopotential Height (m)",
        "field": "z500_anomaly",
        "raw_field": "H at 500 hPa",
        "raw_units": "m",
        "units": "m",
        "height_contours": True,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": -100.0,
        "anomaly_max": 100.0,
        "anomaly_ticks": ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam",
        "scheduled": False,
    },
    PRODUCT_T850_ANOMALY: {
        "name": PRODUCT_T850_ANOMALY,
        "archive_token": "t850",
        "forecast_variable": "T",
        "drift_variable": "t850",
        "expected_units": ("K",),
        "expected_level": 850.0,
        "id_token": "t850a",
        "title": "GEOS-S2S-3 850-mb Temperature Anomaly (°C)",
        "absolute_title": "GEOS-S2S-3 850-mb Temperature (°C)",
        "field": "temperature_850mb_anomaly",
        "raw_field": "T at 850 hPa",
        "raw_units": "K",
        "units": "°C",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  •  {baseline_label}  •  850-mb temperature anomaly (°C)",
        "scheduled": True,
    },
    PRODUCT_T2M_ANOMALY: {
        "name": PRODUCT_T2M_ANOMALY,
        "archive_token": "at",
        "forecast_variable": "T2M",
        "drift_variable": "at",
        "expected_units": ("K",),
        "expected_level": None,
        "id_token": "t2ma",
        "title": "GEOS-S2S-3 2-m Temperature Anomaly (°C)",
        "absolute_title": "GEOS-S2S-3 2-m Temperature (°C)",
        "field": "temperature_2m_anomaly",
        "raw_field": "T2M at 2 m",
        "raw_units": "K",
        "units": "°C",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
        "scheduled": True,
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "archive_token": "precip",
        "forecast_variable": "PRECTOTCORR",
        "drift_variable": "precip",
        "expected_units": ("kg m-2 s-1", "kg/m2/s"),
        "expected_level": None,
        "id_token": "prcpa",
        "title": "GEOS-S2S-3 CONUS Precipitation Anomaly (in)",
        "absolute_title": "GEOS-S2S-3 CONUS Precipitation (in)",
        "field": "precipitation_anomaly",
        "raw_field": "PRECTOTCORR at the surface",
        "raw_units": "kg m-2 s-1",
        "units": "in",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "seasonal_reducer": "sum",
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "Monthly mean precipitation rate multiplied by calendar-month seconds and converted to inches",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": PRECIP_ANOMALY_TICKS,
        "anomaly_palette": PRECIP_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  •  {baseline_label}  •  Precipitation anomaly (in)  •  CONUS domain",
        "scheduled": True,
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "archive_token": "slp",
        "forecast_variable": "SLP",
        "drift_variable": "slp",
        "expected_units": ("Pa",),
        "expected_level": None,
        "id_token": "mslpa",
        "title": "GEOS-S2S-3 MSLP Anomaly (hPa)",
        "absolute_title": "GEOS-S2S-3 Mean Sea-Level Pressure (hPa)",
        "field": "mslp_anomaly",
        "raw_field": "SLP at mean sea level",
        "raw_units": "Pa",
        "units": "hPa",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "Sea-level pressure divided by 100 after anomaly calculation",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": list(range(-10, 11)),
        "anomaly_palette": MSLP_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
        "scheduled": True,
    },
}

PRODUCT_SPECS[PRODUCT_Z500_ANOMALY_NH] = {
    **PRODUCT_SPECS[PRODUCT_Z500_ANOMALY],
    "name": PRODUCT_Z500_ANOMALY_NH,
    "id_token": "z500a-nh",
    "region": NORTHERN_HEMISPHERE_REGION,
    "projection": "north_polar_stereographic",
    "projection_central_longitude": 0.0,
    "title": "GEOS-S2S-3 Northern Hemisphere 500-mb Geopotential Height & Anomaly (m)",
    "absolute_title": "GEOS-S2S-3 Northern Hemisphere 500-mb Geopotential Height (m)",
    "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam  •  Northern Hemisphere",
}

DEFAULT_PRODUCTS = tuple(name for name, spec in PRODUCT_SPECS.items() if spec["scheduled"])
SUPERENSEMBLE_PRODUCTS = frozenset(DEFAULT_PRODUCTS)
PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_Z500_ANOMALY_NH: "500-mb Height Anomaly · Northern Hemisphere",
    PRODUCT_T850_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_T2M_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "CONUS Precipitation Anomaly",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
}


class GEOSS2S3Error(RuntimeError):
    """A user-actionable NASA source, validation, or rendering error."""


@dataclass(frozen=True)
class ForecastMonth:
    grid: Grid
    target: str
    members: tuple[str, ...]
    expected_members: int
    source_files: tuple[str, ...]
    init_dates: tuple[str, ...]


@dataclass(frozen=True)
class GEOSMonth:
    anomaly: Grid
    forecast: Grid
    target: str
    members: tuple[str, ...]
    expected_members: int
    source_files: tuple[str, ...]
    init_dates: tuple[str, ...]
    archive_url: str
    drift_url: str
    drift_years: tuple[int, ...]


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    date = dt.datetime.strptime(init, "%Y%m")
    year, month = month_after(date.year, date.month, lead)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m").replace(tzinfo=dt.timezone.utc)
    year, month = month_after(start.year, start.month, 1)
    return iso_utc(start), iso_utc(dt.datetime(year, month, 1, tzinfo=dt.timezone.utc))


def parse_int_list(value: str, label: str) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise GEOSS2S3Error(f"invalid {label}: {item}") from exc
        if not 0 <= number <= MAX_LEAD:
            raise GEOSS2S3Error(f"{label} must stay between 0 and {MAX_LEAD}")
        if number not in result:
            result.append(number)
    if not result:
        raise GEOSS2S3Error(f"{label} cannot be empty")
    return result


def _request_text(url: str) -> str:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise GEOSS2S3Error("NASA GEOS-S2S-3 downloads require requests") from exc
    try:
        response = requests.get(url, timeout=(30, 120))
        response.raise_for_status()
        return response.text
    except Exception as exc:
        raise GEOSS2S3Error(f"could not read NASA directory {url}: {exc}") from exc


def discover_latest_init(root: str = NASA_NRT_ROOT) -> str:
    issues = sorted(set(re.findall(r'href="(\d{6})/"', _request_text(root))), reverse=True)
    if not issues:
        raise GEOSS2S3Error("NASA APCN directory contains no YYYYMM release")
    return issues[0]


def parse_init(value: str, root: str = NASA_NRT_ROOT) -> str:
    if value == "latest":
        return discover_latest_init(root)
    if not re.fullmatch(r"\d{6}", value):
        raise GEOSS2S3Error("--init must be latest or YYYYMM")
    try:
        dt.datetime.strptime(value, "%Y%m")
    except ValueError as exc:
        raise GEOSS2S3Error(f"invalid NASA release month: {value}") from exc
    return value


def selected_products(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(DEFAULT_PRODUCTS)
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in PRODUCT_SPECS]
    if unknown:
        raise GEOSS2S3Error(f"unsupported NASA product(s): {', '.join(unknown)}")
    if not names:
        raise GEOSS2S3Error("--product cannot be empty")
    return list(dict.fromkeys(names))


def archive_url(init: str, spec: dict[str, Any], root: str = NASA_NRT_ROOT) -> str:
    return urljoin(root.rstrip("/") + "/", f"{init}/{init}_{spec['archive_token']}.tar.xz")


def archive_path(cache_dir: Path, init: str, spec: dict[str, Any]) -> Path:
    return cache_dir / "forecast" / init / f"{init}_{spec['archive_token']}.tar.xz"


def drift_url(init: str, target: str, root: str = NASA_DRIFT_ROOT) -> str:
    init_name = dt.datetime.strptime(init, "%Y%m").strftime("%b").lower()
    return urljoin(root.rstrip("/") + "/", f"{init_name}.APCN.monthly.drift.{target[4:6]}.nc4")


def drift_path(cache_dir: Path, init: str, target: str) -> Path:
    return cache_dir / "drift" / Path(drift_url(init, target)).name


def _download(url: str, destination: Path, request_delay: float = 0.0) -> None:
    try:
        download_file(url, destination, max(0.0, request_delay), 0.0, attempts=3, timeout=(60, 1200))
    except Exception as exc:
        raise GEOSS2S3Error(f"NASA download failed for {url}: {exc}") from exc


def _normal_units(value: Any) -> str:
    return re.sub(r"[^a-z0-9-]", "", str(value).lower())


def _open_data_array(dataset, variable: str, spec: dict[str, Any], source: str):
    if variable not in dataset:
        candidates = [name for name in dataset.data_vars if name != "time_bnds"]
        if len(candidates) != 1:
            raise GEOSS2S3Error(
                f"{source} does not contain expected variable {variable}; found {', '.join(candidates) or 'none'}"
            )
        variable = candidates[0]
    array = dataset[variable]
    expected_units = {_normal_units(item) for item in spec["expected_units"]}
    actual_units = _normal_units(array.attrs.get("units", ""))
    if actual_units not in expected_units:
        raise GEOSS2S3Error(
            f"{source} {variable} units are {array.attrs.get('units', 'missing')!r}; expected {spec['expected_units']}"
        )
    expected_level = spec.get("expected_level")
    level_dims = [name for name in array.dims if name not in {"time", "lat", "lon"}]
    if expected_level is not None:
        if len(level_dims) != 1 or int(array.sizes[level_dims[0]]) != 1:
            raise GEOSS2S3Error(f"{source} must contain one pressure level at {expected_level:.0f} hPa")
        level_name = level_dims[0]
        level_value = float(dataset[level_name].values.reshape(-1)[0])
        if not math.isclose(level_value, float(expected_level), abs_tol=0.1):
            if spec["name"] == PRODUCT_Z500_ANOMALY:
                raise GEOSS2S3Error(
                    f"NASA APCN z500 safety check failed: {source} declares {level_value:.0f} hPa, not 500 hPa; "
                    "refusing to publish this field as a 500-mb map"
                )
            raise GEOSS2S3Error(f"{source} declares {level_value:.0f} hPa; expected {expected_level:.0f} hPa")
    elif level_dims and any(int(array.sizes[name]) != 1 for name in level_dims):
        raise GEOSS2S3Error(f"{source} contains unexpected non-singleton dimensions {level_dims}")
    return array.squeeze(drop=True)


def _time_month(value: Any) -> str:
    match = re.match(r"(\d{4})-(\d{2})", str(value))
    if not match:
        raise GEOSS2S3Error(f"could not decode NASA time coordinate {value!r}")
    return f"{match.group(1)}{match.group(2)}"


def _grid_from_array(array, source: str) -> Grid:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise GEOSS2S3Error("NASA NetCDF decoding requires numpy") from exc
    if set(array.dims) != {"lat", "lon"}:
        raise GEOSS2S3Error(f"{source} did not reduce to a latitude/longitude field: {array.dims}")
    array = array.transpose("lat", "lon")
    lons = np.asarray(array["lon"].values, dtype=float)
    lats = np.asarray(array["lat"].values, dtype=float)
    values = np.asarray(array.values, dtype=float)
    if values.shape != (lats.size, lons.size):
        raise GEOSS2S3Error(f"{source} has inconsistent latitude/longitude dimensions")
    if lons.size != 720 or lats.size != 361:
        raise GEOSS2S3Error(f"{source} grid is {lons.size}x{lats.size}; expected NASA's 720x361 grid")
    if np.any(np.diff(lons) <= 0.0) or np.any(np.diff(lats) <= 0.0):
        raise GEOSS2S3Error(f"{source} coordinates are not strictly increasing")
    values = np.where(np.isfinite(values) & (np.abs(values) < 1.0e10), values, np.nan)
    if not np.any(np.isfinite(values)):
        raise GEOSS2S3Error(f"{source} contains no finite data")
    return Grid(lons.tolist(), lats.tolist(), values.tolist())


def _decode_forecasts(
    archive: Path,
    init: str,
    leads: Sequence[int],
    spec: dict[str, Any],
    work_dir: Path,
) -> dict[int, ForecastMonth]:
    try:
        import numpy as np
        import xarray as xr
    except ImportError as exc:  # pragma: no cover
        raise GEOSS2S3Error("NASA NetCDF decoding requires xarray, netCDF4, and numpy") from exc

    pattern = re.compile(
        rf"^{re.escape(init)}\.{re.escape(spec['archive_token'])}\.(\d{{8}})ens(\d+)\.(2|9)mo\.nc4$"
    )
    requested = sorted(set(leads))
    sums: dict[int, Any] = {}
    finite_counts: dict[int, Any] = {}
    source_files = {lead: [] for lead in requested}
    member_ids = {lead: [] for lead in requested}
    init_dates = {lead: [] for lead in requested}
    axes: tuple[Any, Any] | None = None

    try:
        archive_handle = tarfile.open(archive, mode="r:xz")
    except (OSError, tarfile.TarError) as exc:
        raise GEOSS2S3Error(f"could not read NASA archive {archive}: {exc}") from exc
    with archive_handle:
        parsed: list[tuple[tarfile.TarInfo, re.Match[str]]] = []
        for member in archive_handle.getmembers():
            if not member.isfile() or Path(member.name).name != member.name:
                continue
            match = pattern.fullmatch(member.name)
            if match:
                parsed.append((member, match))
        if len(parsed) != EXPECTED_TOTAL_MEMBERS:
            raise GEOSS2S3Error(
                f"NASA {init} {spec['archive_token']} archive has {len(parsed)} member files; expected {EXPECTED_TOTAL_MEMBERS}"
            )
        long_count = sum(1 for _, match in parsed if int(match.group(3)) == 9)
        if long_count != EXPECTED_LONG_RANGE_MEMBERS:
            raise GEOSS2S3Error(
                f"NASA {init} {spec['archive_token']} archive has {long_count} long-range members; expected {EXPECTED_LONG_RANGE_MEMBERS}"
            )
        expected_by_lead = {
            lead: sum(1 for _, match in parsed if int(match.group(3)) >= lead + 1)
            for lead in requested
        }
        work_dir.mkdir(parents=True, exist_ok=True)
        for member, match in parsed:
            horizon = int(match.group(3))
            member_leads = [lead for lead in requested if lead < horizon]
            if not member_leads:
                continue
            extracted = archive_handle.extractfile(member)
            if extracted is None:
                raise GEOSS2S3Error(f"could not extract {member.name} from {archive.name}")
            temporary_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".nc4", prefix="geos-member-", dir=work_dir, delete=False
                ) as temporary:
                    shutil.copyfileobj(extracted, temporary, length=1024 * 1024)
                    temporary_name = temporary.name
                with xr.open_dataset(temporary_name, engine="netcdf4", decode_times=True) as dataset:
                    array = _open_data_array(dataset, spec["forecast_variable"], spec, member.name)
                    if "time" not in array.dims:
                        raise GEOSS2S3Error(f"{member.name} has no monthly time dimension")
                    if int(array.sizes["time"]) != horizon:
                        raise GEOSS2S3Error(
                            f"{member.name} declares a {horizon}-month horizon but contains {array.sizes['time']} months"
                        )
                    for lead in member_leads:
                        expected_target = target_month(init, lead)
                        actual_target = _time_month(array["time"].values[lead])
                        if actual_target != expected_target:
                            raise GEOSS2S3Error(f"{member.name} lead {lead} is {actual_target}; expected {expected_target}")
                        grid = _grid_from_array(array.isel(time=lead).load(), f"{member.name} lead {lead}")
                        grid_values = np.asarray(grid.values, dtype=float)
                        if axes is None:
                            axes = (np.asarray(grid.lons, dtype=float), np.asarray(grid.lats, dtype=float))
                        elif not np.array_equal(axes[0], np.asarray(grid.lons, dtype=float)) or not np.array_equal(
                            axes[1], np.asarray(grid.lats, dtype=float)
                        ):
                            raise GEOSS2S3Error(f"{member.name} grid axes do not match the ensemble")
                        finite = np.isfinite(grid_values)
                        sums.setdefault(lead, np.zeros(grid_values.shape, dtype=float))[finite] += grid_values[finite]
                        finite_counts.setdefault(lead, np.zeros(grid_values.shape, dtype=int))[finite] += 1
                        source_files[lead].append(member.name)
                        member_ids[lead].append(f"{match.group(1)}-ens{int(match.group(2))}")
                        init_dates[lead].append(match.group(1))
            finally:
                extracted.close()
                if temporary_name:
                    Path(temporary_name).unlink(missing_ok=True)

    if axes is None:
        raise GEOSS2S3Error(f"NASA archive {archive.name} produced no requested forecast grids")
    result: dict[int, ForecastMonth] = {}
    for lead in requested:
        expected = expected_by_lead[lead]
        if len(source_files[lead]) != expected:
            raise GEOSS2S3Error(f"NASA {init} lead {lead} decoded {len(source_files[lead])}/{expected} expected members")
        counts = finite_counts[lead]
        mean_values = np.divide(
            sums[lead], counts, out=np.full(sums[lead].shape, np.nan, dtype=float), where=counts > 0
        )
        result[lead] = ForecastMonth(
            grid=Grid(axes[0].tolist(), axes[1].tolist(), mean_values.tolist()),
            target=target_month(init, lead),
            members=tuple(member_ids[lead]),
            expected_members=expected,
            source_files=tuple(source_files[lead]),
            init_dates=tuple(sorted(set(init_dates[lead]))),
        )
    return result


def _decode_drift(path: Path, target: str, spec: dict[str, Any]) -> tuple[Grid, tuple[int, ...]]:
    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover
        raise GEOSS2S3Error("NASA NetCDF decoding requires xarray and netCDF4") from exc
    with xr.open_dataset(path, engine="netcdf4", decode_times=True) as dataset:
        array = _open_data_array(dataset, spec["drift_variable"], spec, path.name)
        if "time" in array.dims:
            if int(array.sizes["time"]) != 1:
                raise GEOSS2S3Error(f"NASA drift file {path.name} must contain one target month")
            actual_month = _time_month(array["time"].values[0])[4:6]
            if actual_month != target[4:6]:
                raise GEOSS2S3Error(f"NASA drift file {path.name} represents month {actual_month}; expected {target[4:6]}")
            array = array.isel(time=0)
        grid = _grid_from_array(array.load(), path.name)
        history = "\n".join(str(dataset.attrs.get(name, "")) for name in ("history", "History"))
    years = tuple(sorted({int(value) for value in re.findall(r"ffsfile\.(\d{4})\.", history)}))
    return grid, years


def load_anomaly_bundle(
    *,
    product: str,
    init: str,
    leads: Sequence[int],
    cache_dir: Path,
    border_paths: Sequence[Path] = (),
    nrt_root: str = NASA_NRT_ROOT,
    drift_root: str = NASA_DRIFT_ROOT,
    request_delay: float = 0.0,
) -> dict[int, GEOSMonth]:
    if product not in PRODUCT_SPECS:
        raise GEOSS2S3Error(f"unsupported NASA product {product}")
    spec = PRODUCT_SPECS[product]
    source_url = archive_url(init, spec, nrt_root)
    source_path = archive_path(cache_dir, init, spec)
    _download(source_url, source_path, request_delay)
    forecasts = _decode_forecasts(source_path, init, leads, spec, cache_dir / "work")
    result: dict[int, GEOSMonth] = {}
    for lead, forecast in forecasts.items():
        baseline_url = drift_url(init, forecast.target, drift_root)
        baseline_path = drift_path(cache_dir, init, forecast.target)
        _download(baseline_url, baseline_path, request_delay)
        baseline, years = _decode_drift(baseline_path, forecast.target, spec)
        anomaly = prepare_product_grid(subtract_grids(forecast.grid, baseline), spec, forecast.target)
        prepared_forecast = prepare_product_grid(forecast.grid, spec, forecast.target)
        result[lead] = GEOSMonth(
            anomaly=anomaly,
            forecast=prepared_forecast,
            target=forecast.target,
            members=forecast.members,
            expected_members=forecast.expected_members,
            source_files=forecast.source_files,
            init_dates=forecast.init_dates,
            archive_url=source_url,
            drift_url=baseline_url,
            drift_years=years,
        )
    return result


def _initialization_range(init_dates: Sequence[str]) -> tuple[str, str, str]:
    parsed = sorted(dt.datetime.strptime(value, "%Y%m%d") for value in set(init_dates))
    if not parsed:
        raise GEOSS2S3Error("NASA member metadata contains no initialization dates")
    start, end = parsed[0], parsed[-1]
    label = (
        f"Init {start:%d}–{end:%d %b %Y} lagged"
        if start.year == end.year and start.month == end.month
        else f"Init {start:%d %b}–{end:%d %b %Y} lagged"
    )
    return (
        iso_utc(start.replace(tzinfo=dt.timezone.utc)),
        iso_utc((end + dt.timedelta(days=1)).replace(tzinfo=dt.timezone.utc)),
        label,
    )


def _baseline_metadata(months: Sequence[GEOSMonth]) -> dict[str, Any]:
    years = sorted({year for month in months for year in month.drift_years})
    label = DRIFT_LABEL + (f" ({years[0]}–{years[-1]})" if years else "")
    return {
        "status": "native_provider_drift",
        "source": label,
        "years": f"{years[0]}-{years[-1]}" if years else "provider supplied",
        "method": "lead- and initialization-month-matched NASA hindcast ensemble mean",
        "source_urls": [month.drift_url for month in months],
    }


def _target_entry(
    *,
    run_id: str,
    product: str,
    lead: int | str,
    target: str,
    period_label: str,
    months: Sequence[GEOSMonth],
    image: str | None,
    status: str,
) -> dict[str, Any]:
    spec = PRODUCT_SPECS[product]
    start_target, end_target = target.split("-")[0], target.split("-")[-1]
    valid_start, _ = target_period(start_target)
    _, valid_end = target_period(end_target)
    members = set(months[0].members)
    for month in months[1:]:
        members &= set(month.members)
    init_dates = sorted({value for month in months for value in month.init_dates})
    init_start, init_end, _ = _initialization_range(init_dates)
    entry: dict[str, Any] = {
        "id": f"{run_id}-{target}",
        "label": period_label,
        "target_month": target,
        "period_label": period_label,
        "valid_start_utc": valid_start,
        "valid_end_utc": valid_end,
        "lead_month": lead,
        "field": spec["field"],
        "units": spec["units"],
        "statistic": "NASA GEOS-S2S-3 lag/burst ensemble mean anomaly",
        "ensemble_members": len(members),
        "ensemble_expected_members": min(month.expected_members for month in months),
        "ensemble_scope": "long-range selected members" if min(month.expected_members for month in months) == 10 else "full lag/burst members",
        "initialization_start_utc": init_start,
        "initialization_end_utc": init_end,
        "source_archive_url": months[0].archive_url,
        "source_files": sorted({name for month in months for name in month.source_files}),
        "baseline": _baseline_metadata(months),
        "status": status,
    }
    if image:
        entry["image"] = image
    return entry


def write_manifest(path: Path, entries: Iterable[dict[str, Any]], previous: Path | None, retain_cycles: int) -> None:
    if retain_cycles < 1:
        raise GEOSS2S3Error("manifest retention must keep at least one release cycle")
    all_entries: list[dict[str, Any]] = []
    for existing_path in (previous, path):
        if not existing_path or not existing_path.exists():
            continue
        try:
            payload = json.loads(existing_path.read_text(encoding="utf-8"))
            all_entries.extend(
                run for run in payload.get("runs", [])
                if isinstance(run, dict) and not is_retired_product(run.get("product"))
            )
        except (OSError, ValueError) as exc:
            raise GEOSS2S3Error(f"could not read previous NASA manifest {existing_path}: {exc}") from exc
    all_entries.extend(
        run for run in entries
        if isinstance(run, dict) and not is_retired_product(run.get("product"))
    )
    unique = {str(run.get("id")): run for run in all_entries if run.get("id")}
    ordered = sorted(unique.values(), key=lambda item: (str(item.get("init_utc", "")), str(item.get("id", ""))), reverse=True)
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:retain_cycles])
    retained = [run for run in ordered if str(run.get("init_utc", "")) in keep]
    comparison_products = [
        PRODUCT_Z500_ANOMALY
        for _ in [0]
        if any(
            run.get("product") == PRODUCT_Z500_ANOMALY
            and any(target.get("image") for target in run.get("targets", []))
            for run in retained
        )
    ]
    payload = {
        "schema_version": 1,
        "kind": "geos_s2s3_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NASA GEOS-S2S-3 NCCS numerical forecast archive",
        "source_url": NASA_DATA_ROOT,
        "source_urls": [NASA_DATA_ROOT, NASA_NRT_ROOT, NASA_DRIFT_ROOT, NASA_PRIMER_URL, NASA_HISTORY_CONFIG_URL],
        "rendering": "numeric NASA NetCDF ensemble mean minus lead-matched provider drift climatology",
        "comparison_products": comparison_products,
        "product_labels": PRODUCT_LABELS,
        "retention": {"max_cycles": retain_cycles, "history_cycles": max(0, retain_cycles - 1)},
        "source_quality": {
            "z500": "strict 500-hPa coordinate validation; current 200-hPa APCN extraction is rejected",
        },
        "runs": retained,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="all", help="one product, a comma-separated list, or all validated products")
    parser.add_argument("--init", default="latest", help="NASA release as YYYYMM or latest")
    parser.add_argument("--lead-months", default="4,5,6", help="target offsets from the NASA release month")
    parser.add_argument("--seasonal-window", default="4,5,6", help="consecutive offsets for an additional seasonal map")
    parser.add_argument("--nrt-root", default=NASA_NRT_ROOT)
    parser.add_argument("--drift-root", default=NASA_DRIFT_ROOT)
    parser.add_argument("--cache-dir", default=".cache/geos-s2s3")
    parser.add_argument("--border-cache-dir", default=".cache/geos-s2s3")
    parser.add_argument("--output-dir", default="public/seasonal/geos_s2s3")
    parser.add_argument("--manifest", default="public/seasonal/geos_s2s3_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--request-delay", type=float, default=0.0)
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    return parser


def _resolve(value: str | Path, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    products = selected_products(args.product)
    init = parse_init(args.init, args.nrt_root)
    leads = parse_int_list(args.lead_months, "lead months")
    seasonal = parse_int_list(args.seasonal_window, "seasonal window") if args.seasonal_window else []
    if seasonal and seasonal != list(range(min(seasonal), max(seasonal) + 1)):
        raise GEOSS2S3Error("--seasonal-window must contain consecutive leads")
    leads = sorted(set(leads).union(seasonal))
    cache_dir = _resolve(args.cache_dir, root)
    border_cache = _resolve(args.border_cache_dir, root)
    output_dir = _resolve(args.output_dir, root)
    manifest_path = _resolve(args.manifest, root)
    previous = _resolve(args.previous_manifest, root) if args.previous_manifest else None
    borders = ensure_border_files(args, border_cache, root)
    entries: list[dict[str, Any]] = []
    usable_products = 0
    issue_utc = f"{init[:4]}-{init[4:]}-01T00:00:00Z"
    for product in products:
        spec = PRODUCT_SPECS[product]
        run_id = f"geos-s2s3-{init}-{product}"
        run_entry: dict[str, Any] = {
            "id": run_id,
            "init_utc": issue_utc,
            "model": "NASA GEOS-S2S-3",
            "product": product,
            "status": "planned",
            "source": "NASA GEOS-S2S-3 NCCS numerical forecast archive",
            "source_url": NASA_DATA_ROOT,
            "aggregation": "lag/burst ensemble mean minus lead-matched provider drift climatology",
            "output_dir": relative_path(output_dir, root),
            "targets": [],
        }
        try:
            bundle = load_anomaly_bundle(
                product=product,
                init=init,
                leads=leads,
                cache_dir=cache_dir,
                border_paths=borders,
                nrt_root=args.nrt_root,
                drift_root=args.drift_root,
                request_delay=args.request_delay,
            )
            all_dates = sorted({date for month in bundle.values() for date in month.init_dates})
            init_start, init_end, init_label = _initialization_range(all_dates)
            run_entry["initialization_start_utc"] = init_start
            run_entry["initialization_end_utc"] = init_end
            run_entry["ensemble_scope"] = "NASA 40-member lag/burst near term; 10 selected long-range members"
            for lead in leads:
                month = bundle[lead]
                period_label = dt.datetime.strptime(month.target, "%Y%m").strftime("%B %Y")
                output = output_dir / init / f"geos_s2s3_{spec['id_token']}_{month.target}.jpg"
                status = "decoded" if args.decode_only else "rendered"
                image = None
                if not args.decode_only:
                    render_map(
                        month.anomaly, f"{init}0100", month.target, lead, list(range(len(month.members))),
                        output, True, _baseline_metadata([month])["source"], borders,
                        period_label=period_label,
                        ensemble_label=(f"{len(month.members)}-member long-range mean" if len(month.members) == EXPECTED_LONG_RANGE_MEMBERS else f"{len(month.members)}-member lag/burst mean"),
                        initialization_label=init_label,
                        height_grid=month.forecast if spec["height_contours"] else None,
                        product_spec=spec,
                    )
                    image = relative_path(output, root)
                run_entry["targets"].append(
                    _target_entry(run_id=run_id, product=product, lead=lead, target=month.target, period_label=period_label, months=[month], image=image, status=status)
                )
            if seasonal:
                months = [bundle[lead] for lead in seasonal]
                member_sets = [set(month.members) for month in months]
                if any(member_set != member_sets[0] for member_set in member_sets[1:]):
                    raise GEOSS2S3Error("seasonal window crosses the 40-member/10-member horizon boundary; choose months with a consistent NASA member set")
                anomaly = sum_grids([month.anomaly for month in months]) if spec["seasonal_reducer"] == "sum" else mean_grids([month.anomaly for month in months])
                height = mean_grids([month.forecast for month in months]) if spec["height_contours"] else None
                target = f"{months[0].target}-{months[-1].target}"
                period_label = seasonal_period_label(months[0].target, months[-1].target)
                output = output_dir / init / f"geos_s2s3_{spec['id_token']}_{target}.jpg"
                status = "decoded" if args.decode_only else "rendered"
                image = None
                if not args.decode_only:
                    render_map(
                        anomaly, f"{init}0100", months[0].target, f"{seasonal[0]}–{seasonal[-1]}",
                        list(range(len(member_sets[0]))), output, True, _baseline_metadata(months)["source"], borders,
                        period_label=period_label,
                        ensemble_label=f"{len(member_sets[0])}-member long-range mean",
                        initialization_label=init_label,
                        height_grid=height,
                        product_spec=spec,
                    )
                    image = relative_path(output, root)
                run_entry["targets"].append(
                    _target_entry(run_id=run_id, product=product, lead=f"{seasonal[0]}-{seasonal[-1]}", target=target, period_label=period_label, months=months, image=image, status=status)
                )
            run_entry["status"] = "decoded" if args.decode_only else "rendered"
            usable_products += 1
            print(f"rendered NASA GEOS-S2S-3 {product}: {len(run_entry['targets'])} target(s)")
        except Exception as exc:
            run_entry["status"] = "failed"
            run_entry["error"] = str(exc)
            run_entry["targets"] = [
                {
                    "id": f"{run_id}-{target_month(init, lead)}",
                    "label": dt.datetime.strptime(target_month(init, lead), "%Y%m").strftime("%B %Y"),
                    "target_month": target_month(init, lead),
                    "lead_month": lead,
                    "field": spec["field"],
                    "units": spec["units"],
                    "status": "failed",
                    "error": str(exc),
                }
                for lead in leads
            ]
            print(f"NASA GEOS-S2S-3 {product} failed: {exc}", file=sys.stderr)
        entries.append(run_entry)

    write_manifest(manifest_path, entries, previous, args.retain_cycles)
    print(f"wrote NASA GEOS-S2S-3 manifest: {manifest_path} ({len(entries)} product run(s))")
    return 0 if usable_products else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except GEOSS2S3Error as exc:
        print(f"NASA GEOS-S2S-3 ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
