#!/usr/bin/env python3
"""Fetch and render NOAA CPC North American Multi-Model Ensemble products.

The realtime anomaly feed is a public NetCDF archive. The adapter keeps the
official NMME ensemble mean and probability files distinct from the derived
component consensus product.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import numpy as np

from cfsv2_seasonal import (
    DEFAULT_REGION,
    Grid,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    ensure_border_files,
    mean_grids,
    relative_path,
    render_map,
)


REALTIME_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/ENSMEAN/"
PROB_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/prob/netcdf/"
SOURCE_URL = "https://www.cpc.ncep.noaa.gov/products/NMME/data.html"
NCEI_URL = "https://www.ncei.noaa.gov/products/weather-climate-models/north-american-multi-model"
COMPONENTS = ("CanESM5", "CFSv2", "GEM5.2_NEMO", "NASA_GEOS5v2", "NCAR_CCSM4", "NCAR_CESM1")
RETIRED_PRODUCTS = frozenset({"model_spread"})

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
PROBABILITY_TICKS = list(range(0, 101, 10))
PROBABILITY_PALETTES = {
    "probability_above_normal": [
        "#f4f4f2", "#eeeeeb", "#e4e2df", "#f9e4e1", "#f3c8c4",
        "#eaa6a3", "#df8182", "#cf5c66", "#b83f53", "#8e263d",
    ],
    "probability_near_normal": [
        "#f4f4f2", "#eeeeeb", "#e4e2df", "#e7f2e3", "#d1e8c9",
        "#afd7a6", "#82c184", "#55aa68", "#2d8b50", "#11643a",
    ],
    "probability_below_normal": [
        "#f4f4f2", "#eeeeeb", "#e4e2df", "#e0edf0", "#c7dfe5",
        "#9fc8d5", "#75adc2", "#4c8dae", "#2d6994", "#173f68",
    ],
}
PROBABILITY_VARIABLES = ("prob_above", "prob_norm", "prob_below")

BASE_PRODUCTS: dict[str, dict[str, Any]] = {
    "2m_temperature_anomaly": {
        "file_var": "tmp2m", "field": "tmp2m_anomaly", "raw_field": "2-m temperature anomaly",
        "units": "Â°C", "seasonal_units": "Â°C", "min": TEMPERATURE_ANOMALY_MIN_C, "max": TEMPERATURE_ANOMALY_MAX_C,
        "ticks": TEMPERATURE_ANOMALY_TICKS, "palette": TEMPERATURE_ANOMALY_PALETTE, "title": "2-m Temperature Anomaly (Â°C)",
        "conversion": "Kelvin anomaly increments are displayed in Â°C", "reducer": "mean",
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
    # Public site leads follow the shared seasonal convention: lead 1 is one
    # month after initialization. CPC's NetCDF target index 0 is the init
    # month, so the decoder selects source index ``lead`` below.
    year, month = month_after(date.year, date.month, lead)
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
        return f"{start:%b}â€“{end:%b %Y}"
    return f"{start:%B %Y}"


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return int((dt.datetime(year, month, 1) - start).total_seconds())


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
    return ((values + 180.0) % 360.0) - 180.0


def probability_triplet_check(grids: dict[str, Grid], tolerance: float = 0.05) -> dict[str, float | int]:
    """Validate CPC tercile probabilities before any category is published."""

    if set(grids) != set(PROBABILITY_VARIABLES):
        raise NMMEError("NMME probability validation requires above, near, and below fields")
    reference = grids[PROBABILITY_VARIABLES[0]]
    arrays: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for variable in PROBABILITY_VARIABLES:
        grid = grids[variable]
        reference.assert_compatible(grid, f"NMME {variable} probability")
        values = np.asarray(grid.values, dtype=float)
        arrays.append(values)
        masks.append(np.isfinite(values))
    if not all(np.array_equal(masks[0], mask) for mask in masks[1:]):
        raise NMMEError("NMME probability categories have inconsistent missing-value masks")
    valid = masks[0]
    if not np.any(valid):
        raise NMMEError("NMME probability categories contain no finite values")
    stacked = np.stack(arrays)
    if float(np.nanmin(stacked)) < -tolerance or float(np.nanmax(stacked)) > 100.0 + tolerance:
        raise NMMEError("NMME probability category falls outside 0â€“100 percent")
    sums = np.sum(stacked, axis=0)[valid]
    maximum_error = float(np.max(np.abs(sums - 100.0)))
    if maximum_error > tolerance:
        raise NMMEError(
            f"NMME above/near/below probabilities do not sum to 100% (maximum error {maximum_error:.3f})"
        )
    return {
        "finite_points": int(sums.size),
        "minimum_sum_percent": round(float(np.min(sums)), 4),
        "maximum_sum_percent": round(float(np.max(sums)), 4),
        "maximum_sum_error_percent": round(maximum_error, 6),
    }


def decode_netcdf(path: Path, variable: str, lead: int, product: dict[str, Any], *, probability: bool, probability_period: str) -> Grid:
    try:
        import xarray as xr
    except ImportError as exc:
        raise NMMEError("NMME rendering requires xarray and a NetCDF backend") from exc
    dataset = None
    try:
        # CPC files use a non-CF ``months since`` coordinate. We select by
        # positional lead and do not need xarray to decode that coordinate.
        dataset = xr.open_dataset(path, decode_times=False)
        data = dataset[variable]
        target_dimension = next((name for name in ("target", "lead", "time") if name in data.dims), None)
        if target_dimension:
            index = lead
            if target_dimension in data.coords and "initial_time" in dataset:
                targets = np.asarray(data[target_dimension].values, dtype=float).reshape(-1)
                initial = np.asarray(dataset["initial_time"].values, dtype=float).reshape(-1)
                if initial.size:
                    matches = np.flatnonzero(np.isclose(targets, initial[0] + lead))
                    if matches.size:
                        index = int(matches[0])
            if index >= data.sizes[target_dimension]:
                raise NMMEError(f"{path.name} has no target index for lead {lead}")
            data = data.isel({target_dimension: index})
        latitude_name = next((name for name in ("lat", "latitude", "y") if name in data.dims or name in data.coords), None)
        longitude_name = next((name for name in ("lon", "longitude", "x") if name in data.dims or name in data.coords), None)
        if not latitude_name or not longitude_name:
            raise NMMEError(f"{path.name} is missing lat/lon coordinates")
        for dimension in list(data.dims):
            if dimension not in {latitude_name, longitude_name}:
                data = data.mean(dim=dimension, skipna=True)
        data = data.transpose(latitude_name, longitude_name)
        lats = np.asarray(data[latitude_name].values, dtype=float)
        lons = np.asarray(data[longitude_name].values, dtype=float)
        values = np.asarray(data.values, dtype=float)
        units = str(data.attrs.get("units", ""))
    finally:
        if dataset is not None:
            dataset.close()
    lon_order = np.argsort(normalize_longitudes(lons))
    lat_order = np.argsort(lats)
    converted = values[np.ix_(lat_order, lon_order)]
    if probability:
        # CPC labels these variables as percent but stores probabilities on
        # the 0â€“1 scale in the NetCDF files.
        if float(np.nanmax(converted)) <= 1.0:
            converted = converted * 100.0
    if not probability and product["file_var"] == "prate":
        # CPC NMME NetCDF metadata reports precipitation rate anomalies in
        # mm/s; convert the selected target month to liquid-water inches.
        converted = converted * month_seconds(target_month_from_lead(path, lead)) / 25.4
    return Grid(
        lons=[float(value) for value in normalize_longitudes(lons)[lon_order]],
        lats=[float(value) for value in lats[lat_order]],
        values=converted.tolist(),
    )


def target_month_from_lead(path: Path, lead: int) -> str:
    match = re.search(r"\.(\d{6})\.", path.name)
    if not match:
        raise NMMEError(f"could not infer NMME initialization month from {path.name}")
    init = f"{match.group(1)}0800"
    return target_month(init, lead)


def spec_for(product_name: str, base_name: str) -> dict[str, Any]:
    base = BASE_PRODUCTS[base_name]
    if product_name in BASE_PRODUCTS:
        spec = {
            "name": product_name, "variable": base["file_var"], "field": base["field"], "raw_field": base["raw_field"],
            "raw_units": base["units"], "units": base["units"], "seasonal_units": base["seasonal_units"],
            "title": f"NMME {base['title']}", "absolute_title": f"NMME {base['title']}", "height_contours": False,
            "region": base.get("region", DEFAULT_REGION), "anomaly_min": base["min"], "anomaly_max": base["max"],
            "anomaly_ticks": base["ticks"], "anomaly_palette": base["palette"], "conversion": base["conversion"],
            "header_detail": "{source_label}  â€¢  CPC realtime ensemble-mean anomaly  â€¢  {baseline_label}",
        }
        return spec
    if product_name.startswith("probability_"):
        label = {
            "probability_above_normal": "Above-Normal Probability",
            "probability_near_normal": "Near-Normal Probability",
            "probability_below_normal": "Below-Normal Probability",
        }[product_name]
        subject = {
            "2m_temperature_anomaly": "2-m Temperature",
            "precipitation_anomaly": "CONUS Precipitation",
            "200mb_height_anomaly": "200-mb Geopotential Height",
        }[base_name]
        return {
            "name": product_name, "variable": "probability", "field": product_name,
            "raw_field": f"{subject} {label.lower()}", "raw_units": "fraction", "units": "%", "seasonal_units": "%",
            "ti×Ÿ{¶‰žËkºwµçA•¹Í•µ‰±”µ•…¸ˆ¥˜½µÁ½¹•¹Ð€ôô€‰9M58ˆ•±Í”€ ‰=™™¥¥…°AÁÉ½‰…‰¥±¥Ñäˆ¥˜½µÁ½¹•¹Ð€ôô€‰AI=		%1%Qdˆ•±Í”˜‰í±•¸¡½µÁ½¹•¹ÑÌ¥ô½µÁ½¹•¹Ðµ½‘•±Ìˆ¤¤°(€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆè€‰9=A955‰ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰955íÁÉ½‘ÕÑ}¹…µ•ôí½µÁ½¹•¹Ñô±•…í±•…‘ô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤(€€€¥˜¹½ÐÁÉ½‰…‰¥±¥Ñä…¹Í•…Í½¹…±}±•…‘Ì…¹¹½Ð‘•½‘•}½¹±äè(€€€€€€€™¥ÉÍÐ°±…ÍÐ€ôÍ•…Í½¹…±}±•…‘ÍlÁt°Í•…Í½¹…±}±•…‘Íl´Åt(€€€€€€€ÍÑ…ÉÐ°•¹€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°™¥ÉÍÐ¤°Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±…ÍÐ¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉä€ôì(€€€€€€€€€€€€‰¥ˆè˜‰í•¹ÑÉål¥uôµíÍÑ…ÉÑôµí•¹‘ôˆ°€‰Ñ…É•Ñ}µ½¹Ñ ˆè˜‰íÍÑ…ÉÑôµí•¹‘ôˆ°€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡ÍÑ…ÉÐ¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡•¹¥lÅt°€‰±•…‘}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ÷ŠMí±…ÍÑôˆ°€‰µ½¹Ñ¡±å}±•…‘ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Í•…Í½¹…±}Õ¹¥ÑÌ‰t°€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€É¥‘Ì€ômÉ¥‘}‰å}±•…‘m±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít(€€€€€€€€€€€¥˜ÁÉ½‘ÕÑ}¹…µ”€ôô€‰ÁÉ•¥Á¥Ñ…Ñ¥½¹}…¹½µ…±äˆè(€€€€€€€€€€€€€€€™É½´™ÍØÉ}Í•…Í½¹…°¥µÁ½ÉÐÍÕµ}É¥‘Ì(€€€€€€€€€€€€€€€Í•…Í½¹…±}É¥€ôÍÕµ}É¥‘Ì¡É¥‘Ì¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•…Í½¹…±}É¥€ôµ•…¹}É¥‘Ì¡É¥‘Ì¤(€€€€€€€€€€€¥˜¹½Ð‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰¹µµ•}í½µÁ½¹•¹Ð¹±½Ý•È ¥õ}í‰…Í•}¹…µ•õ}íÁÉ½‘ÕÑ}¹…µ•õ}íÍÑ…ÉÑôµí•¹‘ô¹©Áœˆ(€€€€€€€€€€€€€€€É•¹‘•É}µ…À¡Í•…Í½¹…±}É¥°¥¹¥Ð°ÍÑ…ÉÐ°˜‰í™¥ÉÍÑ÷ŠMí±…ÍÑôˆ°±¥ÍÐ¡É…¹”¡µ…à Ä°±•¸¡½µÁ½¹•¹ÑÌ¤¤¤¤°½ÕÑÁÕÐ°…¹½µ…±äõQÉÕ”°‰…Í•±¥¹•}±…‰•°ô‰A955½™™¥¥…°…¹½µ…±ä½ÁÉ½‰…‰¥±¥Ñäˆ°‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•ÉÌ°Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°¡ÍÑ…ÉÐ°€Ì¤°•¹Í•µ‰±•}±…‰•°ô ‰955•¹Í•µ‰±”µ•…¸ˆ¥˜½µÁ½¹•¹Ð€ôô€‰9M58ˆ•±Í”˜‰í±•¸¡½µÁ½¹•¹ÑÌ¥ô½µÁ½¹•¹Ðµ½‘•±Ìˆ¤°ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆè€‰9=A955‰ô¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ¥˜‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤(€€€ÍÑ…ÑÕÍ•Ì€ômÑ…É•Ñl‰ÍÑ…ÑÕÌ‰t™½ÈÑ…É•Ð¥¸•¹ÑÉål‰Ñ…É•ÑÌ‰ut(€€€•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ¥˜ÍÑ…ÑÕÍ•Ì…¹…±°¡ÍÑ…ÑÕÌ€ôô€‰™…¥±•ˆ™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤•±Í”€ ‰Á…ÉÑ¥…°ˆ¥˜™…¥±ÕÉ•Ì•±Í”€ ‰‘•½‘•ˆ¥˜‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ¤¤(€€€•¹ÑÉål‰½ÕÑÁÕÑ}‘¥È‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É½½Ð¤(€€€É•ÑÕÉ¸•¹ÑÉä°™…¥±ÕÉ•Ì(()‘•˜ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡Á…Ñ èA…Ñ °•¹ÑÉ¥•Ìè%Ñ•É…‰±•m‘¥ÑmÍÑÈ°¹åut°ÁÉ•Ù¥½ÕÌèA…Ñ ð9½¹”°É•Ñ…¥¹}å±•Ìè¥¹Ð¤€´ø9½¹”è(€€€½±‘}•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€¥˜ÁÉ•Ù¥½ÕÌ…¹ÁÉ•Ù¥½ÕÌ¹•á¥ÍÑÌ ¤è(€€€€€€€ÑÉäè(€€€€€€€€€€€½±‘}•¹ÑÉ¥•Ì€ôl(€€€€€€€€€€€€€€€ÉÕ¸™½ÈÉÕ¸¥¸©Í½¸¹±½…‘Ì¡ÁÉ•Ù¥½ÕÌ¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤¹•Ð ‰ÉÕ¹Ìˆ°mt¤(€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÉÕ¸°‘¥Ð¤…¹ÍÑÈ¡ÉÕ¸¹•Ð ‰ÁÉ½‘ÕÐˆ°€ˆˆ¤¤¹½Ð¥¸IQ%I}AI=UQL(€€€€€€€€€€€t(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”955ÉÉ½È¡˜‰½Õ±¹½ÐÉ•…ÁÉ•Ù¥½ÕÌ955µ…¹¥™•ÍÐèí•áôˆ¤™É½´•áŒ(€€€Õ¹¥ÅÕ”€ôì(€€€€€€€ÍÑÈ¡ÉÕ¸¹•Ð ‰¥ˆ¤¤èÉÕ¸(€€€€€€€™½ÈÉÕ¸¥¸l©½±‘}•¹ÑÉ¥•Ì°€©•¹ÑÉ¥•Ít(€€€€€€€¥˜ÉÕ¸¹•Ð ‰¥ˆ¤…¹ÍÑÈ¡ÉÕ¸¹•Ð ‰ÁÉ½‘ÕÐˆ°€ˆˆ¤¤¹½Ð¥¸IQ%I}AI=UQL(€€€ô(€€€½É‘•É•€ôÍ½ÉÑ•¡Õ¹¥ÅÕ”¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„ÉÕ¸è€¡ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤°ÍÑÈ¡ÉÕ¸¹•Ð ‰¥ˆ°€ˆˆ¤¤¤°É•Ù•ÉÍ”õQÉÕ”¤(€€€å±•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½ÈÉÕ¸¥¸½É‘•É•è(€€€€€€€å±”€ôÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤(€€€€€€€¥˜å±”¹½Ð¥¸å±•Ìè(€€€€€€€€€€€å±•Ì¹…ÁÁ•¹¡å±”¤(€€€­••À€ôÍ•Ð¡å±•Íléµ…à Ä°É•Ñ…¥¹}å±•Ì¥t¤(€€€±…‰•±Ì€ôì¨©í­•äèÙ…±Õ•l‰Ñ¥Ñ±”‰t™½È­•ä°Ù…±Õ”¥¸	M}AI=UQL¹¥Ñ•µÌ ¥ô°€‰ÁÉ½‰…‰¥±¥Ñå}…‰½Ù•}¹½Éµ…°ˆè€‰‰½Ù”9½Éµ…°AÉ½‰…‰¥±¥Ñäˆ°€‰ÁÉ½‰…‰¥±¥Ñå}¹•…É}¹½Éµ…°ˆè€‰9•…È9½Éµ…°AÉ½‰…‰¥±¥Ñäˆ°€‰ÁÉ½‰…‰¥±¥Ñå}‰•±½Ý}¹½Éµ…°ˆè€‰	•±½Ü9½Éµ…°AÉ½‰…‰¥±¥Ñäˆ°€‰µÕ±Ñ¥}µ½‘•±}½¹Í•¹ÍÕÌˆè€‰9555Õ±Ñ¤µ5½‘•°½¹Í•¹ÍÕÌ‰ô(€€€Á…å±½…€ôì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆè€Ä°€‰­¥¹ˆè€‰¹µµ•}Í•…Í½¹…±}µ…¹¥™•ÍÐˆ°€‰•¹•É…Ñ•‘}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹¹½Ü¡‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€‰Í½ÕÉ”ˆè€‰9=A955ˆ°€‰Í½ÕÉ•}ÕÉ°ˆèM=UI}UI0°€‰Í½ÕÉ•}ÕÉ±ÌˆèmM=UI}UI0°9%}UI1t°(€€€€€€€€‰ÁÉ½‘ÕÑ}±…‰•±Ìˆè±…‰•±Ì°€‰É•Ñ•¹Ñ¥½¸ˆèì‰µ…á}å±•Ìˆèµ…à Ä°É•Ñ…¥¹}å±•Ì¤°€‰¡¥ÍÑ½Éå}å±•Ìˆèµ…à À°É•Ñ…¥¹}å±•Ì€´€Ä¥ô°(€€€€€€€€‰ÉÕ¹ÌˆèmÉÕ¸™½ÈÉÕ¸¥¸½É‘•É•¥˜ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤¥¸­••Át°(€€€ô(€€€Á…Ñ ¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€Ñ•µÁ½É…Éä€ôÁ…Ñ ¹Ý¥Ñ¡}¹…µ”¡Á…Ñ ¹¹…µ”€¬€ˆ¹ÑµÀˆ¤(€€€Ñ•µÁ½É…Éä¹ÝÉ¥Ñ•}Ñ•áÐ¡©Í½¸¹‘ÕµÁÌ¡Á…å±½…°¥¹‘•¹ÐôÈ¤€¬€‰q¸ˆ°•¹½‘¥¹œô‰ÕÑ˜´àˆ¤(€€€Ñ•µÁ½É…Éä¹É•Á±…”¡Á…Ñ ¤(()‘•˜‰Õ¥±‘}Á…ÉÍ•È ¤€´ø…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•Èè(€€€ÁÉ½‘ÕÑ}¡½¥•Ì€ôÑÕÁ±”¡	M}AI=UQL¤€¬€ ‰ÁÉ½‰…‰¥±¥Ñå}…‰½Ù•}¹½Éµ…°ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}¹•…É}¹½Éµ…°ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}‰•±½Ý}¹½Éµ…°ˆ°€‰µÕ±Ñ¥}µ½‘•±}½¹Í•¹ÍÕÌˆ¤(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¡‘•ÍÉ¥ÁÑ¥½¸õ}}‘½}|¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‘ÕÐˆ°¡½¥•ÌõÁÉ½‘ÕÑ}¡½¥•Ì°‘•™…Õ±ÐôˆÉµ}Ñ•µÁ•É…ÑÕÉ•}…¹½µ…±äˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‘ÕÑÌˆ°‘•™…Õ±Ðôˆˆ°¡•±Àô‰½ÁÑ¥½¹…°½µµ„µÍ•Á…É…Ñ•ÁÉ½‘ÕÐ±¥ÍÐˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰…Í”µÁÉ½‘ÕÐˆ°¡½¥•ÌõÑÕÁ±”¡	M}AI=UQL¤°‘•™…Õ±ÐôˆÉµ}Ñ•µÁ•É…ÑÕÉ•}…¹½µ…±äˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½µÁ½¹•¹ÑÌˆ°‘•™…Õ±Ðôˆ°ˆ¹©½¥¸¡=5A=99QL¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‰…‰¥±¥ÑäµÁ•É¥½ˆ°¡½¥•Ìô ‰µ½¸ˆ°€‰Í•…Ìˆ¤°‘•™…Õ±Ðô‰Í•…Ìˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¥¹¥Ðˆ°‘•™…Õ±Ðô‰±…Ñ•ÍÐˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ±•…µµ½¹Ñ¡Ìˆ°‘•™…Õ±ÐôˆÐ°Ô°Øˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ•…Í½¹…°µÝ¥¹‘½Üˆ°‘•™…Õ±ÐôˆÐ°Ô°Øˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…¡”µ‘¥Èˆ°‘•™…Õ±Ðôˆ¹…¡”½¹µµ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½ÕÑÁÕÐµ‘¥Èˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½¹µµ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ…¹¥™•ÍÐˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½¹µµ•}µ…¹¥™•ÍÐ¹©Í½¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ•Ù¥½ÕÌµµ…¹¥™•ÍÐˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•Ñ…¥¸µå±•Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôÐ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ½µÁ½¹•¹ÑÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ‰½É‘•ÉÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ•½©Í½¸ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘•½‘”µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È(()‘•˜ÉÕ¸¡…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”¤€´ø¥¹Ðè(€€€É•ÅÕ•ÍÑ•€ôm¥Ñ•´¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸€¡…ÉÌ¹ÁÉ½‘ÕÑÌ½È…ÉÌ¹ÁÉ½‘ÕÐ¤¹ÍÁ±¥Ð ˆ°ˆ¤¥˜¥Ñ•´¹ÍÑÉ¥À ¥t(€€€¡½¥•Ì€ôÍ•Ð¡	M}AI=UQL¤ðì‰ÁÉ½‰…‰¥±¥Ñå}…‰½Ù•}¹½Éµ…°ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}¹•…É}¹½Éµ…°ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}‰•±½Ý}¹½Éµ…°ˆ°€‰µÕ±Ñ¥}µ½‘•±}½¹Í•¹ÍÕÌ‰ô(€€€Õ¹­¹½Ý¸€ôm¥Ñ•´™½È¥Ñ•´¥¸É•ÅÕ•ÍÑ•¥˜¥Ñ•´¹½Ð¥¸¡½¥•Ít(€€€¥˜Õ¹­¹½Ý¸è(€€€€€€€É…¥Í”955ÉÉ½È¡˜‰Õ¹ÍÕÁÁ½ÉÑ•955ÁÉ½‘ÕÐ¡Ì¤èìœ°€œ¹©½¥¸¡Õ¹­¹½Ý¸¥ôˆ¤(€€€½µÁ½¹•¹ÑÌ€ôm¥Ñ•´¹ÍÑÉ¥À ¤™½È¥Ñ•´¥¸…ÉÌ¹½µÁ½¹•¹ÑÌ¹ÍÁ±¥Ð ˆ°ˆ¤¥˜¥Ñ•´¹ÍÑÉ¥À ¥t(€€€¥˜¹½Ð½µÁ½¹•¹ÑÌè(€€€€€€€É…¥Í”955ÉÉ½È ˆ´µ½µÁ½¹•¹ÑÌ…¹¹½Ð‰”•µÁÑäˆ¤(€€€¥¹¥Ð€ôÁ…ÉÍ•}¥¹¥Ð¡…ÉÌ¹¥¹¥Ð¤(€€€±•…‘Ì€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹±•…‘}µ½¹Ñ¡Ì°€‰±•…µ½¹Ñ¡Ìˆ°€Ä°€ÄÈ¤(€€€Í•…Í½¹…°€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü°€‰Í•…Í½¹…°Ý¥¹‘½Üˆ°€Ä°€ÄÈ¤¥˜…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü•±Í”mt(€€€¥˜Í•…Í½¹…°è(€€€€€€€•áÁ•Ñ•€ô±¥ÍÐ¡É…¹”¡µ¥¸¡Í•…Í½¹…°¤°µ…à¡Í•…Í½¹…°¤€¬€Ä¤¤(€€€€€€€¥˜Í•…Í½¹…°€„ô•áÁ•Ñ•è(€€€€€€€€€€€É…¥Í”955ÉÉ½È ˆ´µÍ•…Í½¹…°µÝ¥¹‘½ÜµÕÍÐ½¹Ñ…¥¸½¹Í•ÕÑ¥Ù”±•…µ½¹Ñ¡Ìˆ¤(€€€€€€€±•…‘Ì€ôÍ½ÉÑ•¡Í•Ð¡±•…‘Ì¤¹Õ¹¥½¸¡Í•…Í½¹…°¤¤(€€€É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€…¡•}‘¥È€ôA…Ñ ¡…ÉÌ¹…¡•}‘¥È¤¥˜A…Ñ ¡…ÉÌ¹…¡•}‘¥È¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼…ÉÌ¹…¡•}‘¥È(€€€½ÕÑÁÕÑ}‘¥È€ôA…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È¤¥˜A…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼…ÉÌ¹½ÕÑÁÕÑ}‘¥È(€€€µ…¹¥™•ÍÐ€ôA…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ¤¥˜A…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼…ÉÌ¹µ…¹¥™•ÍÐ(€€€ÁÉ•Ù¥½ÕÌ€ô9½¹”(€€€¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐè(€€€€€€€ÁÉ•Ù¥½ÕÌ€ô…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ(€€€‰½É‘•ÉÌ€ômt¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°…¡•}‘¥È°É½½Ð¤(€€€…¡”è‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÈ°ÍÑÈ°¥¹Ð°ÍÑÈ°ÍÑÉt°É¥‘t€ôíô(€€€Í½ÕÉ•}™¥±•Ìè‘¥ÑmÑÕÁ±•mÍÑÈ°ÍÑÈ°ÍÑÈ°¥¹Ð°ÍÑÈ°ÍÑÉt°ÍÑÉt€ôíô(€€€ÁÉ½‰…‰¥±¥Ñå}¡•­Ìè‘¥ÑmÑÕÁ±•mÍÑÈ°¥¹Ð°ÍÑÉt°‘¥ÑmÍÑÈ°™±½…Ðð¥¹Ñut€ôíô(€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™…¥±ÕÉ•Ì€ô€À((€€€‘•˜±½…¡Í½ÕÉ”èÍÑÈ°‰…Í•}¹…µ”èÍÑÈ°±•…è¥¹Ð°­¥¹èÍÑÈ°Ù…É¥…‰±”èÍÑÈð9½¹”€ô9½¹”¤€´øÉ¥è(€€€€€€€Í•±•Ñ•€ôÙ…É¥…‰±”½È€ ‰ÁÉ½‰}…‰½Ù”ˆ¥˜­¥¹€ôô€‰ÁÉ½‰…‰¥±¥Ñäˆ•±Í”€‰™ÍÐˆ¤(€€€€€€€­•ä€ô€¡Í½ÕÉ”°‰…Í•}¹…µ”°­¥¹°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°Í•±•Ñ•¤(€€€€€€€¥˜­•ä¥¸…¡”è(€€€€€€€€€€€É•ÑÕÉ¸…¡•m­•åt(€€€€€€€‰…Í”€ô	M}AI=UQMm‰…Í•}¹…µ•t(€€€€€€€¥˜­¥¹€ôô€‰ÁÉ½‰…‰¥±¥Ñäˆè(€€€€€€€€€€€™¥±•¹…µ”€ô˜‰í‰…Í•l™¥±•}Ù…Èuô¹í¥¹¥ÑlèÙuô¹ÁÉ½ˆ¹í…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½‘ô¹¹Œˆ(€€€€€€€€€€€ÕÉ°€ôÕÉ±©½¥¸¡AI=	}I==P°™¥±•¹…µ”¤(€€€€€€€€€€€Á…Ñ €ô…¡•}‘¥È€¼€‰ÁÉ½ˆˆ€¼™¥±•¹…µ”(€€€€€€€€€€€‘½Ý¹±½…¡ÕÉ°°Á…Ñ ¤(€€€€€€€€€€€‰Õ¹‘±”è‘¥ÑmÍÑÈ°É¥‘t€ôíô(€€€€€€€€€€€™½ÈÁÉ½‰…‰¥±¥Ñå}Ù…É¥…‰±”¥¸AI=		%1%Qe}YI%	1Lè(€€€€€€€€€€€€€€€ÁÉ½‰…‰¥±¥Ñå}­•ä€ô€ (€€€€€€€€€€€€€€€€€€€Í½ÕÉ”°‰…Í•}¹…µ”°­¥¹°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°ÁÉ½‰…‰¥±¥Ñå}Ù…É¥…‰±”(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜ÁÉ½‰…‰¥±¥Ñå}­•ä¹½Ð¥¸…¡”è(€€€€€€€€€€€€€€€€€€€…¡•mÁÉ½‰…‰¥±¥Ñå}­•åt€ô‘•½‘•}¹•Ñ‘˜ (€€€€€€€€€€€€€€€€€€€€€€€Á…Ñ °(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‰…‰¥±¥Ñå}Ù…É¥…‰±”°(€€€€€€€€€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€€€€€€€€€‰…Í”°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‰…‰¥±¥ÑäõQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‰…‰¥±¥Ñå}Á•É¥½õ…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}™¥±•ÍmÁÉ½‰…‰¥±¥Ñå}­•åt€ôÕÉ°(€€€€€€€€€€€€€€€‰Õ¹‘±•mÁÉ½‰…‰¥±¥Ñå}Ù…É¥…‰±•t€ô…¡•mÁÉ½‰…‰¥±¥Ñå}­•åt(€€€€€€€€€€€ÁÉ½‰…‰¥±¥Ñå}¡•­Íl¡‰…Í•}¹…µ”°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½¥t€ôÁÉ½‰…‰¥±¥Ñå}ÑÉ¥Á±•Ñ}¡•¬¡‰Õ¹‘±”¤(€€€€€€€€€€€É•ÑÕÉ¸…¡•m­•åt(€€€€€€€•±Í”è(€€€€€€€€€€€™¥±•¹…µ”€ô˜‰íÍ½ÕÉ•ô¹í‰…Í•l™¥±•}Ù…Èuô¹í¥¹¥ÑlèÙuô¹9M58¹…¹½´¹¹Œˆ(€€€€€€€€€€€ÕÉ°€ôÕÉ±©½¥¸¡˜‰íI1Q%5}I==Qõí¥¹¥Ñô¼ˆ°™¥±•¹…µ”¤(€€€€€€€€€€€Á…Ñ €ô…¡•}‘¥È€¼€‰É•…±Ñ¥µ”ˆ€¼¥¹¥Ð€¼™¥±•¹…µ”(€€€€€€€‘½Ý¹±½…¡ÕÉ°°Á…Ñ ¤(€€€€€€€É¥€ô‘•½‘•}¹•Ñ‘˜¡Á…Ñ °Í•±•Ñ•°±•…°‰…Í”°ÁÉ½‰…‰¥±¥Ñäõ­¥¹€ôô€‰ÁÉ½‰…‰¥±¥Ñäˆ°ÁÉ½‰…‰¥±¥Ñå}Á•É¥½õ…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½¤(€€€€€€€…¡•m­•åt€ôÉ¥(€€€€€€€Í½ÕÉ•}™¥±•Ím­•åt€ôÕÉ°(€€€€€€€É•ÑÕÉ¸É¥((€€€‘•˜…±¥¹•¡É¥‘Ìè±¥ÍÑmÉ¥‘t¤€´ø±¥ÍÑmÉ¥‘tè(€€€€€€€¥˜¹½ÐÉ¥‘Ìè(€€€€€€€€€€€É•ÑÕÉ¸mt(€€€€€€€É•™•É•¹”€ôÉ¥‘ÍlÁt(€€€€€€€™É½´™ÍØÉ}Í•…Í½¹…°¥µÁ½ÉÐÉ•É¥‘}¹•…É•ÍÐ(€€€€€€€É•ÑÕÉ¸mÉ•É¥‘}¹•…É•ÍÐ¡É¥°É•™•É•¹”¹±½¹Ì°É•™•É•¹”¹±…ÑÌ°€‰955½µÁ½¹•¹Ðˆ¤™½ÈÉ¥¥¸É¥‘Ít((€€€™½ÈÁÉ½‘ÕÑ}¹…µ”¥¸É•ÅÕ•ÍÑ•è(€€€€€€€‰…Í•}¹…µ”€ôÁÉ½‘ÕÑ}‰…Í”¡ÁÉ½‘ÕÑ}¹…µ”°…ÉÌ¹‰…Í•}ÁÉ½‘ÕÐ¤(€€€€€€€ÍÁ•Œ€ôÍÁ•}™½È¡ÁÉ½‘ÕÑ}¹…µ”°‰…Í•}¹…µ”¤(€€€€€€€É¥‘}‰å}±•…è‘¥Ñm¥¹Ð°É¥‘t€ôíô(€€€€€€€™¥±•Í}‰å}±•…è‘¥Ñm¥¹Ð°ÍÑÉt€ôíô(€€€€€€€½µÁ½¹•¹Ñ}É¥‘}‰å}±•…è‘¥ÑmÍÑÈ°‘¥Ñm¥¹Ð°É¥‘ut€ôí½µÁ½¹•¹Ðèíô™½È½µÁ½¹•¹Ð¥¸½µÁ½¹•¹ÑÍô(€€€€€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€¥˜ÁÉ½‘ÕÑ}¹…µ”¥¸	M}AI=UQLè(€€€€€€€€€€€€€€€€€€€É¥‘}‰å}±•…‘m±•…‘t€ô±½… ‰955ˆ°ÁÉ½‘ÕÑ}¹…µ”°±•…°€‰…¹½µ…±äˆ¤(€€€€€€€€€€€€€€€€€€€™¥±•Í}‰å}±•…‘m±•…‘t€ôÍ½ÕÉ•}™¥±•Íl ‰955ˆ°ÁÉ½‘ÕÑ}¹…µ”°€‰…¹½µ…±äˆ°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°€‰™ÍÐˆ¥t(€€€€€€€€€€€€€€€•±¥˜ÁÉ½‘ÕÑ}¹…µ”¹ÍÑ…ÉÑÍÝ¥Ñ  ‰ÁÉ½‰…‰¥±¥Ñå|ˆ¤è(€€€€€€€€€€€€€€€€€€€Ù…É¥…‰±”€ôì‰ÁÉ½‰…‰¥±¥Ñå}…‰½Ù•}¹½Éµ…°ˆè€‰ÁÉ½‰}…‰½Ù”ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}¹•…É}¹½Éµ…°ˆè€‰ÁÉ½‰}¹½É´ˆ°€‰ÁÉ½‰…‰¥±¥Ñå}‰•±½Ý}¹½Éµ…°ˆè€‰ÁÉ½‰}‰•±½Ü‰õmÁÉ½‘ÕÑ}¹…µ•t(€€€€€€€€€€€€€€€€€€€É¥‘}‰å}±•…‘m±•…‘t€ô±½… ‰955ˆ°‰…Í•}¹…µ”°±•…°€‰ÁÉ½‰…‰¥±¥Ñäˆ°Ù…É¥…‰±”¤(€€€€€€€€€€€€€€€€€€€™¥±•Í}‰å}±•…‘m±•…‘t€ôÍ½ÕÉ•}™¥±•Íl ‰955ˆ°‰…Í•}¹…µ”°€‰ÁÉ½‰…‰¥±¥Ñäˆ°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°Ù…É¥…‰±”¥t(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}É¥‘Ì€ômt(€€€€€€€€€€€€€€€€€€€™½È½µÁ½¹•¹Ð¥¸½µÁ½¹•¹ÑÌè(€€€€€€€€€€€€€€€€€€€€€€€½µÁ½¹•¹Ñ}É¥‘}‰å}±•…‘m½µÁ½¹•¹Ñum±•…‘t€ô±½…¡½µÁ½¹•¹Ð°‰…Í•}¹…µ”°±•…°€‰…¹½µ…±äˆ¤(€€€€€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}É¥‘Ì¹…ÁÁ•¹¡½µÁ½¹•¹Ñ}É¥‘}‰å}±•…‘m½µÁ½¹•¹Ñum±•…‘t¤(€€€€€€€€€€€€€€€€€€€Í½ÕÉ•}É¥‘Ì€ô…±¥¹•¡Í½ÕÉ•}É¥‘Ì¤(€€€€€€€€€€€€€€€€€€€É¥‘}‰å}±•…‘m±•…‘t€ôµ•…¹}É¥‘Ì¡Í½ÕÉ•}É¥‘Ì¤(€€€€€€€€€€€€€€€€€€€™¥±•Í}‰å}±•…‘m±•…‘t€ô€‰½µÁ½¹•¹Ð™¥±•Ìè€ˆ€¬€ˆ°€ˆ¹©½¥¸¡½µÁ½¹•¹ÑÌ¤(€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰955íÁÉ½‘ÕÑ}¹…µ•ô±•…í±•…‘ôÕ¹…Ù…¥±…‰±”èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€¥˜É¥‘}‰å}±•…è(€€€€€€€€€€€½µÁ½¹•¹Ð€ô€‰9M58ˆ¥˜ÁÉ½‘ÕÑ}¹…µ”¥¸	M}AI=UQL•±Í”€ ‰AI=		%1%Qdˆ¥˜ÁÉ½‘ÕÑ}¹…µ”¹ÍÑ…ÉÑÍÝ¥Ñ  ‰ÁÉ½‰…‰¥±¥Ñå|ˆ¤•±Í”€‰=9M9MULˆ¤(€€€€€€€€€€€•¹ÑÉä°½Õ¹Ð€ôÉ•¹‘•É}ÉÕ¸ (€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}¹…µ”õÁÉ½‘ÕÑ}¹…µ”°‰…Í•}¹…µ”õ‰…Í•}¹…µ”°¥¹¥Ðõ¥¹¥Ð°½µÁ½¹•¹Ðõ½µÁ½¹•¹Ð°(€€€€€€€€€€€€€€€½µÁ½¹•¹ÑÌõ½µÁ½¹•¹ÑÌ°ÁÉ½‘ÕÐõÍÁ•Œ°É¥‘}‰å}±•…õÉ¥‘}‰å}±•…°(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}‘¥Èõ½ÕÑÁÕÑ}‘¥È°‰½É‘•ÉÌõ‰½É‘•ÉÌ°±•…‘Ìõ±•…‘Ì°(€€€€€€€€€€€€€€€Í•…Í½¹…±}±•…‘ÌõÍ•…Í½¹…°°ÁÉ½‰…‰¥±¥Ñå}Á•É¥½õ…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°(€€€€€€€€€€€€€€€Í½ÕÉ•}™¥±•Ìõ™¥±•Í}‰å}±•…°‘•½‘•}½¹±äõ…ÉÌ¹‘•½‘•}½¹±ä°(€€€€€€€€€€€€€€€ÁÉ½‰…‰¥±¥Ñå}¡•­Ìõì(€€€€€€€€€€€€€€€€€€€±•…èÁÉ½‰…‰¥±¥Ñå}¡•­Íl¡‰…Í•}¹…µ”°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½¥t(€€€€€€€€€€€€€€€€€€€™½È±•…¥¸É¥‘}‰å}±•…(€€€€€€€€€€€€€€€€€€€¥˜€¡‰…Í•}¹…µ”°±•…°…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½¤¥¸ÁÉ½‰…‰¥±¥Ñå}¡•­Ì(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡•¹ÑÉä¤(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô½Õ¹Ð(€€€€€€€¥˜¹½Ð…ÉÌ¹¹½}½µÁ½¹•¹ÑÌ…¹ÁÉ½‘ÕÑ}¹…µ”¥¸	M}AI=UQLè(€€€€€€€€€€€™½È½µÁ½¹•¹Ð¥¸½µÁ½¹•¹ÑÌè(€€€€€€€€€€€€€€€É¥‘Ì€ô½µÁ½¹•¹Ñ}É¥‘}‰å}±•…‘m½µÁ½¹•¹Ñt(€€€€€€€€€€€€€€€€Œ½µÁ½¹•¹Ð™¥±•Ì…É”±½…‘•±…é¥±ä½¹±ä™½ÈÑ¡”‘•É¥Ù•(€€€€€€€€€€€€€€€€ŒÁÉ½‘ÕÑÌì™•Ñ Ñ¡•´¡•É”™½ÈÑ¡”½ÁÑ¥½¹…°½µÁ½¹•¹ÐÉÕ¹Ì¸(€€€€€€€€€€€€€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€€€€€É¥‘Ím±•…‘t€ô±½…¡½µÁ½¹•¹Ð°ÁÉ½‘ÕÑ}¹…µ”°±•…°€‰…¹½µ…±äˆ¤(€€€€€€€€€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰955½µÁ½¹•¹Ðí½µÁ½¹•¹Ñô±•…í±•…‘ôÕ¹…Ù…¥±…‰±”èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€€€€€€€€€¥˜É¥‘Ìè(€€€€€€€€€€€€€€€€€€€½µÁ½¹•¹Ñ}ÍÁ•Œ€ôì¨©ÍÁ•Œ°€‰Ñ¥Ñ±”ˆè˜‰955í½µÁ½¹•¹Ñôƒ
Üí	M}AI=UQMmÁÉ½‘ÕÑ}¹…µ•ulÑ¥Ñ±”uôˆ°€‰¡•…‘•É}‘•Ñ…¥°ˆè€‰íÍ½ÕÉ•}±…‰•±ô€ƒŠˆ€%¹‘¥Ù¥‘Õ…°955½µÁ½¹•¹Ðµµ½‘•°µ•…¸‰ô(€€€€€€€€€€€€€€€€€€€•¹ÑÉä°½Õ¹Ð€ôÉ•¹‘•É}ÉÕ¸¡ÁÉ½‘ÕÑ}¹…µ”õÁÉ½‘ÕÑ}¹…µ”°‰…Í•}¹…µ”õ‰…Í•}¹…µ”°¥¹¥Ðõ¥¹¥Ð°½µÁ½¹•¹Ðõ½µÁ½¹•¹Ð°½µÁ½¹•¹ÑÌõm½µÁ½¹•¹Ñt°ÁÉ½‘ÕÐõ½µÁ½¹•¹Ñ}ÍÁ•Œ°É¥‘}‰å}±•…õÉ¥‘Ì°½ÕÑÁÕÑ}‘¥Èõ½ÕÑÁÕÑ}‘¥È°‰½É‘•ÉÌõ‰½É‘•ÉÌ°±•…‘Ìõ±•…‘Ì°Í•…Í½¹…±}±•…‘ÌõÍ•…Í½¹…°°ÁÉ½‰…‰¥±¥Ñå}Á•É¥½õ…ÉÌ¹ÁÉ½‰…‰¥±¥Ñå}Á•É¥½°Í½ÕÉ•}™¥±•Ìõíô°‘•½‘•}½¹±äõ…ÉÌ¹‘•½‘•}½¹±ä¤(€€€€€€€€€€€€€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡•¹ÑÉä¤(€€€€€€€€€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô½Õ¹Ð(€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÐ°•¹ÑÉ¥•Ì°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}å±•Ì¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”955µ…¹¥™•ÍÐèíµ…¹¥™•ÍÑô€¡í±•¸¡•¹ÑÉ¥•Ì¥ôÉÕ¸•¹ÑÉ¥•Ì¤ˆ¤(€€€É•ÑÕÉ¸€À¥˜•¹ÑÉ¥•Ì•±Í”€È(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤(€€€•á•ÁÐ955ÉÉ½È…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰955II=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