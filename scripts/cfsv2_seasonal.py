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
# 14Â°N cutoff so South America does not appear in the frame.
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
PRODUCT_2M_TEMPERATURE_ANOMALY = "2m_temperature_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
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
    PRODUCT_2M_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "source_kind": "flxf",
        "match": ":TMP:2 m above ground:",
        "raw_field": "TMP:2 m above ground",
        "raw_units": "K",
        "field": "t2m_anomaly",
        "units": "Â°C",
        "grid_shape": (FLUX_GRID_LON_COUNT, FLUX_GRID_LAT_COUNT),
        "cache_tag": "tmp2m",
        "state_tag": "tmp2m",
        "id_token": "t2ma",
        "file_token": "t2ma",
        "title": "CFSv2 2-m Temperature Anomaly (Â°C)",
        "absolute_title": "CFSv2 2-m Temperature (Â°C)",
        "region": DEFAULT_REGION,
        "height_contours": False,
        "baseline_root": NCEI_FLUX_CALIBRATION_ROOT,
        "baseline_label": NCEI_FLUX_CALIBRATION_LABEL,
        "seasonal_reducer": "mean",
        "seasonal_aggregation": "seasonal mean",
        "seasonal_units": "Â°C",
        "monthly_aggregation": "monthly mean 2-m temperature",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": CFSV2_TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "conversion": "Kelvin offset cancels in forecast-minus-calibration anomalies; displayed in Â°C",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  2-m temperature anomaly (Â°C)",
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
        "region": DEFAULT_REGION,
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Mean sea-level pressure anomaly (hPa)",
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
}


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


