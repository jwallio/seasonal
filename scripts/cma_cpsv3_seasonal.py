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
                "{source_label}  â€¢  CMA CPSv3 native anomaly  â€¢  "
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
        "CMA CPSv3 850-mb Temperature Anomaly (Â°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "2m_temperature_anomaly": _product_spec(
        "2m_temperature_anomaly",
        "t02m",
        "2-m air-temperature anomaly",
        "K",
        "CMA CPSv3 2-m Temperature Anomaly (Â°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "precipitation_anomaly": _product_spec(
        "precipitation_anomaly",
        "prec",
        "total-precipitation-rate anomaly",
        "kg m-2 s-1",
        "CMA CPSv3 CONUS Precipitation Anomaly (in)",
        "kg m-2 s-1 Ã— calendar-month seconds Ã· 25.4 = monthly inches",
    ),
    "sea_surface_temperature_anomaly": _product_spec(
        "sea_surface_temperature_anomaly",
        "sst",
        "sea-surface-temperature anomaly",
        "K",
        "CMA CPSv3 Sea-Surface Temperature Anomaly (Â°C)",
        "Kelvin and Celsius anomaly increments are identical",
    ),
    "mslp_anomaly": _product_spec(
        "mslp_anomaly",
        "mslp",
        "mean-sea-level-pressure anomaly",
        "Pa",
        "CMA CPSv3 Mean Sea-Level Pressure Anomaly (hPa)",
        "Pa Ã· 100 = hPa",
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
        raise CMACPSv3Error("CMA CPSv3 NetCDF decoding requi×­õ¶‰žËkºwµçIÑ}å•…Èˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€•¹€ôÍÑÈ¡…ÑÑÉÌ¹•Ð ‰¡¥¹‘…ÍÑ}•¹‘}å•…Èˆ°€ˆˆ¤¤¹ÍÑÉ¥À ¤(€€€É•ÑÕÉ¸˜‰5AMØÌíÍÑ…ÉÑôµí•¹‘ô¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ¥˜ÍÑ…ÉÐ…¹•¹•±Í”€‰5AMØÌ¹…Ñ¥Ù”¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ(()‘•˜É•¹‘•É}ÁÉ½‘ÕÑ}ÍÁ•Œ¡ÁÉ½‘ÕÐèÍÑÈ°€¨°Í•…Í½¹…°è‰½½°¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€ÍÁ•Œ€ô‘¥Ð¡AI=UQ}MAMmÁÉ½‘ÕÑt¤(€€€¥˜ÁÉ½‘ÕÐ€ôô€‰ÁÉ•¥Á¥Ñ…Ñ¥½¹}…¹½µ…±äˆ…¹¹½ÐÍ•…Í½¹…°è(€€€€€€€ÍÁ•Œ¹ÕÁ‘…Ñ” (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰…¹½µ…±å}µ¥¸ˆè™ÍØÈ¹AI%A}5=9Q!1e}9=51e}5%9}%8°(€€€€€€€€€€€€€€€€‰…¹½µ…±å}µ…àˆè™ÍØÈ¹AI%A}5=9Q!1e}9=51e}5a}%8°(€€€€€€€€€€€€€€€€‰…¹½µ…±å}Ñ¥­Ìˆè™ÍØÈ¹AI%A}5=9Q!1e}9=51e}Q%-L°(€€€€€€€€€€€€€€€€‰…¹½µ…±å}Á…±•ÑÑ”ˆè™ÍØÈ¹AI%A}9=51e}A1QQ°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€É•ÑÕÉ¸ÍÁ•Œ(()‘•˜É•¹‘•É}Ñ…É•Ð (€€€É¥èÉ¥°(€€€ÁÉ½‘ÕÐèÍÑÈ°(€€€¥¹¥ÐèÍÑÈ°(€€€Ñ…É•ÐèÍÑÈ°(€€€±•…è¥¹ÐðÍÑÈ°(€€€½ÕÑÁÕÐèA…Ñ °(€€€‰½É‘•ÉÌè±¥ÍÑmA…Ñ¡t°(€€€‰…Í•±¥¹”èÍÑÈ°(€€€€¨°(€€€Á•É¥½èÍÑÈ€ô€ˆˆ°(¤€´ø9½¹”è(€€€É•¹‘•É}µ…À (€€€€€€€É¥°(€€€€€€€¥¹¥Ð°(€€€€€€€Ñ…É•Ð°(€€€€€€€±•…°(€€€€€€€±¥ÍÐ¡É…¹”¡5}9M5	1}55	IL¤¤°(€€€€€€€½ÕÑÁÕÐ°(€€€€€€€…¹½µ…±äõQÉÕ”°(€€€€€€€‰…Í•±¥¹•}±…‰•°õ‰…Í•±¥¹”°(€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•ÉÌ°(€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½°(€€€€€€€•¹Í•µ‰±•}±…‰•°õ˜‰í5}9M5	1}55	IMôµµ•µ‰•È•¹Í•µ‰±”µ•…¸ˆ°(€€€€€€€ÁÉ½‘ÕÑ}ÍÁ•ŒõÉ•¹‘•É}ÁÉ½‘ÕÑ}ÍÁ•Œ¡ÁÉ½‘ÕÐ°Í•…Í½¹…°õ‰½½°¡Á•É¥½¤¤°(€€€€¤(()‘•˜‰Õ¥±‘}ÉÕ¸ (€€€€¨°(€€€ÁÉ½‘ÕÐèÍÑÈ°(€€€¥ÍÍÕ”èÍÑÈ°(€€€±•…‘Ìè±¥ÍÑm¥¹Ñt°(€€€Í•…Í½¹…±}±•…‘Ìè±¥ÍÑm¥¹Ñt°(€€€Í½ÕÉ•}Á…Ñ èA…Ñ °(€€€Í½ÕÉ•}Ñ½­•¸èÍÑÈ°(€€€É¥‘Ìè‘¥Ñm¥¹Ð°É¥‘t°(€€€…ÑÑÉÌè‘¥ÑmÍÑÈ°¹åt°(€€€Ù…É¥…‰±•}…ÑÑÉÌè‘¥ÑmÍÑÈ°¹åt°(€€€½ÕÑÁÕÑ}‘¥ÈèA…Ñ °(€€€‰½É‘•ÉÌè±¥ÍÑmA…Ñ¡t°(€€€É½½ÐèA…Ñ °(€€€‘•½‘•}½¹±äè‰½½°°(¤€´øÑÕÁ±•m‘¥ÑmÍÑÈ°¹åt°¥¹Ñtè(€€€¥¹¥Ð€ô¥¹¥Ñ}½‘”¡¥ÍÍÕ”¤(€€€ÍÁ•Œ€ôAI=UQ}MAMmÁÉ½‘ÕÑt(€€€±¥µ…Ñ•}±…‰•°€ô‰…Í•±¥¹•}±…‰•°¡…ÑÑÉÌ¤(€€€ÉÕ¹}¥€ô˜‰µ„µÁÍØÌµí¥¹¥ÑôµíÁÉ½‘ÕÑôˆ(€€€ÉÕ¸è‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€‰¥ˆèÉÕ¹}¥°(€€€€€€€€‰µ½‘•°ˆè5}5=0°(€€€€€€€€‰½µÁ½¹•¹Ðˆè€‰µ…}ÁÍØÌˆ°(€€€€€€€€‰½µÁ½¹•¹Ñ}±…‰•°ˆè5}5=0°(€€€€€€€€‰Í½ÕÉ”ˆè€‰]5<1µMA55€¼A	•¥©¥¹œˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè]5=1}	%)%9}%9=}UI0°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèm]5=1}	%)%9}%9=}UI0°]5=1}%IQ}UI0°]5=1}A=1%e}UI1t°(€€€€€€€€‰…É¡¥Ù•}É½½Ðˆè]5=1}%IQ}UI0°(€€€€€€€€‰…É¡¥Ù•}‘¥É•Ñ½Éäˆè¥ÍÍÕ•}‘¥É•Ñ½Éä¡¥ÍÍÕ”¤°(€€€€€€€€‰…É¡¥Ù•}™¥±”ˆè‰Õ¹‘±•}¹…µ”¡¥ÍÍÕ”¤°(€€€€€€€€‰…É¡¥Ù•}Ñ½­•¸ˆèÍ½ÕÉ•}Ñ½­•¸°(€€€€€€€€‰ÁÉ½‘ÕÐˆèÁÉ½‘ÕÐ°(€€€€€€€€‰Ù…É¥…‰±”ˆèÍÁ•l‰Ù…É¥…‰±”‰t°(€€€€€€€€‰Í½ÕÉ•}Ù…É¥…‰±”ˆèÍÁ•l‰Í½ÕÉ•}Ù…É¥…‰±”‰t°(€€€€€€€€‰¥¹¥Ñ}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡¥¹¥Ð°€ˆ•d•´•• ˆ¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰ÁÉ½Ù¥‘•È•¹Í•µ‰±”µµ•…¸…¹½µ…±äˆ°(€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè˜‰5AMØÌí5}9M5	1}55	IMôµµ•µ‰•È™½É•…ÍÐ•¹Í•µ‰±”ˆ°(€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè5}9M5	1}55	IL°(€€€€€€€€‰…É•…Ñ¥½¸ˆè€‰]5<µ½¹Ñ¡±ä…¹½µ…±äˆ°(€€€€€€€€‰™¥•±ˆèÍÁ•l‰™¥•±‰t°(€€€€€€€€‰Õ¹¥ÑÌˆèÍÁ•l‰Õ¹¥ÑÌ‰t°(€€€€€€€€‰É…Ý}™¥•±ˆèÍÁ•l‰É…Ý}™¥•±‰t°(€€€€€€€€‰É…Ý}Õ¹¥ÑÌˆèÍÁ•l‰É…Ý}Õ¹¥ÑÌ‰t°(€€€€€€€€‰Í½ÕÉ•}‘•±…É•‘}Õ¹¥ÑÌˆèÙ…É¥…‰±•}…ÑÑÉÌ¹•Ð ‰Õ¹¥ÑÌˆ°€ˆˆ¤°(€€€€€€€€‰½¹Ù•ÉÍ¥½¸ˆèÍÁ•l‰½¹Ù•ÉÍ¥½¸‰t°(€€€€€€€€‰Í½ÕÉ•}É¥ˆè€ˆÈ¸×
À±½‰…°€ ÄÐÐƒ\€ÜÌ¤ˆ°(€€€€€€€€‰Í½ÕÉ•}™½É•…ÍÑ}µ½¹Ñ¡ÌˆèÍ½ÉÑ•¡MUAA=IQ}1L¤°(€€€€€€€€‰‰…Í•±¥¹”ˆèì(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰ÁÉ½Ù¥‘•É}…¹½µ…±äˆ°(€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰ÁÉ½Ù¥‘•È™½É•…ÍÐ…¹½µ…±äÉ•±…Ñ¥Ù”Ñ¼Ñ¡”5AMØÌ¡¥¹‘…ÍÐ±¥µ…Ñ½±½äˆ°(€€€€€€€€€€€€‰å•…ÉÌˆè˜‰í…ÑÑÉÌ¹•Ð ¡¥¹‘…ÍÑ}ÍÑ…ÉÑ}å•…Èœ°€œœ¥ôµí…ÑÑÉÌ¹•Ð ¡¥¹‘…ÍÑ}•¹‘}å•…Èœ°€œœ¥ôˆ¹ÍÑÉ¥À ˆ´ˆ¤°(€€€€€€€€€€€€‰±…‰•°ˆè±¥µ…Ñ•}±…‰•°°(€€€€€€€ô°(€€€€€€€€‰Í½ÕÉ•}µ•Ñ…‘…Ñ„ˆè…ÑÑÉÌ°(€€€€€€€€‰Ñ…É•ÑÌˆèmt°(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€ô(€€€™…¥±ÕÉ•Ì€ô€À(€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€Ñ…É•Ð€ôŒÍÌ¹Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉäè‘¥ÑmÍÑÈ°¹åt€ôì(€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµ±•…‘í±•…èÀÉ‘ôˆ°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ð°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèŒÍÌ¹Ñ…É•Ñ}Á•É¥½¡Ñ…É•Ð¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèŒÍÌ¹Ñ…É•Ñ}Á•É¥½¡Ñ…É•Ð¥lÅt°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€€€€€‰™¥•±ˆèÍÁ•l‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÍÁ•l‰Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥ŒˆèÉÕ¹l‰ÍÑ…Ñ¥ÍÑ¥Œ‰t°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡Í½ÕÉ•}Á…Ñ °É½½Ð¤°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜±•…¹½Ð¥¸É¥‘Ìè(€€€€€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È¡˜‰™½É•…ÍÐµ½¹Ñ í±•…‘ôÝ…Ì¹½Ð‘•½‘•ˆ¤(€€€€€€€€€€€¥˜‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰µ…}ÁÍØÍ}íÍÁ•lÙ…É¥…‰±”uõ}íÑ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€É•¹‘•É}Ñ…É•Ð¡É¥‘Ím±•…‘t°ÁÉ½‘ÕÐ°¥¹¥Ð°Ñ…É•Ð°±•…°½ÕÑÁÕÐ°‰½É‘•ÉÌ°±¥µ…Ñ•}±…‰•°¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰5AMØÌíÁÉ½‘ÕÑôÑ…É•ÐíÑ…É•Ñô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹l‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤((€€€¥˜Í•…Í½¹…±}±•…‘Ìè(€€€€€€€™¥ÉÍÐ°±…ÍÐ€ôÍ•…Í½¹…±}±•…‘ÍlÁt°Í•…Í½¹…±}±•…‘Íl´Åt(€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð€ôŒÍÌ¹Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°™¥ÉÍÐ¤°ŒÍÌ¹Ñ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±…ÍÐ¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉä€ôì(€€€€€€€€€€€€‰¥ˆè˜‰íÉÕ¹}¥‘ôµí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèŒÍÌ¹Ñ…É•Ñ}Á•É¥½¡™¥ÉÍÑ}Ñ…É•Ð¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèŒÍÌ¹Ñ…É•Ñ}Á•É¥½¡±…ÍÑ}Ñ…É•Ð¥lÅt°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ÷ŠMí±…ÍÑôˆ°(€€€€€€€€€€€€‰µ½¹Ñ¡±å}±•…‘ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€‰™¥•±ˆèÍÁ•l‰™¥•±‰t°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆèÍÁ•l‰Í•…Í½¹…±}Õ¹¥ÑÌ‰t°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥ŒˆèÉÕ¹l‰ÍÑ…Ñ¥ÍÑ¥Œ‰t°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè€ (€€€€€€€€€€€€€€€˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ …ÕµÕ±…Ñ•…¹½µ…±äˆ(€€€€€€€€€€€€€€€¥˜ÍÁ•l‰Í•…Í½¹…±}É•‘Õ•È‰t€ôô€‰ÍÕ´ˆ(€€€€€€€€€€€€€€€•±Í”˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ µ•…¸…¹½µ…±äˆ(€€€€€€€€€€€€¤°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡Í½ÕÉ•}Á…Ñ °É½½Ð¤°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜…¹ä¡±•…¹½Ð¥¸É¥‘Ì™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¤è(€€€€€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È ‰Í•…Í½¹…°Ý¥¹‘½Ü¥Ìµ¥ÍÍ¥¹œ½¹”½Èµ½É”]5<™½É•…ÍÐµ½¹Ñ¡Ìˆ¤(€€€€€€€€€€€É•‘Õ•È€ôÍÕµ}É¥‘Ì¥˜ÍÁ•l‰Í•…Í½¹…±}É•‘Õ•È‰t€ôô€‰ÍÕ´ˆ•±Í”µ•…¹}É¥‘Ì(€€€€€€€€€€€Í•…Í½¹…±}É¥€ôÉ•‘Õ•È¡mÉ¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€¥˜‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€½ÕÑÁÕÐ€ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ñlèát€¼˜‰µ…}ÁÍØÍ}íÍÁ•lÙ…É¥…‰±”uõ}í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô¹©Áœˆ(€€€€€€€€€€€€€€€É•¹‘•É}Ñ…É•Ð (€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}É¥°(€€€€€€€€€€€€€€€€€€€ÁÉ½‘ÕÐ°(€€€€€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€€€€€˜‰í™¥ÉÍÑ÷ŠMí±…ÍÑôˆ°(€€€€€€€€€€€€€€€€€€€½ÕÑÁÕÐ°(€€€€€€€€€€€€€€€€€€€‰½É‘•ÉÌ°(€€€€€€€€€€€€€€€€€€€±¥µ…Ñ•}±…‰•°°(€€€€€€€€€€€€€€€€€€€Á•É¥½õŒÍÌ¹Á•É¥½‘}±…‰•°¡™¥ÉÍÑ}Ñ…É•Ð°±…ÍÑ}Ñ…É•Ð¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÐ°É½½Ð¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰5AMØÌíÁÉ½‘ÕÑôÍ•…Í½¹…°Ý¥¹‘½Ü™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹l‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤((€€€ÍÑ…ÑÕÍ•Ì€ômÍÑÈ¡Ñ…É•Ð¹•Ð ‰ÍÑ…ÑÕÌˆ°€ˆˆ¤¤™½ÈÑ…É•Ð¥¸ÉÕ¹l‰Ñ…É•ÑÌ‰ut(€€€ÕÍ…‰±”€ô…¹ä¡ÍÑ…ÑÕÌ¥¸ì‰‘•½‘•ˆ°€‰É•¹‘•É•‰ô™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤(€€€ÉÕ¹l‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ¥˜¹½ÐÕÍ…‰±”•±Í”€ ‰Á…ÉÑ¥…°ˆ¥˜™…¥±ÕÉ•Ì•±Í”€ ‰‘•½‘•ˆ¥˜‘•½‘•}½¹±ä•±Í”€‰É•¹‘•É•ˆ¤¤(€€€ÉÕ¹l‰½ÕÑÁÕÑ}‘¥È‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É½½Ð¤(€€€É•ÑÕÉ¸ÉÕ¸°™…¥±ÕÉ•Ì(()‘•˜ÝÉ¥Ñ•}µ…¹¥™•ÍÐ (€€€Á…Ñ èA…Ñ °(€€€•¹ÑÉ¥•Ìè%Ñ•É…‰±•m‘¥ÑmÍÑÈ°¹åut°(€€€ÁÉ•Ù¥½ÕÌèA…Ñ ð9½¹”°(€€€É•Ñ…¥¹}å±•Ìè¥¹Ð°(¤€´ø9½¹”è(€€€¥˜É•Ñ…¥¹}å±•Ì€ð€Äè(€€€€€€€É…¥Í”5AMØÍÉÉ½È ‰µ…¹¥™•ÍÐÉ•Ñ•¹Ñ¥½¸µÕÍÐ­••À…Ð±•…ÍÐ½¹”å±”ˆ¤(€€€…±±}•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€™½È…¹‘¥‘…Ñ”¥¸€¡ÁÉ•Ù¥½ÕÌ°Á…Ñ ¤è(€€€€€€€¥˜¹½Ð…¹‘¥‘…Ñ”½È¹½Ð…¹‘¥‘…Ñ”¹•á¥ÍÑÌ ¤è(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€ÑÉäè(€€€€€€€€€€€Á…å±½…€ô©Í½¸¹±½…‘Ì¡…¹‘¥‘…Ñ”¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡ÉÕ¸™½ÈÉÕ¸¥¸Á…å±½…¹•Ð ‰ÉÕ¹Ìˆ°mt¤¥˜¥Í¥¹ÍÑ…¹”¡ÉÕ¸°‘¥Ð¤¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È¡˜‰½Õ±¹½ÐÉ•…ÁÉ¥½È5AMØÌµ…¹¥™•ÍÐí…¹‘¥‘…Ñ•ôèí•áôˆ¤™É½´•áŒ(€€€…±±}•¹ÑÉ¥•Ì¹•áÑ•¹¡•¹ÑÉ¥•Ì¤(€€€Õ¹¥ÅÕ”€ôíÍÑÈ¡ÉÕ¹l‰¥‰t¤èÉÕ¸™½ÈÉÕ¸¥¸…±±}•¹ÑÉ¥•Ì¥˜ÉÕ¸¹•Ð ‰¥ˆ¥ô(€€€½É‘•É•€ôÍ½ÉÑ•¡Õ¹¥ÅÕ”¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„ÉÕ¸è€¡ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤°ÍÑÈ¡ÉÕ¸¹•Ð ‰¥ˆ°€ˆˆ¤¤¤°É•Ù•ÉÍ”õQÉÕ”¤(€€€å±•Ìè±¥ÍÑmÍÑÉt€ômt(€€€™½ÈÉÕ¸¥¸½É‘•É•è(€€€€€€€å±”€ôÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤(€€€€€€€¥˜å±”…¹å±”¹½Ð¥¸å±•Ìè(€€€€€€€€€€€å±•Ì¹…ÁÁ•¹¡å±”¤(€€€­••À€ôÍ•Ð¡å±•ÍléÉ•Ñ…¥¹}å±•Ít¤(€€€Á…å±½…€ôì(€€€€€€€€‰Í¡•µ…}Ù•ÉÍ¥½¸ˆè€Ä°(€€€€€€€€‰­¥¹ˆè€‰µ…}ÁÍØÍ}Í•…Í½¹…±}µ…¹¥™•ÍÐˆ°(€€€€€€€€‰•¹•É…Ñ•‘}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹¹½Ü¡‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€‰Í½ÕÉ”ˆè€‰]5<1µMA55€¼A	•¥©¥¹œ5AMØÌˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè]5=1}	%)%9}%9=}UI0°(€€€€€€€€‰Í½ÕÉ•}ÕÉ±Ìˆèm]5=1}	%)%9}%9=}UI0°]5=1}%IQ}UI0°]5=1}A=1%e}UI1t°(€€€€€€€€‰ÁÉ½‘ÕÑ}±…‰•±ÌˆèAI=UQ}1	1L°(€€€€€€€€‰Í½ÕÉ•}¡½É¥é½¸ˆèì(€€€€€€€€€€€€‰ÍåÍÑ•µ}±•¹Ñ¡}µ½¹Ñ¡Ìˆè€Ü°(€€€€€€€€€€€€‰É•‘¥ÍÑÉ¥‰ÕÑ•‘}™½É•…ÍÑ}µ½¹Ñ¡ÌˆèÍ½ÉÑ•¡MUAA=IQ}1L¤°(€€€€€€€€€€€€‰Á½±¥äˆè€‰=¹±äÑ¡”Ñ¡É•”]5<µÉ•‘¥ÍÑÉ¥‰ÕÑ•™½É•…ÍÐµ½¹Ñ¡Ì…É”É•¹‘•É•¸ˆ°(€€€€€€€ô°(€€€€€€€€‰É•Ñ•¹Ñ¥½¸ˆèì‰µ…á}å±•ÌˆèÉ•Ñ…¥¹}å±•Ì°€‰¡¥ÍÑ½Éå}å±•Ìˆèµ…à À°É•Ñ…¥¹}å±•Ì€´€Ä¥ô°(€€€€€€€€‰ÉÕ¹ÌˆèmÉÕ¸™½ÈÉÕ¸¥¸½É‘•É•¥˜ÍÑÈ¡ÉÕ¸¹•Ð ‰¥¹¥Ñ}ÕÑŒˆ°€ˆˆ¤¤¥¸­••Át°(€€€ô(€€€Á…Ñ ¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€Ñ•µÁ½É…Éä€ôÁ…Ñ ¹Ý¥Ñ¡}¹…µ”¡Á…Ñ ¹¹…µ”€¬€ˆ¹ÑµÀˆ¤(€€€Ñ•µÁ½É…Éä¹ÝÉ¥Ñ•}Ñ•áÐ¡©Í½¸¹‘ÕµÁÌ¡Á…å±½…°¥¹‘•¹ÐôÈ¤€¬€‰q¸ˆ°•¹½‘¥¹œô‰ÕÑ˜´àˆ¤(€€€Ñ•µÁ½É…Éä¹É•Á±…”¡Á…Ñ ¤(()‘•˜‰Õ¥±‘}Á…ÉÍ•È ¤€´ø…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•Èè(€€€Á…ÉÍ•È€ô…ÉÁ…ÉÍ”¹ÉÕµ•¹ÑA…ÉÍ•È¡‘•ÍÉ¥ÁÑ¥½¸õ}}‘½}|¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ½‘ÕÐˆ°‘•™…Õ±Ðô‰…±°ˆ°¡•±Àô‰½¹”ÁÉ½‘ÕÐ°„½µµ„µÍ•Á…É…Ñ•±¥ÍÐ°½È…±°ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¥¹¥Ðˆ°‘•™…Õ±Ðô‰±…Ñ•ÍÐˆ°¡•±Àô‰]5<¥ÍÍÕ”µ½¹Ñ …Ìeeee54½È±…Ñ•ÍÐˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ±•…µµ½¹Ñ¡Ìˆ°‘•™…Õ±ÐôˆÄ°È°Ìˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ•…Í½¹…°µÝ¥¹‘½Üˆ°‘•™…Õ±ÐôˆÄ°È°Ìˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÍ½ÕÉ”µ™¥±”ˆ°ÑåÁ”õA…Ñ °¡•±Àô‰…±É•…‘äµ‘½Ý¹±½…‘•]5<A	•¥©¥¹œ9•Ñ‰Õ¹‘±”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ…¡”µ‘¥Èˆ°‘•™…Õ±Ðôˆ¹…¡”½µ„µÁÍØÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ½ÕÑÁÕÐµ‘¥Èˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½µ…}ÁÍØÌˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µµ…¹¥™•ÍÐˆ°‘•™…Õ±Ðô‰ÁÕ‰±¥Œ½Í•…Í½¹…°½µ…}ÁÍØÍ}µ…¹¥™•ÍÐ¹©Í½¸ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÁÉ•Ù¥½ÕÌµµ…¹¥™•ÍÐˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•Ñ…¥¸µå±•Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôÐ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ±½½­‰…¬µµ½¹Ñ¡Ìˆ°ÑåÁ”õ¥¹Ð°‘•™…Õ±ÐôØ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µÉ•ÅÕ•ÍÐµ‘•±…äˆ°ÑåÁ”õ™±½…Ð°‘•™…Õ±ÐôÀ¸Ô¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ™½É”µ‘½Ý¹±½…ˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‰½É‘•Èµ•½©Í½¸ˆ°…Ñ¥½¸ô‰…ÁÁ•¹ˆ°ÑåÁ”õA…Ñ ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ¹¼µ‰½É‘•ÉÌˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€Á…ÉÍ•È¹…‘‘}…ÉÕµ•¹Ð ˆ´µ‘•½‘”µ½¹±äˆ°…Ñ¥½¸ô‰ÍÑ½É•}ÑÉÕ”ˆ¤(€€€É•ÑÕÉ¸Á…ÉÍ•È(()‘•˜É•Í½±Ù•}Á…Ñ ¡Ù…±Õ”èÍÑÈðA…Ñ °É½½ÐèA…Ñ ¤€´øA…Ñ è(€€€Á…Ñ €ôA…Ñ ¡Ù…±Õ”¤(€€€É•ÑÕÉ¸Á…Ñ ¥˜Á…Ñ ¹¥Í}…‰Í½±ÕÑ” ¤•±Í”É½½Ð€¼Á…Ñ (()‘•˜ÉÕ¸¡…ÉÌè…ÉÁ…ÉÍ”¹9…µ•ÍÁ…”¤€´ø¥¹Ðè(€€€É½½Ð€ôA…Ñ ¡}}™¥±•}|¤¹É•Í½±Ù” ¤¹Á…É•¹ÑÍlÅt(€€€ÁÉ½‘ÕÑÌ€ôÍ•±•Ñ•‘}ÁÉ½‘ÕÑÌ¡…ÉÌ¹ÁÉ½‘ÕÐ¤(€€€±•…‘Ì€ôÁ…ÉÍ•}±•…‘Ì¡…ÉÌ¹±•…‘}µ½¹Ñ¡Ì°€‰±•…µ½¹Ñ¡Ìˆ¤(€€€Í•…Í½¹…°€ôÁ…ÉÍ•}±•…‘Ì¡…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü°€‰Í•…Í½¹…°Ý¥¹‘½Üˆ¤¥˜…ÉÌ¹Í•…Í½¹…±}Ý¥¹‘½Ü•±Í”mt(€€€¥˜Í•…Í½¹…°è(€€€€€€€¥˜Í•…Í½¹…°€„ô±¥ÍÐ¡É…¹”¡µ¥¸¡Í•…Í½¹…°¤°µ…à¡Í•…Í½¹…°¤€¬€Ä¤¤è(€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È ˆ´µÍ•…Í½¹…°µÝ¥¹‘½ÜµÕÍÐ½¹Ñ…¥¸½¹Í•ÕÑ¥Ù”]5<™½É•…ÍÐµ½¹Ñ¡Ìˆ¤(€€€€€€€±•…‘Ì€ôÍ½ÉÑ•¡Í•Ð¡±•…‘Ì¤¹Õ¹¥½¸¡Í•…Í½¹…°¤¤(€€€¥˜…ÉÌ¹±½½­‰…­}µ½¹Ñ¡Ì€ð€Ä½È…ÉÌ¹±½½­‰…­}µ½¹Ñ¡Ì€ø€ÈÐè(€€€€€€€É…¥Í”5AMØÍÉÉ½È ˆ´µ±½½­‰…¬µµ½¹Ñ¡ÌµÕÍÐ‰”‰•ÑÝ••¸€Ä…¹€ÈÐˆ¤(€€€¥˜…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä€ð€Àè(€€€€€€€É…¥Í”5AMØÍÉÉ½È ˆ´µÉ•ÅÕ•ÍÐµ‘•±…ä…¹¹½Ð‰”¹•…Ñ¥Ù”ˆ¤((€€€Á…ÉÍ•‘}¥¹¥Ð€ôÁ…ÉÍ•}¥¹¥Ð¡…ÉÌ¹¥¹¥Ð¤(€€€Í½ÕÉ•}Ñ½­•¸€ô€ˆˆ(€€€Í•ÍÍ¥½¸è¹äð9½¹”€ô9½¹”(€€€¥˜…ÉÌ¹Í½ÕÉ•}™¥±”è(€€€€€€€Í½ÕÉ•}Ñ½­•¸€ô€‰±½…°Í½ÕÉ”™¥±”ˆ(€€€€€€€Í½ÕÉ•}Á…Ñ €ôÉ•Í½±Ù•}Á…Ñ ¡…ÉÌ¹Í½ÕÉ•}™¥±”°É½½Ð¤(€€€€€€€¥˜¹½ÐÍ½ÕÉ•}Á…Ñ ¹•á¥ÍÑÌ ¤½ÈÍ½ÕÉ•}Á…Ñ ¹ÍÑ…Ð ¤¹ÍÑ}Í¥é”€ðô€Àè(€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È¡˜‰5AMØÌÍ½ÕÉ”™¥±”‘½•Ì¹½Ð•á¥ÍÐèíÍ½ÕÉ•}Á…Ñ¡ôˆ¤(€€€€€€€µ…Ñ €ôÉ”¹Í•…É ¡È‰‰•¥©¥¹|¡q‘ìÙô¥|ˆ°Í½ÕÉ•}Á…Ñ ¹¹…µ”°É”¹%9=IM¤(€€€€€€€¥ÍÍÕ”€ôÁ…ÉÍ•‘}¥¹¥Ð¥˜Á…ÉÍ•‘}¥¹¥Ð€„ô€‰±…Ñ•ÍÐˆ•±Í”€¡µ…Ñ ¹É½ÕÀ Ä¤¥˜µ…Ñ •±Í”€ˆˆ¤(€€€€€€€¥˜¹½Ð¥ÍÍÕ”è(€€€€€€€€€€€É…¥Í”5AMØÍÉÉ½È ˆ´µ¥¹¥Ðeeee54¥ÌÉ•ÅÕ¥É•Ý¡•¸€´µÍ½ÕÉ”µ™¥±”¹…µ”‘½•Ì¹½Ð½¹Ñ…¥¸Ñ¡”]5<¥ÍÍÕ”ˆ¤(€€€•±Í”è(€€€€€€€Í•ÍÍ¥½¸€ôÉ•ÅÕ•ÍÑÍ}Í•ÍÍ¥½¸ ¤(€€€€€€€¥˜Á…ÉÍ•‘}¥¹¥Ð€ôô€‰±…Ñ•ÍÐˆè(€€€€€€€€€€€¥ÍÍÕ”°Í½ÕÉ•}Ñ½­•¸€ô‘¥Í½Ù•É}±…Ñ•ÍÑ}¥ÍÍÕ” (€€€€€€€€€€€€€€€±½½­‰…­}µ½¹Ñ¡Ìõ…ÉÌ¹±½½­‰…­}µ½¹Ñ¡Ì°(€€€€€€€€€€€€€€€Í•ÍÍ¥½¸õÍ•ÍÍ¥½¸°(€€€€€€€€€€€€¤(€€€€€€€•±Í”è(€€€€€€€€€€€¥ÍÍÕ”€ôÁ…ÉÍ•‘}¥¹¥Ð(€€€€€€€¥˜…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…äè(€€€€€€€€€€€Ñ¥µ”¹Í±••À¡…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä¤(€€€€€€€Í½ÕÉ•}Á…Ñ °Í½ÕÉ•}Ñ½­•¸€ô‘½Ý¹±½…‘}‰Õ¹‘±” (€€€€€€€€€€€É•Í½±Ù•}Á…Ñ ¡…ÉÌ¹…¡•}‘¥È°É½½Ð¤°(€€€€€€€€€€€¥ÍÍÕ”°(€€€€€€€€€€€Í•ÍÍ¥½¸õÍ•ÍÍ¥½¸°(€€€€€€€€€€€Ñ½­•¸õÍ½ÕÉ•}Ñ½­•¸°(€€€€€€€€€€€™½É”õ…ÉÌ¹™½É•}‘½Ý¹±½…°(€€€€€€€€¤((€€€½ÕÑÁÕÑ}‘¥È€ôÉ•Í½±Ù•}Á…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È°É½½Ð¤(€€€µ…¹¥™•ÍÐ€ôÉ•Í½±Ù•}Á…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ°É½½Ð¤(€€€ÁÉ•Ù¥½ÕÌ€ôÉ•Í½±Ù•}Á…Ñ ¡…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ°É½½Ð¤¥˜…ÉÌ¹ÁÉ•Ù¥½ÕÍ}µ…¹¥™•ÍÐ•±Í”9½¹”(€€€‰½É‘•É}…¡”€ôÉ•Í½±Ù•}Á…Ñ ¡…ÉÌ¹…¡•}‘¥È°É½½Ð¤€¼€‰‰½É‘•ÉÌˆ(€€€‰½É‘•ÉÌ€ômt¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°‰½É‘•É}…¡”°É½½Ð¤((€€€•¹ÑÉ¥•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€Ñ½Ñ…±}™…¥±ÕÉ•Ì€ô€À(€€€™½ÈÁÉ½‘ÕÐ¥¸ÁÉ½‘ÕÑÌè(€€€€€€€É¥‘Ì°…ÑÑÉÌ°Ù…É¥…‰±•}…ÑÑÉÌ€ô‘•½‘•}ÁÉ½‘ÕÑ}‰Õ¹‘±”¡Í½ÕÉ•}Á…Ñ °ÁÉ½‘ÕÐ°¥ÍÍÕ”°±•…‘Ì¤(€€€€€€€•¹ÑÉä°™…¥±ÕÉ•Ì€ô‰Õ¥±‘}ÉÕ¸ (€€€€€€€€€€€ÁÉ½‘ÕÐõÁÉ½‘ÕÐ°(€€€€€€€€€€€¥ÍÍÕ”õ¥ÍÍÕ”°(€€€€€€€€€€€±•…‘Ìõ±•…‘Ì°(€€€€€€€€€€€Í•…Í½¹…±}±•…‘ÌõÍ•…Í½¹…°°(€€€€€€€€€€€Í½ÕÉ•}Á…Ñ õÍ½ÕÉ•}Á…Ñ °(€€€€€€€€€€€Í½ÕÉ•}Ñ½­•¸õÍ½ÕÉ•}Ñ½­•¸°(€€€€€€€€€€€É¥‘ÌõÉ¥‘Ì°(€€€€€€€€€€€…ÑÑÉÌõ…ÑÑÉÌ°(€€€€€€€€€€€Ù…É¥…‰±•}…ÑÑÉÌõÙ…É¥…‰±•}…ÑÑÉÌ°(€€€€€€€€€€€½ÕÑÁÕÑ}‘¥Èõ½ÕÑÁÕÑ}‘¥È°(€€€€€€€€€€€‰½É‘•ÉÌõ‰½É‘•ÉÌ°(€€€€€€€€€€€É½½ÐõÉ½½Ð°(€€€€€€€€€€€‘•½‘•}½¹±äõ…ÉÌ¹‘•½‘•}½¹±ä°(€€€€€€€€¤(€€€€€€€•¹ÑÉ¥•Ì¹…ÁÁ•¹¡•¹ÑÉä¤(€€€€€€€Ñ½Ñ…±}™…¥±ÕÉ•Ì€¬ô™…¥±ÕÉ•Ì((€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÐ°•¹ÑÉ¥•Ì°ÁÉ•Ù¥½ÕÌ°…ÉÌ¹É•Ñ…¥¹}å±•Ì¤(€€€ÕÍ…‰±”€ô…¹ä¡•¹ÑÉä¹•Ð ‰ÍÑ…ÑÕÌˆ¤¥¸ì‰É•¹‘•É•ˆ°€‰‘•½‘•ˆ°€‰Á…ÉÑ¥…°‰ô™½È•¹ÑÉä¥¸•¹ÑÉ¥•Ì¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”5AMØÌµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑô€¡í±•¸¡•¹ÑÉ¥•Ì¥ôÁÉ½‘ÕÐÉÕ¹Ì°íÑ½Ñ…±}™…¥±ÕÉ•Íô™…¥±•Ñ…É•ÑÌ¤ˆ¤(€€€É•ÑÕÉ¸€À¥˜ÕÍ…‰±”•±Í”€È(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡‰Õ¥±‘}Á…ÉÍ•È ¤¹Á…ÉÍ•}…ÉÌ ¤¤(€€€•á•ÁÐ€¡5AMØÍÉÉ½È°ŒÍÌ¹ÍMÉÉ½È¤…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰5AMXÌII=Hèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤(