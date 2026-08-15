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
    ANOMALY_TICKS,
    COMMON_REFERENCE_LABEL,
    COMMON_REFERENCE_YEARS,
    CONUS_PRECIP_REGION,
    CFSv2Error,
    DEFAULT_REGION,
    PRECIP_ANOMALY_PALETTE,
    SWE_ANOMALY_PALETTE,
    Grid,
    load_common_reference,
    ensure_border_files,
    mean_grids,
    regrid_nearest,
    relative_path,
    render_map,
    subtract_grids,
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
    "#6e3b17",
    "#81491e",
    "#955a27",
    "#a96b31",
    "#bb7f3f",
    "#ca9156",
    "#d6a875",
    "#dfbd91",
    "#dcebd7",
    "#c8e4bf",
    "#aad89f",
    "#86c879",
    "#5fba6b",
    "#3aa55b",
    "#1d8947",
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
        "anomaly_ticks": ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "conversion": "geopotential divided by standard gravity to convert mÂ² sâ»Â² to geopotential meters",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Height contours in dam",
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
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "title": "SEAS5 2-m Temperature Anomaly (Â°C)",
        "absolute_title": "SEAS5 2-m Temperature (Â°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -6.0,
        "anomaly_max": 6.0,
        "anomaly_ticks": [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  2-m temperature anomaly (Â°C)",
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "2m_temperature_anomaly",
    },
    T850_ANOMALY: {
        "name": T850_ANOMALY,
        "variable": "t850",
        "field": "t850_anomaly",
        "raw_field": "t850 / temperature",
        "raw_units": "K",
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "title": "SEAS5 850-mb Temperature Anomaly (Â°C)",
        "absolute_title": "SEAS5 850-mb Temperature (Â°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -6.0,
        "anomaly_max": 6.0,
        "anomaly_ticks": [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  850-mb temperature anomaly (Â°C)",
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Precipitation accumulation (in)  â€¢  CONUS domain",
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Snowfall liquid-water equivalent (in)  â€¢  CONUS domain",
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Snow depth water equivalent (in)  â€¢  CONUS domain",
        "cds_dataset": CDS_SINGLE_ANOMALY_DATASET,
        "cds_variable": "snow_depth_anomaly",
    },
    SST_ANOMALY: {
        "name": SST_ANOMALY,
        "variable": "sst",
        "field": "sst_anomaly",
        "raw_field": "sst",
        "raw_units": "K",
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "title": "SEAS5 Sea-Surface Temperature Anomaly (Â°C)",
        "absolute_title": "SEAS5 Sea-Surface Temperature (Â°C)",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -3.0,
        "anomaly_max": 3.0,
        "anomaly_ticks": [-3, -2, -1, 0, 1, 2, 3],
        "anomaly_palette": TEMP_PALETTE,
        "conversion": "Kelvin-to-Celsius offset cancels in anomaly differences",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Sea-surface temperature anomaly (Â°C)",
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Mean sea-level pressure anomaly (hPa)",
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
        (6, 8): f"ënu¶‰žËkºwµçI½‘ÕÑl‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰½É¥¥¹…Ñ¥¹}•¹ÑÉ”ˆèM}=I%%9Q%9}9QI°(€€€€€€€€€€€€‰ÍåÍÑ•´ˆèM}MeMQ4°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆèM}9M5	1}55	IL°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜…ÉÌ¹…‰Í½±ÕÑ”è(€€€€€€€€€€€€€€€™½É•…ÍÐ°Í½ÕÉ•}Á…Ñ €ô…É¡¥Ù”¹¡•¥¡Ñ}É¥¡ÁÉ½‘ÕÐ°¥¹¥Ð°Ñ…É•Ð°±•…¤(€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥‘Ím±•…‘t€ô™½É•…ÍÐ(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}‘…Ñ…Í•Ð‰t€ôÁÉ½‘ÕÑl‰‘Í}É…Ý}‘…Ñ…Í•Ð‰t(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}Ù…É¥…‰±”‰t€ôÁÉ½‘ÕÑl‰‘Í}É…Ý}Ù…É¥…‰±”‰t(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€™½É•…ÍÐ°Í½ÕÉ•}Á…Ñ €ô…É¡¥Ù”¹…¹½µ…±å}É¥¡ÁÉ½‘ÕÐ°¥¹¥Ð°Ñ…É•Ð°±•…¤(€€€€€€€€€€€€€€€¥˜ÁÉ½‘ÕÑl‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t…¹¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€€€€€¡•¥¡Ð°|€ô…É¡¥Ù”¹¡•¥¡Ñ}É¥¡ÁÉ½‘ÕÐ°¥¹¥Ð°Ñ…É•Ð°±•…¤(€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥‘Ím±•…‘t€ô¡•¥¡Ð(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}‘…Ñ…Í•Ð‰t€ôÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}Ù…É¥…‰±”‰t€ôÁÉ½‘ÕÑl‰‘Í}Ù…É¥…‰±”‰t(€€€€€€€€€€€™½É•…ÍÑ}É¥‘Ím±•…‘t€ô™½É•…ÍÐ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}™¥±”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡Í½ÕÉ•}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}ÕÉ°‰t€ô‘Í}‘…Ñ…Í•Ñ}ÕÉ° (€€€€€€€€€€€€€€€ÁÉ½‘ÕÑl‰‘Í}É…Ý}‘…Ñ…Í•Ð‰t¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”ÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰…É•„‰t€ô‘Í}…É•„¡ÁÉ½‘ÕÐ¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰t€ô€ (€€€€€€€€€€€€€€€ì‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}…ÁÁ±¥…‰±”ˆ°€‰É•…Í½¸ˆè€‰…‰Í½±ÕÑ”Í½ÕÉ”Íµ½­”½ÕÑÁÕÐ‰ô(€€€€€€€€€€€€€€€¥˜…ÉÌ¹…‰Í½±ÕÑ”(€€€€€€€€€€€€€€€•±Í”ì(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰½™™¥¥…±}Á½ÍÑÁÉ½•ÍÍ•ˆ°(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè…¹½µ…±å}‰…Í•±¥¹”°(€€€€€€€€€€€€€€€€€€€€‰‘…Ñ…Í•ÐˆèÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰Í•…ÌÕ}íÁÉ½‘ÕÑlÙ…É¥…‰±”uõ}íÑ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€™½É•…ÍÐ°(€€€€€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹”¡M}9M5	1}55	IL¤¤°(€€€€€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€€€€€…¹½µ…±äõ¹½Ð…ÉÌ¹…‰Í½±ÕÑ”°(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°ô ‰‰Í½±ÕÑ”™¥•±Íµ½­”½ÕÑÁÕÐˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”…¹½µ…±å}‰…Í•±¥¹”¤°(€€€€€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õ¡•¥¡Ñ}É¥‘Ì¹•Ð¡±•…¤°(€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰íM}9M5	1}55	IMôµµ•µ‰•Èµ•…¸ˆ°(€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆèM=UI}1	1ô°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€€€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€€€€€¥˜±•…¹½Ð¥¸¡•¥¡Ñ}É¥‘Ìè(€€€€€€€€€€€€€€€€€€€€€€€€€€€É…¥Í”MLÕÉÉ½È ‰É…Ü€ÔÀÀµµˆ¡•¥¡ÐÝ…Ì¹½Ð…Ù…¥±…‰±”™½ÈÑ¡”½µµ½¸½µÁ…É¥Í½¸ˆ¤(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹”°É•™•É•¹•}Á…Ñ °É•™•É•¹•}ÕÉ°°É•™•É•¹•}‘½Ý¹±½…‘•°½µµ½¹}É•™•É•¹•}±…ÍÑ}É•ÅÕ•ÍÐ€ô±½…‘}½µµ½¹}É•™•É•¹” (€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}‘¥È°(€€€€€€€€€€€€€€€€€€€€€€€€€€€…ÉÌ¹½µµ½¹}É•™•É•¹•}ÕÉ°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€µ…à À¸À°…ÉÌ¹½µµ½¹}É•™•É•¹•}É•ÅÕ•ÍÑ}‘•±…ä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}±…ÍÑ}É•ÅÕ•ÍÐ°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹”€ôÉ•É¥‘}¹•…É•ÍÐ (€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥‘Ím±•…‘t¹±½¹Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥‘Ím±•…‘t¹±…ÑÌ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€˜‰½µµ½¸É•™•É•¹”íÑ…É•Ñôˆ°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É¥€ôÍÕ‰ÑÉ…Ñ}É¥‘Ì¡¡•¥¡Ñ}É¥‘Ím±•…‘t°½µµ½¹}É•™•É•¹”¤(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰Í•…ÌÕ}íÁÉ½‘ÕÑlÙ…É¥…‰±”uõ}íÑ…É•Ñõ}½µµ½¸´ÄääÄ´ÈÀÈÀ¹©Áœˆ(€€€€€€€€€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹”¡M}9M5	1}55	IL¤¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}½ÕÑÁÕÐ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õ¡•¥¡Ñ}É¥‘Ím±•…‘t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰íM}9M5	1}55	IMôµµ•µ‰•Èµ•…¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆèM=UI}1	1ô°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¥µ…”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½µµ½¹}½ÕÑÁÕÐ°É•Á½}É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•¹‘•É•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè=55=9}II9}eIL°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡É•™•É•¹•}Á…Ñ °É•Á½}É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÕÉ°ˆèÉ•™•É•¹•}ÕÉ°½È9½¹”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘½Ý¹±½…‘•ˆèÉ•™•É•¹•}‘½Ý¹±½…‘•°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Õ¹…Ù…¥±…‰±”ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè=55=9}II9}eIL°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰MLÔ½µµ½¸½µÁ…É¥Í½¸Ñ…É•ÐíÑ…É•ÑôÕ¹…Ù…¥±…‰±”èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰MLÔÑ…É•ÐíÑ…É•Ñô±•…í±•…‘ô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤((€€€¥˜Í•…Í½¹…±}±•…‘Ì…¹¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€™¥ÉÍÑ}±•…°±…ÍÑ}±•…€ôÍ•…Í½¹…±}±•…‘ÍlÁt°Í•…Í½¹…±}±•…‘Íl´Åt(€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°™¥ÉÍÑ}±•…¤°Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±…ÍÑ}±•…¤(€€€€€€€Í•…Í½¹…±}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡™¥ÉÍÑ}Ñ…É•Ð¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡±…ÍÑ}Ñ…É•Ð¥lÅt°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}±•…‘ôµí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ íÁÉ½‘ÕÑlÍ•…Í½¹…±}É•‘Õ•Èuôˆ°(€€€€€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Í•…Í½¹…±}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰µ½¹Ñ¡±å}±•…‘ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€‰½É¥¥¹…Ñ¥¹}•¹ÑÉ”ˆèM}=I%%9Q%9}9QI°(€€€€€€€€€€€€‰ÍåÍÑ•´ˆèM}MeMQ4°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆèM}9M5	1}55	IL°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜…¹ä¡±•…¹½Ð¥¸™½É•…ÍÑ}É¥‘Ì™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¤è(€€€€€€€€€€€€€€€É…¥Í”MLÕÉÉ½È ‰Í•…Í½¹…°Ý¥¹‘½Ü¥Ìµ¥ÍÍ¥¹œ½¹”½Èµ½É”L™½É•…ÍÐÉ¥‘Ìˆ¤(€€€€€€€€€€€½µ‰¥¹”€ôÍÕµ}É¥‘Ì¥˜ÁÉ½‘ÕÑl‰Í•…Í½¹…±}É•‘Õ•È‰t€ôô€‰ÍÕ´ˆ•±Í”µ•…¹}É¥‘Ì(€€€€€€€€€€€Í•…Í½¹…±}™½É•…ÍÐ€ô½µ‰¥¹”¡m™½É•…ÍÑ}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€Í•…Í½¹…±}¡•¥¡Ð€ô€ (€€€€€€€€€€€€€€€½µ‰¥¹”¡m¡•¥¡Ñ}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€€€€€¥˜ÁÉ½‘ÕÑl‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t…¹…±°¡±•…¥¸¡•¥¡Ñ}É¥‘Ì™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¤(€€€€€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€€€€€¤(€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰Í•…ÌÕ}íÁÉ½‘ÕÑlÙ…É¥…‰±”uõ}í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô¹©Áœˆ(€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€Í•…Í½¹…±}™½É•…ÍÐ°(€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€˜‰í™¥ÉÍÑ}±•…‘÷ŠMí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹”¡M}9M5	1}55	IL¤¤°(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€…¹½µ…±äõ¹½Ð…ÉÌ¹…‰Í½±ÕÑ”°(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°ô ‰‰Í½±ÕÑ”™¥•±Íµ½­”½ÕÑÁÕÐˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”…¹½µ…±å}‰…Í•±¥¹”¤°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÍ•…Í½¹…±}Á•É¥½‘}±…‰•°¡™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð¤°(€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰íM}9M5	1}55	IMôµµ•µ‰•Èµ•…¸ˆ°(€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õÍ•…Í½¹…±}¡•¥¡Ð°(€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆèM=UI}1	1ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰Í½ÕÉ•}‘…Ñ…Í•Ð‰t€ôÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t¥˜¹½Ð…ÉÌ¹…‰Í½±ÕÑ”•±Í”ÁÉ½‘ÕÑl‰‘Í}É…Ý}‘…Ñ…Í•Ð‰t(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰Í½ÕÉ•}ÕÉ°‰t€ô‘Í}‘…Ñ…Í•Ñ}ÕÉ° (€€€€€€€€€€€€€€€ÁÉ½‘ÕÑl‰‘Í}É…Ý}‘…Ñ…Í•Ð‰t¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”ÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰t€ô€ (€€€€€€€€€€€€€€€ì‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}…ÁÁ±¥…‰±”ˆ°€‰É•…Í½¸ˆè€‰…‰Í½±ÕÑ”Í½ÕÉ”Íµ½­”½ÕÑÁÕÐ‰ô(€€€€€€€€€€€€€€€¥˜…ÉÌ¹…‰Í½±ÕÑ”(€€€€€€€€€€€€€€€•±Í”ì(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰½™™¥¥…±}Á½ÍÑÁÉ½•ÍÍ•ˆ°(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè…¹½µ…±å}‰…Í•±¥¹”°(€€€€€€€€€€€€€€€€€€€€‰‘…Ñ…Í•ÐˆèÁÉ½‘ÕÑl‰‘Í}‘…Ñ…Í•Ð‰t°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€¥˜…¹ä¡±•…¹½Ð¥¸¡•¥¡Ñ}É¥‘Ì™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¤è(€€€€€€€€€€€€€€€€€€€€€€€É…¥Í”MLÕÉÉ½È ‰É…Ü€ÔÀÀµµˆ¡•¥¡ÐÝ…Ì¹½Ð…Ù…¥±…‰±”™½ÈÑ¡”½µµ½¸Í•…Í½¹…°½µÁ…É¥Í½¸ˆ¤(€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•Ì€ômt(€€€€€€€€€€€€€€€€€€€É•™•É•¹•}™¥±•Ì€ômt(€€€€€€€€€€€€€€€€€€€É•™•É•¹•}ÕÉ±Ì€ômt(€€€€€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ìè(€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤(€€€€€€€€€€€€€€€€€€€€€€€É•™•É•¹”°É•™•É•¹•}Á…Ñ °É•™•É•¹•}ÕÉ°°É•™•É•¹•}‘½Ý¹±½…‘•°½µµ½¹}É•™•É•¹•}±…ÍÑ}É•ÅÕ•ÍÐ€ô±½…‘}½µµ½¹}É•™•É•¹” (€€€€€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}‘¥È°(€€€€€€€€€€€€€€€€€€€€€€€€€€€…ÉÌ¹½µµ½¹}É•™•É•¹•}ÕÉ°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€µ…à À¸À°…ÉÌ¹½µµ½¹}É•™•É•¹•}É•ÅÕ•ÍÑ}‘•±…ä¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}±…ÍÑ}É•ÅÕ•ÍÐ°(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•Ì¹…ÁÁ•¹¡É•É¥‘}¹•…É•ÍÐ (€€€€€€€€€€€€€€€€€€€€€€€€€€€É•™•É•¹”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}¡•¥¡Ð¹±½¹Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}¡•¥¡Ð¹±…ÑÌ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€˜‰½µµ½¸É•™•É•¹”íÑ…É•Ñôˆ°(€€€€€€€€€€€€€€€€€€€€€€€€¤¤(€€€€€€€€€€€€€€€€€€€€€€€É•™•É•¹•}™¥±•Ì¹…ÁÁ•¹¡É•±…Ñ¥Ù•}Á…Ñ ¡É•™•É•¹•}Á…Ñ °É•Á½}É½½Ð¤¤(€€€€€€€€€€€€€€€€€€€€€€€¥˜É•™•É•¹•}ÕÉ°è(€€€€€€€€€€€€€€€€€€€€€€€€€€€É•™•É•¹•}ÕÉ±Ì¹…ÁÁ•¹¡É•™•É•¹•}ÕÉ°¤(€€€€€€€€€€€€€€€€€€€½µµ½¹}‰…Í•±¥¹”€ôµ•…¹}É¥‘Ì¡½µµ½¹}É•™•É•¹•Ì¤(€€€€€€€€€€€€€€€€€€€½µµ½¹}É¥€ôÍÕ‰ÑÉ…Ñ}É¥‘Ì¡Í•…Í½¹…±}¡•¥¡Ð°½µµ½¹}‰…Í•±¥¹”¤(€€€€€€€€€€€€€€€€€€€½µµ½¹}½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰Í•…ÌÕ}íÁÉ½‘ÕÑlÙ…É¥…‰±”uõ}í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñõ}½µµ½¸´ÄääÄ´ÈÀÈÀ¹©Áœˆ(€€€€€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É¥°(€€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€€€€€˜‰í™¥ÉÍÑ}±•…‘õqÔÈÀÄÍí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹”¡M}9M5	1}55	IL¤¤°(€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}½ÕÑÁÕÐ°(€€€€€€€€€€€€€€€€€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÍ•…Í½¹…±}Á•É¥½‘}±…‰•°¡™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð¤°(€€€€€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰íM}9M5	1}55	IMôµµ•µ‰•Èµ•…¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õÍ•…Í½¹…±}¡•¥¡Ð°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì¨©ÁÉ½‘ÕÐ°€‰Í½ÕÉ•}±…‰•°ˆèM=UI}1	1ô°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¥µ…”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½µµ½¹}½ÕÑÁÕÐ°É•Á½}É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•¹‘•É•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè=55=9}II9}eIL°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰™¥±•ÌˆèÉ•™•É•¹•}™¥±•Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÕÉ±ÌˆèÉ•™•É•¹•}ÕÉ±Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Õ¹…Ù…¥±…‰±”ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè=55=9}II9}1	0°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè=55=9}II9}eIL°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¤°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€ÁÉ¥¹Ð (€€€€€€€€€€€€€€€€€€€€€€€˜‰MLÔ½µµ½¸½µÁ…É¥Í½¸Í•…Í½¹…°Ý¥¹‘½Üí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•ÑôÕ¹…Ù…¥±…‰±”èí•áôˆ°(€€€€€€€€€€€€€€€€€€€€€€€™¥±”õÍåÌ¹ÍÑ‘•ÉÈ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰MLÔÍ•…Í½¹…°Ý¥¹‘½Üí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Í•…Í½¹…±}•¹ÑÉä¤((€€€ÍÑ…ÑÕÍ•Ì€ômÑ…É•Ð¹•Ð ‰ÍÑ…ÑÕÌˆ¤™½ÈÑ…É•Ð¥¸ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰ut(€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ¥˜™…¥±ÕÉ•Ì…¹¹½Ð…¹ä¡ÍÑ…ÑÕÌ€„ô€‰™…¥±•ˆ™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤•±Í”€ (€€€€€€€€‰Á…ÉÑ¥…°ˆ¥˜™…¥±ÕÉ•Ì•±Í”€ ‰‘•½‘•ˆ¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ¤(€€€€¤(€€€ÉÕ¹}•¹ÑÉål‰½ÕÑÁÕÑ}‘¥È‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤(€€€ÁÉ•Ù¥½ÕÌ€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ°É•Á½}É½½Ð¤¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ•±Í”9½¹”(€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÑ}Á…Ñ °É•Á½}É½½Ð°ÉÕ¹}•¹ÑÉä°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}ÉÕ¹Ì¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”MLÔµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑ}Á…Ñ¡ôˆ¤(€€€É•ÑÕÉ¸€È¥˜™…¥±ÕÉ•Ì•±Í”€À(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤4(€€€•á•ÁÐMLÕÉÉ½È…Ì•áŒè4(€€€€€€€ÁÉ¥¹Ð¡˜‰MLÔII=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤4(€€€€€€€É•ÑÕÉ¸€È4(4(4)¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè4(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤4(