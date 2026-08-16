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
        "title": "CanSIPS 