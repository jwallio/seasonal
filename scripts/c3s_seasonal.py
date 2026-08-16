#!/usr/bin/env python3
"""Fetch and render C3S multi-system and component seasonal guidance.

The Copernicus Climate Data Store exposes each contributing centre through the
same postprocessed seasonal datasets.  This adapter keeps those centre/system
choices in the manifest, renders the selected components, and also publishes a
transparent multi-system mean made from the native C3S anomaly fields.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CONUS_PRECIP_REGION,
    DEFAULT_REGION,
    Grid,
    ensure_border_files,
    mean_grids,
    relative_path,
    render_map,
    sum_grids,
)
from seas5_seasonal import grid_from_grib


CDS_API_ROOT = "https://cds.climate.copernicus.eu/api"
PRESSURE_DATASET = "seasonal-postprocessed-pressure-levels"
SINGLE_DATASET = "seasonal-postprocessed-single-levels"
RAW_PRESSURE_DATASET = "seasonal-monthly-pressure-levels"
SOURCE_URL = "https://climate.copernicus.eu/seasonal-forecasts"
PRESSURE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels"
SINGLE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels"
LICENSE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels?tab=download#manage-licences"
NORTH_AMERICA_AREA = [90.0, -170.0, 15.0, 0.0]
CONUS_AREA = [60.0, -135.0, 20.0, -55.0]
GEOPOTENTIAL_GRAVITY = 9.80665
M_TO_INCH = 1000.0 / 25.4

CENTRES: dict[str, dict[str, Any]] = {
    "ecmwf": {"label": "ECMWF", "system": "51", "members": 51},
    "ukmo": {"label": "UK Met Office", "system": "604", "members": 62},
    "meteo_france": {"label": "Météo-France", "system": "9", "members": 51},
    "dwd": {"label": "DWD", "system": "22", "members": 50},
    "cmcc": {"label": "CMCC", "system": "4", "members": 50},
    "ncep": {"label": "NCEP", "system": "2", "members": 24},
    "jma": {"label": "JMA", "system": "2", "members": 28},
    "eccc": {"label": "ECCC", "system": "5", "members": 20},
    "bom": {"label": "BOM", "system": "2", "members": 33},
}

TEMP_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#82bdca", "#b4d6dc", "#e7eeee",
    "#ffffff", "#f8dedd", "#efb6b5", "#e38e8e", "#d36c73", "#b84c5a",
]
MSLP_PALETTE = [
    "#315f85", "#4e83a3", "#72a6bb", "#d9e5e6", "#ffffff", "#dfa69f", "#ac4f55", "#672b3a",
]
SST_PALETTE = ["#28567f", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5", "#b84c5a"]
PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f", "#86c879",
    "#5fba6b", "#3aa55b", "#1d8947", "#006d2c",
]

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "name": "500mb_height_anomaly", "variable": "z500", "field": "z500_anomaly",
        "raw_field": "geopotential anomaly", "raw_units": "m² s⁻²", "units": "m",
        "seasonal_units": "m", "height_contours": True, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -200.0,
        "anomaly_max": 200.0, "anomaly_ticks": ANOMALY_TICKS, "anomaly_palette": ANOMALY_PALETTE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "geopotential_anomaly",
        "cds_pressure_level": "500", "cds_raw_dataset": RAW_PRESSURE_DATASET,
        "cds_raw_variable": "geopotential", "raw_field_name": "geopotential",
    },
    "850mb_temperature_anomaly": {
        "name": "850mb_temperature_anomaly", "variable": "t850", "field": "t850_anomaly",
        "raw_field": "temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -6.0,
        "anomaly_max": 6.0, "anomaly_ticks": list(range(-6, 7)), "anomaly_palette": TEMP_PALETTE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "temperature_anomaly",
        "cds_pressure_level": "850",
    },
    "2m_temperature_anomaly": {
        "name": "2m_temperature_anomaly", "variable": "t2m", "field": "t2m_anomaly",
        "raw_field": "2-m temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -6.0,
        "anomaly_max": 6.0, "anomaly_ticks": list(range(-6, 7)), "anomaly_palette": TEMP_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "2m_temperature_anomaly",
    },
    "precipitation_anomaly": {
        "name": "precipitation_anomaly", "variable": "pr", "field": "precipitation_anomaly",
        "raw_field": "total precipitation anomaly", "raw_units": "m s⁻¹", "units": "in",
        "seasonal_units": "in", "height_contours": False, "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total", "seasonal_reducer": "sum", "anomaly_min": -8.0,
        "anomaly_max": 8.0, "anomaly_ticks": list(range(-8, 9)), "anomaly_palette": PRECIP_PALETTE,
        "cds_dataset": SINGLE_DATASET,
        "cds_variable": "total_precipitation_anomalous_rate_of_accumulation",
    },
    "sea_surface_temperature_anomaly": {
        "name": "sea_surface_temperature_anomaly", "variable": "sst", "field": "sst_anomaly",
        "raw_field": "sea-surface temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -3.0,
        "anomaly_max": 3.0, "anomaly_ticks": list(range(-3, 4)), "anomaly_palette": SST_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "sea_surface_temperature_anomaly",
    },
    "mslp_anomaly": {
        "name": "mslp_anomaly", "variable": "slp", "field": "mslp_anomaly",
        "raw_field": "mean sea-level pressure anomaly", "raw_units": "Pa", "units": "hPa",
        "seasonal_units": "hPa", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -20.0,
        "anomaly_max": 20.0, "anomaly_ticks": list(range(-20, 21, 5)), "anomaly_palette": MSLP_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "mean_sea_level_pressure_anomaly",
    },
}


class C3SError(RuntimeError):
    """A user-actionable C3S source or rendering error."""


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_init(value: str) -> str:
    if value == "latest":
        now = dt.datetime.now(dt.timezone.utc)
        return f"{now.year:04d}{now.month:02d}0100"
    if re.fullmatch(r"\d{6}", value):
        return f"{value}0100"
    if re.fullmatch(r"\d{8}", value):
        return f"{value}00"
    if re.fullmatch(r"\d{10}", value):
        return value
    raise C3SError("--init must be latest, YYYYMM, YYYYMMDD, or YYYYMMDDHH")


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise C3SError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise C3SError(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise C3SError(f"{label} cannot be empty")
    return result


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
