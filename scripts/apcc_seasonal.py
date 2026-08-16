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
APCC_ACKNOWLEDGEMENT = (
    "APCC MME data collected and reproduced by APCC based on hindcast/forecast "
    "data produced by APCC MME Producing Centres."
)

APCC_Z500_TICKS = list(range(-100, 101, 10))
APCC_TEMP_TICKS = [value / 2 for value in range(-6, 7)]
APCC_TEMP_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#82bdca", "#b4d6dc", "#e7eeee",
    "#ffffff", "#f8dedd", "#efb6b5", "#e38e8e", "#d36c73", "#b84c5a",
]
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
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "z500a",
    },
    "850mb_temperature_anomaly": {
        "api_variable": "t850", "field": "t850_anomaly", "raw_field": "850-mb temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME 850-mb Temperature Anomaly (°C)",
        "absolute_title": "APCC MME 850-mb Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -3.0, "anomaly_max": 3.0,
        "anomaly_ticks": APCC_TEMP_TICKS, "anomaly_palette": APCC_TEMP_PALETTE,
        "anomaly_tick_decimals": 1,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "t850a",
    },
    "2m_temperature_anomaly": {
        "api_variable": "t2m", "field": "t2m_anomaly", "raw_field": "2-m temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME 2-m Temperature Anomaly (°C)",
        "absolute_title": "APCC MME 2-m Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -3.0, "anomaly_max": 3.0,
        "anomaly_ticks": APCC_TEMP_TICKS, "anomaly_palette": APCC_TEMP_PALETTE,
        "anomaly_tick_decimals": 1,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "t2ma",
    },
    "precipitation_anomaly": {
        "api_variable": "prec", "field": "precipitation_anomaly", "raw_field": "precipitation anomaly",
        "raw_units": "mm/day", "units": "mm", "title": "APCC MME Seasonal Precipitation Anomaly (mm)",
        "absolute_title": "APCC MME Precipitation (mm)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -200.0, "anomaly_max": 200.0,
        "anomaly_ticks": APCC_PRECIP_TICKS, "anomaly_palette": APCC_PRECIP_PALETTE,
        "precipitation_conversion": "seasonal mean mm/day × valid-season days = seasonal accumulation mm",
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "preca",
    },
    "sea_surface_temperature_anomaly": {
        "api_variable": "sst", "field": "sst_anomaly", "raw_field": "sea-surface temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME Sea-Surface Temperature Anomaly (°C)",
        "absolute_title": "APCC MME Sea-Surface Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -4.0, "anomaly_max": 4.0,
        "anomaly_ticks": APCC_SST_TICKS, "anomaly_palette": APCC_SST_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "ssta",
    },
    "mslp_anomaly": {
        "api_variable": "slp", "field": "mslp_anomaly", "raw_field": "mean sea-level pressure anomaly",
        "raw_units": "mb", "units": "hPa", "title": "APCC MME Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "APCC MME Mean Sea-Level Pressure (hPa)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -6.0, "anomaly_max": 6.0,
        "anomaly_ticks": APCC_PRESSURE_TICKS, "anomaly_palette": APCC_PRESSURE_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
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


def parse_init(value: str) -> str:
    if value == "latest":
        now = dt.datetime.now(dt.timezone.utc)
        return f"{now.year:04d}{now.month:02d}"
    if re.fullmatch(r"\d{6}", value):
        try:
            dt.datetime.strptime(value, "%Y%m")
        except ValueError as exc:
            raise APCCError(f"invalid APCC initialization month: {value}") from exc
        return value
    raise APCCError("--init must be latest or YYYYMM")


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
    if not values or values != list(range(min(values), max(values) + 1)):
        raise APCCError("--target-window must contain consecutive lead offsets")
    date = dt.datetime.strptime(init, "%Y%m")
    first = month_after(date.year, date.month, min(values))
    last = month_after(date.year, date.month, max(values))
    first_code = f"{first[0]:04d}{first[1]:02d}"
    last_code = f"{last[0]:04d}{last[1]:02d}"
    season = {(12, 2): "DJF", (3, 5): "MAM", (6, 8): "JJA", (9, 11): "SON"}.get((first[1], last[1]))
    if season and ((first[1] == 12 and last[0] == first[0] + 1) or last[0] == first[0]):
        label = f"{season} {last[0]}"
    else:
        first_date = dt.date(first[0], first[1], 1)
        last_date = dt.date(last[0], last[1], 1)
        label = f"{first_date:%b %Y}–{last_date:%b %Y}"
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
        print(f"APCC job {job_id}: {status}")
    if not download_url:
        raise APCCError(f"APCC job {job_id} completed without a download URL")
    temporary = output.with_name(output.name + ".tmp")
    try:
        with requests.get(download_url, stream=True, timeout=(30, 180)) as download:
            download.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        temporary.replace(output)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise APCCError(f"APCC result download failed: {exc}") from exc
    return output


def safe_extract(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(archive) as bundle:
        root = destin5׫h��춻�q�^t
                (requested_last_year, requested_last_month),
            )
        ),
        "forecast_info": "",
        "fallback": True,
    }
    if files:
        reference_path = find_product_file(files, PRODUCT_SPECS[selected[0]], "")
        reference_metadata = read_netcdf_metadata(reference_path, PRODUCT_SPECS[selected[0]])
        native_period = source_period_from_metadata(
            reference_metadata["global_attrs"], init, source_path=reference_path
        )
    target_code = native_period["target_code"]
    period = native_period["period_label"]
    first_target = native_period["first_target"]
    entries: list[dict[str, Any]] = []
    successes = 0
    init_utc = f"{init[:4]}-{init[4:]}-01T00:00:00Z"
    valid_start = native_period["valid_start_utc"]
    valid_end = native_period["valid_end_utc"]
    source_season_code = str(native_period.get("season_code", ""))
    for product_name in selected:
        product = PRODUCT_SPECS[product_name]
        target_entry: dict[str, Any] = {
            "id": f"apcc-{init}-{product['id_token']}-{target_code}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": args.lead_month,
            "target_month": target_code,
            "period_label": period,
            "aggregation": "native APCC seasonal mean",
            "field": product["field"],
            "units": product["units"],
            "raw_field": product["raw_field"],
            "raw_units": product["raw_units"],
            "statistic": "APCC multi-model ensemble mean",
            "ensemble_scope": "APCC MME seasonal product",
            "source_urls": [dataset_url(args.dataset), APCC_API_DOCS_URL],
            "source_period": native_period,
            "status": "planned",
        }
        run_entry: dict[str, Any] = {
            "id": f"apcc-{init}-{product_name}",
            "init_utc": init_utc,
            "product": product_name,
            "status": "planned",
            "source": "APCC multi-model ensemble via CLIK",
            "source_url": APCC_SOURCE_URL,
            "dataset": args.dataset,
            "lead_month": args.lead_month,
            "resolution": args.resolution,
            "method": args.method,
            "requested_target_window": args.target_window,
            "native_period": native_period,
            "targets": [target_entry],
            "output_dir": relative_path(output_dir, repo_root),
        }
        try:
            if args.decode_only:
                raise APCCError("--decode-only requires a cached APCC archive; provide the adapter cache first")
            source_path = find_product_file(files, product, source_season_code)
            metadata = read_netcdf_metadata(source_path, product)
            source_units = _source_units(metadata["data_attrs"])
            grid = grid_from_netcdf(source_path, product, precip_days=int(native_period["days"]))
            stats = grid_stats(grid)
            output = output_dir / init / f"apcc_{product['id_token']}_{target_code}.jpg"
            render_map(
                grid,
                f"{init}0100",
                first_target,
                args.lead_month,
                [],
                output,
                anomaly=True,
                baseline_label="Native APCC MME anomaly",
                border_paths=borders,
                period_label=period,
                ensemble_label="APCC multi-model ensemble mean",
                product_spec={
                    **product,
                    "name": product_name,
                    "source_label": "APCC MME / CLIK",
                },
            )
            target_entry["image"] = relative_path(output, repo_root)
            target_entry["source_file"] = relative_path(source_path, repo_root)
            target_entry["raw_units"] = source_units
            target_entry["data_range"] = stats
            target_entry["native_grid"] = grid_resolution(grid)
            target_entry["baseline"] = {"status": "native_source_anomaly", "label": "Native APCC MME anomaly"}
            if product_name == "precipitation_anomaly":
                target_entry["conversion"] = product["precipitation_conversion"]
                target_entry["aggregation"] = "seasonal accumulation from native APCC seasonal mean"
            target_entry["status"] = "rendered"
            run_entry["status"] = "rendered"
            successes += 1
            print(f"rendered APCC {product_name}: {output}")
        except Exception as exc:
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            run_entry["status"] = "failed"
            print(f"APCC {product_name} failed: {exc}")
        entries.append(run_entry)
    write_manifest(manifest_path, entries, previous, args.retain_cycles)
    print(f"wrote APCC manifest: {manifest_path} ({len(entries)} product run(s))")
    return 0 if successes else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except APCCError as exc:
        print(f"APCC ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
