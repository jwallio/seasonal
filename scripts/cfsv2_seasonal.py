#!/usr/bin/env python3
"""Fetch and render CFSv2 monthly seasonal products.

This is intentionally a standalone seasonal adapter.  WeatherNext frames use
Earth Engine and forecast-hour metadata; CFSv2 seasonal frames use the NOAA
NOMADS monthly ``pgbf``/``flxf`` GRIB2 files and calendar-month lead metadata.

The production anomaly path uses a month-matched CFSv2/reforecast baseline.
The script never substitutes a WeatherNext, ERA5, or MERRA-2 climatology.
``--absolute`` is available only for source/decoder smoke tests and is labelled
as an absolute-height product in the manifest and image.
The separately named snowfall-accumulation product is an explicit derived
snow-depth estimate using a documented climatological snow-to-liquid ratio;
it is never presented as an anomaly or as storm-scale deterministic snowfall.
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
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import urljoin

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
from seasonal_products import grid_quality_control, is_retired_product, require_quality_control


NOMADS_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/"
NCEI_CALIBRATION_ROOT = "https://www.ncei.noaa.gov/thredds/fileServer/model-cfs_refor_calclim_mm_9m_pgbf/"
NCEI_FLUX_CALIBRATION_ROOT = (
    "https://www.ncei.noaa.gov/thredds/fileServer/"
    "model-cfs-allfile-reforecast/calibration-climatologies/flux-1982-2010/"
)
NCEI_CALIBRATION_YEARS = "1982-2010"
NCEI_CALIBRATION_LABEL = "NCEI CFS reforecast calibration climatology; 1982-2010"
NCEI_FLUX_CALIBRATION_LABEL = "NCEI CFS reforecast flux calibration climatology; 1982-2010"
NCEI_CALIBRATION_DOWNLOAD_ATTEMPTS = 3
NCEI_CALIBRATION_FALLBACK_MAX_DAYS = 7
COMMON_REFERENCE_YEARS = "1991-2020"
COMMON_REFERENCE_LABEL = "Common 1991-2020 reference (CanSIPS v3 hindcast)"
COMMON_REFERENCE_FILENAME = "z500_{target}.csv.gz"
CFS_CYCLE_HOURS = (0, 6, 12, 18)
ROLLING_MEMBER_DEFAULT = 1
GRID_LON_COUNT = 360
GRID_LAT_COUNT = 181
FLUX_GRID_LON_COUNT = 384
FLUX_GRID_LAT_COUNT = 190
# Shared fixed scale for every true seasonal 500-mb height-anomaly map.
# Keeping one range across providers makes side-by-side comparisons honest and
# gives the relatively small seasonal signal enough contrast to be readable.
ANOMALY_MIN_M = -100.0
ANOMALY_MAX_M = 100.0
PRECIP_ANOMALY_MIN_IN = -8.0
PRECIP_ANOMALY_MAX_IN = 8.0
CFSV2_HEIGHT_ANOMALY_MIN_M = -100.0
CFSV2_HEIGHT_ANOMALY_MAX_M = 100.0
PRECIP_MONTHLY_ANOMALY_MIN_IN = -4.0
PRECIP_MONTHLY_ANOMALY_MAX_IN = 4.0
PRECIP_SEASONAL_ANOMALY_MIN_IN = -8.0
PRECIP_SEASONAL_ANOMALY_MAX_IN = 8.0
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
ANOMALY_TICKS = list(range(-100, 101, 10))
PRECIP_ANOMALY_TICKS = list(range(-8, 9))
CFSV2_HEIGHT_ANOMALY_TICKS = list(range(-100, 101, 10))
PRECIP_MONTHLY_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]
PRECIP_SEASONAL_ANOMALY_TICKS = list(range(-8, 9))
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
# Shared snowfall liquid-water-equivalent departure scale.  The previous ±1.2
# range clips broad areas of the DJF CanSIPS field, so use a symmetric,
# nonlinear set of bins: finer near zero and progressively wider toward the
# tails.  The bins are intentionally categorical (uniform visual widths), so
# the strong departures retain contrast without turning ordinary departures
# into a mostly white map.
SNOWFALL_ANOMALY_MIN_IN = -4.0
SNOWFALL_ANOMALY_MAX_IN = 4.0
SNOWFALL_ANOMALY_TICK_DECIMALS = 2
SNOWFALL_ANOMALY_TICK_FORMAT = "signed_trimmed"
SNOWFALL_ANOMALY_TICKS = [
    -4.0,
    -3.5,
    -3.0,
    -2.5,
    -2.0,
    -1.75,
    -1.5,
    -1.25,
    -1.0,
    -0.75,
    -0.5,
    0.0,
    0.5,
    0.75,
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    3.5,
    4.0,
]
SNOWFALL_ANOMALY_PALETTE = [
    "#572308",
    "#6b2d0c",
    "#7b370d",
    "#8c4712",
    "#9d5517",
    "#ae691f",
    "#bd7d34",
    "#ca9156",
    "#d7a875",
    "#e3c99a",
    "#ffffff",
    "#ffffff",
    "#b9dce8",
    "#96c9d7",
    "#75b8cc",
    "#5ca5bd",
    "#4a93b2",
    "#3a80a5",
    "#2e6d93",
    "#245b83",
    "#1b496e",
    "#123856",
]
# A single month has a smaller liquid-water-equivalent departure amplitude
# than a three-month total. Use the approved fine breakpoints through 2 inches
# for monthly maps, while the generic snowfall constants above remain the
# wider seasonal/DJF scale for existing callers.
SNOWFALL_MONTHLY_ANOMALY_MIN_IN = -2.0
SNOWFALL_MONTHLY_ANOMALY_MAX_IN = 2.0
SNOWFALL_MONTHLY_ANOMALY_TICKS = [
    -2.0,
    -1.75,
    -1.5,
    -1.25,
    -1.0,
    -0.75,
    -0.5,
    0.0,
    0.5,
    0.75,
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
]
SNOWFALL_MONTHLY_ANOMALY_PALETTE = [
    "#572308",
    "#7b370d",
    "#9d5517",
    "#bd7d34",
    "#d7a875",
    "#e3c99a",
    "#ffffff",
    "#ffffff",
    "#b9dce8",
    "#75b8cc",
    "#4a93b2",
    "#2e6d93",
    "#1b496e",
    "#123856",
]
# Snow-depth accumulation uses the established WN2 snowfall palette. Monthly
# products retain two-inch bins through 40 inches, then use five-inch bins
# through 100 inches and ten-inch high-end bins through 200 inches. Three-month
# totals use five-inch bins through 100 inches and the same ten-inch high-end
# bins. Major labels remain sparse while every contour boundary is retained.
SNOWFALL_ACCUMULATION_MONTHLY_BOUNDS_IN = (
    list(range(0, 42, 2)) + list(range(45, 105, 5)) + list(range(110, 201, 10))
)
SNOWFALL_ACCUMULATION_MONTHLY_TICKS_IN = list(range(0, 201, 20))
SNOWFALL_ACCUMULATION_SEASONAL_BOUNDS_IN = list(range(0, 105, 5)) + list(range(110, 201, 10))
SNOWFALL_ACCUMULATION_SEASONAL_TICKS_IN = list(range(0, 201, 20))
SNOWFALL_ACCUMULATION_BLUE_PALETTE = [
    "#eaf8ff", "#cfeeff", "#a9defd", "#8bd1fa",
    "#6ac1f0", "#4aaee8", "#3a9ee1", "#2f8fd9",
]
SNOWFALL_ACCUMULATION_PURPLE_PALETTE = [
    "#516dd0", "#5f5fc9", "#6b52c6", "#7849c2",
    "#8540be", "#9d36b7", "#b93db8", "#d451bb",
]
SNOWFALL_ACCUMULATION_CYAN_PALETTE = [
    "#00b8d6", "#16c4df", "#28d0e6",
    "#52def0", "#74e8f4", "#cbfbff",
]
SNOWFALL_ACCUMULATION_GREEN_PALETTE = [
    "#1ec48f", "#53d8ae", "#97edd0", "#ddfff1",
]
SNOWFALL_ACCUMULATION_YELLOW_PALETTE = ["#ffd153"]
SNOWFALL_ACCUMULATION_ORANGE_PALETTE = ["#f9b03b", "#f28530", "#ef6c2f"]
SNOWFALL_ACCUMULATION_RED_PALETTE = [
    "#e95732", "#e33f36", "#d92d3a", "#c91f3a", "#ba173f",
]
SNOWFALL_ACCUMULATION_HIGH_PALETTE = [
    "#a50f47", "#92154f", "#7f1d5a", "#6a1a59",
    "#53144f", "#441143", "#3b103f",
]
SNOWFALL_ACCUMULATION_MONTHLY_PALETTE = (
    SNOWFALL_ACCUMULATION_BLUE_PALETTE
    + SNOWFALL_ACCUMULATION_PURPLE_PALETTE
    + SNOWFALL_ACCUMULATION_CYAN_PALETTE
    + SNOWFALL_ACCUMULATION_GREEN_PALETTE
    + SNOWFALL_ACCUMULATION_YELLOW_PALETTE
    + SNOWFALL_ACCUMULATION_ORANGE_PALETTE
    + SNOWFALL_ACCUMULATION_RED_PALETTE
    + SNOWFALL_ACCUMULATION_HIGH_PALETTE
)
SNOWFALL_ACCUMULATION_SEASONAL_PALETTE = (
    SNOWFALL_ACCUMULATION_BLUE_PALETTE
    + SNOWFALL_ACCUMULATION_PURPLE_PALETTE
    + SNOWFALL_ACCUMULATION_CYAN_PALETTE
    + [SNOWFALL_ACCUMULATION_GREEN_PALETTE[0], SNOWFALL_ACCUMULATION_GREEN_PALETTE[2]]
    + SNOWFALL_ACCUMULATION_YELLOW_PALETTE
    + [SNOWFALL_ACCUMULATION_ORANGE_PALETTE[1]]
    + [SNOWFALL_ACCUMULATION_RED_PALETTE[1], SNOWFALL_ACCUMULATION_RED_PALETTE[3]]
    + [SNOWFALL_ACCUMULATION_HIGH_PALETTE[2], SNOWFALL_ACCUMULATION_HIGH_PALETTE[4]]
)
# Shared fixed scale for seasonal 850-mb and 2-m temperature anomalies.
# Model-specific narrower ranges clipped stronger signals and made the same
# anomaly look different in comparison views.
TEMPERATURE_ANOMALY_MIN_C = -7.0
TEMPERATURE_ANOMALY_MAX_C = 7.0
TEMPERATURE_ANOMALY_TICKS = list(range(-7, 8))
# Retain the CFSv2 name for callers that imported the former model-specific
# tick list; CFSv2 now uses the shared scale too.
CFSV2_TEMPERATURE_ANOMALY_TICKS = TEMPERATURE_ANOMALY_TICKS
TEMPERATURE_ANOMALY_PALETTE = [
    "#24527a",
    "#306b90",
    "#3d83a6",
    "#539cb8",
    "#70b2c6",
    "#95c4d3",
    "#e1e4e7",
    "#f2cecd",
    "#eaaaa8",
    "#e28c8b",
    "#d3686c",
    "#ca5861",
    "#a1384a",
    "#84283f",
]
MSLP_ANOMALY_TICKS = list(range(-20, 21, 2))
CFSV2_MSLP_ANOMALY_TICKS = list(range(-10, 11))
MSLP_ANOMALY_PALETTE = ANOMALY_PALETTE
# A social-sized North America view: retain Alaska and all of Greenland while
# keeping the lower field in the subtropics. Border drawing applies a separate
# 14°N cutoff so South America does not appear in the frame.
DEFAULT_REGION = (-160.0, -10.0, 22.0, 85.0)
# CONUS products use a tight lower-48 frame: enough margin to keep the
# westernmost, easternmost, northernmost, and southernmost states visible,
# while minimizing unused Canada and Mexico in the square projected canvas.
# This is a render crop only; provider download areas remain deliberately
# larger for edge-data coverage.
CONUS_PRECIP_REGION = (-126.0, -66.0, 24.0, 50.0)
CONUS_REGION = CONUS_PRECIP_REGION
NORTHERN_HEMISPHERE_REGION = (-180.0, 180.0, 0.0, 90.0)
# The renderer uses these names when a CONUS product requests a land-only
# frame. Keeping the list here lets every provider share the same lower-48
# crop instead of relying on a provider-specific lon/lat rectangle.
CONUS_STATE_NAMES = (
    "Alabama", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
    "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
)
# Shift the projected window slightly west so the CONUS sits at the visual
# center of the square canvas while preserving Alaska and all of Greenland.
PROJECTED_X_SHIFT_FRACTION = 0.035
# Keep the projection definition named and shared with other seasonal
# renderers. Analog products are re-rendered through this same function so
# their geometry cannot drift from the operational seasonal maps.
SEASONAL_LCC_PROJECTION_NAME = "Lambert Conformal Conic"
SEASONAL_LCC_STANDARD_PARALLEL_1 = 30.0
SEASONAL_LCC_STANDARD_PARALLEL_2 = 60.0
SEASONAL_LCC_LATITUDE_ORIGIN = 45.0
SEASONAL_LCC_CENTRAL_LONGITUDE = -100.0
SEASONAL_NORTH_POLAR_STEREOGRAPHIC_PROJECTION_NAME = "North Polar Stereographic"
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
PRODUCT_HEIGHT_ANOMALY_NH = "500mb_height_anomaly_nh"
PRODUCT_HEIGHT_ABSOLUTE = "500mb_height_absolute"
PRODUCT_850_TEMPERATURE_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_2M_TEMPERATURE_ANOMALY = "2m_temperature_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_SWE_ANOMALY = "snow_water_equivalent_anomaly"
PRODUCT_SNOWFALL_ANOMALY = "snowfall_anomaly"
PRODUCT_SNOWFALL_ACCUMULATION = "snowfall_accumulation"

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
        "region": DEFAULT_REGION,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "m",
        "monthly_aggregation": "monthly forecast average",
        "anomaly_min": CFSV2_HEIGHT_ANOMALY_MIN_M,
        "anomaly_max": CFSV2_HEIGHT_ANOMALY_MAX_M,
        "anomaly_ticks": CFSV2_HEIGHT_ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
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
    PRODUCT_850_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_850_TEMPERATURE_ANOMALY,
        "source_kind": "pgbf",
        "match": ":TMP:850 mb:",
        "raw_field": "TMP:850 mb",
        "raw_units": "K",
        "field": "t850_anomaly",
        "units": "°C",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "tmp850",
        "state_tag": "tmp850",
        "id_token": "t850a",
        "file_token": "t850a",
        "title": "CFSv2 850-mb Temperature Anomaly (°C)",
        "absolute_title": "CFSv2 850-mb Temperature (°C)",
        "region": CONUS_REGION,
        "height_contours": False,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "°C",
        "monthly_aggregation": "monthly mean 850-mb temperature",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": CFSV2_TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "conversion": "Kelvin offset cancels in forecast-minus-calibration anomalies; displayed in °C",
        "header_detail": "{source_label}  •  {baseline_label}  •  850-mb temperature anomaly (°C)",
    },
    PRODUCT_2M_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "source_kind": "flxf",
        "match": ":TMP:2 m above ground:",
        "raw_field": "TMP:2 m above ground",
        "raw_units": "K",
        "field": "t2m_anomaly",
        "units": "°C",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "tmp2m",
        "state_tag": "tmp2m",
        "id_token": "t2ma",
        "file_token": "t2ma",
        "title": "CFSv2 2-m Temperature Anomaly (°C)",
        "absolute_title": "CFSv2 2-m Temperature (°C)",
        "region": CONUS_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "°C",
        "monthly_aggregation": "monthly mean 2-m temperature",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": CFSV2_TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "conversion": "Kelvin offset cancels in forecast-minus-calibration anomalies; displayed in °C",
        "header_detail": "{source_label}  •  {baseline_label}  •  2-m temperature anomaly (°C)",
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "source_kind": "pgbf",
        "match": ":PRES:mean sea level:",
        "raw_field": "PRES:mean sea level",
        "raw_units": "Pa",
        "field": "mslp_anomaly",
        "units": "hPa",
        "grid_shape": (GRID_LON_COUNT, GRID_LAT_COUNT),
        "cache_tag": "mslp",
        "state_tag": "mslp",
        "id_token": "mslpa",
        "file_token": "mslpa",
        "title": "CFSv2 Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "CFSv2 Mean Sea-Level Pressure (hPa)",
        "region": CONUS_REGION,
        "height_contours": False,
        "baseline_root": NCEI_CALIBRATION_ROOT,
        "baseline_label": NCEI_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "hPa",
        "monthly_aggregation": "monthly mean sea-level pressure",
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "PRES divided by 100 to convert Pa to hPa before calculating the anomaly",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": CFSV2_MSLP_ANOMALY_TICKS,
        "anomaly_palette": MSLP_ANOMALY_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Mean sea-level pressure anomaly (hPa)",
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
        "title": "CFSv2 Precipitation Anomaly (in)",
        "absolute_title": "CFSv2 Precipitation (in)",
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
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "weasd",
        "state_tag": "weasd_in",
        "id_token": "swe-anomaly",
        "file_token": "swea",
        "title": "CFSv2 Snow-Water-Equivalent Anomaly (in)",
        "absolute_title": "CFSv2 Snow-Water Equivalent (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean snow-water equivalent",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly snow-water-equivalent average",
        "conversion_kind": "snow_water_equivalent_inches",
        "conversion": "WEASD divided by 25.4 to convert kg m-2/mm of liquid water equivalent to inches",
        "map_domain": "land",
    },
    PRODUCT_SNOWFALL_ANOMALY: {
        "name": PRODUCT_SNOWFALL_ANOMALY,
        "source_kind": "derived",
        "dependencies": (
            PRODUCT_2M_TEMPERATURE_ANOMALY,
            PRODUCT_850_TEMPERATURE_ANOMALY,
            PRODUCT_PRECIPITATION_ANOMALY,
        ),
        "raw_field": "Derived from TMP:2 m above ground, TMP:850 mb, and PRATE:surface",
        "raw_units": "K; K; kg m-2 s-1",
        "field": "snowfall_lwe",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "id_token": "snowfall-anomaly",
        "file_token": "snowfalla",
        "title": "CFSv2 Snowfall Departure (in)",
        "absolute_title": "CFSv2 Derived Snowfall Liquid-Water Equivalent (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "baseline_label": "NCEI CFSR/CFSv2 1982-2010 derived snowfall climatology",
        "seasonal_reducer": "sum",
        "seasonal_aggregation": "seasonal snowfall departure total",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly derived snowfall departure",
        "anomaly_min": SNOWFALL_ANOMALY_MIN_IN,
        "anomaly_max": SNOWFALL_ANOMALY_MAX_IN,
        "anomaly_ticks": SNOWFALL_ANOMALY_TICKS,
        "anomaly_palette": SNOWFALL_ANOMALY_PALETTE,
        "monthly_anomaly_min": SNOWFALL_MONTHLY_ANOMALY_MIN_IN,
        "monthly_anomaly_max": SNOWFALL_MONTHLY_ANOMALY_MAX_IN,
        "monthly_anomaly_ticks": SNOWFALL_MONTHLY_ANOMALY_TICKS,
        "monthly_anomaly_palette": SNOWFALL_MONTHLY_ANOMALY_PALETTE,
        "conversion": "Dai 2008 snow fraction applied member-by-member to monthly precipitation LWE using max(2-m, 850-mb) temperature",
        "map_domain": "land",
        "fit_frame_to_domain": True,
        "domain_frame_padding_fraction": 0.0,
        "mask_states": list(CONUS_STATE_NAMES),
        "border_files": ("us-states.geojson",),
    },
    PRODUCT_SNOWFALL_ACCUMULATION: {
        "name": PRODUCT_SNOWFALL_ACCUMULATION,
        "source_kind": "flxf",
        "match": ":SRWEQ:surface:",
        "cache_tag": "native-srweq-v1",
        "state_tag": "native-srweq-v1",
        "raw_field": "SRWEQ:surface",
        "raw_units": "kg m-2 s-1",
        "field": "snowfall_accumulation",
        "units": "in",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "id_token": "snowfall-accumulation",
        "file_token": "snowfall",
        "title": "CFSv2 Estimated Snowfall Accumulation (in)",
        "absolute_title": "CFSv2 Estimated Snowfall Accumulation (in)",
        "region": CONUS_PRECIP_REGION,
        "height_contours": False,
        "requires_baseline": False,
        "render_as_anomaly": False,
        "seasonal_reducer": "sum",
        "seasonal_aggregation": "seasonal estimated snowfall accumulation",
        "seasonal_units": "in",
        "monthly_aggregation": "monthly estimated snowfall accumulation",
        "monthly_absolute_bounds": SNOWFALL_ACCUMULATION_MONTHLY_BOUNDS_IN,
        "monthly_absolute_ticks": SNOWFALL_ACCUMULATION_MONTHLY_TICKS_IN,
        "monthly_absolute_palette": SNOWFALL_ACCUMULATION_MONTHLY_PALETTE,
        "seasonal_absolute_bounds": SNOWFALL_ACCUMULATION_SEASONAL_BOUNDS_IN,
        "seasonal_absolute_ticks": SNOWFALL_ACCUMULATION_SEASONAL_TICKS_IN,
        "seasonal_absolute_palette": SNOWFALL_ACCUMULATION_SEASONAL_PALETTE,
        "conversion": "Native SRWEQ integrated over calendar months, complete-cycle mean, multiplied by verified CIPS 1971-2000 CWA mean SLR; unadjusted estimate",
        "header_detail": "{source_label}  •  Native snowfall × CIPS 1971-2000 CWA mean ratio  •  Estimated snow depth (in)  •  CONUS domain",
        "map_domain": "land",
        "fit_frame_to_domain": True,
        "domain_frame_padding_fraction": 0.0,
        "mask_states": list(CONUS_STATE_NAMES),
        "border_files": ("us-states.geojson",),
    },
}


# Both 500-mb views decode the same NOMADS field and share the rolling cache;
# only the published image framing and output token differ.
PRODUCT_SPECS[PRODUCT_HEIGHT_ANOMALY_NH] = {
    **PRODUCT_SPECS[PRODUCT_HEIGHT_ANOMALY],
    "name": PRODUCT_HEIGHT_ANOMALY_NH,
    "id_token": "z500a-nh",
    "file_token": "z500a-nh",
    "region": NORTHERN_HEMISPHERE_REGION,
    "projection": "north_polar_stereographic",
    "projection_central_longitude": 0.0,
    "title": "CFSv2 Northern Hemisphere 500-mb Geopotential Height & Anomaly (m)",
    "absolute_title": "CFSv2 Northern Hemisphere 500-mb Geopotential Height (m)",
    "header_detail": "{source_label}  •  {baseline_label}  •  Height contours in dam  •  Northern Hemisphere",
}

HEIGHT_ANOMALY_PRODUCTS = frozenset({PRODUCT_HEIGHT_ANOMALY, PRODUCT_HEIGHT_ANOMALY_NH})
SNOWFALL_PRODUCTS = frozenset({PRODUCT_SNOWFALL_ANOMALY, PRODUCT_SNOWFALL_ACCUMULATION})


class CFSv2Error(RuntimeError):
    """A user-actionable CFSv2 pipeline error."""


@dataclass
class Grid:
    """A longitude/latitude grid represented without a hard dependency."""

    lons: list[float]
    lats: list[float]
    values: list[list[float]]

    def assert_compatible(self, other: "Grid", label: str) -> None:
        if self.lons != other.lons or self.lats != other.lats:
            raise CFSv2Error(f"{label} grid does not match the forecast grid")


# CFSv2 does not provide a directly comparable monthly snowfall field. Keep
# the derivation explicit and aligned with the CanSIPS implementation: use
# Dai (2008) land snow-frequency fits and the warmer of the 2-m and 850-hPa
# monthly mean temperatures as a conservative phase gate.
SNOWFALL_DAI_LAND_PARAMS_BY_SEASON = {
    "ANN": (-48.2292, 0.7205, 1.1662, 1.0223),
    "DJF": (-48.2372, 0.7449, 1.0919, 1.0209),
    "MAM": (-48.2493, 0.6634, 1.3388, 1.0270),
    "JJA": (-46.4000, 0.7013, 0.8362, 1.0217),
    "SON": (-48.3251, 0.7798, 1.1502, 1.0180),
}


def snowfall_fraction_from_temperature_c(temperature_c: float, season: str = "DJF") -> float:
    """Return the Dai (2008) land snow fraction for a mean temperature."""

    if not math.isfinite(temperature_c):
        return math.nan
    try:
        coefficient, slope, midpoint, offset = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON[season]
    except KeyError as exc:
        raise CFSv2Error(
            f"unsupported snowfall phase season {season!r}; choose from "
            f"{', '.join(SNOWFALL_DAI_LAND_PARAMS_BY_SEASON)}"
        ) from exc
    fraction = coefficient * (math.tanh(slope * (temperature_c - midpoint)) - offset) / 100.0
    return max(0.0, min(1.0, fraction))


def snowfall_phase_season(target: str) -> str:
    """Return the Dai seasonal fit name for a YYYYMM target month."""

    month = dt.datetime.strptime(target, "%Y%m").month
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def derive_snowfall_lwe_grid(
    temperature_2m_grids: dict[str, Grid],
    temperature_850_grids: dict[str, Grid],
    precipitation_grids: dict[str, Grid],
    target: str,
) -> tuple[Grid, dict[str, object]]:
    """Derive a member/cycle-mean monthly snowfall LWE grid in inches.

    The inputs are already decoded and converted to the output grid. This
    function deliberately requires the same successful member/cycle keys for
    all three fields so a missing dependency cannot silently turn into a SWE
    or precipitation substitute.
    """

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - requirements install numpy
        raise CFSv2Error("CFSv2 snowfall derivation requires numpy") from exc

    key_sets = {
        "2-m temperature": set(temperature_2m_grids),
        "850-mb temperature": set(temperature_850_grids),
        "precipitation": set(precipitation_grids),
    }
    keys = key_sets["2-m temperature"]
    if not keys:
        raise CFSv2Error("CFSv2 snowfall derivation received no complete members or cycles")
    if any(candidate != keys for candidate in key_sets.values()):
        details = "; ".join(
            f"{label}: {len(candidate)}" for label, candidate in key_sets.items()
        )
        raise CFSv2Error(
            "CFSv2 snowfall dependencies do not contain the same successful members/cycles "
            f"({details})"
        )

    first_key = sorted(keys)[0]
    reference = temperature_2m_grids[first_key]
    reference.assert_compatible(precipitation_grids[first_key], "snowfall precipitation")
    reference.assert_compatible(temperature_850_grids[first_key], "snowfall 850-mb temperature")
    shape = (len(reference.lats), len(reference.lons))
    member_lwe = []
    for key in sorted(keys):
        temperature_2m = temperature_2m_grids[key]
        temperature_850 = temperature_850_grids[key]
        precipitation = precipitation_grids[key]
        reference.assert_compatible(temperature_2m, f"snowfall 2-m temperature {key}")
        reference.assert_compatible(temperature_850, f"snowfall 850-mb temperature {key}")
        reference.assert_compatible(precipitation, f"snowfall precipitation {key}")
        t2m = np.asarray(temperature_2m.values, dtype=float)
        t850 = np.asarray(temperature_850.values, dtype=float)
        prate_inches = np.asarray(precipitation.values, dtype=float)
        if t2m.shape != shape or t850.shape != shape or prate_inches.shape != shape:
            raise CFSv2Error(f"CFSv2 snowfall dependency {key} has an inconsistent grid shape")
        valid = np.isfinite(t2m) & np.isfinite(t850) & np.isfinite(prate_inches)
        phase_temperature_c = np.maximum(t2m - 273.15, t850 - 273.15)
        coefficient, slope, midpoint, offset = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON[
            snowfall_phase_season(target)
        ]
        snow_fraction = np.clip(
            coefficient * (np.tanh(slope * (phase_temperature_c - midpoint)) - offset) / 100.0,
            0.0,
            1.0,
        )
        member_lwe.append(
            np.where(valid, np.maximum(prate_inches, 0.0) * snow_fraction, np.nan)
        )

    member_values = np.asarray(member_lwe, dtype=float)
    valid_counts = np.sum(np.isfinite(member_values), axis=0)
    totals = np.nansum(member_values, axis=0)
    means = np.divide(
        totals,
        valid_counts,
        out=np.full(valid_counts.shape, np.nan, dtype=float),
        where=valid_counts > 0,
    )
    if not np.isfinite(means).any():
        raise CFSv2Error("CFSv2 snowfall derivation produced no finite values")
    phase_season = snowfall_phase_season(target)
    coefficient, slope, midpoint, offset = SNOWFALL_DAI_LAND_PARAMS_BY_SEASON[phase_season]
    diagnostics = {
        "member_or_cycle_count": len(keys),
        "valid_member_count_min": int(valid_counts.min()),
        "valid_member_count_max": int(valid_counts.max()),
        "valid_member_fraction_min": round(float(valid_counts.min() / len(keys)), 4),
        "snow_fraction": {
            "method": "Dai_2008_land_seasonal_hyperbolic_tangent",
            "season": phase_season,
            "parameters": {
                "a_percent": coefficient,
                "b_per_c": slope,
                "c_c": midpoint,
                "d": offset,
            },
            "phase_temperature": "max(2-m, 850-hPa)",
        },
        "precipitation_input": "monthly total liquid-water equivalent in inches",
        "temperature_input": "absolute monthly mean Kelvin fields",
        "regridding": "850-mb pressure grid nearest-neighbor regridded to the FLXF Gaussian grid",
    }
    return Grid(reference.lons[:], reference.lats[:], means.tolist()), diagnostics


# Baxter et al. (2005) published objectively analyzed mean SLR contours for
# midwinter (Dec-Feb) and late winter (Mar-Apr). The interactive CIPS page is
# a legacy image map rather than a numeric service, so retain a compact,
# versioned set of representative contour anchors here and interpolate them
# deterministically. Values and locations follow Figs. 7-8 and are deliberately
# bounded to the published 8:1-18:1 CONUS range. This is a climatological snow
# depth estimate, not a storm-scale microphysics diagnosis.
CIPS_SLR_SOURCE_URL = "https://doi.org/10.1175/WAF856.1"
CIPS_SLR_INTERACTIVE_URL = "https://www.eas.slu.edu/CIPS/SLR/slrmap.htm"
CIPS_SLR_BASELINE_YEARS = "1971-2000"
CIPS_SLR_MIN = 8.0
CIPS_SLR_MAX = 18.0
CIPS_SLR_ANCHORS = {
    "DJF": (
        (-124.0, 47.0, 9.0), (-123.0, 43.5, 10.0), (-122.0, 39.5, 10.0),
        (-119.5, 35.5, 9.0), (-116.5, 40.5, 12.0), (-113.5, 45.0, 16.0),
        (-109.0, 47.0, 17.0), (-107.0, 43.0, 18.0), (-106.0, 39.0, 17.0),
        (-111.0, 34.5, 11.0), (-106.0, 34.5, 14.0), (-102.0, 35.0, 14.0),
        (-100.5, 47.0, 17.0), (-99.0, 42.5, 15.0), (-98.0, 38.0, 13.0),
        (-96.0, 32.0, 14.0), (-93.0, 46.0, 16.0), (-89.0, 44.5, 16.0),
        (-87.0, 42.5, 16.0), (-84.5, 44.5, 17.0), (-90.0, 39.0, 12.0),
        (-84.0, 39.5, 12.0), (-80.0, 42.5, 15.0), (-76.5, 43.0, 16.0),
        (-72.5, 44.0, 15.0), (-69.5, 45.5, 15.0), (-73.5, 41.0, 11.0),
        (-76.0, 39.0, 10.0), (-80.0, 37.5, 11.0), (-82.0, 34.5, 9.0),
        (-88.0, 35.0, 10.0), (-92.0, 33.0, 9.0),
    ),
    "MAM": (
        (-124.0, 47.0, 9.0), (-123.0, 43.5, 9.0), (-122.0, 39.5, 10.0),
        (-119.0, 35.5, 8.0), (-117.0, 42.0, 13.0), (-113.5, 46.0, 16.0),
        (-108.0, 45.0, 15.0), (-107.0, 40.0, 15.0), (-111.0, 34.5, 9.0),
        (-106.0, 34.5, 14.0), (-100.0, 46.5, 12.0), (-98.0, 38.5, 11.0),
        (-93.0, 45.0, 12.0), (-85.0, 44.0, 13.0), (-90.0, 39.0, 11.0),
        (-84.0, 39.0, 11.0), (-77.0, 42.5, 13.0), (-71.0, 45.0, 13.0),
        (-72.0, 42.0, 11.0), (-74.5, 39.5, 9.0), (-80.0, 37.0, 11.0),
        (-82.5, 34.5, 8.0), (-88.0, 35.0, 10.0), (-93.0, 34.0, 10.0),
    ),
}


def cips_slr_season(target: str) -> str:
    """Return the available CIPS seasonal SLR contour set for a YYYYMM target."""

    month = dt.datetime.strptime(target, "%Y%m").month
    if month in (12, 1, 2):
        return "DJF"
    if month == 3:
        return "MAM"
    raise CFSv2Error(
        "CFSv2 climatology-adjusted snowfall accumulation is supported only "
        "for December through March"
    )


def cips_climatological_slr_grid(grid: Grid, target: str) -> tuple[Grid, dict[str, object]]:
    """Interpolate the published CIPS seasonal mean SLR contour anchors."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - requirements install numpy
        raise CFSv2Error("CFSv2 snowfall accumulation requires numpy") from exc

    season = cips_slr_season(target)
    anchors = np.asarray(CIPS_SLR_ANCHORS[season], dtype=float)
    longitudes, latitudes = np.meshgrid(
        np.asarray([_normalize_lon(value) for value in grid.lons], dtype=float),
        np.asarray(grid.lats, dtype=float),
    )
    # A 175-km floor prevents a contour anchor from becoming a pixel-scale
    # bullseye on the coarse FLXF grid. Longitude distance is latitude-scaled.
    latitude_midpoint = np.deg2rad((latitudes[..., None] + anchors[:, 1]) / 2.0)
    dx_km = (
        (longitudes[..., None] - anchors[:, 0])
        * np.cos(latitude_midpoint)
        * 111.32
    )
    dy_km = (latitudes[..., None] - anchors[:, 1]) * 110.57
    distance_squared = dx_km * dx_km + dy_km * dy_km
    weights = 1.0 / (distance_squared + 175.0**2)
    ratios = np.sum(weights * anchors[:, 2], axis=-1) / np.sum(weights, axis=-1)
    ratios = np.clip(ratios, CIPS_SLR_MIN, CIPS_SLR_MAX)
    diagnostics = {
        "method": "Baxter_2005_CIPS_published_contour_anchor_IDW",
        "source": CIPS_SLR_SOURCE_URL,
        "interactive_reference": CIPS_SLR_INTERACTIVE_URL,
        "baseline_years": CIPS_SLR_BASELINE_YEARS,
        "season": season,
        "anchor_count": int(anchors.shape[0]),
        "interpolation": "inverse-distance weighting with 175-km distance floor",
        "ratio_bounds": [CIPS_SLR_MIN, CIPS_SLR_MAX],
        "limitations": "seasonal climatological mean; does not resolve storm-scale crystal growth, melting, wind compaction, or settling",
    }
    return Grid(grid.lons[:], grid.lats[:], ratios.tolist()), diagnostics


