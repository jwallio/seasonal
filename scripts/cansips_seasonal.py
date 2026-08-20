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
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
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

MSLP_ANOMALY_TICKS = list(range(-10, 11))
SST_ANOMALY_TICKS = list(range(-3, 4))
SST_ANOMALY_PALETTE = [
    "#28567f", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5", "#b84c5a",
]
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
        "anomaly_min": -100.0,
        "anomaly_max": 100.0,
        "anomaly_ticks": ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Height contours in dam",
    },
    PRODUCT_850MB_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_850MB_TEMPERATURE_ANOMALY,
        "source_var": "AirTemp",
        "level": "ISBL-0850",
        "state_tag": "t850",
        "id_token": "t850a",
        "title": "CanSIPS v3 850-mb Temperature Anomaly (Â°C)",
        "absolute_title": "CanSIPS v3 850-mb Temperature (Â°C)",
        "field": "temperature_850mb_anomaly",
        "raw_field": "AirTemp at 850 hPa",
        "raw_units": "K",
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  850-mb temperature anomaly (Â°C)",
    },
    PRODUCT_2M_TEMPERATURE_ANOMALY: {
        "name": PRODUCT_2M_TEMPERATURE_ANOMALY,
        "source_var": "AirTemp",
        "level": "AGL-2m",
        "state_tag": "t2m",
        "id_token": "t2ma",
        "title": "CanSIPS v3 2-m Temperature Anomaly (Â°C)",
        "absolute_title": "CanSIPS v3 2-m Temperature (Â°C)",
        "field": "temperature_2m_anomaly",
        "raw_field": "AirTemp at 2 m",
        "raw_units": "K",
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS,
        "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  2-m temperature anomaly (Â°C)",
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
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Precipitation anomaly (in)",
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
        "anomaly_min": -10.0,
        "anomaly_max": 10.0,
        "anomaly_ticks": MSLP_ANOMALY_TICKS,
        "anomaly_palette": ANOMALY_PALETTE,
        "conversion_kind": "pascals_to_hectopascals",
        "conversion": "Pressure divided by 100 to convert Pa to hPa",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Mean sea-level pressure anomaly (hPa)",
    },
    PRODUCT_SST_ANOMALY: {
        "name": PRODUCT_SST_ANOMALY,
        "source_var": "WaterTemp",
        "level": "Sfc",
        "state_tag": "sst",
        "id_token": "ssta",
        "title": "CanSIPS v3 Sea-Surface Temperature Anomaly (Â°C)",
        "absolute_title": "CanSIPS v3 Sea-Surface Temperature (Â°C)",
        "field": "sst_anomaly",
        "raw_field": "WaterTemp at the surface",
        "raw_units": "K",
        "units": "Â°C",
        "seasonal_units": "Â°C",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -3.0,
        "anomaly_max": 3.0,
        "anomaly_ticks": SST_ANOMALY_TICKS,
        "anomaly_palette": SST_ANOMALY_PALETTE,
        "map_domain": "ocean",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Sea-surface temperature anomaly (Â°C)",
    },
    PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY: {
        "name": PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY,
        "source_var": "SeaSfcHeight-Geoid",
        "level": "",
        "state_tag": "ssh",
        "id_token": "ssha",
        "title": "CanSIPS v3 Sea-Surface Height Anomaly (m)",
        "absolute_title": "CanSIPS v3 Sea-Surface Height (m)",
        "field": "sea_surface_height_anomaly",
        "raw_field": "Sea-surface height relative to geoid",
        "raw_units": "m",
        "units": "m",
        "seasonal_units": "m",
        "height_contours": False,
        "region": CANSIPS_DEFAULT_REGION,
        "monthly_reducer": "mean",
        "seasonal_reducer": "mean",
        "anomaly_min": -0.50,
        "anomaly_max": 0.50,
        "anomaly_ticks": SSH_ANOMALY_TICKS,
        "anomaly_tick_decimals": 2,
        "anomaly_palette": SSH_ANOMALY_PALETTE,
        "map_domain": "ocean",
        "source_label": "ECCC MSC CanSIPS v3 / Datamart",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Sea-surface height anomaly (m)",
    },
}
PRODUCT_LABELS = {
    PRODUCT_Z500_ANOMALY: "500-mb Height Anomaly",
    PRODUCT_850MB_TEMPERATURE_ANOMALY: "850-mb Temperature Anomaly",
    PRODUCT_2M_TEMPERATURE_ANOMALY: "2-m Temperature Anomaly",
    PRODUCT_PRECIPITATION_ANOMALY: "Precipitation Anomaly",
    PRODUCT_MSLP_ANOMALY: "MSLP Anomaly",
    PRODUCT_SST_ANOMALY: "Sea-Surface Temperature Anomaly",
    PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY: "Sea-Surface Height Anomaly",
}


