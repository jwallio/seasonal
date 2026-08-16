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
        "monthly_reducer"