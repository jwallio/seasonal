#!/usr/bin/env python3
"""Fetch and render C3S multi-system and component seasonal guidance.

The Copernicus Climate Data Store exposes each contributing centre through the
same postprocessed seasonal datasets.  This adapter keeps those centre/system
choices in the manifest, renders the selected components, and also publishes a
transparent multi-system mean.  Snowfall adds the already-produced, corrected
NOAA CFSv2 departure as the NCEP blend input because CDS does not expose a
native NCEP snowfall field for the operational window.
"""

from __future__ import annotations

from height_display import HEIGHT_ANOMALY_STYLE, HEIGHT_NH_FRAME

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

import numpy as np

from cds_client import client_options, retrieve_with_queue_retry
from cfsv2_seasonal import (
    CONUS_REGION,
    CONUS_PRECIP_REGION,
    DEFAULT_REGION,
    Grid,
    SNOWFALL_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    ensure_border_files,
    mean_grids,
    NORTHERN_HEMISPHERE_REGION,
    read_grid_state,
    relative_path,
    render_map,
    sum_grids,
    write_grid_state,
)
from seas5_seasonal import grid_from_grib
from seasonal_products import grid_quality_control, is_retired_product, require_quality_control
from seasonal_rendering import canonicalize_product_spec
from snowfall_display import depth_departure


CDS_API_ROOT = "https://cds.climate.copernicus.eu/api"
PRESSURE_DATASET = "seasonal-postprocessed-pressure-levels"
SINGLE_DATASET = "seasonal-postprocessed-single-levels"
RAW_PRESSURE_DATASET = "seasonal-monthly-pressure-levels"
SOURCE_URL = "https://climate.copernicus.eu/seasonal-forecasts"
PRESSURE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels"
SINGLE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels"
LICENSE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels?tab=download#manage-licences"
NORTH_AMERICA_AREA = [90.0, -170.0, 15.0, 0.0]
NORTHERN_HEMISPHERE_AREA = [90.0, -180.0, 0.0, 180.0]
CONUS_AREA = [60.0, -135.0, 20.0, -55.0]
GEOPOTENTIAL_GRAVITY = 9.80665
M_TO_INCH = 1000.0 / 25.4
CFSV2_COMPONENT = "ncep"
CFSV2_COMPONENT_LABEL = "NCEP / CFSv2"
CFSV2_PUBLISHED_MANIFEST_URL = "https://jwallio.github.io/seasonal/cfsv2_manifest.json"
CFSV2_MANIFEST_TIMEOUT_SECONDS = 30
CFSV2_GRID_TIMEOUT_SECONDS = 180

CENTRES: dict[str, dict[str, Any]] = {
    "ecmwf": {"label": "ECMWF", "system": "51", "members": 51},
    "ukmo": {
        "label": "UK Met Office",
        "system": "610",
        "members": 62,
        "model_version": "GloSea6-GC5.1",
    },
    "meteo_france": {"label": "Météo-France", "system": "9", "members": 51},
    "dwd": {"label": "DWD", "system": "22", "members": 50},
    "cmcc": {"label": "CMCC", "system": "4", "members": 50},
    "ncep": {"label": "NCEP", "system": "2", "members": 24},
    "jma": {"label": "JMA", "system": "4", "members": 55, "model_version": "JMA/MRI-CPS4"},
    "eccc": {"label": "ECCC", "system": "5", "members": 20},
    "bom": {"label": "BOM", "system": "2", "members": 33},
}

# Native snowfall is currently published for these six C3S systems.  Keep
# this list aligned with check_seasonal_releases.py and the live blend policy;
# the other catalogue centre/system pairs return MarsNoDataError for the
# snowfall field in the operational DJF window.
SNOWFALL_CENTRES = (
    "ecmwf", "ukmo", "meteo_france", "dwd", "cmcc", "eccc",
)

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "name": "500mb_height_anomaly", "variable": "z500", "field": "z500_anomaly",
        "raw_field": "geopotential anomaly", "raw_units": "m² s⁻²", "units": "m",
        "seasonal_units": "m", "height_contours": True, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", **HEIGHT_ANOMALY_STYLE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "geopotential_anomaly",
        "cds_pressure_level": "500", "cds_raw_dataset": RAW_PRESSURE_DATASET,
        "cds_raw_variable": "geopotential", "raw_field_name": "geopotential",
    },
    "850mb_temperature_anomaly": {
        "name": "850mb_temperature_anomaly", "variable": "t850", "field": "t850_anomaly",
        "raw_field": "temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": CONUS_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C, "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "temperature_anomaly",
        "cds_pressure_level": "850",
    },
    "2m_temperature_anomaly": {
        "name": "2m_temperature_anomaly", "variable": "t2m", "field": "t2m_anomaly",
        "raw_field": "2-m temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": CONUS_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C, "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "2m_temperature_anomaly",
    },
    "precipitation_anomaly": {
        "name": "precipitation_anomaly", "variable": "pr", "field": "precipitation_anomaly",
        "raw_field": "total precipitation anomaly", "raw_units": "m s⁻¹", "units": "in",
        "seasonal_units": "in", "height_contours": False, "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total", "seasonal_reducer": "sum",
        "cds_dataset": SINGLE_DATASET,
        "cds_variable": "total_precipitation_anomalous_rate_of_accumulation",
    },
    "snowfall_anomaly": {
        "name": "snowfall_anomaly", "variable": "sf", "field": "snowfall_anomaly",
        "raw_field": "snowfall anomalous rate of accumulation",
        "raw_units": "m s⁻¹ of water equivalent", "units": "in", "seasonal_units": "in",
        "height_contours": False, "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total", "seasonal_reducer": "sum",
        "cds_dataset": SINGLE_DATASET,
        "cds_variable": "snowfall_anomalous_rate_of_accumulation",
    },
    "mslp_anomaly": {
        "name": "mslp_anomaly", "variable": "slp", "field": "mslp_anomaly",
        "raw_field": "mean sea-level pressure anomaly", "raw_units": "Pa", "units": "hPa",
        "seasonal_units": "hPa", "height_contours": False, "region": CONUS_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean",
        "cds_dataset": SINGLE_DATASET, "cds_variable": "mean_sea_level_pressure_anomaly",
    },
}


PRODUCT_SPECS["500mb_height_anomaly_nh"] = {
    **PRODUCT_SPECS["500mb_height_anomaly"],
    "name": "500mb_height_anomaly_nh",
    "region": NORTHERN_HEMISPHERE_REGION,
    **HEIGHT_NH_FRAME,
}

