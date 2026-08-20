#!/usr/bin/env python3
"""Render numerical NASA GEOS-S2S-3 seasonal anomaly guidance.

NASA's NCCS data share publishes monthly NetCDF forecasts as a 40-member
lag/burst package. Ten selected members continue through month nine. This
adapter forms the available-member mean for each target, subtracts NASA's
lead-matched provider drift climatology, and uses the shared seasonal renderer.

The public long-range archive currently named ``z500`` is validated strictly.
It is rejected unless the NetCDF pressure coordinate is exactly 500 hPa; this
prevents the current 200-hPa extraction from being published as a 500-mb map.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CONUS_PRECIP_REGION,
    DEFAULT_REGION,
    Grid,
    MSLP_ANOMALY_PALETTE,
    MSLP_ANOMALY_TICKS,
    PRECIP_ANOMALY_PALETTE,
    PRECIP_ANOMALY_TICKS,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    download_file,
    ensure_border_files,
    land_mask_from_borders,
    mean_grids,
    prepare_product_grid,
    relative_path,
    render_map,
    seasonal_period_label,
    subtract_grids,
    sum_grids,
)


NASA_DATA_ROOT = "https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/"
NASA_NRT_ROOT = urljoin(NASA_DATA_ROOT, "NRT/APCN/")
NASA_DRIFT_ROOT = urljoin(NASA_DATA_ROOT, "Drift/for_APCN/")
NASA_PRIMER_URL = urljoin(NASA_DATA_ROOT, "GEOS-S2S-3-primer.pdf")
NASA_ATMRIVER_ROOT = urljoin(NASA_DATA_ROOT, "NRT/AtmRiver/")
NASA_HISTORY_CONFIG_URL = (
    "https://github.com/GEOS-ESM/GEOS-S2S-3/blob/main/"
    "src/Applications/GEOSgcm_App/HISTORY.AOGCM-S2Sv3.rc.tmpl"
)

EXPECTED_TOTAL_MEMBERS = 40
EXPECTED_LONG_RANGE_MEMBERS = 10
MAX_LEAD = 8
DRIFT_LABEL = "NASA GEOS-S2S-3 provider drift climatology"

SST_ANOMALY_TICKS = list(range(-3, 4))
SST_ANOMALY_PALETTE = [
    "#28567f", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5", "#b84c5a",
]

PRODUCT_Z500_ANOMALY = "500mb_height_anomaly"
PRODUCT_T850_ANOMALY = "850mb_temperature_anomaly"
PRODUCT_T2M_ANOMALY = "2m_temperature_anomaly"
PRODUCT_PRECIPITATION_ANOMALY = "precipitation_anomaly"
PRODUCT_MSLP_ANOMALY = "mslp_anomaly"
PRODUCT_SST_ANOMALY = "sea_surface_temperature_anomaly"

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    PRODUCT_Z500_ANOMALY: {
        "name": PRODUCT_Z500_ANOMALY,
        "archive_token": "z500",
        "forecast_variable": "H",
        "drift_variable": "z500",
        "expected_units": ("m",),
        "expected_level": 500.0,
        "id_token": "z500a",
        "title": "GEOS-S2S-3 500-mb Geopotential Height & Anomaly (m)",
        "absolute_title": "GEOS-S2S-3 500-mb Geopotential Height (m)",
        "field": "z500_anomaly",
        "raw_field": "H at 500 hPa",
        "raw_units": "m",
        "units": "m",
        "height_contours": True,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": -100.0,
        "anomaly_max": 100.0,
        "anomaly_ticks": ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Height contours in dam",
        "scheduled": False,
    },
    PRODUCT_T850_ANOMALY: {
        "name": PRODUCT_T850_ANOMALY,
        "archive_token": "t850",
        "forecast_variable": "T",
        "drift_variable": "t850",
        "expected_units": ("K",),
        "expected_level": 850.0,
        "id_token": "t850a",
        "title": "GEOS-S2S-3 850-mb Temperature Anomaly (Â°C)",
        "absolute_title": "GEOS-S2S-3 850-mb Temperature (Â°C)",
        "field": "temperature_850mb_anomaly",
        "raw_field": "T at 850 hPa",
        "raw_units": "K",
        "units": "Â°C",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  850-mb temperature anomaly (Â°C)",
        "scheduled": True,
    },
    PRODUCT_T2M_ANOMALY: {
        "name": PRODUCT_T2M_ANOMALY,
        "archive_token": "at",
        "forecast_variable": "T2M",
        "drift_variable": "at",
        "expected_units": ("K",),
        "expected_level": None,
        "id_token": "t2ma",
        "title": "GEOS-S2S-3 2-m Temperature Anomaly (Â°C)",
        "absolute_title": "GEOS-S2S-3 2-m Temperature (Â°C)",
        "field": "temperature_2m_anomaly",
        "raw_field": "T2M at 2 m",
        "raw_units": "K",
        "units": "Â°C",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  2-m temperature anomaly (Â°C)",
        "scheduled": True,
    },
    PRODUCT_PRECIPITATION_ANOMALY: {
        "name": PRODUCT_PRECIPITATION_ANOMALY,
        "archive_token": "precip",
        "forecast_variable": "PRECTOTCORR",
        "drift_variable": "precip",
        "expected_units": ("kg m-2 s-1", "kg/m2/s"),
        "expected_level": None,
        "id_token": "prcpa",
        "title": "GEOS-S2S-3 CONUS Precipitation Anomaly (in)",
        "absolute_title": "GEOS-S2S-3 CONUS Precipitation (in)",
        "field": "precipitation_anomaly",
        "raw_field": "PRECTOTCORR at the surface",
        "raw_units": "kg m-2 s-1",
        "units": "in",
        "height_contours": False,
        "region": CONUS_PRECIP_REGION,
        "seasonal_reducer": "sum",
        "conversion_kind": "monthly_precipitation_total_inches",
        "conversion": "Monthly mean precipitation rate multiplied by calendar-month seconds and converted to inches",
        "anomaly_min": -8.0,
        "anomaly_max": 8.0,
        "anomaly_ticks": PRECIP_ANOMALY_TICKS,
        "anomaly_palette": PRECIP_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Precipitation anomaly (in)  â€¢  CONUS domain",
        "scheduled": True,
    },
    PRODUCT_MSLP_ANOMALY: {
        "name": PRODUCT_MSLP_ANOMALY,
        "archive_token": "slp",
        "forecast_variable": "SLP",
        "drift_variable": "slp",
        "expected_units": ("Pa",),
        "expected_level": None,
        "id_token": "mslpa",
        "title": "GEOS-S2S-3 MSLP Anomaly (hPa)",
        "absolute_title": "GEOS-S2S-3 Mean Sea-Level Pressure (hPa)",
        "field": "mslp_anomaly",
        "raw_field": "SLP at mean sea level",
        "raw_units": "Pa",
        "units": "hPa",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "Sea-level pressure divided by 100 after anomaly calculation",
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": list(range(-10, 11)),
        "anomaly_palette": MSLP_ANOMALY_PALETTE,
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Mean sea-level pressure anomaly (hPa)",
        "scheduled": True,
    },
    PRODUCT_SST_ANOMALY: {
        "name": PRODUCT_SST_ANOMALY,
        "archive_token": "sst",
        "forecast_variable": "TS",
        "drift_variable": "sst",
        "expected_units": ("K",),
        "expected_level": None,
        "id_token": "ssta",
        "title": "GEOS-S2S-3 Sea-Surface Temperature Anomaly (Â°C)",
        "absolute_title": "GEOS-S2S-3 Sea-Surface Temperature (Â°C)",
        "field": "sea_surface_temperature_anomaly",
        "raw_field": "TS surface skin temperature, ocean cells retained",
        "raw_units": "K",
        "units": "Â°C",
        "height_contours": False,
        "region": DEFAULT_REGION,
        "seasonal_reducer": "mean",
        "anomaly_min": -3.0,
        "anomaly_max": 3.0,
        "anomaly_ticks": SST_ANOMALY_TICKS,
        "anomaly_palette": SST_ANOMALY_PALETTE,
        "map_domain": "ocean",
        "source_label": "NASA GEOS-S2S-3 / NCCS",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Sea-surface temperature anomaly (Â°C)",
        "mask_land": True,
        "scheduled": True,
    },
}

DEFAULT_PRODUCTS = tuple(name for name, spec in PRODUCT_SPECS.items() if spec["scheduled"])
SUPERENSEMBLE_PRODUCTS = frozenset(DEFAULT_PRODUCTS)
PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_T850_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_T2M_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "CONUS Precipitation Anomaly",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
    PRODUCT_SST_ANOMALY: "Sea-Surface Temperature Anomaly",
}


class GEOSS2S3Error(RuntimeError):
    """A user-actionable NASA source, validation, or rendering error."""


@dataclass(frozen=True)
class ForecastMonth:
    grid: Grid
    target: str
    members: tuple[str, ...]
    expected_members: int
    source_files: tuple[str, ...]
    init_dates: tuple[str, ...]


@dataclass(frozen=True)
class GEOSMonth:
    anomaly: Grid
    forecast: Grid
    target: str
    members: tuple[str, ...]
    expected_members: int
    source_files: tuple[str, ...]
    init_dates: tuple[str, ...]
    archive_url: str
    drift_url: str
    drift_years: tuple[int, ...]


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    date = dt.datetime.strptime(init, "%Y%m")
    year, month = month_after(date.year, date.month, lead)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m").replace(tzinfo=dt.timezone.utc)
    year, month = month_after(start.year, start.month, 1)
    return iso_utc(start), iso_utc(dt.datetime(year, month, 1, tzinfo=dt.timezone.utc))


def parse_int_list(value: str, label: str) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise GEOSS2S3Error(f"invalid {label}: {item}") from exc
        if not 0 <= number <= MAX_LEAD:
            raise GEOSS2S3Error(f"{label} must stay between 0 and {MAX_LEAD}")
        if number not in result:
            result.append(number)
    if not result:
        raise GEOSS2S3Error(f"{label} cannot be empty")
    return result


def _request_text(url: str) -> str:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise GEOSS2S3Error("NASA GEOS-S2S-3 downloads require requests") from exc
    try:
        response = requests.get(url, timeout=(30, 120))
        response.raise_for_status()
        return response.text
    except Exception as exc:
        raise GEOSS2S3Error(f"could not read NASA directory {url}: {exc}") from exc


def discover_latest_init(root: str = NASA_NRT_ROOT) -> str:
    issues = sorted(set(re.findall(r'href="(\d{6})/"', _request_text(root))), reverse=True)
    if not issues:
        raise GEOSS2S3Error("NASA APCN directory contains no YYYYMM release")
    return issues[0]


def parse_init(value: str, root: str = NASA_NRT_ROOT) -> str:
    if value == "latest":
        return discover_latest_init(root)
    if not re.fullmatch(r"\d{6}", value):
        raise GEOSS2S3Error("--init must be latest or YYYYMM")
    try:
        dt.datetime.strptime(value, "%Y%m")
    except ValueError as exc:
        raise GEOSS2S3Error(f"invalid NASA release month: {value}") from exc
    return value


def selected_products(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(DEFAULT_PRODUCTS)
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in PRODUCT_SPECS]
    if unknown:
        raise GEOSS2S3Error(f"unsupported NASA product(s): {', '.join(unknown)}")
    if not names:
        raise GEOSS2S3Error("--product cannot be empty")
    return list(dict.fromkeys(names))


def archive_url(init: str, spec: dict[str, Any], root: str = NASA_NRT_ROOT) -> str:
    return urljoin(root.rstrip("/") + "/", f"{init}/{init}_{spec['archive_token']}.tar.xz")


def archive_path(cache_dir: Path, init: str, spec: dict[str, Any]) -> Path:
    return cache_dir / "forecast" / init / f"{init}_{spec['archive_token']}.tar.xz"


def drift_url(init: str, target: str, root: str = NASA_DRIFT_ROOT) -> str:
    init_name = dt.datetime.strptime(init, "%Y%m").strftime("%b").lower()
    return urljoin(root.rstrip("/") + "/", f"{init_name}.APCN.monthly.drift.{target[4:6]}.nc4")


def drift_path(cache_dir: Path, init: str, target: str) -> Path:
    return cache_dir / "drift" / Path(drift_url(init, target)).name


def _download(url: str, destination: Path, request_delay: float = 0.0) -> None:
    try:
        download_file(url, destination, max(0.0, request_delay), 0.0, attempts=3, timeout=(60, 1200))
    except Exception as exc:
        raise GEOSS2S3Error(f"NASA download failed for {url}: {exc}") from exc


def _normal_units(value: Any) -> str:
    return re.sub(r"[^a-z0-9-]", "", str(value).lower())


def _open_data_array(dataset, variable: str, spec: dict[str, Any], source: str):
    if variable not in dataset:
        candidates = [name for name in dataset.data_vars if name != "time_bnds"]
        if len(candidates) != 1:
            raise GEOSS2S3Error(
                f"{source} does not contain expected variable {variable}; found {', '.join(candidates) or 'none'}"
            )
        variable = candidates[0]
    array = dataset[variable]
    expected_units = {_normal_units(item) for item in spec["expected_units"]}
    actual_units = _normal_units(array.attrs.get("units", ""))
    if actual_units not in expected_units:
        raise GEOSS2S3Error(
            f"{source} {variable} units are {array.attrs.get('units', 'missing')!r}; expected {spec['expected_units']}"
        )
  ãN;¶‰žËkºwµç@€É•ÍÕ±Ñm±•…‘t€ô=M5½¹Ñ  (€€€€€€€€€€€…¹½µ…±äõ…¹½µ…±ä°(€€€€€€€€€€€™½É•…ÍÐõÁÉ•Á…É•‘}™½É•…ÍÐ°(€€€€€€€€€€€Ñ…É•Ðõ™½É•…ÍÐ¹Ñ…É•Ð°(€€€€€€€€€€€µ•µ‰•ÉÌõ™½É•…ÍÐ¹µ•µ‰•ÉÌ°(€€€€€€€€€€€•áÁ•Ñ•‘}µ•µ‰•ÉÌõ™½É•…ÍÐ¹•áÁ•Ñ•‘}µ•µ‰•ÉÌ°(€€€€€€€€€€€Í½ÕÉ•}™¥±•Ìõ™½É•…ÍÐ¹Í½ÕÉ•}™¥±•Ì°(€€€€€€€€€€€¥¹¥Ñ}‘…Ñ•Ìõ™½É•…ÍÐ¹¥¹¥Ñ}‘…Ñ•Ì°(€€€€€€€€€€€…É¡¥Ù•}ÕÉ°õÍ½ÕÉ•}ÕÉ°°(€€€€€€€€€€€‘É¥™Ñ}ÕÉ°õ‰…Í•±¥¹•}ÕÉ°°(€€€€€€€€€€€‘É¥™Ñ}å•…ÉÌõå•…ÉÌ°(€€€€€€€€¤(€€€É•ÑÕÉ¸É•ÍÕ±Ð(()‘•˜}¥¹¥Ñ¥…±¥é…Ñ¥½¹}É…¹”¡¥¹¥Ñ}‘…Ñ•ÌèM•ÅÕ•¹•mÍÑÉt¤€´øÑÕÁ±•mÍÑÈ°ÍÑÈ°ÍÑÉtè(€€€Á…ÉÍ•€ôÍ½ÉÑ•¡‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡Ù…±Õ”°€ˆ•d•´•ˆ¤™½ÈÙ…±Õ”¥¸Í•Ð¡¥¹¥Ñ}‘…Ñ•Ì¤¤(€€€¥˜¹½ÐÁ…ÉÍ•è(€€€€€€€É…¥Í”=MLÉLÍÉÉ½È ‰9Mµ•µ‰•Èµ•Ñ…‘…Ñ„½¹Ñ…¥¹Ì¹¼¥¹¥Ñ¥…±¥é…Ñ¥½¸‘…Ñ•Ìˆ¤(€€€ÍÑ…ÉÐ°•¹€ôÁ…ÉÍ•‘lÁt°Á…ÉÍ•‘l´Åt(€€€±…‰•°€ô€ (€€€€€€€˜‰%¹¥ÐíÍÑ…ÉÐè•‘÷ŠMí•¹è•€•ˆ€•eô±…•ˆ(€€€€€€€¥˜ÍÑ…ÉÐ¹å•…È€ôô•¹¹å•…È…¹ÍÑ…ÉÐ¹µ½¹Ñ €ôô•¹¹µ½¹Ñ (€€€€€€€•±Í”˜‰%¹¥ÐíÍÑ…ÉÐè•€•‰÷ŠMí•¹è•€•ˆ€•eô±…•ˆ(€€€€¤(€€€É•ÑÕÉ¸€ (€€€€€€€¥Í½}ÕÑŒ¡ÍÑ…ÉÐ¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€¥Í½}ÕÑŒ ¡•¹€¬‘Ð¹Ñ¥µ•‘•±Ñ„¡‘…åÌôÄ¤¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€±…‰•°°(€€€€¤(()‘•˜}‰…Í•±¥¹•}µ•Ñ…‘…Ñ„¡µ½¹Ñ¡ÌèM•ÅÕ•¹•m=M5½¹Ñ¡t¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€å•…ÉÌ€ôÍ½ÉÑ•¡íå•…È™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ì™½Èå•…È¥¸µ½¹Ñ ¹‘É¥™Ñ}å•…ÉÍô¤(€€€±…‰•°€ôI%Q}1	0€¬€¡˜ˆ€¡íå•…ÉÍlÁu÷ŠMíå•…ÉÍl´Åuô¤ˆ¥˜å•…ÉÌ•±Í”€ˆˆ¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰¹…Ñ¥Ù•}ÁÉ½Ù¥‘•É}‘É¥™Ðˆ°(€€€€€€€€‰Í½ÕÉ”ˆè±…‰•°°(€€€€€€€€‰å•…ÉÌˆè˜‰íå•…ÉÍlÁuôµíå•…ÉÍl´Åuôˆ¥˜å•…ÉÌ•±Í”€‰ÁÉ½Ù¥‘•ÈÍÕÁÁ±¥•ˆ°(€€€€€€€€‰µ•Ñ¡½ˆè€‰±•…´…¹¥¹¥Ñ¥…±¥é…Ñ¥½¸µµ½¹Ñ µµ…Ñ¡•9M¡¥¹‘…ÍÐ•¹Í•µ‰±”µ•…¸ˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèmµ½¹Ñ ¹‘É¥™Ñ}ÕÉ°™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ít°(€€€ô(()‘•˜}Ñ…É•Ñ}•¹ÑÉä (€€€€¨°(€€€ÉÕ¹}¥èÍÑÈ°(€€€ÁÉ½‘ÕÐèÍÑÈ°(€€€±•…è¥¹ÐðÍÑÈ°(€€€Ñ…É•ÐèÍÑÈ°(€€€Á•É¥½‘}±…‰•°èÍÑÈ°(€€€µ½¹Ñ¡ÌèM•ÅÕ•¹•m=M5½¹Ñ¡t°(€€€¥µ…”èÍÑÈð9½¹”°(€€€ÍÑ…ÑÕÌèÍÑÈ°(¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€ÍÁ•Œ€ôAI=UQ}MAMmÁÉ½‘ÕÑt(€€€ÍÑ…ÉÑ}Ñ…É•Ð°•¹‘}Ñ…É•Ð€ôÑ…É•Ð¹ÍÁ±¥Ð ˆ´ˆ¥lÁt°Ñ…É•Ð¹ÍÁ±¥Ð ˆ´ˆ¥l´Åt(€€€Ù…±¥‘}ÍÑ…ÉÐ°|€ôÑ…É•Ñ}Á•É¥½¡ÍÑ…ÉÑ}Ñ…É•Ð¤(€€€|°Ù…±¥‘}•¹€ôÑ…É•Ñ}Á•É¥½¡•¹‘}Ñ…É•Ð¤(€€€µ•µ‰•ÉÌ€ôÍ•Ð¡µ½¹Ñ¡ÍlÁt¹µ•µ‰•ÉÌ¤(€€€™½Èµ½¹Ñ ¥¸µ½¹Ñ¡ÍlÄétè(€€€€€€€µ•µ‰•ÉÌ€˜ôÍ•Ð¡µ½¹Ñ ¹µ•µ‰•ÉÌ¤(€€€¥¹¥Ñ}‘…Ñ•Ì€ôÍ½ÉÑ•¡íÙ…±Õ”™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ì™½ÈÙ…±Õ”¥¸µ½¹Ñ ¹¥¹¥Ñ}‘…Ñ•Íô¤(€€€¥¹¥Ñ}ÍÑ…ÉÐ°¥¹¥Ñ}•¹°|€ô}¥¹¥Ñ¥…±¥é…Ñ¥½¹}É…¹”¡¥¹¥Ñ}‘…Ñ•Ì¤(€€€•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµíÑ…É•Ñôˆ°(€€€€€€€€‰±…‰•°ˆèÁ•É¥½‘}±…‰•°°(€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ð°(€€€€€€€€‰Á•É¥½‘}±…‰•°ˆèÁ•É¥½‘}±…‰•°°(€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÙ…±¥‘}ÍÑ…ÉÐ°(€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÙ…±¥‘}•¹°(€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€‰™¥•±ˆèÍÁ•l‰™¥•±‰t°(€€€€€€€€‰Õ¹¥ÑÌˆèÍÁ•l‰Õ¹¥ÑÌ‰t°(€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰9M=LµLÉL´Ì±…œ½‰ÕÉÍÐ•¹Í•µ‰±”µ•…¸…¹½µ…±äˆ°(€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè±•¸¡µ•µ‰•ÉÌ¤°(€€€€€€€€‰•¹Í•µ‰±•}•áÁ•Ñ•‘}µ•µ‰•ÉÌˆèµ¥¸¡µ½¹Ñ ¹•áÁ•Ñ•‘}µ•µ‰•ÉÌ™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ì¤°(€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€‰±½¹œµÉ…¹”Í•±•Ñ•µ•µ‰•ÉÌˆ¥˜µ¥¸¡µ½¹Ñ ¹•áÁ•Ñ•‘}µ•µ‰•ÉÌ™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ì¤€ôô€ÄÀ•±Í”€‰™Õ±°±…œ½‰ÕÉÍÐµ•µ‰•ÉÌˆ°(€€€€€€€€‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}ÍÑ…ÉÑ}ÕÑŒˆè¥¹¥Ñ}ÍÑ…ÉÐ°(€€€€€€€€‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}•¹‘}ÕÑŒˆè¥¹¥Ñ}•¹°(€€€€€€€€‰Í½ÕÉ•}…É¡¥Ù•}ÕÉ°ˆèµ½¹Ñ¡ÍlÁt¹…É¡¥Ù•}ÕÉ°°(€€€€€€€€‰Í½ÕÉ•}™¥±•ÌˆèÍ½ÉÑ•¡í¹…µ”™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ì™½È¹…µ”¥¸µ½¹Ñ ¹Í½ÕÉ•}™¥±•Íô¤°(€€€€€€€€‰‰…Í•±¥¹”ˆè}‰…Í•±¥¹•}µ•Ñ…‘…Ñ„¡µ½¹Ñ¡Ì¤°(€€€€€€€€‰ÍÑ…ÑÕÌˆèÍÑ…ÑÕÌ°(€€€ô(€€€¥˜¥µ…”è(€€€€€€€•¹ÑÉål‰¥µ…”‰t€ô¥µ…”(€€€É•ÑÕÉ¸•¹ÑÉä(()‘•˜ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡Á…Ñ èA…Ñ °•¹ÑÉ¥•Ìè%Ñ•É…‰±•m‘¥ÑmÍÑÈ°¹åut°ÁÉ•Ù¥½ÕÌèA…Ñ ð9½¹”°É•Ñ…¥¹}å±•Ìè¥¹Ð¤€´ø9½¹”è(€€€¥˜É•Ñ…¥¹}å±•Ì€ð€Äè(€€€€€€€É…¥Í”=MLÉLÍÉÉ½È ‰µ…¹¥™•ÍÐÉ•Ñ•¹Ñ¥½¸µÕÍÐ­••À…Ð±•…ÍÐ½¹”É•±•…Í”å±”ˆ¤(€€€…±±}•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½È•á¥ÍÑ¥¹}Á…Ñ ¥¸€¡ÁÉ•Ù¥½ÕÌ°Á…Ñ ¤è(€€€€€€€¥˜¹½Ð•á¥ÍÑ¥¹}Á…Ñ ½È¹½Ð•á¥ÍÑ¥¹}Á…Ñ ¹•á¥ÍÑÌ ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€ÑÉäè(€€€€€€€€€€€Á…å±½…€ô©Í½¸¹±½…‘Ì¡•á¥ÍÑ¥¹}Á…Ñ ¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡ÉÕ¸™½ÈÉÕ¸¥¸Á…å±½…¹•Ð ‰ÉÕ¹Ìˆ°mt¤¥˜¥Í¥¹ÍÑ…¹”¡ÉÕ¸°‘¥Ð¤¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”=MLÉLÍÉÉ½È¡˜‰½Õ±¹½ÐÉ•…ÁÉ•Ù¥½ÕÌ9Mµ…¹¥™•ÍÐí•á¥ÍÑ¥¹}Á…Ñ¡ôèí•áôˆ¤™É½´•áŒ(€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡•¹ÑÉ¥•Ì¤(€€€Õ¹¥ÅÕ”€ôíÍÑÈ¡ÉÕ¸¹•Ð ‰¥ˆ¤¤èÉÕ¸™½ÈÉÕ¸¥¸…±±}•¹ÑÉ¥•Ì¥˜ÉÕ¸¹•Ð ‰¥ˆ¥ô(€€€½É‘•É•€ôÍ½ÉÑ•¡Õ¹¥ÅÕ”¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„¥Ñ•´è€¡ÍÑÈ¡¥Ñ•´¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤°ÍÑÈ¡¥Ñ•´¹•Ð ‰¥ˆ°€ˆˆ¤¤¤°É•Ù•ÉÍ”õQÉÕ”¤(€€€å±•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½ÈÉÕ¸¥¸½É‘•É•è(€€€€€€€å±”€ôÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤(€€€€€€€¥˜å±”¹½Ð¥¸å±•Ìè(€€€€€€€€€€€å±•Ì¹…ÁÁ•¹¡å±”¤(€€€­••À€ôÍ•Ð¡å±•ÍléÉ•Ñ…¥¹}å±•Ít¤(€€€É•Ñ…¥¹•€ômÉÕ¸™½ÈÉÕ¸¥¸½É‘•É•¥˜ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤¥¸­••Át(€€€½µÁ…É¥Í½¹}ÁÉ½‘ÕÑÌ€ôl(€€€€€€€AI=UQ}hÔÀÁ}9=51d(€€€€€€€™½È|¥¸lÁt(€€€€€€€¥˜…¹ä (€€€€€€€€€€€ÉÕ¸¹•Ð ‰ÁÉ½‘ÕÐˆ¤€ôôAI=UQ}hÔÀÁ}9=51d(€€€€€€€€€€€…¹…¹ä¡Ñ…É•Ð¹•Ð ‰¥µ…”ˆ¤™½ÈÑ…É•Ð¥¸ÉÕ¸¹•Ð ‰Ñ…É•ÑÌˆ°mt¤¤(€€€€€€€€€€€™½ÈÉÕ¸¥¸É•Ñ…¥¹•(€€€€€€€€¤(€€€t(€€€Á…å±½…€ôì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰­¥¹ˆè€‰•½Í}ÌÉÌÍ}Í•…Í½¹…±}µ…¹¥™•ÍÐˆ°(€€€€€€€€‰•¹•É…Ñ•‘}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹¹½Ü¡‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€‰Í½ÕÉ”ˆè€‰9M=LµLÉL´Ì9L¹Õµ•É¥…°™½É•…ÍÐ…É¡¥Ù”ˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè9M}Q}I==P°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèm9M}Q}I==P°9M}9IQ}I==P°9M}I%Q}I==P°9M}AI%5I}UI0°9M}!%MQ=Ie}=9%}UI1t°(€€€€€€€€‰É•¹‘•É¥¹œˆè€‰¹Õµ•É¥Œ9M9•Ñ•¹Í•µ‰±”µ•…¸µ¥¹ÕÌ±•…µµ…Ñ¡•ÁÉ½Ù¥‘•È‘É¥™Ð±¥µ…Ñ½±½äˆ°(€€€€€€€€‰½µÁ…É¥Í½¹}ÁÉ½‘ÕÑÌˆè½µÁ…É¥Í½¹}ÁÉ½‘ÕÑÌ°(€€€€€€€€‰ÁÉ½‘ÕÑ}±…‰•±ÌˆèAI=UQ}1	1L°(€€€€€€€€‰É•Ñ•¹Ñ¥½¸ˆèì‰µ…á}å±•ÌˆèÉ•Ñ…¥¹}å±•Ì°€‰¡¥ÍÑ½Éå}å±•Ìˆèµ…à À°É•Ñ…¥¹}å±•Ì€´€Ä¥ô°(€€€€€€€€‰Í½ÕÉ•}ÅÕ…±¥Ñäˆèì(€€€€€€€€€€€€‰èÔÀÀˆè€‰ÍÑÉ¥Ð€ÔÀÀµ¡A„½½É‘¥¹…Ñ”Ù…±¥‘…Ñ¥½¸ìÕÉÉ•¹Ð€ÈÀÀµ¡A„A8•áÑÉ…Ñ¥½¸¥ÌÉ•©•Ñ•ˆ°(€€€€€€€€€€€€‰ÍÍÐˆè€‰9MQL™¥•±Ý¥Ñ 9…ÑÕÉ…°…ÉÑ ±…¹•±±Ìµ…Í­•‰•™½É”É•¹‘•É¥¹œ½È‰±•¹‘¥¹œˆ°(€€€€€€€ô°(€€€€€€€€‰ÉÕ¹ÌˆèÉ•Ñ…¥¹•°(€€€ô(€€€Á…Ñ ¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€Ñ•µÁ½É…Éä€ôÁ…Ñ ¹Ý¥Ñ¡}¹…µ”¡Á…Ñ ¹¹…µ”€¬€ˆ¹ÑµÀˆ¤(€€€Ñ•µÁ½É…Éä¹ÝÉ¥Ñ•}Ñ•áÐ¡©Í½¸¹‘ÕµÁÌ¡Á…å±½…°¥¹‘•¹ÐôÈ¤€¬€‰q¸ˆ°•¹½‘¥¹œô‰ÕÑ˜´àˆ¤(€€€Ñ•µÁ½É…Éä¹É•Á±…”¡Á…Ñ ¤(()‘•˜‰Õ¥±‘}Á…ÉÍ•È ¤€´ø…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•Èè(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¡‘•ÍÉ¥ÁÑ¥½¸õ}}‘½}|¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‘ÕÐˆ°‘•™…Õ±Ðô‰…±°ˆ°¡•±Àô‰½¹”ÁÉ½‘ÕÐ°„½µµ„µÍ•Á…É…Ñ•±¥ÍÐ°½È…±°Ù…±¥‘…Ñ•ÁÉ½‘ÕÑÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¥¹¥Ðˆ°‘•™…Õ±Ðô‰±…Ñ•ÍÐˆ°¡•±Àô‰9MÉ•±•…Í”…Ìeeee54½È±…Ñ•ÍÐˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ±•…µµ½¹Ñ¡Ìˆ°‘•™…Õ±ÐôˆÐ°Ô°Øˆ°¡•±Àô‰Ñ…É•Ð½™™Í•ÑÌ™É½´Ñ¡”9MÉ•±•…Í”µ½¹Ñ ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ•…Í½¹…°µÝ¥¹‘½Üˆ°‘•™…Õ±ÐôˆÐ°Ô°Øˆ°¡•±Àô‰½¹Í•ÕÑ¥Ù”½™™Í•ÑÌ™½È…¸…‘‘¥Ñ¥½¹…°Í•…Í½¹…°µ…Àˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹ÉÐµÉ½½Ðˆ°‘•™…Õ±Ðõ9M}9IQ}I==P¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘É¥™ÐµÉ½½Ðˆ°‘•™…Õ±Ðõ9M}I%Q}I==P¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…¡”µ‘¥Èˆ°‘•™…Õ±Ðôˆ¹…¡”½•½ÌµÌÉÌÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ…¡”µ‘¥Èˆ°‘•™…Õ±Ðôˆ¹…¡”½•½ÌµÌÉÌÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½ÕÑÁÕÐµ‘¥Èˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½•½Í}ÌÉÌÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ…¹¥™•ÍÐˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½•½Í}ÌÉÌÍ}µ…¹¥™•ÍÐ¹©Í½¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ•Ù¥½ÕÌµµ…¹¥™•ÍÐˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•Ñ…¥¸µå±•Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôÐ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•ÅÕ•ÍÐµ‘•±…äˆ°ÑåÁ”õ™±½…Ð°‘•™…Õ±ÐôÀ¸À¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ•½©Í½¸ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ‰½É‘•ÉÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘•½‘”µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È(()‘•˜}É•Í½±Ù”¡Ù…±Õ”èÍÑÈðA…Ñ °É½½ÐèA…Ñ ¤€´øA…Ñ è(€€€Á…Ñ €ôA…Ñ ¡Ù…±Õ”¤(€€€É•ÑÕÉ¸Á…Ñ ¥˜Á…Ñ ¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼Á…Ñ (()‘•˜ÉÕ¸¡…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”¤€´ø¥¹Ðè(€€€É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€ÁÉ½‘ÕÑÌ€ôÍ•±•Ñ•‘}ÁÉ½‘ÕÑÌ¡…ÉÌ¹ÁÉ½‘ÕÐ¤(€€€¥¹¥Ð€ôÁ…ÉÍ•}¥¹¥Ð¡…ÉÌ¹¥¹¥Ð°…ÉÌ¹¹ÉÑ}É½½Ð¤(€€€±•…‘Ì€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹±•…‘}µ½¹Ñ¡Ì°€‰±•…µ½¹Ñ¡Ìˆ¤(€€€Í•…Í½¹…°€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü°€‰Í•…Í½¹…°Ý¥¹‘½Üˆ¤¥˜…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü•±Í”mt(€€€¥˜Í•…Í½¹…°…¹Í•…Í½¹…°€„ô±¥ÍÐ¡É…¹”¡µ¥¸¡Í•…Í½¹…°¤°µ…à¡Í•…Í½¹…°¤€¬€Ä¤¤è(€€€€€€€É…¥Í”=MLÉLÍÉÉ½È ˆ´µÍ•…Í½¹…°µÝ¥¹‘½ÜµÕÍÐ½¹Ñ…¥¸½¹Í•ÕÑ¥Ù”±•…‘Ìˆ¤(€€€±•…‘Ì€ôÍ½ÉÑ•¡Í•Ð¡±•…‘Ì¤¹Õ¹¥½¸¡Í•…Í½¹…°¤¤(€€€…¡•}‘¥È€ô}É•Í½±Ù”¡…ÉÌ¹…¡•}‘¥È°É½½Ð¤(€€€‰½É‘•É}…¡”€ô}É•Í½±Ù”¡…ÉÌ¹‰½É‘•É}…¡•}‘¥È°É½½Ð¤(€€€½ÕÑÁÕÑ}‘¥È€ô}É•Í½±Ù”¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È°É½½Ð¤(€€€µ…¹¥™•ÍÑ}Á…Ñ €ô}É•Í½±Ù”¡…ÉÌ¹µ…¹¥™•ÍÐ°É½½Ð¤(€€€ÁÉ•Ù¥½ÕÌ€ô}É•Í½±Ù”¡…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ°É½½Ð¤¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ•±Í”9½¹”(€€€‰½É‘•ÉÌ€ô•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°‰½É‘•É}…¡”°É½½Ð¤(€€€¥˜AI=UQ}MMQ}9=51d¥¸ÁÉ½‘ÕÑÌ…¹¹½Ð…¹ä¡Á…Ñ ¹¹…µ”€ôô€‰½Õ¹ÑÉ¥•Ì¹•½©Í½¸ˆ™½ÈÁ…Ñ ¥¸‰½É‘•ÉÌ¤è(€€€€€€€É…¥Í”=MLÉLÍÉÉ½È ‰Í•„µÍÕÉ™…”Ñ•µÁ•É…ÑÕÉ”…¹¹½ÐÉÕ¸Ý¥Ñ¡½ÕÐÑ¡”½Õ¹ÑÉ¥•Ì±…¹µ…Í¬ˆ¤((€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€ÕÍ…‰±•}ÁÉ½‘ÕÑÌ€ô€À(€€€¥ÍÍÕ•}ÕÑŒ€ô˜‰í¥¹¥ÑlèÑuôµí¥¹¥ÑlÐéuô´ÀÅPÀÀèÀÀèÀÁhˆ(€€€™½ÈÁÉ½‘ÕÐ¥¸ÁÉ½‘ÕÑÌè(€€€€€€€ÍÁ•Œ€ôAI=UQ}MAMmÁÉ½‘ÕÑt(€€€€€€€ÉÕ¹}¥€ô˜‰•½ÌµÌÉÌÌµí¥¹¥ÑôµíÁÉ½‘ÕÑôˆ(€€€€€€€ÉÕ¹}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆèÉÕ¹}¥°(€€€€€€€€€€€€‰¥¹¥Ñ}ÕÑŒˆè¥ÍÍÕ•}ÕÑŒ°(€€€€€€€€€€€€‰µ½‘•°ˆè€‰9M=LµLÉL´Ìˆ°(€€€€€€€€€€€€‰ÁÉ½‘ÕÐˆèÁÉ½‘ÕÐ°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰9M=LµLÉL´Ì9L¹Õµ•É¥…°™½É•…ÍÐ…É¡¥Ù”ˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè9M}Q}I==P°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè€‰±…œ½‰ÕÉÍÐ•¹Í•µ‰±”µ•…¸µ¥¹ÕÌ±•…µµ…Ñ¡•ÁÉ½Ù¥‘•È‘É¥™Ð±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€‰½ÕÑÁÕÑ}‘¥ÈˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É½½Ð¤°(€€€€€€€€€€€€‰Ñ…É•ÑÌˆèmt°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€‰Õ¹‘±”€ô±½…‘}…¹½µ…±å}‰Õ¹‘±” (€€€€€€€€€€€€€€€ÁÉ½‘ÕÐõÁÉ½‘ÕÐ°(€€€€€€€€€€€€€€€¥¹¥Ðõ¥¹¥Ð°(€€€€€€€€€€€€€€€±•…‘Ìõ±•…‘Ì°(€€€€€€€€€€€€€€€…¡•}‘¥Èõ…¡•}‘¥È°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•ÉÌ°(€€€€€€€€€€€€€€€¹ÉÑ}É½½Ðõ…ÉÌ¹¹ÉÑ}É½½Ð°(€€€€€€€€€€€€€€€‘É¥™Ñ}É½½Ðõ…ÉÌ¹‘É¥™Ñ}É½½Ð°(€€€€€€€€€€€€€€€É•ÅÕ•ÍÑ}‘•±…äõ…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä°(€€€€€€€€€€€€¤(€€€€€€€€€€€…±±}‘…Ñ•Ì€ôÍ½ÉÑ•¡í‘…Ñ”™½Èµ½¹Ñ ¥¸‰Õ¹‘±”¹Ù…±Õ•Ì ¤™½È‘…Ñ”¥¸µ½¹Ñ ¹¥¹¥Ñ}‘…Ñ•Íô¤(€€€€€€€€€€€¥¹¥Ñ}ÍÑ…ÉÐ°¥¹¥Ñ}•¹°¥¹¥Ñ}±…‰•°€ô}¥¹¥Ñ¥…±¥é…Ñ¥½¹}É…¹”¡…±±}‘…Ñ•Ì¤(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}ÍÑ…ÉÑ}ÕÑŒ‰t€ô¥¹¥Ñ}ÍÑ…ÉÐ(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}•¹‘}ÕÑŒ‰t€ô¥¹¥Ñ}•¹(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰•¹Í•µ‰±•}Í½Á”‰t€ô€‰9M€ÐÀµµ•µ‰•È±…œ½‰ÕÉÍÐ¹•…ÈÑ•É´ì€ÄÀÍ•±•Ñ•±½¹œµÉ…¹”µ•µ‰•ÉÌˆ(€€€€€€€€€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€€€€€€€€€µ½¹Ñ €ô‰Õ¹‘±•m±•…‘t(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°€ô‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡µ½¹Ñ ¹Ñ…É•Ð°€ˆ•d•´ˆ¤¹ÍÑÉ™Ñ¥µ” ˆ•€•dˆ¤(€€€€€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ð€¼˜‰•½Í}ÌÉÌÍ}íÍÁ•l¥‘}Ñ½­•¸uõ}íµ½¹Ñ ¹Ñ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‰‘•½‘•ˆ¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ(€€€€€€€€€€€€€€€¥µ…”€ô9½¹”(€€€€€€€€€€€€€€€¥˜¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€€€€€µ½¹Ñ ¹…¹½µ…±ä°˜‰í¥¹¥ÑôÀÄÀÀˆ°µ½¹Ñ ¹Ñ…É•Ð°±•…°±¥ÍÐ¡É…¹”¡±•¸¡µ½¹Ñ ¹µ•µ‰•ÉÌ¤¤¤°(€€€€€€€€€€€€€€€€€€€€€€€½ÕÑÁÕÐ°QÉÕ”°}‰…Í•±¥¹•}µ•Ñ…‘…Ñ„¡mµ½¹Ñ¡t¥l‰Í½ÕÉ”‰t°‰½É‘•ÉÌ°(€€€€€€€€€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°ô¡˜‰í±•¸¡µ½¹Ñ ¹µ•µ‰•ÉÌ¥ôµµ•µ‰•È±½¹œµÉ…¹”µ•…¸ˆ¥˜±•¸¡µ½¹Ñ ¹µ•µ‰•ÉÌ¤€ôôaAQ}1=9}I9}55	IL•±Í”˜‰í±•¸¡µ½¹Ñ ¹µ•µ‰•ÉÌ¥ôµµ•µ‰•È±…œ½‰ÕÉÍÐµ•…¸ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ñ¥…±¥é…Ñ¥½¹}±…‰•°õ¥¹¥Ñ}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õµ½¹Ñ ¹™½É•…ÍÐ¥˜ÍÁ•l‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t•±Í”9½¹”°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•ŒõÍÁ•Œ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€¥µ…”€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€}Ñ…É•Ñ}•¹ÑÉä¡ÉÕ¹}¥õÉÕ¹}¥°ÁÉ½‘ÕÐõÁÉ½‘ÕÐ°±•…õ±•…°Ñ…É•Ðõµ½¹Ñ ¹Ñ…É•Ð°Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°µ½¹Ñ¡Ìõmµ½¹Ñ¡t°¥µ…”õ¥µ…”°ÍÑ…ÑÕÌõÍÑ…ÑÕÌ¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜Í•…Í½¹…°è(€€€€€€€€€€€€€€€µ½¹Ñ¡Ì€ôm‰Õ¹‘±•m±•…‘t™½È±•…¥¸Í•…Í½¹…±t(€€€€€€€€€€€€€€€µ•µ‰•É}Í•ÑÌ€ômÍ•Ð¡µ½¹Ñ ¹µ•µ‰•ÉÌ¤™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ít(€€€€€€€€€€€€€€€¥˜…¹ä¡µ•µ‰•É}Í•Ð€„ôµ•µ‰•É}Í•ÑÍlÁt™½Èµ•µ‰•É}Í•Ð¥¸µ•µ‰•É}Í•ÑÍlÄét¤è(€€€€€€€€€€€€€€€€€€€É…¥Í”=MLÉLÍÉÉ½È ‰Í•…Í½¹…°Ý¥¹‘½ÜÉ½ÍÍ•ÌÑ¡”€ÐÀµµ•µ‰•È¼ÄÀµµ•µ‰•È¡½É¥é½¸‰½Õ¹‘…Éäì¡½½Í”µ½¹Ñ¡ÌÝ¥Ñ „½¹Í¥ÍÑ•¹Ð9Mµ•µ‰•ÈÍ•Ðˆ¤(€€€€€€€€€€€€€€€…¹½µ…±ä€ôÍÕµ}É¥‘Ì¡mµ½¹Ñ ¹…¹½µ…±ä™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ít¤¥˜ÍÁ•l‰Í•…Í½¹…±}É•‘Õ•È‰t€ôô€‰ÍÕ´ˆ•±Í”µ•…¹}É¥‘Ì¡mµ½¹Ñ ¹…¹½µ…±ä™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ít¤(€€€€€€€€€€€€€€€¡•¥¡Ð€ôµ•…¹}É¥‘Ì¡mµ½¹Ñ ¹™½É•…ÍÐ™½Èµ½¹Ñ ¥¸µ½¹Ñ¡Ít¤¥˜ÍÁ•l‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t•±Í”9½¹”(€€€€€€€€€€€€€€€Ñ…É•Ð€ô˜‰íµ½¹Ñ¡ÍlÁt¹Ñ…É•Ñôµíµ½¹Ñ¡Íl´Åt¹Ñ…É•Ñôˆ(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°€ôÍ•…Í½¹…±}Á•É¥½‘}±…‰•°¡µ½¹Ñ¡ÍlÁt¹Ñ…É•Ð°µ½¹Ñ¡Íl´Åt¹Ñ…É•Ð¤(€€€€€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ð€¼˜‰•½Í}ÌÉÌÍ}íÍÁ•l¥‘}Ñ½­•¸uõ}íÑ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€ÍÑ…ÑÕÌ€ô€‰‘•½‘•ˆ¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ(€€€€€€€€€€€€€€€¥µ…”€ô9½¹”(€€€€€€€€€€€€€€€¥˜¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€€€€€…¹½µ…±ä°˜‰í¥¹¥ÑôÀÄÀÀˆ°µ½¹Ñ¡ÍlÁt¹Ñ…É•Ð°˜‰íÍ•…Í½¹…±lÁu÷ŠMíÍ•…Í½¹…±l´Åuôˆ°(€€€€€€€€€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹”¡±•¸¡µ•µ‰•É}Í•ÑÍlÁt¤¤¤°½ÕÑÁÕÐ°QÉÕ”°}‰…Í•±¥¹•}µ•Ñ…‘…Ñ„¡µ½¹Ñ¡Ì¥l‰Í½ÕÉ”‰t°‰½É‘•ÉÌ°(€€€€€€€€€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰í±•¸¡µ•µ‰•É}Í•ÑÍlÁt¥ôµµ•µ‰•È±½¹œµÉ…¹”µ•…¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ñ¥…±¥é…Ñ¥½¹}±…‰•°õ¥¹¥Ñ}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õ¡•¥¡Ð°(€€€€€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•ŒõÍÁ•Œ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€¥µ…”€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹ (€€€€€€€€€€€€€€€€€€€}Ñ…É•Ñ}•¹ÑÉä¡ÉÕ¹}¥õÉÕ¹}¥°ÁÉ½‘ÕÐõÁÉ½‘ÕÐ°±•…õ˜‰íÍ•…Í½¹…±lÁuôµíÍ•…Í½¹…±l´Åuôˆ°Ñ…É•ÐõÑ…É•Ð°Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°µ½¹Ñ¡Ìõµ½¹Ñ¡Ì°¥µ…”õ¥µ…”°ÍÑ…ÑÕÌõÍÑ…ÑÕÌ¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ(€€€€€€€€€€€ÕÍ…‰±•}ÁÉ½‘ÕÑÌ€¬ô€Ä(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰É•¹‘•É•9M=LµLÉL´ÌíÁÉ½‘ÕÑôèí±•¸¡ÉÕ¹}•¹ÑÉålÑ…É•ÑÌt¥ôÑ…É•Ð¡Ì¤ˆ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t€ôl(€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµíÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¥ôˆ°(€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤°€ˆ•d•´ˆ¤¹ÍÑÉ™Ñ¥µ” ˆ•€•dˆ¤°(€€€€€€€€€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤°(€€€€€€€€€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€€€€€€€€€€€€€‰™¥•±ˆèÍÁ•l‰™¥•±‰t°(€€€€€€€€€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÍÁ•l‰Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰™…¥±•ˆ°(€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½ÈˆèÍÑÈ¡•áŒ¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€™½È±•…¥¸±•…‘Ì(€€€€€€€€€€€t(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰9M=LµLÉL´ÌíÁÉ½‘ÕÑô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡ÉÕ¹}•¹ÑÉä¤((€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÑ}Á…Ñ °•¹ÑÉ¥•Ì°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}å±•Ì¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”9M=LµLÉL´Ìµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑ}Á…Ñ¡ô€¡í±•¸¡•¹ÑÉ¥•Ì¥ôÁÉ½‘ÕÐÉÕ¸¡Ì¤¤ˆ¤(€€€É•ÑÕÉ¸€À¥˜ÕÍ…‰±•}ÁÉ½‘ÕÑÌ•±Í”€È(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤(€€€•á•ÁÐ=MLÉLÍÉÉ½È…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰9M=LµLÉL´ÌII=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(