def derive_snowfall_accumulation_grid(
    snowfall_lwe_grid: Grid,
    target: str,
) -> tuple[Grid, dict[str, object]]:
    """Convert monthly snowfall LWE to estimated snow depth using CIPS SLR."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - requirements install numpy
        raise CFSv2Error("CFSv2 snowfall accumulation requires numpy") from exc

    ratio_grid, diagnostics = cips_climatological_slr_grid(snowfall_lwe_grid, target)
    snowfall_lwe_grid.assert_compatible(ratio_grid, "CIPS SLR")
    lwe = np.asarray(snowfall_lwe_grid.values, dtype=float)
    ratios = np.asarray(ratio_grid.values, dtype=float)
    valid = np.isfinite(lwe) & np.isfinite(ratios)
    snowfall_depth = np.where(valid, np.maximum(lwe, 0.0) * ratios, np.nan)
    if not np.isfinite(snowfall_depth).any():
        raise CFSv2Error("CFSv2 snowfall accumulation produced no finite values")
    diagnostics = dict(diagnostics)
    diagnostics["formula"] = "monthly snowfall LWE (in) multiplied by climatological SLR"
    diagnostics["input_units"] = "inches liquid-water equivalent"
    diagnostics["output_units"] = "inches estimated snow depth"
    return Grid(
        snowfall_lwe_grid.lons[:],
        snowfall_lwe_grid.lats[:],
        snowfall_depth.tolist(),
    ), diagnostics


def _bicubic_sample_grid(
    source_lons,
    source_lats,
    field,
    longitude_values,
    latitude_values,
    smoothing_sigma: float = 0.0,
):
    """Smoothly sample a complete global grid for display rendering."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - target installs requirements.txt
        raise CFSv2Error("bicubic map resampling requires numpy") from exc

    longitude_axis = np.asarray(source_lons, dtype=float)
    latitude_axis = np.asarray(source_lats, dtype=float)
    source_field = np.asarray(field, dtype=float)
    if source_field.shape != (latitude_axis.size, longitude_axis.size):
        raise CFSv2Error("bicubic source field does not match its coordinate axes")
    if latitude_axis.size < 4 or longitude_axis.size < 4:
        raise CFSv2Error("bicubic map resampling requires at least four grid points per axis")
    if not np.isfinite(source_field).all():
        raise CFSv2Error("bicubic map resampling requires a complete finite source grid")
    if not math.isfinite(smoothing_sigma) or smoothing_sigma < 0.0:
        raise CFSv2Error("bicubic source smoothing must be a finite non-negative value")

    render_field = source_field
    if smoothing_sigma > 0.0:
        # Smooth a rendering copy, not the decoded or averaged Grid. Longitude
        # wraps at the dateline; latitude stops at the poles. A sub-cell sigma
        # suppresses coarse-grid contour facets while retaining the synoptic
        # anomaly centers and the original value range.
        radius = max(1, int(math.ceil(3.0 * smoothing_sigma)))
        offsets = np.arange(-radius, radius + 1, dtype=int)
        kernel = np.exp(-0.5 * (offsets / smoothing_sigma) ** 2)
        kernel /= np.sum(kernel)
        longitude_smoothed = sum(
            weight * np.roll(source_field, int(offset), axis=1)
            for offset, weight in zip(offsets, kernel, strict=True)
        )
        latitude_padded = np.pad(
            longitude_smoothed,
            ((radius, radius), (0, 0)),
            mode="edge",
        )
        render_field = sum(
            weight
            * latitude_padded[
                radius + int(offset):radius + int(offset) + latitude_axis.size,
                :,
            ]
            for offset, weight in zip(offsets, kernel, strict=True)
        )

    def cubic(p0, p1, p2, p3, fraction):
        fraction_squared = fraction * fraction
        return 0.5 * (
            2.0 * p1
            + (-p0 + p2) * fraction
            + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * fraction_squared
            + (-p0 + 3.0 * p1 - 3.0 * p2 + p3)
            * fraction_squared
            * fraction
        )

    # WRIT grids are global. Cyclic longitude indices keep the cubic stencil
    # continuous across the dateline without copying or altering the grid.
    wrapped_longitudes = (
        np.mod(np.asarray(longitude_values, dtype=float) - longitude_axis[0], 360.0)
        + longitude_axis[0]
    )
    clipped_latitudes = np.clip(
        np.asarray(latitude_values, dtype=float),
        latitude_axis[0],
        latitude_axis[-1],
    )
    longitude_right = np.searchsorted(longitude_axis, wrapped_longitudes, side="right")
    longitude_wrap = longitude_right >= longitude_axis.size
    lon_index_1 = np.where(
        longitude_wrap,
        longitude_axis.size - 1,
        np.maximum(longitude_right - 1, 0),
    )
    lon_index_2 = np.where(longitude_wrap, 0, longitude_right)
    lon_index_0 = np.mod(lon_index_1 - 1, longitude_axis.size)
    lon_index_3 = np.mod(lon_index_2 + 1, longitude_axis.size)
    left_longitude = longitude_axis[lon_index_1]
    right_longitude = np.where(
        longitude_wrap,
        longitude_axis[0] + 360.0,
        longitude_axis[lon_index_2],
    )
    longitude_fraction = np.divide(
        wrapped_longitudes - left_longitude,
        right_longitude - left_longitude,
        out=np.zeros_like(wrapped_longitudes),
        where=(right_longitude - left_longitude) != 0.0,
    )

    latitude_right = np.searchsorted(latitude_axis, clipped_latitudes, side="right")
    latitude_right = np.clip(latitude_right, 1, latitude_axis.size - 1)
    lat_index_1 = latitude_right - 1
    lat_index_2 = latitude_right
    lat_index_0 = np.maximum(lat_index_1 - 1, 0)
    lat_index_3 = np.minimum(lat_index_2 + 1, latitude_axis.size - 1)
    left_latitude = latitude_axis[lat_index_1]
    right_latitude = latitude_axis[lat_index_2]
    latitude_fraction = np.divide(
        clipped_latitudes - left_latitude,
        right_latitude - left_latitude,
        out=np.zeros_like(clipped_latitudes),
        where=(right_latitude - left_latitude) != 0.0,
    )

    longitude_rows = [
        cubic(
            render_field[lat_index, lon_index_0],
            render_field[lat_index, lon_index_1],
            render_field[lat_index, lon_index_2],
            render_field[lat_index, lon_index_3],
            longitude_fraction,
        )
        for lat_index in (lat_index_0, lat_index_1, lat_index_2, lat_index_3)
    ]
    sampled = cubic(
        longitude_rows[0],
        longitude_rows[1],
        longitude_rows[2],
        longitude_rows[3],
        latitude_fraction,
    )
    # Cubic interpolation can overshoot around a sharp local gradient. Keep
    # the display interpolation inside the observed source-grid extrema.
    return np.clip(sampled, float(np.min(source_field)), float(np.max(source_field)))


