#!/usr/bin/env python3
"""Fetch and render APCC multi-model seasonal mean anomaly products.

APCC's official CLIK API returns an authenticated ZIP archive.  This adapter
keeps that request boundary explicit, decodes the NetCDF fields inside the
archive, and renders the native APCC MME anomalies with the common North
America map renderer used by the other seasonal models.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable
import zipfile

import numpy as np

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    DEFAULT_REGION,
    Grid,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    ensure_border_files,
    relative_path,
    render_map,
)


APCC_SOURCE_URL = "https://apcc21.org/clik/processing/prediction"
APCC_DATASET_URLS = {
    "MME_3MONTH": "https://apcc21.org/clik/dataset/mme/3-MON?lang=en",
    "MME_6MONTH": "https://apcc21.org/clik/dataset/mme/6-MON?lang=en",
}
APCC_API_DOCS_URL = "https://apcc21.org/clik/clikapi?lang=en"
APCC_REQUEST_URL = "https://www.apcc21.org/clikapi/request/apccdata"
APCC_STATUS_URL = "https://www.apcc21.org/clikapi/request/status"
APCC_RELEASE_DAY = 15
APCC_ACKNOWLEDGEMENT = (
    "APCC MME data collected and reproduced by APCC based on hindcast/forecast "
    "data produced by APCC MME Producing Centres."
)

APCC_Z500_TICKS = list(range(-100, 101, 10))
APCC_PRECIP_TICKS = list(range(-200, 201, 25))
APCC_PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f",
    "#86c879", "#5fba6b", "#3aa55b", "#006d2c",
]
APCC_SST_TICKS = list(range(-4, 5))
APCC_SST_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5",
    "#d36c73", "#a1384a",
]
APCC_PRESSURE_TICKS = list(range(-6, 7))
APCC_PRESSURE_PALETTE = [
    "#306b90", "#4891b0", "#61a7bf", "#95c4d3", "#c4dce3", "#e1e4e7",
    "#f2cecd", "#eaaaa8", "#e28c8b", "#d3686c", "#bf4856", "#84283f",
]


def dataset_url(dataset: str) -> str:
    """Return the official APCC dataset page matching the requested horizon."""

    return APCC_DATASET_URLS.get(dataset, APCC_DATASET_URLS["MME_3MONTH"])


PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "api_variable": "z500", "field": "z500_anomaly", "raw_field": "geopotential height anomaly",
        "raw_units": "native APCC units", "units": "m", "title": "APCC MME 500-mb Geopotential Height Anomaly (m)",
        "absolute_title": "APCC MME 500-mb Geopotential Height (m)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -100.0, "anomaly_max": 100.0,
        "anomaly_ticks": APCC_Z500_TICKS, "anomaly_palette": ANOMALY_PALETTE,
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "z500a",
    },
    "850mb_temperature_anomaly": {
        "api_variable": "t850", "field": "t850_anomaly", "raw_field": "850-mb temperature anomaly",
        "raw_units": "K", "units": "Â°C", "title": "APCC MME 850-mb Temperature Anomaly (Â°C)",
        "absolute_title": "APCC MME 850-mb Temperature (Â°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": TEMPERATURE_ANOMALY_MIN_C, "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "t850a",
    },
    "2m_temperature_anomaly": {
        "api_variable": "t2m", "field": "t2m_anomaly", "raw_field": "2-m temperature anomaly",
        "raw_units": "K", "units": "Â°C", "title": "APCC MME 2-m Temperature Anomaly (Â°C)",
        "absolute_title": "APCC MME 2-m Temperature (Â°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": TEMPERATURE_ANOMALY_MIN_C, "anomaly_max": TEMPERATURE_ANOMALY_MAX_C,
        "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "t2ma",
    },
    "precipitation_anomaly": {
        "api_variable": "prec", "field": "precipitation_anomaly", "raw_field": "precipitation anomaly",
        "raw_units": "mm/day", "units": "mm", "title": "APCC MME Seasonal Precipitation Anomaly (mm)",
        "absolute_title": "APCC MME Precipitation (mm)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -200.0, "anomaly_max": 200.0,
        "anomaly_ticks": APCC_PRECIP_TICKS, "anomaly_palette": APCC_PRECIP_PALETTE,
        "precipitation_conversion": "seasonal mean mm/day Ã— valid-season days = seasonal accumulation mm",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "preca",
    },
    "sea_surface_temperature_anomaly": {
        "api_variable": "sst", "field": "sst_anomaly", "raw_field": "sea-surface temperature anomaly",
        "raw_units": "K", "units": "Â°C", "title": "APCC MME Sea-Surface Temperature Anomaly (Â°C)",
        "absolute_title": "APCC MME Sea-Surface Temperature (Â°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -4.0, "anomaly_max": 4.0,
        "anomaly_ticks": APCC_SST_TICKS, "anomaly_palette": APCC_SST_PALETTE,
        "map_domain": "ocean",
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "ssta",
    },
    "mslp_anomaly": {
        "api_variable": "slp", "field": "mslp_anomaly", "raw_field": "mean sea-level pressure anomaly",
        "raw_units": "mb", "units": "hPa", "title": "APCC MME Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "APCC MME Mean Sea-Level Pressure (hPa)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -6.0, "anomaly_max": 6.0,
        "anomaly_ticks": APCC_PRESSURE_TICKS, "anomaly_palette": APCC_PRESSURE_PALETTE,
        "header_detail": "{source_label}  â€¢  {baseline_label}  â€¢  Native APCC seasonal MME anomaly",
        "id_token": "slpa",
    },
}


class APCCError(RuntimeError):
    """A user-actionable APCC source or rendering error."""


SEASON_START_MONTH = {
    "JFM": 1,
    "FMA": 2,
    "MAM": 3,
    "AMJ": 4,
    "MJJ": 5,
    "JJA": 6,
    "JAS": 7,
    "ASO": 8,
    "SON": 9,
    "OND": 10,
    "NDJ": 11,
    "DJF": 12,
}


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def latest_target_month(now: dt.datetime | None = None) -> str:
    """Return the first valid month from the newest nominal APCC issue.

    APCC indexes MME downloads by the first forecast target month, not by the
    issue month.  The target month advances after the mid-month release, so an
    August issue is requested as September and carries SON/DJF products.
    """

    current = now or dt.datetime.now(dt.timezone.utc)
    offset = 1 if current.day >= APCC_RELEASE_DAY else 0
    year, month = month_after(current.year, current.month, offset)
    return f"{year:04d}{month:02d}"


def parse_init(value: str) -> str:
    if value == "latest":
        return latest_target_month()
    if re.fullmatch(r"\d{6}", value):
        try:
            dt.datetime.strptime(value, "%Y%m")
        except ValueError as exc:
            raise APCCError(f"invalid APCC initialization month: {value}") from exc
        return value
    raise APCCError("--init must be latest or the APCC first target month as YYYYMM")


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def period_from_season(season_code: str, season_year: int) -> dict[str, Any]:
    season = season_code.upper()
    if season not in SEASON_START_MONTH:
        raise APCCError(f"unsupported APCC season code: {season_code}")
    start_month = SEASON_START_MONTH[season]
    start_year = int(season_year)
    last_year, last_month = month_after(start_year, start_month, 2)
    end_year, end_month = month_after(last_year, last_month, 1)
    days = sum(
        calendar.monthrange(year, month)[1]
        for year, month in (
            (start_year, start_month),
            month_after(start_year, start_month, 1),
            (last_year, last_month),
        )
    )
    return {
        "season_code": season,
        "season_year": start_year,
        "period_label": f"{season} {last_year}",
        "target_code": f"{start_year:04d}{start_month:02d}-{last_year:04d}{last_month:02d}",
        "first_target": f"{start_year:04d}{start_month:02d}",
        "valid_start_utc": f"{start_year:04d}-{start_month:02d}-01T00:00:00Z",
        "valid_end_utc": f"{end_year:04d}-{end_month:02d}-01T00:00:00Z",
        "days": days,
    }


def target_window(init: str, offsets: str) -> tuple[str, str, str]:
    values = [int(item.strip()) for item in offsets.split(",") if item.strip()]
    if len(values) != 3 or values != list(range(min(values), max(values) + 1)):
        raise APCCError("--target-window must contain three consecutive lead offsets")
    date = dt.datetime.strptime(init, "%Y%m")
    first = month_after(date.year, date.month, min(values))
    last = month_after(date.year, date.month, max(values))
    first_code = f"{first[0]:04d}{first[1]:02d}"
    last_code = f"{last[0]:04d}{last[1]:02d}"
    season = next(code for code, month in SEASON_START_MONTH.items() if month == first[1])
    label = f"{season} {last[0]}"
    return f"{first_code}-{last_code}", label, first_code


def source_period_from_metadata(
    attrs: dict[str, Any], fallback_init: str, source_path: Path | None = None
) -> dict[str, Any]:
    forecast_info = str(attrs.get("MME_Forecast_Info", ""))
    match = re.search(r"Forecast\s+for\s+([A-Z]{3})\s*\(\s*(\d{4})", forecast_info, re.IGNORECASE)
    if not match and source_path is not None:
        match = re.search(r"_([A-Z]{3})_(\d{4})", source_path.name, re.IGNORECASE)
    if match:
        period = period_from_season(match.group(1), int(match.group(2)))
        period["forecast_info"] = forecast_info
        return period
    target_code, period_label, first_target = target_window(fallback_init, "0,1,2")
    first_year, first_month = int(first_target[:4]), int(first_target[4:])
    last_year, last_month = month_after(first_year, first_month, 2)
    end_year, end_month = month_after(last_year, last_month, 1)
    return {
        "season_code": "",
        "season_year": first_year,
        "period_label": period_label,
        "target_code": target_code,
        "first_target": first_target,
        "valid_start_utc": f"{first_year:04d}-{first_month:02d}-01T00:00:00Z",
        "valid_end_utc": f"{end_year:04d}-{end_month:02d}-01T00:00:00Z",
        "days": sum(
            calendar.monthrange(year, month)[1]
            for year, month in (
                (first_year, first_month),
                month_after(first_year, first_month, 1),
                (last_year, last_month),
            )
        ),
        "forecast_info": forecast_info,
        "fallback": True,
    }


def source_issue_datetime(attrs: dict[str, Any], request_target_month: str) -> dt.datetime:
    """Read the provider issue date, with a conservative mid-month fallback."""

    issued = str(attrs.get("Issued_Date", "")).strip()
    for date_format in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(issued, date_format)
            return parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    target = dt.datetime.strptime(request_target_month, "%Y%m")
    issue_year, issue_month = month_after(target.year, target.month, -1)
    return dt.datetime(issue_year, issue_month, APCC_RELEASE_DAY, tzinfo=dt.timezone.utc)


def _request_details(args: argparse.Namespace, init: str, variables: list[str]) -> dict[str, Any]:
    return {
        "jobtype": "MME",
        "dataset": args.dataset,
        "lead_month": args.lead_month,
        "resolution": args.resolution,
        "type": "FORECAST",
        "variable": variables,
        "method": args.method,
        "period": ["SEASONAL"],
        "yearmonth": [init],
    }


def request_archive(details: dict[str, Any], output: Path, args: argparse.Namespace) -> Path:
    if output.exists() and output.stat().st_size > 0:
        return output
    api_key = os.environ.get("APCC_API_KEY", "").strip()
    if not api_key:
        raise APCCError("APCC_API_KEY repository secret is required for APCC CLIK data")
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - workflow installs requests
        raise APCCError("APCC rendering requires requests") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.post(
            args.request_url,
            json={"key": api_key, "details": details},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=(30, 120),
        )
        response.raise_for_status()
        reply = response.json()
    except Exception as exc:
        raise APCCError(f"APCC request could not be submitted: {exc}") from exc
    if int(reply.get("status", 500)) > 202:
        info = reply.get("data", {}).get("info", "no additional information")
        raise APCCError(f"APCC request rejected: {reply.get('message', 'unknown error')} ({info})")
    data = reply.get("data") or {}
    job_id = data.get("id")
    if not job_id:
        raise APCCError("APCC request did not return a job id")

    deadline = time.monotonic() + max(60, args.timeout_minutes * 60)
    status = str(data.get("status", "Queued"))
    download_url = data.get("download_url")
    while status.lower() not in {"complete", "completed"}:
        if status.lower() == "failed":
            raise APCCError(f"APCC job {job_id} failed: {data.get('message', 'unknown error')}")
        if time.monotonic() >= deadline:
            raise APCCError(f"APCC job {job_id} exceeded the {args.timeout_minutes}-minute timeout")
        time.sleep(max(1.0, args.poll_seconds))
        try:
            status_response = requests.get(f"{args.status_url.rstrip('/')}/{job_id}", timeout=(30, 60))
            status_response.raise_for_status()
            status_reply = status_response.json()
        except Exception as exc:
            raise APCCError(f"APCC job status could not be read: {exc}") from exc
        data = status_reply.get("data") or {}
        status = str(data.get("status", ""))
        download_url = data.get("download_url", download_url)
        print(f"APCC job {job_id}: {stat×½ô¶‰žËkºwµçd€”€ÌØÀ¸À¤€´€ÄàÀ¸À(€€€±½¹}½É‘•È€ô¹À¹…ÉÍ½ÉÐ¡¹½Éµ…±¥é•‘}±½¹Ì¤(€€€±…Ñ}½É‘•È€ô¹À¹…ÉÍ½ÉÐ¡±…ÑÌ¤(€€€½É‘•É•€ôÙ…±Õ•Ím¹À¹¥á|¡±…Ñ}½É‘•È°±½¹}½É‘•È¥t(€€€¥˜¹½Ð¹À¹¥Í™¥¹¥Ñ”¡½É‘•É•¤¹…¹ä ¤è(€€€€€€€É…¥Í”AÉÉ½È¡˜‰A9•ÑíÁ…Ñ ¹¹…µ•ô½¹Ñ…¥¹Ì¹¼™¥¹¥Ñ”Ù…±Õ•Ìˆ¤(€€€É•ÑÕÉ¸É¥ (€€€€€€€±½¹Ìõm™±½…Ð¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸¹½Éµ…±¥é•‘}±½¹Ím±½¹}½É‘•Éut°(€€€€€€€±…ÑÌõm™±½…Ð¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸±…ÑÍm±…Ñ}½É‘•Éut°(€€€€€€€Ù…±Õ•Ìõ½É‘•É•¹Ñ½±¥ÍÐ ¤°(€€€€¤(()‘•˜É¥‘}ÍÑ…ÑÌ¡É¥èÉ¥¤€´ø‘¥ÑmÍÑÈ°™±½…Ñtè(€€€Ù…±Õ•Ì€ô¹À¹…Í…ÉÉ…ä¡É¥¹Ù…±Õ•Ì°‘ÑåÁ”õ™±½…Ð¤(€€€™¥¹¥Ñ”€ôÙ…±Õ•Ím¹À¹¥Í™¥¹¥Ñ”¡Ù…±Õ•Ì¥t(€€€¥˜™¥¹¥Ñ”¹Í¥é”€ôô€Àè(€€€€€€€É…¥Í”AÉÉ½È ‰‘•½‘•AÉ¥½¹Ñ…¥¹Ì¹¼™¥¹¥Ñ”Ù…±Õ•Ìˆ¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰µ¥¸ˆèÉ½Õ¹¡™±½…Ð¡¹À¹µ¥¸¡™¥¹¥Ñ”¤¤°€Ð¤°(€€€€€€€€‰µ…àˆèÉ½Õ¹¡™±½…Ð¡¹À¹µ…à¡™¥¹¥Ñ”¤¤°€Ð¤°(€€€€€€€€‰ÀÀÔˆèÉ½Õ¹¡™±½…Ð¡¹À¹Á•É•¹Ñ¥±”¡™¥¹¥Ñ”°€Ô¤¤°€Ð¤°(€€€€€€€€‰ÀäÔˆèÉ½Õ¹¡™±½…Ð¡¹À¹Á•É•¹Ñ¥±”¡™¥¹¥Ñ”°€äÔ¤¤°€Ð¤°(€€€€€€€€‰™¥¹¥Ñ•}Á½¥¹ÑÌˆè¥¹Ð¡™¥¹¥Ñ”¹Í¥é”¤°(€€€ô(()‘•˜É¥‘}É•Í½±ÕÑ¥½¸¡É¥èÉ¥¤€´ø‘¥ÑmÍÑÈ°™±½…Ñtè(€€€±½¹}ÍÑ•À€ô¹À¹‘¥™˜¡¹À¹…Í…ÉÉ…ä¡É¥¹±½¹Ì°‘ÑåÁ”õ™±½…Ð¤¤(€€€±…Ñ}ÍÑ•À€ô¹À¹‘¥™˜¡¹À¹…Í…ÉÉ…ä¡É¥¹±…ÑÌ°‘ÑåÁ”õ™±½…Ð¤¤(€€€É•ÑÕÉ¸ì(€€€€€€€€‰±½¹¥ÑÕ‘•}‘•É••ÌˆèÉ½Õ¹¡™±½…Ð¡¹À¹µ•‘¥…¸¡¹À¹…‰Ì¡±½¹}ÍÑ•À¤¤¤°€Ð¤¥˜±½¹}ÍÑ•À¹Í¥é”•±Í”€À¸À°(€€€€€€€€‰±…Ñ¥ÑÕ‘•}‘•É••ÌˆèÉ½Õ¹¡™±½…Ð¡¹À¹µ•‘¥…¸¡¹À¹…‰Ì¡±…Ñ}ÍÑ•À¤¤¤°€Ð¤¥˜±…Ñ}ÍÑ•À¹Í¥é”•±Í”€À¸À°(€€€ô(()‘•˜™¥¹‘}ÁÉ½‘ÕÑ}™¥±”¡™¥±•Ìè%Ñ•É…‰±•mA…Ñ¡t°ÁÉ½‘ÕÐè‘¥ÑmÍÑÈ°¹åt°Í•…Í½¹}½‘”èÍÑÈ¤€´øA…Ñ è(€€€…¹‘¥‘…Ñ•Ì€ômÁ…Ñ ™½ÈÁ…Ñ ¥¸™¥±•Ì¥˜Á…Ñ ¹ÍÕ™™¥à¹±½Ý•È ¤¥¸ìˆ¹¹Œˆ°€ˆ¹¹ŒÐˆ°€ˆ¹¹•Ñ‘˜‰õt(€€€¥˜¹½Ð…¹‘¥‘…Ñ•Ìè(€€€€€€€É…¥Í”AÉÉ½È ‰AÉ•ÍÕ±Ði%@½¹Ñ…¥¹•¹¼9•Ñ™¥±•Ìˆ¤(€€€Ñ½­•¸€ôÁÉ½‘ÕÑl‰…Á¥}Ù…É¥…‰±”‰t¹±½Ý•È ¤(€€€µ…Ñ¡¥¹œ€ômÁ…Ñ ™½ÈÁ…Ñ ¥¸…¹‘¥‘…Ñ•Ì¥˜Ñ½­•¸¥¸Á…Ñ ¹¹…µ”¹±½Ý•È ¥t(€€€¥˜¹½Ðµ…Ñ¡¥¹œè(€€€€€€€µ…Ñ¡¥¹œ€ô…¹‘¥‘…Ñ•Ì(€€€¥˜Í•…Í½¹}½‘”è(€€€€€€€Í•…Í½¹…°€ômÁ…Ñ ™½ÈÁ…Ñ ¥¸µ…Ñ¡¥¹œ¥˜Í•…Í½¹}½‘”¹±½Ý•È ¤¥¸ÍÑÈ¡Á…Ñ ¤¹±½Ý•È ¥t(€€€€€€€¥˜¹½ÐÍ•…Í½¹…°è(€€€€€€€€€€€É…¥Í”AÉÉ½È (€€€€€€€€€€€€€€€˜‰AÉ•ÍÕ±Ði%@½¹Ñ…¥¹•¹¼íÍ•…Í½¹}½‘”¹ÕÁÁ•È ¥ô™¥±”™½ÈíÁÉ½‘ÕÑl…Á¥}Ù…É¥…‰±”uôˆ(€€€€€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸Í½ÉÑ•¡Í•…Í½¹…°¥lÁt(€€€É•ÑÕÉ¸Í½ÉÑ•¡µ…Ñ¡¥¹œ¥lÁt(()‘•˜ÝÉ¥Ñ•}µ…¹¥™•ÍÐ (€€€Á…Ñ èA…Ñ °(€€€•¹ÑÉ¥•Ìè%Ñ•É…‰±•m‘¥ÑmÍÑÈ°¹åut°(€€€ÁÉ•Ù¥½ÕÌèA…Ñ ð9½¹”°(€€€É•Ñ…¥¹}å±•Ìè¥¹Ð°(€€€‘…Ñ…Í•ÐèÍÑÈ€ô€‰55|Í5=9Q ˆ°(¤€´ø9½¹”è(€€€…±±}•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½È•á¥ÍÑ¥¹}Á…Ñ ¥¸€¡ÁÉ•Ù¥½ÕÌ°Á…Ñ ¤è(€€€€€€€¥˜¹½Ð•á¥ÍÑ¥¹}Á…Ñ ½È¹½Ð•á¥ÍÑ¥¹}Á…Ñ ¹•á¥ÍÑÌ ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€ÑÉäè(€€€€€€€€€€€Á…å±½…€ô©Í½¸¹±½…‘Ì¡•á¥ÍÑ¥¹}Á…Ñ ¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡ÉÕ¸™½ÈÉÕ¸¥¸Á…å±½…¹•Ð ‰ÉÕ¹Ìˆ°mt¤¥˜¥Í¥¹ÍÑ…¹”¡ÉÕ¸°‘¥Ð¤¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”AÉÉ½È¡˜‰½Õ±¹½ÐÉ•…ÁÉ•Ù¥½ÕÌAµ…¹¥™•ÍÐí•á¥ÍÑ¥¹}Á…Ñ¡ôèí•áôˆ¤™É½´•áŒ(€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡•¹ÑÉ¥•Ì¤(€€€Õ¹¥ÅÕ”€ôíÍÑÈ¡ÉÕ¸¹•Ð ‰¥ˆ¤¤èÉÕ¸™½ÈÉÕ¸¥¸…±±}•¹ÑÉ¥•Ì¥˜ÉÕ¸¹•Ð ‰¥ˆ¥ô(€€€½É‘•É•€ôÍ½ÉÑ•¡Õ¹¥ÅÕ”¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„¥Ñ•´è€¡ÍÑÈ¡¥Ñ•´¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤°ÍÑÈ¡¥Ñ•´¹•Ð ‰¥ˆ°€ˆˆ¤¤¤°É•Ù•ÉÍ”õQÉÕ”¤(€€€å±•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½ÈÉÕ¸¥¸½É‘•É•è(€€€€€€€å±”€ôÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤(€€€€€€€¥˜å±”¹½Ð¥¸å±•Ìè(€€€€€€€€€€€å±•Ì¹…ÁÁ•¹¡å±”¤(€€€­••À€ôÍ•Ð¡å±•Íléµ…à Ä°É•Ñ…¥¹}å±•Ì¥t¤(€€€Á…å±½…€ôì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰­¥¹ˆè€‰…Á}Í•…Í½¹…±}µ…¹¥™•ÍÐˆ°(€€€€€€€€‰•¹•É…Ñ•‘}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹¹½Ü¡‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€‰Í½ÕÉ”ˆè€‰AµÕ±Ñ¤µµ½‘•°•¹Í•µ‰±”Ù¥„1%,ˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆèA}M=UI}UI0°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±ÌˆèmA}M=UI}UI0°‘…Ñ…Í•Ñ}ÕÉ°¡‘…Ñ…Í•Ð¤°A}A%}=M}UI1t°(€€€€€€€€‰…­¹½Ý±•‘•µ•¹ÐˆèA}-9=]159P°(€€€€€€€€‰ÁÉ½‘ÕÑ}±…‰•±Ìˆèì(€€€€€€€€€€€€ˆÔÀÁµ‰}¡•¥¡Ñ}…¹½µ…±äˆè€ˆÔÀÀµµˆ!•¥¡Ð¹½µ…±äˆ°(€€€€€€€€€€€€ˆàÔÁµ‰}Ñ•µÁ•É…ÑÕÉ•}…¹½µ…±äˆè€ˆàÔÀµµˆQ•µÁ•É…ÑÕÉ”¹½µ…±äˆ°(€€€€€€€€€€€€ˆÉµ}Ñ•µÁ•É…ÑÕÉ•}…¹½µ…±äˆè€ˆÈµ´Q•µÁ•É…ÑÕÉ”¹½µ…±äˆ°(€€€€€€€€€€€€‰ÁÉ•¥Á¥Ñ…Ñ¥½¹}…¹½µ…±äˆè€‰AÉ•¥Á¥Ñ…Ñ¥½¸¹½µ…±äˆ°(€€€€€€€€€€€€‰Í•…}ÍÕÉ™…•}Ñ•µÁ•É…ÑÕÉ•}…¹½µ…±äˆè€‰M•„µMÕÉ™…”Q•µÁ•É…ÑÕÉ”¹½µ…±äˆ°(€€€€€€€€€€€€‰µÍ±Á}…¹½µ…±äˆè€‰5M1@¹½µ…±äˆ°(€€€€€€€ô°(€€€€€€€€‰É•Ñ•¹Ñ¥½¸ˆèì‰µ…á}å±•Ìˆèµ…à Ä°É•Ñ…¥¹}å±•Ì¤°€‰¡¥ÍÑ½Éå}å±•Ìˆèµ…à À°É•Ñ…¥¹}å±•Ì€´€Ä¥ô°(€€€€€€€€‰ÉÕ¹ÌˆèmÉÕ¸™½ÈÉÕ¸¥¸½É‘•É•¥˜ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤¥¸­••Át°(€€€ô(€€€Á…Ñ ¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€Ñ•µÁ½É…Éä€ôÁ…Ñ ¹Ý¥Ñ¡}¹…µ”¡Á…Ñ ¹¹…µ”€¬€ˆ¹ÑµÀˆ¤(€€€Ñ•µÁ½É…Éä¹ÝÉ¥Ñ•}Ñ•áÐ¡©Í½¸¹‘ÕµÁÌ¡Á…å±½…°¥¹‘•¹ÐôÈ¤€¬€‰q¸ˆ°•¹½‘¥¹œô‰ÕÑ˜´àˆ¤(€€€Ñ•µÁ½É…Éä¹É•Á±…”¡Á…Ñ ¤(()‘•˜‰Õ¥±‘}Á…ÉÍ•È ¤€´ø…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•Èè(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¡‘•ÍÉ¥ÁÑ¥½¸õ}}‘½}|¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‘ÕÐˆ°¡½¥•Ìô ‰…±°ˆ°€©AI=UQ}MAL¤°‘•™…Õ±Ðô‰…±°ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¥¹¥Ðˆ°‘•™…Õ±Ðô‰±…Ñ•ÍÐˆ°¡•±Àô‰A™¥ÉÍÐÑ…É•Ðµ½¹Ñ …Ìeeee54½È±…Ñ•ÍÐˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÑ…É•ÐµÝ¥¹‘½Üˆ°‘•™…Õ±Ðôˆˆ°¡•±Àô‰Ñ¡É•”µµ½¹Ñ ½™™Í•ÑÌ™É½´Ñ¡”™¥ÉÍÐÑ…É•Ðµ½¹Ñ ì‘•™…Õ±ÑÌÑ¼Ñ¡”™…ÈÝ¥¹‘½Ü™½ÈÑ¡”Í•±•Ñ•¡½É¥é½¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘…Ñ…Í•Ðˆ°‘•™…Õ±Ðô‰55|Ù5=9Q ˆ°¡½¥•Ìô ‰55|Í5=9Q ˆ°€‰55|Ù5=9Q ˆ¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ±•…µµ½¹Ñ ˆ°‘•™…Õ±Ðõ9½¹”°¡½¥•Ìô ˆÌµ5=8ˆ°€ˆØµ5=8ˆ¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•Í½±ÕÑ¥½¸ˆ°‘•™…Õ±ÐôˆÈ¸Ôˆ°¡½¥•Ìô ˆÄ¸Àˆ°€ˆÈ¸Ôˆ¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ•Ñ¡½ˆ°‘•™…Õ±Ðô‰M4ˆ°¡½¥•Ìô ‰M4ˆ°€‰ULˆ¤¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•ÅÕ•ÍÐµÕÉ°ˆ°‘•™…Õ±ÐõA}IEUMQ}UI0¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍÑ…ÑÕÌµÕÉ°ˆ°‘•™…Õ±ÐõA}MQQUM}UI0¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÑ¥µ•½ÕÐµµ¥¹ÕÑ•Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôÐÔ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁ½±°µÍ•½¹‘Ìˆ°ÑåÁ”õ™±½…Ð°‘•™…Õ±ÐôÄÀ¸À¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…¡”µ‘¥Èˆ°‘•™…Õ±Ðôˆ¹…¡”½…ÁŒˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½ÕÑÁÕÐµ‘¥Èˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½…ÁŒˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ…¹¥™•ÍÐˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½…Á}µ…¹¥™•ÍÐ¹©Í½¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ•Ù¥½ÕÌµµ…¹¥™•ÍÐˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•Ñ…¥¸µå±•Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôÐ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ•½©Í½¸ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ‰½É‘•ÉÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘•½‘”µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È(()‘•˜ÉÕ¸¡…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”¤€´ø¥¹Ðè(€€€É•Á½}É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€•áÁ•Ñ•‘}¡½É¥é½¸€ô€ˆÌµ5=8ˆ¥˜…ÉÌ¹‘…Ñ…Í•Ð€ôô€‰55|Í5=9Q ˆ•±Í”€ˆØµ5=8ˆ(€€€¥˜…ÉÌ¹±•…‘}µ½¹Ñ …¹…ÉÌ¹±•…‘}µ½¹Ñ €„ô•áÁ•Ñ•‘}¡½É¥é½¸è(€€€€€€€É…¥Í”AÉÉ½È¡˜‰í…ÉÌ¹‘…Ñ…Í•ÑôµÕÍÐÕÍ”Ñ¡”í•áÁ•Ñ•‘}¡½É¥é½¹ô™½É•…ÍÐ¡½É¥é½¸ˆ¤(€€€…ÉÌ¹±•…‘}µ½¹Ñ €ô•áÁ•Ñ•‘}¡½É¥é½¸(€€€¥˜¹½Ð…ÉÌ¹Ñ…É•Ñ}Ý¥¹‘½Üè(€€€€€€€…ÉÌ¹Ñ…É•Ñ}Ý¥¹‘½Ü€ô€ˆÀ°Ä°Èˆ¥˜…ÉÌ¹‘…Ñ…Í•Ð€ôô€‰55|Í5=9Q ˆ•±Í”€ˆÌ°Ð°Ôˆ(€€€¥¹¥Ð€ôÁ…ÉÍ•}¥¹¥Ð¡…ÉÌ¹¥¹¥Ð¤(€€€É•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘”°É•ÅÕ•ÍÑ•‘}Á•É¥½°É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}Ñ…É•Ð€ôÑ…É•Ñ}Ý¥¹‘½Ü¡¥¹¥Ð°…ÉÌ¹Ñ…É•Ñ}Ý¥¹‘½Ü¤(€€€Í•±•Ñ•€ô±¥ÍÐ¡AI=UQ}MAL¤¥˜…ÉÌ¹ÁÉ½‘ÕÐ€ôô€‰…±°ˆ•±Í”m…ÉÌ¹ÁÉ½‘ÕÑt(€€€…¡•}‘¥È€ôA…Ñ ¡…ÉÌ¹…¡•}‘¥È¤¥˜A…Ñ ¡…ÉÌ¹…¡•}‘¥È¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É•Á½}É½½Ð€¼…ÉÌ¹…¡•}‘¥È(€€€½ÕÑÁÕÑ}‘¥È€ôA…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È¤¥˜A…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É•Á½}É½½Ð€¼…ÉÌ¹½ÕÑÁÕÑ}‘¥È(€€€µ…¹¥™•ÍÑ}Á…Ñ €ôA…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ¤¥˜A…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ¤¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É•Á½}É½½Ð€¼…ÉÌ¹µ…¹¥™•ÍÐ(€€€ÁÉ•Ù¥½ÕÌ€ô…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ¥˜¹½Ð…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ½È…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É•Á½}É½½Ð€¼…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ(€€€‰½É‘•ÉÌ€ô•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°…¡•}‘¥È°É•Á½}É½½Ð¤(€€€Ù…É¥…‰±•Ì€ôÍ½ÉÑ•¡íAI=UQ}MAMmÁÉ½‘ÕÑul‰…Á¥}Ù…É¥…‰±”‰t™½ÈÁÉ½‘ÕÐ¥¸Í•±•Ñ•‘ô¤(€€€…É¡¥Ù”€ô…¡•}‘¥È€¼€‰…É¡¥Ù•Ìˆ€¼˜‰í…ÉÌ¹‘…Ñ…Í•Ð¹±½Ý•È ¥õ}í…ÉÌ¹±•…‘}µ½¹Ñ ¹±½Ý•È ¥õ}í¥¹¥Ñõ}ì|œ¹©½¥¸¡Ù…É¥…‰±•Ì¥ô¹é¥Àˆ(€€€™¥±•Ìè±¥ÍÑmA…Ñ¡t€ômt(€€€¥˜¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€É•ÅÕ•ÍÑ}…É¡¥Ù”¡}É•ÅÕ•ÍÑ}‘•Ñ…¥±Ì¡…ÉÌ°¥¹¥Ð°Ù…É¥…‰±•Ì¤°…É¡¥Ù”°…ÉÌ¤(€€€€€€€™¥±•Ì€ôÍ…™•}•áÑÉ…Ð¡…É¡¥Ù”°…¡•}‘¥È€¼€‰•áÑÉ…Ñ•ˆ€¼…É¡¥Ù”¹ÍÑ•´¤(€€€É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}µ½¹Ñ €ô¥¹Ð¡É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}Ñ…É•ÑlèÑt¤°¥¹Ð¡É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}Ñ…É•ÑlÐét¤(€€€É•ÅÕ•ÍÑ•‘}±…ÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}±…ÍÑ}µ½¹Ñ €ôµ½¹Ñ¡}…™Ñ•È¡É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}µ½¹Ñ °€È¤(€€€É•ÅÕ•ÍÑ•‘}•¹‘}å•…È°É•ÅÕ•ÍÑ•‘}•¹‘}µ½¹Ñ €ôµ½¹Ñ¡}…™Ñ•È¡É•ÅÕ•ÍÑ•‘}±…ÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}±…ÍÑ}µ½¹Ñ °€Ä¤(€€€¹…Ñ¥Ù•}Á•É¥½€ôì(€€€€€€€€‰Í•…Í½¹}½‘”ˆèÉ•ÅÕ•ÍÑ•‘}Á•É¥½¹ÍÁ±¥Ð ¥lÁt°(€€€€€€€€‰Á•É¥½‘}±…‰•°ˆèÉ•ÅÕ•ÍÑ•‘}Á•É¥½°(€€€€€€€€‰Ñ…É•Ñ}½‘”ˆèÉ•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘”°(€€€€€€€€‰™¥ÉÍÑ}Ñ…É•ÐˆèÉ•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆè˜‰íÉ•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘•lèÑuôµíÉ•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘•lÐèÙuô´ÀÅPÀÀèÀÀèÀÁhˆ°(€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆè˜‰íÉ•ÅÕ•ÍÑ•‘}•¹‘}å•…ÈèÀÑ‘ôµíÉ•ÅÕ•ÍÑ•‘}•¹‘}µ½¹Ñ èÀÉ‘ô´ÀÅPÀÀèÀÀèÀÁhˆ°(€€€€€€€€‰‘…åÌˆèÍÕ´ (€€€€€€€€€€€…±•¹‘…È¹µ½¹Ñ¡É…¹”¡å•…È°µ½¹Ñ ¥lÅt(€€€€€€€€€€€™½Èå•…È°µ½¹Ñ ¥¸€ (€€€€€€€€€€€€€€€€¡É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}µ½¹Ñ ¤°(€€€€€€€€€€€€€€€µ½¹Ñ¡}…™Ñ•È¡É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}™¥ÉÍÑ}µ½¹Ñ °€Ä¤°(€€€€€€€€€€€€€€€€¡É•ÅÕ•ÍÑ•‘}±…ÍÑ}å•…È°É•ÅÕ•ÍÑ•‘}±…ÍÑ}µ½¹Ñ ¤°(€€€€€€€€€€€€¤(€€€€€€€€¤°(€€€€€€€€‰™½É•…ÍÑ}¥¹™¼ˆè€ˆˆ°(€€€€€€€€‰™…±±‰…¬ˆèQÉÕ”°(€€€ô(€€€¥˜™¥±•Ìè(€€€€€€€É•ÅÕ•ÍÑ•‘}Í•…Í½¹}½‘”€ôÉ•ÅÕ•ÍÑ•‘}Á•É¥½¹ÍÁ±¥Ð ¥lÁt¹ÕÁÁ•È ¤(€€€€€€€¥˜É•ÅÕ•ÍÑ•‘}Í•…Í½¹}½‘”¹½Ð¥¸MM=9}MQIQ}5=9Q è(€€€€€€€€€€€É…¥Í”AÉÉ½È (€€€€€€€€€€€€€€€€ˆ´µÑ…É•ÐµÝ¥¹‘½ÜµÕÍÐÉ•Í½±Ù”Ñ¼„¹…µ•Ñ¡É•”µµ½¹Ñ Í•…Í½¸™½ÈAÍ•…Í½¹…°‘…Ñ„ˆ(€€€€€€€€€€€€¤(€€€€€€€É•™•É•¹•}Á…Ñ €ô™¥¹‘}ÁÉ½‘ÕÑ}™¥±” (€€€€€€€€€€€™¥±•Ì°(€€€€€€€€€€€AI=UQ}MAMmÍ•±•Ñ•‘lÁut°(€€€€€€€€€€€É•ÅÕ•ÍÑ•‘}Í•…Í½¹}½‘”°(€€€€€€€€¤(€€€€€€€É•™•É•¹•}µ•Ñ…‘…Ñ„€ôÉ•…‘}¹•Ñ‘™}µ•Ñ…‘…Ñ„¡É•™•É•¹•}Á…Ñ °AI=UQ}MAMmÍ•±•Ñ•‘lÁut¤(€€€€€€€¹…Ñ¥Ù•}Á•É¥½€ôÍ½ÕÉ•}Á•É¥½‘}™É½µ}µ•Ñ…‘…Ñ„ (€€€€€€€€€€€É•™•É•¹•}µ•Ñ…‘…Ñ…l‰±½‰…±}…ÑÑÉÌ‰t°¥¹¥Ð°Í½ÕÉ•}Á…Ñ õÉ•™•É•¹•}Á…Ñ (€€€€€€€€¤(€€€€€€€¥˜¹…Ñ¥Ù•}Á•É¥½‘l‰Ñ…É•Ñ}½‘”‰t€„ôÉ•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘”è(€€€€€€€€€€€É…¥Í”AÉÉ½È (€€€€€€€€€€€€€€€€‰AÍ½ÕÉ”Í•…Í½¸‘½•Ì¹½Ðµ…Ñ Ñ¡”É•ÅÕ•ÍÑ•Ñ…É•ÐÝ¥¹‘½Ü€ˆ(€€€€€€€€€€€€€€€˜ˆ¡í¹…Ñ¥Ù•}Á•É¥½‘lÑ…É•Ñ}½‘”uô€„ôíÉ•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}½‘•ô¤ˆ(€€€€€€€€€€€€¤(€€€Ñ…É•Ñ}½‘”€ô¹…Ñ¥Ù•}Á•É¥½‘l‰Ñ…É•Ñ}½‘”‰t(€€€Á•É¥½€ô¹…Ñ¥Ù•}Á•É¥½‘l‰Á•É¥½‘}±…‰•°‰t(€€€™¥ÉÍÑ}Ñ…É•Ð€ô¹…Ñ¥Ù•}Á•É¥½‘l‰™¥ÉÍÑ}Ñ…É•Ð‰t(€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€ÍÕ•ÍÍ•Ì€ô€À(€€€¥ÍÍÕ•}‘…Ñ•Ñ¥µ”€ôÍ½ÕÉ•}¥ÍÍÕ•}‘…Ñ•Ñ¥µ” (€€€€€€€É•™•É•¹•}µ•Ñ…‘…Ñ…l‰±½‰…±}…ÑÑÉÌ‰t¥˜™¥±•Ì•±Í”íô°(€€€€€€€¥¹¥Ð°(€€€€¤(€€€¥¹¥Ñ}ÕÑŒ€ô¥Í½}ÕÑŒ¡¥ÍÍÕ•}‘…Ñ•Ñ¥µ”¤(€€€É•¹‘•É•É}¥¹¥Ð€ô¥ÍÍÕ•}‘…Ñ•Ñ¥µ”¹ÍÑÉ™Ñ¥µ” ˆ•d•´•• ˆ¤(€€€¥ÍÍÕ•}±…‰•°€ô˜‰%ÍÍÕ•í¥ÍÍÕ•}‘…Ñ•Ñ¥µ”è•€•ˆ€•eôˆ(€€€Ù…±¥‘}ÍÑ…ÉÐ€ô¹…Ñ¥Ù•}Á•É¥½‘l‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒ‰t(€€€Ù…±¥‘}•¹€ô¹…Ñ¥Ù•}Á•É¥½‘l‰Ù…±¥‘}•¹‘}ÕÑŒ‰t(€€€Í½ÕÉ•}Í•…Í½¹}½‘”€ôÍÑÈ¡¹…Ñ¥Ù•}Á•É¥½¹•Ð ‰Í•…Í½¹}½‘”ˆ°€ˆˆ¤¤(€€€™½ÈÁÉ½‘ÕÑ}¹…µ”¥¸Í•±•Ñ•è(€€€€€€€ÁÉ½‘ÕÐ€ôAI=UQ}MAMmÁÉ½‘ÕÑ}¹…µ•t(€€€€€€€Ñ…É•Ñ}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰…ÁŒµí¥¹¥ÑôµíÁÉ½‘ÕÑl¥‘}Ñ½­•¸uôµíÑ…É•Ñ}½‘•ôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÙ…±¥‘}ÍÑ…ÉÐ°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÙ…±¥‘}•¹°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè…ÉÌ¹±•…‘}µ½¹Ñ °(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ñ}½‘”°(€€€€€€€€€€€€‰Á•É¥½‘}±…‰•°ˆèÁ•É¥½°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè€‰¹…Ñ¥Ù”AÍ•…Í½¹…°µ•…¸ˆ°(€€€€€€€€€€€€‰™¥•±ˆèÁÉ½‘ÕÑl‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰É…Ý}™¥•±ˆèÁÉ½‘ÕÑl‰É…Ý}™¥•±‰t°(€€€€€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÁÉ½‘ÕÑl‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰AµÕ±Ñ¤µµ½‘•°•¹Í•µ‰±”µ•…¸ˆ°(€€€€€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€‰A55Í•…Í½¹…°ÁÉ½‘ÕÐˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèm‘…Ñ…Í•Ñ}ÕÉ°¡…ÉÌ¹‘…Ñ…Í•Ð¤°A}A%}=M}UI1t°(€€€€€€€€€€€€‰Í½ÕÉ•}Á•É¥½ˆè¹…Ñ¥Ù•}Á•É¥½°(€€€€€€€€€€€€‰Í½ÕÉ•}¥ÍÍÕ•}ÕÑŒˆè¥¹¥Ñ}ÕÑŒ°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÉÕ¹}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰…ÁŒµí¥¹¥ÑôµíÁÉ½‘ÕÑ}¹…µ•ôˆ°(€€€€€€€€€€€€‰¥¹¥Ñ}ÕÑŒˆè¥¹¥Ñ}ÕÑŒ°(€€€€€€€€€€€€‰ÁÉ½‘ÕÐˆèÁÉ½‘ÕÑ}¹…µ”°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰AµÕ±Ñ¤µµ½‘•°•¹Í•µ‰±”Ù¥„1%,ˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆèA}M=UI}UI0°(€€€€€€€€€€€€‰‘…Ñ…Í•Ðˆè…ÉÌ¹‘…Ñ…Í•Ð°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè…ÉÌ¹±•…‘}µ½¹Ñ °(€€€€€€€€€€€€‰™½É•…ÍÑ}¡½É¥é½¸ˆè…ÉÌ¹±•…‘}µ½¹Ñ °(€€€€€€€€€€€€‰É•ÅÕ•ÍÑ}Ñ…É•Ñ}µ½¹Ñ ˆè¥¹¥Ð°(€€€€€€€€€€€€‰É•Í½±ÕÑ¥½¸ˆè…ÉÌ¹É•Í½±ÕÑ¥½¸°(€€€€€€€€€€€€‰µ•Ñ¡½ˆè…ÉÌ¹µ•Ñ¡½°(€€€€€€€€€€€€‰É•ÅÕ•ÍÑ•‘}Ñ…É•Ñ}Ý¥¹‘½Üˆè…ÉÌ¹Ñ…É•Ñ}Ý¥¹‘½Ü°(€€€€€€€€€€€€‰¹…Ñ¥Ù•}Á•É¥½ˆè¹…Ñ¥Ù•}Á•É¥½°(€€€€€€€€€€€€‰Ñ…É•ÑÌˆèmÑ…É•Ñ}•¹ÑÉåt°(€€€€€€€€€€€€‰½ÕÑÁÕÑ}‘¥ÈˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€É…¥Í”AÉÉ½È ˆ´µ‘•½‘”µ½¹±äÉ•ÅÕ¥É•Ì„…¡•A…É¡¥Ù”ìÁÉ½Ù¥‘”Ñ¡”…‘…ÁÑ•È…¡”™¥ÉÍÐˆ¤(€€€€€€€€€€€Í½ÕÉ•}Á…Ñ €ô™¥¹‘}ÁÉ½‘ÕÑ}™¥±”¡™¥±•Ì°ÁÉ½‘ÕÐ°Í½ÕÉ•}Í•…Í½¹}½‘”¤(€€€€€€€€€€€µ•Ñ…‘…Ñ„€ôÉ•…‘}¹•Ñ‘™}µ•Ñ…‘…Ñ„¡Í½ÕÉ•}Á…Ñ °ÁÉ½‘ÕÐ¤(€€€€€€€€€€€Í½ÕÉ•}Õ¹¥ÑÌ€ô}Í½ÕÉ•}Õ¹¥ÑÌ¡µ•Ñ…‘…Ñ…l‰‘…Ñ…}…ÑÑÉÌ‰t¤(€€€€€€€€€€€É¥€ôÉ¥‘}™É½µ}¹•Ñ‘˜¡Í½ÕÉ•}Á…Ñ °ÁÉ½‘ÕÐ°ÁÉ•¥Á}‘…åÌõ¥¹Ð¡¹…Ñ¥Ù•}Á•É¥½‘l‰‘…åÌ‰t¤¤(€€€€€€€€€€€ÍÑ…ÑÌ€ôÉ¥‘}ÍÑ…ÑÌ¡É¥¤(€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ð€¼˜‰…Á}íÁÉ½‘ÕÑl¥‘}Ñ½­•¸uõ}íÑ…É•Ñ}½‘•ô¹©Áœˆ(€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€É¥°(€€€€€€€€€€€€€€€É•¹‘•É•É}¥¹¥Ð°(€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€…ÉÌ¹±•…‘}µ½¹Ñ °(€€€€€€€€€€€€€€€mt°(€€€€€€€€€€€€€€€½ÕÑÁÕÐ°(€€€€€€€€€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°ô‰9…Ñ¥Ù”A55…¹½µ…±äˆ°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•ÉÌ°(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½°(€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°ô‰AµÕ±Ñ¤µµ½‘•°•¹Í•µ‰±”µ•…¸ˆ°(€€€€€€€€€€€€€€€¥¹¥Ñ¥…±¥é…Ñ¥½¹}±…‰•°õ¥ÍÍÕ•}±…‰•°°(€€€€€€€€€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•Œõì(€€€€€€€€€€€€€€€€€€€€¨©ÁÉ½‘ÕÐ°(€€€€€€€€€€€€€€€€€€€€‰¹…µ”ˆèÁÉ½‘ÕÑ}¹…µ”°(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}±…‰•°ˆè€‰A55€¼1%,ˆ°(€€€€€€€€€€€€€€€€€€€€‰±•…‘}±…‰•°ˆè˜‰í…ÉÌ¹±•…‘}µ½¹Ñ¡ô¡½É¥é½¸ˆ°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É•Á½}É½½Ð¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}™¥±”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡Í½ÕÉ•}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰É…Ý}Õ¹¥ÑÌ‰t€ôÍ½ÕÉ•}Õ¹¥ÑÌ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‘…Ñ…}É…¹”‰t€ôÍÑ…ÑÌ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¹…Ñ¥Ù•}É¥‰t€ôÉ¥‘}É•Í½±ÕÑ¥½¸¡É¥¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì‰ÍÑ…ÑÕÌˆè€‰¹…Ñ¥Ù•}Í½ÕÉ•}…¹½µ…±äˆ°€‰±…‰•°ˆè€‰9…Ñ¥Ù”A55…¹½µ…±ä‰ô(€€€€€€€€€€€¥˜ÁÉ½‘ÕÑ}¹…µ”€ôô€‰ÁÉ•¥Á¥Ñ…Ñ¥½¹}…¹½µ…±äˆè(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰½¹Ù•ÉÍ¥½¸‰t€ôÁÉ½‘ÕÑl‰ÁÉ•¥Á¥Ñ…Ñ¥½¹}½¹Ù•ÉÍ¥½¸‰t(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰…É•…Ñ¥½¸‰t€ô€‰Í•…Í½¹…°…ÕµÕ±…Ñ¥½¸™É½´¹…Ñ¥Ù”AÍ•…Í½¹…°µ•…¸ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€€€€€ÍÕ•ÍÍ•Ì€¬ô€Ä(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰É•¹‘•É•AíÁÉ½‘ÕÑ}¹…µ•ôèí½ÕÑÁÕÑôˆ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰AíÁÉ½‘ÕÑ}¹…µ•ô™…¥±•èí•áôˆ¤(€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡ÉÕ¹}•¹ÑÉä¤(€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÑ}Á…Ñ °•¹ÑÉ¥•Ì°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}å±•Ì°…ÉÌ¹‘…Ñ…Í•Ð¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”Aµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑ}Á…Ñ¡ô€¡í±•¸¡•¹ÑÉ¥•Ì¥ôÁÉ½‘ÕÐÉÕ¸¡Ì¤¤ˆ¤(€€€É•ÑÕÉ¸€À¥˜ÍÕ•ÍÍ•Ì•±Í”€È(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤(€€€•á•ÁÐAÉÉ½È…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰AII=Hèí•áôˆ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(