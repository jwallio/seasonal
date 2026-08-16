#!/usr/bin/env python3
"""Fetch and render NOAA CPC North American Multi-Model Ensemble products.

The realtime anomaly feed is a public NetCDF archive.  The adapter keeps the
official NMME ensemble mean and probability files distinct from derived
component consensus and model-spread products.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import numpy as np

from cfsv2_seasonal import DEFAULT_REGION, Grid, ensure_border_files, mean_grids, relative_path, render_map


REALTIME_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/ENSMEAN/"
PROB_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/prob/netcdf/"
SOURCE_URL = "https://www.cpc.ncep.noaa.gov/products/NMME/data.html"
NCEI_URL = "https://www.ncei.noaa.gov/products/weather-climate-models/north-american-multi-model"
COMPONENTS = ("CanESM5", "CFSv2", "GEM5.2_NEMO", "NASA_GEOS5v2", "NCAR_CCSM4", "NCAR_CESM1")

TEMP_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#82bdca", "#b4d6dc", "#e7eeee",
    "#ffffff", "#f8dedd", "#efb6b5", "#e38e8e", "#d36c73", "#b84c5a",
]
PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f", "#86c879",
    "#5fba6b", "#3aa55b", "#1d8947", "#006d2c",
]
HEIGHT_PALETTE = [
    "#24527a", "#306b90", "#3d83a6", "#4891b0", "#539cb8", "#61a7bf",
    "#70b2c6", "#95c4d3", "#c4dce3", "#e1e4e7", "#eee0e0", "#f2cecd",
    "#eaaaa8", "#e28c8b", "#db797b", "#d3686c", "#ca5861", "#bf4856",
    "#a1384a", "#84283f",
]
PROB_PALETTE = ["#173f68", "#2b6590", "#4d8fb0", "#83b8c9", "#c9dfe5", "#fffdf8", "#f5c8c2", "#dd8f89", "#c6545c", "#8e263d"]

BASE_PRODUCTS: dict[str, dict[str, Any]] = {
    "2m_temperature_anomaly": {
        "file_var": "tmp2m", "field": "tmp2m_anomaly", "raw_field": "2-m temperature anomaly",
        "units": "°C", "seasonal_units": "°C", "min": -6.0, "max": 6.0,
        "ticks": list(range(-6, 7)), "palette": TEMP_PALETTE, "title": "2-m Temperature Anomaly (°C)",
        "conversion": "Kelvin anomaly increments are displayed in °C", "reducer": "mean",
    },
    "precipitation_anomaly": {
        "file_var": "prate", "field": "precipitation_anomaly", "raw_field": "precipitation anomaly",
        "units": "in", "seasonal_units": "in", "min": -8.0, "max": 8.0,
        "ticks": list(range(-8, 9)), "palette": PRECIP_PALETTE, "title": "CONUS Precipitation Anomaly (in)",
        "conversion": "NMME precipitation-rate anomaly multiplied by target-month seconds and converted to inches", "reducer": "sum",
        "region": (-128.0, -65.0, 22.0, 52.0),
    },
    "200mb_height_anomaly": {
        "file_var": "z200", "field": "z200_anomaly", "raw_field": "200-mb geopotential height anomaly",
        "units": "m", "seasonal_units": "m", "min": -200.0, "max": 200.0,
        "ticks": list(range(-200, 201, 20)), "palette": HEIGHT_PALETTE, "title": "200-mb Geopotential Height Anomaly (m)",
        "conversion": "NMME z200 anomaly field displayed in metres", "reducer": "mean",
    },
}


class NMMEError(RuntimeError):
    """A user-actionable NMME source or decoding error."""


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_init(value: str) -> str:
    if value == "latest":
        try:
            import requests
            response = requests.get(REALTIME_ROOT, timeout=(20, 60))
            response.raise_for_status()
            candidates = sorted(set(re.findall(r'href="(\d{10})/"', response.text)), reverse=True)
            now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            for candidate in candidates:
                parsed = dt.datetime.strptime(candidate, "%Y%m%d%H")
                if parsed <= now:
                    return candidate
        except Exception as exc:
            raise NMMEError(f"could not discover the latest CPC NMME cycle: {exc}") from exc
        raise NMMEError("the CPC NMME directory listed no usable initialization")
    if re.fullmatch(r"\d{6}", value):
        return f"{value}0800"
    if re.fullmatch(r"\d{8}", value):
        return f"{value}00"
    if re.fullmatch(r"\d{10}", value):
        return value
    raise NMMEError("--init must be latest, YYYYMM, YYYYMMDD, or YYYYMMDDHH")


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    values: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise NMMEError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise NMMEError(f"{label} must be between {minimum} and {maximum}")
        if number not in values:
            values.append(number)
    if not values:
        raise NMMEError(f"{label} cannot be empty")
    return values


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(date.year, date.month, lead - 1)
    return f"{year:04d}{month:02d}"


def target_period(target: str, months: int = 1) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, months)
    return iso_utc(start.replace(tzinfo=dt.timezone.utc)), iso_utc(dt.datetime(year, month, 1, tzinfo=dt.timezone.utc))


def period_label(first: str, months: int = 1) -> str:
    start = dt.datetime.strptime(first, "%Y%m")
    if months == 3:
        end_year, end_month = month_after(start.year, start.month, 2)
        end = dt.datetime(end_year, end_month, 1)
        season = {(12, 2): "DJF", (3, 5): "MAM", (6, 8): "JJA", (9, 11): "SON"}.get((start.month, end.month))
        if season:
            return f"{season} {end.year}"
        return f"{start:%b}–{end:%b %Y}"
    return f"{start:%B %Y}"


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return int((dt.datetime(year, month, 1) - start).total_seconds())


def grid_std(grids: list[Grid]) -> Grid:
    if not grids:
        raise NMMEError("cannot calculate spread from an empty component set")
    first = grids[0]
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        row: list[float] = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [value for value in samples if math.isfinite(value)]
            row.append(float(np.std(finite, ddof=0)) if finite else math.nan)
        values.append(row)
    return Grid(first.lons[:], first.lats[:], values)


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    try:
        import requests
        response = requests.get(url, stream=True, timeout=(30, 300))
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if temporary.stat().st_size == 0:
            raise NMMEError(f"empty NMME download: {url}")
        temporary.replace(path)
    except NMMEError:
        raise
    except Exception as exc:
        raise NMMEError(f"NMME download failed for {url}: {exc}") from exc


def normalize_longitudes(values: np.ndarray) -> np.ndarray:
    return ((values + 180.0) %