def get_product_spec(product: str) -> dict:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        available = ", ".join(sorted(PRODUCT_SPECS))
        raise CFSv2Error(f"unsupported CFSv2 product {product!r}; choose from {available}") from exc


def product_dependency_names(product: str) -> tuple[str, ...]:
    """Return the raw CFSv2 products required to build ``product``."""

    spec = get_product_spec(product)
    dependencies = spec.get("dependencies") or (product,)
    return tuple(dict.fromkeys(str(dependency) for dependency in dependencies))


def selected_product(args: argparse.Namespace) -> tuple[str, dict, bool]:
    product = getattr(args, "product", PRODUCT_HEIGHT_ANOMALY)
    if getattr(args, "absolute", False):
        if product not in {PRODUCT_HEIGHT_ANOMALY, PRODUCT_HEIGHT_ABSOLUTE}:
            raise CFSv2Error("--absolute is only valid with the 500mb_height_absolute product")
        product = PRODUCT_HEIGHT_ABSOLUTE
    spec = get_product_spec(product)
    return product, spec, product == PRODUCT_HEIGHT_ABSOLUTE


def anomaly_style(
    product_spec: dict,
    seasonal: bool = False,
) -> tuple[float, float, Sequence[float], Sequence[str]]:
    """Return the fixed comparable scale for one anomaly product and period."""

    period_prefix = "seasonal" if seasonal else "monthly"
    period_min_key = f"{period_prefix}_anomaly_min"
    if period_min_key in product_spec:
        return (
            float(product_spec[period_min_key]),
            float(product_spec[f"{period_prefix}_anomaly_max"]),
            product_spec.get(f"{period_prefix}_anomaly_ticks", []),
            product_spec.get(f"{period_prefix}_anomaly_palette", ANOMALY_PALETTE),
        )
    if "anomaly_min" in product_spec:
        return (
            float(product_spec["anomaly_min"]),
            float(product_spec["anomaly_max"]),
            product_spec.get("anomaly_ticks", []),
            product_spec.get("anomaly_palette", ANOMALY_PALETTE),
        )
    if product_spec["name"] == PRODUCT_PRECIPITATION_ANOMALY:
        if seasonal:
            return (
                PRECIP_SEASONAL_ANOMALY_MIN_IN,
                PRECIP_SEASONAL_ANOMALY_MAX_IN,
                PRECIP_SEASONAL_ANOMALY_TICKS,
                PRECIP_ANOMALY_PALETTE,
            )
        return (
            PRECIP_MONTHLY_ANOMALY_MIN_IN,
            PRECIP_MONTHLY_ANOMALY_MAX_IN,
            PRECIP_MONTHLY_ANOMALY_TICKS,
            PRECIP_ANOMALY_PALETTE,
        )
    if product_spec["name"] == PRODUCT_SWE_ANOMALY:
        return (
            SWE_ANOMALY_MIN_IN,
            SWE_ANOMALY_MAX_IN,
            SWE_ANOMALY_TICKS,
            SWE_ANOMALY_PALETTE,
        )
    return ANOMALY_MIN_M, ANOMALY_MAX_M, ANOMALY_TICKS, ANOMALY_PALETTE


def absolute_style(
    product_spec: dict,
    seasonal: bool = False,
) -> tuple[Sequence[float], Sequence[float], Sequence[str]] | None:
    """Return an optional fixed categorical scale for a non-anomaly product."""

    period_prefix = "seasonal" if seasonal else "monthly"
    bounds = product_spec.get(f"{period_prefix}_absolute_bounds")
    if bounds is None:
        return None
    ticks = product_spec.get(f"{period_prefix}_absolute_ticks", bounds)
    palette = product_spec.get(f"{period_prefix}_absolute_palette")
    if palette is None:
        raise CFSv2Error(f"{product_spec['name']} fixed absolute scale has no palette")
    return bounds, ticks, palette


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def parse_init(value: str) -> str:
    if not re.fullmatch(r"\d{10}", value):
        raise CFSv2Error("--init must be YYYYMMDDHH or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d%H")
    except ValueError as exc:
        raise CFSv2Error(f"invalid CFSv2 initialization time: {value}") from exc
    if parsed.hour not in (0, 6, 12, 18):
        raise CFSv2Error("CFSv2 initialization hour must be 00, 06, 12, or 18")
    return value


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise CFSv2Error(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise CFSv2Error(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise CFSv2Error(f"{label} cannot be empty")
    return result


def parse_seasonal_windows(value: str) -> list[list[int]]:
    """Parse one or more semicolon-separated consecutive lead windows."""

    windows: list[list[int]] = []
    for index, raw_window in enumerate(str(value or "").split(";"), start=1):
        if not raw_window.strip():
            continue
        leads = parse_int_list(raw_window, f"seasonal window {index}", 1, 9)
        if leads != list(range(min(leads), max(leads) + 1)):
            raise CFSv2Error("each --seasonal-window group must contain consecutive lead months")
        if leads not in windows:
            windows.append(leads)
    return windows


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
    """Use standard meteorological season shorthand for three-month windows."""

    start = dt.datetime.strptime(first_target, "%Y%m")
    end = dt.datetime.strptime(last_target, "%Y%m")
    season = {
        (12, 2): f"DJF {start.year}\u2013{end.year % 100:02d}",
        (1, 3): f"JFM {end.year}",
        (3, 5): f"MAM {end.year}",
        (6, 8): f"JJA {end.year}",
        (9, 11): f"SON {end.year}",
    }.get((start.month, end.month))
    if season and ((start.month == 12 and end.year == start.year + 1) or end.year == start.year):
        return season
    if start.year == end.year:
        return f"{start:%b}\u2013{end:%b %Y}"
    return f"{start:%b %Y}\u2013{end:%b %Y}"


def listed_cycle_inits(
    root: str = NOMADS_ROOT,
    now: dt.datetime | None = None,
) -> list[str]:
    """Return usable CFSv2 cycles listed by NOMADS, newest first."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required when --init latest is used") from exc

    try:
        response = requests.get(root, timeout=(20, 60))
        response.raise_for_status()
    except Exception as exc:
        raise CFSv2Error(f"could not read the NOMADS CFSv2 directory: {exc}") from exc
    dates = sorted(set(re.findall(r'href="cfs\.(\d{8})/"', response.text)), reverse=True)
    if not dates:
        raise CFSv2Error("could not find a cfs.YYYYMMDD cycle in the NOMADS index")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(dt.timezone.utc).replace(tzinfo=None)
    candidates = []
    for date_text in dates:
        for hour in reversed(CFS_CYCLE_HOURS):
            candidate = f"{date_text}{hour:02d}"
            if dt.datetime.strptime(candidate, "%Y%m%d%H") <= current:
                candidates.append(candidate)
    return candidates


def filter_mature_cycle_inits(
    candidate_inits: Sequence[str],
    minimum_age_minutes: int,
    *,
    now: dt.datetime | None = None,
) -> list[str]:
    """Keep listed cycles old enough for their delayed monthly files.

    NOMADS creates each cycle directory well before ``monthly_grib_01`` is
    complete. Scheduled full-suite runs use this filter so their bounded
    readiness retry follows the cycle that should now be publishing instead
    of waiting on the next, much younger cycle directory.
    """

    try:
        minimum_age = int(minimum_age_minutes)
    except (TypeError, ValueError) as exc:
        raise CFSv2Error("CFSv2 minimum cycle age must be an integer number of minutes") from exc
    if minimum_age < 0:
        raise CFSv2Error("CFSv2 minimum cycle age cannot be negative")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(dt.timezone.utc).replace(tzinfo=None)
    cutoff = current - dt.timedelta(minutes=minimum_age)
    mature = []
    for candidate in candidate_inits:
        try:
            initialized = dt.datetime.strptime(candidate, "%Y%m%d%H")
        except ValueError as exc:
            raise CFSv2Error(f"invalid CFSv2 cycle initialization: {candidate}") from exc
        if initialized <= cutoff:
            mature.append(candidate)
    return mature


def discover_latest_init(root: str = NOMADS_ROOT) -> str:
    """Select the newest listed cycle from the official NOMADS directory."""

    candidates = listed_cycle_inits(root)
    if not candidates:
        raise CFSv2Error("NOMADS listed no usable CFSv2 cycle")
    return candidates[0]


def discover_latest_ready_init(
    product_names: Sequence[str],
    leads: Sequence[int],
    root: str = NOMADS_ROOT,
    *,
    candidate_inits: Sequence[str] | None = None,
    probe: Callable[[str], int | None] | None = None,
    wait_for_latest_minutes: int = 0,
    retry_seconds: int = 60,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> str:
    """Select the newest cycle whose requested monthly files are published.

    NOMADS lists a cycle directory before all of that cycle's monthly GRIB2
    files are necessarily available.  A rolling blend can use retained state
    for older cycles, but the selected anchor must have every requested target
    file ready or the run will be incomplete from the outset.

    When ``wait_for_latest_minutes`` is positive, the newest listed cycle is
    retried before falling back to an older complete cycle.  This handles the
    normal gap between a cycle appearing in the NOMADS directory and all of
    its monthly files being published, without ever selecting a partial
    anchor.  ``candidate_inits``, ``probe``, and the timing functions are
    injectable so the readiness policy can be tested without making network
    requests or sleeping.
    """

    if isinstance(product_names, str):
        product_names = [value.strip() for value in product_names.split(",") if value.strip()]
    normalized_products = list(dict.fromkeys(product_names))
    if not normalized_products:
        raise CFSv2Error("at least one CFSv2 product is required for readiness checks")
    dependency_names = []
    for product_name in normalized_products:
        for dependency_name in product_dependency_names(product_name):
            if dependency_name not in dependency_names:
                dependency_names.append(dependency_name)
    product_specs = [get_product_spec(product_name) for product_name in dependency_names]

    if isinstance(leads, str):
        leads = [value.strip() for value in leads.split(",") if value.strip()]
    try:
        normalized_leads = sorted({int(lead) for lead in leads})
    except (TypeError, ValueError) as exc:
        raise CFSv2Error("CFSv2 readiness leads must be integers") from exc
    if not normalized_leads or any(lead < 1 or lead > 9 for lead in normalized_leads):
        raise CFSv2Error("CFSv2 readiness leads must be between 1 and 9")
    try:
        wait_seconds = max(0, int(wait_for_latest_minutes)) * 60
        retry_seconds = int(retry_seconds)
    except (TypeError, ValueError) as exc:
        raise CFSv2Error("CFSv2 readiness retry timing must be integers") from exc
    if retry_seconds < 1:
        raise CFSv2Error("CFSv2 readiness retry interval must be at least one second")

    if probe is None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - minimal environments only
            raise CFSv2Error("requests is required for CFSv2 readiness checks") from exc

        session = requests.Session()

        def probe(url: str) -> int | None:
            try:
                response = session.head(
                    url,
                    allow_redirects=True,
                    timeout=(15, 45),
                )
                status = response.status_code
                response.close()
                if 200 <= status < 300:
                    return status
                # NOMADS/Akamai can return a bare 302 to HEAD from hosted
                # runners even when the same object is ready. A one-byte GET
                # verifies the object without transferring the GRIB payload.
                response = session.get(
                    url,
                    headers={"Range": "bytes=0-0"},
                    allow_redirects=True,
                    stream=True,
                    timeout=(15, 45),
                )
                status = response.status_code
                response.close()
                return status
            except requests.RequestException:
                return None

    candidates = list(candidate_inits) if candidate_inits is not None else listed_cycle_inits(root)
    if not candidates:
        raise CFSv2Error("NOMADS listed no usable CFSv2 cycle")

    def required_urls(candidate: str) -> list[str]:
        return sorted(
            {
                cfs_file_url(
                    candidate,
                    ROLLING_MEMBER_DEFAULT,
                    target_month(candidate, lead),
                    product_spec["source_kind"],
                    root=root,
                )
                for product_spec in product_specs
                for lead in normalized_leads
            }
        )

    def is_ready(candidate: str) -> bool:
        urls = required_urls(candidate)
        statuses = [(url, probe(url)) for url in urls]
        missing = [(url, status) for url, status in statuses if status is None or not 200 <= status < 300]
        if not missing:
            print(
                f"CFSv2 readiness selected {candidate}: {len(urls)} required monthly files are available",
                file=sys.stderr,
            )
            return True
        examples = ", ".join(
            f"{url.rsplit('/', 1)[-1]}={status or 'request-error'}"
            for url, status in missing[:3]
        )
        print(
            f"CFSv2 readiness skipped {candidate}: {len(missing)}/{len(urls)} required files unavailable ({examples})",
            file=sys.stderr,
        )
        return False

    newest = candidates[0]
    deadline = clock_fn() + wait_seconds
    while True:
        if is_ready(newest):
            return newest
        remaining = deadline - clock_fn()
        if remaining <= 0:
            break
        delay = min(float(retry_seconds), remaining)
        print(
            f"CFSv2 readiness waiting for newest listed cycle {newest}; retrying in {delay:.0f} seconds",
            file=sys.stderr,
        )
        sleep_fn(delay)

    for candidate in candidates[1:]:
        if is_ready(candidate):
            return candidate

    product_label = ", ".join(normalized_products)
    raise CFSv2Error(
        f"no ready CFSv2 cycle found for {product_label} leads {','.join(map(str, normalized_leads))}"
    )


def find_wgrib2(explicit: str) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CFSV2_WGRIB2"):
        candidates.append(os.environ["CFSV2_WGRIB2"])
    found = shutil.which("wgrib2")
    if found:
        candidates.append(found)
    candidates.append(r"C:\wgrib2\wgrib2.exe")
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    raise CFSv2Error(
        "wgrib2 was not found; install it or set CFSV2_WGRIB2/--wgrib2 to the executable path"
    )


def cfs_file_url(
    init: str,
    member: int,
    target: str,
    source_kind: str = "pgbf",
    root: str = NOMADS_ROOT,
) -> str:
    date_text, hour_text = init[:8], init[8:]
    filename = f"{source_kind}.{member:02d}.{init}.{target}.avrg.grib.grb2"
    return urljoin(
        root.rstrip("/") + "/",
        f"cfs.{date_text}/{hour_text}/monthly_grib_{member:02d}/{filename}",
    )


def cached_source_path(
    cache_dir: Path,
    init: str,
    member: int,
    target: str,
    source_kind: str = "pgbf",
) -> Path:
    filename = Path(cfs_file_url(init, member, target, source_kind)).name
    return cache_dir / init / f"member_{member:02d}" / filename


def ncei_calibration_url(init: str, lead: int, source_kind: str = "pgbf") -> str:
    month, day, hour = init[4:6], init[6:8], init[8:]
    filename = f"{source_kind}.{month}.{day}.{hour}.l{lead:02d}.fclm.{NCEI_CALIBRATION_YEARS.replace('-', '.')}.grb2"
    root = NCEI_FLUX_CALIBRATION_ROOT if source_kind == "flxf" else NCEI_CALIBRATION_ROOT
    return urljoin(root, f"{month}/{filename}")


def cached_calibration_path(
    cache_dir: Path,
    init: str,
    lead: int,
    source_kind: str = "pgbf",
) -> Path:
    return cache_dir / "calibration" / source_kind / init / Path(
        ncei_calibration_url(init, lead, source_kind)
    ).name


def cached_calibration_fallback(
    cache_dir: Path,
    init: str,
    lead: int,
    source_kind: str = "pgbf",
    max_age_days: int = NCEI_CALIBRATION_FALLBACK_MAX_DAYS,
) -> tuple[Path, str] | None:
    """Find a recent prior-cycle calibration when NCEI has a transient outage.

    The fallback is deliberately restricted to the same initialization month,
    preferably the same cycle hour, and a short age window.  Callers must label
    the result as a fallback in the manifest and map header.
    """

    requested_time = dt.datetime.strptime(init, "%Y%m%d%H")
    calibration_root = cache_dir / "calibration" / source_kind
    if not calibration_root.exists():
        return None

    candidates: list[tuple[tuple[bool, bool, dt.datetime], Path, str]] = []
    for candidate_dir in calibration_root.iterdir():
        if not candidate_dir.is_dir():
            continue
        candidate_init = candidate_dir.name
        try:
            candidate_time = dt.datetime.strptime(candidate_init, "%Y%m%d%H")
        except ValueError:
            continue
        if candidate_time >= requested_time:
            continue
        if requested_time - candidate_time > dt.timedelta(days=max_age_days):
            continue
        candidate_path = cached_calibration_path(cache_dir, candidate_init, lead, source_kind)
        if not candidate_path.exists() or candidate_path.stat().st_size <= 0:
            continue
        same_month = candidate_init[4:6] == init[4:6]
        same_hour = candidate_init[8:] == init[8:]
        candidates.append(((same_month, same_hour, candidate_time), candidate_path, candidate_init))

    if not candidates:
        return None
    _, path, candidate_init = max(candidates, key=lambda item: item[0])
    return path, candidate_init


def load_ncei_calibration(
    *,
    cache_dir: Path,
    init: str,
    lead: int,
    source_kind: str,
    request_delay: float,
    last_request: float,
    allow_stale: bool,
) -> tuple[Path, str, bool, float, str | None]:
    """Download the matching calibration, optionally retaining a recent cache.

    Returns the path, initialization represented by that path, download flag,
    updated request clock, and the original error when a fallback was used.
    """

    requested_url = ncei_calibration_url(init, lead, source_kind)
    requested_path = cached_calibration_path(cache_dir, init, lead, source_kind)
    try:
        downloaded, last_request = download_file(
            requested_url,
            requested_path,
            request_delay,
            last_request,
            attempts=NCEI_CALIBRATION_DOWNLOAD_ATTEMPTS,
            timeout=(30, 300),
        )
        return requested_path, init, downloaded, last_request, None
    except Exception as exc:
        if not allow_stale:
            raise
        fallback = cached_calibration_fallback(cache_dir, init, lead, source_kind)
        if fallback is None:
            raise
        fallback_path, fallback_init = fallback
        fallback_url = ncei_calibration_url(fallback_init, lead, source_kind)
        print(
            f"CFSv2 calibration unavailable for {requested_url} ({exc}); "
            f"using cached prior cycle {fallback_url}"
        )
        return fallback_path, fallback_init, False, last_request, str(exc)


def rolling_cycle_inits(end_init: str, cycle_count: int) -> list[str]:
    """Return the most recent six-hourly cycles, oldest first."""

    if cycle_count < 1:
        raise CFSv2Error("rolling cycle count must be positive")
    end_date = dt.datetime.strptime(end_init, "%Y%m%d%H")
    return [
        (end_date - dt.timedelta(hours=6 * offset)).strftime("%Y%m%d%H")
        for offset in range(cycle_count - 1, -1, -1)
    ]


def lead_for_target(init: str, target: str) -> int:
    """Find the monthly lead that reaches a fixed target month."""

    for lead in range(1, 10):
        if target_month(init, lead) == target:
            return lead
    raise CFSv2Error(f"CFSv2 cycle {init} has no 1-9 month lead for target {target}")


def default_winter_snowfall_windows(init: str) -> tuple[list[int], list[list[int]]]:
    """Return Dec-Mar leads plus DJF/JFM windows for the next forecast winter."""

    parsed_init = dt.datetime.strptime(parse_init(init), "%Y%m%d%H")
    winter_start_year = parsed_init.year if parsed_init.month <= 11 else parsed_init.year + 1
    target_months = [
        f"{winter_start_year:04d}12",
        f"{winter_start_year + 1:04d}01",
        f"{winter_start_year + 1:04d}02",
        f"{winter_start_year + 1:04d}03",
    ]
    try:
        leads = [lead_for_target(init, target) for target in target_months]
    except CFSv2Error as exc:
        raise CFSv2Error(
            "a complete December-March snowfall window is outside this cycle's 1-9 month horizon"
        ) from exc
    return leads, [leads[:3], leads[1:]]


def rolling_state_path(
    state_dir: Path,
    init: str,
    member: int,
    target: str,
    state_tag: str = "hgt500",
) -> Path:
    if state_tag == "hgt500":
        # Preserve the original height-state layout so existing rolling cache
        # entries remain usable after adding the FLXF product.
        return state_dir / target / f"hgt500.{init}.m{member:02d}.csv.gz"
    return state_dir / state_tag / target / f"{state_tag}.{init}.m{member:02d}.csv.gz"


def write_grid_state(grid: Grid, path: Path) -> None:
    """Persist a decoded grid compactly so it survives the 7-day NOMADS rotation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("lon", "lat", "value"))
        for lat, row in zip(grid.lats, grid.values):
            for lon, value in zip(grid.lons, row):
                writer.writerow((lon, lat, value))
    temporary.replace(path)


def read_grid_state(path: Path) -> Grid:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(path))


