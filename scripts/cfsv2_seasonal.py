#!/usr/bin/env python3
"""Fetch and render CFSv2 monthly seasonal products.

This is intentionally a standalone seasonal adapter.  WeatherNext frames use
Earth Engine and forecast-hour metadata; CFSv2 seasonal frames use the NOAA
NOMADS monthly ``pgbf``/``flxf`` GRIB2 files and calendar-month lead metadata.

The production anomaly path uses a month-matched CFSv2/reforecast baseline.
The script never substitutes a WeatherNext, ERA5, or MERRA-2 climatology.
``--absolute`` is available only for source/decoder smoke tests and is labelled
as an absolute-height product in the manifest and image.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import urljoin


NOMADS_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/"
NCEI_CALIBRATION_ROOT = "https://www.ncei.noaa.gov/thredds/fileServer/model-cfs_refor_calclim_mm_9m_pgbf/"
NCEI_FLUX_CALIBRATION_ROOT = (
    "https://www.ncei.noaa.gov/thredds/fileServer/"
    "model-cfs-allfile-reforecast/calibration-climatologies/flux-1982-2010/"
)
NCEI_CALIBRATION_YEARS = "1982-2010"
NCEI_CALIBRATION_LABEL = "NCEI CFS reforecast calibration climatology; 1982-2010"
NCEI_FLUX_CALIBRATION_LABEL = "NCEI CFS reforecast flux calibration climatology; 1982-2010"
COMMON_REFERENCE_YEARS = "1991-2020"
COMMON_REFERENCE_LABEL = "Common 1991-2020 reference (CanSIPS v3 hindcast)"
COMMON_REFERENCE_FILENAME = "z500_{target}.csv.gz"
CFS_CYCLE_HOURS = (0, 6, 12, 18)
ROLLING_MEMBER_DEFAULT = 1
GRID_LON_COUNT = 360
GRID_LAT_COUNT = 181
FLUX_GRID_LON_COUNT = 384
FLUX_GRID_LAT_COUNT = 190
ANOMALY_MIN_M = -200.0
ANOMALY_MAX_M = 200.0
PRECIP_ANOMALY_MIN_IN = -8.0
PRECIP_ANOMALY_MAX_IN = 8.0
SWE_ANOMALY_MIN_IN = -8.0
SWE_ANOMALY_MAX_IN = 8.0
ANOMALY_PALETTE = [
    "#24527a",
    "#306b90",
    "#3d83a6",
    "#4891b0",
    "#539cb8",
    "#61a7bf",
    "#70b2c6",
    "#95c4d3",
    "#c4dce3",
    "#e1e4e7",
    "#eee0e0",
    "#f2cecd",
    "#eaaaa8",
    "#e28c8b",
    "#db797b",
    "#d3686c",
    "#ca5861",
    "#bf4856",
    "#a1384a",
    "#84283f",
]
ANOMALY_TICKS = list(range(-200, 201, 20))
PRECIP_ANOMALY_TICKS = list(range(-8, 9))
PRECIP_ANOMALY_PALETTE = [
    "#7f3b08",
    "#914b0d",
    "#a6611a",
    "#bd7a2d",
    "#d0a052",
    "#dfbd7d",
    "#ead8b3",
    "#f5ead8",
    "#edf7e9",
    "#d9efd2",
    "#bfe4b6",
    "#9bd694",
    "#74c476",
    "#41ab5d",
    "#238b45",
    "#006d2c",
]
SWE_ANOMALY_TICKS = list(range(-8, 9))
SWE_ANOMALY_PALETTE = [
    "#6b2d0c",
    "#85400f",
    "#a65f1b",
    "#bd7d34",
    "#d09b57",
    "#dfbd84",
    "#ead9b8",
    "#ffffff",
    "#ffffff",
    "#b9dce8",
    "#68aec8",
    "#448fb4",
    "#2f7198",
    "#245b83",
    "#1d496f",
    "#143b5f",
]
# A social-sized North America view: retain Alaska and all of Greenland while
# keeping the lower field in the subtropics. Border drawing applies a separate
# 14°N cutoff so South America does not appear in the frame.
DEFAULT_REGION = (-160.0, -10.0, 22.0, 85.0)
# Precipitation is a CONUS-only product: the lower 48 states remain the
# subject of the map, with a small surrounding margin for geographic context.
CONUS_PRECIP_REGION = (-128.0, -65.0, 22.0, 52.0)
# Shift the projected window slightly west so the CONUS sits at the visual
# center of the square canvas while preserving Alaska and all of Greenland.
PROJECTED_X_SHIFT_FRACTION = 0.035
DEFAULT_BORDER_URLS = (
    (
        "countries.geojson",
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
    ),
    (
        "us-states.geojson",
        "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json",
    ),
)

PRODUCT_HEIGHT_ANOMALY = "500mb_height_anomaly"
PRODUCT_HEIGHT_ABSOLUTE = "500mb_height_absolute"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_SWE_ANOMALY = "snow_water_equivalent_anomaly"

# The NOMADS filenames retain the ``pgbf.`` and ``flxf.`` product prefixes.
# The FLXF monthly files are on the native CFSv2 Gaussian grid. Keep the
# source field and conversion metadata explicit so a manifest can explain
# exactly how each displayed surface product was made.
PRODUCT_SPECS = {
    PRODUCT_HEIGHT_ANOMALY: {
        "name": PRODUCT_HEIGHT_ANOMALY,
        "source_kind": "pgbf",
        "match": ":HGT:500 mb:",
        "raw_field": "HGT:500 mb",
        "raw_units": "m",
        "field": "z500_anomaly",
        "units": "m",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "hgt500",
        "state_tag": "hgt500",
        "id_token": "z500a",
        "file_token": "z500a",
        "title": "CFSv2 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "CFSv2 500-mb Geopotential Height (m)",
        "height_contours": True,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "m",
        "monthly_aggregation": "monthly forecast average",
    },
    PRODUCT_HEIGHT_ABSOLUTE: {
        "name": PRODUCT_HEIGHT_ABSOLUTE,
        "source_kind": "pgbf",
        "match": ":HGT:500 mb:",
        "raw_field": "HGT:500 mb",
        "raw_units": "m",
        "field": "z500",
        "units": "m",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "hgt500",
        "state_tag": "hgt500",
        "id_token": "z500-absolute",
        "file_token": "z500",
        "title": "CFSv2 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "CFSv2 500-mb Geopotential Height (m)",
        "height_contours": True,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "m",
        "monthly_aggregation": "monthly forecast average",
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "source_kind": "flxf",
        "match": ":PRATE:surface:",
        "raw_field": "PRATE:surface",
        "raw_units": "kg m-2 s-1",
        "field": "precipitation_anomaly",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "prate",
        # Keep inch-state files separate from the earlier mm implementation so
        # a retained rolling grid can never be reused with the wrong units.
        "state_tag": "prate_in",
        "id_token": "prate-anomaly",
        "file_token": "pratea",
        "title": "CFSv2 CONUS Precipitation Anomaly (in)",
        "absolute_title": "CFSv2 CONUS Precipitation (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "sum",
        "seasonal_aggregation": "seasonal total",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly total precipitation",
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "PRATE multiplied by calendar-month seconds, converted from mm to inches",
    },
    PRODUCT_SWE_ANOMALY: {
        "name": PRODUCT_SWE_ANOMALY,
        "source_kind": "flxf",
        "match": ":WEASD:surface:",
        "raw_field": "WEASD:surface",
        "raw_units": "kg m-2",
        "field": "snow_water_equivalent_anomaly",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT,