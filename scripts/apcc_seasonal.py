#!/usr/bin/env python3
"""Fetch and render APCC multi-model seasonal mean anomaly products.

APCC's official CLIK API returns an authenticated ZIP archive.  This adapter
keeps that request boundary explicit, decodes the NetCDF fields inside the
archive, and renders the native APCC MME anomalies with the common North
America map renderer used by the other seasonal models.
"""

from __future__ import annotations

import argparse
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
    ANOMALY_TICKS,
    DEFAULT_REGION,
    Grid,
    ensure_border_files,
    relative_path,
    render_map,
)


APCC_SOURCE_URL = "https://apcc21.org/clik/processing/prediction"
APCC_DATASET_URL = "https://apcc21.org/clik/dataset/mme/3-MON?lang=en"
APCC_API_DOCS_URL = "https://apcc21.org/clik/clikapi?lang=en"
APCC_REQUEST_URL = "https://www.apcc21.org/clikapi/request/apccdata"
APCC_STATUS_URL = "https://www.apcc21.org/clikapi/request/status"
APCC_ACKNOWLEDGEMENT = (
    "APCC MME data collected and reproduced by APCC based on hindcast/forecast "
    "data produced by APCC MME Producing Centres."
)

TEMP_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#82bdca", "#b4d6dc", "#e7eeee",
    "#ffffff", "#f8dedd", "#efb6b5", "#e38e8e", "#d36c73", "#b84c5a",
]
PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f", "#86c879",
    "#5fba6b", "#3aa55b", "#1d8947", "#006d2c",
]
APCC_PRECIP_PALETTE = [
    "#7f3b08", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d", "#ead8b3",
    "#ffffff", "#e5f1dc", "#c8e4bf", "#86c879", "#3aa55b", "#006d2c",
]
SST_PALETTE = ["#28567f", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5", "#b84c5a"]


PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "api_variable": "z500", "field": "z500_anomaly", "raw_field": "geopotential height anomaly",
        "raw_units": "native APCC units", "units": "m", "title": "APCC MME 500-mb Geopotential Height Anomaly (m)",
        "absolute_title": "APCC MME 500-mb Geopotential Height (m)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -200.0, "anomaly_max": 200.0,
        "anomaly_ticks": ANOMALY_TICKS, "anomaly_palette": ANOMALY_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "z500a",
    },
    "850mb_temperature_anomaly": {
        "api_variable": "t850", "field": "t850_anomaly", "raw_field": "850-mb temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME 850-mb Temperature Anomaly (°C)",
        "absolute_title": "APCC MME 850-mb Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -6.0, "anomaly_max": 6.0,
        "anomaly_ticks": list(range(-6, 7)), "anomaly_palette": TEMP_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "t850a",
    },
    "2m_temperature_anomaly": {
        "api_variable": "t2m", "field": "t2m_anomaly", "raw_field": "2-m temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME 2-m Temperature Anomaly (°C)",
        "absolute_title": "APCC MME 2-m Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -6.0, "anomaly_max": 6.0,
        "anomaly_ticks": list(range(-6, 7)), "anomaly_palette": TEMP_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "t2ma",
    },
    "precipitation_anomaly": {
        "api_variable": "prec", "field": "precipitation_anomaly", "raw_field": "precipitation anomaly",
        "raw_units": "native APCC units", "units": "mm", "title": "APCC MME Precipitation Anomaly (mm)",
        "absolute_title": "APCC MME Precipitation (mm)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -300.0, "anomaly_max": 300.0,
        "anomaly_ticks": list(range(-300, 301, 50)), "anomaly_palette": APCC_PRECIP_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "preca",
    },
    "sea_surface_temperature_anomaly": {
        "api_variable": "sst", "field": "sst_anomaly", "raw_field": "sea-surface temperature anomaly",
        "raw_units": "K", "units": "°C", "title": "APCC MME Sea-Surface Temperature Anomaly (°C)",
        "absolute_title": "APCC MME Sea-Surface Temperature (°C)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -3.0, "anomaly_max": 3.0,
        "anomaly_ticks": list(range(-3, 4)), "anomaly_palette": SST_PALETTE,
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "ssta",
    },
    "mslp_anomaly": {
        "api_variable": "slp", "field": "mslp_anomaly", "raw_field": "mean sea-level pressure anomaly",
        "raw_units": "Pa", "units": "hPa", "title": "APCC MME Mean Sea-Level Pressure Anomaly (hPa)",
        "absolute_title": "APCC MME Mean Sea-Level Pressure (hPa)", "height_contours": False,
        "region": DEFAULT_REGION, "anomaly_min": -20.0, "anomaly_max": 20.0,
        "anomaly_ticks": list(range(-20, 21, 5)), "anomaly_palette": ANOMALY_PALETTE[:4] + ANOMALY_PALETTE[10:14],
        "header_detail": "{source_label}  •  {baseline_label}  •  Native APCC seasonal MME anomaly",
        "id_token": "slpa",
    },
}


