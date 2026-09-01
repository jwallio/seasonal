#!/usr/bin/env python3
"""Fetch and render C3S multi-system and component seasonal guidance.

The Copernicus Climate Data Store exposes each contributing centre through the
same postprocessed seasonal datasets.  This adapter keeps those centre/system
choices in the manifest, renders the selected components, and also publishes a
transparent multi-system mean made from the native C3S anomaly fields.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from cfsv2_seasonal import (
    ANOMALY_PALETTE,
    ANOMALY_TICKS,
    CONUS_PRECIP_REGION,
    CONUS_STATE_NAMES,
    DEFAULT_REGION,
    Grid,
    SNOWFALL_ANOMALY_MAX_IN,
    SNOWFALL_ANOMALY_MIN_IN,
    SNOWFALL_ANOMALY_PALETTE,
    SNOWFALL_ANOMALY_TICK_DECIMALS,
    SNOWFALL_ANOMALY_TICK_FORMAT,
    SNOWFALL_ANOMALY_TICKS,
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
    ensure_border_files,
    mean_grids,
    relative_path,
    render_map,
    sum_grids,
)
from seas5_seasonal import grid_from_grib
from seasonal_products import grid_quality_control, require_quality_control


CDS_API_ROOT = "https://cds.climate.copernicus.eu/api"
PRESSURE_DATASET = "seasonal-postprocessed-pressure-levels"
SINGLE_DATASET = "seasonal-postprocessed-single-levels"
RAW_PRESSURE_DATASET = "seasonal-monthly-pressure-levels"
SOURCE_URL = "https://climate.copernicus.eu/seasonal-forecasts"
PRESSURE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels"
SINGLE_SOURCE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-single-levels"
LICENSE_URL = "https://cds.climate.copernicus.eu/datasets/seasonal-postprocessed-pressure-levels?tab=download#manage-licences"
NORTH_AMERICA_AREA = [90.0, -170.0, 15.0, 0.0]
CONUS_AREA = [60.0, -135.0, 20.0, -55.0]
GEOPOTENTIAL_GRAVITY = 9.80665
M_TO_INCH = 1000.0 / 25.4

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

MSLP_PALETTE = ANOMALY_PALETTE
SST_PALETTE = ["#28567f", "#5b9fba", "#b4d6dc", "#ffffff", "#efb6b5", "#b84c5a"]
PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f", "#86c879",
    "#5fba6b", "#3aa55b", "#1d8947", "#006d2c",
]

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "500mb_height_anomaly": {
        "name": "500mb_height_anomaly", "variable": "z500", "field": "z500_anomaly",
        "raw_field": "geopotential anomaly", "raw_units": "m² s⁻²", "units": "m",
        "seasonal_units": "m", "height_contours": True, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -100.0,
        "anomaly_max": 100.0, "anomaly_ticks": ANOMALY_TICKS, "anomaly_palette": ANOMALY_PALETTE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "geopotential_anomaly",
        "cds_pressure_level": "500", "cds_raw_dataset": RAW_PRESSURE_DATASET,
        "cds_raw_variable": "geopotential", "raw_field_name": "geopotential",
    },
    "850mb_temperature_anomaly": {
        "name": "850mb_temperature_anomaly", "variable": "t850", "field": "t850_anomaly",
        "raw_field": "temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C, "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "cds_dataset": PRESSURE_DATASET, "cds_variable": "temperature_anomaly",
        "cds_pressure_level": "850",
    },
    "2m_temperature_anomaly": {
        "name": "2m_temperature_anomaly", "variable": "t2m", "field": "t2m_anomaly",
        "raw_field": "2-m temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": TEMPERATURE_ANOMALY_MIN_C,
        "anomaly_max": TEMPERATURE_ANOMALY_MAX_C, "anomaly_ticks": TEMPERATURE_ANOMALY_TICKS, "anomaly_palette": TEMPERATURE_ANOMALY_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "2m_temperature_anomaly",
    },
    "precipitation_anomaly": {
        "name": "precipitation_anomaly", "variable": "pr", "field": "precipitation_anomaly",
        "raw_field": "total precipitation anomaly", "raw_units": "m s⁻¹", "units": "in",
        "seasonal_units": "in", "height_contours": False, "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total", "seasonal_reducer": "sum", "anomaly_min": -8.0,
        "anomaly_max": 8.0, "anomaly_ticks": list(range(-8, 9)), "anomaly_palette": PRECIP_PALETTE,
        "cds_dataset": SINGLE_DATASET,
        "cds_variable": "total_precipitation_anomalous_rate_of_accumulation",
    },
    "snowfall_anomaly": {
        "name": "snowfall_anomaly", "variable": "sf", "field": "snowfall_anomaly",
        "raw_field": "snowfall anomalous rate of accumulation",
        "raw_units": "m s⁻¹ of water equivalent", "units": "in", "seasonal_units": "in",
        "height_contours": False, "region": CONUS_PRECIP_REGION,
        "monthly_reducer": "total", "seasonal_reducer": "sum",
        "anomaly_min": SNOWFALL_ANOMALY_MIN_IN, "anomaly_max": SNOWFALL_ANOMALY_MAX_IN,
        "anomaly_ticks": SNOWFALL_ANOMALY_TICKS, "anomaly_palette": SNOWFALL_ANOMALY_PALETTE,
        "anomaly_tick_decimals": SNOWFALL_ANOMALY_TICK_DECIMALS,
        "anomaly_tick_format": SNOWFALL_ANOMALY_TICK_FORMAT,
        "cds_dataset": SINGLE_DATASET,
        "cds_variable": "snowfall_anomalous_rate_of_accumulation",
    },
    "sea_surface_temperature_anomaly": {
        "name": "sea_surface_temperature_anomaly", "variable": "sst", "field": "sst_anomaly",
        "raw_field": "sea-surface temperature anomaly", "raw_units": "K", "units": "°C",
        "seasonal_units": "°C", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -3.0,
        "anomaly_max": 3.0, "anomaly_ticks": list(range(-3, 4)), "anomaly_palette": SST_PALETTE,
        "map_domain": "ocean",
        "cds_dataset": SINGLE_DATASET, "cds_variable": "sea_surface_temperature_anomaly",
    },
    "mslp_anomaly": {
        "name": "mslp_anomaly", "variable": "slp", "field": "mslp_anomaly",
        "raw_field": "mean sea-level pressure anomaly", "raw_units": "Pa", "units": "hPa",
        "seasonal_units": "hPa", "height_contours": False, "region": DEFAULT_REGION,
        "monthly_reducer": "mean", "seasonal_reducer": "mean", "anomaly_min": -10.0,
        "anomaly_max": 10.0, "anomaly_ticks": list(range(-10, 11)), "anomaly_palette": MSLP_PALETTE,
        "cds_dataset": SINGLE_DATASET, "cds_variable": "mean_sea_level_pressure_anomaly",
    },
}


class C3SError(RuntimeError):
    """A user-actionable C3S source or rendering error."""


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_init(value: str) -> str:
    if value == "latest":
        now = dt.datetime.now(dt.timezone.utc)
        return f"{now.year:04d}{now.month:02d}0100"
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


def parse_centres(value: str) -> list[str]:
    names = list(CENTRES) if value.strip().lower() in {"all", "c3s", "multi-system"} else [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in CENTRES]
    if unknown:
        raise C3SError(f"unknown C3S centre(s): {', '.join(unknown)}")
    return list(dict.fromkeys(names))


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
    return list(CONUS_AREA if product["region"] == CONUS_PRECIP_REGION else NORTH_AMERICA_AREA)


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
            self._client = cdsapi.Client(url=url, key=key, quiet=True) if key else cdsapi.Client(quiet=True)
        except Exception as exc:
            raise C3SError(f"could not initialize the CDS API client: {exc}") from exc
        return self._client

    def retrieve(self, product: dict[str, Any], init: str, lead: int, *, raw: bool = False) -> Path:
        dataset = product["cds_raw_dataset"] if raw else product["cds_dataset"]
        variable = product["cds_raw_variable"] if raw else product["cds_variable"]
        pressure = product.get("cds_pressure_level")
        tag = "raw" if raw else "anom"
        safe = f"{self.centre}_{self.system}_{dataset}_{variable}_{init[:6]}_{tag}_l{lead:02d}".replace("-", "_")
        path = self.cache_dir / "cds" / safe / "field.grib"
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
            self.client().retrieve(dataset, request, str(temporary))
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
        path = self.retrieve(product, init, lead)
        try:
            return grid_from_grib(path, product, target, lead), path
        except Exception as exc:
            raise C3SError(f"could not decode C3S {self.centre}/{self.system} {path.name}: {exc}") from exc

    def height(self, product: dict[str, Any], init: str, target: str, lead: int) -> tuple[Grid, Path]:
        path = self.retrieve(product, init, lead, raw=True)
        try:
            return grid_from_grib(path, {**product, "variable": "z500"}, target, lead), path
        except Exception as exc:
            raise C3SError(f"could not decode C3S raw geopotential {path.name}: {exc}") from exc


def product_spec(product: str, label: str, *, multisystem: bool = False) -> dict[str, Any]:
    base = dict(PRODUCT_SPECS[product])
    prefix = "C3S multi-system" if multisystem else f"C3S {label}"
    subject = {
        "500mb_height_anomaly": "500-mb Geopotential Height & Anomaly (m)",
        "850mb_temperature_anomaly": "850-mb Temperature Anomaly (°C)",
        "2m_temperature_anomaly": "2-m Temperature Anomaly (°C)",
        "precipitation_anomaly": "CONUS Precipitation Anomaly (in)",
        "snowfall_anomaly": "Snowfall Departure",
        "sea_surface_temperature_anomaly": "Sea-Surface Temperature Anomaly (°C)",
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
    base["source_label"] = f"Copernicus C3S / {('multi-system' if multisystem else label)}"
    detail = (
        "Height contours in dam"
        if base["height_contours"]
        else "Official snowfall departure  •  LWE  •  CONUS  •  clipped at \u00b14.0 in"
        if product == "snowfall_anomaly"
        else f"{base['units']} anomaly"
    )
    if product == "snowfall_anomaly":
        base["header_detail"] = "{source_label}  •  Official postprocessed snowfall departure  •  " + detail
    else:
        base["header_detail"] = "{source_label}  •  Native C3S postprocessed anomaly  •  " + detail
    if product == "snowfall_anomaly":
        base.update(
            {
                "map_domain": "land",
                "fit_frame_to_domain": True,
                "domain_frame_padding_fraction": 0.012,
                "mask_states": list(CONUS_STATE_NAMES),
                "border_files": ("us-states.geojson",),
                "anomaly_endpoint_labels": {"minimum": "\u2264\u22124.0", "maximum": "\u2265+4.0"},
            }
        )
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
) -> None:
    render_map(
        grid, init, target, lead, list(range(max(1, int(str(lead).split("–")[0])))), output,
        anomaly=True, baseline_label="C3S native postprocessed anomaly", border_paths=borders,
        period_label=period, height_grid=height, ensemble_label=ensemble_label,
        product_spec=product,
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
) -> dict[str, Any]:
    source_datasets = [product["cds_dataset"]]
    if product["height_contours"]:
        source_datasets.append(product["cds_raw_dataset"])
    return {
        "id": f"c3s-{component}-{init}-{product_name}",
        "model": "C3S multi-system" if multisystem else f"C3S {label}",
        "component": component,
        "component_label": label,
        "components": centres if multisystem else [component],
        "originating_centre": "multi-system" if multisystem else component,
        "system": system if not multisystem else "multiple",
        "source": f"Copernicus C3S / {label if not multisystem else 'multi-system'}",
        "source_url": dataset_url(product["cds_dataset"]),
        "source_urls": [dataset_url(dataset) for dataset in source_datasets],
        "archive_root": CDS_API_ROOT,
        "source_datasets": source_datasets,
        "model_version": CENTRES.get(component, {}).get("model_version") if not multisystem else "C3S multi-system",
        "product": product_name,
        "variable": product["variable"],
        "init_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
        "statistic": "multi-system mean of native ensemble-mean anomalies" if multisystem else "ensemble_mean",
        "ensemble_scope": "C3S multi-system blend" if multisystem else f"{label}/System {system} ensemble",
        "ensemble_members": members if not multisystem else None,
        "aggregation": "official C3S monthly ensemble-mean anomaly",
        "field": product["field"], "units": product["units"],
        "raw_field": product["raw_field"], "raw_units": product["raw_units"],
        "baseline": {"status": "official_postprocessed", "source": "C3S native bias-adjusted anomaly"},
        "climatology": {"status": "not_used", "method": "native C3S postprocessed anomaly"},
        "targets": [], "status": "planned",
    }


def build_run(
    *, component: str, label: str, system: str, product_name: str, product: dict[str, Any], init: str,
    leads: list[int], seasonal_leads: list[int], archive: CDSArchive | None,
    lead_grids: dict[int, Grid], lead_heights: dict[int, Grid], output_dir: Path,
    borders: list[Path], members: int | None, multisystem: bool, centres: list[str], decode_only: bool,
) -> tuple[dict[str, Any], int]:
    entry = base_run_entry(component, label, system, product, product_name, init, members, multisystem, centres)
    failures = 0
    for lead in leads:
        target = target_month(init, lead)
        target_entry: dict[str, Any] = {
            "id": f"{entry['id']}-lead{lead:02d}", "target_month": target,
            "valid_start_utc": target_period(target)[0], "valid_end_utc": target_period(target)[1],
            "lead_month": lead, "field": product["field"], "units": product["units"],
            "statistic": entry["statistic"], "status": "planned",
        }
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
                render_target(lead_grids[lead], product, init, target, lead, output, borders, lead_heights.get(lead), f"{members or len(centres)}-member mean" if not multisystem else f"{len(centres)}-system mean")
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
        try:
            if any(lead not in lead_grids for lead in seasonal_leads):
                raise C3SError("seasonal window is missing one or more component grids")
            combine = sum_grids if product["seasonal_reducer"] == "sum" else mean_grids
            seasonal_grid = combine([lead_grids[lead] for lead in seasonal_leads])
            target_entry["quality_control"] = grid_quality_control(
                product_name,
                seasonal_grid.values,
                units=product["seasonal_units"],
                field=product["field"],
                seasonal=True,
            )
            require_quality_control(target_entry["quality_control"], C3SError)
            seasonal_height = combine([lead_heights[lead] for lead in seasonal_leads]) if product["height_contours"] and all(lead in lead_heights for lead in seasonal_leads) else None
            output = output_dir / init[:8] / f"c3s_{component}_{product['variable']}_{first_target}-{last_target}.jpg"
            render_target(seasonal_grid, product, init, first_target, f"{first}–{last}", output, borders, seasonal_height, f"{members or len(centres)}-member mean" if not multisystem else f"{len(centres)}-system mean", period_label(first_target, last_target))
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
            all_entries.extend(run for run in old.get("runs", []) if isinstance(run, dict))
        except (OSError, ValueError) as exc:
            raise C3SError(f"could not read C3S manifest {candidate}: {exc}") from exc
    all_entries.extend(entries)
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
        "source_url": SOURCE_URL, "source_urls": [SOURCE_URL, PRESSURE_SOURCE_URL, SINGLE_SOURCE_URL],
        "product_labels": {
            key: {"500mb_height_anomaly": "500-mb Height Anomaly", "850mb_temperature_anomaly": "850-mb Temperature Anomaly", "2m_temperature_anomaly": "2-m Temperature Anomaly", "precipitation_anomaly": "Precipitation Anomaly", "sea_surface_temperature_anomaly": "Sea-Surface Temperature Anomaly", "mslp_anomaly": "MSLP Anomaly"}.get(key, key)
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
    parser.add_argument("--lead-months", default="4,5,6")
    parser.add_argument("--seasonal-window", default="4,5,6")
    parser.add_argument("--cache-dir", default=".cache/c3s")
    parser.add_argument("--output-dir", default="public/seasonal/c3s")
    parser.add_argument("--manifest", default="public/seasonal/c3s_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--no-components", action="store_true", help="publish only the C3S multi-system blend")
    parser.add_argument("--no-blend", action="store_true", help="publish only the selected C3S component entries")
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--decode-only", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    product_name = args.product
    product = PRODUCT_SPECS[product_name]
    centres = parse_centres(args.centres)
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

    blend_failures = 0
    if not args.no_blend:
        blend_grids: dict[int, Grid] = {}
        blend_heights: dict[int, Grid] = {}
        for lead in leads:
            available = [component_grids[centre][lead] for centre in centres if lead in component_grids.get(centre, {})]
            if available:
                reference = available[0]
                # C3S systems normally share the one-degree axes.  If a centre
                # returns a different grid, nearest-neighbour alignment keeps the
                # blend explicit instead of silently dropping that component.
                from cfsv2_seasonal import regrid_nearest
                blend_grids[lead] = mean_grids([regrid_nearest(grid, reference.lons, reference.lats, "C3S blend") for grid in available])
            heights = [component_heights[centre][lead] for centre in centres if lead in component_heights.get(centre, {})]
            if heights:
                reference = heights[0]
                from cfsv2_seasonal import regrid_nearest
                blend_heights[lead] = mean_grids([regrid_nearest(grid, reference.lons, reference.lats, "C3S height blend") for grid in heights])
        if blend_grids:
            entry, count = build_run(component="multisystem", label="multi-system", system="multiple", product_name=product_name, product=product_spec(product_name, "multi-system", multisystem=True), init=init, leads=leads, seasonal_leads=seasonal, archive=None, lead_grids=blend_grids, lead_heights=blend_heights, output_dir=output_dir, borders=borders, members=None, multisystem=True, centres=centres, decode_only=args.decode_only)
            entry["available_components"] = [centre for centre in centres if component_grids.get(centre)]
            entry["component_count"] = len(entry["available_components"])
            entries.append(entry)
            blend_failures += count
        else:
            print("C3S multi-system blend has no component fields", file=sys.stderr)
            blend_failures += 1
    write_manifest(manifest_path, entries, previous, args.retain_cycles)
    print(f"wrote C3S manifest: {manifest_path} ({len(entries)} run entries)")
    # A missing centre should be visible in the manifest but should not make a
    # usable multi-system release impossible.  Fail only when no rendered or
    # decoded entry survived.
    usable = any(entry.get("status") in {"rendered", "decoded", "partial"} for entry in entries)
    return 0 if usable and not blend_failures else (0 if usable and entries else 2)


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except C3SError as exc:
        print(f"C3S ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