def download_file(
    url: str,
    destination: Path,
    request_delay: float,
    last_request: float,
    *,
    attempts: int = 1,
    timeout: tuple[int, int] = (30, 300),
) -> tuple[bool, float]:
    if destination.exists() and destination.stat().st_size > 0:
        return False, last_request
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required to download CFSv2 files") from exc

    elapsed = time.monotonic() - last_request if last_request else request_delay
    if last_request and elapsed < request_delay:
        time.sleep(request_delay - elapsed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, stream=True, timeout=timeout)
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            if partial.stat().st_size == 0:
                raise CFSv2Error(f"empty download from {url}")
            partial.replace(destination)
            return True, time.monotonic()
        except Exception:
            if partial.exists():
                partial.unlink()
            if attempt >= attempts:
                raise
            time.sleep(min(30.0, float(2 ** (attempt - 1))))
    raise AssertionError("download retry loop did not return or raise")


def common_reference_path(directory: Path, target: str) -> Path:
    return directory / COMMON_REFERENCE_FILENAME.format(target=target)


def common_reference_url(root: str, target: str) -> str:
    return urljoin(root.rstrip("/") + "/", common_reference_path(Path("."), target).name)


def load_common_reference(
    target: str,
    directory: Path | None,
    url_root: str,
    request_delay: float,
    last_request: float,
) -> tuple[Grid, Path, str, bool, float]:
    """Load the published CanSIPS 1991-2020 reference grid for a target month."""

    if directory is None and not url_root:
        raise CFSv2Error("a common-reference directory or URL is required")
    local_directory = directory or Path(".cache/common-reference")
    path = common_reference_path(local_directory, target)
    url = common_reference_url(url_root, target) if url_root else ""
    downloaded = False
    if not path.exists() or path.stat().st_size == 0:
        if not url:
            raise CFSv2Error(f"common 1991-2020 reference is missing for {target}: {path}")
        downloaded, last_request = download_file(
            url,
            path,
            request_delay,
            last_request,
            attempts=3,
            timeout=(30, 120),
        )
    try:
        grid = read_grid_state(path) if path.suffix == ".gz" else read_grid_csv(path)
    except Exception as exc:
        raise CFSv2Error(f"could not decode common 1991-2020 reference {path}: {exc}") from exc
    return grid, path, url, downloaded, last_request


def regrid_nearest(grid: Grid, lons: Sequence[float], lats: Sequence[float], label: str) -> Grid:
    """Regrid a smooth global reference field to the forecast axes by nearest point."""

    def lon_distance(left: float, right: float) -> float:
        difference = abs(left - right) % 360.0
        return min(difference, 360.0 - difference)

    lon_indices = [
        min(range(len(grid.lons)), key=lambda index: lon_distance(grid.lons[index], lon))
        for lon in lons
    ]
    lat_indices = [
        min(range(len(grid.lats)), key=lambda index: abs(grid.lats[index] - lat))
        for lat in lats
    ]
    if not lon_indices or not lat_indices:
        raise CFSv2Error(f"{label} reference grid has no usable axes")
    values = [
        [grid.values[lat_index][lon_index] for lon_index in lon_indices]
        for lat_index in lat_indices
    ]
    return Grid(list(lons), list(lats), values)


def _float_or_nan(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def _normalize_lon(value: float) -> float:
    lon = value % 360.0
    if lon > 180.0:
        lon -= 360.0
    return round(lon, 6)


def grid_from_rows(
    rows: Iterable[Sequence[str]],
    source: str,
    expected_shape: tuple[int, int] | None = None,
) -> Grid:
    points: dict[tuple[float, float], float] = {}
    for row in rows:
        if len(row) < 3:
            continue
        lon = _float_or_nan(row[-3])
        lat = _float_or_nan(row[-2])
        value = _float_or_nan(row[-1])
        if not all(math.isfinite(item) for item in (lon, lat)):
            continue
        points[(_normalize_lon(lon), round(lat, 6))] = value

    lons = sorted({lon for lon, _ in points})
    lats = sorted({lat for _, lat in points})
    if expected_shape and (len(lons), len(lats)) != expected_shape:
        expected_lons, expected_lats = expected_shape
        raise CFSv2Error(
            f"{source} did not decode the expected {expected_lons}x{expected_lats} grid "
            f"(got {len(lons)}x{len(lats)})"
        )
    if len(lons) < 2 or len(lats) < 2:
        raise CFSv2Error(f"{source} did not decode a usable longitude/latitude grid")
    values = []
    for lat in lats:
        row = []
        for lon in lons:
            if (lon, lat) not in points:
                raise CFSv2Error(f"{source} has a missing grid point at {lon},{lat}")
            row.append(points[(lon, lat)])
        values.append(row)
    return Grid(lons=lons, lats=lats, values=values)


def read_grid_csv(csv_path: Path, expected_shape: tuple[int, int] | None = None) -> Grid:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(csv_path), expected_shape)


def decode_grib(
    grib_path: Path,
    wgrib2: str,
    force: bool = False,
    match_pattern: str = ":HGT:500 mb:",
    cache_tag: str = "hgt500",
    expected_shape: tuple[int, int] | None = None,
) -> Grid:
    csv_path = grib_path.with_name(grib_path.name + f".{cache_tag}.csv")
    if force or not csv_path.exists() or csv_path.stat().st_size == 0:
        command = [wgrib2, str(grib_path), "-match", match_pattern, "-csv", str(csv_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "wgrib2 failed").strip()
            raise CFSv2Error(f"wgrib2 failed for {grib_path.name}: {detail[-800:]}")
    return read_grid_csv(csv_path, expected_shape)


def transform_grid(grid: Grid, transform: Callable[[float], float]) -> Grid:
    values = []
    for row in grid.values:
        values.append([transform(value) if math.isfinite(value) else math.nan for value in row])
    return Grid(grid.lons[:], grid.lats[:], values)


def monthly_precipitation_total_inches(grid: Grid, target: str) -> Grid:
    start = dt.datetime.strptime(target, "%Y%m")
    next_year, next_month = month_after(start.year, start.month, 1)
    end = dt.datetime(next_year, next_month, 1)
    seconds = (end - start).total_seconds()
    return transform_grid(grid, lambda value: value * seconds / 25.4)


def snow_water_equivalent_inches(grid: Grid) -> Grid:
    """Convert WEASD from kg m-2 (equivalent to mm of liquid water) to inches."""

    return transform_grid(grid, lambda value: value / 25.4)


def prepare_product_grid(grid: Grid, product_spec: dict, target: str) -> Grid:
    conversion_kind = product_spec.get("conversion_kind")
    if conversion_kind == "monthly_precipitation_total_inches":
        return monthly_precipitation_total_inches(grid, target)
    if conversion_kind == "snow_water_equivalent_inches":
        return snow_water_equivalent_inches(grid)
    if conversion_kind == "pascals_to_hectopascals":
        return transform_grid(grid, lambda value: value / 100.0)
    return grid


def mean_grids(grids: Sequence[Grid]) -> Grid:
    if not grids:
        raise CFSv2Error("cannot average an empty CFSv2 member set")
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "ensemble member")
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        mean_row = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [sample for sample in samples if math.isfinite(sample)]
            mean_row.append(sum(finite) / len(finite) if finite else math.nan)
        values.append(mean_row)
    return Grid(first.lons[:], first.lats[:], values)


def sum_grids(grids: Sequence[Grid]) -> Grid:
    if not grids:
        raise CFSv2Error("cannot sum an empty CFSv2 grid set")
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "seasonal member")
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        sum_row = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [sample for sample in samples if math.isfinite(sample)]
            sum_row.append(sum(finite) if finite else math.nan)
        values.append(sum_row)
    return Grid(first.lons[:], first.lats[:], values)