class CanSIPSError(CFSv2Error):
    """A user-actionable CanSIPS source, decode, or rendering error."""


def get_product_spec(product: str) -> dict[str, Any]:
    try:
        return PRODUCT_SPECS[product]
    except KeyError as exc:
        raise CanSIPSError(
            f"unsupported CanSIPS product {product!r}; choose from {', '.join(PRODUCT_SPECS)}"
        ) from exc


def selected_products(product: str) -> list[dict[str, Any]]:
    if product == PRODUCT_ALL:
        return list(PRODUCT_SPECS.values())
    return [get_product_spec(product)]


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def month_after(year: int, month: int, lead_months: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + lead_months
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead_months: int) -> str:
    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(init_date.year, init_date.month, lead_months)
    return f"{year:04d}{month:02d}"


def parse_init(value: str) -> str:
    if value.lower() == "latest":
        return discover_latest_init()
    if re.fullmatch(r"\d{6}", value):
        value = f"{value}0100"
    if not re.fullmatch(r"\d{10}", value):
        raise CanSIPSError("--init must be YYYYMM, YYYYMM0100, or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d%H")
    except ValueError as exc:
        raise CanSIPSError(f"invalid CanSIPS initialization: {value}") from exc
    if parsed.day != 1 or parsed.hour != 0:
        raise CanSIPSError("CanSIPS initialization must be the first day of the month at 00Z")
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
            raise CanSIPSError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise CanSIPSError(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise CanSIPSError(f"{label} cannot be empty")
    return result


def discover_latest_init() -> str:
    """Select the newest monthly forecast directory from the official index."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - minimal environments only
        raise CanSIPSError("requests is required when --init latest is used") from exc
    try:
        response = requests.get(CANSIPS_FORECAST_ROOT, timeout=(20, 60))
        response.raise_for_status()
        years = sorted(set(re.findall(r'href="(20\d{2})/"', response.text)), reverse=True)
        for year in years:
            month_url = urljoin(CANSIPS_FORECAST_ROOT, f"{year}/")
            month_response = requests.get(month_url, timeout=(20, 60))
            month_response.raise_for_status()
            months = sorted(
                set(re.findall(r'href="(\d{2})/"', month_response.text)),
                reverse=True,
            )
            if months:
                return f"{year}{months[0]}0100"
    except Exception as exc:
        raise CanSIPSError(f"could not read the CanSIPS forecast index: {exc}") from exc
    raise CanSIPSError("the CanSIPS Datamart listed no forecast initialization")


def file_name(init: str, lead: int, hindcast: bool, product_spec: dict[str, Any] | None = None) -> str:
    product = product_spec or PRODUCT_SPECS[PRODUCT_Z500_ANOMALY]
    prefix = f"{init[:6]}_MSC_CanSIPS{'-Hindcast' if hindcast else ''}"
    level = f"_{product['level']}" if product.get("level") else ""
    return f"{prefix}_{product['source_var']}{level}_LatLon1.0_P{lead:02d}M.grib2"


def source_url(
    init: str,
    lead: int,
    hindcast: bool,
    product_spec: dict[str, Any] | None = None,
) -> str:
    root = CANSIPS_HINDCAST_ROOT if hindcast else CANSIPS_FORECAST_ROOT
    return urljoin(root, f"{init[:4]}/{init[4:6]}/{file_name(init, lead, hindcast, product_spec)}")


def cache_paths(
    cache_dir: Path,
    init: str,
    lead: int,
    hindcast: bool,
    product_spec: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    product = product_spec or PRODUCT_SPECS[PRODUCT_Z500_ANOMALY]
    kind = "hindcast" if hindcast else "forecast"
    name = file_name(init, lead, hindcast, product)
    raw_path = cache_dir / "raw" / kind / ß­9¶‰žËkºwµçA9M%AM}]I%È¥Ì…±Í¼¡½¹½É•ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•ÅÕ•ÍÐµ‘•±…äˆ°ÑåÁ”õ™±½…Ð°‘•™…Õ±ÐôÀ¸ÈÔ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ•½©Í½¸ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ‰½É‘•ÉÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘•½‘”µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ™½É”µ‘•½‘”ˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È(()‘•˜™¥¹‘}ÝÉ¥ˆÈ¡•áÁ±¥¥ÐèÍÑÈ¤€´øÍÑÈè(€€€¥µÁ½ÉÐ½Ì(€€€¥µÁ½ÉÐÍ¡ÕÑ¥°((€€€…¹‘¥‘…Ñ•Ì€ôm•áÁ±¥¥Ñt¥˜•áÁ±¥¥Ð•±Í”mt(€€€¥˜½Ì¹•¹Ù¥É½¸¹•Ð ‰9M%AM}]I%Èˆ¤è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡½Ì¹•¹Ù¥É½¹l‰9M%AM}]I%È‰t¤(€€€¥˜Í¡ÕÑ¥°¹Ý¡¥  ‰ÝÉ¥ˆÈˆ¤è(€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡Í¡ÕÑ¥°¹Ý¡¥  ‰ÝÉ¥ˆÈˆ¤½È€ˆˆ¤(€€€…¹‘¥‘…Ñ•Ì¹•áÑ•¹¡mÈ‰éqÝÉ¥ˆÉqÝÉ¥ˆÈ¹•á”ˆ°€ˆ½ÕÍÈ½±½…°½‰¥¸½ÝÉ¥ˆÈˆ°€ˆ½ÕÍÈ½‰¥¸½ÝÉ¥ˆÈ‰t¤(€€€™½È…¹‘¥‘…Ñ”¥¸…¹‘¥‘…Ñ•Ìè(€€€€€€€¥˜…¹‘¥‘…Ñ”…¹A…Ñ ¡…¹‘¥‘…Ñ”¤¹¥Í}™¥±” ¤è(€€€€€€€€€€€É•ÑÕÉ¸…¹‘¥‘…Ñ”(€€€É…¥Í”…¹M%AMÉÉ½È ‰ÝÉ¥ˆÈÝ…Ì¹½Ð™½Õ¹ì¥¹ÍÑ…±°¥Ð½ÈÍ•Ð9M%AM}]I%È¼´µÝÉ¥ˆÈˆ¤(()‘•˜É•¹‘•É}ÁÉ½‘ÕÑ}ÉÕ¸ (€€€…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”°(€€€ÁÉ½‘ÕÐè‘¥ÑmÍÑÈ°¹åt°(€€€¥¹¥ÐèÍÑÈ°(€€€±•…‘Ìè±¥ÍÑm¥¹Ñt°(€€€Í•…Í½¹…±}±•…‘Ìè±¥ÍÑm¥¹Ñt°(€€€ÝÉ¥ˆÈèÍÑÈ°(€€€…¡•}‘¥ÈèA…Ñ °(€€€½ÕÑÁÕÑ}‘¥ÈèA…Ñ °(€€€‰½É‘•É}Á…Ñ¡Ìè±¥ÍÑmA…Ñ¡t°(€€€½µµ½¹}É•™•É•¹•}‘¥ÈèA…Ñ ð9½¹”°(¤€´øÑÕÁ±•m‘¥ÑmÍÑÈ°¹åt°¥¹Ñtè(€€€É•Á½}É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€¥¹¥Ñ}‘…Ñ”€ô‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡¥¹¥Ð°€ˆ•d•´•• ˆ¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤(€€€ÉÕ¹}¥€ô˜‰…¹Í¥ÁÌµí¥¹¥ÑôµíÁÉ½‘ÕÑl¹…µ”uôˆ(€€€‰…Í•±¥¹•}±…‰•°€ô˜‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äìí…ÉÌ¹±¥µ½}ÍÑ…ÉÑôµí…ÉÌ¹±¥µ½}•¹‘ôˆ(€€€½µµ½¹}É•™•É•¹•}•¹…‰±•€ô€ (€€€€€€€ÁÉ½‘ÕÑl‰¹…µ”‰t€ôôAI=UQ}hÔÀÁ}9=51d(€€€€€€€…¹…ÉÌ¹±¥µ½}ÍÑ…ÉÐ€ôô9M%AM}!%9MQ}MQIP(€€€€€€€…¹…ÉÌ¹±¥µ½}•¹€ôô9M%AM}!%9MQ}9(€€€€€€€…¹½µµ½¹}É•™•É•¹•}‘¥È¥Ì¹½Ð9½¹”(€€€€¤(€€€ÉÕ¹}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€‰¥ˆèÉÕ¹}¥°(€€€€€€€€‰Í½ÕÉ”ˆè€‰5M…¹M%ALØÌ€¼…Ñ…µ…ÉÐˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè9M%AM}I5}UI0°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèm9M%AM}=IMQ}I==P°9M%AM}!%9MQ}I==P°9M%AM}I5}UI1t°(€€€€€€€€‰µ½‘•°ˆè€‰…¹M%ALØÌˆ°(€€€€€€€€‰ÁÉ½‘ÕÐˆèÁÉ½‘ÕÑl‰¹…µ”‰t°(€€€€€€€€‰¥¹¥Ñ}ÕÑŒˆè¥Í½}ÕÑŒ¡¥¹¥Ñ}‘…Ñ”¤°(€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè9M%AM}9M5	1}55	IL°(€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€ˆÐÀµµ•µ‰•È…¹M%ALØÌ‰±•¹ˆ°(€€€€€€€€‰µ•µ‰•É}É½ÕÁÌˆèl(€€€€€€€€€€€ì‰µ½‘•°ˆè€‰4Ô¸Èµ95<ˆ°€‰µ•µ‰•ÉÌˆè€ˆÄ´ÈÀˆ°€‰½Õ¹Ðˆè€ÈÁô°(€€€€€€€€€€€ì‰µ½‘•°ˆè€‰…¹M4Ôˆ°€‰µ•µ‰•ÉÌˆè€ˆÈÄ´ÐÀˆ°€‰½Õ¹Ðˆè€ÈÁô°(€€€€€€€t°(€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€‰…É•…Ñ¥½¸ˆè€ (€€€€€€€€€€€˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ Í•…Í½¹…°µ•…¸½˜µ½¹Ñ¡±ä™½É•…ÍÐ…¹½µ…±¥•Ìˆ(€€€€€€€€€€€¥˜Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€•±Í”€‰µ½¹Ñ¡±ä€ÐÀµµ•µ‰•È™½É•…ÍÐ…¹½µ…±äˆ(€€€€€€€€¤°(€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°(€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Õ¹¥ÑÌ‰t°(€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€‰É¥ˆèì‰±½¹¥ÑÕ‘•}½Õ¹Ðˆè€ÌØÀ°€‰±…Ñ¥ÑÕ‘•}½Õ¹Ðˆè€ÄàÀ°€‰É•Í½±ÕÑ¥½¸ˆè€ˆÄ‘•É•”ˆ°€‰±…å½ÕÐˆè€‰1…Ñ1½¸Ä¸À‰ô°(€€€€€€€€‰±¥µ…Ñ½±½äˆèì(€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ•¹Í•µ‰±”µ•…¹Ìˆ°(€€€€€€€€€€€€‰å•…ÉÌˆè˜‰í…ÉÌ¹±¥µ½}ÍÑ…ÉÑôµí…ÉÌ¹±¥µ½}•¹‘ôˆ°(€€€€€€€€€€€€‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}µ½¹Ñ ˆè¥¹¥ÑlÐèÙt°(€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰™½É•…ÍÐ€ÐÀµµ•µ‰•Èµ•…¸µ¥¹ÕÌÑ¡”µ…Ñ¡¥¹œµ¥¹¥Ñ¥…±¥é…Ñ¥½¸µµ½¹Ñ …¹±•…¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€ô°(€€€€€€€€‰‰½É‘•É}Í½ÕÉ•Ìˆèmt¥˜…ÉÌ¹¹½}‰½É‘•ÉÌ•±Í”mì‰¹…µ”ˆèÁ…Ñ ¹¹…µ•ô™½ÈÁ…Ñ ¥¸‰½É‘•É}Á…Ñ¡Ít°(€€€€€€€€‰Ñ…É•ÑÌˆèmt°(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€ô(€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€ÉÕ¹}•¹ÑÉål‰½µÁ…É¥Í½¹}É•™•É•¹”‰t€ôì(€€€€€€€€€€€€‰¥ˆè€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆ°(€€€€€€€€€€€€‰±…‰•°ˆè€‰½µµ½¸€ÄääÄ´ÈÀÈÀÉ•™•É•¹”€¡…¹M%ALØÌ¡¥¹‘…ÍÐ¤ˆ°(€€€€€€€€€€€€‰å•…ÉÌˆè€ˆÄääÄ´ÈÀÈÀˆ°(€€€€€€€€€€€€‰Í½ÕÉ”ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€‰‘¥É•Ñ½ÉäˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½µµ½¹}É•™•É•¹•}‘¥È°É•Á½}É½½Ð¤°(€€€€€€€ô(€€€™½É•…ÍÑ}É¥‘Ìè‘¥Ñm¥¹Ð°É¥‘t€ôíô(€€€…¹½µ…±å}É¥‘Ìè‘¥Ñm¥¹Ð°É¥‘t€ôíô(€€€Ñ…É•Ñ}•¹ÑÉ¥•Ìè‘¥Ñm¥¹Ð°‘¥ÑmÍÑÈ°¹åut€ôíô(€€€™…¥±ÕÉ•Ì€ô€À(€€€±…ÍÑ}É•ÅÕ•ÍÐ€ô€À¸À(€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤(€€€€€€€Ù…±¥‘}ÍÑ…ÉÐ°Ù…±¥‘}•¹€ôÑ…É•Ñ}Á•É¥½¡Ñ…É•Ð¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµ±•…‘í±•…èÀÉ‘ôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÙ…±¥‘}ÍÑ…ÉÐ°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÙ…±¥‘}•¹°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ð°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè€‰µ½¹Ñ¡±ä™½É•…ÍÐ…¹½µ…±äˆ°(€€€€€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè9M%AM}9M5	1}55	IL°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±•Ìˆèmt°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€™½É•…ÍÐ°™½É•…ÍÑ}Í½ÕÉ”°±…ÍÑ}É•ÅÕ•ÍÐ€ô±½…‘}•¹Í•µ‰±•}µ•…¸ (€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€…±Í”°(€€€€€€€€€€€€€€€…¡•}‘¥È°(€€€€€€€€€€€€€€€É•Á½}É½½Ð°(€€€€€€€€€€€€€€€ÝÉ¥ˆÈ°(€€€€€€€€€€€€€€€…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä°(€€€€€€€€€€€€€€€±…ÍÑ}É•ÅÕ•ÍÐ°(€€€€€€€€€€€€€€€ÁÉ½‘ÕÐ°(€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€…ÉÌ¹™½É•}‘•½‘”°(€€€€€€€€€€€€¤(€€€€€€€€€€€±¥µ…Ñ½±½ä°¡¥¹‘…ÍÑ}Í½ÕÉ•Ì°±…ÍÑ}É•ÅÕ•ÍÐ€ô¡¥¹‘…ÍÑ}±¥µ…Ñ½±½ä (€€€€€€€€€€€€€€€¥¹¥Ð°±•…°…ÉÌ¹±¥µ½}ÍÑ…ÉÐ°…ÉÌ¹±¥µ½}•¹°…¡•}‘¥È°É•Á½}É½½Ð°(€€€€€€€€€€€€€€€ÝÉ¥ˆÈ°…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä°±…ÍÑ}É•ÅÕ•ÍÐ°ÁÉ½‘ÕÐ°…ÉÌ¹™½É•}‘•½‘”°(€€€€€€€€€€€€¤(€€€€€€€€€€€…¹½µ…±ä€ôÍÕ‰ÑÉ…Ñ}É¥‘Ì¡™½É•…ÍÐ°±¥µ…Ñ½±½ä¤(€€€€€€€€€€€™½É•…ÍÑ}É¥‘Ím±•…‘t€ô™½É•…ÍÐ(€€€€€€€€€€€…¹½µ…±å}É¥‘Ím±•…‘t€ô…¹½µ…±ä(€€€€€€€€€€€½µµ½¹}É•™•É•¹•}™¥±”€ô9½¹”(€€€€€€€€€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}™¥±”€ô½µµ½¹}É•™•É•¹•}‘¥È€¼˜‰èÔÀÁ}íÑ…É•Ñô¹ÍØ¹èˆ(€€€€€€€€€€€€€€€ÝÉ¥Ñ•}É¥‘}ÍÑ…Ñ”¡±¥µ…Ñ½±½ä°½µµ½¹}É•™•É•¹•}™¥±”¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}™¥±•Ì‰t€ôm™½É•…ÍÑ}Í½ÕÉ•t(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€‰å•…ÉÌˆè˜‰í…ÉÌ¹±¥µ½}ÍÑ…ÉÑôµí…ÉÌ¹±¥µ½}•¹‘ôˆ°(€€€€€€€€€€€€€€€€‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}µ½¹Ñ ˆè¥¹¥ÑlÐèÙt°(€€€€€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè9M%AM}9M5	1}55	IL°(€€€€€€€€€€€€€€€€‰™¥±•Ìˆè¡¥¹‘…ÍÑ}Í½ÕÉ•Ì°(€€€€€€€€€€€ô(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}½µÁ±•Ñ”‰t€ôQÉÕ”(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ(€€€€€€€€€€€¥˜¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰…¹Í¥ÁÍ}íÁÉ½‘ÕÑl¥‘}Ñ½­•¸uõ}íÑ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€€€€€…¹½µ…±ä°(€€€€€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹” Ä°9M%AM}9M5	1}55	IL€¬€Ä¤¤°(€€€€€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°ôˆÐÀµµ•µ‰•È‰±•¹ˆ°(€€€€€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õ™½É•…ÍÐ¥˜ÁÉ½‘ÕÑl‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t•±Í”9½¹”°(€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•ŒõÁÉ½‘ÕÐ°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€€€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰¥µ…”ˆèÑ…É•Ñ}•¹ÑÉål‰¥µ…”‰t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•¹‘•É•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè€ˆÄääÄ´ÈÀÈÀˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½µµ½¹}É•™•É•¹•}™¥±”°É•Á½}É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰…¹M%ALÑ…É•ÐíÑ…É•Ñô±•…í±•…‘ô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Ím±•…‘t€ôÑ…É•Ñ}•¹ÑÉä((€€€¥˜Í•…Í½¹…±}±•…‘Ì…¹¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€™¥ÉÍÑ}±•…°±…ÍÑ}±•…€ôÍ•…Í½¹…±}±•…‘ÍlÁt°Í•…Í½¹…±}±•…‘Íl´Åt(€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°™¥ÉÍÑ}±•…¤°Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±…ÍÑ}±•…¤(€€€€€€€Í•…Í½¹…±}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡™¥ÉÍÑ}Ñ…É•Ð¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡±…ÍÑ}Ñ…É•Ð¥lÅt°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}±•…‘ôµí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ Í•…Í½¹…°µ•…¸ˆ°(€€€€€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Í•…Í½¹…±}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè9M%AM}9M5	1}55	IL°(€€€€€€€€€€€€‰µ½¹Ñ¡±å}±•…‘ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±•Ìˆèmt°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜…¹ä¡±•…¹½Ð¥¸…¹½µ…±å}É¥‘Ì™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¤è(€€€€€€€€€€€€€€€É…¥Í”…¹M%AMÉÉ½È ‰Í•…Í½¹…°Ý¥¹‘½Ü¥Ìµ¥ÍÍ¥¹œ½¹”½Èµ½É”‘•½‘•…¹M%AL™¥•±‘Ìˆ¤(€€€€€€€€€€€Í•…Í½¹…±}…¹½µ…±ä€ôµ•…¹}É¥‘Ì¡m…¹½µ…±å}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€Í•…Í½¹…±}¡•¥¡Ð€ô€ (€€€€€€€€€€€€€€€µ•…¹}É¥‘Ì¡m™½É•…ÍÑ}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€€€€€¥˜ÁÉ½‘ÕÑl‰¡•¥¡Ñ}½¹Ñ½ÕÉÌ‰t(€€€€€€€€€€€€€€€•±Í”9½¹”(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰Í½ÕÉ•}™¥±•Ì‰t€ôl(€€€€€€€€€€€€€€€Í½ÕÉ”™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì™½ÈÍ½ÕÉ”¥¸Ñ…É•Ñ}•¹ÑÉ¥•Ím±•…‘t¹•Ð ‰Í½ÕÉ•}™¥±•Ìˆ°mt¤(€€€€€€€€€€€t(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€‰å•…ÉÌˆè˜‰í…ÉÌ¹±¥µ½}ÍÑ…ÉÑôµí…ÉÌ¹±¥µ½}•¹‘ôˆ°(€€€€€€€€€€€€€€€€‰¥¹¥Ñ¥…±¥é…Ñ¥½¹}µ½¹Ñ ˆè¥¹¥ÑlÐèÙt°(€€€€€€€€€€€€€€€€‰±•…‘}µ½¹Ñ¡ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰µ•…¸½˜µ½¹Ñ¡±ä™½É•…ÍÐµµ¥¹ÕÌµ¡¥¹‘…ÍÐ…¹½µ…±¥•Ìˆ°(€€€€€€€€€€€ô(€€€€€€€€€€€Á•É¥½‘}±…‰•°€ôÍ•…Í½¹…±}Á•É¥½‘}±…‰•°¡™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð¤(€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰…¹Í¥ÁÍ}íÁÉ½‘ÕÑl¥‘}Ñ½­•¸uõ}í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô¹©Áœˆ(€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€Í•…Í½¹…±}…¹½µ…±ä°(€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€˜‰í™¥ÉÍÑ}±•…‘õqÔÈÀÄÍí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€€€€±¥ÍÐ¡É…¹” Ä°9M%AM}9M5	1}55	IL€¬€Ä¤¤°(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°(€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°ôˆÐÀµµ•µ‰•È‰±•¹ˆ°(€€€€€€€€€€€€€€€¡•¥¡Ñ}É¥õÍ•…Í½¹…±}¡•¥¡Ð°(€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•ŒõÁÉ½‘ÕÐ°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€¥˜½µµ½¹}É•™•É•¹•}•¹…‰±•è(€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰½µÁ…É¥Í½¸‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰½µµ½¹|ÄääÅ|ÈÀÈÀˆèì(€€€€€€€€€€€€€€€€€€€€€€€€‰¥µ…”ˆèÍ•…Í½¹…±}•¹ÑÉål‰¥µ…”‰t°(€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰É•¹‘•É•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè€ˆÄääÄ´ÈÀÈÀˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰…¹M%ALØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰™¥±•Ìˆèl(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€É•±…Ñ¥Ù•}Á…Ñ  (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€½µµ½¹}É•™•É•¹•}‘¥È€¼˜‰èÔÀÁ}íÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¥ô¹ÍØ¹èˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€É•Á½}É½½Ð°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€t°(€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€ô(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰…¹M%ALÍ•…Í½¹…°Ý¥¹‘½Üí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Í•…Í½¹…±}•¹ÑÉä¤((€€€ÍÑ…ÑÕÍ•Ì€ômÑ…É•Ñl‰ÍÑ…ÑÕÌ‰t™½ÈÑ…É•Ð¥¸ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰ut(€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ¥˜™…¥±ÕÉ•Ì…¹¹½Ð…¹ä¡ÍÑ…ÑÕÌ€„ô€‰™…¥±•ˆ™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤•±Í”€ (€€€€€€€€‰Á…ÉÑ¥…°ˆ¥˜™…¥±ÕÉ•Ì•±Í”€ ‰‘•½‘•ˆ¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ¤(€€€€¤(€€€ÉÕ¹}•¹ÑÉål‰½ÕÑÁÕÑ}‘¥È‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤(€€€ÉÕ¹}•¹ÑÉål‰•¹•É…Ñ•‘}ÕÑŒ‰t€ô¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹¹½Ü¡‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤(€€€É•ÑÕÉ¸ÉÕ¹}•¹ÑÉä°™…¥±ÕÉ•Ì(()‘•˜ÉÕ¸¡…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”¤€´ø¥¹Ðè(€€€É•Á½}É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€¥¹¥Ð€ôÁ…ÉÍ•}¥¹¥Ð¡…ÉÌ¹¥¹¥Ð¤(€€€±•…‘Ì€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹±•…‘}µ½¹Ñ¡Ì°€‰±•…µ½¹Ñ¡Ìˆ°€À°€ÄÄ¤(€€€Í•…Í½¹…±}±•…‘Ì€ôÁ…ÉÍ•}¥¹Ñ}±¥ÍÐ¡…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü°€‰Í•…Í½¹…°Ý¥¹‘½Üˆ°€À°€ÄÄ¤¥˜…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü•±Í”mt(€€€¥˜Í•…Í½¹…±}±•…‘Ìè(€€€€€€€•áÁ•Ñ•€ô±¥ÍÐ¡É…¹”¡µ¥¸¡Í•…Í½¹…±}±•…‘Ì¤°µ…à¡Í•…Í½¹…±}±•…‘Ì¤€¬€Ä¤¤(€€€€€€€¥˜Í•…Í½¹…±}±•…‘Ì€„ô•áÁ•Ñ•è(€€€€€€€€€€€É…¥Í”…¹M%AMÉÉ½È ˆ´µÍ•…Í½¹…°µÝ¥¹‘½ÜµÕÍÐ½¹Ñ…¥¸½¹Í•ÕÑ¥Ù”±•…µ½¹Ñ¡Ìˆ¤(€€€€€€€±•…‘Ì€ôÍ½ÉÑ•¡Í•Ð¡±•…‘Ì¤¹Õ¹¥½¸¡Í•…Í½¹…±}±•…‘Ì¤¤(€€€¥˜…ÉÌ¹±¥µ½}ÍÑ…ÉÐ€ð9M%AM}!%9MQ}MQIP½È…ÉÌ¹±¥µ½}•¹€ø9M%AM}!%9MQ}9½È…ÉÌ¹±¥µ½}ÍÑ…ÉÐ€ø…ÉÌ¹±¥µ½}•¹è(€€€€€€€É…¥Í”…¹M%AMÉÉ½È (€€€€€€€€€€€˜‰±¥µ…Ñ½±½äå•…ÉÌµÕÍÐÍÑ…ä¥¹Í¥‘”í9M%AM}!%9MQ}MQIQôµí9M%AM}!%9MQ}9ôˆ(€€€€€€€€¤(€€€ÝÉ¥ˆÈ€ô™¥¹‘}ÝÉ¥ˆÈ¡…ÉÌ¹ÝÉ¥ˆÈ¤(€€€…¡•}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹…¡•}‘¥È°É•Á½}É½½Ð¤(€€€½ÕÑÁÕÑ}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤(€€€µ…¹¥™•ÍÑ}Á…Ñ €ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ°É•Á½}É½½Ð¤(€€€½µµ½¹}É•™•É•¹•}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹½µµ½¹}É•™•É•¹•}‘¥È°É•Á½}É½½Ð¤¥˜…ÉÌ¹½µµ½¹}É•™•É•¹•}‘¥È•±Í”9½¹”(€€€‰½É‘•É}Á…Ñ¡Ì€ômt¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°…¡•}‘¥È°É•Á½}É½½Ð¤(€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™…¥±ÕÉ•Ì€ô€À(€€€ÁÉ½‘ÕÑÌ€ôÍ•±•Ñ•‘}ÁÉ½‘ÕÑÌ¡…ÉÌ¹ÁÉ½‘ÕÐ¤(€€€™½ÈÁÉ½‘ÕÐ¥¸ÁÉ½‘ÕÑÌè(€€€€€€€•¹ÑÉä°ÁÉ½‘ÕÑ}™…¥±ÕÉ•Ì€ôÉ•¹‘•É}ÁÉ½‘ÕÑ}ÉÕ¸ (€€€€€€€€€€€…ÉÌ°(€€€€€€€€€€€ÁÉ½‘ÕÐ°(€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€±•…‘Ì°(€€€€€€€€€€€Í•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€ÝÉ¥ˆÈ°(€€€€€€€€€€€…¡•}‘¥È°(€€€€€€€€€€€½ÕÑÁÕÑ}‘¥È°(€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€½µµ½¹}É•™•É•¹•}‘¥È°(€€€€€€€€¤(€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡•¹ÑÉä¤(€€€€€€€™…¥±ÕÉ•Ì€¬ôÁÉ½‘ÕÑ}™…¥±ÕÉ•Ì(€€€ÁÉ•Ù¥½ÕÌ€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ°É•Á½}É½½Ð¤¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ•±Í”9½¹”(€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÑ}Á…Ñ °É•Á½}É½½Ð°•¹ÑÉ¥•Ì°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}ÉÕ¹Ì¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”…¹M%ALµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑ}Á…Ñ¡ô€¡í±•¸¡•¹ÑÉ¥•Ì¥ôÁÉ½‘ÕÐÉÕ¹ìÌœ¥˜±•¸¡•¹ÑÉ¥•Ì¤€„ô€Ä•±Í”€œô¤ˆ¤(€€€É•ÑÕÉ¸€È¥˜™…¥±ÕÉ•Ì•±Í”€À(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤(€€€•á•ÁÐ…¹M%AMÉÉ½È…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰…¹M%ALII=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(