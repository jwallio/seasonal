#!/usr/bin/env python3
"""Render NOAA's experimental SFS beta2 seasonal anomaly guidance.

The NOAA SFS development archive publishes consolidated Zarr stores rather
than one file per member.  This adapter reads only the requested monthly
chunks over HTTPS, forms the 31-member beta2 forecast mean, subtracts the
same-calendar-month 35-year reforecast mean, and sends the result through the
shared seasonal renderer.

The archive is a development product.  Every field is checked against its
declared variable name, units, dimensions, member count, and global 0.5-degree
grid before it can be rendered.  Snowfall uses the native total-snow-
precipitation field as liquid-water equivalent, treats the archive values as
monthly mean daily accumulation, applies the calendar-month length, converts it
to inches of estimated snow depth at a fixed 10:1 ratio, and publishes that
converted quantity while retaining the source units in the provenance
metadata.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin

from cfsv2_seasonal import (
    CONUS_PRECIP_REGION,
    CONUS_REGION,
    DEFAULT_REGION,
    Grid,
    MSLP_ANOMALY_PALETTE,
    MSLP_ANOMALY_TICKS,
    PRECIP_ANOMALY_PALETTE,
    PRECIP_ANOMALY_TICKS,
    SNOWFALL_ANOMALY_MAX_IN,
    SNOWFALL_ANOMALY_MIN_IN,
    SNOWFALL_ANOMALY_PALETTE,
    SNOWFALL_ANOMALY_TICKS,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    ensure_border_files,
    render_map,
    relative_path,
    seasonal_period_label,
)
from height_display import HEIGHT_ANOMALY_STYLE, HEIGHT_NH_FRAME
from seasonal_products import grid_quality_control, is_retired_product, require_quality_control
from snowfall_display import DISPLAY as SNOWFALL_DISPLAY, RATIO as SNOW_TO_LIQUID_RATIO


SFS_BUCKET_ROOT = "https://noaa-oar-sfsdev-pds.s3.amazonaws.com/"
SFS_INDEX_URL = urljoin(SFS_BUCKET_ROOT, "index.html")
SFS_EXPERIMENT = "beta2"
SFS_EXPERIMENTS = ("beta2",)
SFS_FORECAST_DATASET = "atm_monthly.zarr"
SFS_REFORECAST_DATASET = "atm_monthly.zarr"
SFS_MAX_LEAD = 11
SFS_FORECAST_MEMBERS = 31
SFS_REFORECAST_MEMBERS = 11
SFS_MIN_REFORECAST_INITIALIZATIONS = 30
SFS_GRID_LON_COUNT = 720
SFS_GRID_LAT_COUNT = 361
SFS_DEFAULT_LEADS = (3, 4, 5)
SFS_DEFAULT_SEASONAL_WINDOW = (3, 4, 5)
SFS_DOWNLOAD_WORKERS = 8

PRODUCT_Z500_ANOMALY = "500mb_height_anomaly"
PRODUCT_Z500_ANOMALY_NH = "500mb_height_anomaly_nh"
PRODUCT_T850_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_T2M_ANOMALY = "2m_temperature_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_SNOWFALL_ANOMALY = "snowfall_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"


class SFSDataError(RuntimeError):
    """A source, schema, numerical, or rendering error with an actionable message."""


PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    PRODUCT_Z500_ANOMALY: {
        "name": PRODUCT_Z500_ANOMALY,
        "source_variable": "z500",
        "raw_field": "z500 / HGT_500mb",
        "raw_units": "m",
        "field": "z500_anomaly",
        "units": "m",
        "title": "NOAA SFS beta2 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "NOAA SFS beta2 500-mb Geopotential Height (m)",
        "height_contours": True,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "scheduled": True,
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam",
        **HEIGHT_ANOMALY_STYLE,
    },
    PRODUCT_T850_ANOMALY: {
        "name": PRODUCT_T850_ANOMALY,
        "source_variable": "t850",
        "raw_field": "t850 / TMP_850mb",
        "raw_units": "K",
        "field": "t850_anomaly",
        "units": "°C",
        "title": "NOAA SFS beta2 850-mb Temperature Anomaly (°C)",
        "absolute_title": "NOAA SFS beta2 850-mb Temperature (°C)",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "header_detail": "{source_label}  •  {baseline_label}  •  850-mb temperature anomaly (°C)",
        "scheduled": True,
    },
    PRODUCT_T2M_ANOMALY: {
        "name": PRODUCT_T2M_ANOMALY,
        "source_variable": "tmp2m",
        "raw_field": "tmp2m / TMP_2maboveground",
        "raw_units": "K",
        "field": "t2m_anomaly",
        "units": "°C",
        "title": "NOAA SFS beta2 2-m Temperature Anomaly (°C)",
        "absolute_title": "NOAA SFS beta2 2-m Temperature (°C)",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
        "scheduled": True,
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "source_variable": "pratesfc",
        "raw_field": "pratesfc / PRATE_surface",
        "raw_units": "kg/m^2/s",
        "field": "precipitation_anomaly",
        "units": "in",
        "title": "NOAA SFS beta2 Precipitation Anomaly (in)",
        "absolute_title": "NOAA SFS beta2 Precipitation (in)",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "seasonal_reducer": "sum",
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "Monthly PRATE multiplied by calendar-month seconds and converted from kg m-2 to inches",
        "anomaly_ticks": PRECIP_ANOMALY_TICKS,
        "anomaly_palette": PRECIP_ANOMALY_PALETTE,
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "header_detail": "{source_label}  •  {baseline_label}  •  Precipitation anomaly (in)  •  CONUS domain",
        "scheduled": True,
    },
    PRODUCT_SNOWFALL_ANOMALY: {
        "name": PRODUCT_SNOWFALL_ANOMALY,
        "source_variable": "tsnowpsfc",
        "raw_field": "tsnowpsfc / TSNOWP_surface",
        "raw_units": "kg/m^2",
        "field": "snowfall_depth_anomaly",
        "units": "in",
        "title": "NOAA SFS beta2 Snowfall Departure (in snow)",
        "absolute_title": "NOAA SFS beta2 Estimated Snowfall Depth (in snow)",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "seasonal_reducer": "sum",
        "conversion_kind": "snowfall_lwe_to_snow_depth_10_to_1",
        "conversion": "Native TSNOWP monthly-mean daily snow precipitation multiplied by calendar-month days, converted from kg m-2 to liquid-water-equivalent inches, then multiplied by 10.0; published snowfall departure is estimated snow depth in inches",
        "anomaly_min": SNOWFALL_ANOMALY_MIN_IN,
        "anomaly_max": SNOWFALL_ANOMALY_MAX_IN,
        "anomaly_ticks": SNOWFALL_ANOMALY_TICKS,
        "anomaly_palette": SNOWFALL_ANOMALY_PALETTE,
        "map_domain": "land",
        "mask_states": [
            "Alabama", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
            "Delaware", "Florida", "Georgia", "Idaho", "Illinois", "Indiana", "Iowa",
            "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
            "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
            "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
            "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
            "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
            "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
        ],
        "border_files": ("us-states.geojson",),
        "snowfall_input_kind": "Native NOAA SFS TSNOWP snowfall",
        "snowfall_values_are_depth": True,
        "source_accumulation": "TSNOWP monthly mean daily accumulation × calendar-month days",
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "scheduled": True,
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "source_variable": "prmsl",
        "raw_field": "prmsl / PRMSL_meansealevel",
        "raw_units": "Pa",
        "field": "mslp_anomaly",
        "units": "hPa",
        "title": "NOAA SFS beta2 Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "NOAA SFS beta2 Mean Sea-Level Pressure (hPa)",
        "height_contours": False,
        "region": CONUS_REGION,
        "seasonal_reducer": "mean",
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "PRMSL anomaly divided by 100 to convert Pa to hPa",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": MSLP_ANOMALY_TICKS,
        "anomaly_palette": MSLP_ANOMALY_PALETTE,
        "source_label": "NOAA SFS beta2 / SFS development archive",
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
        "scheduled": True,
    },
}

PRODUCT_SPECS[PRODUCT_Z500_ANOMALY_NH] = {
    **PRODUCT_SPECS[PRODUCT_Z500_ANOMALY],
    "name": PRODUCT_Z500_ANOMALY_NH,
    "scheduled": False,
    "region": (-180.0, 180.0, 0.0, 90.0),
    **HEIGHT_NH_FRAME,
    "title": "NOAA SFS beta2 Northern Hemisphere 500-mb Geopotential Height & Anomaly (m)",
    "absolute_title": "NOAA SFS beta2 Northern Hemisphere 500-mb Geopotential Height (m)",
    "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam  •  Northern Hemisphere",
}

DEFAULT_PRODUCTS = tuple(name for name, spec in PRODUCT_SPECS.items() if spec.get("scheduled"))
PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_Z500_ANOMALY_NH: "500-mb Height Anomaly · Northern Hemisphere",
    PRODUCT_T850_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_T2M_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "Precipitation Anomaly",
    PRODUCT_SNOWFALL_ANOMALY: "Snowfall Departure",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
}


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    start = dt.datetime.strptime(init, "%Y%m")
    year, month = month_after(start.year, start.month, lead)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m").replace(tzinfo=dt.timezone.utc)
    year, month = month_after(start.year, start.month, 1)
    end = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    return iso_utc(start), iso_utc(end)


def parse_leads(value: str, label: str) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            lead = int(item)
        except ValueError as exc:
            raise SFSDataError(f"invalid {label}: {item}") from exc
        if lead < 0 or lead > SFS_MAX_LEAD:
            raise SFSDataError(f"{label} must stay between 0 and {SFS_MAX_LEAD}")
        if lead not in result:
            result.append(lead)
    if not result:
        raise SFSDataError(f"{label} cannot be empty")
    return result


def parse_init(value: str, experiment: str = SFS_EXPERIMENT) -> str:
    if value == "latest":
        return discover_latest_init(experiment)
    if not re.fullmatch(r"\d{6}", value):
        raise SFSDataError("--init must be latest or YYYYMM")
    try:
        dt.datetime.strptime(value, "%Y%m")
    except ValueError as exc:
        raise SFSDataError(f"invalid SFS release month: {value}") from exc
    return value


def selected_products(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(DEFAULT_PRODUCTS)
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in PRODUCT_SPECS]
    if unknown:
        raise SFSDataError(f"unsupported NOAA SFS product(s): {', '.join(unknown)}")
    if not names:
        raise SFSDataError("--product cannot be empty")
    return list(dict.fromkeys(names))


def store_url(experiment: str, kind: str, init: str, dataset: str = SFS_FORECAST_DATASET) -> str:
    if experiment not in SFS_EXPERIMENTS:
        raise SFSDataError(f"unsupported NOAA SFS experiment: {experiment}")
    if kind not in {"forecast", "reforecast"}:
        raise SFSDataError(f"unsupported NOAA SFS store kind: {kind}")
    if kind == "forecast":
        path = f"experiments/{experiment}/forecast/{init}/{dataset}"
    else:
        path = f"experiments/{experiment}/reforecast/{init[4:6]}/{SFS_REFORECAST_DATASET}"
    return urljoin(SFS_BUCKET_ROOT, path)


def discover_latest_init(experiment: str = SFS_EXPERIMENT) -> str:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise SFSDataError("requests is required to discover the latest NOAA SFS release") from exc
    prefix = f"experiments/{experiment}/forecast/"
    url = f"{SFS_BUCKET_ROOT}?list-type=2&prefix={prefix}&delimiter=/&max-keys=1000"
    try:
        response = requests.get(url, timeout=(30, 120))
        response.raise_for_status()
    except Exception as exc:
        raise SFSDataError(f"could not read NOAA SFS release inventory: {exc}") from exc
    releases = sorted(set(re.findall(rf"<Prefix>{re.escape(prefix)}(\d{{6}})/</Prefix>", response.text)), reverse=True)
    if not releases:
        raise SFSDataError(f"NOAA SFS {experiment} inventory contains no YYYYMM forecast release")
    return releases[0]


def _request_bytes(url: str) -> bytes:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise SFSDataError("requests is required for NOAA SFS downloads") from exc
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=(30, 600))
            if response.status_code == 404:
                raise SFSDataError(f"NOAA SFS object is missing: {url}")
            response.raise_for_status()
            return response.content
        except SFSDataError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))
    raise SFSDataError(f"NOAA SFS download failed for {url}: {last_error}")


class HTTPZarrStore:
    """Small read-only consolidated-Zarr store with a persistent byte cache."""

    def __init__(self, base_url: str, cache_dir: Path):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir / "objects"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        metadata_url = f"{self.base_url}/.zmetadata"
        self.metadata = json.loads(self.get(".zmetadata").decode("utf-8")).get("metadata", {})
        if not isinstance(self.metadata, dict) or not self.metadata:
            raise SFSDataError(f"NOAA SFS store has no consolidated metadata: {metadata_url}")

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(f"{self.base_url}/{key}".encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / digest[2:]

    def get(self, key: str) -> bytes:
        destination = self._cache_path(key)
        if destination.is_file() and destination.stat().st_size > 0:
            return destination.read_bytes()
        url = f"{self.base_url}/{key.lstrip('/')}"
        payload = _request_bytes(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.tmp-{time.time_ns()}")
        temporary.write_bytes(payload)
        temporary.replace(destination)
        return payload

    def array(self, name: str) -> "ZarrArray":
        if f"{name}/.zarray" not in self.metadata:
            raise SFSDataError(f"NOAA SFS store is missing variable {name}")
        return ZarrArray(self, name)


@dataclass(frozen=True)
class ZarrArray:
    store: HTTPZarrStore
    name: str

    @property
    def zarray(self) -> dict[str, Any]:
        value = self.store.metadata.get(f"{self.name}/.zarray")
        if not isinstance(value, dict):
            raise SFSDataError(f"NOAA SFS variable {self.name} has invalid Zarr metadata")
        return value

    @property
    def attrs(self) -> dict[str, Any]:
        value = self.store.metadata.get(f"{self.name}/.zattrs", {})
        return value if isinstance(value, dict) else {}

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.zarray["shape"])

    @property
    def chunks(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.zarray["chunks"])

    def chunk_indices(self, indices: Sequence[int]) -> tuple[int, ...]:
        if len(indices) != len(self.shape):
            raise SFSDataError(f"NOAA SFS {self.name} index rank does not match its shape")
        return tuple(int(index) // chunk for index, chunk in zip(indices, self.chunks))

    def read_chunk_at(self, indices: Sequence[int]) -> Any:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise SFSDataError("numpy is required for NOAA SFS decoding") from exc
        chunk_indices = self.chunk_indices(indices)
        separator = str(self.zarray.get("dimension_separator", "."))
        chunk_key = separator.join(str(index) for index in chunk_indices)
        payload = self.store.get(f"{self.name}/{chunk_key}")
        compressor = self.zarray.get("compressor")
        if compressor:
            if compressor.get("id") != "blosc":
                raise SFSDataError(
                    f"NOAA SFS {self.name} uses unsupported compressor {compressor.get('id')!r}"
                )
            try:
                from numcodecs import Blosc
            except ImportError as exc:  # pragma: no cover
                raise SFSDataError("NOAA SFS decoding requires numcodecs") from exc
            payload = Blosc(
                cname=compressor.get("cname", "lz4"),
                clevel=int(compressor.get("clevel", 5)),
                shuffle=int(compressor.get("shuffle", 1)),
                blocksize=int(compressor.get("blocksize", 0)),
            ).decode(payload)
        actual_shape = tuple(
            min(int(chunk), int(size) - int(index) * int(chunk))
            for index, chunk, size in zip(chunk_indices, self.chunks, self.shape)
        )
        try:
            values = np.frombuffer(payload, dtype=np.dtype(self.zarray["dtype"])).reshape(
                actual_shape, order=str(self.zarray.get("order", "C"))
            )
        except (TypeError, ValueError) as exc:
            raise SFSDataError(f"could not decode NOAA SFS chunk {self.name}/{chunk_key}: {exc}") from exc
        values = np.array(values, dtype=float, copy=True)
        values[~np.isfinite(values) | (np.abs(values) >= 1.0e10)] = np.nan
        return values

    def read_coordinate(self) -> Any:
        if len(self.shape) != 1:
            raise SFSDataError(f"NOAA SFS coordinate {self.name} is not one-dimensional")
        return self.read_chunk_at((0,)).reshape(-1)


def _unit_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower().replace("^", ""))


def _units_match(product: str, units: Any) -> bool:
    token = _unit_token(units)
    if product in {PRODUCT_Z500_ANOMALY, PRODUCT_Z500_ANOMALY_NH, PRODUCT_T850_ANOMALY, PRODUCT_T2M_ANOMALY}:
        return token in {"m", "k"}
    if product == PRODUCT_MSLP_ANOMALY:
        return token == "pa"
    if product == PRODUCT_PRECIPITATION_ANOMALY:
        return token in {"kgm2s", "kgm2s1"}
    if product == PRODUCT_SNOWFALL_ANOMALY:
        return token in {"kgm2", "kgm2s", "kgm2s1"}
    return False


def _validate_field(array: ZarrArray, product: str, forecast: bool) -> None:
    spec = PRODUCT_SPECS[product]
    expected_dims = ("member", "lead", "lat", "lon") if forecast else ("init", "member", "lead", "lat", "lon")
    dimensions = tuple(array.attrs.get("_ARRAY_DIMENSIONS", ()))
    if dimensions != expected_dims:
        raise SFSDataError(f"NOAA SFS {array.name} dimensions are {dimensions}; expected {expected_dims}")
    if not _units_match(product, array.attrs.get("units")):
        raise SFSDataError(
            f"NOAA SFS {array.name} units are {array.attrs.get('units')!r}; expected the source units for {product}"
        )
    if forecast:
        if array.shape != (SFS_FORECAST_MEMBERS, 12, SFS_GRID_LAT_COUNT, SFS_GRID_LON_COUNT):
            raise SFSDataError(f"NOAA SFS forecast {array.name} shape is {array.shape}; expected 31x12x361x720")
    elif (
        len(array.shape) != 5
        or array.shape[1] != SFS_REFORECAST_MEMBERS
        or array.shape[2:] != (12, SFS_GRID_LAT_COUNT, SFS_GRID_LON_COUNT)
        or array.shape[0] < SFS_MIN_REFORECAST_INITIALIZATIONS
    ):
        raise SFSDataError(
            f"NOAA SFS reforecast {array.name} shape is {array.shape}; expected at least 30x11x12x361x720"
        )
    level = str(array.attrs.get("level", "")).lower()
    short_name = str(array.attrs.get("short_name", "")).lower()
    if product in {PRODUCT_Z500_ANOMALY, PRODUCT_Z500_ANOMALY_NH} and "500" not in f"{level} {short_name}":
        raise SFSDataError(f"NOAA SFS {array.name} does not identify a 500-mb field")
    if product == PRODUCT_T850_ANOMALY and "850" not in f"{level} {short_name}":
        raise SFSDataError(f"NOAA SFS {array.name} does not identify an 850-mb field")
    if product == PRODUCT_T2M_ANOMALY and "2m" not in f"{level} {short_name}":
        raise SFSDataError(f"NOAA SFS {array.name} does not identify a 2-m field")
    if product == PRODUCT_MSLP_ANOMALY and "mean sea level" not in level and "prmsl" not in short_name:
        raise SFSDataError(f"NOAA SFS {array.name} does not identify a mean-sea-level pressure field")


def _validate_coordinates(forecast: HTTPZarrStore, reforecast: HTTPZarrStore) -> tuple[list[float], list[float], dict[int, int]]:
    import numpy as np

    forecast_lats = forecast.array("lat").read_coordinate()
    forecast_lons = forecast.array("lon").read_coordinate()
    ref_lats = reforecast.array("lat").read_coordinate()
    ref_lons = reforecast.array("lon").read_coordinate()
    if forecast_lats.shape != (SFS_GRID_LAT_COUNT,) or forecast_lons.shape != (SFS_GRID_LON_COUNT,):
        raise SFSDataError("NOAA SFS forecast coordinates are not the expected 0.5-degree global grid")
    if not np.array_equal(forecast_lats, ref_lats) or not np.array_equal(forecast_lons, ref_lons):
        raise SFSDataError("NOAA SFS forecast and reforecast coordinates do not match")
    if np.any(np.diff(forecast_lats) <= 0.0) or np.any(np.diff(forecast_lons) <= 0.0):
        raise SFSDataError("NOAA SFS coordinates are not strictly increasing")
    lead_values = forecast.array("lead").read_coordinate()
    ref_lead_values = reforecast.array("lead").read_coordinate()
    if not np.array_equal(lead_values, ref_lead_values) or list(map(int, lead_values)) != list(range(12)):
        raise SFSDataError("NOAA SFS monthly lead coordinate is not 0 through 11")
    if list(map(int, forecast.array("member").read_coordinate())) != list(range(SFS_FORECAST_MEMBERS)):
        raise SFSDataError("NOAA SFS forecast member coordinate is not the expected 31-member ensemble")
    if list(map(int, reforecast.array("member").read_coordinate())) != list(range(SFS_REFORECAST_MEMBERS)):
        raise SFSDataError("NOAA SFS reforecast member coordinate is not the expected 11-member ensemble")
    return forecast_lons.tolist(), forecast_lats.tolist(), {int(value): index for index, value in enumerate(lead_values)}


def _finite_mean(values: Any, axis: int | tuple[int, ...]) -> Any:
    import numpy as np

    finite = np.isfinite(values)
    count = np.sum(finite, axis=axis)
    total = np.nansum(values, axis=axis)
    return np.divide(total, count, out=np.full(total.shape, np.nan, dtype=float), where=count > 0)


def _read_forecast_mean(array: ZarrArray, lead_index: int) -> Any:
    chunk = array.read_chunk_at((0, lead_index, 0, 0))
    return _finite_mean(chunk, axis=0)[0]


def _read_reforecast_mean(array: ZarrArray, lead_index: int, workers: int) -> Any:
    import numpy as np

    keys = [(init, 0, lead_index, 0, 0) for init in range(array.shape[0])]

    def read(key: tuple[int, ...]) -> Any:
        return array.read_chunk_at(key)[0, :, 0, :, :]

    total = np.zeros((SFS_GRID_LAT_COUNT, SFS_GRID_LON_COUNT), dtype=float)
    count = np.zeros(total.shape, dtype=np.int32)
    worker_count = max(1, int(workers))
    # Source chunks are about 28 MiB each after decoding. Consume small
    # batches so a 35-initialization baseline never becomes a huge list of
    # pending arrays.
    batch_size = worker_count
    for start in range(0, len(keys), batch_size):
        batch = keys[start : start + batch_size]
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for chunk in executor.map(read, batch):
                finite = np.isfinite(chunk)
                total += np.nansum(chunk, axis=0)
                count += np.sum(finite, axis=0, dtype=np.int32)
    return np.divide(total, count, out=np.full(total.shape, np.nan, dtype=float), where=count > 0)


def _month_seconds(target: str) -> float:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return (dt.datetime(year, month, 1) - start).total_seconds()


def _month_days(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return (dt.datetime(year, month, 1) - start).days


def _convert_anomaly(values: Any, product: str, target: str) -> Any:
    if product == PRODUCT_PRECIPITATION_ANOMALY:
        return values * _month_seconds(target) / 25.4
    if product == PRODUCT_SNOWFALL_ANOMALY:
        # TSNOWP is published in kg m-2 but the monthly archive value behaves
        # as a mean daily accumulation. Convert each calendar month's mean
        # daily liquid-water equivalent to a monthly total before applying the
        # fixed 10:1 estimated snow-depth ratio.
        return values * _month_days(target) / 25.4 * SNOW_TO_LIQUID_RATIO
    if product == PRODUCT_MSLP_ANOMALY:
        return values / 100.0
    return values


def _grid(lons: Sequence[float], lats: Sequence[float], values: Any) -> Grid:
    return Grid(list(map(float, lons)), list(map(float, lats)), values.tolist())


def _qc_values(grid: Grid, product: str) -> Any:
    import numpy as np

    spec = PRODUCT_SPECS[product]
    lon_min, lon_max, lat_min, lat_max = spec.get("region", DEFAULT_REGION)
    lons = np.asarray(grid.lons, dtype=float)
    lats = np.asarray(grid.lats, dtype=float)
    values = np.asarray(grid.values, dtype=float)
    signed_lons = ((lons + 180.0) % 360.0) - 180.0
    lon_mask = (signed_lons >= lon_min) & (signed_lons <= lon_max)
    lat_mask = (lats >= lat_min) & (lats <= lat_max)
    selected = values[np.ix_(lat_mask, lon_mask)]
    return selected if selected.size else values


@dataclass(frozen=True)
class MonthlyField:
    target: str
    anomaly: Grid
    forecast: Grid
    source_files: tuple[str, ...]
    baseline_years: tuple[int, ...]


def _reforecast_years(store: HTTPZarrStore) -> tuple[int, ...]:
    attrs = store.array("init").attrs
    units = str(attrs.get("units", ""))
    match = re.match(r"days since (\d{4})-(\d{2})-(\d{2})", units)
    if not match:
        raise SFSDataError(f"NOAA SFS reforecast init coordinate has unsupported units: {units!r}")
    origin = dt.datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    values = store.array("init").read_coordinate()
    dates = tuple(origin + dt.timedelta(days=int(value)) for value in values)
    if len(dates) < SFS_MIN_REFORECAST_INITIALIZATIONS:
        raise SFSDataError("NOAA SFS reforecast has too few historical initializations")
    if len({date.month for date in dates}) != 1:
        raise SFSDataError("NOAA SFS reforecast store is not a single initialization month")
    return tuple(sorted({date.year for date in dates}))


def load_product_fields(
    *,
    product: str,
    init: str,
    leads: Sequence[int],
    forecast_store: HTTPZarrStore,
    reforecast_store: HTTPZarrStore,
    lons: Sequence[float],
    lats: Sequence[float],
    lead_indices: dict[int, int],
    workers: int,
) -> dict[int, MonthlyField]:
    spec = PRODUCT_SPECS[product]
    forecast_array = forecast_store.array(spec["source_variable"])
    reforecast_array = reforecast_store.array(spec["source_variable"])
    _validate_field(forecast_array, product, True)
    _validate_field(reforecast_array, product, False)
    baseline_years = _reforecast_years(reforecast_store)
    forecast_url = f"{forecast_store.base_url}/{spec['source_variable']}"
    reforecast_url = f"{reforecast_store.base_url}/{spec['source_variable']}"
    result: dict[int, MonthlyField] = {}
    for lead in sorted(set(leads)):
        if lead not in lead_indices:
            raise SFSDataError(f"NOAA SFS does not contain requested lead {lead}")
        lead_index = lead_indices[lead]
        target = target_month(init, lead)
        forecast_raw = _read_forecast_mean(forecast_array, lead_index)
        baseline_raw = _read_reforecast_mean(reforecast_array, lead_index, workers)
        anomaly_values = _convert_anomaly(forecast_raw - baseline_raw, product, target)
        forecast_values = _convert_anomaly(forecast_raw, product, target) if product != PRODUCT_Z500_ANOMALY and product != PRODUCT_Z500_ANOMALY_NH else forecast_raw
        anomaly = _grid(lons, lats, anomaly_values)
        forecast = _grid(lons, lats, forecast_values)
        if not any(math.isfinite(value) for row in anomaly.values for value in row):
            raise SFSDataError(f"NOAA SFS {product} lead {lead} contains no finite anomaly values")
        result[lead] = MonthlyField(
            target=target,
            anomaly=anomaly,
            forecast=forecast,
            source_files=(forecast_url, reforecast_url),
            baseline_years=baseline_years,
        )
    return result


def _aggregate_fields(fields: Sequence[MonthlyField], product: str) -> MonthlyField:
    if not fields:
        raise SFSDataError("cannot aggregate an empty NOAA SFS seasonal window")
    import numpy as np

    first = fields[0]
    anomaly_arrays = [np.asarray(field.anomaly.values, dtype=float) for field in fields]
    forecast_arrays = [np.asarray(field.forecast.values, dtype=float) for field in fields]
    reducer = PRODUCT_SPECS[product].get("seasonal_reducer", "mean")
    anomaly_values = np.nansum(anomaly_arrays, axis=0) if reducer == "sum" else _finite_mean(np.stack(anomaly_arrays), axis=0)
    forecast_values = _finite_mean(np.stack(forecast_arrays), axis=0)
    return MonthlyField(
        target=f"{first.target}-{fields[-1].target}",
        anomaly=_grid(first.anomaly.lons, first.anomaly.lats, anomaly_values),
        forecast=_grid(first.forecast.lons, first.forecast.lats, forecast_values),
        source_files=tuple(sorted({url for field in fields for url in field.source_files})),
        baseline_years=tuple(sorted({year for field in fields for year in field.baseline_years})),
    )


def _initialization_label(init: str) -> str:
    value = dt.datetime.strptime(init, "%Y%m")
    return f"Init {value:%d %b %Y} 00Z"


def _baseline_label(fields: Sequence[MonthlyField], experiment: str = SFS_EXPERIMENT) -> str:
    years = sorted({year for field in fields for year in field.baseline_years})
    suffix = f" ({years[0]}–{years[-1]})" if years else ""
    return f"NOAA SFS {experiment} same-calendar-month reforecast mean{suffix}"


def _target_entry(
    *,
    run_id: str,
    product: str,
    lead: int | str,
    field: MonthlyField,
    image: str | None,
    status: str,
    seasonal: bool,
    experiment: str = SFS_EXPERIMENT,
) -> dict[str, Any]:
    spec = PRODUCT_SPECS[product]
    first_target = field.target.split("-")[0]
    last_target = field.target.split("-")[-1]
    valid_start, _ = target_period(first_target)
    _, valid_end = target_period(last_target)
    qc = grid_quality_control(
        product,
        _qc_values(field.anomaly, product),
        units=spec["units"],
        field=spec["field"],
        seasonal=seasonal,
    )
    require_quality_control(qc, SFSDataError)
    entry: dict[str, Any] = {
        "id": f"{run_id}-{field.target}",
        "label": (
            seasonal_period_label(first_target, last_target)
            if seasonal
            else dt.datetime.strptime(first_target, "%Y%m").strftime("%B %Y")
        ),
        "target_month": field.target,
        "period_label": (
            seasonal_period_label(first_target, last_target)
            if seasonal
            else dt.datetime.strptime(first_target, "%Y%m").strftime("%B %Y")
        ),
        "valid_start_utc": valid_start,
        "valid_end_utc": valid_end,
        "lead_month": lead,
        "field": spec["field"],
        "units": spec["units"],
        "statistic": f"NOAA SFS {experiment} 31-member ensemble mean anomaly",
        "aggregation": "Forecast mean minus same-calendar-month reforecast mean",
        "ensemble_members": SFS_FORECAST_MEMBERS,
        "ensemble_expected_members": SFS_FORECAST_MEMBERS,
        "ensemble_complete": True,
        "ensemble_label": f"{SFS_FORECAST_MEMBERS}-member NOAA SFS {experiment} mean",
        "ensemble_scope": f"NOAA SFS {experiment} forecast members",
        "source_files": list(field.source_files),
        "baseline": {
            "status": "same_calendar_month_reforecast_mean",
            "source": _baseline_label([field], experiment),
            "years": f"{min(field.baseline_years)}-{max(field.baseline_years)}" if field.baseline_years else "provider supplied",
            "method": "35 same-month initializations × 11 reforecast members, lead matched to the forecast",
            "source_urls": [field.source_files[-1]],
        },
        "quality_control": qc,
        "status": status,
    }
    if product == PRODUCT_SNOWFALL_ANOMALY:
        entry["display"] = dict(SNOWFALL_DISPLAY)
    if spec.get("conversion"):
        entry["derivation"] = {"method": spec["conversion_kind"], "description": spec["conversion"]}
    if image:
        entry["image"] = image
    return entry


def write_manifest(
    path: Path,
    entries: Iterable[dict[str, Any]],
    previous: Path | None,
    retain_cycles: int,
    experiment: str = SFS_EXPERIMENT,
) -> None:
    if retain_cycles < 1:
        raise SFSDataError("manifest retention must keep at least one release cycle")
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
            raise SFSDataError(f"could not read previous NOAA SFS manifest {existing_path}: {exc}") from exc
    all_entries.extend(
        run for run in entries
        if isinstance(run, dict) and not is_retired_product(run.get("product"))
    )
    unique = {str(run.get("id")): run for run in all_entries if run.get("id")}
    ordered = sorted(
        unique.values(),
        key=lambda item: (str(item.get("init_utc", "")), str(item.get("id", ""))),
        reverse=True,
    )
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:retain_cycles])
    retained = [run for run in ordered if str(run.get("init_utc", "")) in keep]
    comparison_products = [product for product in DEFAULT_PRODUCTS if product != PRODUCT_Z500_ANOMALY_NH]
    payload = {
        "schema_version": 1,
        "kind": "noaa_sfs_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": f"NOAA SFS {experiment} development archive",
        "source_url": SFS_INDEX_URL,
        "source_urls": [SFS_INDEX_URL, SFS_BUCKET_ROOT],
        "experiment": experiment,
        "rendering": f"31-member NOAA SFS {experiment} mean minus same-calendar-month 35-year reforecast mean; fixed discrete map bands",
        "comparison_products": comparison_products,
        "product_labels": PRODUCT_LABELS,
        "retention": {"max_cycles": retain_cycles, "history_cycles": max(0, retain_cycles - 1)},
        "source_quality": {
            "forecast_store": f"31-member 0.5-degree global {SFS_FORECAST_DATASET}",
            "reforecast_store": "same-calendar-month 11-member reforecast climatology with at least 30 initializations",
            "snowfall": "native TSNOWP_surface monthly mean daily accumulation multiplied by calendar-month days, then converted from LWE to estimated snow-depth inches at a fixed 10:1 ratio",
            "map_bands": "discrete BoundaryNorm intervals; no smooth interpolation between palette colors",
        },
        "runs": retained,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _failed_target(init: str, run_id: str, product: str, lead: int | str, error: str) -> dict[str, Any]:
    target = str(lead)
    if isinstance(lead, int):
        target = target_month(init, lead)
    else:
        parts = target.split("-")
        target = f"{target_month(init, int(parts[0]))}-{target_month(init, int(parts[-1]))}"
    first_target = target.split("-")[0]
    last_target = target.split("-")[-1]
    valid_start, _ = target_period(first_target)
    _, valid_end = target_period(last_target)
    spec = PRODUCT_SPECS[product]
    return {
        "id": f"{run_id}-{target}",
        "label": seasonal_period_label(first_target, last_target) if "-" in target else dt.datetime.strptime(first_target, "%Y%m").strftime("%B %Y"),
        "target_month": target,
        "valid_start_utc": valid_start,
        "valid_end_utc": valid_end,
        "lead_month": lead,
        "field": spec["field"],
        "units": spec["units"],
        "status": "failed",
        "error": error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="all", help="one product, a comma-separated list, or all validated products")
    parser.add_argument("--init", default="latest", help="NOAA SFS release as YYYYMM or latest")
    parser.add_argument("--experiment", default=SFS_EXPERIMENT, choices=SFS_EXPERIMENTS)
    parser.add_argument("--lead-months", default=",".join(map(str, SFS_DEFAULT_LEADS)), help="target offsets from the release month")
    parser.add_argument("--seasonal-window", default=",".join(map(str, SFS_DEFAULT_SEASONAL_WINDOW)), help="consecutive offsets for the seasonal aggregate")
    parser.add_argument("--cache-dir", default=".cache/sfs")
    parser.add_argument("--border-cache-dir", default=".cache/sfs")
    parser.add_argument("--output-dir", default="public/seasonal/sfs")
    parser.add_argument("--manifest", default="public/seasonal/sfs_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--download-workers", type=int, default=SFS_DOWNLOAD_WORKERS)
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
    init = parse_init(args.init, args.experiment)
    leads = parse_leads(args.lead_months, "lead months")
    seasonal = parse_leads(args.seasonal_window, "seasonal window") if args.seasonal_window else []
    if seasonal and seasonal != list(range(min(seasonal), max(seasonal) + 1)):
        raise SFSDataError("--seasonal-window must contain consecutive leads")
    leads = sorted(set(leads).union(seasonal))
    if args.download_workers < 1:
        raise SFSDataError("--download-workers must be at least one")
    cache_dir = _resolve(args.cache_dir, root)
    border_cache = _resolve(args.border_cache_dir, root)
    output_dir = _resolve(args.output_dir, root)
    manifest_path = _resolve(args.manifest, root)
    previous = _resolve(args.previous_manifest, root) if args.previous_manifest else None
    borders = ensure_border_files(args, border_cache, root) if not args.decode_only else []
    forecast_url = store_url(args.experiment, "forecast", init)
    reforecast_url = store_url(args.experiment, "reforecast", init)
    entries: list[dict[str, Any]] = []
    usable_products = 0
    issue_utc = f"{init[:4]}-{init[4:]}-01T00:00:00Z"
    try:
        forecast_store = HTTPZarrStore(forecast_url, cache_dir / "forecast" / init)
        reforecast_store = HTTPZarrStore(reforecast_url, cache_dir / "reforecast" / init[4:6])
        lons, lats, lead_indices = _validate_coordinates(forecast_store, reforecast_store)
    except Exception as exc:
        error = str(exc)
        for product in products:
            run_id = f"sfs-{args.experiment}-{init}-{product}"
            entries.append({
                "id": run_id,
                "init_utc": issue_utc,
                "model": f"NOAA SFS {args.experiment}",
                "product": product,
                "status": "failed",
                "source": f"NOAA SFS {args.experiment} development archive",
                "source_url": SFS_INDEX_URL,
                "output_dir": relative_path(output_dir, root),
                "targets": [_failed_target(init, run_id, product, lead, error) for lead in leads],
                "error": error,
            })
        write_manifest(manifest_path, entries, previous, args.retain_cycles, args.experiment)
        print(f"NOAA SFS source failed: {error}", file=sys.stderr)
        return 2

    for product in products:
        spec = PRODUCT_SPECS[product]
        run_id = f"sfs-{args.experiment}-{init}-{product}"
        run_entry: dict[str, Any] = {
            "id": run_id,
            "init_utc": issue_utc,
            "model": f"NOAA SFS {args.experiment}",
            "product": product,
            "status": "planned",
            "source": f"NOAA SFS {args.experiment} development archive",
            "source_url": SFS_INDEX_URL,
            "source_urls": [forecast_url, reforecast_url],
            "aggregation": "31-member forecast mean minus same-calendar-month reforecast mean",
            "output_dir": relative_path(output_dir, root),
            "targets": [],
            "ensemble_members": SFS_FORECAST_MEMBERS,
            "ensemble_expected_members": SFS_FORECAST_MEMBERS,
            "ensemble_scope": f"NOAA SFS {args.experiment} 31-member forecast",
            "raw_field": spec["raw_field"],
            "raw_units": spec["raw_units"],
            "field": spec["field"],
            "units": spec["units"],
            "conversion": spec.get("conversion", "Forecast-minus-reforecast anomaly in native source units"),
            "source_warning": f"NOAA SFS {args.experiment} is an experimental development archive; source fields are schema-validated before publication.",
        }
        if product == PRODUCT_SNOWFALL_ANOMALY:
            run_entry["display"] = dict(SNOWFALL_DISPLAY)
        try:
            fields = load_product_fields(
                product=product,
                init=init,
                leads=leads,
                forecast_store=forecast_store,
                reforecast_store=reforecast_store,
                lons=lons,
                lats=lats,
                lead_indices=lead_indices,
                workers=args.download_workers,
            )
            init_label = _initialization_label(init)
            baseline_label = _baseline_label(list(fields.values()), args.experiment)
            for lead in leads:
                field = fields[lead]
                status = "decoded" if args.decode_only else "rendered"
                image = None
                if not args.decode_only:
                    output = output_dir / init / f"sfs_{spec['name']}_{field.target}.jpg"
                    render_map(
                        field.anomaly,
                        f"{init}0100",
                        field.target,
                        lead,
                        list(range(SFS_FORECAST_MEMBERS)),
                        output,
                        True,
                        baseline_label,
                        borders,
                        period_label=dt.datetime.strptime(field.target, "%Y%m").strftime("%B %Y"),
                        ensemble_label=f"{SFS_FORECAST_MEMBERS}-member {args.experiment} mean",
                        height_grid=field.forecast if spec["height_contours"] else None,
                        product_spec=spec,
                        initialization_label=init_label,
                    )
                    image = relative_path(output, root)
                run_entry["targets"].append(
                    _target_entry(
                        run_id=run_id,
                        product=product,
                        lead=lead,
                        field=field,
                        image=image,
                        status=status,
                        seasonal=False,
                        experiment=args.experiment,
                    )
                )
            if seasonal:
                seasonal_fields = [fields[lead] for lead in seasonal]
                field = _aggregate_fields(seasonal_fields, product)
                status = "decoded" if args.decode_only else "rendered"
                image = None
                if not args.decode_only:
                    output = output_dir / init / f"sfs_{spec['name']}_{field.target}.jpg"
                    render_map(
                        field.anomaly,
                        f"{init}0100",
                        seasonal_fields[0].target,
                        f"{seasonal[0]}–{seasonal[-1]}",
                        list(range(SFS_FORECAST_MEMBERS)),
                        output,
                        True,
                        baseline_label,
                        borders,
                        period_label=seasonal_period_label(seasonal_fields[0].target, seasonal_fields[-1].target),
                        seasonal=True,
                        ensemble_label=f"{SFS_FORECAST_MEMBERS}-member {args.experiment} mean",
                        height_grid=field.forecast if spec["height_contours"] else None,
                        product_spec=spec,
                        initialization_label=init_label,
                    )
                    image = relative_path(output, root)
                run_entry["targets"].append(
                    _target_entry(
                        run_id=run_id,
                        product=product,
                        lead=f"{seasonal[0]}-{seasonal[-1]}",
                        field=field,
                        image=image,
                        status=status,
                        seasonal=True,
                        experiment=args.experiment,
                    )
                )
            run_entry["status"] = "decoded" if args.decode_only else "rendered"
            run_entry["initialization_start_utc"] = issue_utc
            run_entry["initialization_end_utc"] = f"{init[:4]}-{init[4:]}-02T00:00:00Z"
            usable_products += 1
            print(f"rendered NOAA SFS {args.experiment} {product}: {len(run_entry['targets'])} target(s)")
        except Exception as exc:
            run_entry["status"] = "failed"
            run_entry["error"] = str(exc)
            run_entry["targets"] = [
                _failed_target(init, run_id, product, lead, str(exc))
                for lead in leads
            ]
            print(f"NOAA SFS {args.experiment} {product} failed: {exc}", file=sys.stderr)
        entries.append(run_entry)

    write_manifest(manifest_path, entries, previous, args.retain_cycles, args.experiment)
    print(f"wrote NOAA SFS manifest: {manifest_path} ({len(entries)} product run(s))")
    return 0 if usable_products else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except SFSDataError as exc:
        print(f"NOAA SFS ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