for _product_name, _product_spec in list(PRODUCT_SPECS.items()):
    PRODUCT_SPECS[_product_name] = canonicalize_product_spec(_product_spec, seasonal=False)


class C3SError(RuntimeError):
    """A user-actionable C3S source or rendering error."""


C3S_RELEASE_DAY = 10
C3S_RELEASE_HOUR_UTC = 12


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def latest_init(now: dt.datetime | None = None) -> str:
    """Return the newest monthly issue that CDS can have released.

    C3S/JMA current-month contributions are released on the 10th.  Selecting
    the calendar month on the 1st–9th makes a manual ``latest`` run request a
    known-restricted cycle and turns a harmless early-month refresh into a
    failed Actions run.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    year, month = now.year, now.month
    if (now.day, now.hour) < (C3S_RELEASE_DAY, C3S_RELEASE_HOUR_UTC):
        year, month = month_after(year, month, -1)
    return f"{year:04d}{month:02d}0100"


def parse_init(value: str) -> str:
    if value == "latest":
        return latest_init()
    if re.fullmatch(r"\d{6}", value):
        return f"{value}0100"
    if re.fullmatch(r"\d{8}", value):
        return f"{value}00"
    if re.fullmatch(r"\d{10}", value):
        return value
    raise C3SError("--init must be latest, YYYYMM, YYYYMMDD, or YYYYMMDDHH")


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise C3SError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise C3SError(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise C3SError(f"{label} cannot be empty")
    return result


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(date.year, date.month, lead)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    end = dt.datetime(year, month, 1)
    return iso_utc(start.replace(tzinfo=dt.timezone.utc)), iso_utc(end.replace(tzinfo=dt.timezone.utc))


def period_label(first: str, last: str) -> str:
    start = dt.datetime.strptime(first, "%Y%m")
    end = dt.datetime.strptime(last, "%Y%m")
    season = {(12, 2): "DJF", (3, 5): "MAM", (6, 8): "JJA", (9, 11): "SON"}.get((start.month, end.month))
    if season and ((start.month == 12 and end.year == start.year + 1) or end.year == start.year):
        if season == "DJF" and end.year == start.year + 1:
            return f"{season} {start.year}\u2013{end.year % 100:02d}"
        return f"{season} {end.year}"
    return f"{start:%b %Y}–{end:%b %Y}"


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return int((dt.datetime(year, month, 1) - start).total_seconds())


def parse_centres(value: str, product_name: str | None = None) -> list[str]:
    requested_all = value.strip().lower() in {"all", "c3s", "multi-system"}
    if requested_all and product_name == "snowfall_anomaly":
        names = list(SNOWFALL_CENTRES)
    elif requested_all:
        names = list(CENTRES)
    else:
        names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in CENTRES]
    if unknown:
        raise C3SError(f"unknown C3S centre(s): {', '.join(unknown)}")
    return list(dict.fromkeys(names))


def _cache_busted_url(url: str) -> str:
    """Prevent a Pages/CDN cache from hiding the newest CFSv2 manifest."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    query = f"{parsed.query}&" if parsed.query else ""
    query += f"c3s_refresh={int(dt.datetime.now(dt.timezone.utc).timestamp())}"
    return urlunparse(parsed._replace(query=query))


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        _cache_busted_url(url),
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    try:
        with urlopen(request, timeout=CFSV2_MANIFEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise C3SError(f"could not read published CFSv2 manifest {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise C3SError(f"published CFSv2 manifest {url} is not a JSON object")
    return payload


def _published_cfsv2_asset_url(manifest_url: str, asset: str) -> str:
    """Map a repository-relative CFSv2 asset to the Pages publication root.

    The CFSv2 producer records its checkout path (``public/seasonal/cfsv2``)
    in the manifest, while the Pages publisher exposes that payload at
    ``<site-root>/cfsv2``.  Keeping this translation here prevents the C3S
    adapter from depending on a local checkout of the other workflow's files.
    """

    asset = str(asset).strip()
    if not asset:
        raise C3SError("published CFSv2 target has an empty numeric-grid path")
    if urlparse(asset).scheme in {"http", "https"}:
        return asset
    relative = asset.lstrip("/")
    for prefix in ("public/seasonal/cfsv2/", "seasonal/cfsv2/", "cfsv2/"):
        if relative.startswith(prefix):
            relative = "cfsv2/" + relative[len(prefix):]
            break
    else:
        if relative.startswith("public/seasonal/"):
            relative = relative[len("public/seasonal/"):]
    return urljoin(manifest_url, relative)


def _published_cfsv2_lwe_asset(target_entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return the CFSv2 asset that is still in LWE units.

    Recent CFSv2 snowfall manifests expose both ``numeric_grid`` (the
    already-converted snow-depth sidecar) and ``native_lwe_grid``.  The latter
    is the only valid input for the C3S blend.  Older corrected manifests used
    the unsuffixed ``numeric_grid`` for LWE, so retain that narrowly-defined
    compatibility path while rejecting ``.snow.csv.gz`` outright.
    """

    native_lwe = target_entry.get("native_lwe_grid")
    if native_lwe:
        return str(native_lwe), "native_lwe_grid"
    numeric = str(target_entry.get("numeric_grid") or "")
    if numeric and not numeric.endswith(".snow.csv.gz"):
        return numeric, "legacy_numeric_grid_lwe"
    return None


def _download_published_grid(url: str, destination: Path) -> Path:
    """Download one already-decoded CFSv2 CSV grid into the C3S cache."""

    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    try:
        temporary.unlink(missing_ok=True)
        request = Request(
            url,
            headers={"Accept": "application/octet-stream", "Cache-Control": "no-cache"},
        )
        with urlopen(request, timeout=CFSV2_GRID_TIMEOUT_SECONDS) as response, temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise C3SError(f"published CFSv2 numeric grid is empty: {url}")
        temporary.replace(destination)
    except C3SError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise C3SError(f"could not download published CFSv2 numeric grid {url}: {exc}") from exc
    return destination


def _published_cfsv2_candidates(
    payload: dict[str, Any], targets: dict[int, str]
) -> list[tuple[dict[str, Any], dict[int, dict[str, Any]]]]:
    """Return newest complete, baseline-applied CFSv2 snowfall runs first."""

    candidates: list[tuple[dict[str, Any], dict[int, dict[str, Any]]]] = []
    for run in payload.get("runs", []):
        if not isinstance(run, dict) or run.get("product") != "snowfall_anomaly":
            continue
        if run.get("status") not in {"rendered", "decoded", "partial"}:
            continue
        by_target: dict[str, dict[str, Any]] = {}
        for target_entry in run.get("targets", []):
            if not isinstance(target_entry, dict):
                continue
            target = str(target_entry.get("target_month", ""))
            if target in targets.values() and target not in by_target:
                baseline = target_entry.get("baseline") or {}
                lwe_asset = _published_cfsv2_lwe_asset(target_entry)
                if (
                    target_entry.get("status") in {"rendered", "decoded", "partial"}
                    and target_entry.get("field") == "snowfall_lwe"
                    and target_entry.get("units") == "in"
                    and lwe_asset is not None
                    and baseline.get("status") == "applied"
                ):
                    by_target[target] = target_entry
        if len(by_target) != len(set(targets.values())):
            continue
        entries_by_lead = {
            lead: by_target[target]
            for lead, target in targets.items()
        }
        candidates.append((run, entries_by_lead))
    candidates.sort(
        key=lambda candidate: (
            str(candidate[0].get("init_utc", "")),
            str(candidate[0].get("generated_utc", "")),
            str(candidate[0].get("id", "")),
        ),
        reverse=True,
    )
    return candidates


def load_published_cfsv2_snowfall(
    *,
    targets: dict[int, str],
    cache_dir: Path,
    manifest_url: str,
    repo_root: Path,
) -> tuple[dict[int, Grid], dict[str, Any]]:
    """Load CFSv2's published LWE departures for the C3S blend.

    The CFSv2 snowfall workflow has already done the ensemble and matched
    baseline work.  This adapter intentionally consumes its numeric LWE grid
    directly: no metre conversion, month-length multiplication, or 10:1 snow
    ratio is applied here.  The shared C3S renderer applies the 10:1 display
    conversion once, after the seven-source blend is formed.
    """

    if not targets:
        raise C3SError("CFSv2 snowfall source was requested without target months")
    payload = _fetch_json(manifest_url)
    candidates = _published_cfsv2_candidates(payload, targets)
    if not candidates:
        requested = ", ".join(f"lead {lead}={target}" for lead, target in targets.items())
        raise C3SError(
            "published CFSv2 manifest has no complete baseline-applied snowfall run for "
            f"{requested}"
        )

    errors: list[str] = []
    for run, entries_by_lead in candidates:
        run_id = str(run.get("id", "unknown"))
        grids: dict[int, Grid] = {}
        target_provenance: dict[str, Any] = {}
        try:
            for lead, target_entry in entries_by_lead.items():
                target = targets[lead]
                lwe_asset = _published_cfsv2_lwe_asset(target_entry)
                if lwe_asset is None:
                    raise C3SError(f"published CFSv2 target has no LWE grid for {target}")
                source_url = _published_cfsv2_asset_url(
                    manifest_url, lwe_asset[0]
                )
                cached_path = cache_dir / "cfsv2-published" / run_id / f"{target}.snow.csv.gz"
                grid = read_grid_state(_download_published_grid(source_url, cached_path))
                if not grid.lons or not grid.lats:
                    raise C3SError(f"published CFSv2 grid has no usable axes for {target}")
                grids[lead] = grid
                target_provenance[target] = {
                    "run_id": run_id,
                    "init_utc": run.get("init_utc"),
                    "source_url": manifest_url,
                    "numeric_grid_url": source_url,
                    "source_file": relative_path(cached_path, repo_root),
                    "grid_role": lwe_asset[1],
                    "field": target_entry.get("field"),
                    "units": target_entry.get("units"),
                    "baseline": target_entry.get("baseline"),
                    "derivation": target_entry.get("derivation"),
                }
            first_target = next(iter(target_provenance))
            first_baseline = target_provenance[first_target]["baseline"]
            provenance = {
                "component": CFSV2_COMPONENT,
                "component_label": CFSV2_COMPONENT_LABEL,
                "model": "NOAA CFSv2",
                "source": "published corrected CFSv2 snowfall departure",
                "source_url": manifest_url,
                "run_id": run_id,
                "init_utc": run.get("init_utc"),
                "field": "snowfall_lwe",
                "units": "in",
                "quantity": "snowfall liquid-water-equivalent departure",
                "baseline": {
                    "status": "applied",
                    "method": first_baseline.get("method"),
                    "source": first_baseline.get("source"),
                    "years": first_baseline.get("years"),
                },
                "conversion": (
                    "published values are already inches LWE; blend unchanged; "
                    "shared renderer applies ×10 once for estimated snow depth"
                ),
                "targets": target_provenance,
            }
            return grids, provenance
        except Exception as exc:
            errors.append(f"{run_id}: {exc}")

    raise C3SError(
        "could not load any complete published CFSv2 snowfall run: " + "; ".join(errors)
    )


def parse_system_overrides(value: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        if "=" not in item:
            raise C3SError("--systems must use centre=system pairs")
        centre, system = (part.strip() for part in item.split("=", 1))
        if centre not in CENTRES or not system.isdigit():
            raise C3SError(f"invalid C3S system override: {item}")
        overrides[centre] = system
    return overrides


def cds_area(product: dict[str, Any]) -> list[float]:
    if product.get("projection") == "north_polar_stereographic":
        return list(NORTHERN_HEMISPHERE_AREA)
    return list(CONUS_AREA if product["region"] == CONUS_REGION else NORTH_AMERICA_AREA)


def dataset_url(dataset: str) -> str:
    return f"https://cds.climate.copernicus.eu/datasets/{dataset}"


def convert_product_grid(grid: Grid, product: dict[str, Any], target: str) -> Grid:
    variable = product["variable"]
    factor = 1.0
    if variable == "z500":
        factor = 1.0 / GEOPOTENTIAL_GRAVITY
    elif variable == "pr":
        factor = month_seconds(target) * M_TO_INCH
    elif variable == "sf":
        factor = month_seconds(target) * M_TO_INCH
    elif variable == "slp":
        factor = 0.01
    if factor == 1.0:
        return grid
    return Grid(grid.lons[:], grid.lats[:], [[value * factor for value in row] for row in grid.values])


class CDSArchive:
    def __init__(self, cache_dir: Path, centre: str, system: str):
        self.cache_dir = cache_dir
        self.centre = centre
        self.system = system
        self._client: Any | None = None

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import cdsapi
        except ImportError as exc:
            raise C3SError("C3S rendering requires cdsapi>=0.7.7") from exc
        try:
            url = os.environ.get("CDS_API_URL", CDS_API_ROOT)
            key = os.environ.get("CDS_API_KEY", "").strip()
            options = client_options()
            self._client = (
                cdsapi.Client(url=url, key=key, quiet=True, **options)
                if key
                else cdsapi.Client(quiet=True, **options)
            )
        except Exception as exc:
            raise C3SError(f"could not initialize the CDS API client: {exc}") from exc
        return self._client

    def decoded_grid_path(
        self,
        product: dict[str, Any],
        init: str,
        lead: int,
        *,
        raw: bool = False,
    ) -> Path:
        """Return the compact decoded-grid cache used by later super ensembles."""

        tag = "raw" if raw else "anom"
        product_name = str(product["name"]).replace("-", "_")
        if product["name"] == "snowfall_anomaly":
            product_name += "_valid_month_v2"
        safe = f"{self.centre}_{self.system}_{product_name}_{init[:6]}_{tag}_l{lead:02d}".replace("-", "_")
        return self.cache_dir / "decoded" / safe / "field.csv.gz"

    def _cached_grid(
        self,
        product: dict[str, Any],
        init: str,
        lead: int,
        *,
        raw: bool = False,
    ) -> Grid | None:
        path = self.decoded_grid_path(product, init, lead, raw=raw)
        if not path.exists() or path.stat().st_size == 0:
            return None
        try:
            return read_grid_state(path)
        except Exception as exc:
            # A partial cache entry must never hide a usable source download.
            path.unlink(missing_ok=True)
            print(f"discarding unreadable C3S decoded cache {path}: {exc}", file=sys.stderr)
            return None

    def _save_grid(self, grid: Grid, path: Path) -> None:
        try:
            write_grid_state(grid, path)
        except Exception as exc:
            # Cache acceleration is best-effort; the forecast render remains
            # valid even when a runner cannot write its cache.
            print(f"could not save C3S decoded cache {path}: {exc}", file=sys.stderr)

    def retrieve_path(self, product: dict[str, Any], init: str, lead: int, *, raw: bool = False) -> Path:
        dataset = product["cds_raw_dataset"] if raw else product["cds_dataset"]
        variable = product["cds_raw_variable"] if raw else product["cds_variable"]
        tag = "raw" if raw else "anom"
        safe = f"{self.centre}_{self.system}_{dataset}_{variable}_{init[:6]}_{tag}_l{lead:02d}".replace("-", "_")
        return self.cache_dir / "cds" / safe / "field.grib"

    def retrieve(self, product: dict[str, Any], init: str, lead: int, *, raw: bool = False) -> Path:
        dataset = product["cds_raw_dataset"] if raw else product["cds_dataset"]
        variable = product["cds_raw_variable"] if raw else product["cds_variable"]
        pressure = product.get("cds_pressure_level")
        path = self.retrieve_path(product, init, lead, raw=raw)
        if path.exists() and path.stat().st_size > 0:
            return path
        request: dict[str, Any] = {
            "originating_centre": self.centre,
            "system": self.system,
            "variable": [variable],
            "product_type": ["monthly_mean" if raw else "ensemble_mean"],
            "year": [init[:4]], "month": [init[4:6]], "leadtime_month": [str(lead)],
            "area": cds_area(product), "data_format": "grib",
        }
        if pressure:
            request["pressure_level"] = [pressure]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("field.grib.tmp")
        try:
            temporary.unlink(missing_ok=True)
            retrieve_with_queue_retry(
                lambda: self.client().retrieve(dataset, request, str(temporary)),
                label=f"C3S {self.centre}/{self.system} {variable} lead {lead}",
            )
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise C3SError(f"CDS returned no data for {self.centre}/{self.system} {variable} lead {lead}")
            temporary.replace(path)
        except C3SError:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if "required licen" in str(exc).lower():
                raise C3SError(f"accept the current C3S dataset terms at {LICENSE_URL} and retry") from exc
            raise C3SError(f"C3S request failed for {self.centre}/{self.system} {variable} lead {lead}: {exc}") from exc
        return path

    def grid(self, product: dict[str, Any], init: str, target: str, lead: int) -> tuple[Grid, Path]:
        if product["name"] == "snowfall_anomaly":
            if target != target_month(init, lead):
                raise C3SError("Snowfall target and initialization lead disagree")
            # Dashboard leads are zero-based; CDS forecastMonth 1 is the
            # initialization month. August leads 4/5 require CDS months 5/6.
            lead += 1
            if not 1 <= lead <= 6:
                raise C3SError(f"Native monthly snowfall ends {target_month(init, 5)} for initialization {init[:6]}; {target} is unavailable")
        cached = self._cached_grid(product, init, lead)
        if cached is not None:
            return cached, self.retrieve_path(product, init, lead)
        path = self.retrieve(product, init, lead)
        try:
            grid = grid_from_grib(path, product, target, lead)
            self._save_grid(grid, self.decoded_grid_path(product, init, lead))
            return grid, path
        except Exception as exc:
            raise C3SError(f"could not decode C3S {self.centre}/{self.system} {path.name}: {exc}") from exc

    def height(self, product: dict[str, Any], init: str, target: str, lead: int) -> tuple[Grid, Path]:
        cached = self._cached_grid(product, init, lead, raw=True)
        if cached is not None:
            return cached, self.retrieve_path(product, init, lead, raw=True)
        path = self.retrieve(product, init, lead, raw=True)
        try:
            grid = grid_from_grib(path, {**product, "variable": "z500"}, target, lead)
            self._save_grid(grid, self.decoded_grid_path(product, init, lead, raw=True))
            return grid, path
        except Exception as exc:
            raise C3SError(f"could not decode C3S raw geopotential {path.name}: {exc}") from exc


def product_spec(
    product: str,
    label: str,
    *,
    multisystem: bool = False,
    includes_cfsv2: bool = False,
) -> dict[str, Any]:
    base = dict(PRODUCT_SPECS[product])
    prefix = "C3S multi-system" if multisystem else f"C3S {label}"
    subject = {
        "500mb_height_anomaly": "500-mb Geopotential Height & Anomaly (m)",
        "500mb_height_anomaly_nh": "Northern Hemisphere 500-mb Geopotential Height & Anomaly (m)",
        "850mb_temperature_anomaly": "850-mb Temperature Anomaly (°C)",
        "2m_temperature_anomaly": "2-m Temperature Anomaly (°C)",
        "precipitation_anomaly": "CONUS Precipitation Anomaly (in)",
        "snowfall_anomaly": "Snowfall Departure",
        "mslp_anomaly": "Mean Sea-Level Pressure Anomaly (hPa)",
    }[product]
    base["title"] = f"{prefix} {subject}"
    if product == "snowfall_anomaly":
        # The long provider name and repeated CONUS/unit wording caused the
        # valid-period label to collide with the title on the 1080px canvas.
        # Keep the full centre name in the metadata/detail line, but use the
        # compact operational abbreviation in the image title.
        title_label = "multi-system" if multisystem else {
            "UK Met Office": "UKMO",
            "Météo-France": "Météo-France",
        }.get(label, label)
        base["title"] = f"C3S {title_label} Snowfall Departure"
    base["absolute_title"] = base["title"].replace(" & Anomaly", "")
    base["includes_cfsv2"] = bool(includes_cfsv2 and product == "snowfall_anomaly" and multisystem)
    base["source_label"] = (
        "Copernicus C3S + NOAA CFSv2 / multi-system"
        if base["includes_cfsv2"]
        else f"Copernicus C3S / {('multi-system' if multisystem else label)}"
    )
    detail = (
        "Height contours in dam"
        if base["height_contours"]
        else "Official snowfall departure  •  LWE  •  CONUS  •  {snowfall_scale_label}"
        if product == "snowfall_anomaly"
        else f"{base['units']} anomaly"
    )
    if product == "snowfall_anomaly":
        base["header_detail"] = (
            "{source_label}  •  Native C3S + corrected CFSv2 snowfall departure  •  " + detail
            if base["includes_cfsv2"]
            else "{source_label}  •  Official postprocessed snowfall departure  •  " + detail
        )
    else:
        base["header_detail"] = "{source_label}  •  Native C3S postprocessed anomaly  •  " + detail
    return base


def render_target(
    grid: Grid,
    product: dict[str, Any],
    init: str,
    target: str,
    lead: int | str,
    output: Path,
    borders: list[Path],
    height: Grid | None,
    ensemble_label: str,
    period: str = "",
    seasonal: bool = False,
) -> None:
    # C3S publishes snowfall departures as liquid-water equivalent. Convert
    # that comparison field once for the image and pass the canonical
    # aggregation-based snow-depth contract to the shared renderer/sidecars.
    if product.get("name") == "snowfall_anomaly":
        grid, product = depth_departure(
            grid, product, SNOWFALL_ANOMALY_PALETTE, seasonal=seasonal
        )
    baseline_label = (
        "C3S native + NOAA CFSv2 published snowfall departure"
        if product.get("includes_cfsv2")
        else "C3S native postprocessed anomaly"
    )
    render_map(
        grid, init, target, lead, list(range(max(1, int(str(lead).split("–")[0])))), output,
        anomaly=True, baseline_label=baseline_label, border_paths=borders,
        period_label=period, height_grid=height, ensemble_label=ensemble_label,
        product_spec=product, seasonal=seasonal,
    )


def base_run_entry(
    component: str,
    label: str,
    system: str,
    product: dict[str, Any],
    product_name: str,
    init: str,
    members: int | None,
    multisystem: bool,
    centres: list[str],
    includes_cfsv2: bool = False,
    cfsv2_manifest_url: str = CFSV2_PUBLISHED_MANIFEST_URL,
) -> dict[str, Any]:
    source_datasets = [product["cds_dataset"]]
    if product["height_contours"]:
        source_datasets.append(product["cds_raw_dataset"])
    mixed_snowfall = bool(includes_cfsv2 and multisystem and product_name == "snowfall_anomaly")
    return {
        "id": f"c3s-{component}-{init}-{product_name}",
        "model": "C3S multi-system + CFSv2" if mixed_snowfall else ("C3S multi-system" if multisystem else f"C3S {label}"),
        "component": component,
        "component_label": label,
        "components": centres if multisystem else [component],
        "originating_centre": "multi-system" if multisystem else component,
        "system": system if not multisystem else "multiple",
        "source": (
            "Copernicus C3S + NOAA CFSv2 / multi-system"
            if mixed_snowfall
            else f"Copernicus C3S / {label if not multisystem else 'multi-system'}"
        ),
        "source_url": dataset_url(product["cds_dataset"]),
        "source_urls": [dataset_url(dataset) for dataset in source_datasets]
        + ([cfsv2_manifest_url] if mixed_snowfall else []),
        "archive_root": CDS_API_ROOT,
        "source_datasets": source_datasets,
        "model_version": CENTRES.get(component, {}).get("model_version") if not multisystem else (
            "C3S multi-system + NOAA CFSv2" if mixed_snowfall else "C3S multi-system"
        ),
        "product": product_name,
        "variable": product["variable"],
        "init_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
        "statistic": (
            "equal-weight mean of native C3S and published CFSv2 snowfall departures"
            if mixed_snowfall
            else "multi-system mean of native ensemble-mean anomalies" if multisystem else "ensemble_mean"
        ),
        "ensemble_scope": (
            "C3S native systems + NOAA CFSv2 blend"
            if mixed_snowfall
            else "C3S multi-system blend" if multisystem else f"{label}/System {system} ensemble"
        ),
        "ensemble_members": members if not multisystem else None,
        "aggregation": (
            "equal-weight source mean of monthly LWE departures"
            if mixed_snowfall else "official C3S monthly ensemble-mean anomaly"
        ),
        "field": product["field"], "units": product["units"],
        "raw_field": product["raw_field"], "raw_units": product["raw_units"],
        "baseline": (
            {"status": "mixed_native_and_published", "source": "C3S native bias-adjusted anomaly + corrected NOAA CFSv2 snowfall departure"}
            if mixed_snowfall
            else {"status": "official_postprocessed", "source": "C3S native bias-adjusted anomaly"}
        ),
        "climatology": {"status": "not_used", "method": "native C3S postprocessed anomaly"},
        "targets": [], "status": "planned",
    }


def build_run(
    *, component: str, label: str, system: str, product_name: str, product: dict[str, Any], init: str,
    leads: list[int], seasonal_leads: list[int], archive: CDSArchive | None,
    lead_grids: dict[int, Grid], lead_heights: dict[int, Grid], output_dir: Path,
    borders: list[Path], members: int | None, multisystem: bool, centres: list[str], decode_only: bool,
    component_names_by_lead: dict[int, list[str]] | None = None,
    seasonal_component_names: list[str] | None = None,
    seasonal_grid_override: Grid | None = None,
    seasonal_height_override: Grid | None = None,
    includes_cfsv2: bool = False,
    component_sources_by_lead: dict[int, dict[str, Any]] | None = None,
    source_components: dict[str, Any] | None = None,
    cfsv2_manifest_url: str = CFSV2_PUBLISHED_MANIFEST_URL,
) -> tuple[dict[str, Any], int]:
    entry = base_run_entry(
        component, label, system, product, product_name, init, members, multisystem, centres,
        includes_cfsv2=includes_cfsv2,
        cfsv2_manifest_url=cfsv2_manifest_url,
    )
    if source_components:
        entry["source_components"] = source_components
    component_names_by_lead = component_names_by_lead or {}
    failures = 0
    for lead in leads:
        target = target_month(init, lead)
        available_components = component_names_by_lead.get(lead, centres) if multisystem else [component]
        target_entry: dict[str, Any] = {
            "id": f"{entry['id']}-lead{lead:02d}", "target_month": target,
            "valid_start_utc": target_period(target)[0], "valid_end_utc": target_period(target)[1],
            "lead_month": lead, "field": product["field"], "units": product["units"],
            "statistic": entry["statistic"], "status": "planned",
        }
        if multisystem:
            target_entry["available_components"] = available_components
            target_entry["component_count"] = len(available_components)
            if component_sources_by_lead and component_sources_by_lead.get(lead):
                target_entry["component_sources"] = component_sources_by_lead[lead]
        try:
            if lead not in lead_grids:
                if archive is None:
                    raise C3SError("multi-system blend has no component grid for this lead")
                forecast, source_path = archive.grid(product, init, target, lead)
                lead_grids[lead] = forecast
                target_entry["source_file"] = relative_path(source_path, Path(__file__).resolve().parents[1])
                if product["height_contours"] and not decode_only:
                    height, _ = archive.height(product, init, target, lead)
                    lead_heights[lead] = height
            target_entry["quality_control"] = grid_quality_control(
                product_name,
                lead_grids[lead].values,
                units=product["units"],
                field=product["field"],
                seasonal=False,
            )
            require_quality_control(target_entry["quality_control"], C3SError)
            if decode_only:
                target_entry["status"] = "decoded"
            else:
                output = output_dir / init[:8] / f"c3s_{component}_{product['variable']}_{target}.jpg"
                ensemble_label = (
                    f"{members or len(centres)}-member mean"
                    if not multisystem
                    else f"{len(available_components)}-system mean"
                )
                render_target(lead_grids[lead], product, init, target, lead, output, borders, lead_heights.get(lead), ensemble_label)
                target_entry["image"] = relative_path(output, Path(__file__).resolve().parents[1])
                target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"C3S {component} target {target} failed: {exc}", file=sys.stderr)
        entry["targets"].append(target_entry)

    if seasonal_leads and not decode_only:
        first, last = seasonal_leads[0], seasonal_leads[-1]
        first_target, last_target = target_month(init, first), target_month(init, last)
        target_entry = {
            "id": f"{entry['id']}-{first_target}-{last_target}", "target_month": f"{first_target}-{last_target}",
            "valid_start_utc": target_period(first_target)[0], "valid_end_utc": target_period(last_target)[1],
            "lead_month": f"{first}–{last}", "monthly_leads": seasonal_leads,
            "field": product["field"], "units": product["seasonal_units"],
            "statistic": entry["statistic"], "status": "planned",
        }
        if multisystem:
            complete_components = seasonal_component_names if seasonal_component_names is not None else centres
            target_entry["available_components"] = complete_components
            target_entry["component_count"] = len(complete_components)
        try:
            if multisystem and seasonal_component_names is not None and not seasonal_component_names:
                raise C3SError("no C3S system supplied every month in the seasonal window")
            combine = sum_grids if product["seasonal_reducer"] == "sum" else mean_grids
            if seasonal_grid_override is not None:
                seasonal_grid = seasonal_grid_override
            else:
                if any(lead not in lead_grids for lead in seasonal_leads):
                    raise C3SError("seasonal window is missing one or more component grids")
                seasonal_grid = combine([lead_grids[lead] for lead in seasonal_leads])
            target_entry["quality_control"] = grid_quality_control(
                product_name,
                seasonal_grid.values,
                units=product["seasonal_units"],
                field=product["field"],
                seasonal=True,
            )
            require_quality_control(target_entry["quality_control"], C3SError)
            seasonal_height = seasonal_height_override
            if seasonal_height is None and product["height_contours"] and all(lead in lead_heights for lead in seasonal_leads):
                seasonal_height = combine([lead_heights[lead] for lead in seasonal_leads])
            output = output_dir / init[:8] / f"c3s_{component}_{product['variable']}_{first_target}-{last_target}.jpg"
            ensemble_label = (
                f"{members or len(centres)}-member mean"
                if not multisystem
                else f"{len(complete_components)}-system mean"
            )
            render_target(seasonal_grid, product, init, first_target, f"{first}–{last}", output, borders, seasonal_height, ensemble_label, period_label(first_target, last_target), seasonal=True)
            target_entry["image"] = relative_path(output, Path(__file__).resolve().parents[1])
            target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"C3S {component} seasonal window failed: {exc}", file=sys.stderr)
        entry["targets"].append(target_entry)
    statuses = [target["status"] for target in entry["targets"]]
    entry["status"] = "failed" if statuses and all(status == "failed" for status in statuses) else ("partial" if failures else ("decoded" if decode_only else "rendered"))
    entry["output_dir"] = relative_path(output_dir, Path(__file__).resolve().parents[1])
    return entry, failures


def write_manifest(path: Path, entries: Iterable[dict[str, Any]], previous: Path | None, retain_cycles: int) -> None:
    all_entries: list[dict[str, Any]] = []
    seen_manifests: set[Path] = set()
    for candidate in (previous, path):
        if not candidate or not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen_manifests:
            continue
        seen_manifests.add(resolved)
        try:
            old = json.loads(candidate.read_text(encoding="utf-8"))
            all_entries.extend(
                run for run in old.get("runs", [])
                if isinstance(run, dict) and not is_retired_product(run.get("product"))
            )
        except (OSError, ValueError) as exc:
            raise C3SError(f"could not read C3S manifest {candidate}: {exc}") from exc
    current_entries = [
        run for run in entries
        if isinstance(run, dict) and not is_retired_product(run.get("product"))
    ]
    all_entries.extend(current_entries)

    # A refreshed multi-system run is authoritative for its current cycle.
    # Remove stale component rows that were requested by an older, broader
    # centre list (for example NCEP/JMA/BOM snowfall rows) so the dashboard
    # cannot expose failed members beside the repaired blend.
    for blend in current_entries:
        if blend.get("component") != "multisystem":
            continue
        requested = set(blend.get("components") or blend.get("requested_components") or [])
        synthetic = set(blend.get("synthetic_components") or [])
        if not requested:
            requested = synthetic
        elif synthetic:
            requested.update(synthetic)
        product_name = blend.get("product")
        init_utc = blend.get("init_utc")
        all_entries = [
            run for run in all_entries
            if not (
                run.get("product") == product_name
                and run.get("init_utc") == init_utc
                and run.get("component") != "multisystem"
                and (
                    run.get("component") not in requested
                    or run.get("component") in synthetic
                )
            )
        ]

    cfsv2_urls = sorted(
        {
            str(source_url)
            for run in current_entries
            if run.get("product") == "snowfall_anomaly"
            and CFSV2_COMPONENT in set(run.get("components") or [])
            for source_url in run.get("source_urls", [])
            if "cfsv2_manifest" in str(source_url)
        }
    )
    unique: dict[str, dict[str, Any]] = {str(run.get("id")): run for run in all_entries if run.get("id")}
    ordered = sorted(unique.values(), key=lambda run: (str(run.get("init_utc", "")), str(run.get("id", ""))), reverse=True)
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:max(1, retain_cycles)])
    payload = {
        "schema_version": 1, "kind": "c3s_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)), "source": "Copernicus C3S seasonal forecasts",
        "source_url": SOURCE_URL,
        "source_urls": [
            SOURCE_URL,
            PRESSURE_SOURCE_URL,
            SINGLE_SOURCE_URL,
            *cfsv2_urls,
        ],
        "product_labels": {
            key: {
                "500mb_height_anomaly": "500-mb Height Anomaly",
                "500mb_height_anomaly_nh": "500-mb Height Anomaly · Northern Hemisphere",
                "850mb_temperature_anomaly": "850-mb Temperature Anomaly",
                "2m_temperature_anomaly": "2-m Temperature Anomaly",
                "precipitation_anomaly": "Precipitation Anomaly",
                "snowfall_anomaly": "Snowfall Departure",
                "mslp_anomaly": "MSLP Anomaly",
            }.get(key, key)
            for key in PRODUCT_SPECS
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
    parser.add_argument("--product", choices=tuple(PRODUCT_SPECS), default="500mb_height_anomaly")
    parser.add_argument("--centres", default="all", help="comma-separated C3S centre IDs or all")
    parser.add_argument("--systems", default="", help="optional centre=system overrides")
    parser.add_argument("--init", default="latest")
    parser.add_argument("--lead-months", default="3,4,5")
    parser.add_argument("--seasonal-window", default="3,4,5")
    parser.add_argument("--cache-dir", default=".cache/c3s")
    parser.add_argument("--output-dir", default="public/seasonal/c3s")
    parser.add_argument("--manifest", default="public/seasonal/c3s_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument(
        "--cfsv2-manifest-url",
        default=CFSV2_PUBLISHED_MANIFEST_URL,
        help="published CFSv2 manifest used as the NCEP snowfall blend input",
    )
    parser.add_argument(
        "--no-cfsv2",
        action="store_true",
        help="do not include the published CFSv2 snowfall source in the blend",
    )
    parser.add_argument("--no-components", action="store_true", help="publish only the C3S multi-system blend")
    parser.add_argument("--no-blend", action="store_true", help="publish only the selected C3S component entries")
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--decode-only", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    product_name = args.product
    product = PRODUCT_SPECS[product_name]
    centres = parse_centres(args.centres, args.product)
    overrides = parse_system_overrides(args.systems)
    systems = {centre: overrides.get(centre, str(CENTRES[centre]["system"])) for centre in centres}
    init = parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 1, 6)
    seasonal = parse_int_list(args.seasonal_window, "seasonal window", 1, 6) if args.seasonal_window else []
    if seasonal:
        expected = list(range(min(seasonal), max(seasonal) + 1))
        if seasonal != expected:
            raise C3SError("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal))
    repo_root = Path(__file__).resolve().parents[1]
    cache_dir = Path(args.cache_dir) if Path(args.cache_dir).is_absolute() else repo_root / args.cache_dir
    output_dir = Path(args.output_dir) if Path(args.output_dir).is_absolute() else repo_root / args.output_dir
    manifest_path = Path(args.manifest) if Path(args.manifest).is_absolute() else repo_root / args.manifest
    previous = None
    if args.previous_manifest:
        previous = args.previous_manifest if args.previous_manifest.is_absolute() else repo_root / args.previous_manifest
    borders = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)
    entries: list[dict[str, Any]] = []
    component_grids: dict[str, dict[int, Grid]] = {}
    component_heights: dict[str, dict[int, Grid]] = {}
    failures = 0
    for centre in centres:
        label = CENTRES[centre]["label"]
        archive = CDSArchive(cache_dir, centre, systems[centre])
        lead_grids: dict[int, Grid] = {}
        lead_heights: dict[int, Grid] = {}
        if not args.no_components:
            entry, count = build_run(component=centre, label=label, system=systems[centre], product_name=product_name, product=product_spec(product_name, label), init=init, leads=leads, seasonal_leads=seasonal, archive=archive, lead_grids=lead_grids, lead_heights=lead_heights, output_dir=output_dir, borders=borders, members=int(CENTRES[centre]["members"]), multisystem=False, centres=centres, decode_only=args.decode_only)
            entries.append(entry)
            failures += count
        else:
            # The blend still needs each component field; --no-components
            # suppresses only the individual rendered entries.
            for lead in leads:
                target = target_month(init, lead)
                try:
                    lead_grids[lead], _ = archive.grid(product, init, target, lead)
                    if product["height_contours"] and not args.decode_only:
                        lead_heights[lead], _ = archive.height(product, init, target, lead)
                except Exception as exc:
                    print(f"C3S {centre} blend input lead {lead} unavailable: {exc}", file=sys.stderr)
        component_grids[centre] = lead_grids
        component_heights[centre] = lead_heights

    cfsv2_requested = (
        product_name == "snowfall_anomaly"
        and not getattr(args, "no_cfsv2", False)
        and not args.no_blend
    )
    requested_blend_centres = list(centres)
    if cfsv2_requested:
        requested_blend_centres.append(CFSV2_COMPONENT)
    blend_centres = list(centres)
    cfsv2_provenance: dict[str, Any] | None = None
    cfsv2_sources_by_lead: dict[int, dict[str, Any]] = {}
    cfsv2_error: str | None = None
    if cfsv2_requested:
        try:
            cfsv2_targets = {lead: target_month(init, lead) for lead in leads}
            cfsv2_grids, cfsv2_provenance = load_published_cfsv2_snowfall(
                targets=cfsv2_targets,
                cache_dir=cache_dir,
                manifest_url=getattr(args, "cfsv2_manifest_url", CFSV2_PUBLISHED_MANIFEST_URL),
                repo_root=repo_root,
            )
            component_grids[CFSV2_COMPONENT] = cfsv2_grids
            component_heights[CFSV2_COMPONENT] = {}
            blend_centres.append(CFSV2_COMPONENT)
            cfsv2_sources_by_lead = {
                lead: {CFSV2_COMPONENT: cfsv2_provenance["targets"][target]}
                for lead, target in cfsv2_targets.items()
            }
            print(
                f"Loaded published CFSv2 snowfall departure {cfsv2_provenance['run_id']} "
                f"as the {CFSV2_COMPONENT} C3S blend source"
            )
        except C3SError as exc:
            cfsv2_error = str(exc)
            print(f"CFSv2 snowfall blend source unavailable: {exc}", file=sys.stderr)

    blend_failures = 0
    if not args.no_blend:
        blend_grids: dict[int, Grid] = {}
        blend_heights: dict[int, Grid] = {}
        component_names_by_lead: dict[int, list[str]] = {}
        for lead in leads:
            available_components = [centre for centre in blend_centres if lead in component_grids.get(centre, {})]
            component_names_by_lead[lead] = available_components
            available = [component_grids[centre][lead] for centre in available_components]
            if available:
                reference = available[0]
                # C3S systems normally share the one-degree axes.  If a centre
                # returns a different grid, nearest-neighbour alignment keeps the
                # blend explicit instead of silently dropping that component.
                from cfsv2_seasonal import regrid_nearest
                blend_grids[lead] = mean_grids([regrid_nearest(grid, reference.lons, reference.lats, "C3S blend") for grid in available])
            heights = [component_heights[centre][lead] for centre in blend_centres if lead in component_heights.get(centre, {})]
            if heights:
                reference = heights[0]
                from cfsv2_seasonal import regrid_nearest
                blend_heights[lead] = mean_grids([regrid_nearest(grid, reference.lons, reference.lats, "C3S height blend") for grid in heights])
        if blend_grids:
            from cfsv2_seasonal import regrid_nearest

            seasonal_components = [
                centre for centre in blend_centres
                if seasonal and all(lead in component_grids.get(centre, {}) for lead in seasonal)
            ]
            seasonal_grid_override = None
            seasonal_height_override = None
            if seasonal_components:
                combine = sum_grids if product["seasonal_reducer"] == "sum" else mean_grids
                # Every source is still in monthly LWE inches here, including
                # the published CFSv2 member.  Sum each source's months first,
                # then average source totals; the 10:1 display conversion is
                # intentionally deferred until render_target().
                component_seasonal_grids = [
                    combine([component_grids[centre][lead] for lead in seasonal])
                    for centre in seasonal_components
                ]
                reference = component_seasonal_grids[0]
                seasonal_grid_override = mean_grids([
                    regrid_nearest(grid, reference.lons, reference.lats, "C3S seasonal blend")
                    for grid in component_seasonal_grids
                ])
                seasonal_height_components = [
                    centre for centre in seasonal_components
                    if all(lead in component_heights.get(centre, {}) for lead in seasonal)
                ]
                if product["height_contours"] and seasonal_height_components:
                    component_seasonal_heights = [
                        combine([component_heights[centre][lead] for lead in seasonal])
                        for centre in seasonal_height_components
                    ]
                    height_reference = component_seasonal_heights[0]
                    seasonal_height_override = mean_grids([
                        regrid_nearest(grid, height_reference.lons, height_reference.lats, "C3S seasonal height blend")
                        for grid in component_seasonal_heights
                    ])
            entry, count = build_run(
                component="multisystem", label="multi-system", system="multiple",
                product_name=product_name,
                product=product_spec(
                    product_name,
                    "multi-system",
                    multisystem=True,
                    includes_cfsv2=cfsv2_provenance is not None,
                ),
                init=init, leads=leads, seasonal_leads=seasonal, archive=None,
                lead_grids=blend_grids, lead_heights=blend_heights, output_dir=output_dir,
                borders=borders, members=None, multisystem=True, centres=blend_centres,
                decode_only=args.decode_only, component_names_by_lead=component_names_by_lead,
                seasonal_component_names=seasonal_components,
                seasonal_grid_override=seasonal_grid_override,
                seasonal_height_override=seasonal_height_override,
                includes_cfsv2=cfsv2_provenance is not None,
                component_sources_by_lead=cfsv2_sources_by_lead,
                source_components=(
                    {CFSV2_COMPONENT: cfsv2_provenance}
                    if cfsv2_provenance is not None else None
                ),
                cfsv2_manifest_url=getattr(args, "cfsv2_manifest_url", CFSV2_PUBLISHED_MANIFEST_URL),
            )
            entry["requested_components"] = list(requested_blend_centres)
            entry["available_components"] = [centre for centre in blend_centres if component_grids.get(centre)]
            entry["components"] = entry["available_components"]
            entry["component_count"] = len(entry["available_components"])
            entry["component_count_by_lead"] = {
                str(lead): len(component_names_by_lead.get(lead, [])) for lead in leads
            }
            if product_name == "snowfall_anomaly":
                # NCEP is represented by the published CFSv2 adapter above,
                # not by a separate native C3S CDS component row.
                entry["synthetic_components"] = [CFSV2_COMPONENT]
            if cfsv2_error:
                entry["component_errors"] = {CFSV2_COMPONENT: cfsv2_error}
            entries.append(entry)
            blend_failures += count
            if cfsv2_error and cfsv2_requested:
                blend_failures += 1
        else:
            print("C3S multi-system blend has no component fields", file=sys.stderr)
            blend_failures += 1
    write_manifest(manifest_path, entries, previous, args.retain_cycles)
    print(f"wrote C3S manifest: {manifest_path} ({len(entries)} run entries)")
    # A missing native centre should be visible in the manifest but should not
    # make a usable multi-system release impossible.  The explicitly required
    # CFSv2 snowfall handoff is handled as a hard failure just below.
    usable = any(entry.get("status") in {"rendered", "decoded", "partial"} for entry in entries)
    if cfsv2_error and cfsv2_requested:
        # Snowfall blends explicitly promise the CFSv2/NCEP member.  Do not
        # publish a six-source map under the seven-source contract when the
        # published CFSv2 handoff is unavailable.
        return 2
    return 0 if usable and not blend_failures else (0 if usable and entries else 2)


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except C3SError as exc:
        print(f"C3S ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
