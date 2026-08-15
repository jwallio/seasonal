#!/usr/bin/env python3
"""Fetch and render current ECMWF SEAS5 seasonal products through the CDS API.

The Copernicus Climate Data Store publishes the current ECMWF/System 51
monthly ensemble-mean anomalies at 1-degree resolution.  This adapter keeps
the source and nominal initialization explicit, requests only the selected
lead months and North American area, and shares WN2's operational map
renderer and static manifest contract with the CFSv2 viewer without treating
the two models as the same source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
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


# The CDS catalogue currently identifies ECMWF SEAS5 as originating centre
# ``ecmwf`` and system ``51``.  The postprocessed datasets contain the official
# monthly anomaly fields; the monthly statistics dataset supplies the raw
# geopotential field used only for 500-mb contour lines.
CDS_API_ROOT = "https://cds.climate.copernicus.eu/api"
CDS_PRESSURE_ANOMALY_DATASET = "seasonal-postprocessed-pressure-levels"
CDS_SINGLE_ANOMALY_DATASET = "seasonal-postprocessed-single-levels"
CDS_PRESSURE_MONTHLY_DATASET = "seasonal-monthly-pressure-levels"
CDS_ORIGINATING_CENTRE = "ecmwf"
CDS_SYSTEM = "51"
CDS_ECMWF_RELEASE_DAY = 6
CDS_ECMWF_RELEASE_HOUR = 12
CDS_NORTH_AMERICA_AREA = [90.0, -170.0, 15.0, 0.0]
CDS_CONUS_AREA = [60.0, -135.0, 20.0, -55.0]
CDS_ENSEMBLE_MEMBERS = 51
HINDCAST_START = 1981
HINDCAST_END = 2016
GEOPOTENTIAL_GRAVITY = 9.80665
M_TO_INCH = 1000.0 / 25.4
SOURCE_LABEL = "ECMWF SEAS5 / Copernicus CDS"
SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels"
CDS_LICENSE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels?tab=download#manage-licences"

Z500_ANOMALY = "500mb_height_anomaly"
T2M_ANOMALY = "2m_temperature_anomaly"
T850_ANOMALY = "850mb_temperature_anomaly"
PRECIP_ANOMALY = "precipitation_anomaly"
SNOWFALL_ANOMALY = "snowfall_anomaly"
SNOW_DEPTH_ANOMALY = "snow_depth_anomaly"
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
SEAS5_PRECIP_ANOMALY_PALETTE = [
    "#6b2d05",
    "#7f3608",
    "#93430d",
    "#a95213",
    "#bc661b",
    "#cb7929",
    "#d88f42",
    "#e1aa67",
    "#ffffff",
    "#d9efd2",
    "#bfe4b6",
    "#9bd694",
    "#74c476",
    "#41ab5d",
    "#238b45",
    "#006d2c",
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
        "cds_dataset": CDS_PRESSURE_ANOMALY_DATASET,
        "cds_variable": "geopotential_anomaly",
        "cds_pressure_level": "500",
        "cds_raw_dataset": CDS_PRESSURE_MONTHLY_DATASET,
        "cds_raw_variable": "geopotential",
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
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "2m_temperature_anomaly",
    },
    T850_ANOMALY: {
        "name": T850_ANOMALY,
        "variable": "t850",
        "field": "t850_anomaly",
        "raw_field": "t850 / temperature",
        "raw_units": "K",
        "units": "°C",
        "seasonal_units": "°C",
        "title": "SEAS5 850-mb Temperature Anomaly (°C)",
        "absolute_title": "SEAS5 850-mb Temperature (°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -6.0,
        "anomaly_max": 6.0,
        "anomaly_ticks": [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  •  {baseline_label}  •  850-mb temperature anomaly (°C)",
        "cds_dataset": CDS_PRESSURE_ANOMALY_DATASET,
        "cds_variable": "temperature_anomaly",
        "cds_pressure_level": "850",
    },
    PRECIP_ANOMALY: {
        "name": PRECIP_ANOMALY,
        "variable": "pr",
        "field": "precipitation_anomaly",
        "raw_field": "pr / total precipitation",
        "raw_units": "m s**-1",
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
        "anomaly_palette": SEAS5_PRECIP_ANOMALY_PALETTE,
        "conversion": "CDS anomalous water rate multiplied by target-month seconds and converted from metres to inches",
        "header_detail": "{source_label}  •  {baseline_label}  •  Precipitation accumulation (in)  •  CONUS domain",
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "total_precipitation_anomalous_rate_of_accumulation",
    },
    SNOWFALL_ANOMALY: {
        "name": SNOWFALL_ANOMALY,
        "variable": "sf",
        "field": "snowfall_anomaly",
        "raw_field": "sf / snowfall",
        "raw_units": "m s**-1",
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
        "conversion": "CDS anomalous snowfall water rate multiplied by target-month seconds and converted from metres to inches",
        "header_detail": "{source_label}  •  {baseline_label}  •  Snowfall liquid-water equivalent (in)  •  CONUS domain",
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "snowfall_anomalous_rate_of_accumulation",
    },
    SNOW_DEPTH_ANOMALY: {
        "name": SNOW_DEPTH_ANOMALY,
        "variable": "snow_depth",
        "field": "snow_depth_anomaly",
        "raw_field": "snow depth",
        "raw_units": "m of water equivalent",
        "units": "in w.e.",
        "seasonal_units": "in w.e.",
        "title": "SEAS5 CONUS Snow-Depth Anomaly (in w.e.)",
        "absolute_title": "SEAS5 CONUS Snow Depth (in w.e.)",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": list(range(-8, 9)),
        "anomaly_palette": SWE_ANOMALY_PALETTE,
        "conversion": "CDS snow-depth anomaly converted from metres of water equivalent to inches",
        "header_detail": "{source_label}  •  {baseline_label}  •  Snow depth water equivalent (in)  •  CONUS domain",
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "snow_depth_anomaly",
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
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "sea_surface_temperature_anomaly",
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
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "mean_sea_level_pressure_anomaly",
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


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    return int((dt.datetime(next_year, next_month, 1) - start).total_seconds())


def convert_values(values: np.ndarray, product: dict[str, Any], target: str) -> np.ndarray:
    variable = product["variable"]
    converted = np.asarray(values, dtype=float)
    if variable == "z500":
        return converted / GEOPOTENTIAL_GRAVITY
    if variable in {"t2m", "t850", "sst"}:
        # Anomaly fields have the same numerical increment in K and °C.
        return converted
    if variable == "pr":
        return converted * month_seconds(target) * M_TO_INCH
    if variable == "sf":
        return converted * month_seconds(target) * M_TO_INCH
    if variable == "snow_depth":
        return converted * M_TO_INCH
    if variable == "slp":
        return converted / 100.0
    raise SEAS5Error(f"no unit conversion is defined for SEAS5 variable {variable}")


def latest_cds_init(now: dt.datetime | None = None) -> str:
    """Return the newest nominal ECMWF start month released by the CDS."""
    current = now or dt.datetime.now(dt.timezone.utc)
    year, month = current.year, current.month
    if (current.day, current.hour) < (CDS_ECMWF_RELEASE_DAY, CDS_ECMWF_RELEASE_HOUR):
        year, month = month_after(year, month, -1)
    return f"{year:04d}{month:02d}0100"


def cds_area(product: dict[str, Any]) -> list[float]:
    return list(CDS_CONUS_AREA if product["region"] == CONUS_PRECIP_REGION else CDS_NORTH_AMERICA_AREA)


def cds_dataset_url(dataset: str) -> str:
    return f"https://cds.climate.copernicus.eu/datasets/{dataset}"


def _coord_name(data: Any, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in data.dims or name in data.coords:
            return name
    raise SEAS5Error(f"CDS GRIB field is missing a {candidates[0]}/{candidates[-1]} coordinate")


def _select_forecast_month(data: Any, lead: int) -> Any:
    for name in ("forecastMonth", "leadtime_month"):
        if name not in data.dims:
            continue
        coordinate = np.asarray(data[name].values)
        numeric = np.asarray([int(value) for value in coordinate], dtype=int)
        matches = np.flatnonzero(numeric == lead)
        if not len(matches):
            raise SEAS5Error(f"CDS GRIB field has no forecastMonth={lead}")
        return data.isel({name: int(matches[0])})
    return data


def grid_from_grib(path: Path, product: dict[str, Any], target: str, lead: int) -> Grid:
    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover - environment contract
        raise SEAS5Error("SEAS5 rendering requires xarray and cfgrib") from exc

    backend_attempts = (
        {"filter_by_keys": {"dataType": "em"}, "time_dims": ("forecastMonth", "time")},
        {"time_dims": ("forecastMonth", "time")},
        {"filter_by_keys": {"dataType": "em"}},
        {},
    )
    dataset = None
    errors: list[str] = []
    for backend_kwargs in backend_attempts:
        try:
            candidate = xr.open_dataset(path, engine="cfgrib", backend_kwargs=backend_kwargs)
            candidate.load()
            dataset = candidate
            break
        except Exception as exc:
            errors.append(str(exc))
            try:
                candidate.close()
            except Exception:
                pass
    if dataset is None:
        detail = errors[-1] if errors else "unknown cfgrib error"
        raise SEAS5Error(f"could not decode CDS GRIB {path.name}: {detail}")

    try:
        variables = list(dataset.data_vars)
        if not variables:
            raise SEAS5Error(f"CDS GRIB {path.name} contains no data variable")
        data = dataset[variables[0]]
        data = _select_forecast_month(data, lead).squeeze(drop=True)
        latitude_name = _coord_name(data, ("latitude", "lat"))
        longitude_name = _coord_name(data, ("longitude", "lon"))
        for dimension in list(data.dims):
            if dimension not in {latitude_name, longitude_name}:
                data = data.mean(dim=dimension, skipna=True)
        data = data.transpose(latitude_name, longitude_name)
        lats = np.asarray(data[latitude_name].values, dtype=float)
        lons = np.asarray(data[longitude_name].values, dtype=float)
        raw = np.asarray(data.values, dtype=float)
    finally:
        dataset.close()

    if raw.ndim != 2:
        raise SEAS5Error(f"CDS GRIB {path.name} did not reduce to a 2-D latitude/longitude field")
    normalized_lons = ((lons + 180.0) % 360.0) - 180.0
    lon_order = np.argsort(normalized_lons)
    lat_order = np.argsort(lats)
    converted = convert_values(raw[np.ix_(lat_order, lon_order)], product, target)
    return Grid(
        lons=[float(value) for value in normalized_lons[lon_order]],
        lats=[float(value) for value in lats[lat_order]],
        values=converted.tolist(),
    )


class CDSArchive:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._client: Any | None = None

    def latest_init(self) -> str:
        return latest_cds_init()

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import cdsapi
        except ImportError as exc:  # pragma: no cover - environment contract
            raise SEAS5Error("SEAS5 rendering requires cdsapi>=0.7.7") from exc
        url = os.environ.get("CDS_API_URL", CDS_API_ROOT)
        key = os.environ.get("CDS_API_KEY", "").strip()
        try:
            if key:
                self._client = cdsapi.Client(url=url, key=key, quiet=True)
            else:
                # Local users can keep the official token in ~/.cdsapirc.
                self._client = cdsapi.Client(quiet=True)
        except Exception as exc:
            raise SEAS5Error(
                "could not initialize the CDS API client; configure CDS_API_KEY "
                "or ~/.cdsapirc"
            ) from exc
        return self._client

    def _cache_path(self, dataset: str, variable: str, product: dict[str, Any], init: str, lead: int) -> Path:
        area_name = "conus" if product["region"] == CONUS_PRECIP_REGION else "north-america"
        safe_dataset = dataset.replace("-", "_")
        safe_variable = variable.replace("-", "_")
        return self.cache_dir / "cds" / area_name / (
            f"{safe_dataset}_{safe_variable}_{init[:6]}_lead{lead:02d}.grib"
        )

    def _retrieve(
        self,
        dataset: str,
        variable: str,
        product: dict[str, Any],
        init: str,
        lead: int,
        pressure_level: str | None = None,
    ) -> Path:
        path = self._cache_path(dataset, variable, product, init, lead)
        if path.exists() and path.stat().st_size > 0:
            return path
        request: dict[str, Any] = {
            "originating_centre": CDS_ORIGINATING_CENTRE,
            "system": CDS_SYSTEM,
            "variable": [variable],
            "product_type": ["ensemble_mean"],
            "year": [init[:4]],
            "month": [init[4:6]],
            "leadtime_month": [str(lead)],
            "area": cds_area(product),
            "data_format": "grib",
        }
        if pressure_level is not None:
            request["pressure_level"] = [pressure_level]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.unlink(missing_ok=True)
            self._client_or_raise().retrieve(dataset, request, str(temporary))
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise SEAS5Error(f"CDS returned no data for {dataset} {variable} lead {lead}")
            temporary.replace(path)
        except SEAS5Error:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if "required licen" in str(exc).lower():
                raise SEAS5Error(
                    "the CDS dataset licence has not been accepted; accept the current SEAS5 "
                    f"dataset terms at {CDS_LICENSE_URL} and retry"
                ) from exc
            raise SEAS5Error(
                f"CDS request failed for {dataset} {variable} {init[:6]} lead {lead}: {exc}"
            ) from exc
        return path

    def anomaly_grid(self, product: dict[str, Any], init: str, target: str, lead: int) -> tuple[Grid, Path]:
        path = self._retrieve(
            product["cds_dataset"],
            product["cds_variable"],
            product,
            init,
            lead,
            product.get("cds_pressure_level"),
        )
        return grid_from_grib(path, product, target, lead), path

    def height_grid(self, product: dict[str, Any], init: str, target: str, lead: int) -> tuple[Grid, Path]:
        if product["name"] != Z500_ANOMALY:
            raise SEAS5Error("raw geopotential contours are only available for the 500-mb product")
        path = self._retrieve(
            product["cds_raw_dataset"],
            product["cds_raw_variable"],
            product,
            init,
            lead,
            product["cds_pressure_level"],
        )
        return grid_from_grib(path, product, target, lead), path


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
        "source_url": run_entry.get("source_url", SOURCE_URL),
        "source_urls": run_entry.get("source_urls", [SOURCE_URL]),
        "archive_root": CDS_API_ROOT,
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
    parser.add_argument("--climo-years", default="1981-2016", help="legacy compatibility option; official CDS anomaly baseline is used")
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
    archive = CDSArchive(resolve_repo_path(args.cache_dir, repo_root))
    init = archive.latest_init() if args.init == "latest" else parse_init(args.init)
    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    leads = parse_int_list(args.lead_months, "lead months", 1, 6)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 1, 6) if args.seasonal_window else []
    if seasonal_leads:
        expected = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected:
            raise SEAS5Error("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    # Retain the option for workflow compatibility, but the official CDS
    # anomaly fields already contain the matched model post-processing.
    climo_years = parse_years(args.climo_years)
    if args.absolute and args.product != Z500_ANOMALY:
        raise SEAS5Error("--absolute is only supported for the 500-mb field")

    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)
    run_id = f"seas5-{init}-{args.product}"
    anomaly_baseline = "C3S official postprocessed anomaly; ECMWF/System 51"
    source_datasets = [product["cds_dataset"]]
    if product["height_contours"]:
        source_datasets.append(product["cds_raw_dataset"])
    source_urls = [cds_dataset_url(dataset) for dataset in source_datasets]
    run_entry: dict[str, Any] = {
        "id": run_id,
        "source": SOURCE_LABEL,
        "source_url": cds_dataset_url(product["cds_dataset"]),
        "source_urls": source_urls,
        "archive_root": CDS_API_ROOT,
        "source_datasets": source_datasets,
        "originating_centre": CDS_ORIGINATING_CENTRE,
        "system": CDS_SYSTEM,
        "model": "ECMWF SEAS5",
        "product": args.product,
        "variable": product["variable"],
        "init_utc": iso_utc(init_date),
        "statistic": "ensemble_mean",
        "ensemble_scope": "ECMWF SEAS5/System 51 forecast ensemble",
        "ensemble_members": CDS_ENSEMBLE_MEMBERS,
        "aggregation": (
            f"{len(seasonal_leads)}-month {product['seasonal_reducer']} of official CDS monthly ensemble-mean anomalies"
            if seasonal_leads
            else "official CDS monthly ensemble-mean anomaly"
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "conversion": product["conversion"],
        "climatology": {
            "source": "C3S postprocessed anomaly field",
            "years_requested": f"{climo_years[0]}-{climo_years[1]}",
            "method": "official CDS bias-adjusted monthly ensemble-mean anomaly; no local hindcast subtraction",
            "status": "not_used",
        },
        "border_sources": [] if args.no_borders else [{"name": path.name} for path in border_paths],
        "targets": [],
        "status": "planned",
    }
    latest_init = archive.latest_init()
    run_entry["archive_latest_init"] = latest_init
    run_entry["archive_age_days"] = max(
        0,
        (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(latest_init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)).days,
    )
    if run_entry["archive_age_days"] > 45:
        run_entry["source_warning"] = (
            f"The latest nominal ECMWF SEAS5 initialization selected by the release calendar is "
            f"{latest_init}; confirm the monthly CDS release before rendering."
        )

    forecast_grids: dict[int, Grid] = {}
    height_grids: dict[int, Grid] = {}
    failures = 0
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
            "originating_centre": CDS_ORIGINATING_CENTRE,
            "system": CDS_SYSTEM,
            "ensemble_members": CDS_ENSEMBLE_MEMBERS,
            "status": "planned",
        }
        try:
            if args.absolute:
                forecast, source_path = archive.height_grid(product, init, target, lead)
                height_grids[lead] = forecast
                target_entry["source_dataset"] = product["cds_raw_dataset"]
                target_entry["source_variable"] = product["cds_raw_variable"]
            else:
                forecast, source_path = archive.anomaly_grid(product, init, target, lead)
                if product["height_contours"] and not args.decode_only:
                    height, _ = archive.height_grid(product, init, target, lead)
                    height_grids[lead] = height
                target_entry["source_dataset"] = product["cds_dataset"]
                target_entry["source_variable"] = product["cds_variable"]
            forecast_grids[lead] = forecast
            target_entry["source_file"] = relative_path(source_path, repo_root)
            target_entry["source_url"] = cds_dataset_url(
                product["cds_raw_dataset"] if args.absolute else product["cds_dataset"]
            )
            target_entry["area"] = cds_area(product)
            target_entry["baseline"] = (
                {"status": "not_applicable", "reason": "absolute source smoke output"}
                if args.absolute
                else {
                    "status": "official_postprocessed",
                    "source": anomaly_baseline,
                    "dataset": product["cds_dataset"],
                }
            )
            if args.decode_only:
                target_entry["status"] = "decoded"
            else:
                output_path = output_dir / init[:8] / f"seas5_{product['variable']}_{target}.jpg"
                render_map(
                    forecast,
                    init,
                    target,
                    lead,
                    list(range(CDS_ENSEMBLE_MEMBERS)),
                    output_path,
                    anomaly=not args.absolute,
                    baseline_label=("Absolute field smoke output" if args.absolute else anomaly_baseline),
                    border_paths=border_paths,
                    height_grid=height_grids.get(lead),
                    ensemble_label=f"{CDS_ENSEMBLE_MEMBERS}-member mean",
                    product_spec={**product, "source_label": SOURCE_LABEL},
                )
                target_entry["image"] = relative_path(output_path, repo_root)
                target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"SEAS5 target {target} lead {lead} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(target_entry)

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
            "originating_centre": CDS_ORIGINATING_CENTRE,
            "system": CDS_SYSTEM,
            "ensemble_members": CDS_ENSEMBLE_MEMBERS,
            "status": "planned",
        }
        try:
            if any(lead not in forecast_grids for lead in seasonal_leads):
                raise SEAS5Error("seasonal window is missing one or more CDS forecast grids")
            combine = sum_grids if product["seasonal_reducer"] == "sum" else mean_grids
            seasonal_forecast = combine([forecast_grids[lead] for lead in seasonal_leads])
            seasonal_height = (
                combine([height_grids[lead] for lead in seasonal_leads])
                if product["height_contours"] and all(lead in height_grids for lead in seasonal_leads)
                else None
            )
            output_path = output_dir / init[:8] / f"seas5_{product['variable']}_{first_target}-{last_target}.jpg"
            render_map(
                seasonal_forecast,
                init,
                first_target,
                f"{first_lead}–{last_lead}",
                list(range(CDS_ENSEMBLE_MEMBERS)),
                output_path,
                anomaly=not args.absolute,
                baseline_label=("Absolute field smoke output" if args.absolute else anomaly_baseline),
                border_paths=border_paths,
                period_label=seasonal_period_label(first_target, last_target),
                ensemble_label=f"{CDS_ENSEMBLE_MEMBERS}-member mean",
                height_grid=seasonal_height,
                product_spec={**product, "source_label": SOURCE_LABEL},
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            seasonal_entry["source_dataset"] = product["cds_dataset"] if not args.absolute else product["cds_raw_dataset"]
            seasonal_entry["source_url"] = cds_dataset_url(
                product["cds_raw_dataset"] if args.absolute else product["cds_dataset"]
            )
            seasonal_entry["baseline"] = (
                {"status": "not_applicable", "reason": "absolute source smoke output"}
                if args.absolute
                else {
                    "status": "official_postprocessed",
                    "source": anomaly_baseline,
                    "dataset": product["cds_dataset"],
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
