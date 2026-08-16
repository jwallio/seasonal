#!/usr/bin/env python3
"""Fetch and render NOAA CPC North American Multi-Model Ensemble products.

The realtime anomaly feed is a public NetCDF archive.  The adapter keeps the
official NMME ensemble mean and probability files distinct from derived
component consensus and model-spread products.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import numpy as np

from cfsv2_seasonal import DEFAULT_REGION, Grid, ensure_border_files, mean_grids, relative_path, render_map


REALTIME_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/ENSMEAN/"
PROB_ROOT = "https://ftp.cpc.ncep.noaa.gov/NMME/prob/netcdf/"
SOURCE_URL = "https://www.cpc.ncep.noaa.gov/products/NMME/data.html"
NCEI_URL = "https://www.ncei.noaa.gov/products/weather-climate-models/north-american-multi-model"
COMPONENTS = ("CanESM5", "CFSv2", "GEM5.2_NEMO", "NASA_GEOS5v2", "NCAR_CCSM4", "NCAR_CESM1")

TEMP_PALETTE = [
    "#28567f", "#397ba2", "#5b9fba", "#82bdca", "#b4d6dc", "#e7eeee",
    "#ffffff", "#f8dedd", "#efb6b5", "#e38e8e", "#d36c73", "#b84c5a",
]
PRECIP_PALETTE = [
    "#7f3b08", "#914b0d", "#a6611a", "#bd7a2d", "#d0a052", "#dfbd7d",
    "#ead8b3", "#ffffff", "#e5f1dc", "#c8e4bf", "#aad89f", "#86c879",
    "#5fba6b", "#3aa55b", "#1d8947", "#006d2c",
]
HEIGHT_PALETTE = [
    "#24527a", "#306b90", "#3d83a6", "#4891b0", "#539cb8", "#61a7bf",
    "#70b2c6", "#95c4d3", "#c4dce3", "#e1e4e7", "#eee0e0", "#f2cecd",
    "#eaaaa8", "#e28c8b", "#db797b", "#d3686c", "#ca5861", "#bf4856",
    "#a1384a", "#84283f",
]
PROB_PALETTE = ["#173f68", "#2b6590", "#4d8fb0", "#83b8c9", "#c9dfe5", "#fffdf8", "#f5c8c2", "#dd8f89", "#c6545c", "#8e263d"]

BASE_PRODUCTS: dict[str, dict[str, Any]] = {
    "2m_temperature_anomaly": {
        "file_var": "tmp2m", "field": "tmp2m_anomaly", "raw_field": "2-m temperature anomaly",
        "units": "°C", "seasonal_units": "°C", "min": -6.0, "max": 6.0,
        "ticks": list(range(-6, 7)), "palette": TEMP_PALETTE, "title": "2-m Temperature Anomaly (°C)",
        "conversion": "Kelvin anomaly increments are displayed in °C", "reducer": "mean",
    },
    "precipitation_anomaly": {
        "file_var": "prate", "field": "precipitation_anomaly", "raw_field": "precipitation anomaly",
        "units": "in", "seasonal_units": "in", "min": -8.0, "max": 8.0,
        "ticks": list(range(-8, 9)), "palette": PRECIP_PALETTE, "title": "CONUS Precipitation Anomaly (in)",
        "conversion": "NMME precipitation-rate anomaly multiplied by target-month seconds and converted to inches", "reducer": "sum",
        "region": (-128.0, -65.0, 22.0, 52.0),
    },
    "200mb_height_anomaly": {
        "file_var": "z200", "field": "z200_anomaly", "raw_field": "200-mb geopotential height anomaly",
        "units": "m", "seasonal_units": "m", "min": -200.0, "max": 200.0,
        "ticks": list(range(-200, 201, 20)), "palette": HEIGHT_PALETTE, "title": "200-mb Geopotential Height Anomaly (m)",
        "conversion": "NMME z200 anomaly field displayed in metres", "reducer": "mean",
    },
}


class NMMEError(RuntimeError):
    """A user-actionable NMME source or decoding error."""


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_init(value: str) -> str:
    if value == "latest":
        try:
            import requests
            response = requests.get(REALTIME_ROOT, timeout=(20, 60))
            response.raise_for_status()
            candidates = sorted(set(re.findall(r'href="(\d{10})/"', response.text)), reverse=True)
            now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            for candidate in candidates:
                parsed = dt.datetime.strptime(candidate, "%Y%m%d%H")
                if parsed <= now:
                    return candidate
        except Exception as exc:
            raise NMMEError(f"could not discover the latest CPC NMME cycle: {exc}") from exc
        raise NMMEError("the CPC NMME directory listed no usable initialization")
    if re.fullmatch(r"\d{6}", value):
        return f"{value}0800"
    if re.fullmatch(r"\d{8}", value):
        return f"{value}00"
    if re.fullmatch(r"\d{10}", value):
        return value
    raise NMMEError("--init must be latest, YYYYMM, YYYYMMDD, or YYYYMMDDHH")


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    values: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise NMMEError(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise NMMEError(f"{label} must be between {minimum} and {maximum}")
        if number not in values:
            values.append(number)
    if not values:
        raise NMMEError(f"{label} cannot be empty")
    return values


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead: int) -> str:
    date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(date.year, date.month, lead - 1)
    return f"{year:04d}{month:02d}"


def target_period(target: str, months: int = 1) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, months)
    return iso_utc(start.replace(tzinfo=dt.timezone.utc)), iso_utc(dt.datetime(year, month, 1, tzinfo=dt.timezone.utc))


def period_label(first: str, months: int = 1) -> str:
    start = dt.datetime.strptime(first, "%Y%m")
    if months == 3:
        end_year, end_month = month_after(start.year, start.month, 2)
        end = dt.datetime(end_year, end_month, 1)
        season = {(12, 2): "DJF", (3, 5): "MAM", (6, 8): "JJA", (9, 11): "SON"}.get((start.month, end.month))
        if season:
            return f"{season} {end.year}"
        return f"{start:%b}–{end:%b %Y}"
    return f"{start:%B %Y}"


def month_seconds(target: str) -> int:
    start = dt.datetime.strptime(target, "%Y%m")
    year, month = month_after(start.year, start.month, 1)
    return int((dt.datetime(year, month, 1) - start).total_seconds())


def grid_std(grids: list[Grid]) -> Grid:
    if not grids:
        raise NMMEError("cannot calculate spread from an empty component set")
    first = grids[0]
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        row: list[float] = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [value for value in samples if math.isfinite(value)]
            row.append(float(np.std(finite, ddof=0)) if finite else math.nan)
        values.append(row)
    return Grid(first.lons[:], first.lats[:], values)


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    try:
        import requests
        response = requests.get(url, stream=True, timeout=(30, 300))
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if temporary.stat().st_size == 0:
            raise NMMEError(f"empty NMME download: {url}")
        temporary.replace(path)
    except NMMEError:
        raise
    except Exception as exc:
        raise NMMEError(f"NMME download failed for {url}: {exc}") from exc


def normalize_longitudes(values: np.ndarray) -> np.ndarray:
    return ((values + 180.0) % 360.0) - 180.0


def decode_netcdf(path: Path, variable: str, lead: int, product: dict[str, Any], *, probability: bool, probability_period: str) -> Grid:
    try:
        import xarray as xr
    except ImportError as exc:
        raise NMMEError("NMME rendering requires xarray and a NetCDF backend") from exc
    dataset = None
    try:
        # CPC files use a non-CF ``months since`` coordinate. We select by
        # positional lead and do not need xarray to decode that coordinate.
        dataset = xr.open_dataset(path, decode_times=False)
        data = dataset[variable]
        target_dimension = next((name for name in ("target", "lead", "time") if name in data.dims), None)
        if target_dimension:
            index = max(0, lead - 1)
            if index >= data.sizes[target_dimension]:
                raise NMMEError(f"{path.name} has no target index for lead {lead}")
            data = data.isel({target_dimension: index})
        latitude_name = next((name for name in ("lat", "latitude", "y") if name in data.dims or name in data.coords), None)
        longitude_name = next((name for name in ("lon", "longitude", "x") if name in data.dims or name in data.coords), None)
        if not latitude_name or not longitude_name:
            raise NMMEError(f"{path.name} is missing lat/lon coordinates")
        for dimension in list(data.dims):
            if dimension not in {latitude_name, longitude_name}:
                data = data.mean(dim=dimension, skipna=True)
        data = data.transpose(latitude_name, longitude_name)
        lats = np.asarray(data[latitude_name].values, dtype=float)
        lons = np.asarray(data[longitude_name].values, dtype=float)
        values = np.asarray(data.values, dtype=float)
        units = str(data.attrs.get("units", ""))
    finally:
        if dataset is not None:
            dataset.close()
    lon_order = np.argsort(normalize_longitudes(lons))
    lat_order = np.argsort(lats)
    converted = values[np.ix_(lat_order, lon_order)]
    if probability:
        # CPC labels these variables as percent but stores probabilities on
        # the 0–1 scale in the NetCDF files.
        if float(np.nanmax(converted)) <= 1.0:
            converted = converted * 100.0
    if not probability and product["file_var"] == "prate":
        # CPC NMME NetCDF metadata reports precipitation rate anomalies in
        # mm/s; convert the selected target month to liquid-water inches.
        converted = converted * month_seconds(target_month_from_lead(path, lead)) / 25.4
    return Grid(
        lons=[float(value) for value in normalize_longitudes(lons)[lon_order]],
        lats=[float(value) for value in lats[lat_order]],
        values=converted.tolist(),
    )


def target_month_from_lead(path: Path, lead: int) -> str:
    match = re.search(r"\.(\d{6})\.", path.name)
    if not match:
        raise NMMEError(f"could not infer NMME initialization month from {path.name}")
    init = f"{match.group(1)}0800"
    return target_month(init, lead)


def spec_for(product_name: str, base_name: str) -> dict[str, Any]:
    base = BASE_PRODUCTS[base_name]
    if product_name in BASE_PRODUCTS:
        spec = {
            "name": product_name, "variable": base["file_var"], "field": base["field"], "raw_field": base["raw_field"],
            "raw_units": base["units"], "units": base["units"], "seasonal_units": base["seasonal_units"],
            "title": f"NMME {base['title']}", "absolute_title": f"NMME {base['title']}", "height_contours": False,
            "region": base.get("region", DEFAULT_REGION), "anomaly_min": base["min"], "anomaly_max": base["max"],
            "anomaly_ticks": base["ticks"], "anomaly_palette": base["palette"], "conversion": base["conversion"],
            "header_detail": "{source_label}  •  CPC realtime ensemble-mean anomaly  •  {baseline_label}",
        }
        return spec
    if product_name.startswith("probability_"):
        label = {"probability_above_normal": "Above Normal Probability", "probability_near_normal": "Near Normal Probability", "probability_below_normal": "Below Normal Probability"}[product_name]
        return {
            "name": product_name, "variable": "probability", "field": product_name,
            "raw_field": f"{base['title']} {label.lower()}", "raw_units": "%", "units": "%", "seasonal_units": "%",
            "title": f"NMME {base['title'].replace(' Anomaly', '')} · {label}", "absolute_title": f"NMME {label}", "height_contours": False,
            "region": base.get("region", DEFAULT_REGION), "anomaly_min": 0.0, "anomaly_max": 100.0,
            "anomaly_ticks": list(range(0, 101, 10)), "anomaly_palette": PROB_PALETTE, "anomaly_tick_format": "plain",
            "conversion": "official CPC NMME probability field", "header_detail": "{source_label}  •  Official CPC NMME probability product",
        }
    if product_name == "model_spread":
        if base_name == "2m_temperature_anomaly":
            minimum, maximum, ticks, palette = 0.0, 6.0, list(range(0, 7)), TEMP_PALETTE[:6]
        elif base_name == "precipitation_anomaly":
            minimum, maximum, ticks, palette = 0.0, 8.0, list(range(0, 9)), PRECIP_PALETTE[:8]
        else:
            minimum, maximum, ticks, palette = 0.0, 200.0, list(range(0, 201, 20)), HEIGHT_PALETTE[:10]
        return {
            "name": product_name, "variable": base["file_var"], "field": f"{base['field']}_spread", "raw_field": "NMME component standard deviation",
            "raw_units": base["units"], "units": base["units"], "seasonal_units": base["units"], "title": f"NMME Model Spread · {base['title']}",
            "absolute_title": f"NMME Model Spread · {base['title']}", "height_contours": False, "region": base.get("region", DEFAULT_REGION),
            "anomaly_min": minimum, "anomaly_max": maximum, "anomaly_ticks": ticks, "anomaly_palette": palette,
            "anomaly_tick_format": "plain", "conversion": "standard deviation across available NMME component means",
            "header_detail": "{source_label}  •  Component-model standard deviation",
        }
    if product_name == "multi_model_consensus":
        return {
            **spec_for(base_name, base_name), "name": product_name, "title": f"NMME Multi-Model Consensus · {base['title']}",
            "absolute_title": f"NMME Multi-Model Consensus · {base['title']}", "field": f"{base['field']}_consensus",
            "header_detail": "{source_label}  •  Equal-weight component-model consensus",
        }
    raise NMMEError(f"unsupported NMME product {product_name}")


def product_base(product_name: str, requested: str) -> str:
    return requested if product_name not in BASE_PRODUCTS else product_name


def make_run_entry(product_name: str, base_name: str, init: str, component: str, components: list[str], product: dict[str, Any], probability_period: str) -> dict[str, Any]:
    return {
        "id": f"nmme-{component.lower().replace('.', '_')}-{init}-{product_name}-{base_name}",
        "model": "NOAA NMME", "component": component, "components": components, "product": product_name,
        "base_product": base_name, "source": "NOAA CPC NMME", "source_url": SOURCE_URL, "source_urls": [SOURCE_URL, NCEI_URL],
        "archive_root": REALTIME_ROOT, "init_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
        "statistic": "official NMME ensemble mean" if component == "ENSMEAN" else ("official CPC probability" if component == "PROBABILITY" else ("component standard deviation" if component == "SPREAD" else "equal-weight component mean")),
        "ensemble_scope": "NMME realtime ensemble mean" if component == "ENSMEAN" else ("NMME component models" if component in {"SPREAD", "CONSENSUS"} else "CPC NMME probability product"),
        "field": product["field"], "units": product["units"], "raw_field": product["raw_field"], "raw_units": product["raw_units"],
        "conversion": product["conversion"], "probability_period": probability_period if component == "PROBABILITY" else None,
        "baseline": {"status": "official_nmme_anomaly_or_probability"}, "targets": [], "status": "planned",
    }


def render_run(
    *, product_name: str, base_name: str, init: str, component: str, components: list[str], product: dict[str, Any],
    grid_by_lead: dict[int, Grid], output_dir: Path, borders: list[Path], leads: list[int], seasonal_leads: list[int],
    probability_period: str, source_files: dict[int, str], decode_only: bool,
) -> tuple[dict[str, Any], int]:
    root = Path(__file__).resolve().parents[1]
    entry = make_run_entry(product_name, base_name, init, component, components, product, probability_period)
    failures = 0
    probability = component == "PROBABILITY"
    render_leads = [seasonal_leads[0]] if probability and seasonal_leads else leads
    for lead in render_leads:
        start = target_month(init, lead)
        season_months = 3 if probability and probability_period == "seas" else 1
        end_start = target_month(init, lead + season_months - 1)
        target_key = f"{start}-{end_start}" if season_months == 3 else start
        target_entry: dict[str, Any] = {
            "id": f"{entry['id']}-lead{lead:02d}", "target_month": target_key,
            "valid_start_utc": target_period(start, season_months)[0], "valid_end_utc": target_period(start, season_months)[1],
            "lead_month": lead, "field": product["field"], "units": product["units"], "status": "planned",
            "source_file": source_files.get(lead),
        }
        try:
            if decode_only:
                target_entry["status"] = "decoded"
            else:
                output = output_dir / init[:8] / f"nmme_{component.lower()}_{base_name}_{product_name}_{target_key}.jpg"
                render_map(
                    grid_by_lead[lead], init, start, lead, list(range(max(1, len(components)))), output, anomaly=True,
                    baseline_label="CPC NMME official anomaly/probability", border_paths=borders,
                    period_label=period_label(start, season_months), ensemble_label=("NMME ensemble mean" if component == "ENSMEAN" else ("Official CPC probability" if component == "PROBABILITY" else f"{len(components)} component models")),
                    product_spec={**product, "source_label": "NOAA CPC NMME"},
                )
                target_entry["image"] = relative_path(output, root)
                target_entry["status"] = "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"NMME {product_name} {component} lead {lead} failed: {exc}", file=sys.stderr)
        entry["targets"].append(target_entry)
    if not probability and seasonal_leads and not decode_only:
        first, last = seasonal_leads[0], seasonal_leads[-1]
        start, end = target_month(init, first), target_month(init, last)
        target_entry = {
            "id": f"{entry['id']}-{start}-{end}", "target_month": f"{start}-{end}", "valid_start_utc": target_period(start)[0],
            "valid_end_utc": target_period(end)[1], "lead_month": f"{first}–{last}", "monthly_leads": seasonal_leads,
            "field": product["field"], "units": product["seasonal_units"], "status": "planned",
        }
        try:
            grids = [grid_by_lead[lead] for lead in seasonal_leads]
            if product_name == "precipitation_anomaly":
                from cfsv2_seasonal import sum_grids
                seasonal_grid = sum_grids(grids)
            else:
                seasonal_grid = mean_grids(grids)
            if not decode_only:
                output = output_dir / init[:8] / f"nmme_{component.lower()}_{base_name}_{product_name}_{start}-{end}.jpg"
                render_map(seasonal_grid, init, start, f"{first}–{last}", list(range(max(1, len(components)))), output, anomaly=True, baseline_label="CPC NMME official anomaly/probability", border_paths=borders, period_label=period_label(start, 3), ensemble_label=("NMME ensemble mean" if component == "ENSMEAN" else f"{len(components)} component models"), product_spec={**product, "source_label": "NOAA CPC NMME"})
                target_entry["image"] = relative_path(output, root)
            target_entry["status"] = "decoded" if decode_only else "rendered"
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
        entry["targets"].append(target_entry)
    statuses = [target["status"] for target in entry["targets"]]
    entry["status"] = "failed" if statuses and all(status == "failed" for status in statuses) else ("partial" if failures else ("decoded" if decode_only else "rendered"))
    entry["output_dir"] = relative_path(output_dir, root)
    return entry, failures


def write_manifest(path: Path, entries: Iterable[dict[str, Any]], previous: Path | None, retain_cycles: int) -> None:
    old_entries: list[dict[str, Any]] = []
    if previous and previous.exists():
        try:
            old_entries = [run for run in json.loads(previous.read_text(encoding="utf-8")).get("runs", []) if isinstance(run, dict)]
        except (OSError, ValueError) as exc:
            raise NMMEError(f"could not read previous NMME manifest: {exc}") from exc
    unique = {str(run.get("id")): run for run in [*old_entries, *entries] if run.get("id")}
    ordered = sorted(unique.values(), key=lambda run: (str(run.get("init_utc", "")), str(run.get("id", ""))), reverse=True)
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:max(1, retain_cycles)])
    labels = {**{key: value["title"] for key, value in BASE_PRODUCTS.items()}, "probability_above_normal": "Above Normal Probability", "probability_near_normal": "Near Normal Probability", "probability_below_normal": "Below Normal Probability", "model_spread": "NMME Model Spread", "multi_model_consensus": "NMME Multi-Model Consensus"}
    payload = {
        "schema_version": 1, "kind": "nmme_seasonal_manifest", "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NOAA CPC NMME", "source_url": SOURCE_URL, "source_urls": [SOURCE_URL, NCEI_URL],
        "product_labels": labels, "retention": {"max_cycles": max(1, retain_cycles), "history_cycles": max(0, retain_cycles - 1)},
        "runs": [run for run in ordered if str(run.get("init_utc", "")) in keep],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    product_choices = tuple(BASE_PRODUCTS) + ("probability_above_normal", "probability_near_normal", "probability_below_normal", "model_spread", "multi_model_consensus")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=product_choices, default="2m_temperature_anomaly")
    parser.add_argument("--products", default="", help="optional comma-separated product list")
    parser.add_argument("--base-product", choices=tuple(BASE_PRODUCTS), default="2m_temperature_anomaly")
    parser.add_argument("--components", default=",".join(COMPONENTS))
    parser.add_argument("--probability-period", choices=("mon", "seas"), default="seas")
    parser.add_argument("--init", default="latest")
    parser.add_argument("--lead-months", default="4,5,6")
    parser.add_argument("--seasonal-window", default="4,5,6")
    parser.add_argument("--cache-dir", default=".cache/nmme")
    parser.add_argument("--output-dir", default="public/seasonal/nmme")
    parser.add_argument("--manifest", default="public/seasonal/nmme_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--no-components", action="store_true")
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--decode-only", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    requested = [item.strip() for item in (args.products or args.product).split(",") if item.strip()]
    choices = set(BASE_PRODUCTS) | {"probability_above_normal", "probability_near_normal", "probability_below_normal", "model_spread", "multi_model_consensus"}
    unknown = [item for item in requested if item not in choices]
    if unknown:
        raise NMMEError(f"unsupported NMME product(s): {', '.join(unknown)}")
    components = [item.strip() for item in args.components.split(",") if item.strip()]
    if not components:
        raise NMMEError("--components cannot be empty")
    init = parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 1, 12)
    seasonal = parse_int_list(args.seasonal_window, "seasonal window", 1, 12) if args.seasonal_window else []
    if seasonal:
        expected = list(range(min(seasonal), max(seasonal) + 1))
        if seasonal != expected:
            raise NMMEError("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal))
    root = Path(__file__).resolve().parents[1]
    cache_dir = Path(args.cache_dir) if Path(args.cache_dir).is_absolute() else root / args.cache_dir
    output_dir = Path(args.output_dir) if Path(args.output_dir).is_absolute() else root / args.output_dir
    manifest = Path(args.manifest) if Path(args.manifest).is_absolute() else root / args.manifest
    previous = None
    if args.previous_manifest:
        previous = args.previous_manifest if args.previous_manifest.is_absolute() else root / args.previous_manifest
    borders = [] if args.decode_only else ensure_border_files(args, cache_dir, root)
    cache: dict[tuple[str, str, str, int, str, str], Grid] = {}
    source_files: dict[tuple[str, str, str, int, str, str], str] = {}
    entries: list[dict[str, Any]] = []
    failures = 0

    def load(source: str, base_name: str, lead: int, kind: str, variable: str | None = None) -> Grid:
        selected = variable or ("prob_above" if kind == "probability" else "fcst")
        key = (source, base_name, kind, lead, args.probability_period, selected)
        if key in cache:
            return cache[key]
        base = BASE_PRODUCTS[base_name]
        if kind == "probability":
            filename = f"{base['file_var']}.{init[:6]}.prob.{args.probability_period}.nc"
            url = urljoin(PROB_ROOT, filename)
            path = cache_dir / "prob" / filename
        else:
            filename = f"{source}.{base['file_var']}.{init[:6]}.ENSMEAN.anom.nc"
            url = urljoin(f"{REALTIME_ROOT}{init}/", filename)
            path = cache_dir / "realtime" / init / filename
        download(url, path)
        grid = decode_netcdf(path, selected, lead, base, probability=kind == "probability", probability_period=args.probability_period)
        cache[key] = grid
        source_files[key] = url
        return grid

    def aligned(grids: list[Grid]) -> list[Grid]:
        if not grids:
            return []
        reference = grids[0]
        from cfsv2_seasonal import regrid_nearest
        return [regrid_nearest(grid, reference.lons, reference.lats, "NMME component") for grid in grids]

    for product_name in requested:
        base_name = product_base(product_name, args.base_product)
        spec = spec_for(product_name, base_name)
        grid_by_lead: dict[int, Grid] = {}
        files_by_lead: dict[int, str] = {}
        component_grid_by_lead: dict[str, dict[int, Grid]] = {component: {} for component in components}
        for lead in leads:
            try:
                if product_name in BASE_PRODUCTS:
                    grid_by_lead[lead] = load("NMME", product_name, lead, "anomaly")
                    files_by_lead[lead] = source_files[("NMME", product_name, "anomaly", lead, args.probability_period, "fcst")]
                elif product_name.startswith("probability_"):
                    variable = {"probability_above_normal": "prob_above", "probability_near_normal": "prob_norm", "probability_below_normal": "prob_below"}[product_name]
                    grid_by_lead[lead] = load("NMME", base_name, lead, "probability", variable)
                    files_by_lead[lead] = source_files[("NMME", base_name, "probability", lead, args.probability_period, variable)]
                else:
                    source_grids = []
                    for component in components:
                        component_grid_by_lead[component][lead] = load(component, base_name, lead, "anomaly")
                        source_grids.append(component_grid_by_lead[component][lead])
                    source_grids = aligned(source_grids)
                    if product_name == "model_spread":
                        grid_by_lead[lead] = grid_std(source_grids)
                    else:
                        grid_by_lead[lead] = mean_grids(source_grids)
                    files_by_lead[lead] = "component files: " + ", ".join(components)
            except Exception as exc:
                failures += 1
                print(f"NMME {product_name} lead {lead} unavailable: {exc}", file=sys.stderr)
        if grid_by_lead:
            component = "ENSMEAN" if product_name in BASE_PRODUCTS else ("PROBABILITY" if product_name.startswith("probability_") else ("SPREAD" if product_name == "model_spread" else "CONSENSUS"))
            entry, count = render_run(product_name=product_name, base_name=base_name, init=init, component=component, components=components, product=spec, grid_by_lead=grid_by_lead, output_dir=output_dir, borders=borders, leads=leads, seasonal_leads=seasonal, probability_period=args.probability_period, source_files=files_by_lead, decode_only=args.decode_only)
            entries.append(entry)
            failures += count
        if not args.no_components and product_name in BASE_PRODUCTS:
            for component in components:
                grids = component_grid_by_lead[component]
                # Component files are loaded lazily only for the derived
                # products; fetch them here for the optional component runs.
                for lead in leads:
                    try:
                        grids[lead] = load(component, product_name, lead, "anomaly")
                    except Exception as exc:
                        print(f"NMME component {component} lead {lead} unavailable: {exc}", file=sys.stderr)
                if grids:
                    component_spec = {**spec, "title": f"NMME {component} · {BASE_PRODUCTS[product_name]['title']}", "header_detail": "{source_label}  •  Individual NMME component-model mean"}
                    entry, count = render_run(product_name=product_name, base_name=base_name, init=init, component=component, components=[component], product=component_spec, grid_by_lead=grids, output_dir=output_dir, borders=borders, leads=leads, seasonal_leads=seasonal, probability_period=args.probability_period, source_files={}, decode_only=args.decode_only)
                    entries.append(entry)
                    failures += count
    write_manifest(manifest, entries, previous, args.retain_cycles)
    print(f"wrote NMME manifest: {manifest} ({len(entries)} run entries)")
    return 0 if entries else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except NMMEError as exc:
        print(f"NMME ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
