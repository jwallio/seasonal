#!/usr/bin/env python3
"""Fetch and render CMA CPSv3 seasonal anomaly guidance.

The WMO Lead Centre for Seasonal Prediction Multi-Model Ensemble redistributes
the GPC Beijing contribution as one compact NetCDF bundle per issue.  The
bundle contains provider-calculated monthly anomalies for forecast months 1-3
on the WMO 2.5-degree global grid.  This adapter preserves that source horizon
and native model climatology instead of manufacturing unavailable longer leads.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import ssl
import sys
import time
from typing import Any, Iterable
import zipfile

import numpy as np

import c3s_seasonal as c3s
import cfsv2_seasonal as cfsv2
from cfsv2_seasonal import Grid, ensure_border_files, mean_grids, relative_path, render_map, sum_grids


WMOLC_ROOT = "https://www.wmolc.org"
WMOLC_DIRECT_URL = f"{WMOLC_ROOT}/seasonDownload/direct"
WMOLC_DOWNLOAD_URL = f"{WMOLC_ROOT}/seasonDownload/directDownload"
WMOLC_BEIJING_INFO_URL = f"{WMOLC_ROOT}/contents2/index/Beijing"
WMOLC_POLICY_URL = f"{WMOLC_ROOT}/contents/index/Data%2BExchange%2BPolicy"
WMOLC_MODEL_DIRECTORY = "Beijing"
WMOLC_INTERMEDIATE_CA = Path(__file__).resolve().parent / "certs" / "RapidSSLTLSRSACAG1.crt.pem"
WMOLC_INTERMEDIATE_CA_URL = "https://cacerts.digicert.com/RapidSSLTLSRSACAG1.crt.pem"
WMOLC_INTERMEDIATE_CA_SHA256 = "4422e963ee53cd58cc9f85cd40bf5ffec0095fdf1a154535661c1c06bcadc69b"
CMA_MODEL = "CMA CPSv3"
CMA_ENSEMBLE_MEMBERS = 21
SUPPORTED_LEADS = frozenset({1, 2, 3})
EXPECTED_LATITUDES = 73
EXPECTED_LONGITUDES = 144
SOURCE_UNIT_ALIASES = {
    "h500": frozenset({"gpm"}),
    "t850": frozenset({"k"}),
    "t02m": frozenset({"k"}),
    # WMO policy defines a rate; the converted Beijing file currently omits
    # the per-second suffix in its units attribute even though its magnitude
    # and policy contract are rate-valued.
    "prec": frozenset({"kg/m^2", "kg m-2 s-1", "kg m^-2 s^-1"}),
    "sst": frozenset({"k"}),
    "mslp": frozenset({"pa"}),
}
PRODUCT_LABELS = {
    "500mb_height_anomaly": "500-mb Height Anomaly",
    "850mb_temperature_anomaly": "850-mb Temperature Anomaly",
    "2m_temperature_anomaly": "2-m Temperature Anomaly",
    "precipitation_anomaly": "CONUS Precipitation Anomaly",
    "sea_surface_temperature_anomaly": "Sea-Surface Temperature Anomaly",
    "mslp_anomaly": "MSLP Anomaly",
}


def _product_spec(
    key: str,
    source_variable: str,
    raw_field: str,
    raw_units: str,
    title: str,
    conversion: str,
) -> dict[str, Any]:
    spec = dict(c3s.PRODUCT_SPECS[key])
    spec.update(
        {
            "source_variable": source_variable,
            "raw_field": raw_field,
            "raw_units": raw_units,
            "title": title,
            "absolute_title": title,
            "height_contours": False,
            "conversion": conversion,
            "source_label": "WMO LC-SPMME / GPC Beijing",
            "header_detail": (
                "{source_label}  •  CMA CPSv3 native anomaly  •  "
                + ("Ocean only" if spec.get("map_domain") == "ocean" else f"{spec['units']} anomaly")
            ),
        }
    )
    return spec


PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": _product_spec(
        "500mb_height_anomaly",
        "h500",
        "500-hPa geopotential-height anomaly",
        "gpm",
        "CMA CPSv3 500-mb Geopotential Height Anomaly (m)",
        "1 gpm anomaly is treated as 1 m geopotential-height anomaly",
    ),
    "850mb_temperature_anomaly": _product_spec(
        "850mb_temperature_anomaly",
        "t850",
        "850-hPa air-temperature anomaly",
        "K",
        "CMA CPSv3 850-mb Temperature Anomaly (°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "2m_temperature_anomaly": _product_spec(
        "2m_temperature_anomaly",
        "t02m",
        "2-m air-temperature anomaly",
        "K",
        "CMA CPSv3 2-m Temperature Anomaly (°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "precipitation_anomaly": _product_spec(
        "precipitation_anomaly",
        "prec",
        "total-precipitation-rate anomaly",
        "kg m-2 s-1",
        "CMA CPSv3 CONUS Precipitation Anomaly (in)",
        "kg m-2 s-1 × calendar-month seconds ÷ 25.4 = monthly inches",
    ),
    "sea_surface_temperature_anomaly": _product_spec(
        "sea_surface_temperature_anomaly",
        "sst",
        "sea-surface-temperature anomaly",
        "K",
        "CMA CPSv3 Sea-Surface Temperature Anomaly (°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "mslp_anomaly": _product_spec(
        "mslp_anomaly",
        "mslp",
        "mean-sea-level-pressure anomaly",
        "Pa",
        "CMA CPSv3 Mean Sea-Level Pressure Anomaly (hPa)",
        "Pa ÷ 100 = hPa",
    ),
}


class CMACPSv3Error(RuntimeError):
    """A user-actionable CMA CPSv3 source or rendering error."""


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_init(value: str) -> str:
    if value == "latest":
        return value
    match = re.fullmatch(r"(\d{6})(?:01(?:00)?)?", value)
    if not match:
        raise CMACPSv3Error("--init must be latest, YYYYMM, YYYYMM01, or YYYYMM0100")
    try:
        dt.datetime.strptime(match.group(1), "%Y%m")
    except ValueError as exc:
        raise CMACPSv3Error(f"invalid CMA CPSv3 issue month: {value}") from exc
    return match.group(1)


def init_code(issue: str) -> str:
    return f"{issue}0100"


def bundle_name(issue: str) -> str:
    init = init_code(issue)
    first = c3s.target_month(init, min(SUPPORTED_LEADS))
    last = c3s.target_month(init, max(SUPPORTED_LEADS))
    return f"beijing_{issue}_{first}_{last}.nc"


def issue_directory(issue: str) -> str:
    return f"/forecast/{WMOLC_MODEL_DIRECTORY}/{issue[:4]}/{issue[4:6]}"


def listing_entries(html: str) -> dict[str, str]:
    """Return filename -> exact WMO download token from a direct-listing page."""

    entries: dict[str, str] = {}
    pattern = re.compile(r'name=["\']fnames\[\]["\'][^>]*value=["\']([^"\']+)["\']', re.IGNORECASE)
    for match in pattern.finditer(html):
        token = unescape(match.group(1)).strip()
        filename = token.split("|", 1)[0]
        if filename:
            entries[filename] = token
    return entries


def wmolc_ssl_context() -> ssl.SSLContext:
    try:
        pem = WMOLC_INTERMEDIATE_CA.read_text(encoding="ascii")
        certificate = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, ValueError) as exc:
        raise CMACPSv3Error(f"could not load the pinned WMO TLS intermediate: {exc}") from exc
    fingerprint = hashlib.sha256(certificate).hexdigest()
    if fingerprint != WMOLC_INTERMEDIATE_CA_SHA256:
        raise CMACPSv3Error("the bundled WMO TLS intermediate failed its DigiCert SHA-256 fingerprint check")

    # Keep the platform's trusted roots and add the missing intermediate as a
    # chain-building certificate. Pointing Requests at the intermediate alone
    # discards the root store and fails on OpenSSL-based GitHub runners.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(WMOLC_INTERMEDIATE_CA))
    return context


def requests_session() -> Any:
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as exc:  # pragma: no cover - workflow installs requests
        raise CMACPSv3Error("CMA CPSv3 downloads require requests") from exc

    context = wmolc_ssl_context()

    class WMOLCTLSAdapter(HTTPAdapter):
        def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
            pool_kwargs["ssl_context"] = context
            super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    retries = Retry(
        total=4,
        connect=4,
        read=4,
        other=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "wall.cloud WN2 CMA-CPSv3 seasonal adapter"})
    # WMO currently serves only its leaf certificate. Supplying the official
    # DigiCert intermediate alongside the normal trusted roots restores a
    # verifiable chain without disabling hostname or certificate validation.
    session.mount(f"{WMOLC_ROOT}/", WMOLCTLSAdapter(max_retries=retries))
    return session


def list_issue(session: Any, issue: str) -> dict[str, str]:
    try:
        session.get(WMOLC_DIRECT_URL, timeout=(20, 60)).raise_for_status()
        response = session.post(
            WMOLC_DIRECT_URL,
            data={"curdir": issue_directory(issue), "parentdir": f"/forecast/{WMOLC_MODEL_DIRECTORY}/{issue[:4]}"},
            timeout=(20, 60),
        )
        response.raise_for_status()
    except Exception as exc:
        raise CMACPSv3Error(f"WMO GPC Beijing directory could not be read for {issue}: {exc}") from exc
    return listing_entries(response.text)


def month_offset(value: dt.datetime, offset: int) -> str:
    year, month = c3s.month_after(value.year, value.month, offset)
    return f"{year:04d}{month:02d}"


def discover_latest_issue(
    *,
    now: dt.datetime | None = None,
    lookback_months: int = 6,
    session: Any | None = None,
) -> tuple[str, str]:
    current = now or dt.datetime.now(dt.timezone.utc)
    client = session or requests_session()
    errors: list[str] = []
    for offset in range(0, -lookback_months, -1):
        issue = month_offset(current, offset)
        try:
            entries = list_issue(client, issue)
        except CMACPSv3Error as exc:
            errors.append(str(exc))
            continue
        expected = bundle_name(issue)
        if expected in entries:
            return issue, entries[expected]
    detail = f" Last source error: {errors[-1]}" if errors else ""
    raise CMACPSv3Error(
        f"no CMA CPSv3 GPC Beijing NetCDF package was found in the latest {lookback_months} WMO issue months.{detail}"
    )


def safe_extract_bundle(archive: Path, destination: Path, expected_name: str) -> Path:
    if not zipfile.is_zipfile(archive):
        raise CMACPSv3Error("WMO download did not return the expected ZIP archive")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / expected_name
    temporary = output.with_name(output.name + ".tmp")
    with zipfile.ZipFile(archive) as bundle:
        matches = [member for member in bundle.infolist() if Path(member.filename).name == expected_name]
        if len(matches) != 1:
            raise CMACPSv3Error(f"WMO archive did not contain exactly one {expected_name} member")
        member = matches[0]
        if member.is_dir() or ".." in Path(member.filename).parts:
            raise CMACPSv3Error(f"refusing unsafe WMO archive member: {member.filename}")
        temporary.unlink(missing_ok=True)
        with bundle.open(member) as source, temporary.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
    if temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        raise CMACPSv3Error("extracted WMO NetCDF package is empty")
    temporary.replace(output)
    return output


def download_bundle(
    cache_dir: Path,
    issue: str,
    *,
    session: Any | None = None,
    token: str = "",
    force: bool = False,
) -> tuple[Path, str]:
    expected = bundle_name(issue)
    issue_cache = cache_dir / "wmolc" / issue
    netcdf = issue_cache / expected
    if netcdf.exists() and netcdf.stat().st_size > 0 and not force:
        return netcdf, token or expected

    client = session or requests_session()
    entries = list_issue(client, issue) if not token else {}
    download_token = token or entries.get(expected, "")
    if not download_token:
        raise CMACPSv3Error(f"WMO issue {issue} does not contain {expected}")

    issue_cache.mkdir(parents=True, exist_ok=True)
    archive = issue_cache / f"{expected}.zip"
    temporary = archive.with_name(archive.name + ".tmp")
    try:
        with client.post(
            WMOLC_DOWNLOAD_URL,
            data={"selectDir": issue_directory(issue), "fnames[]": download_token},
            headers={"Origin": WMOLC_ROOT, "Referer": WMOLC_DIRECT_URL},
            stream=True,
            timeout=(30, 180),
        ) as response:
            response.raise_for_status()
            temporary.unlink(missing_ok=True)
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if not zipfile.is_zipfile(temporary):
            temporary.unlink(missing_ok=True)
            raise CMACPSv3Error("WMO download did not return the expected ZIP archive")
        temporary.replace(archive)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise CMACPSv3Error(f"WMO CMA CPSv3 package download failed: {exc}") from exc
    return safe_extract_bundle(archive, issue_cache, expected), download_token


def selected_products(value: str) -> list[str]:
    names = list(PRODUCT_SPECS) if value.strip().lower() == "all" else [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in PRODUCT_SPECS]
    if unknown:
        raise CMACPSv3Error(f"unsupported CMA CPSv3 product(s): {', '.join(unknown)}")
    if not names:
        raise CMACPSv3Error("--product cannot be empty")
    return list(dict.fromkeys(names))


def parse_leads(value: str, label: str) -> list[int]:
    leads = c3s.parse_int_list(value, label, min(SUPPORTED_LEADS), max(SUPPORTED_LEADS))
    unsupported = [lead for lead in leads if lead not in SUPPORTED_LEADS]
    if unsupported:
        raise CMACPSv3Error(
            f"WMO redistributes CMA CPSv3 forecast months 1-3 only; unsupported {label}: {unsupported}"
        )
    return leads


def convert_values(values: np.ndarray, product: str, target: str) -> np.ndarray:
    converted = np.asarray(values, dtype=float)
    if product == "mslp_anomaly":
        converted = converted * 0.01
    elif product == "precipitation_anomaly":
        converted = converted * c3s.month_seconds(target) / 25.4
    return converted


def decode_product_bundle(
    path: Path,
    product: str,
    issue: str,
    leads: Iterable[int],
) -> tuple[dict[int, Grid], dict[str, Any], dict[str, Any]]:
    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover - workflow installs xarray/netCDF4
        raise CMACPSv3Error("CMA CPSv3 NetCDF decoding requires xarray and netCDF4") from exc

    spec = PRODUCT_SPECS[product]
    try:
        dataset = xr.open_dataset(path, engine="netcdf4", decode_times=False, mask_and_scale=True)
    except Exception as exc:
        raise CMACPSv3Error(f"could not open WMO CMA CPSv3 NetCDF package {path.name}: {exc}") from exc
    with dataset:
        variable_name = spec["source_variable"]
        if variable_name not in dataset:
            raise CMACPSv3Error(f"WMO package {path.name} is missing {variable_name}")
        variable = dataset[variable_name]
        required = ("forecast_time0", "g0_lat_1", "g0_lon_2")
        if tuple(variable.dims) != required:
            raise CMACPSv3Error(f"WMO variable {variable_name} has unexpected dimensions {variable.dims}")
        source_leads = np.asarray(dataset["forecast_time0"].values, dtype=int).reshape(-1)
        lats = np.asarray(dataset["g0_lat_1"].values, dtype=float).reshape(-1)
        lons = np.asarray(dataset["g0_lon_2"].values, dtype=float).reshape(-1)
        if source_leads.size != len(SUPPORTED_LEADS) or set(source_leads.tolist()) != set(SUPPORTED_LEADS):
            raise CMACPSv3Error(f"WMO package {path.name} has unexpected forecast months {source_leads.tolist()}")
        if lats.size != EXPECTED_LATITUDES or lons.size != EXPECTED_LONGITUDES:
            raise CMACPSv3Error(
                f"WMO package {path.name} has unexpected grid {lons.size} × {lats.size}; "
                f"expected {EXPECTED_LONGITUDES} × {EXPECTED_LATITUDES}"
            )
        if not np.all(np.isfinite(lats)) or not np.all(np.isfinite(lons)):
            raise CMACPSv3Error(f"WMO package {path.name} contains non-finite coordinates")
        normalized_lons = ((lons + 180.0) % 360.0) - 180.0
        if (
            len(np.unique(lats)) != lats.size
            or len(np.unique(normalized_lons)) != normalized_lons.size
            or not np.allclose(np.diff(np.sort(lats)), 2.5)
            or not np.allclose(np.diff(np.sort(normalized_lons)), 2.5)
        ):
            raise CMACPSv3Error(f"WMO package {path.name} is not on the declared regular 2.5-degree grid")
        declared_units = str(variable.attrs.get("units", "")).strip().lower()
        if declared_units not in SOURCE_UNIT_ALIASES[variable_name]:
            raise CMACPSv3Error(
                f"WMO variable {variable_name} declares unsupported units {variable.attrs.get('units', '')!r}"
            )
        initial_time = str(variable.attrs.get("initial_time", ""))
        initial_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", initial_time)
        if not initial_match or f"{initial_match.group(3)}{initial_match.group(2)}" != issue:
            raise CMACPSv3Error(
                f"WMO variable {variable_name} initialization {initial_time!r} does not match issue {issue}"
            )
        lon_order = np.argsort(normalized_lons)
        lat_order = np.argsort(lats)
        grids: dict[int, Grid] = {}
        for lead in leads:
            matches = np.flatnonzero(source_leads == lead)
            if len(matches) != 1:
                raise CMACPSv3Error(f"WMO package {path.name} has no unique forecast month {lead}")
            target = c3s.target_month(init_code(issue), lead)
            data = variable.isel(forecast_time0=int(matches[0])).transpose("g0_lat_1", "g0_lon_2")
            values = convert_values(np.asarray(data.values, dtype=float), product, target)
            values = values[np.ix_(lat_order, lon_order)]
            if not np.any(np.isfinite(values)):
                raise CMACPSv3Error(f"WMO variable {variable_name} forecast month {lead} contains no finite values")
            grids[lead] = Grid(
                normalized_lons[lon_order].astype(float).tolist(),
                lats[lat_order].astype(float).tolist(),
                values.tolist(),
            )
        attrs = {str(key): str(value) for key, value in dataset.attrs.items()}
        variable_attrs = {str(key): str(value) for key, value in variable.attrs.items()}
        if attrs.get("title", "").strip().lower() != "anomaly":
            raise CMACPSv3Error(f"WMO package {path.name} is not labelled as an anomaly package")
        if "beijing" not in attrs.get("model", "").strip().lower():
            raise CMACPSv3Error(f"WMO package {path.name} is not labelled as the GPC Beijing model")
        try:
            hindcast_start = int(attrs["hindcast_start_year"])
            hindcast_end = int(attrs["hindcast_end_year"])
        except (KeyError, ValueError) as exc:
            raise CMACPSv3Error(f"WMO package {path.name} has invalid hindcast-year metadata") from exc
        if hindcast_start > hindcast_end:
            raise CMACPSv3Error(f"WMO package {path.name} has reversed hindcast years")
    return grids, attrs, variable_attrs


def baseline_label(attrs: dict[str, Any]) -> str:
    start = str(attrs.get("hindcast_start_year", "")).strip()
    end = str(attrs.get("hindcast_end_year", "")).strip()
    return f"CMA CPSv3 {start}-{end} hindcast climatology" if start and end else "CMA CPSv3 native hindcast climatology"


def render_product_spec(product: str, *, seasonal: bool) -> dict[str, Any]:
    spec = dict(PRODUCT_SPECS[product])
    if product == "precipitation_anomaly" and not seasonal:
        spec.update(
            {
                "anomaly_min": cfsv2.PRECIP_MONTHLY_ANOMALY_MIN_IN,
                "anomaly_max": cfsv2.PRECIP_MONTHLY_ANOMALY_MAX_IN,
                "anomaly_ticks": cfsv2.PRECIP_MONTHLY_ANOMALY_TICKS,
                "anomaly_palette": cfsv2.PRECIP_ANOMALY_PALETTE,
            }
        )
    return spec


def render_target(
    grid: Grid,
    product: str,
    init: str,
    target: str,
    lead: int | str,
    output: Path,
    borders: list[Path],
    baseline: str,
    *,
    period: str = "",
) -> None:
    render_map(
        grid,
        init,
        target,
        lead,
        list(range(CMA_ENSEMBLE_MEMBERS)),
        output,
        anomaly=True,
        baseline_label=baseline,
        border_paths=borders,
        period_label=period,
        ensemble_label=f"{CMA_ENSEMBLE_MEMBERS}-member ensemble mean",
        product_spec=render_product_spec(product, seasonal=bool(period)),
    )


def build_run(
    *,
    product: str,
    issue: str,
    leads: list[int],
    seasonal_leads: list[int],
    source_path: Path,
    source_token: str,
    grids: dict[int, Grid],
    attrs: dict[str, Any],
    variable_attrs: dict[str, Any],
    output_dir: Path,
    borders: list[Path],
    root: Path,
    decode_only: bool,
) -> tuple[dict[str, Any], int]:
    init = init_code(issue)
    spec = PRODUCT_SPECS[product]
    climate_label = baseline_label(attrs)
    run_id = f"cma-cpsv3-{init}-{product}"
    run: dict[str, Any] = {
        "id": run_id,
        "model": CMA_MODEL,
        "component": "cma_cpsv3",
        "component_label": CMA_MODEL,
        "source": "WMO LC-SPMME / GPC Beijing",
        "source_url": WMOLC_BEIJING_INFO_URL,
        "source_urls": [WMOLC_BEIJING_INFO_URL, WMOLC_DIRECT_URL, WMOLC_POLICY_URL],
        "archive_root": WMOLC_DIRECT_URL,
        "archive_directory": issue_directory(issue),
        "archive_file": bundle_name(issue),
        "archive_token": source_token,
        "product": product,
        "variable": spec["variable"],
        "source_variable": spec["source_variable"],
        "init_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
        "statistic": "provider ensemble-mean anomaly",
        "ensemble_scope": f"CMA CPSv3 {CMA_ENSEMBLE_MEMBERS}-member forecast ensemble",
        "ensemble_members": CMA_ENSEMBLE_MEMBERS,
        "aggregation": "WMO monthly anomaly",
        "field": spec["field"],
        "units": spec["units"],
        "raw_field": spec["raw_field"],
        "raw_units": spec["raw_units"],
        "source_declared_units": variable_attrs.get("units", ""),
        "conversion": spec["conversion"],
        "source_grid": "2.5° global (144 × 73)",
        "source_forecast_months": sorted(SUPPORTED_LEADS),
        "baseline": {
            "status": "provider_anomaly",
            "method": "provider forecast anomaly relative to the CMA CPSv3 hindcast climatology",
            "years": f"{attrs.get('hindcast_start_year', '')}-{attrs.get('hindcast_end_year', '')}".strip("-"),
            "label": climate_label,
        },
        "source_metadata": attrs,
        "targets": [],
        "status": "planned",
    }
    failures = 0
    for lead in leads:
        target = c3s.target_month(init, lead)
        target_entry: dict[str, Any] = {
            "id": f"{run_id}-lead{lead:02d}",
            "target_month": target,
            "valid_start_utc": c3s.target_period(target)[0],
            "valid_end_utc": c3s.target_period(target)[1],
            "lead_month": lead,
            "field": spec["field"],
            "units": spec["units"],
            "statistic": run["statistic"],
            "source_file": relative_path(source_path, root),
            "status": "planned",
        }
        try:
            if lead not in grids:
                raise CMACPSv3Error(f"forecast month {lead} was not decoded")
            if decode_only:
                target_entry["status"] = "decoded"
            else:
                output = output_dir / init[:8] / f"cma_cpsv3_{spec['variable']}_{target}.jpg"
                render_target(grids[lead], product, init, target, lead, output, borders, climate_label)
                target_entry["image"] = relative_path(output, root)
                target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CMA CPSv3 {product} target {target} failed: {exc}", file=sys.stderr)
        run["targets"].append(target_entry)

    if seasonal_leads:
        first, last = seasonal_leads[0], seasonal_leads[-1]
        first_target, last_target = c3s.target_month(init, first), c3s.target_month(init, last)
        target_entry = {
            "id": f"{run_id}-{first_target}-{last_target}",
            "target_month": f"{first_target}-{last_target}",
            "valid_start_utc": c3s.target_period(first_target)[0],
            "valid_end_utc": c3s.target_period(last_target)[1],
            "lead_month": f"{first}–{last}",
            "monthly_leads": seasonal_leads,
            "field": spec["field"],
            "units": spec["seasonal_units"],
            "statistic": run["statistic"],
            "aggregation": (
                f"{len(seasonal_leads)}-month accumulated anomaly"
                if spec["seasonal_reducer"] == "sum"
                else f"{len(seasonal_leads)}-month mean anomaly"
            ),
            "source_file": relative_path(source_path, root),
            "status": "planned",
        }
        try:
            if any(lead not in grids for lead in seasonal_leads):
                raise CMACPSv3Error("seasonal window is missing one or more WMO forecast months")
            reducer = sum_grids if spec["seasonal_reducer"] == "sum" else mean_grids
            seasonal_grid = reducer([grids[lead] for lead in seasonal_leads])
            if decode_only:
                target_entry["status"] = "decoded"
            else:
                output = output_dir / init[:8] / f"cma_cpsv3_{spec['variable']}_{first_target}-{last_target}.jpg"
                render_target(
                    seasonal_grid,
                    product,
                    init,
                    first_target,
                    f"{first}–{last}",
                    output,
                    borders,
                    climate_label,
                    period=c3s.period_label(first_target, last_target),
                )
                target_entry["image"] = relative_path(output, root)
                target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CMA CPSv3 {product} seasonal window failed: {exc}", file=sys.stderr)
        run["targets"].append(target_entry)

    statuses = [str(target.get("status", "")) for target in run["targets"]]
    usable = any(status in {"decoded", "rendered"} for status in statuses)
    run["status"] = "failed" if not usable else ("partial" if failures else ("decoded" if decode_only else "rendered"))
    run["output_dir"] = relative_path(output_dir, root)
    return run, failures


def write_manifest(
    path: Path,
    entries: Iterable[dict[str, Any]],
    previous: Path | None,
    retain_cycles: int,
) -> None:
    if retain_cycles < 1:
        raise CMACPSv3Error("manifest retention must keep at least one cycle")
    all_entries: list[dict[str, Any]] = []
    for candidate in (previous, path):
        if not candidate or not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            all_entries.extend(run for run in payload.get("runs", []) if isinstance(run, dict))
        except (OSError, ValueError) as exc:
            raise CMACPSv3Error(f"could not read prior CMA CPSv3 manifest {candidate}: {exc}") from exc
    all_entries.extend(entries)
    unique = {str(run["id"]): run for run in all_entries if run.get("id")}
    ordered = sorted(unique.values(), key=lambda run: (str(run.get("init_utc", "")), str(run.get("id", ""))), reverse=True)
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle and cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:retain_cycles])
    payload = {
        "schema_version": 1,
        "kind": "cma_cpsv3_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "WMO LC-SPMME / GPC Beijing CMA CPSv3",
        "source_url": WMOLC_BEIJING_INFO_URL,
        "source_urls": [WMOLC_BEIJING_INFO_URL, WMOLC_DIRECT_URL, WMOLC_POLICY_URL],
        "product_labels": PRODUCT_LABELS,
        "source_horizon": {
            "system_length_months": 7,
            "redistributed_forecast_months": sorted(SUPPORTED_LEADS),
            "policy": "Only the three WMO-redistributed forecast months are rendered.",
        },
        "retention": {"max_cycles": retain_cycles, "history_cycles": max(0, retain_cycles - 1)},
        "runs": [run for run in ordered if str(run.get("init_utc", "")) in keep],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="all", help="one product, a comma-separated list, or all")
    parser.add_argument("--init", default="latest", help="WMO issue month as YYYYMM or latest")
    parser.add_argument("--lead-months", default="1,2,3")
    parser.add_argument("--seasonal-window", default="1,2,3")
    parser.add_argument("--source-file", type=Path, help="already-downloaded WMO GPC Beijing NetCDF bundle")
    parser.add_argument("--cache-dir", default=".cache/cma-cpsv3")
    parser.add_argument("--output-dir", default="public/seasonal/cma_cpsv3")
    parser.add_argument("--manifest", default="public/seasonal/cma_cpsv3_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--lookback-months", type=int, default=6)
    parser.add_argument("--request-delay", type=float, default=0.5)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    return parser


def resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    products = selected_products(args.product)
    leads = parse_leads(args.lead_months, "lead months")
    seasonal = parse_leads(args.seasonal_window, "seasonal window") if args.seasonal_window else []
    if seasonal:
        if seasonal != list(range(min(seasonal), max(seasonal) + 1)):
            raise CMACPSv3Error("--seasonal-window must contain consecutive WMO forecast months")
        leads = sorted(set(leads).union(seasonal))
    if args.lookback_months < 1 or args.lookback_months > 24:
        raise CMACPSv3Error("--lookback-months must be between 1 and 24")
    if args.request_delay < 0:
        raise CMACPSv3Error("--request-delay cannot be negative")

    parsed_init = parse_init(args.init)
    source_token = ""
    session: Any | None = None
    if args.source_file:
        source_token = "local source file"
        source_path = resolve_path(args.source_file, root)
        if not source_path.exists() or source_path.stat().st_size <= 0:
            raise CMACPSv3Error(f"CMA CPSv3 source file does not exist: {source_path}")
        match = re.search(r"beijing_(\d{6})_", source_path.name, re.IGNORECASE)
        issue = parsed_init if parsed_init != "latest" else (match.group(1) if match else "")
        if not issue:
            raise CMACPSv3Error("--init YYYYMM is required when --source-file name does not contain the WMO issue")
    else:
        session = requests_session()
        if parsed_init == "latest":
            issue, source_token = discover_latest_issue(
                lookback_months=args.lookback_months,
                session=session,
            )
        else:
            issue = parsed_init
        if args.request_delay:
            time.sleep(args.request_delay)
        source_path, source_token = download_bundle(
            resolve_path(args.cache_dir, root),
            issue,
            session=session,
            token=source_token,
            force=args.force_download,
        )

    output_dir = resolve_path(args.output_dir, root)
    manifest = resolve_path(args.manifest, root)
    previous = resolve_path(args.previous_manifest, root) if args.previous_manifest else None
    border_cache = resolve_path(args.cache_dir, root) / "borders"
    borders = [] if args.decode_only else ensure_border_files(args, border_cache, root)

    entries: list[dict[str, Any]] = []
    total_failures = 0
    for product in products:
        grids, attrs, variable_attrs = decode_product_bundle(source_path, product, issue, leads)
        entry, failures = build_run(
            product=product,
            issue=issue,
            leads=leads,
            seasonal_leads=seasonal,
            source_path=source_path,
            source_token=source_token,
            grids=grids,
            attrs=attrs,
            variable_attrs=variable_attrs,
            output_dir=output_dir,
            borders=borders,
            root=root,
            decode_only=args.decode_only,
        )
        entries.append(entry)
        total_failures += failures

    write_manifest(manifest, entries, previous, args.retain_cycles)
    usable = any(entry.get("status") in {"rendered", "decoded", "partial"} for entry in entries)
    print(f"wrote CMA CPSv3 manifest: {manifest} ({len(entries)} product runs, {total_failures} failed targets)")
    return 0 if usable else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (CMACPSv3Error, c3s.C3SError) as exc:
        print(f"CMA CPSV3 ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