def decode_target_ensemble(
    args: argparse.Namespace,
    init: str,
    target: str,
    members: Sequence[int],
    rolling_inits: Sequence[str],
    cache_dir: Path,
    state_dir: Path,
    wgrib2: str,
    repo_root: Path,
    last_request: float,
    product_spec: dict,
    return_member_grids: bool = False,
) -> tuple:
    """Decode either the original single-cycle ensemble or a rolling blend."""

    source_kind = product_spec["source_kind"]

    def prepare_grid(grid: Grid) -> Grid:
        return prepare_product_grid(grid, product_spec, target)

    def source_metadata() -> dict:
        metadata = {
            "product": product_spec["name"],
            "source_kind": source_kind.upper(),
            "decoded_field": product_spec["raw_field"],
            "raw_units": product_spec["raw_units"],
            "units": product_spec["units"],
        }
        if product_spec.get("conversion"):
            metadata["conversion"] = product_spec["conversion"]
        return metadata

    grids: list[Grid] = []
    member_keys: list[str] = []
    source_files: list[dict] = []
    if rolling_inits:
        expected_count = len(rolling_inits)
        rolling_member = args.rolling_member
        for cycle in rolling_inits:
            cycle_lead = lead_for_target(cycle, target)
            url = cfs_file_url(cycle, rolling_member, target, source_kind)
            cache_path = cached_source_path(cache_dir, cycle, rolling_member, target, source_kind)
            state_path = rolling_state_path(
                state_dir,
                cycle,
                rolling_member,
                target,
                product_spec["state_tag"],
            )
            source_file = {
                "initialization": cycle,
                "initialization_utc": iso_utc(dt.datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
                "lead_month": cycle_lead,
                "member": rolling_member,
                "url": url,
                "cache_file": relative_path(cache_path, repo_root),
                "state_file": relative_path(state_path, repo_root),
            }
            try:
                downloaded, last_request = download_file(
                    url,
                    cache_path,
                    max(0.0, args.request_delay),
                    last_request,
                )
                grid = prepare_grid(
                    decode_grib(
                        cache_path,
                        wgrib2,
                        force=args.force_decode,
                        match_pattern=product_spec["match"],
                        cache_tag=product_spec["cache_tag"],
                        expected_shape=product_spec["grid_shape"],
                    )
                )
                write_grid_state(grid, state_path)
                if rolling_inits and not getattr(args, "keep_source_cache", False):
                    # The compressed decoded state is the durable rolling input;
                    # do not grow the CI cache with dozens of 25-MB GRIB2 files.
                    decoded_csv = cache_path.with_name(
                        cache_path.name + f".{product_spec['cache_tag']}.csv"
                    )
                    for temporary_source in (cache_path, decoded_csv):
                        try:
                            temporary_source.unlink()
                        except FileNotFoundError:
                            pass
                source_file.update(
                    {
                        "storage": "nomads_grib2",
                        "downloaded": downloaded,
                    }
                )
                source_file.update(source_metadata())
            except Exception as exc:
                if state_path.exists():
                    grid = read_grid_state(state_path)
                    source_file.update(
                        {
                            "storage": "retained_decoded_grid",
                            "downloaded": False,
                            "download_error": str(exc),
                        }
                    )
                    source_file.update(source_metadata())
                elif args.allow_partial_rolling:
                    source_file.update({"status": "missing", "error": str(exc)})
                    source_files.append(source_file)
                    continue
                else:
                    raise CFSv2Error(
                        f"rolling CFSv2 cycle {cycle} is unavailable and has no retained grid; "
                        "the NOMADS archive rotates after seven days, so run the scheduled job "
                        "twice daily or use --allow-partial-rolling"
                    ) from exc
            source_file["status"] = "available"
            source_files.append(source_file)
            grids.append(grid)
            member_keys.append(cycle)
        if not grids:
            raise CFSv2Error("rolling CFSv2 window produced no usable member grids")
        if len(grids) < expected_count and not args.allow_partial_rolling:
            raise CFSv2Error(
                f"rolling CFSv2 window has {len(grids)} of {expected_count} members; "
                "use --allow-partial-rolling only for an explicitly incomplete product"
            )
        label = f"{len(grids)}/{expected_count}-cycle rolling mean"
        result = mean_grids(grids), source_files, len(grids), expected_count, label, last_request
        if return_member_grids:
            return (*result, dict(zip(member_keys, grids, strict=True)))
        return result

    for member in members:
        url = cfs_file_url(init, member, target, source_kind)
        cache_path = cached_source_path(cache_dir, init, member, target, source_kind)
        downloaded, last_request = download_file(
            url,
            cache_path,
            max(0.0, args.request_delay),
            last_request,
        )
        grid = prepare_grid(
            decode_grib(
                cache_path,
                wgrib2,
                force=args.force_decode,
                match_pattern=product_spec["match"],
                cache_tag=product_spec["cache_tag"],
                expected_shape=product_spec["grid_shape"],
            )
        )
        grids.append(grid)
        member_keys.append(f"{init}:{member}")
        source_file = {
            "initialization": init,
            "initialization_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "lead_month": lead_for_target(init, target),
            "member": member,
            "url": url,
            "cache_file": relative_path(cache_path, repo_root),
            "downloaded": downloaded,
            "status": "available",
        }
        source_file.update(source_metadata())
        source_files.append(source_file)
    result = mean_grids(grids), source_files, len(grids), len(grids), f"{len(grids)}-member mean", last_request
    if return_member_grids:
        return (*result, dict(zip(member_keys, grids, strict=True)))
    return result


def decode_snowfall_target_ensemble(
    args: argparse.Namespace,
    init: str,
    target: str,
    members: Sequence[int],
    rolling_inits: Sequence[str],
    cache_dir: Path,
    state_dir: Path,
    wgrib2: str,
    repo_root: Path,
    last_request: float,
    product_name: str = PRODUCT_SNOWFALL_ANOMALY,
) -> tuple[Grid, list[dict], int, int, str, float, dict[str, object]]:
    """Decode the three CFSv2 fields needed for derived snowfall."""

    if product_name not in SNOWFALL_PRODUCTS:
        raise CFSv2Error(f"{product_name} is not a derived CFSv2 snowfall product")
    if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
        from cfsv2_native_snow import decode
        return decode(args, init, target, members, rolling_inits, cache_dir, state_dir, wgrib2)
    dependencies = PRODUCT_SPECS[product_name]["dependencies"]
    member_grids: dict[str, dict[str, Grid]] = {}
    source_files: list[dict] = []
    counts: list[tuple[int, int]] = []
    labels: list[str] = []
    for dependency in dependencies:
        product_spec = get_product_spec(dependency)
        result = decode_target_ensemble(
            args,
            init,
            target,
            members,
            rolling_inits,
            cache_dir,
            state_dir,
            wgrib2,
            repo_root,
            last_request,
            product_spec,
            return_member_grids=True,
        )
        _, dependency_sources, available_count, expected_count, label, last_request, grids = result
        labels.append(label)
        counts.append((available_count, expected_count))
        for source_file in dependency_sources:
            tagged_source = dict(source_file)
            tagged_source["derived_dependency"] = dependency
            source_files.append(tagged_source)
        for key, grid in grids.items():
            member_grids.setdefault(key, {})[dependency] = grid

    required_keys = set(member_grids)
    if not required_keys:
        raise CFSv2Error("CFSv2 snowfall derivation received no complete members or cycles")
    incomplete = [key for key, fields in member_grids.items() if len(fields) != len(dependencies)]
    if incomplete:
        raise CFSv2Error(
            "CFSv2 snowfall dependencies do not share the same successful members/cycles; "
            f"missing fields for {', '.join(sorted(incomplete)[:5])}"
        )
    snowfall_inputs = {
        dependency: {key: fields[dependency] for key, fields in member_grids.items()}
        for dependency in dependencies
    }
    t850_regridded = {
        key: regrid_nearest(
            grid,
            snowfall_inputs[PRODUCT_2M_TEMPERATURE_ANOMALY][key].lons,
            snowfall_inputs[PRODUCT_2M_TEMPERATURE_ANOMALY][key].lats,
            f"CFSv2 850-mb temperature {key}",
        )
        for key, grid in snowfall_inputs[PRODUCT_850_TEMPERATURE_ANOMALY].items()
    }
    snowfall_grid, diagnostics = derive_snowfall_lwe_grid(
        snowfall_inputs[PRODUCT_2M_TEMPERATURE_ANOMALY],
        t850_regridded,
        snowfall_inputs[PRODUCT_PRECIPITATION_ANOMALY],
        target,
    )
    if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
        snowfall_grid, ratio_diagnostics = derive_snowfall_accumulation_grid(
            snowfall_grid,
            target,
        )
        diagnostics["snow_to_liquid_ratio"] = ratio_diagnostics
    diagnostics["dependencies"] = list(dependencies)
    diagnostics["regridded_dependency"] = PRODUCT_850_TEMPERATURE_ANOMALY
    available_count = min(count for count, _ in counts)
    expected_count = max(expected for _, expected in counts)
    label = labels[0] if labels and all(candidate == labels[0] for candidate in labels) else (
        f"{available_count}/{expected_count}-cycle derived snowfall mean"
        if rolling_inits
        else f"{available_count}-member derived snowfall mean"
    )
    if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
        label = f"{label}; CIPS climatological SLR"
    return snowfall_grid, source_files, available_count, expected_count, label, last_request, diagnostics


def subtract_grids(left: Grid, right: Grid) -> Grid:
    left.assert_compatible(right, "baseline")
    values = []
    for left_row, right_row in zip(left.values, right.values):
        values.append(
            [
                (a - b) if math.isfinite(a) and math.isfinite(b) else math.nan
                for a, b in zip(left_row, right_row)
            ]
        )
    return Grid(left.lons[:], left.lats[:], values)


def load_baseline(path: Path, wgrib2: str, product_spec: dict, target: str) -> Grid:
    suffix = path.suffix.lower()
    if suffix in {".grb2", ".grib2", ".grib"}:
        grid = decode_grib(
            path,
            wgrib2,
            match_pattern=product_spec["match"],
            cache_tag=f"{product_spec['cache_tag']}_baseline",
            expected_shape=product_spec["grid_shape"],
        )
        return prepare_product_grid(grid, product_spec, target)
    return read_grid_csv(path)


def baseline_for_target(args: argparse.Namespace, target: str, repo_root: Path) -> tuple[Path, str]:
    if args.baseline_file:
        if getattr(args, "product", "") == PRODUCT_SNOWFALL_ANOMALY:
            raise CFSv2Error(
                "CFSv2 snowfall derivation needs three matching baseline fields; "
                "use --ncei-calibration or --baseline-dir with tmp2m, tmp850, and prate grids"
            )
        path = resolve_repo_path(args.baseline_file, repo_root)
        if not path.exists():
            raise CFSv2Error(f"baseline file does not exist: {path}")
        return path, args.baseline_label or path.name
    if args.baseline_dir:
        directory = resolve_repo_path(args.baseline_dir, repo_root)
        product_name = getattr(args, "product", "")
        prefix = (
            "prate"
            if product_name.startswith("precipitation")
            else "weasd"
            if product_name == PRODUCT_SWE_ANOMALY
            else "tmp2m"
            if product_name == PRODUCT_2M_TEMPERATURE_ANOMALY
            else "tmp850"
            if product_name == PRODUCT_850_TEMPERATURE_ANOMALY
            else "mslp"
            if product_name == PRODUCT_MSLP_ANOMALY
            else "z500"
        )
        candidates = (
            f"{prefix}_{target}.csv",
            f"{prefix}_{target}.grb2",
            f"{prefix}_{target}.grib2",
            f"baseline_{target}.csv",
            f"baseline_{target}.grb2",
            f"{target}.csv",
            f"{target}.grb2",
        )
        for name in candidates:
            path = directory / name
            if path.exists():
                return path, args.baseline_label or name
        raise CFSv2Error(f"no baseline grid for target month {target} in {directory}")
    raise CFSv2Error(
        "anomaly rendering requires --baseline-file or --baseline-dir; "
        "use --ncei-calibration or --absolute for a clearly labelled alternative"
    )


def load_snowfall_baseline(
    args: argparse.Namespace,
    init: str,
    target: str,
    lead: int,
    cache_dir: Path,
    repo_root: Path,
    wgrib2: str,
    last_request: float,
) -> tuple[Grid, dict[str, object], float]:
    """Load and derive a matching snowfall baseline from all three fields."""

    if args.baseline_file:
        raise CFSv2Error(
            "CFSv2 snowfall derivation cannot use one baseline file; provide "
            "--ncei-calibration or --baseline-dir with tmp2m, tmp850, and prate grids"
        )
    dependencies = PRODUCT_SPECS[PRODUCT_SNOWFALL_ANOMALY]["dependencies"]
    baseline_grids: dict[str, Grid] = {}
    dependency_metadata: list[dict[str, object]] = []
    for dependency in dependencies:
        product_spec = get_product_spec(dependency)
        fallback_error = None
        represented_init = init
        downloaded = False
        requested_url = None
        if args.ncei_calibration:
            requested_url = ncei_calibration_url(init, lead, product_spec["source_kind"])
            (
                baseline_path,
                represented_init,
                downloaded,
                last_request,
                fallback_error,
            ) = load_ncei_calibration(
                cache_dir=cache_dir,
                init=init,
                lead=lead,
                source_kind=product_spec["source_kind"],
                request_delay=max(0.0, args.request_delay),
                last_request=last_request,
                allow_stale=getattr(args, "allow_stale_calibration", False),
            )
            label = configured_baseline_label(args)
            if fallback_error:
                label = f"{label} (cached {represented_init} fallback)"
        else:
            if not args.baseline_dir:
                raise CFSv2Error(
                    "CFSv2 snowfall derivation needs --ncei-calibration or --baseline-dir "
                    "with tmp2m, tmp850, and prate grids"
                )
            directory = resolve_repo_path(args.baseline_dir, repo_root)
            prefix = {
                PRODUCT_2M_TEMPERATURE_ANOMALY: "tmp2m",
                PRODUCT_850_TEMPERATURE_ANOMALY: "tmp850",
                PRODUCT_PRECIPITATION_ANOMALY: "prate",
            }[dependency]
            baseline_path = next(
                (
                    directory / filename
                    for filename in (
                        f"{prefix}_{target}.csv",
                        f"{prefix}_{target}.grb2",
                        f"{prefix}_{target}.grib2",
                    )
                    if (directory / filename).exists()
                ),
                None,
            )
            if baseline_path is None:
                raise CFSv2Error(
                    f"no {dependency} baseline grid for target month {target} in {directory}; "
                    f"expected {prefix}_{target}.csv or matching GRIB2"
                )
            label = args.baseline_label or baseline_path.name
        baseline_grids[dependency] = load_baseline(baseline_path, wgrib2, product_spec, target)
        dependency_metadata.append(
            {
                "product": dependency,
                "file": relative_path(baseline_path, repo_root),
                "label": label,
                "url": requested_url,
                "requested_initialization": init if args.ncei_calibration else None,
                "used_initialization": represented_init if args.ncei_calibration else None,
                "downloaded": downloaded if args.ncei_calibration else None,
                "fallback": "cached_prior_initialization" if fallback_error else None,
                "fallback_error": fallback_error,
            }
        )

    temperature_2m = baseline_grids[PRODUCT_2M_TEMPERATURE_ANOMALY]
    temperature_850 = regrid_nearest(
        baseline_grids[PRODUCT_850_TEMPERATURE_ANOMALY],
        temperature_2m.lons,
        temperature_2m.lats,
        "CFSv2 snowfall baseline 850-mb temperature",
    )
    derived, diagnostics = derive_snowfall_lwe_grid(
        {"baseline": temperature_2m},
        {"baseline": temperature_850},
        {"baseline": baseline_grids[PRODUCT_PRECIPITATION_ANOMALY]},
        target,
    )
    info = {
        "source": "NCEI CFSR/CFSv2 1982-2010 derived snowfall climatology"
        if args.ncei_calibration
        else (args.baseline_label or "user-supplied CFSv2/reforecast derived snowfall climatology"),
        "label": (
            configured_baseline_label(args)
            if args.ncei_calibration and not any(item["fallback"] for item in dependency_metadata)
            else (
                f"{configured_baseline_label(args)} (cached prior-cycle fallback)"
                if args.ncei_calibration
                else (args.baseline_label or "user-supplied CFSv2/reforecast derived snowfall climatology")
            )
        ),
        "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
        "required": True,
        "status": "applied",
        "dependencies": dependency_metadata,
        "derivation": diagnostics,
    }
    return derived, info, last_request


def configured_baseline_label(args: argparse.Namespace) -> str:
    if args.baseline_label:
        return args.baseline_label
    if args.ncei_calibration:
        product = getattr(args, "product", PRODUCT_HEIGHT_ANOMALY)
        return get_product_spec(product)["baseline_label"]
    return "user-supplied CFSv2/reforecast baseline"


def seasonal_baseline_manifest(
    monthly_baselines: Sequence[dict[str, object]],
    default_label: str,
    years: str | None,
    *,
    rolling_init: str | None = None,
) -> dict[str, object]:
    """Build seasonal baseline provenance for direct and derived products."""

    provenance_records: list[dict[str, object]] = []
    for baseline in monthly_baselines:
        dependencies = baseline.get("dependencies")
        nested_records = (
            [item for item in dependencies if isinstance(item, dict)]
            if isinstance(dependencies, list)
            else []
        )
        provenance_records.extend(nested_records or [baseline])

    metadata: dict[str, object] = {
        "files": [item["file"] for item in provenance_records if item.get("file")],
        "label": monthly_baselines[0].get("label", default_label) if monthly_baselines else default_label,
        "years": years,
    }
    if rolling_init:
        metadata["rolling_policy"] = "anchor_initialization"
        metadata["anchor_init"] = rolling_init

    baseline_urls = [item.get("url") for item in provenance_records if item.get("url")]
    if baseline_urls:
        metadata["urls"] = baseline_urls

    fallback_baselines = [
        {
            "requested_initialization": item.get("requested_initialization"),
            "used_initialization": item.get("used_initialization"),
            "requested_url": item.get("requested_url"),
            "url": item.get("url"),
            "error": item.get("fallback_error"),
        }
        for item in provenance_records
        if item.get("fallback") == "cached_prior_initialization"
    ]
    if fallback_baselines:
        metadata["fallbacks"] = fallback_baselines
    return metadata


def _finite_values(grid: Grid) -> Iterator[float]:
    for row in grid.values:
        for value in row:
            if math.isfinite(value):
                yield value


def _geojson_rings(geometry: dict) -> Iterator[list[list[float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        if coordinates:
            yield coordinates[0]
    elif kind == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                yield polygon[0]
    elif kind == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _geojson_rings(child)


def geojson_features(payload: dict) -> Iterator[list[list[float]]]:
    if payload.get("type") == "FeatureCollection":
        for feature in payload.get("features", []):
            geometry = feature.get("geometry") or {}
            yield from _geojson_rings(geometry)
    elif payload.get("type") == "Feature":
        yield from _geojson_rings(payload.get("geometry") or {})
    else:
        yield from _geojson_rings(payload)


def _geojson_feature_records(payload: dict) -> Iterator[tuple[dict, dict]]:
    """Yield GeoJSON properties and geometries without discarding feature metadata."""

    payload_type = payload.get("type")
    if payload_type == "FeatureCollection":
        for feature in payload.get("features", []):
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties")
            geometry = feature.get("geometry")
            if isinstance(geometry, dict):
                yield properties if isinstance(properties, dict) else {}, geometry
    elif payload_type == "Feature":
        properties = payload.get("properties")
        geometry = payload.get("geometry")
        if isinstance(geometry, dict):
            yield properties if isinstance(properties, dict) else {}, geometry
    elif isinstance(payload, dict):
        yield {}, payload


def _geojson_feature_identifiers(properties: dict) -> set[str]:
    return {
        str(properties.get(key, "")).strip().casefold()
        for key in (
            "name",
            "NAME",
            "state",
            "STATE_NAME",
            "STUSPS",
            "postal",
            "abbr",
            "code",
        )
        if properties.get(key)
    }


def _geojson_feature_matches(properties: dict, requested_states: set[str]) -> bool:
    return bool(_geojson_feature_identifiers(properties).intersection(requested_states))


def land_mask_from_borders(
    border_paths: Sequence[Path],
    longitude_values,
    latitude_values,
    state_names: Sequence[str] | None = None,
):
    """Return a land mask, optionally restricted to named U.S. states."""
    try:
        import numpy as np
        from matplotlib.path import Path as MatplotlibPath
    except ImportError:  # pragma: no cover - matplotlib is required by render_map
        return None

    longitudes = np.asarray(longitude_values, dtype=float)
    latitudes = np.asarray(latitude_values, dtype=float)
    points = np.column_stack((longitudes.ravel(), latitudes.ravel()))
    land = np.zeros(points.shape[0], dtype=bool)

    requested_states = {
        str(state).strip().casefold()
        for state in (state_names or ())
        if str(state).strip()
    }
    if requested_states:
        state_paths = [path for path in border_paths if path.name == "us-states.geojson"]
        for border_path in state_paths:
            try:
                payload = json.loads(border_path.read_text(encoding="utf-8"))
                for properties, geometry in _geojson_feature_records(payload):
                    if not _geojson_feature_matches(properties, requested_states):
                        continue
                    for ring in _geojson_rings(geometry):
                        vertices = np.asarray(ring, dtype=float)
                        if vertices.ndim != 2 or vertices.shape[0] < 3 or vertices.shape[1] < 2:
                            continue
                        land |= MatplotlibPath(vertices[:, :2], closed=True).contains_points(points)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return land.reshape(longitudes.shape)

    country_paths = [path for path in border_paths if path.name == "countries.geojson"]
    for border_path in country_paths:
        try:
            payload = json.loads(border_path.read_text(encoding="utf-8"))
            for ring in geojson_features(payload):
                vertices = np.asarray(ring, dtype=float)
                if vertices.ndim != 2 or vertices.shape[0] < 3 or vertices.shape[1] < 2:
                    continue
                land |= MatplotlibPath(vertices[:, :2], closed=True).contains_points(points)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return land.reshape(longitudes.shape)


def ensure_border_files(args: argparse.Namespace, cache_dir: Path, repo_root: Path) -> list[Path]:
    if args.no_borders:
        return []
    if args.border_geojson:
        paths = [resolve_repo_path(item, repo_root) for item in args.border_geojson]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise CFSv2Error(f"border GeoJSON does not exist: {', '.join(missing)}")
        return paths
    try:
        import requests
    except ImportError:
        print("warning: requests unavailable; continuing without map borders", file=sys.stderr)
        return []
    border_dir = cache_dir / "borders"
    paths: list[Path] = []
    for filename, url in DEFAULT_BORDER_URLS:
        destination = border_dir / filename
        if not destination.exists() or destination.stat().st_size == 0:
            try:
                border_dir.mkdir(parents=True, exist_ok=True)
                response = requests.get(url, timeout=(20, 120))
                response.raise_for_status()
                destination.write_bytes(response.content)
            except Exception as exc:
                print(f"warning: could not download {filename}; continuing without it: {exc}", file=sys.stderr)
                continue
        paths.append(destination)
    return paths


def render_map(
    grid: Grid,
    init: str,
    target: str,
    lead: int | str,
    members: Sequence[int],
    output_path: Path,
    anomaly: bool,
    baseline_label: str,
    border_paths: Sequence[Path],
    period_label: str = "",
    seasonal: bool = False,
    ensemble_label: str = "",
    height_grid: Grid | None = None,
    region: tuple[float, float, float, float] = DEFAULT_REGION,
    product_spec: dict | None = None,
    initialization_label: str = "",
    footer_text: str = "",
    native_lwe: Grid | None = None,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.colors as mcolors
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:  # pragma: no cover - target installs requirements.txt
        raise CFSv2Error("rendering requires numpy and matplotlib; install requirements.txt") from exc

    product_spec = product_spec or PRODUCT_SPECS[PRODUCT_HEIGHT_ANOMALY]
    if product_spec["name"] == PRODUCT_SNOWFALL_ACCUMULATION:
        if native_lwe is None:
            raise CFSv2Error("Native accumulation rendering requires its paired LWE grid")
        from cfsv2_native_snow import render
        return render(native_lwe, init, target, lead, output_path, seasonal, period_label, ensemble_label)
    region = product_spec.get("region", region)
    if product_spec["height_contours"]:
        # Absolute products can contour their own field.  An anomaly product
        # must never use the anomaly grid as a substitute for absolute heights:
        # doing so produces lines labelled as dam that are actually anomaly
        # values.  C3S/JMA raw geopotential decoding can legitimately be
        # unavailable for a partial run, so fail closed and omit the overlay.
        if height_grid is None and not anomaly:
            height_grid = grid
        if height_grid is not None:
            height_grid.assert_compatible(grid, "height contour")
    else:
        height_grid = None
    lon_min, lon_max, lat_min, lat_max = region
    source_lons = np.asarray(grid.lons, dtype=float)
    source_lats = np.asarray(grid.lats, dtype=float)
    source_data = np.asarray(grid.values, dtype=float)
    source_height = (
        np.asarray(height_grid.values, dtype=float) / 10.0
        if height_grid is not None
        else None
    )
    if source_data.shape != (source_lats.size, source_lons.size):
        raise CFSv2Error("decoded CFSv2 grid has inconsistent latitude/longitude dimensions")
    if source_lons.size < 2 or source_lats.size < 2:
        raise CFSv2Error("decoded CFSv2 grid is too small to project")
    if np.any(np.diff(source_lons) <= 0.0) or np.any(np.diff(source_lats) <= 0.0):
        raise CFSv2Error("decoded CFSv2 grid longitude/latitude coordinates must be sorted")

    projection_kind = str(product_spec.get("projection", "lambert_conformal_conic")).strip().lower()
    if projection_kind == "north_polar_stereographic":
        # Normalized north-polar stereographic coordinates put the pole at
        # the origin and the equator at radius two. The square canvas is
        # masked outside the requested hemisphere, so no southern-hemisphere
        # interpolation or border bleed can appear in the corners.
        polar_central_longitude = np.deg2rad(
            float(product_spec.get("projection_central_longitude", 0.0))
        )

        def map_project(lon_values, lat_values):
            longitude = np.deg2rad(np.asarray(lon_values, dtype=float))
            latitude = np.deg2rad(np.clip(np.asarray(lat_values, dtype=float), -89.5, 89.5))
            radius = 2.0 * np.tan(np.pi / 4.0 - latitude / 2.0)
            angle = longitude - polar_central_longitude
            return radius * np.sin(angle), -radius * np.cos(angle)

        def map_inverse(x_values, y_values):
            x_array = np.asarray(x_values, dtype=float)
            y_array = np.asarray(y_values, dtype=float)
            radius = np.hypot(x_array, y_array)
            latitude = np.pi / 2.0 - 2.0 * np.arctan(radius / 2.0)
            longitude = polar_central_longitude + np.arctan2(x_array, -y_array)
            return np.rad2deg(longitude), np.rad2deg(latitude)

        polar_equator_radius = 2.0 * np.tan(
            np.pi / 4.0 - np.deg2rad(max(0.0, min(90.0, lat_min))) / 2.0
        )
        x_min, x_max = -float(polar_equator_radius), float(polar_equator_radius)
        y_min, y_max = -float(polar_equator_radius), float(polar_equator_radius)
    elif projection_kind == "lambert_conformal_conic":
        # Match the shared North America Lambert Conformal Conic defaults
        # unless a regional product supplies its own center and framing.
        standard_parallel_1 = np.deg2rad(
            float(product_spec.get("projection_standard_parallel_1", SEASONAL_LCC_STANDARD_PARALLEL_1))
        )
        standard_parallel_2 = np.deg2rad(
            float(product_spec.get("projection_standard_parallel_2", SEASONAL_LCC_STANDARD_PARALLEL_2))
        )
        latitude_origin = np.deg2rad(
            float(product_spec.get("projection_latitude_origin", SEASONAL_LCC_LATITUDE_ORIGIN))
        )
        central_longitude = np.deg2rad(
            float(product_spec.get("projection_central_longitude", SEASONAL_LCC_CENTRAL_LONGITUDE))
        )
        n_coefficient = np.log(np.cos(standard_parallel_1) / np.cos(standard_parallel_2)) / np.log(
                np.tan(np.pi / 4.0 + standard_parallel_2 / 2.0)
                / np.tan(np.pi / 4.0 + standard_parallel_1 / 2.0)
        )
        scale = (
            np.cos(standard_parallel_1)
            * np.tan(np.pi / 4.0 + standard_parallel_1 / 2.0) ** n_coefficient
            / n_coefficient
        )
        origin_radius = scale / np.tan(np.pi / 4.0 + latitude_origin / 2.0) ** n_coefficient

        def map_project(lon_values, lat_values):
            longitude = np.deg2rad(np.asarray(lon_values, dtype=float))
            latitude = np.deg2rad(np.clip(np.asarray(lat_values, dtype=float), -89.5, 89.5))
            radius = scale / np.tan(np.pi / 4.0 + latitude / 2.0) ** n_coefficient
            angle = n_coefficient * (longitude - central_longitude)
            return radius * np.sin(angle), origin_radius - radius * np.cos(angle)

        # Operational map frames use a rectangular projected window rather
        # than the bounding box of a lon/lat rectangle. Anchor the horizontal
        # edges at the projection origin and the vertical edges on the
        # requested latitude span; this keeps the map filled in all four
        # corners and centers Greenland over North America.
        horizontal_x, _ = map_project(
            np.asarray([lon_min, lon_max]),
            np.full(2, np.rad2deg(latitude_origin)),
        )
        _, bottom_y = map_project(
            np.asarray([np.rad2deg(central_longitude)]),
            np.asarray([lat_min]),
        )
        top_edge_lons = np.linspace(lon_min, lon_max, 240)
        _, top_edge_y = map_project(top_edge_lons, np.full(top_edge_lons.shape, lat_max))
        x_min, x_max = float(np.nanmin(horizontal_x)), float(np.nanmax(horizontal_x))
        y_min, y_max = float(np.nanmin(bottom_y)), float(np.nanmax(top_edge_y))
    else:
        raise CFSv2Error(f"unsupported seasonal map projection {projection_kind!r}")

    projected_x_shift = (
        (x_max - x_min) * float(
            product_spec.get("projected_x_shift_fraction", PROJECTED_X_SHIFT_FRACTION)
        )
        if projection_kind == "lambert_conformal_conic"
        else 0.0
    )
    x_min -= projected_x_shift
    x_max -= projected_x_shift
    x_pad = max(0.01, (x_max - x_min) * 0.006)
    y_pad = max(0.01, (y_max - y_min) * 0.006)

    # Resample the full global field onto a regular projected canvas. Using
    # only the source cells inside the lon/lat box leaves the corners of a
    # projected map empty; inverse projection keeps those corners data-filled.
    canvas_columns = 520
    canvas_rows = max(260, int(round(canvas_columns * (y_max - y_min) / (x_max - x_min))))
    canvas_x = np.linspace(x_min, x_max, canvas_columns)
    canvas_y = np.linspace(y_min, y_max, canvas_rows)
    canvas_x_mesh, canvas_y_mesh = np.meshgrid(canvas_x, canvas_y)
    resampling_method = str(product_spec.get("resampling_method", "bilinear")).lower()
    if resampling_method not in {"bilinear", "bicubic"}:
        raise CFSv2Error(f"unsupported map resampling method {resampling_method!r}")
    source_smoothing_sigma = float(product_spec.get("source_smoothing_sigma", 0.0))
    if not math.isfinite(source_smoothing_sigma) or source_smoothing_sigma < 0.0:
        raise CFSv2Error("map source smoothing must be a finite non-negative value")
    if source_smoothing_sigma > 0.0 and resampling_method != "bicubic":
        raise CFSv2Error("map source smoothing requires bicubic resampling")

    if projection_kind == "lambert_conformal_conic":
        def map_inverse(x_values, y_values):
            x_array = np.asarray(x_values, dtype=float)
            y_array = np.asarray(y_values, dtype=float)
            rho = np.hypot(x_array, origin_radius - y_array)
            rho = np.where(rho == 0.0, np.finfo(float).eps, rho)
            angle = np.arctan2(x_array, origin_radius - y_array)
            latitude = 2.0 * np.arctan((scale / rho) ** (1.0 / n_coefficient)) - np.pi / 2.0
            longitude = central_longitude + angle / n_coefficient
            return np.rad2deg(longitude), np.rad2deg(latitude)

    def sample_source(field, longitude_values, latitude_values):
        if resampling_method == "bicubic" and np.isfinite(field).all():
            return _bicubic_sample_grid(
                source_lons,
                source_lats,
                field,
                longitude_values,
                latitude_values,
                smoothing_sigma=source_smoothing_sigma,
            )
        # CFSv2 pressure-level files are regular 1-degree grids, while FLXF
        # files use Gaussian latitudes.  Bracket coordinates directly so both
        # grids can be resampled without inventing a regular-latitude grid.
        # Incomplete fields also use this finite-safe fallback because cubic
        # interpolation cannot preserve interior missing-data masks.
        wrapped_longitudes = np.mod(longitude_values - source_lons[0], 360.0) + source_lons[0]
        longitude_right = np.searchsorted(source_lons, wrapped_longitudes, side="right")
        longitude_wrap = longitude_right >= source_lons.size
        lon_left = np.where(longitude_wrap, source_lons.size - 1, np.maximum(longitude_right - 1, 0))
        lon_right = np.where(longitude_wrap, 0, np.minimum(longitude_right, source_lons.size - 1))
        left_lon_value = source_lons[lon_left]
        right_lon_value = np.where(longitude_wrap, source_lons[0] + 360.0, source_lons[lon_right])
        lon_weight = np.divide(
            wrapped_longitudes - left_lon_value,
            right_lon_value - left_lon_value,
            out=np.zeros_like(wrapped_longitudes, dtype=float),
            where=(right_lon_value - left_lon_value) != 0.0,
        )

        clipped_latitudes = np.clip(latitude_values, source_lats[0], source_lats[-1])
        latitude_right = np.searchsorted(source_lats, clipped_latitudes, side="right")
        latitude_right = np.clip(latitude_right, 1, source_lats.size - 1)
        lat_left = latitude_right - 1
        lat_right = latitude_right
        left_lat_value = source_lats[lat_left]
        right_lat_value = source_lats[lat_right]
        lat_weight = np.divide(
            clipped_latitudes - left_lat_value,
            right_lat_value - left_lat_value,
            out=np.zeros_like(clipped_latitudes, dtype=float),
            where=(right_lat_value - left_lat_value) != 0.0,
        )

        values = (
            field[lat_left, lon_left] * (1.0 - lon_weight) * (1.0 - lat_weight)
            + field[lat_left, lon_right] * lon_weight * (1.0 - lat_weight)
            + field[lat_right, lon_left] * (1.0 - lon_weight) * lat_weight
            + field[lat_right, lon_right] * lon_weight * lat_weight
        )
        return values

    canvas_lons, canvas_lats = map_inverse(canvas_x_mesh, canvas_y_mesh)
    data = sample_source(source_data, canvas_lons, canvas_lats)
    height_data = (
        sample_source(source_height, canvas_lons, canvas_lats)
        if source_height is not None
        else None
    )
    if projection_kind == "north_polar_stereographic":
        polar_valid = (canvas_lats >= lat_min) & (canvas_lats <= lat_max)
        data = np.ma.masked_where(~polar_valid, data)
        if height_data is not None:
            height_data = np.ma.masked_where(~polar_valid, height_data)

    # Match a 1080x1080 social-media footprint. Size the map box from the
    # projected bounds so the LCC geometry remains undistorted at square size.
    # Snowfall maps use a tighter legend gap and are cropped after rendering so
    # the branded share image does not carry a large unused lower panel.
    figure = plt.figure(figsize=(9.0, 9.0), dpi=120, facecolor="#f7f9fb")
    has_footer = bool(footer_text.strip())
    is_snowfall = product_spec.get("name") in SNOWFALL_PRODUCTS
    colorbar_height = 0.032
    colorbar_gap = float(product_spec.get("colorbar_gap", 0.012 if is_snowfall else 0.025))
    if not math.isfinite(colorbar_gap) or colorbar_gap < 0.0:
        raise CFSv2Error("colorbar_gap must be a finite non-negative number")
    footer_line_count = footer_text.count("\n") + 1 if has_footer else 0
    colorbar_floor = 0.055 + 0.012 * footer_line_count
    map_left = 0.05 if has_footer else 0.035
    map_width = 0.90 if has_footer else 0.93
    map_height = map_width * (y_max - y_min) / (x_max - x_min)
    map_top = 0.88
    # Tall regional frames can otherwise push the map behind the colorbar.
    # Shrink the map box proportionally when needed so the complete requested
    # geographic extent remains visible above the legend and footer.
    map_height_limit = map_top - (colorbar_floor + colorbar_gap + colorbar_height)
    if map_height > map_height_limit:
        map_height = map_height_limit
        map_width = map_height * (x_max - x_min) / (y_max - y_min)
        map_left = (1.0 - map_width) / 2.0
    map_bottom = map_top - map_height
    axes = figure.add_axes([map_left, map_bottom, map_width, map_height])
    axes.set_facecolor("#ffffff" if product_spec["name"] == PRODUCT_SWE_ANOMALY else "#edf3f5")

    # Light graticules make the projection legible without competing with the
    # height field. The map remains intentionally free of axis tick clutter.
    for longitude_line in range(math.ceil(lon_min / 20.0) * 20, math.floor(lon_max / 20.0) * 20 + 1, 20):
        line_lats = np.linspace(lat_min, lat_max, 240)
        line_x, line_y = map_project(np.full(line_lats.shape, longitude_line), line_lats)
        axes.plot(line_x, line_y, color="#70808a", linewidth=0.35, alpha=0.34, linestyle=(0, (1, 3)), zorder=1)
    for latitude_line in range(math.ceil(lat_min / 10.0) * 10, math.floor(lat_max / 10.0) * 10 + 1, 10):
        line_lons = np.linspace(lon_min, lon_max, 300)
        line_x, line_y = map_project(line_lons, np.full(line_lons.shape, latitude_line))
        axes.plot(line_x, line_y, color="#70808a", linewidth=0.35, alpha=0.34, linestyle=(0, (1, 3)), zorder=1)

    masked = np.ma.masked_invalid(data)
    map_domain = product_spec.get("map_domain")
    if map_domain:
        if map_domain not in {"land", "ocean"}:
            raise CFSv2Error(f"unsupported map domain {map_domain!r}")
        mask_states = product_spec.get("mask_states")
        land_mask = land_mask_from_borders(
            border_paths,
            canvas_lons,
            canvas_lats,
            state_names=mask_states,
        )
        if land_mask is None or not np.any(land_mask):
            mask_label = "selected-state" if mask_states else "countries"
            raise CFSv2Error(
                f"{product_spec['name']} requires the {mask_label} land mask to render its {map_domain}-only domain"
            )
        # Domain-specific products must not display model fill or extrapolated
        # values where the parameter is undefined.  In particular, several
        # seasonal archives encode SST and sea-surface height at every grid
        # point even though their land values are not geophysical ocean data.
        masked = np.ma.masked_where(
            ~land_mask if map_domain == "land" else land_mask,
            masked,
        )
        if product_spec.get("fit_frame_to_domain"):
            if map_domain != "land":
                raise CFSv2Error("fit_frame_to_domain requires a land-only product")
            domain_points = land_mask & np.isfinite(data)
            if not np.any(domain_points):
                raise CFSv2Error("fit_frame_to_domain found no finite land cells")
            domain_x = canvas_x_mesh[domain_points]
            domain_y = canvas_y_mesh[domain_points]
            x_min = float(np.nanmin(domain_x))
            x_max = float(np.nanmax(domain_x))
            y_min = float(np.nanmin(domain_y))
            y_max = float(np.nanmax(domain_y))
            frame_padding_fraction = float(
                product_spec.get("domain_frame_padding_fraction", 0.0)
            )
            if not math.isfinite(frame_padding_fraction) or frame_padding_fraction < 0.0:
                raise CFSv2Error("domain_frame_padding_fraction must be a finite non-negative number")
            x_pad = (x_max - x_min) * frame_padding_fraction
            y_pad = (y_max - y_min) * frame_padding_fraction
            map_height = map_width * (y_max - y_min) / (x_max - x_min)
            if map_height > map_height_limit:
                map_height = map_height_limit
                map_width = map_height * (x_max - x_min) / (y_max - y_min)
                map_left = (1.0 - map_width) / 2.0
            map_bottom = map_top - map_height
            axes.set_position([map_left, map_bottom, map_width, map_height])
            # The fitted lower-48 frame replaces the original regional bounds;
            # keep its visible border padding proportional to the fitted
            # domain rather than reusing padding from the full CONUS window.
            x_pad = max(0.01, (x_max - x_min) * frame_padding_fraction)
            y_pad = max(0.01, (y_max - y_min) * frame_padding_fraction)
    snowfall_scale_label = ""
    fixed_absolute_style = None
    if anomaly:
        anomaly_min, anomaly_max, colorbar_ticks, palette = anomaly_style(
            product_spec,
            seasonal=seasonal,
        )
        snowfall_scale_label = (
            f"clipped at ±{max(abs(anomaly_min), abs(anomaly_max)):.1f} in"
            if is_snowfall
            else ""
        )
        # Most products use their labelled ticks as color transitions. A
        # regional product may provide separate boundaries when a labelled
        # neutral value needs to sit inside a dedicated center swatch (for
        # example, snowfall departures with white at zero).
        boundary_values = product_spec.get("anomaly_bounds", colorbar_ticks)
        bounds = np.asarray(boundary_values, dtype=float)
        if (
            bounds.ndim != 1
            or bounds.size != len(palette) + 1
            or np.any(np.diff(bounds) <= 0.0)
        ):
            raise CFSv2Error(
                "anomaly palette must have one fewer color than strictly increasing boundaries"
            )
        cmap = mcolors.ListedColormap(palette)
        norm = mcolors.BoundaryNorm(bounds, cmap.N, clip=True)
        image = axes.contourf(
            canvas_x,
            canvas_y,
            np.ma.clip(masked, anomaly_min, anomaly_max),
            levels=bounds,
            cmap=cmap,
            norm=norm,
            antialiased=True,
        )
    else:
        fixed_absolute_style = absolute_style(product_spec, seasonal=seasonal)
        if fixed_absolute_style is not None:
            absolute_bounds, colorbar_ticks, palette = fixed_absolute_style
            bounds = np.asarray(absolute_bounds, dtype=float)
            if (
                bounds.ndim != 1
                or bounds.size != len(palette) + 1
                or np.any(np.diff(bounds) <= 0.0)
            ):
                raise CFSv2Error(
                    "absolute palette must have one fewer color than strictly increasing boundaries"
                )
            cmap = mcolors.ListedColormap(palette)
            norm = mcolors.BoundaryNorm(bounds, cmap.N, clip=True)
            image = axes.contourf(
                canvas_x,
                canvas_y,
                np.ma.clip(masked, bounds[0], bounds[-1]),
                levels=bounds,
                cmap=cmap,
                norm=norm,
                antialiased=True,
            )
        else:
            finite = np.asarray(list(_finite_values(grid)), dtype=float)
            if finite.size == 0:
                raise CFSv2Error("decoded grid contains no finite values")
            vmin = float(np.nanpercentile(finite, 2))
            vmax = float(np.nanpercentile(finite, 98))
            if vmin == vmax:
                vmin -= 1.0
                vmax += 1.0
            image = axes.contourf(
                canvas_x,
                canvas_y,
                masked,
                levels=np.linspace(vmin, vmax, 17),
                cmap="viridis",
                norm=mcolors.Normalize(vmin=vmin, vmax=vmax),
                extend="both",
                antialiased=True,
            )
            colorbar_ticks = np.linspace(vmin, vmax, 7)

    # Filled anomalies show the signal; actual 500-mb heights provide the
    # synoptic structure and make the map readable like an operational
    # seasonal product. Heights are labelled in decametres (dam).
    height_masked = np.ma.masked_invalid(height_data) if height_data is not None else None
    finite_heights = np.ma.compressed(height_masked) if height_masked is not None else np.asarray([])
    if product_spec["height_contours"] and finite_heights.size > 1 and float(np.nanmax(finite_heights)) > float(np.nanmin(finite_heights)):
        contour_step = 6.0
        height_min = math.floor(float(np.nanpercentile(finite_heights, 2)) / contour_step) * contour_step
        height_max = math.ceil(float(np.nanpercentile(finite_heights, 98)) / contour_step) * contour_step
        height_levels = np.arange(height_min, height_max + contour_step * 0.5, contour_step)
        if height_levels.size > 1:
            minor_levels = np.arange(height_min, height_max + 3.0 * 0.5, 3.0)
            axes.contour(
                canvas_x,
                canvas_y,
                height_masked,
                levels=minor_levels,
                colors="#34444d",
                linewidths=0.24,
                alpha=0.38,
                linestyles="dotted",
                zorder=3,
            )
            height_lines = axes.contour(
                canvas_x,
                canvas_y,
                height_masked,
                levels=height_levels,
                colors="#1c2931",
                linewidths=0.62,
                alpha=0.84,
                zorder=4,
            )
            label_levels = height_levels[::2] if height_levels.size > 14 else height_levels
            axes.clabel(
                height_lines,
                levels=label_levels,
                inline=True,
                inline_spacing=3,
                fmt=lambda value: f"{value:.0f}",
                fontsize=7.2,
                colors="#1c2931",
            )

    def projected_ring_segments(ring):
        segments = []
        current = []
        previous_lon = None
        border_lat_min = max(0.0, min(14.0, lat_min))
        for point in ring:
            if len(point) < 2:
                continue
            longitude, latitude = float(point[0]), float(point[1])
            if not math.isfinite(longitude) or not math.isfinite(latitude) or abs(latitude) >= 89.5:
                if len(current) > 1:
                    segments.append(current)
                current = []
                previous_lon = None
                continue
            if not (lon_min <= longitude <= lon_max) or latitude < border_lat_min:
                if len(current) > 1:
                    segments.append(current)
                current = []
                previous_lon = None
                continue
            if previous_lon is not None and abs(longitude - previous_lon) > 180.0:
                if len(current) > 1:
                    segments.append(current)
                current = []
            point_x, point_y = map_project(np.array([longitude]), np.array([latitude]))
            current.append((float(point_x[0]), float(point_y[0])))
            previous_lon = longitude
        if len(current) > 1:
            segments.append(current)
        return segments

    configured_border_files = product_spec.get("border_files")
    render_border_paths = border_paths
    if configured_border_files is not None:
        allowed_border_files = {Path(str(item)).name for item in configured_border_files}
        render_border_paths = [path for path in border_paths if path.name in allowed_border_files]

    for border_path in render_border_paths:
        try:
            payload = json.loads(border_path.read_text(encoding="utf-8"))
            mask_states = product_spec.get("mask_states")
            requested_states = {
                str(state).strip().casefold()
                for state in (mask_states or ())
                if str(state).strip()
            }
            if requested_states and border_path.name == "us-states.geojson":
                border_rings = (
                    ring
                    for properties, geometry in _geojson_feature_records(payload)
                    if _geojson_feature_matches(properties, requested_states)
                    for ring in _geojson_rings(geometry)
                )
            else:
                border_rings = geojson_features(payload)
            for ring in border_rings:
                for segment in projected_ring_segments(ring):
                    axes.plot(
                        [point[0] for point in segment],
                        [point[1] for point in segment],
                        color="#17232c",
                        linewidth=0.66,
                        alpha=0.92,
                        solid_capstyle="round",
                        zorder=5,
                    )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: could not draw borders from {border_path}: {exc}", file=sys.stderr)

    axes.set_xlim(x_min - x_pad, x_max + x_pad)
    axes.set_ylim(y_min - y_pad, y_max + y_pad)
    axes.set_aspect("equal", adjustable="box")
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(True)
        spine.set_color("#20313a")
        spine.set_linewidth(0.75)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    target_date = dt.datetime.strptime(target, "%Y%m")
    display_period = period_label or target_date.strftime("%B %Y")
    if not seasonal and not period_label:
        # Keep the monthly valid label compact enough to share the header row
        # with the product title (for example, ``Valid: Dec 2026``).
        display_period = target_date.strftime("%b %Y")
    mean_label = ensemble_label or f"{len(members)}-member mean"
    title = product_spec["title"] if anomaly else product_spec["absolute_title"]
    title_text = figure.text(
        0.035,
        0.965,
        title,
        ha="left",
        va="center",
        fontsize=15.5,
        fontweight="bold",
        color="#172735",
    )
    valid_text = figure.text(
        0.965,
        0.965,
        f"Valid: {display_period}",
        ha="right",
        va="center",
        fontsize=13.0,
        fontweight="bold",
        color="#172735",
    )
    # A long calendar-month label such as "Valid: December 2026" must not
    # overlap the title and visually erase the first characters of "Valid".
    # Fit only the title when the two header artists do not leave a readable
    # gap, preserving the prominent right-aligned valid-period label.
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    title_box = title_text.get_window_extent(renderer=renderer)
    valid_box = valid_text.get_window_extent(renderer=renderer)
    available_title_width = max(1.0, valid_box.x0 - title_box.x0 - 16.0)
    if title_box.width > available_title_width:
        title_text.set_fontsize(max(12.5, 15.5 * available_title_width / title_box.width))
    source_label = product_spec.get("source_label", "NOAA CFSv2 / NOMADS")
    display_baseline_label = re.sub(
        r"\s*\(cached\s+[^)]*fallback\)\s*$",
        "",
        str(baseline_label),
        flags=re.IGNORECASE,
    ).strip()

    def fit_header_artist(artist: object, minimum_fontsize: float) -> None:
        """Keep one-line image metadata inside the right figure margin."""

        figure.canvas.draw()
        artist_box = artist.get_window_extent(renderer=figure.canvas.get_renderer())
        available_width = max(
            1.0,
            (0.965 - float(artist.get_position()[0])) * figure.bbox.width,
        )
        if artist_box.width > available_width:
            artist.set_fontsize(
                max(
                    minimum_fontsize,
                    float(artist.get_fontsize()) * available_width / artist_box.width,
                )
            )

    configured_header_summary = product_spec.get("header_summary")
    if configured_header_summary:
        header_summary = str(configured_header_summary).format(
            source_label=source_label,
            baseline_label=(
                "Absolute field smoke output" if not anomaly else display_baseline_label
            ),
            period_label=display_period,
        )
    else:
        init_text = initialization_label or f"Init {init_date:%d %b %Y %HZ}"
        lead_label = str(product_spec.get("lead_label", f"Lead {lead}"))
        header_summary = f"{init_text}  •  {lead_label}  •  {mean_label}"
    header_summary_text = figure.text(
        0.035,
        0.925,
        header_summary,
        ha="left",
        va="center",
        fontsize=10.5,
        color="#42515d",
    )
    fit_header_artist(header_summary_text, 8.4)
    if not product_spec.get("suppress_header_detail"):
        configured_header_detail = product_spec.get("header_detail", "")
        if configured_header_detail:
            header_detail = configured_header_detail.format(
                source_label=source_label,
                baseline_label=(
                    "Absolute field smoke output" if not anomaly else display_baseline_label
                ),
                snowfall_scale_label=snowfall_scale_label,
            )
        elif product_spec["height_contours"]:
            header_detail = (
                f"{source_label}  •  {display_baseline_label}  •  Height contours in dam"
                if anomaly
                else f"{source_label}  •  Absolute field smoke output  •  Height contours in dam"
            )
        else:
            header_detail = (
                f"{source_label}  •  {display_baseline_label}  •  Precipitation accumulation (in)  •  CONUS domain"
            )
        if product_spec["name"] == PRODUCT_SWE_ANOMALY:
            header_detail = (
                f"{source_label}  •  {display_baseline_label}  •  Snow-water equivalent (in)  •  CONUS domain"
            )
        if product_spec["name"] == PRODUCT_SNOWFALL_ANOMALY:
            header_detail = (
                f"{source_label}  •  {display_baseline_label}  •  Derived snowfall liquid-water equivalent (in)  •  CONUS domain"
            )
        header_detail_text = figure.text(
            0.035,
            0.899,
            header_detail,
            ha="left",
            va="center",
            fontsize=8.2,
            color="#5d6b75",
        )
        fit_header_artist(header_detail_text, 6.4)
    colorbar_bottom = max(colorbar_floor, map_bottom - colorbar_gap - colorbar_height)
    colorbar_axes = figure.add_axes([map_left, colorbar_bottom, map_width, colorbar_height])
    colorbar_options = {"ticks": colorbar_ticks}
    if anomaly or fixed_absolute_style is not None:
        colorbar_options["boundaries"] = bounds
    colorbar = figure.colorbar(
        image,
        cax=colorbar_axes,
        orientation="horizontal",
        extend="neither",
        spacing="uniform",
        drawedges=product_spec["name"] in {
            PRODUCT_PRECIPITATION_ANOMALY,
            PRODUCT_SWE_ANOMALY,
            PRODUCT_SNOWFALL_ANOMALY,
            PRODUCT_SNOWFALL_ACCUMULATION,
        },
        **colorbar_options,
    )
    colorbar.set_ticks(colorbar_ticks)
    if anomaly:
        automatic_tick_decimals = (
            1 if any(not float(tick).is_integer() for tick in colorbar_ticks) else 0
        )
        tick_decimals = int(product_spec.get("anomaly_tick_decimals", automatic_tick_decimals))
        tick_format = product_spec.get("anomaly_tick_format", "signed")

        def format_anomaly_tick(value: float) -> str:
            numeric = float(value)
            if abs(numeric) < 0.5 * (10 ** -tick_decimals):
                numeric = 0.0
            if tick_format == "plain":
                return f"{numeric:.{tick_decimals}f}"
            if tick_format == "signed_trimmed":
                if numeric == 0.0:
                    return "0"
                formatted = f"{abs(numeric):.{tick_decimals}f}".rstrip("0").rstrip(".")
                return f"+{formatted}" if numeric > 0.0 else f"−{formatted}"
            if tick_decimals:
                return f"{numeric:+.{tick_decimals}f}" if numeric else f"{numeric:.{tick_decimals}f}"
            return f"+{int(round(numeric))}" if numeric > 0 else str(int(round(numeric)))

        endpoint_labels = product_spec.get(
            f"{'seasonal' if seasonal else 'monthly'}_anomaly_endpoint_labels",
            product_spec.get("anomaly_endpoint_labels", {}),
        )
        tick_labels = []
        for index, tick in enumerate(colorbar_ticks):
            if index == 0 and endpoint_labels.get("minimum"):
                tick_labels.append(str(endpoint_labels["minimum"]))
            elif index == len(colorbar_ticks) - 1 and endpoint_labels.get("maximum"):
                tick_labels.append(str(endpoint_labels["maximum"]))
            else:
                tick_labels.append(format_anomaly_tick(tick))
        colorbar.set_ticklabels(tick_labels)
    dense_tick_labels = len(colorbar_ticks) > 20
    colorbar.ax.tick_params(
        axis="x",
        which="major",
        labelsize=8.2 if dense_tick_labels else 10.0,
        length=5.0,
        width=0.85,
        pad=1.2 if dense_tick_labels else 1.8,
        colors="#263640",
        direction="out",
    )
    colorbar.outline.set_edgecolor("#52636c")
    colorbar.outline.set_linewidth(0.65)
    footer_artist = None
    if has_footer:
        footer_y = 0.012
        if is_snowfall:
            # Snowfall maps are cropped to their legend.  Keep the optional
            # multi-system provenance footer inside that compact footprint
            # instead of leaving it at the old square-canvas baseline.
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
            colorbar_bbox = colorbar.ax.get_tightbbox(renderer)
            figure_height_px = float(figure.canvas.get_width_height()[1])
            if colorbar_bbox is not None and figure_height_px > 0.0:
                footer_y = max(
                    footer_y,
                    (colorbar_bbox.y0 - 24.0) / figure_height_px,
                )
        footer_artist = figure.text(
            0.5,
            footer_y,
            footer_text,
            ha="center",
            va="bottom",
            multialignment="center",
            linespacing=1.18,
            fontsize=6.3,
            color="#52616b",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop_bottom_to_legend = bool(
        product_spec.get("crop_bottom_to_legend", is_snowfall)
    )
    crop_bottom_px = None
    if crop_bottom_to_legend:
        # Draw once so Matplotlib resolves the tick-label extents.  The saved
        # source map is then cropped only below the lowest relevant artist;
        # the top/header and all legend labels remain unchanged.
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        bottom_boxes = [colorbar.ax.get_tightbbox(renderer)]
        if footer_artist is not None:
            bottom_boxes.append(footer_artist.get_window_extent(renderer=renderer))
        bottom_y = min(box.y0 for box in bottom_boxes if box is not None)
        padding_px = float(product_spec.get("crop_bottom_padding_px", 10.0))
        if not math.isfinite(padding_px) or padding_px < 0.0:
            raise CFSv2Error("crop_bottom_padding_px must be a finite non-negative number")
        figure_height_px = float(figure.canvas.get_width_height()[1])
        crop_bottom_px = max(
            1,
            min(
                int(round(figure_height_px)),
                int(math.ceil(figure_height_px - bottom_y + padding_px)),
            ),
        )
    figure.savefig(output_path, dpi=120, facecolor=figure.get_facecolor())
    plt.close(figure)
    if crop_bottom_px is not None:
        from PIL import Image

        temporary_path = output_path.with_name(
            output_path.stem + ".crop.tmp" + output_path.suffix
        )
        with Image.open(output_path) as saved:
            cropped = saved.crop((0, 0, saved.width, min(saved.height, crop_bottom_px)))
            if output_path.suffix.lower() in {".jpg", ".jpeg"}:
                cropped.save(temporary_path, format="JPEG", quality=95, subsampling=0)
            else:
                cropped.save(temporary_path)
        temporary_path.replace(output_path)


def relative_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def manifest_product_key(run: dict) -> str:
    """Return a stable product key, including for manifests predating ``product``."""

    product = run.get("product")
    if product:
        return str(product)
    return {
        "z500_anomaly": PRODUCT_HEIGHT_ANOMALY,
        "z500_anomaly_nh": PRODUCT_HEIGHT_ANOMALY_NH,
        "z500": PRODUCT_HEIGHT_ABSOLUTE,
        "t850_anomaly": PRODUCT_850_TEMPERATURE_ANOMALY,
        "t2m_anomaly": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "mslp_anomaly": PRODUCT_MSLP_ANOMALY,
        "precipitation_anomaly": PRODUCT_PRECIPITATION_ANOMALY,
        "snow_water_equivalent_anomaly": PRODUCT_SWE_ANOMALY,
        "snowfall_anomaly": PRODUCT_SNOWFALL_ANOMALY,
        "snowfall_accumulation": PRODUCT_SNOWFALL_ACCUMULATION,
    }.get(str(run.get("field", "")), PRODUCT_HEIGHT_ANOMALY)


def write_manifest(
    path: Path,
    repo_root: Path,
    run_entry: dict,
    previous_manifest: Path | None = None,
    retain_runs: int = 4,
) -> None:
    if retain_runs < 1:
        raise CFSv2Error("manifest retention must keep at least one run")
    payload = {
        "schema_version": 1,
        "kind": "cfsv2_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "retention": {
            "scope": "per_product",
            "max_runs": retain_runs,
            "history_runs": max(0, retain_runs - 1),
            "max_runs_per_product": retain_runs,
            "history_runs_per_product": max(0, retain_runs - 1),
        },
        "runs": [],
    }
    existing_paths = []
    if previous_manifest and previous_manifest.resolve() != path.resolve():
        existing_paths.append(previous_manifest)
    # A manifest already assembled during this process is newer than the
    # published fallback. Load it last so sequential product renders retain
    # earlier products from the same workflow invocation.
    existing_paths.append(path)
    for existing_path in existing_paths:
        if not existing_path.exists():
            continue
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                payload.update({key: existing[key] for key in ("schema_version", "kind", "source", "source_url") if key in existing})
                payload["runs"].extend(
                    run for run in existing["runs"]
                    if isinstance(run, dict) and not is_retired_product(run.get("product"))
                )
        except (OSError, ValueError) as exc:
            raise CFSv2Error(f"could not read existing CFSv2 manifest {existing_path}: {exc}") from exc
    payload["generated_utc"] = iso_utc(dt.datetime.now(dt.timezone.utc))
    unique_runs = {}
    for run in payload["runs"]:
        if isinstance(run, dict) and run.get("id") and not is_retired_product(run.get("product")):
            unique_runs[run["id"]] = run
    if not is_retired_product(run_entry.get("product")):
        unique_runs[run_entry["id"]] = run_entry
    sorted_runs = list(unique_runs.values())
    sorted_runs.sort(
        key=lambda item: (
            str(item.get("init_utc", "")),
            str(item.get("generated_utc", "")),
            str(item.get("id", "")),
        ),
        reverse=True,
    )
    retained_counts: dict[str, int] = {}
    payload["runs"] = []
    for retained_run in sorted_runs:
        product_key = manifest_product_key(retained_run)
        if retained_counts.get(product_key, 0) >= retain_runs:
            continue
        payload["runs"].append(retained_run)
        retained_counts[product_key] = retained_counts.get(product_key, 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product",
        choices=tuple(product for product in PRODUCT_SPECS if not is_retired_product(product)),
        default=PRODUCT_HEIGHT_ANOMALY,
        help="product to decode and render",
    )
    parser.add_argument("--init", default="latest", help="CFSv2 cycle as YYYYMMDDHH, or latest")
    parser.add_argument("--lead-months", default="1,2,3", help="comma-separated target leads, usually 1,2,3")
    parser.add_argument(
        "--seasonal-window",
        default="",
        help="optional consecutive leads for seasonal aggregates; separate multiple windows with semicolons, e.g. 3,4,5;4,5,6",
    )
    parser.add_argument("--members", default="1,2,3,4", help="comma-separated monthly_grib member directories")
    parser.add_argument(
        "--rolling-days",
        type=int,
        default=0,
        help="use a lagged initial-condition blend covering this many days; 6 gives an archive-safe 24 cycles and 10 gives CPC-style 40 cycles with retained state",
    )
    parser.add_argument("--rolling-member", type=int, default=ROLLING_MEMBER_DEFAULT, help="monthly_grib member used for each rolling six-hourly cycle (default: 1)")
    parser.add_argument("--rolling-state-dir", default=".cache/cfsv2/rolling", help="retained decoded grids used after NOMADS rotates old cycles")
    parser.add_argument("--allow-partial-rolling", action="store_true", help="render with available rolling cycles when the requested window is incomplete")
    parser.add_argument("--cache-dir", default=".cache/cfsv2", help="raw GRIB2/decoder/border cache")
    parser.add_argument("--output-dir", default="public/seasonal/cfsv2", help="rendered image directory")
    parser.add_argument("--manifest", default="public/seasonal/cfsv2_manifest.json", help="seasonal manifest path")
    parser.add_argument("--previous-manifest", type=Path, help="previous published manifest used to retain older runs")
    parser.add_argument(
        "--retain-runs",
        type=int,
        default=4,
        help="number of current and historical runs to retain per product in the manifest",
    )
    parser.add_argument("--baseline-file", type=Path, help="one CFSv2/reforecast baseline CSV or GRIB2 grid")
    parser.add_argument("--baseline-dir", type=Path, help="directory containing a baseline grid for each YYYYMM target")
    parser.add_argument("--ncei-calibration", action="store_true", help="fetch the matching official NCEI CFS reforecast calibration baseline (1982-2010)")
    parser.add_argument(
        "--allow-stale-calibration",
        action="store_true",
        help="when NCEI is temporarily unavailable, use a cached prior-cycle calibration and label the fallback",
    )
    parser.add_argument("--baseline-label", default="", help="human-readable baseline source and period for metadata")
    parser.add_argument("--baseline-years", default="", help="optional baseline years for manifest provenance")
    parser.add_argument("--common-reference-dir", type=Path, help="cached CanSIPS 1991-2020 reference grids for the comparison view")
    parser.add_argument("--common-reference-url", default="", help="base URL for published CanSIPS 1991-2020 reference grids")
    parser.add_argument("--wgrib2", default="", help="path to wgrib2.exe; CFSV2_WGRIB2 is also honored")
    parser.add_argument("--request-delay", type=float, default=2.0, help="seconds between NOAA downloads")
    parser.add_argument("--border-geojson", action="append", type=Path, help="local GeoJSON border file; repeatable")
    parser.add_argument("--no-borders", action="store_true", help="skip optional border downloads/drawing")
    parser.add_argument("--decode-only", action="store_true", help="download/decode/average but do not render")
    parser.add_argument("--absolute", action="store_true", help="render absolute heights; never label them as anomalies")
    parser.add_argument("--force-decode", action="store_true", help="rerun wgrib2 even when a decoded CSV is cached")
    parser.add_argument(
        "--keep-source-cache",
        action="store_true",
        help="retain raw and decoded cycle files for other products in the same workflow invocation",
    )
    return parser


def _run_single_window(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    product_name, product, absolute = selected_product(args)
    requires_baseline = bool(product.get("requires_baseline", not absolute))
    render_as_anomaly = bool(product.get("render_as_anomaly", requires_baseline))
    if is_retired_product(product_name):
        raise CFSv2Error(
            f"{product_name} is quarantined from production because its available "
            "calibration field is not a valid anomaly baseline"
        )
    init = discover_latest_init() if args.init == "latest" else parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 1, 9)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 1, 9) if args.seasonal_window else []
    if seasonal_leads:
        expected_window = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected_window:
            raise CFSv2Error("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    members = parse_int_list(args.members, "members", 1, 4)
    if args.rolling_days < 0 or args.rolling_days > 30:
        raise CFSv2Error("--rolling-days must be between 0 and 30")
    if not 1 <= args.rolling_member <= 4:
        raise CFSv2Error("--rolling-member must be between 1 and 4")
    rolling_inits = rolling_cycle_inits(init, args.rolling_days * 4) if args.rolling_days else []
    configured_baselines = sum(
        bool(value) for value in (args.baseline_file, args.baseline_dir, args.ncei_calibration)
    )
    if configured_baselines > 1:
        raise CFSv2Error("use only one of --baseline-file, --baseline-dir, and --ncei-calibration")
    if args.allow_stale_calibration and not args.ncei_calibration:
        raise CFSv2Error("--allow-stale-calibration requires --ncei-calibration")
    if args.ncei_calibration and args.baseline_years and args.baseline_years != NCEI_CALIBRATION_YEARS:
        raise CFSv2Error(
            f"--ncei-calibration uses the published {NCEI_CALIBRATION_YEARS} baseline"
        )
    if requires_baseline and not args.decode_only and configured_baselines == 0:
        raise CFSv2Error(
            "production anomaly rendering needs a CFSv2/reforecast baseline; "
            "provide --baseline-file/--baseline-dir, use --ncei-calibration, or use --absolute for smoke testing"
        )
    wgrib2 = find_wgrib2(args.wgrib2)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    state_dir = resolve_repo_path(args.rolling_state_dir, repo_root)
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    common_reference_dir = resolve_repo_path(
        args.common_reference_dir or ".cache/common-reference",
        repo_root,
    ) if (args.common_reference_dir or args.common_reference_url) else None
    cache_dir.mkdir(parents=True, exist_ok=True)
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    run_id = (
        f"cfsv2-{init}"
        if product_name == PRODUCT_HEIGHT_ANOMALY
        else f"cfsv2-{init}-{product_name}"
    )
    rolling_mode = bool(rolling_inits)
    ensemble_expected = len(rolling_inits) if rolling_mode else len(members)
    run_entry = {
        "id": run_id,
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "model": "CFSv2",
        "product": product_name,
        "source_kind": product["source_kind"].upper(),
        "init_utc": iso_utc(init_date),
        "decoder": {"tool": "wgrib2", "executable": wgrib2},
        "statistic": "ensemble_mean",
        "members": [args.rolling_member] if rolling_mode else members,
        "ensemble_members": ensemble_expected,
        "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
        "aggregation": (
            (f"{args.rolling_days}-day rolling initial-condition mean; " if rolling_mode else "")
            + (
                f"{len(seasonal_leads)}-month {product['seasonal_aggregation']}"
                if seasonal_leads
                else product.get("monthly_aggregation", "monthly forecast average")
            )
        ),
        "field": product["field"],
        "units": product["units"],
        "raw_field": product["raw_field"],
        "raw_units": product["raw_units"],
        "border_sources": (
            []
            if args.no_borders
            else (
                [{"file": relative_path(resolve_repo_path(path, repo_root), repo_root)} for path in args.border_geojson]
                if args.border_geojson
                else [{"name": name, "url": url} for name, url in DEFAULT_BORDER_URLS]
            )
        ),
        "baseline": None,
        "status": "planned",
        "targets": [],
    }
    if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
        run_entry["source_warning"] = "Unadjusted native snowfall × CIPS CWA ratios; 19 CWAs unavailable. The separate phase-derived departure is not a reference for this accumulation."
    common_reference_enabled = bool(common_reference_dir or args.common_reference_url) and product_name == PRODUCT_HEIGHT_ANOMALY
    if common_reference_enabled:
        run_entry["comparison_reference"] = {
            "id": "common_1991_2020",
            "label": COMMON_REFERENCE_LABEL,
            "years": COMMON_REFERENCE_YEARS,
            "source": "CanSIPS v3 hindcast climatology",
            "url_root": args.common_reference_url or None,
        }
    if rolling_mode:
        run_entry["rolling_window"] = {
            "days": args.rolling_days,
            "expected_cycles": len(rolling_inits),
            "cycle_interval_hours": 6,
            "member": args.rolling_member,
            "start_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[0], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "end_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[-1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "source": "lagged CFSv2 initial conditions",
        }
    if product.get("conversion"):
        run_entry["conversion"] = product["conversion"]
    if not requires_baseline:
        run_entry["baseline"] = {
            "status": "not_applicable",
            "reason": (
                "unadjusted native snowfall × CIPS CWA ratio; native departure unavailable"
                if product_name == PRODUCT_SNOWFALL_ACCUMULATION
                else "absolute smoke output"
            ),
        }
    elif absolute:
        run_entry["baseline"] = {"status": "not_applicable", "reason": "absolute smoke output"}
    elif args.decode_only:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
            "status": "not_applied_decode_only",
        }
    elif args.ncei_calibration:
        run_entry["baseline"] = {
            "source": product["baseline_label"],
            "years": NCEI_CALIBRATION_YEARS,
            "required": True,
        }
        if product.get("dependencies"):
            run_entry["baseline"]["dependencies"] = [
                {
                    "product": dependency,
                    "source_kind": get_product_spec(dependency)["source_kind"],
                    "url_root": get_product_spec(dependency)["baseline_root"],
                }
                for dependency in product["dependencies"]
            ]
        else:
            run_entry["baseline"]["url_root"] = product["baseline_root"]
    else:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
        }
    if rolling_mode and requires_baseline:
        run_entry["baseline"]["rolling_policy"] = "anchor_initialization"

    last_request = 0.0
    failures = 0
    native_lwe_grids: dict[int, Grid] = {}
    forecast_grids: dict[int, Grid] = {}
    baseline_grids: dict[int, Grid] = {}
    target_entries_by_lead: dict[int, dict] = {}
    for lead in leads:
        target = target_month(init, lead)
        valid_start, valid_end = target_period(target)
        target_entry = {
            "id": f"cfsv2-{target}-{product['id_token']}-lead{lead:02d}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": lead,
            "target_month": target,
            "aggregation": product.get("monthly_aggregation", "monthly forecast average"),
            "field": product["field"],
            "units": product["units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "source_files": [],
            "status": "planned",
        }
        try:
            if product_name in SNOWFALL_PRODUCTS:
                (
                    ensemble,
                    source_files,
                    ensemble_count,
                    ensemble_expected_for_target,
                    ensemble_label,
                    last_request,
                    derivation_diagnostics,
                ) = decode_snowfall_target_ensemble(
                    args,
                    init,
                    target,
                    members,
                    rolling_inits,
                    cache_dir,
                    state_dir,
                    wgrib2,
                    repo_root,
                    last_request,
                    product_name,
                )
                if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
                    native_lwe_grids[lead] = derivation_diagnostics.pop("_native_lwe")
                    target_entry["source_warning"] = "Unadjusted native snowfall estimate; 19 CWA ratios unavailable. Separate phase-derived departures are not its reference."
                target_entry["derivation"] = derivation_diagnostics
            else:
                ensemble, source_files, ensemble_count, ensemble_expected_for_target, ensemble_label, last_request = decode_target_ensemble(
                    args,
                    init,
                    target,
                    members,
                    rolling_inits,
                    cache_dir,
                    state_dir,
                    wgrib2,
                    repo_root,
                    last_request,
                    product,
                )
            target_entry["source_files"] = source_files
            target_entry["ensemble_members"] = ensemble_count
            target_entry["ensemble_expected_members"] = ensemble_expected_for_target
            target_entry["ensemble_complete"] = ensemble_count == ensemble_expected_for_target
            target_entry["ensemble_label"] = ensemble_label
            forecast_grids[lead] = ensemble
            target_entry["status"] = "partial" if ensemble_count < ensemble_expected_for_target else "decoded"
            if args.decode_only:
                run_entry["targets"].append(target_entry)
                target_entries_by_lead[lead] = target_entry
                print(f"decoded CFSv2 {target} lead {lead} from {ensemble_count}/{ensemble_expected_for_target} member(s)")
                continue

            baseline_label = "not applicable"
            anomaly_grid = ensemble
            if requires_baseline:
                if product_name == PRODUCT_SNOWFALL_ANOMALY:
                    baseline_grid, baseline_info, last_request = load_snowfall_baseline(
                        args,
                        init,
                        target,
                        lead,
                        cache_dir,
                        repo_root,
                        wgrib2,
                        last_request,
                    )
                    baseline_grids[lead] = baseline_grid
                    anomaly_grid = subtract_grids(ensemble, baseline_grid)
                    baseline_label = str(baseline_info["label"])
                    target_entry["baseline"] = baseline_info
                    if rolling_mode:
                        target_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                        target_entry["baseline"]["anchor_init"] = init
                    baseline_url = None
                    baseline_downloaded = False
                    baseline_fallback_error = None
                    baseline_init = init
                else:
                    baseline_url = None
                    baseline_downloaded = False
                    baseline_init = init
                    baseline_fallback_error = None
                if product_name != PRODUCT_SNOWFALL_ANOMALY:
                    if args.ncei_calibration:
                        baseline_url = ncei_calibration_url(init, lead, product["source_kind"])
                        (
                            baseline_path,
                            baseline_init,
                            baseline_downloaded,
                            last_request,
                            baseline_fallback_error,
                        ) = load_ncei_calibration(
                            cache_dir=cache_dir,
                            init=init,
                            lead=lead,
                            source_kind=product["source_kind"],
                            request_delay=max(0.0, args.request_delay),
                            last_request=last_request,
                            allow_stale=getattr(args, "allow_stale_calibration", False),
                        )
                        baseline_label = configured_baseline_label(args)
                        if baseline_fallback_error:
                            baseline_label = f"{baseline_label} (cached {baseline_init} fallback)"
                    else:
                        baseline_path, baseline_label = baseline_for_target(args, target, repo_root)
                    baseline_grid = load_baseline(baseline_path, wgrib2, product, target)
                    baseline_grids[lead] = baseline_grid
                    anomaly_grid = subtract_grids(ensemble, baseline_grid)
                    target_entry["baseline"] = {
                        "file": relative_path(baseline_path, repo_root),
                        "label": baseline_label,
                        "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                    }
                    if rolling_mode:
                        target_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                        target_entry["baseline"]["anchor_init"] = init
                    if baseline_url:
                        target_entry["baseline"]["url"] = ncei_calibration_url(
                            baseline_init,
                            lead,
                            product["source_kind"],
                        )
                        if baseline_fallback_error:
                            target_entry["baseline"].update(
                                {
                                    "requested_url": baseline_url,
                                    "requested_initialization": init,
                                    "used_initialization": baseline_init,
                                    "fallback": "cached_prior_initialization",
                                    "fallback_error": baseline_fallback_error,
                                }
                            )
                        target_entry["baseline"]["downloaded"] = baseline_downloaded

            if getattr(args, "_seasonal_only", False):
                target_entry["quality_control"] = grid_quality_control(
                    product_name,
                    anomaly_grid.values,
                    units=product["units"],
                    field=product["field"],
                    seasonal=False,
                )
                require_quality_control(target_entry["quality_control"], CFSv2Error)
                target_entry["status"] = "partial" if not target_entry["ensemble_complete"] else "decoded"
                run_entry["targets"].append(target_entry)
                target_entries_by_lead[lead] = target_entry
                print(f"decoded CFSv2 {target} lead {lead} for an additional seasonal window")
                continue

            output_path = output_dir / init / f"cfsv2_{product['file_token']}_{target}.jpg"
            target_entry["quality_control"] = grid_quality_control(
                product_name,
                anomaly_grid.values,
                units=product["units"],
                field=product["field"],
                seasonal=False,
            )
            require_quality_control(target_entry["quality_control"], CFSv2Error)
            render_map(
                anomaly_grid,
                init,
                target,
                lead,
                members,
                output_path,
                anomaly=render_as_anomaly,
                baseline_label=baseline_label,
                border_paths=border_paths,
                ensemble_label=ensemble_label,
                native_lwe=native_lwe_grids.get(lead),
                height_grid=ensemble if product["height_contours"] else None,
                product_spec=product,
            )
            target_entry["image"] = relative_path(output_path, repo_root)
            if product_name in {PRODUCT_HEIGHT_ANOMALY, PRODUCT_SNOWFALL_ACCUMULATION}:
                numeric_grid_path = output_dir / init / f"cfsv2_{product['file_token']}_{target}.csv.gz"
                write_grid_state(anomaly_grid, numeric_grid_path)
                target_entry["numeric_grid"] = relative_path(numeric_grid_path, repo_root)
                target_entry["numeric_grid_format"] = "csv.gz"
            if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
                native_path = output_dir / init / f"cfsv2_snowfall_lwe_{target}.csv.gz"
                write_grid_state(native_lwe_grids[lead], native_path)
                target_entry["native_lwe_grid"] = relative_path(native_path, repo_root)
            target_entry["status"] = "partial" if not target_entry["ensemble_complete"] else "rendered"
            print(f"rendered CFSv2 {target} lead {lead}: {output_path}")
            if common_reference_enabled:
                try:
                    common_reference, reference_path, reference_url, reference_downloaded, last_request = load_common_reference(
                        target,
                        common_reference_dir,
                        args.common_reference_url,
                        max(0.0, args.request_delay),
                        last_request,
                    )
                    common_reference = regrid_nearest(
                        common_reference,
                        ensemble.lons,
                        ensemble.lats,
                        f"common reference {target}",
                    )
                    common_grid = subtract_grids(ensemble, common_reference)
                    common_qc = grid_quality_control(
                        product_name,
                        common_grid.values,
                        units=product["units"],
                        field=product["field"],
                        seasonal=False,
                    )
                    require_quality_control(common_qc, CFSv2Error)
                    common_output = output_dir / init / f"cfsv2_{product['file_token']}_{target}_common-1991-2020.jpg"
                    render_map(
                        common_grid,
                        init,
                        target,
                        lead,
                        members,
                        common_output,
                        anomaly=True,
                        baseline_label=COMMON_REFERENCE_LABEL,
                        border_paths=border_paths,
                        ensemble_label=ensemble_label,
                        height_grid=ensemble,
                        product_spec=product,
                    )
                    target_entry["comparison"] = {
                        "common_1991_2020": {
                            "image": relative_path(common_output, repo_root),
                            "status": "rendered",
                            "quality_control": common_qc,
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                                "file": relative_path(reference_path, repo_root),
                                "url": reference_url or None,
                                "downloaded": reference_downloaded,
                            },
                        }
                    }
                except Exception as exc:
                    target_entry["comparison"] = {
                        "common_1991_2020": {
                            "status": "unavailable",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                            },
                            "error": str(exc),
                        }
                    }
                    print(f"CFSv2 common comparison target {target} unavailable: {exc}", file=sys.stderr)
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CFSv2 target {target} lead {lead} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(target_entry)
        target_entries_by_lead[lead] = target_entry

    if seasonal_leads and not args.decode_only:
        first_lead = seasonal_leads[0]
        last_lead = seasonal_leads[-1]
        first_target = target_month(init, first_lead)
        last_target = target_month(init, last_lead)
        seasonal_entry = {
            "id": f"cfsv2-{first_target}-{last_target}-{product['id_token']}-seasonal",
            "valid_start_utc": target_period(first_target)[0],
            "valid_end_utc": target_period(last_target)[1],
            "lead_month": f"{first_lead}-{last_lead}",
            "target_month": f"{first_target}-{last_target}",
            "aggregation": (
                f"{len(seasonal_leads)}-month {product['seasonal_aggregation']}"
            ),
            "field": product["field"],
            "units": product["seasonal_units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "monthly_leads": seasonal_leads,
            "source_files": [],
            "status": "planned",
        }
        try:
            missing_forecasts = [lead for lead in seasonal_leads if lead not in forecast_grids]
            if missing_forecasts:
                raise CFSv2Error(f"seasonal window is missing decoded lead(s): {missing_forecasts}")
            seasonal_forecast = (
                sum_grids([forecast_grids[lead] for lead in seasonal_leads])
                if product["seasonal_reducer"] == "sum"
                else mean_grids([forecast_grids[lead] for lead in seasonal_leads])
            )
            seasonal_grid = seasonal_forecast
            baseline_label = "not applicable"
            if requires_baseline:
                missing_baselines = [lead for lead in seasonal_leads if lead not in baseline_grids]
                if missing_baselines:
                    raise CFSv2Error(f"seasonal window is missing baseline lead(s): {missing_baselines}")
                seasonal_baseline = (
                    sum_grids([baseline_grids[lead] for lead in seasonal_leads])
                    if product["seasonal_reducer"] == "sum"
                    else mean_grids([baseline_grids[lead] for lead in seasonal_leads])
                )
                seasonal_grid = subtract_grids(seasonal_forecast, seasonal_baseline)
                monthly_baselines = [
                    target_entries_by_lead[lead]["baseline"]
                    for lead in seasonal_leads
                    if "baseline" in target_entries_by_lead.get(lead, {})
                ]
                baseline_label = (
                    monthly_baselines[0].get("label", configured_baseline_label(args))
                    if monthly_baselines
                    else configured_baseline_label(args)
                )
                seasonal_entry["baseline"] = seasonal_baseline_manifest(
                    monthly_baselines,
                    baseline_label,
                    NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                    rolling_init=init if rolling_mode else None,
                )
            else:
                seasonal_entry["baseline"] = {
                    "status": "not_applicable",
                    "reason": (
                        "unadjusted native snowfall × CIPS CWA ratio; native departure unavailable"
                        if product_name == PRODUCT_SNOWFALL_ACCUMULATION
                        else "absolute smoke output"
                    ),
                }
            seasonal_entry["source_files"] = [
                source_file
                for lead in seasonal_leads
                for source_file in target_entries_by_lead[lead].get("source_files", [])
            ]
            seasonal_entry["ensemble_complete"] = all(
                target_entries_by_lead[lead].get("ensemble_complete", False)
                for lead in seasonal_leads
            )
            seasonal_entry["ensemble_members"] = min(
                target_entries_by_lead[lead].get("ensemble_members", 0)
                for lead in seasonal_leads
            )
            start_date = dt.datetime.strptime(first_target, "%Y%m")
            end_date = dt.datetime.strptime(last_target, "%Y%m")
            period_label = seasonal_period_label(first_target, last_target)
            seasonal_entry["label"] = period_label
            output_path = output_dir / init / f"cfsv2_{product['file_token']}_{first_target}-{last_target}.jpg"
            seasonal_entry["quality_control"] = grid_quality_control(
                product_name,
                seasonal_grid.values,
                units=product["seasonal_units"],
                field=product["field"],
                seasonal=True,
            )
            require_quality_control(seasonal_entry["quality_control"], CFSv2Error)
            render_map(
                seasonal_grid,
                init,
                first_target,
                f"{first_lead}\u2013{last_lead}",
                members,
                output_path,
                anomaly=render_as_anomaly,
                baseline_label=baseline_label,
                border_paths=border_paths,
                period_label=period_label,
                seasonal=True,
                ensemble_label=(
                    f"{seasonal_entry['ensemble_members']}/{ensemble_expected}-cycle rolling mean"
                    if rolling_mode
                    else f"{len(members)}-member mean"
                ),
                native_lwe=sum_grids([native_lwe_grids[l] for l in seasonal_leads]) if product_name == PRODUCT_SNOWFALL_ACCUMULATION else None,
                height_grid=seasonal_forecast if product["height_contours"] else None,
                product_spec=product,
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            if product_name in {PRODUCT_HEIGHT_ANOMALY, PRODUCT_SNOWFALL_ACCUMULATION}:
                numeric_grid_path = output_dir / init / f"cfsv2_{product['file_token']}_{first_target}-{last_target}.csv.gz"
                write_grid_state(seasonal_grid, numeric_grid_path)
                seasonal_entry["numeric_grid"] = relative_path(numeric_grid_path, repo_root)
                seasonal_entry["numeric_grid_format"] = "csv.gz"
            if product_name == PRODUCT_SNOWFALL_ACCUMULATION:
                seasonal_entry["derivation"] = target_entries_by_lead[first_lead]["derivation"]
                seasonal_entry["source_warning"] = target_entries_by_lead[first_lead]["source_warning"]
                native_path = output_dir / init / f"cfsv2_snowfall_lwe_{first_target}-{last_target}.csv.gz"
                write_grid_state(sum_grids([native_lwe_grids[l] for l in seasonal_leads]), native_path)
                seasonal_entry["native_lwe_grid"] = relative_path(native_path, repo_root)
            seasonal_entry["status"] = "rendered" if seasonal_entry["ensemble_complete"] else "partial"
            print(f"rendered CFSv2 seasonal product {first_target}-{last_target}: {output_path}")
            if common_reference_enabled:
                try:
                    common_references = []
                    reference_files = []
                    reference_urls = []
                    for lead in seasonal_leads:
                        target = target_month(init, lead)
                        reference, reference_path, reference_url, reference_downloaded, last_request = load_common_reference(
                            target,
                            common_reference_dir,
                            args.common_reference_url,
                            max(0.0, args.request_delay),
                            last_request,
                        )
                        common_references.append(regrid_nearest(
                            reference,
                            seasonal_forecast.lons,
                            seasonal_forecast.lats,
                            f"common reference {target}",
                        ))
                        reference_files.append(relative_path(reference_path, repo_root))
                        if reference_url:
                            reference_urls.append(reference_url)
                    common_baseline = (
                        sum_grids(common_references)
                        if product["seasonal_reducer"] == "sum"
                        else mean_grids(common_references)
                    )
                    common_grid = subtract_grids(seasonal_forecast, common_baseline)
                    common_qc = grid_quality_control(
                        product_name,
                        common_grid.values,
                        units=product["seasonal_units"],
                        field=product["field"],
                        seasonal=True,
                    )
                    require_quality_control(common_qc, CFSv2Error)
                    common_output = output_dir / init / f"cfsv2_{product['file_token']}_{first_target}-{last_target}_common-1991-2020.jpg"
                    render_map(
                        common_grid,
                        init,
                        first_target,
                        f"{first_lead}\u2013{last_lead}",
                        members,
                        common_output,
                        anomaly=True,
                        baseline_label=COMMON_REFERENCE_LABEL,
                        border_paths=border_paths,
                        period_label=period_label,
                        seasonal=True,
                        ensemble_label=(
                            f"{seasonal_entry['ensemble_members']}/{ensemble_expected}-cycle rolling mean"
                            if rolling_mode
                            else f"{len(members)}-member mean"
                        ),
                        height_grid=seasonal_forecast,
                        product_spec=product,
                    )
                    seasonal_entry["comparison"] = {
                        "common_1991_2020": {
                            "image": relative_path(common_output, repo_root),
                            "status": "rendered",
                            "quality_control": common_qc,
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                                "files": reference_files,
                                "urls": reference_urls,
                            },
                        }
                    }
                except Exception as exc:
                    seasonal_entry["comparison"] = {
                        "common_1991_2020": {
                            "status": "unavailable",
                            "baseline": {
                                "label": COMMON_REFERENCE_LABEL,
                                "years": COMMON_REFERENCE_YEARS,
                                "source": "CanSIPS v3 hindcast climatology",
                            },
                            "error": str(exc),
                        }
                    }
                    print(
                        f"CFSv2 common comparison seasonal window {first_target}-{last_target} unavailable: {exc}",
                        file=sys.stderr,
                    )
        except Exception as exc:
            failures += 1
            seasonal_entry["status"] = "failed"
            seasonal_entry["error"] = str(exc)
            print(f"CFSv2 seasonal window {first_target}-{last_target} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(seasonal_entry)

    statuses = [target["status"] for target in run_entry["targets"]]
    partial_targets = any(status == "partial" for status in statuses)
    if failures or partial_targets:
        run_entry["status"] = "partial" if any(status != "failed" for status in statuses) else "failed"
    elif args.decode_only:
        run_entry["status"] = "decoded"
    else:
        run_entry["status"] = "rendered"
    run_entry["output_dir"] = relative_path(output_dir, repo_root)
    previous_manifest = resolve_repo_path(args.previous_manifest, repo_root) if args.previous_manifest else None
    write_manifest(manifest_path, repo_root, run_entry, previous_manifest, args.retain_runs)
    print(f"wrote CFSv2 manifest: {manifest_path}")
    return 2 if failures else 0


def merge_seasonal_window_runs(runs: Sequence[dict], windows: Sequence[Sequence[int]]) -> dict:
    """Merge one-init snowfall runs while retaining the best copy of each target."""

    if not runs:
        raise CFSv2Error("multiple seasonal windows produced no CFSv2 runs")
    combined = json.loads(json.dumps(runs[0]))
    targets_by_id = {
        str(target.get("id")): target
        for target in combined.get("targets", [])
        if isinstance(target, dict) and target.get("id")
    }
    target_order = list(targets_by_id)
    for run_entry in runs[1:]:
        if run_entry.get("id") != combined.get("id"):
            raise CFSv2Error("seasonal-window fragments identify different CFSv2 runs")
        for target in run_entry.get("targets", []):
            if not isinstance(target, dict) or not target.get("id"):
                continue
            target_id = str(target["id"])
            if target_id not in targets_by_id:
                target_order.append(target_id)
                targets_by_id[target_id] = target
            elif not targets_by_id[target_id].get("image") and target.get("image"):
                targets_by_id[target_id] = target
    combined["targets"] = [targets_by_id[target_id] for target_id in target_order]
    combined["seasonal_windows"] = [list(window) for window in windows]
    statuses = [str(target.get("status", "")) for target in combined["targets"]]
    if any(status in {"failed", "partial"} for status in statuses):
        combined["status"] = "partial" if any(status != "failed" for status in statuses) else "failed"
    elif statuses:
        combined["status"] = "rendered"
    return combined


def run(args: argparse.Namespace) -> int:
    seasonal_windows = parse_seasonal_windows(args.seasonal_window)
    if len(seasonal_windows) <= 1:
        if seasonal_windows:
            args.seasonal_window = ",".join(str(lead) for lead in seasonal_windows[0])
        return _run_single_window(args)

    product_name, _product, _absolute = selected_product(args)
    if product_name not in SNOWFALL_PRODUCTS:
        raise CFSv2Error(
            "multiple --seasonal-window groups are currently supported only for snowfall products"
        )
    if args.decode_only:
        raise CFSv2Error("multiple --seasonal-window groups require rendered output")

    repo_root = Path(__file__).resolve().parents[1]
    resolved_init = discover_latest_init() if args.init == "latest" else parse_init(args.init)
    expected_run_id = f"cfsv2-{resolved_init}-{product_name}"
    window_runs: list[dict] = []
    result = 0
    with tempfile.TemporaryDirectory(prefix="cfsv2-seasonal-windows-") as temporary:
        temporary_root = Path(temporary)
        for index, seasonal_window in enumerate(seasonal_windows):
            child = argparse.Namespace(**vars(args))
            child.init = resolved_init
            child.seasonal_window = ",".join(str(lead) for lead in seasonal_window)
            child.manifest = temporary_root / f"window-{index}.json"
            child.previous_manifest = None
            if index > 0:
                child.lead_months = child.seasonal_window
                child._seasonal_only = True
            child_result = _run_single_window(child)
            result = max(result, child_result)
            payload = json.loads(Path(child.manifest).read_text(encoding="utf-8"))
            current = next(
                (
                    run_entry
                    for run_entry in payload.get("runs", [])
                    if run_entry.get("id") == expected_run_id
                ),
                None,
            )
            if current is None:
                raise CFSv2Error(f"seasonal window {child.seasonal_window} did not write {expected_run_id}")
            window_runs.append(current)

    combined_run = merge_seasonal_window_runs(window_runs, seasonal_windows)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    previous_manifest = resolve_repo_path(args.previous_manifest, repo_root) if args.previous_manifest else None
    write_manifest(manifest_path, repo_root, combined_run, previous_manifest, args.retain_runs)
    print(f"merged {len(seasonal_windows)} CFSv2 snowfall seasonal windows: {manifest_path}")
    return result


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except CFSv2Error as exc:
        print(f"CFSv2 error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