def get_product_spec(product: str) -> dict:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        available = ", ".join(sorted(PRODUCT_SPECS))
        raise CFSv2Error(f"unsupported CFSv2 product {product!r}; choose from {available}") from exc


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

    if "anomaly_min" in product_spec:
        return (
            float(product_spec["anomaly_min"]),
            float(product_spec["anomaly_max"]),
            product_spec.get("anomaly_ticks", []),
            product_spec.get("anomaly_palette", ANOMALY_PALETTE),
        )
    if product_spec["name"] == PRODUCT_PRECIPITATION_ANOMALY:
        if seasonal:
            rÛ=îÚ$z{-®éÜj×æöÖÇ•öw&–BÀ¢–æ—BÀ¢F&vWBÀ¢ÆVBÀ¢ÖVÖ&W'2À¢÷WGWE÷F‚À¢æöÖÇ“Öæ÷B'6öÇWFRÀ¢&6VÆ–æUöÆ&VÃÖ&6VÆ–æUöÆ&VÂÀ¢&÷&FW%÷F‡3Ö&÷&FW%÷F‡2À¢Vç6VÖ&ÆUöÆ&VÃÖVç6VÖ&ÆUöÆ&VÂÀ¢†V–v‡Eöw&–CÖVç6VÖ&ÆR–b&öGV7E²&†V–v‡Eö6öçF÷W'2%ÒVÇ6RæöæRÀ¢&öGV7E÷7V3×&öGV7BÀ¢¢F&vWEöVçG'•²&–ÖvR%ÒÒ&VÆF—fU÷F‚†÷WGWE÷F‚Â&Wõ÷&ö÷B¢F&vWEöVçG'•²'7FGW2%ÒÒ''F–Â"–bæ÷BF&vWEöVçG'•²&Vç6VÖ&ÆUö6ö×ÆWFR%ÒVÇ6R'&VæFW&VB ¢&–çB†b'&VæFW&VB4e7c"·F&vWGÒÆVB¶ÆVGÓ¢¶÷WGWE÷F‡Ò"¢–b6öÖÖöå÷&VfW&Væ6UöVæ&ÆVC ¢G'“ ¢6öÖÖöå÷&VfW&Væ6RÂ&VfW&Væ6U÷F‚Â&VfW&Væ6U÷W&ÂÂ&VfW&Væ6UöF÷væÆöFVBÂÆ7E÷&WVW7BÒÆöEö6öÖÖöå÷&VfW&Væ6R€¢F&vWBÀ¢6öÖÖöå÷&VfW&Væ6UöF—"À¢&w2æ6öÖÖöå÷&VfW&Væ6U÷W&ÂÀ¢Ö‚ƒãÂ&w2ç&WVW7EöFVÆ’’À¢Æ7E÷&WVW7BÀ¢¢6öÖÖöå÷&VfW&Væ6RÒ&Vw&–EöæV&W7B€¢6öÖÖöå÷&VfW&Væ6RÀ¢Vç6VÖ&ÆRæÆöç2À¢Vç6VÖ&ÆRæÆG2À¢b&6öÖÖöâ&VfW&Væ6R·F&vWGÒ"À¢¢6öÖÖöåöw&–BÒ7V'G&7Eöw&–G2†Vç6VÖ&ÆRÂ6öÖÖöå÷&VfW&Væ6R¢6öÖÖöåö÷WGWBÒ÷WGWEöF—"ò–æ—Bòb&6g7c%÷·&öGV7E²vf–ÆU÷Fö¶Vâu×Õ÷·F&vWGÕö6öÖÖöâÓ““Ó##æ§r ¢&VæFW%öÖ€¢6öÖÖöåöw&–BÀ¢–æ—BÀ¢F&vWBÀ¢ÆVBÀ¢ÖVÖ&W'2À¢6öÖÖöåö÷WGWBÀ¢æöÖÇ“ÕG'VRÀ¢&6VÆ–æUöÆ&VÃÔ4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢&÷&FW%÷F‡3Ö&÷&FW%÷F‡2À¢Vç6VÖ&ÆUöÆ&VÃÖVç6VÖ&ÆUöÆ&VÂÀ¢†V–v‡Eöw&–CÖVç6VÖ&ÆRÀ¢&öGV7E÷7V3×&öGV7BÀ¢¢F&vWEöVçG'•²&6ö×&—6öâ%ÒÒ°¢&6öÖÖöåó““ó###¢°¢&–ÖvR#¢&VÆF—fU÷F‚†6öÖÖöåö÷WGWBÂ&Wõ÷&ö÷B’À¢'7FGW2#¢'&VæFW&VB"À¢&&6VÆ–æR#¢°¢&Æ&VÂ#¢4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢'–V'2#¢4ôÔÔôåõ$TdU$Tä4Uõ”T%2À¢'6÷W&6R#¢$6å4•2c2†–æF67B6Æ–ÖFöÆöw’"À¢&f–ÆR#¢&VÆF—fU÷F‚‡&VfW&Væ6U÷F‚Â&Wõ÷&ö÷B’À¢'W&Â#¢&VfW&Væ6U÷W&Â÷"æöæRÀ¢&F÷væÆöFVB#¢&VfW&Væ6UöF÷væÆöFVBÀ¢ÒÀ¢Ð¢Ð¢W†6WBW†6WF–öâ2W†3 ¢F&vWEöVçG'•²&6ö×&—6öâ%ÒÒ°¢&6öÖÖöåó““ó###¢°¢'7FGW2#¢'Væf–Æ&ÆR"À¢&&6VÆ–æR#¢°¢&Æ&VÂ#¢4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢'–V'2#¢4ôÔÔôåõ$TdU$Tä4Uõ”T%2À¢'6÷W&6R#¢$6å4•2c2†–æF67B6Æ–ÖFöÆöw’"À¢ÒÀ¢&W'&÷"#¢7G"†W†2’À¢Ð¢Ð¢&–çB†b$4e7c"6öÖÖöâ6ö×&—6öâF&vWB·F&vWGÒVæf–Æ&ÆS¢¶W†7Ò"Âf–ÆS×7—2ç7FFW'"¢W†6WBW†6WF–öâ2W†3 ¢f–ÇW&W2³Ò¢F&vWEöVçG'•²'7FGW2%ÒÒ&f–ÆVB ¢F&vWEöVçG'•²&W'&÷"%ÒÒ7G"†W†2¢&–çB†b$4e7c"F&vWB·F&vWGÒÆVB¶ÆVGÒf–ÆVC¢¶W†7Ò"Âf–ÆS×7—2ç7FFW'"¢'VåöVçG'•²'F&vWG2%ÒæVæB‡F&vWEöVçG'’¢F&vWEöVçG&–W5ö'•öÆVE¶ÆVEÒÒF&vWEöVçG' ¢–b6V6öæÅöÆVG2æBæ÷B&w2æFV6öFUööæÇ“ ¢f—'7EöÆVBÒ6V6öæÅöÆVG5³Ð¢Æ7EöÆVBÒ6V6öæÅöÆVG5²ÓÐ¢f—'7E÷F&vWBÒF&vWEöÖöçF‚†–æ—BÂf—'7EöÆVB¢Æ7E÷F&vWBÒF&vWEöÖöçF‚†–æ—BÂÆ7EöÆVB¢6V6öæÅöVçG'’Ò°¢&–B#¢b&6g7c"×¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÒ×·&öGV7E²v–E÷Fö¶Vâu×Ò×6V6öæÂ"À¢'fÆ–E÷7F'E÷WF2#¢F&vWE÷W&–öB†f—'7E÷F&vWB•³ÒÀ¢'fÆ–EöVæE÷WF2#¢F&vWE÷W&–öB†Æ7E÷F&vWB•³ÒÀ¢&ÆVEöÖöçF‚#¢b'¶f—'7EöÆVGÒ×¶Æ7EöÆVGÒ"À¢'F&vWEöÖöçF‚#¢b'¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÒ"À¢&vw&VvF–öâ#¢€¢b'¶ÆVâ‡6V6öæÅöÆVG2—ÒÖÖöçF‚·&öGV7E²w6V6öæÅövw&VvF–öâu×Ò ¢’À¢&f–VÆB#¢&öGV7E²&f–VÆB%ÒÀ¢'Væ—G2#¢&öGV7E²'6V6öæÅ÷Væ—G2%ÒÀ¢'&uöf–VÆB#¢&öGV7E²'&uöf–VÆB%ÒÀ¢'&u÷Væ—G2#¢&öGV7E²'&u÷Væ—G2%ÒÀ¢'7FF—7F–2#¢&Vç6VÖ&ÆUöÖVâ"À¢&ÖVÖ&W'2#¢¶&w2ç&öÆÆ–æuöÖVÖ&W%Ò–b&öÆÆ–æuöÖöFRVÇ6RÖVÖ&W'2À¢&Vç6VÖ&ÆUöÖVÖ&W'2#¢Vç6VÖ&ÆUöW‡V7FVBÀ¢&Vç6VÖ&ÆU÷66÷R#¢'&öÆÆ–æuö–æ—F–Åö6öæF—F–öç2"–b&öÆÆ–æuöÖöFRVÇ6R'6–ævÆUö–æ—F–Åö6öæF—F–öåö7–6ÆR"À¢&ÖöçF†Ç•öÆVG2#¢6V6öæÅöÆVG2À¢'6÷W&6Uöf–ÆW2#¢µÒÀ¢'7FGW2#¢'ÆææVB"À¢Ð¢G'“ ¢Ö—76–æuöf÷&V67G2Ò¶ÆVBf÷"ÆVB–â6V6öæÅöÆVG2–bÆVBæ÷B–âf÷&V67Eöw&–G5Ð¢–bÖ—76–æuöf÷&V67G3 ¢&—6R4e7c$W'&÷"†b'6V6öæÂv–æF÷r—2Ö—76–ærFV6öFVBÆVB‡2“¢¶Ö—76–æuöf÷&V67G7Ò"¢6V6öæÅöf÷&V67BÒ€¢7VÕöw&–G2…¶f÷&V67Eöw&–G5¶ÆVEÒf÷"ÆVB–â6V6öæÅöÆVG5Ò¢–b&öGV7E²'6V6öæÅ÷&VGV6W"%ÒÓÒ'7VÒ ¢VÇ6RÖVåöw&–G2…¶f÷&V67Eöw&–G5¶ÆVEÒf÷"ÆVB–â6V6öæÅöÆVG5Ò¢¢6V6öæÅöw&–BÒ6V6öæÅöf÷&V67@¢&6VÆ–æUöÆ&VÂÒ&'6öÇWFRf–VÆB6Öö¶R÷WGWB ¢–bæ÷B'6öÇWFS ¢Ö—76–æuö&6VÆ–æW2Ò¶ÆVBf÷"ÆVB–â6V6öæÅöÆVG2–bÆVBæ÷B–â&6VÆ–æUöw&–G5Ð¢–bÖ—76–æuö&6VÆ–æW3 ¢&—6R4e7c$W'&÷"†b'6V6öæÂv–æF÷r—2Ö—76–ær&6VÆ–æRÆVB‡2“¢¶Ö—76–æuö&6VÆ–æW7Ò"¢6V6öæÅö&6VÆ–æRÒ€¢7VÕöw&–G2…¶&6VÆ–æUöw&–G5¶ÆVEÒf÷"ÆVB–â6V6öæÅöÆVG5Ò¢–b&öGV7E²'6V6öæÅ÷&VGV6W"%ÒÓÒ'7VÒ ¢VÇ6RÖVåöw&–G2…¶&6VÆ–æUöw&–G5¶ÆVEÒf÷"ÆVB–â6V6öæÅöÆVG5Ò¢¢6V6öæÅöw&–BÒ7V'G&7Eöw&–G2‡6V6öæÅöf÷&V67BÂ6V6öæÅö&6VÆ–æR¢&6VÆ–æUöÆ&VÂÒ6öæf–wW&VEö&6VÆ–æUöÆ&VÂ†&w2¢6V6öæÅöVçG'•²&&6VÆ–æR%ÒÒ°¢&f–ÆW2#¢°¢F&vWEöVçG&–W5ö'•öÆVE¶ÆVEÕ²&&6VÆ–æR%Õ²&f–ÆR%Ð¢f÷"ÆVB–â6V6öæÅöÆVG0¢–b&&6VÆ–æR"–âF&vWEöVçG&–W5ö'•öÆVBævWB†ÆVBÂ·Ò¢ÒÀ¢&Æ&VÂ#¢&6VÆ–æUöÆ&VÂÀ¢'–V'2#¢ä4T•ô4Ä”%$D”ôåõ”T%2–b&w2ææ6V•ö6Æ–'&F–öâVÇ6R†&w2æ&6VÆ–æU÷–V'2÷"æöæR’À¢Ð¢–b&öÆÆ–æuöÖöFS ¢6V6öæÅöVçG'•²&&6VÆ–æR%Õ²'&öÆÆ–æu÷öÆ–7’%ÒÒ&æ6†÷%ö–æ—F–Æ—¦F–öâ ¢6V6öæÅöVçG'•²&&6VÆ–æR%Õ²&æ6†÷%ö–æ—B%ÒÒ–æ—@¢&6VÆ–æU÷W&Ç2Ò°¢F&vWEöVçG&–W5ö'•öÆVE¶ÆVEÕ²&&6VÆ–æR%ÒævWB‚'W&Â"¢f÷"ÆVB–â6V6öæÅöÆVG0¢–bF&vWEöVçG&–W5ö'•öÆVE¶ÆVEÒævWB‚&&6VÆ–æR"Â·Ò’ævWB‚'W&Â"¢Ð¢–b&6VÆ–æU÷W&Ç3 ¢6V6öæÅöVçG'•²&&6VÆ–æR%Õ²'W&Ç2%ÒÒ&6VÆ–æU÷W&Ç0¢VÇ6S ¢6V6öæÅöVçG'•²&&6VÆ–æR%ÒÒ²'7FGW2#¢&æ÷EöÆ–6&ÆR"Â'&V6öâ#¢&'6öÇWFR6Öö¶R÷WGWB'Ð¢6V6öæÅöVçG'•²'6÷W&6Uöf–ÆW2%ÒÒ°¢6÷W&6Uöf–ÆP¢f÷"ÆVB–â6V6öæÅöÆVG0¢f÷"6÷W&6Uöf–ÆR–âF&vWEöVçG&–W5ö'•öÆVE¶ÆVEÒævWB‚'6÷W&6Uöf–ÆW2"ÂµÒ¢Ð¢6V6öæÅöVçG'•²&Vç6VÖ&ÆUö6ö×ÆWFR%ÒÒÆÂ€¢F&vWEöVçG&–W5ö'•öÆVE¶ÆVEÒævWB‚&Vç6VÖ&ÆUö6ö×ÆWFR"ÂfÇ6R¢f÷"ÆVB–â6V6öæÅöÆVG0¢¢6V6öæÅöVçG'•²&Vç6VÖ&ÆUöÖVÖ&W'2%ÒÒÖ–â€¢F&vWEöVçG&–W5ö'•öÆVE¶ÆVEÒævWB‚&Vç6VÖ&ÆUöÖVÖ&W'2"Â¢f÷"ÆVB–â6V6öæÅöÆVG0¢¢7F'EöFFRÒGBæFFWF–ÖRç7G'F–ÖR†f—'7E÷F&vWBÂ"U’VÒ"¢VæEöFFRÒGBæFFWF–ÖRç7G'F–ÖR†Æ7E÷F&vWBÂ"U’VÒ"¢W&–öEöÆ&VÂÒ6V6öæÅ÷W&–öEöÆ&VÂ†f—'7E÷F&vWBÂÆ7E÷F&vWB¢÷WGWE÷F‚Ò÷WGWEöF—"ò–æ—Bòb&6g7c%÷·&öGV7E²vf–ÆU÷Fö¶Vâu×Õ÷¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÒæ§r ¢&VæFW%öÖ€¢6V6öæÅöw&–BÀ¢–æ—BÀ¢f—'7E÷F&vWBÀ¢b'¶f—'7EöÆVGÕÇS#7¶Æ7EöÆVGÒ"À¢ÖVÖ&W'2À¢÷WGWE÷F‚À¢æöÖÇ“Öæ÷B'6öÇWFRÀ¢&6VÆ–æUöÆ&VÃÖ&6VÆ–æUöÆ&VÂÀ¢&÷&FW%÷F‡3Ö&÷&FW%÷F‡2À¢W&–öEöÆ&VÃ×W&–öEöÆ&VÂÀ¢6V6öæÃÕG'VRÀ¢Vç6VÖ&ÆUöÆ&VÃÒ€¢b'·6V6öæÅöVçG'•²vVç6VÖ&ÆUöÖVÖ&W'2u×Ò÷¶Vç6VÖ&ÆUöW‡V7FVGÒÖ7–6ÆR&öÆÆ–ærÖVâ ¢–b&öÆÆ–æuöÖöFP¢VÇ6Rb'¶ÆVâ†ÖVÖ&W'2—ÒÖÖVÖ&W"ÖVâ ¢’À¢†V–v‡Eöw&–C×6V6öæÅöf÷&V67B–b&öGV7E²&†V–v‡Eö6öçF÷W'2%ÒVÇ6RæöæRÀ¢&öGV7E÷7V3×&öGV7BÀ¢¢6V6öæÅöVçG'•²&–ÖvR%ÒÒ&VÆF—fU÷F‚†÷WGWE÷F‚Â&Wõ÷&ö÷B¢6V6öæÅöVçG'•²'7FGW2%ÒÒ'&VæFW&VB"–b6V6öæÅöVçG'•²&Vç6VÖ&ÆUö6ö×ÆWFR%ÒVÇ6R''F–Â ¢&–çB†b'&VæFW&VB4e7c"6V6öæÂ&öGV7B¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÓ¢¶÷WGWE÷F‡Ò"¢–b6öÖÖöå÷&VfW&Væ6UöVæ&ÆVC ¢G'“ ¢6öÖÖöå÷&VfW&Væ6W2ÒµÐ¢&VfW&Væ6Uöf–ÆW2ÒµÐ¢&VfW&Væ6U÷W&Ç2ÒµÐ¢f÷"ÆVB–â6V6öæÅöÆVG3 ¢F&vWBÒF&vWEöÖöçF‚†–æ—BÂÆVB¢&VfW&Væ6RÂ&VfW&Væ6U÷F‚Â&VfW&Væ6U÷W&ÂÂ&VfW&Væ6UöF÷væÆöFVBÂÆ7E÷&WVW7BÒÆöEö6öÖÖöå÷&VfW&Væ6R€¢F&vWBÀ¢6öÖÖöå÷&VfW&Væ6UöF—"À¢&w2æ6öÖÖöå÷&VfW&Væ6U÷W&ÂÀ¢Ö‚ƒãÂ&w2ç&WVW7EöFVÆ’’À¢Æ7E÷&WVW7BÀ¢¢6öÖÖöå÷&VfW&Væ6W2æVæB‡&Vw&–EöæV&W7B€¢&VfW&Væ6RÀ¢6V6öæÅöf÷&V67BæÆöç2À¢6V6öæÅöf÷&V67BæÆG2À¢b&6öÖÖöâ&VfW&Væ6R·F&vWGÒ"À¢’¢&VfW&Væ6Uöf–ÆW2æVæB‡&VÆF—fU÷F‚‡&VfW&Væ6U÷F‚Â&Wõ÷&ö÷B’¢–b&VfW&Væ6U÷W&Ã ¢&VfW&Væ6U÷W&Ç2æVæB‡&VfW&Væ6U÷W&Â¢6öÖÖöåö&6VÆ–æRÒ€¢7VÕöw&–G2†6öÖÖöå÷&VfW&Væ6W2¢–b&öGV7E²'6V6öæÅ÷&VGV6W"%ÒÓÒ'7VÒ ¢VÇ6RÖVåöw&–G2†6öÖÖöå÷&VfW&Væ6W2¢¢6öÖÖöåöw&–BÒ7V'G&7Eöw&–G2‡6V6öæÅöf÷&V67BÂ6öÖÖöåö&6VÆ–æR¢6öÖÖöåö÷WGWBÒ÷WGWEöF—"ò–æ—Bòb&6g7c%÷·&öGV7E²vf–ÆU÷Fö¶Vâu×Õ÷¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÕö6öÖÖöâÓ““Ó##æ§r ¢&VæFW%öÖ€¢6öÖÖöåöw&–BÀ¢–æ—BÀ¢f—'7E÷F&vWBÀ¢b'¶f—'7EöÆVGÕÇS#7¶Æ7EöÆVGÒ"À¢ÖVÖ&W'2À¢6öÖÖöåö÷WGWBÀ¢æöÖÇ“ÕG'VRÀ¢&6VÆ–æUöÆ&VÃÔ4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢&÷&FW%÷F‡3Ö&÷&FW%÷F‡2À¢W&–öEöÆ&VÃ×W&–öEöÆ&VÂÀ¢6V6öæÃÕG'VRÀ¢Vç6VÖ&ÆUöÆ&VÃÒ€¢b'·6V6öæÅöVçG'•²vVç6VÖ&ÆUöÖVÖ&W'2u×Ò÷¶Vç6VÖ&ÆUöW‡V7FVGÒÖ7–6ÆR&öÆÆ–ærÖVâ ¢–b&öÆÆ–æuöÖöFP¢VÇ6Rb'¶ÆVâ†ÖVÖ&W'2—ÒÖÖVÖ&W"ÖVâ ¢’À¢†V–v‡Eöw&–C×6V6öæÅöf÷&V67BÀ¢&öGV7E÷7V3×&öGV7BÀ¢¢6V6öæÅöVçG'•²&6ö×&—6öâ%ÒÒ°¢&6öÖÖöåó““ó###¢°¢&–ÖvR#¢&VÆF—fU÷F‚†6öÖÖöåö÷WGWBÂ&Wõ÷&ö÷B’À¢'7FGW2#¢'&VæFW&VB"À¢&&6VÆ–æR#¢°¢&Æ&VÂ#¢4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢'–V'2#¢4ôÔÔôåõ$TdU$Tä4Uõ”T%2À¢'6÷W&6R#¢$6å4•2c2†–æF67B6Æ–ÖFöÆöw’"À¢&f–ÆW2#¢&VfW&Væ6Uöf–ÆW2À¢'W&Ç2#¢&VfW&Væ6U÷W&Ç2À¢ÒÀ¢Ð¢Ð¢W†6WBW†6WF–öâ2W†3 ¢6V6öæÅöVçG'•²&6ö×&—6öâ%ÒÒ°¢&6öÖÖöåó““ó###¢°¢'7FGW2#¢'Væf–Æ&ÆR"À¢&&6VÆ–æR#¢°¢&Æ&VÂ#¢4ôÔÔôåõ$TdU$Tä4UôÄ$TÂÀ¢'–V'2#¢4ôÔÔôåõ$TdU$Tä4Uõ”T%2À¢'6÷W&6R#¢$6å4•2c2†–æF67B6Æ–ÖFöÆöw’"À¢ÒÀ¢&W'&÷"#¢7G"†W†2’À¢Ð¢Ð¢&–çB€¢b$4e7c"6öÖÖöâ6ö×&—6öâ6V6öæÂv–æF÷r¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÒVæf–Æ&ÆS¢¶W†7Ò"À¢f–ÆS×7—2ç7FFW'"À¢¢W†6WBW†6WF–öâ2W†3 ¢f–ÇW&W2³Ò¢6V6öæÅöVçG'•²'7FGW2%ÒÒ&f–ÆVB ¢6V6öæÅöVçG'•²&W'&÷"%ÒÒ7G"†W†2¢&–çB†b$4e7c"6V6öæÂv–æF÷r¶f—'7E÷F&vWGÒ×¶Æ7E÷F&vWGÒf–ÆVC¢¶W†7Ò"Âf–ÆS×7—2ç7FFW'"¢'VåöVçG'•²'F&vWG2%ÒæVæB‡6V6öæÅöVçG'’ ¢7FGW6W2Ò·F&vWE²'7FGW2%Òf÷"F&vWB–â'VåöVçG'•²'F&vWG2%ÕÐ¢'F–Å÷F&vWG2Òç’‡7FGW2ÓÒ''F–Â"f÷"7FGW2–â7FGW6W2¢–bf–ÇW&W2÷"'F–Å÷F&vWG3 ¢'VåöVçG'•²'7FGW2%ÒÒ''F–Â"–bç’‡7FGW2Ò&f–ÆVB"f÷"7FGW2–â7FGW6W2’VÇ6R&f–ÆVB ¢VÆ–b&w2æFV6öFUööæÇ“ ¢'VåöVçG'•²'7FGW2%ÒÒ&FV6öFVB ¢VÇ6S ¢'VåöVçG'•²'7FGW2%ÒÒ'&VæFW&VB ¢'VåöVçG'•²&÷WGWEöF—"%ÒÒ&VÆF—fU÷F‚†÷WGWEöF—"Â&Wõ÷&ö÷B¢&Wf–÷W5öÖæ–fW7BÒ&W6öÇfU÷&Wõ÷F‚†&w2ç&Wf–÷W5öÖæ–fW7BÂ&Wõ÷&ö÷B’–b&w2ç&Wf–÷W5öÖæ–fW7BVÇ6RæöæP¢w&—FUöÖæ–fW7B†Öæ–fW7E÷F‚Â&Wõ÷&ö÷BÂ'VåöVçG'’Â&Wf–÷W5öÖæ–fW7BÂ&w2ç&WF–å÷'Vç2¢&–çB†b'w&÷FR4e7c"Öæ–fW7C¢¶Öæ–fW7E÷F‡Ò"¢&WGW&â"–bf–ÇW&W2VÇ6R   ¦FVbÖ–â‚’Óâ–çC ¢'6W"Ò'V–ÆE÷'6W"‚¢&w2Ò'6W"ç'6Uö&w2‚¢G'“ ¢&WGW&â'Vâ†&w2¢W†6WB4e7c$W'&÷"2W†3 ¢&–çB†b$4e7c"W'&÷#¢¶W†7Ò"Âf–ÆS×7—2ç7FFW'"¢&WGW&â   ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢&—6R7—7FVÔW†—B†Ö–â‚’ 