class APCCError(RuntimeError):
    """A user-actionable APCC source or rendering error."""


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
        label = f"{first:%b %Y}–{last:%b %Y}"
    return f"{first_code}-{last_code}", label, first_code


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
        root = destination.resolve()
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise APCCError(f"refusing unsafe APCC archive member: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                output.write(source.read())
            extracted.append(target)
    return extracted


def _coordinate_name(data: Any, tokens: tuple[str, ...]) -> str:
    names = list(data.coords) + list(data.dims)
    for name in names:
        lower = str(name).lower()
        if any(token in lower for token in tokens):
            return str(name)
    raise APCCError(f"APCC NetCDF field has no coordinate matching {tokens}")


def _convert_values(values: np.ndarray, product: dict[str, Any], attrs: dict[str, Any]) -> np.ndarray:
    units = str(attrs.get("units", "")).lower().replace("^", "")
    if product["api_variable"] == "z500" and ("m2 s-2" in units or "m^2 s-2" in units or "m2/s2" in units):
        return values / 9.80665
    if product["api_variable"] == "slp" and ("pa" in units and "hpa" not in units):
        return values / 100.0
    if product["api_variable"] == "prec" and (units in {"m", "meter", "metre"} or "m water" in units):
        return values * 1000.0
    return values


def grid_from_netcdf(path: Path, product: dict[str, Any]) -> Grid:
    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover - workflow installs xarray/netCDF4
        raise APCCError("APCC NetCDF decoding requires xarray") from exc
    try:
        dataset = xr.open_dataset(path)
    except Exception as exc:
        raise APCCError(f"could not open APCC NetCDF {path.name}: {exc}") from exc
    try:
        candidates = list(dataset.data_vars)
        token = product["api_variable"].lower()
        exact = [name for name in candidates if str(name).lower() == token]
        data = dataset[exact[0] if exact else candidates[0]] if candidates else None
        if data is None:
            raise APCCError(f"APCC NetCDF {path.name} contains no data variable")
        data = data.squeeze(drop=True)
        lat_name = _coordinate_name(data, ("latitude", "lat"))
        lon_name = _coordinate_name(data, ("longitude", "lon"))
        for dimension in list(data.dims):
            if dimension not in {lat_name, lon_name}:
                data = data.mean(dim=dimension, skipna=True)
        data = data.transpose(lat_name, lon_name)
        lats = np.asarray(data[lat_name].values, dtype=float)
        lons = np.asarray(data[lon_name].values, dtype=float)
        values = _convert_values(np.asarray(data.values, dtype=float), product, dict(data.attrs))
    finally:
        dataset.close()
    if values.ndim != 2:
        raise APCCError(f"APCC NetCDF {path.name} did not reduce to a 2-D field")
    normalized_lons = ((lons + 180.0) % 360.0) - 180.0
    lon_order = np.argsort(normalized_lons)
    lat_order = np.argsort(lats)
    ordered = values[np.ix_(lat_order, lon_order)]
    if not np.isfinite(ordered).any():
        raise APCCError(f"APCC NetCDF {path.name} contains no finite values")
    return Grid(
        lons=[float(value) for value in normalized_lons[lon_order]],
        lats=[float(value) for value in lats[lat_order]],
        values=ordered.tolist(),
    )


def find_product_file(files: Iterable[Path], product: dict[str, Any], season_code: str) -> Path:
    candidates = [path for path in files if path.suffix.lower() in {".nc", ".nc4", ".netcdf"}]
    if not candidates:
        raise APCCError("APCC result ZIP contained no NetCDF files")
    token = product["api_variable"].lower()
    matching = [path for path in candidates if token in path.name.lower()]
    if not matching:
        matching = candidates
    seasonal = [path for path in matching if season_code.lower() in str(path).lower()]
    return sorted(seasonal or matching)[0]


def write_manifest(path: Path, entries: Iterable[dict[str, Any]], previous: Path | None, retain_cycles: int) -> None:
    all_entries: list[dict[str, Any]] = []
    for existing_path in (previous, path):
        if not existing_path or not existing_path.exists():
            continue
        try:
            payload = json.loads(existing_path.read_text(encoding="utf-8"))
            all_entries.extend(run for run in payload.get("runs", []) if isinstance(run, dict))
        except (OSError, ValueError) as exc:
            raise APCCError(f"could not read previous APCC manifest {existing_path}: {exc}") from exc
    all_entries.extend(entries)
    unique = {str(run.get("id")): run for run in all_entries if run.get("id")}
    ordered = sorted(unique.values(), key=lambda item: (str(item.get("init_utc", "")), str(item.get("id", ""))), reverse=True)
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:max(1, retain_cycles)])
    payload = {
        "schema_version": 1,
        "kind": "apcc_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "APCC multi-model ensemble via CLIK",
        "source_url": APCC_SOURCE_URL,
        "source_urls": [APCC_SOURCE_URL, APCC_DATASET_URL, APCC_API_DOCS_URL],
        "acknowledgement": APCC_ACKNOWLEDGEMENT,
        "product_labels": {
            "500mb_height_anomaly": "500-mb Height Anomaly",
            "850mb_temperature_anomaly": "850-mb Temperature Anomaly",
            "2m_temperature_anomaly": "2-m Temperature Anomaly",
            "precipitation_anomaly": "Precipitation Anomaly",
            "sea_surface_temperature_anomaly": "Sea-Surface Temperature Anomaly",
            "mslp_anomaly": "MSLP Anomaly",
        },
        "retention": {"max_cycles": max(1, retain_cycles), "history_cycles": max(0, retain_cycles - 1)},
        "runs": [run for run in ordered if str(run.get("init_utc", "")) in keep],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("all", *PRODUCT_SPECS), default="all")
    parser.add_argument("--init", default="latest", help="APCC initialization as YYYYMM or latest")
    parser.add_argument("--target-window", default="4,5,6", help="viewer lead offsets used to label the native 3-month APCC period")
    parser.add_argument("--dataset", default="MME_3MONTH", choices=("MME_3MONTH", "MME_6MONTH"))
    parser.add_argument("--lead-month", default="3-MON", choices=("3-MON", "6-MON"))
    parser.add_argument("--resolution", default="1.0", choices=("1.0", "2.5"))
    parser.add_argument("--method", default="SCM", choices=("SCM", "GAUS"))
    parser.add_argument("--request-url", default=APCC_REQUEST_URL)
    parser.add_argument("--status-url", default=APCC_STATUS_URL)
    parser.add_argument("--timeout-minutes", type=int, default=45)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--cache-dir", default=".cache/apcc")
    parser.add_argument("--output-dir", default="public/seasonal/apcc")
    parser.add_argument("--manifest", default="public/seasonal/apcc_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    init = parse_init(args.init)
    target_code, period, first_target = target_window(init, args.target_window)
    selected = list(PRODUCT_SPECS) if args.product == "all" else [args.product]
    cache_dir = Path(args.cache_dir) if Path(args.cache_dir).is_absolute() else repo_root / args.cache_dir
    output_dir = Path(args.output_dir) if Path(args.output_dir).is_absolute() else repo_root / args.output_dir
    manifest_path = Path(args.manifest) if Path(args.manifest).is_absolute() else repo_root / args.manifest
    previous = args.previous_manifest if not args.previous_manifest or args.previous_manifest.is_absolute() else repo_root / args.previous_manifest
    borders = ensure_border_files(args, cache_dir, repo_root)
    variables = sorted({PRODUCT_SPECS[product]["api_variable"] for product in selected})
    season_code = period.split()[0]
    archive = cache_dir / "archives" / f"{args.dataset.lower()}_{args.lead_month.lower()}_{init}_{'_'.join(variables)}.zip"
    files: list[Path] = []
    if not args.decode_only:
        request_archive(_request_details(args, init, variables), archive, args)
        files = safe_extract(archive, cache_dir / "extracted" / archive.stem)
    entries: list[dict[str, Any]] = []
    successes = 0
    init_utc = f"{init[:4]}-{init[4:]}-01T00:00:00Z"
    valid_start = f"{target_code[:4]}-{target_code[4:6]}-01T00:00:00Z"
    end_code = target_code[7:]
    end_date = dt.datetime.strptime(end_code, "%Y%m")
    end_next = month_after(end_date.year, end_date.month, 1)
    valid_end = f"{end_next[0]:04d}-{end_next[1]:02d}-01T00:00:00Z"
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
            "source_urls": [APCC_DATASET_URL, APCC_API_DOCS_URL],
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
            "targets": [target_entry],
            "output_dir": relative_path(output_dir, repo_root),
        }
        try:
            if args.decode_only:
                raise APCCError("--decode-only requires a cached APCC archive; provide the adapter cache first")
            source_path = find_product_file(files, product, season_code)
            grid = grid_from_netcdf(source_path, product)
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
            target_entry["baseline"] = {"status": "native_source_anomaly", "label": "Native APCC MME anomaly"}
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
