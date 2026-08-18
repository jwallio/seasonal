#!/usr/bin/env python3
"""Build a transparent, deduplicated seasonal super ensemble.

The package combines separable numeric forecast-system fields rather than
averaging rendered images or nesting provider multi-model means.  Each
canonical source unit receives one equal vote.  Standalone products that are
already represented inside C3S, overlapping NMME components, and opaque
multi-model aggregates are recorded in the membership ledger but never added
again.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import c3s_seasonal as c3s
import cansips_seasonal as cansips
import cfsv2_seasonal as cfsv2
import geos_s2s3_seasonal as geos
import nmme_seasonal as nmme
from cfsv2_seasonal import (
    Grid,
    ensure_border_files,
    mean_grids,
    regrid_nearest,
    relative_path,
    render_map,
    subtract_grids,
    sum_grids,
)


SOURCE_URLS = [
    c3s.SOURCE_URL,
    cansips.CANSIPS_README_URL,
    cfsv2.NOMADS_ROOT,
    cfsv2.NCEI_CALIBRATION_ROOT,
    geos.NASA_NRT_ROOT,
    geos.NASA_DRIFT_ROOT,
    nmme.SOURCE_URL,
]

# ECCC is intentionally omitted here.  Its C3S field is already represented
# by the fuller 40-member CanSIPS v3 family blend below.
C3S_CANONICAL_CENTRES = (
    "ecmwf",
    "ukmo",
    "meteo_france",
    "dwd",
    "cmcc",
    "ncep",
    "jma",
    "bom",
)
NMME_UNIQUE_COMPONENTS = ("NCAR_CCSM4", "NCAR_CESM1")
NMME_PRODUCTS = frozenset({"2m_temperature_anomaly", "precipitation_anomaly"})
CFSV2_STANDALONE_PRODUCTS = frozenset(
    {
        "500mb_height_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "mslp_anomaly",
    }
)
CFSV2_MEMBER_KEY = "noaa_cfsv2_rolling40"
GEOS_MEMBER_KEY = "nasa_geos_s2s3"
PRODUCTS = tuple(c3s.PRODUCT_SPECS)

PRODUCT_LABELS = {
    "500mb_height_anomaly": "500-mb Height Anomaly",
    "850mb_temperature_anomaly": "850-mb Temperature Anomaly",
    "2m_temperature_anomaly": "2-m Temperature Anomaly",
    "precipitation_anomaly": "CONUS Precipitation Anomaly",
    "sea_surface_temperature_anomaly": "Sea-Surface Temperature Anomaly",
    "mslp_anomaly": "MSLP Anomaly",
}

PRODUCT_TITLES = {
    "500mb_height_anomaly": "Super Ensemble 500-mb Geopotential Height & Anomaly (m)",
    "850mb_temperature_anomaly": "Super Ensemble 850-mb Temperature Anomaly (°C)",
    "2m_temperature_anomaly": "Super Ensemble 2-m Temperature Anomaly (°C)",
    "precipitation_anomaly": "Super Ensemble CONUS Precipitation Anomaly (in)",
    "sea_surface_temperature_anomaly": "Super Ensemble Sea-Surface Temperature Anomaly (°C)",
    "mslp_anomaly": "Super Ensemble Mean Sea-Level Pressure Anomaly (hPa)",
}

COMMON_EXCLUSIONS: list[dict[str, Any]] = [
    {
        "package": "ECMWF SEAS5 standalone",
        "reason": "duplicate",
        "represented_by": "c3s_ecmwf_system51",
    },
    {
        "package": "JMA standalone",
        "reason": "duplicate",
        "represented_by": "c3s_jma_system4",
    },
    {
        "package": "C3S ECCC System 5",
        "reason": "duplicate within the fuller ECCC family",
        "represented_by": "eccc_cansips_v3",
    },
    {
        "package": "C3S multi-system mean",
        "reason": "aggregate of individually included C3S systems",
        "represented_by": "C3S component fields",
    },
    {
        "package": "NMME GEM5.2_NEMO and CanESM5",
        "reason": "duplicate ECCC systems",
        "represented_by": "eccc_cansips_v3",
    },
    {
        "package": "NMME ensemble/consensus products",
        "reason": "aggregate containing already represented systems",
        "represented_by": "two unique NCAR NMME component fields where supported",
    },
    {
        "package": "APCC MME",
        "reason": "opaque overlapping aggregate; current package does not expose separable component grids",
        "represented_by": None,
    },
]


def canonical_exclusions(product: str) -> list[dict[str, Any]]:
    exclusions = [dict(item) for item in COMMON_EXCLUSIONS]
    if product in NMME_PRODUCTS:
        exclusions.append(
            {
                "package": "NMME NASA_GEOS5v2",
                "reason": "duplicate NASA GEOS forecast family",
                "represented_by": GEOS_MEMBER_KEY,
            }
        )
    if product == "500mb_height_anomaly":
        exclusions.append(
            {
                "package": "NASA GEOS-S2S-3 APCN z500 archive",
                "reason": "current long-range files declare 200 hPa and fail the strict 500-hPa safety check",
                "represented_by": None,
            }
        )
    if product in CFSV2_STANDALONE_PRODUCTS:
        exclusions.extend(
            [
                {
                    "package": "C3S NCEP System 2",
                    "reason": "duplicate CFSv2 family",
                    "represented_by": CFSV2_MEMBER_KEY,
                },
                {
                    "package": "NMME CFSv2",
                    "reason": "duplicate CFSv2 family",
                    "represented_by": CFSV2_MEMBER_KEY,
                },
            ]
        )
    else:
        exclusions.extend(
            [
                {
                    "package": "CFSv2 standalone",
                    "reason": "standalone rolling adapter does not currently expose this parameter",
                    "represented_by": "c3s_ncep_system2",
                },
                {
                    "package": "NMME CFSv2",
                    "reason": "duplicate CFSv2 family",
                    "represented_by": "c3s_ncep_system2",
                },
            ]
        )
    return exclusions


class SuperEnsembleError(RuntimeError):
    """A user-actionable super-ensemble source or rendering error."""


@dataclass(frozen=True)
class MemberDefinition:
    key: str
    label: str
    source_package: str
    system: str
    internal_members: int | None = None
    notes: str = ""

    def manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source_package": self.source_package,
            "system": self.system,
            "internal_members": self.internal_members,
            "notes": self.notes,
        }


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def selected_products(value: str) -> list[str]:
    names = list(PRODUCTS) if value.strip().lower() == "all" else [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in names if item not in PRODUCTS]
    if unknown:
        raise SuperEnsembleError(f"unsupported super-ensemble product(s): {', '.join(unknown)}")
    if not names:
        raise SuperEnsembleError("--product cannot be empty")
    return list(dict.fromkeys(names))


def c3s_centres_for(product: str) -> tuple[str, ...]:
    if product in CFSV2_STANDALONE_PRODUCTS:
        return tuple(centre for centre in C3S_CANONICAL_CENTRES if centre != "ncep")
    return C3S_CANONICAL_CENTRES


def canonical_members(product: str) -> list[MemberDefinition]:
    members = [
        MemberDefinition(
            key=f"c3s_{centre}_system{c3s.CENTRES[centre]['system']}",
            label=f"{c3s.CENTRES[centre]['label']} / System {c3s.CENTRES[centre]['system']}",
            source_package="Copernicus C3S",
            system=f"{centre}/{c3s.CENTRES[centre]['system']}",
            internal_members=int(c3s.CENTRES[centre]["members"]),
            notes="Official C3S postprocessed ensemble-mean anomaly",
        )
        for centre in c3s_centres_for(product)
    ]
    if product in CFSV2_STANDALONE_PRODUCTS:
        members.append(
            MemberDefinition(
                key=CFSV2_MEMBER_KEY,
                label="NOAA CFSv2 40-cycle rolling blend",
                source_package="NOAA CFSv2 NOMADS",
                system="10-day lagged initial-condition blend",
                internal_members=40,
                notes="One CFSv2-family vote; C3S NCEP and NMME CFSv2 copies are excluded",
            )
        )
    members.append(
        MemberDefinition(
            key="eccc_cansips_v3",
            label="ECCC CanSIPS v3 family",
            source_package="ECCC CanSIPS v3",
            system="20 GEM5.2-NEMO + 20 CanESM5 members",
            internal_members=cansips.CANSIPS_ENSEMBLE_MEMBERS,
            notes="One ECCC family vote; C3S ECCC and NMME ECCC copies are excluded",
        )
    )
    if product in geos.SUPERENSEMBLE_PRODUCTS:
        members.append(
            MemberDefinition(
                key=GEOS_MEMBER_KEY,
                label="NASA GEOS-S2S-3",
                source_package="NASA GEOS-S2S-3 NCCS numerical archive",
                system="GEOS-S2S-3 APCN lag/burst ensemble",
                notes="One NASA-family vote; the NMME NASA_GEOS5v2 copy is excluded",
            )
        )
    if product in NMME_PRODUCTS:
        members.extend(
            MemberDefinition(
                key=f"nmme_{component.lower()}",
                label=f"NMME {component}",
                source_package="NOAA CPC NMME component archive",
                system=component,
                notes="Unique NMME component not otherwise represented in C3S/CanSIPS",
            )
            for component in NMME_UNIQUE_COMPONENTS
        )
    keys = [member.key for member in members]
    if len(keys) != len(set(keys)):
        raise SuperEnsembleError("canonical super-ensemble roster contains a duplicate key")
    return members


def membership_ledger(product: str) -> dict[str, Any]:
    included = canonical_members(product)
    return {
        "product": product,
        "weighting_unit": "canonical non-overlapping forecast-family source",
        "weighting": "equal weight after each source has formed its own ensemble mean",
        "native_baselines": True,
        "included": [member.manifest() for member in included],
        "expected_count": len(included),
        "excluded": canonical_exclusions(product),
    }


def product_spec(product: str, *, synthetic: bool = False) -> dict[str, Any]:
    spec = dict(c3s.PRODUCT_SPECS[product])
    spec["title"] = PRODUCT_TITLES[product]
    spec["absolute_title"] = PRODUCT_TITLES[product]
    spec["source_label"] = "wall.cloud seasonal super ensemble"
    detail = "Synthetic style preview — not forecast data" if synthetic else "Deduplicated equal-weight forecast families"
    field_detail = "Height contours in dam" if spec["height_contours"] else f"{spec['units']} anomaly"
    spec["header_detail"] = f"{{source_label}}  •  {detail}  •  Native-model anomaly baselines  •  {field_detail}"
    return spec


def aligned_mean(grids_by_key: dict[str, Grid], ordered_keys: Iterable[str], label: str) -> Grid:
    keys = [key for key in ordered_keys if key in grids_by_key]
    if not keys:
        raise SuperEnsembleError(f"{label} has no member grids")
    reference = grids_by_key[keys[0]]
    aligned = [
        regrid_nearest(grids_by_key[key], reference.lons, reference.lats, f"{label} / {key}")
        for key in keys
    ]
    return mean_grids(aligned)


def combine_member_months(grids: list[Grid], reducer: str, label: str) -> Grid:
    if not grids:
        raise SuperEnsembleError(f"{label} has no monthly grids")
    reference = grids[0]
    aligned = [regrid_nearest(grid, reference.lons, reference.lats, label) for grid in grids]
    return sum_grids(aligned) if reducer == "sum" else mean_grids(aligned)


def weights_for(keys: list[str], definitions: dict[str, MemberDefinition]) -> list[dict[str, Any]]:
    weight = 1.0 / len(keys)
    return [
        {
            "key": key,
            "label": definitions[key].label,
            "weight": round(weight, 10),
        }
        for key in keys
    ]


def load_c3s_members(
    *,
    product: str,
    init: str,
    leads: list[int],
    cache_dir: Path,
    root: Path,
    systems: dict[str, str],
    member_grids: dict[int, dict[str, Grid]],
    height_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
    decode_only: bool,
) -> None:
    spec = c3s.PRODUCT_SPECS[product]
    for centre in c3s_centres_for(product):
        system = systems.get(centre, str(c3s.CENTRES[centre]["system"]))
        key = f"c3s_{centre}_system{c3s.CENTRES[centre]['system']}"
        archive = c3s.CDSArchive(cache_dir, centre, system)
        for lead in leads:
            target = c3s.target_month(init, lead)
            try:
                grid, path = archive.grid(spec, init, target, lead)
                member_grids[lead][key] = grid
                provenance[lead][key] = {
                    "source_package": "Copernicus C3S",
                    "originating_centre": centre,
                    "system": system,
                    "source_file": relative_path(path, root),
                    "baseline": "official C3S native postprocessed anomaly",
                }
            except Exception as exc:
                errors[lead][key] = str(exc)
                print(f"super ensemble C3S {centre} lead {lead} unavailable: {exc}", file=sys.stderr)
                continue
            if spec["height_contours"] and not decode_only:
                try:
                    height, height_path = archive.height(spec, init, target, lead)
                    height_grids[lead][key] = height
                    provenance[lead][key]["height_source_file"] = relative_path(height_path, root)
                except Exception as exc:
                    provenance[lead][key]["height_error"] = str(exc)
                    print(f"super ensemble C3S {centre} height lead {lead} unavailable: {exc}", file=sys.stderr)


def resolve_cfsv2_anchor(value: str, shared_init: str) -> str:
    anchor = cfsv2.discover_latest_init() if value == "latest" else cfsv2.parse_init(value)
    if anchor[:6] != shared_init[:6]:
        raise SuperEnsembleError(
            f"rolling CFSv2 anchor {anchor} does not match the shared initialization month {shared_init[:6]}"
        )
    return anchor


def load_cfsv2_member(
    *,
    args: argparse.Namespace,
    product: str,
    init: str,
    leads: list[int],
    cache_dir: Path,
    state_dir: Path,
    root: Path,
    wgrib2: str,
    member_grids: dict[int, dict[str, Grid]],
    height_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
) -> None:
    if product not in CFSV2_STANDALONE_PRODUCTS:
        return
    key = CFSV2_MEMBER_KEY
    try:
        anchor = resolve_cfsv2_anchor(args.cfsv2_anchor_init, init)
    except Exception as exc:
        for lead in leads:
            errors[lead][key] = str(exc)
        print(f"super ensemble rolling CFSv2 unavailable: {exc}", file=sys.stderr)
        return

    product_spec = cfsv2.get_product_spec(product)
    rolling_inits = cfsv2.rolling_cycle_inits(anchor, args.cfsv2_rolling_days * 4)
    decoder_args = argparse.Namespace(
        rolling_member=args.cfsv2_rolling_member,
        request_delay=args.request_delay,
        force_decode=args.force_decode,
        allow_partial_rolling=True,
    )
    last_request = 0.0
    for lead in leads:
        target = c3s.target_month(init, lead)
        try:
            anchor_lead = cfsv2.lead_for_target(anchor, target)
            forecast, source_files, available, expected, label, last_request = cfsv2.decode_target_ensemble(
                decoder_args,
                anchor,
                target,
                [args.cfsv2_rolling_member],
                rolling_inits,
                cache_dir,
                state_dir,
                wgrib2,
                root,
                last_request,
                product_spec,
            )
            baseline_url = cfsv2.ncei_calibration_url(anchor, anchor_lead, product_spec["source_kind"])
            baseline_path = cfsv2.cached_calibration_path(
                cache_dir,
                anchor,
                anchor_lead,
                product_spec["source_kind"],
            )
            baseline_downloaded, last_request = cfsv2.download_file(
                baseline_url,
                baseline_path,
                max(0.0, args.request_delay),
                last_request,
            )
            baseline = cfsv2.load_baseline(baseline_path, wgrib2, product_spec, target)
            member_grids[lead][key] = subtract_grids(forecast, baseline)
            if product_spec["height_contours"] and not args.decode_only:
                height_grids[lead][key] = forecast
            provenance[lead][key] = {
                "source_package": "NOAA CFSv2 NOMADS",
                "anchor_initialization": anchor,
                "anchor_initialization_utc": cfsv2.iso_utc(
                    dt.datetime.strptime(anchor, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
                ),
                "rolling_window": {
                    "days": args.cfsv2_rolling_days,
                    "cycle_interval_hours": 6,
                    "expected_cycles": expected,
                    "available_cycles": available,
                    "member_stream": args.cfsv2_rolling_member,
                    "complete": available == expected,
                    "label": label,
                },
                "source_files": source_files,
                "baseline": {
                    "source": product_spec["baseline_label"],
                    "years": cfsv2.NCEI_CALIBRATION_YEARS,
                    "url": baseline_url,
                    "file": relative_path(baseline_path, root),
                    "downloaded": baseline_downloaded,
                    "rolling_policy": "anchor_initialization",
                },
            }
        except Exception as exc:
            errors[lead][key] = str(exc)
            print(f"super ensemble rolling CFSv2 lead {lead} unavailable: {exc}", file=sys.stderr)


def load_cansips_member(
    *,
    args: argparse.Namespace,
    product: str,
    init: str,
    leads: list[int],
    cache_dir: Path,
    root: Path,
    wgrib2: str,
    member_grids: dict[int, dict[str, Grid]],
    height_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
) -> None:
    product_name = "sst_anomaly" if product == "sea_surface_temperature_anomaly" else product
    spec = cansips.PRODUCT_SPECS[product_name]
    key = "eccc_cansips_v3"
    last_request = 0.0
    for lead in leads:
        target = c3s.target_month(init, lead)
        try:
            forecast, forecast_source, last_request = cansips.load_ensemble_mean(
                init,
                lead,
                False,
                cache_dir,
                root,
                wgrib2,
                args.request_delay,
                last_request,
                spec,
                target,
                args.force_decode,
            )
            climatology, hindcast_sources, last_request = cansips.hindcast_climatology(
                init,
                lead,
                args.climo_start,
                args.climo_end,
                cache_dir,
                root,
                wgrib2,
                args.request_delay,
                last_request,
                spec,
                args.force_decode,
            )
            member_grids[lead][key] = subtract_grids(forecast, climatology)
            if spec["height_contours"] and not args.decode_only:
                height_grids[lead][key] = forecast
            provenance[lead][key] = {
                "source_package": "ECCC CanSIPS v3",
                "source_file": forecast_source,
                "hindcast_file_count": len(hindcast_sources),
                "baseline": f"CanSIPS v3 hindcast climatology {args.climo_start}-{args.climo_end}",
                "internal_members": cansips.CANSIPS_ENSEMBLE_MEMBERS,
                "internal_groups": ["GEM5.2-NEMO", "CanESM5"],
            }
        except Exception as exc:
            errors[lead][key] = str(exc)
            print(f"super ensemble CanSIPS lead {lead} unavailable: {exc}", file=sys.stderr)


def load_nmme_members(
    *,
    product: str,
    init: str,
    leads: list[int],
    cache_dir: Path,
    member_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
) -> None:
    if product not in NMME_PRODUCTS:
        return
    base = nmme.BASE_PRODUCTS[product]
    nmme_init = f"{init[:6]}0800"
    for component in NMME_UNIQUE_COMPONENTS:
        key = f"nmme_{component.lower()}"
        filename = f"{component}.{base['file_var']}.{init[:6]}.ENSMEAN.anom.nc"
        url = urljoin(f"{nmme.REALTIME_ROOT}{nmme_init}/", filename)
        path = cache_dir / "realtime" / nmme_init / filename
        for lead in leads:
            # C3S lead 4 is the fourth month after initialization.  CPC NMME
            # lead 5 indexes that same calendar month because lead 1 is the
            # initialization month.
            nmme_lead = lead + 1
            target = c3s.target_month(init, lead)
            try:
                if nmme.target_month(nmme_init, nmme_lead) != target:
                    raise SuperEnsembleError("C3S/NMME target-month alignment failed")
                nmme.download(url, path)
                member_grids[lead][key] = nmme.decode_netcdf(
                    path,
                    "fcst",
                    nmme_lead,
                    base,
                    probability=False,
                    probability_period="mon",
                )
                provenance[lead][key] = {
                    "source_package": "NOAA CPC NMME component archive",
                    "component": component,
                    "source_url": url,
                    "baseline": "CPC NMME native component anomaly",
                    "source_lead_month": nmme_lead,
                }
            except Exception as exc:
                errors[lead][key] = str(exc)
                print(f"super ensemble NMME {component} lead {lead} unavailable: {exc}", file=sys.stderr)


def load_geos_member(
    *,
    args: argparse.Namespace,
    product: str,
    init: str,
    leads: list[int],
    cache_dir: Path,
    border_paths: list[Path],
    member_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
) -> None:
    if product not in geos.SUPERENSEMBLE_PRODUCTS:
        return
    try:
        bundle = geos.load_anomaly_bundle(
            product=product,
            init=init[:6],
            leads=leads,
            cache_dir=cache_dir,
            border_paths=border_paths,
            request_delay=args.request_delay,
        )
    except Exception as exc:
        for lead in leads:
            errors[lead][GEOS_MEMBER_KEY] = str(exc)
        print(f"super ensemble NASA GEOS-S2S-3 unavailable: {exc}", file=sys.stderr)
        return

    for lead in leads:
        month = bundle[lead]
        member_grids[lead][GEOS_MEMBER_KEY] = month.anomaly
        provenance[lead][GEOS_MEMBER_KEY] = {
            "source_package": "NASA GEOS-S2S-3 NCCS numerical archive",
            "release_month": init[:6],
            "source_archive_url": month.archive_url,
            "source_files": list(month.source_files),
            "internal_members": len(month.members),
            "initialization_dates": list(month.init_dates),
            "baseline": {
                "source": geos.DRIFT_LABEL,
                "years": (
                    f"{min(month.drift_years)}-{max(month.drift_years)}"
                    if month.drift_years else "provider supplied"
                ),
                "url": month.drift_url,
                "method": "lead- and initialization-month-matched NASA hindcast ensemble mean",
            },
        }


def synthetic_members(
    product: str,
    leads: list[int],
    members: list[MemberDefinition],
) -> tuple[dict[int, dict[str, Grid]], dict[int, dict[str, Grid]], dict[int, dict[str, dict[str, Any]]]]:
    lons = [float(value) for value in range(-180, 180, 2)]
    lats = [float(value) for value in range(-88, 90, 2)]
    member_grids = {lead: {} for lead in leads}
    height_grids = {lead: {} for lead in leads}
    provenance = {lead: {} for lead in leads}
    scale = {
        "500mb_height_anomaly": 1.0,
        "850mb_temperature_anomaly": 0.045,
        "2m_temperature_anomaly": 0.045,
        "precipitation_anomaly": 0.025,
        "sea_surface_temperature_anomaly": 0.025,
        "mslp_anomaly": 0.09,
    }[product]
    for lead in leads:
        for index, member in enumerate(members):
            phase = index * 4.5 + lead * 2.0
            values: list[list[float]] = []
            heights: list[list[float]] = []
            for lat in lats:
                value_row: list[float] = []
                height_row: list[float] = []
                for lon in lons:
                    ridge = 125.0 * math.exp(-(((lon + 92.0 - phase * 0.25) / 36.0) ** 2 + ((lat - 61.0) / 18.0) ** 2))
                    trough = -118.0 * math.exp(-(((lon + 100.0 + phase * 0.18) / 33.0) ** 2 + ((lat - 33.0) / 15.0) ** 2))
                    wave = 28.0 * math.sin(math.radians(lon * 1.7 + phase)) * math.cos(math.radians((lat - 48.0) * 2.0))
                    value_row.append((ridge + trough + wave + (index - len(members) / 2.0) * 1.5) * scale)
                    height_row.append(5940.0 - (lat - 15.0) * 10.5 + 105.0 * math.sin(math.radians(lon + phase)))
                values.append(value_row)
                heights.append(height_row)
            member_grids[lead][member.key] = Grid(lons[:], lats[:], values)
            if product == "500mb_height_anomaly":
                height_grids[lead][member.key] = Grid(lons[:], lats[:], heights)
            provenance[lead][member.key] = {
                "source_package": "synthetic style preview",
                "not_forecast_data": True,
            }
    return member_grids, height_grids, provenance


def target_base(
    *,
    run_id: str,
    product: str,
    init: str,
    lead: int | str,
    target: str,
    keys: list[str],
    expected: list[MemberDefinition],
    definitions: dict[str, MemberDefinition],
    provenance: dict[str, dict[str, Any]],
    errors: dict[str, str],
) -> dict[str, Any]:
    start_target = target.split("-")[0]
    end_target = target.split("-")[-1]
    expected_keys = [member.key for member in expected]
    return {
        "id": f"{run_id}-{target}",
        "target_month": target,
        "valid_start_utc": c3s.target_period(start_target)[0],
        "valid_end_utc": c3s.target_period(end_target)[1],
        "lead_month": lead,
        "field": c3s.PRODUCT_SPECS[product]["field"],
        "units": c3s.PRODUCT_SPECS[product]["units"],
        "statistic": "equal_weight_deduplicated_family_mean",
        "expected_member_count": len(expected_keys),
        "member_count": len(keys),
        "included_members": keys,
        "missing_members": [key for key in expected_keys if key not in keys],
        "member_weights": weights_for(keys, definitions),
        "member_sources": [{"key": key, **provenance[key]} for key in keys if key in provenance],
        "member_errors": errors,
        "ensemble_scope": f"{len(keys)}/{len(expected_keys)} canonical forecast families",
        "baseline": {
            "status": "native_model_baselines",
            "method": "equal mean of source-native anomaly fields; no common climatology imposed",
        },
        "status": "planned",
    }


def render_product_run(
    *,
    args: argparse.Namespace,
    product: str,
    init: str,
    leads: list[int],
    seasonal_leads: list[int],
    output_dir: Path,
    borders: list[Path],
    root: Path,
    member_grids: dict[int, dict[str, Grid]],
    height_grids: dict[int, dict[str, Grid]],
    provenance: dict[int, dict[str, dict[str, Any]]],
    errors: dict[int, dict[str, str]],
) -> tuple[dict[str, Any], int]:
    expected = canonical_members(product)
    definitions = {member.key: member for member in expected}
    ordered_keys = [member.key for member in expected]
    run_id = f"superensemble-{init}-{product}"
    run: dict[str, Any] = {
        "id": run_id,
        "model": "Seasonal Super Ensemble",
        "source": "wall.cloud deduplicated seasonal super ensemble",
        "source_url": c3s.SOURCE_URL,
        "source_urls": SOURCE_URLS,
        "product": product,
        "init_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
        "field": c3s.PRODUCT_SPECS[product]["field"],
        "units": c3s.PRODUCT_SPECS[product]["units"],
        "statistic": "equal_weight_deduplicated_family_mean",
        "aggregation": "monthly or seasonal mean of canonical non-overlapping forecast-family anomalies",
        "ensemble_scope": f"up to {len(expected)} canonical forecast families",
        "membership_policy": membership_ledger(product),
        "synthetic_preview": bool(args.synthetic_preview),
        "targets": [],
        "status": "planned",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
    }
    failures = 0
    render_spec = product_spec(product, synthetic=args.synthetic_preview)
    for lead in leads:
        target = c3s.target_month(init, lead)
        keys = [key for key in ordered_keys if key in member_grids[lead]]
        entry = target_base(
            run_id=run_id,
            product=product,
            init=init,
            lead=lead,
            target=target,
            keys=keys,
            expected=expected,
            definitions=definitions,
            provenance=provenance[lead],
            errors=errors[lead],
        )
        if len(keys) < args.minimum_members:
            failures += 1
            entry["status"] = "failed"
            entry["error"] = f"only {len(keys)} canonical families available; minimum is {args.minimum_members}"
            run["targets"].append(entry)
            continue
        try:
            anomaly = aligned_mean(member_grids[lead], keys, f"{product} lead {lead}")
            height_keys = [key for key in keys if key in height_grids[lead]]
            height = aligned_mean(height_grids[lead], height_keys, f"height lead {lead}") if height_keys else None
            entry["height_member_count"] = len(height_keys)
            if not args.decode_only:
                output = output_dir / init[:8] / f"superensemble_{c3s.PRODUCT_SPECS[product]['variable']}_{target}.jpg"
                render_map(
                    anomaly,
                    init,
                    target,
                    lead,
                    list(range(len(keys))),
                    output,
                    anomaly=True,
                    baseline_label="Native-model anomaly baselines",
                    border_paths=borders,
                    ensemble_label=f"{len(keys)}-family deduplicated mean",
                    height_grid=height,
                    product_spec=render_spec,
                )
                entry["image"] = relative_path(output, root)
            entry["status"] = "partial" if len(keys) < len(expected) else ("decoded" if args.decode_only else "rendered")
        except Exception as exc:
            failures += 1
            entry["status"] = "failed"
            entry["error"] = str(exc)
            print(f"super ensemble {product} lead {lead} failed: {exc}", file=sys.stderr)
        run["targets"].append(entry)

    if seasonal_leads:
        first_lead, last_lead = seasonal_leads[0], seasonal_leads[-1]
        first_target = c3s.target_month(init, first_lead)
        last_target = c3s.target_month(init, last_lead)
        target = f"{first_target}-{last_target}"
        # A seasonal blend uses the intersection, not the union, so every
        # source has the same weight in each constituent month.
        common = set(ordered_keys)
        for lead in seasonal_leads:
            common &= set(member_grids[lead])
        keys = [key for key in ordered_keys if key in common]
        seasonal_provenance = {
            key: {
                "member": definitions[key].manifest(),
                "monthly_sources": [provenance[lead].get(key, {}) for lead in seasonal_leads],
            }
            for key in keys
        }
        seasonal_errors = {
            f"lead{lead}_{key}": message
            for lead in seasonal_leads
            for key, message in errors[lead].items()
        }
        entry = target_base(
            run_id=run_id,
            product=product,
            init=init,
            lead=f"{first_lead}–{last_lead}",
            target=target,
            keys=keys,
            expected=expected,
            definitions=definitions,
            provenance=seasonal_provenance,
            errors=seasonal_errors,
        )
        entry["monthly_leads"] = seasonal_leads
        entry["composition_rule"] = "intersection of canonical members available for every month"
        if len(keys) < args.minimum_members:
            failures += 1
            entry["status"] = "failed"
            entry["error"] = f"only {len(keys)} canonical families complete across the seasonal window; minimum is {args.minimum_members}"
        else:
            try:
                reducer = c3s.PRODUCT_SPECS[product]["seasonal_reducer"]
                member_seasonal = {
                    key: combine_member_months(
                        [member_grids[lead][key] for lead in seasonal_leads],
                        reducer,
                        f"{product} seasonal {key}",
                    )
                    for key in keys
                }
                anomaly = aligned_mean(member_seasonal, keys, f"{product} seasonal blend")
                height_keys = [
                    key for key in keys
                    if all(key in height_grids[lead] for lead in seasonal_leads)
                ]
                member_heights = {
                    key: combine_member_months(
                        [height_grids[lead][key] for lead in seasonal_leads],
                        "mean",
                        f"seasonal height {key}",
                    )
                    for key in height_keys
                }
                height = aligned_mean(member_heights, height_keys, "seasonal height blend") if height_keys else None
                entry["height_member_count"] = len(height_keys)
                if not args.decode_only:
                    output = output_dir / init[:8] / f"superensemble_{c3s.PRODUCT_SPECS[product]['variable']}_{target}.jpg"
                    render_map(
                        anomaly,
                        init,
                        first_target,
                        f"{first_lead}–{last_lead}",
                        list(range(len(keys))),
                        output,
                        anomaly=True,
                        baseline_label="Native-model anomaly baselines",
                        border_paths=borders,
                        period_label=c3s.period_label(first_target, last_target),
                        ensemble_label=f"{len(keys)}-family deduplicated mean",
                        height_grid=height,
                        product_spec=render_spec,
                    )
                    entry["image"] = relative_path(output, root)
                entry["status"] = "partial" if len(keys) < len(expected) else ("decoded" if args.decode_only else "rendered")
            except Exception as exc:
                failures += 1
                entry["status"] = "failed"
                entry["error"] = str(exc)
                print(f"super ensemble {product} seasonal window failed: {exc}", file=sys.stderr)
        run["targets"].append(entry)

    statuses = [str(target.get("status", "")) for target in run["targets"]]
    usable = any(status in {"rendered", "decoded", "partial"} for status in statuses)
    run["status"] = "failed" if not usable else ("partial" if any(status in {"partial", "failed"} for status in statuses) else ("decoded" if args.decode_only else "rendered"))
    run["output_dir"] = relative_path(output_dir, root)
    return run, failures


def write_manifest(
    path: Path,
    entries: Iterable[dict[str, Any]],
    previous: Path | None,
    retain_cycles: int,
) -> None:
    if retain_cycles < 1:
        raise SuperEnsembleError("manifest retention must keep at least one cycle")
    all_entries: list[dict[str, Any]] = []
    for candidate in (previous, path):
        if not candidate or not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            all_entries.extend(run for run in payload.get("runs", []) if isinstance(run, dict))
        except (OSError, ValueError) as exc:
            raise SuperEnsembleError(f"could not read prior super-ensemble manifest {candidate}: {exc}") from exc
    all_entries.extend(entries)
    unique = {str(run["id"]): run for run in all_entries if run.get("id")}
    ordered = sorted(
        unique.values(),
        key=lambda run: (str(run.get("init_utc", "")), str(run.get("id", ""))),
        reverse=True,
    )
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle and cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:retain_cycles])
    payload = {
        "schema_version": 1,
        "kind": "deduplicated_seasonal_superensemble_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "wall.cloud deduplicated seasonal super ensemble",
        "source_url": c3s.SOURCE_URL,
        "source_urls": SOURCE_URLS,
        "product_labels": PRODUCT_LABELS,
        "membership_policy": {
            "weighting_unit": "canonical non-overlapping forecast-family source",
            "weighting": "equal weight",
            "seasonal_membership": "intersection across all constituent months",
            "native_baselines": True,
            "excluded_packages_by_product": {
                product: canonical_exclusions(product) for product in PRODUCTS
            },
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
    parser.add_argument("--product", default="500mb_height_anomaly", help="one product, a comma-separated list, or all")
    parser.add_argument("--init", default="latest", help="initialization as YYYYMM or latest")
    parser.add_argument("--lead-months", default="4,5,6")
    parser.add_argument("--seasonal-window", default="4,5,6")
    parser.add_argument("--systems", default="", help="optional C3S centre=system overrides")
    parser.add_argument("--climo-start", type=int, default=cansips.CANSIPS_HINDCAST_START)
    parser.add_argument("--climo-end", type=int, default=cansips.CANSIPS_HINDCAST_END)
    parser.add_argument("--minimum-members", type=int, default=6)
    parser.add_argument("--c3s-cache-dir", default=".cache/c3s")
    parser.add_argument("--cansips-cache-dir", default=".cache/cansips")
    parser.add_argument("--cfsv2-cache-dir", default=".cache/cfsv2")
    parser.add_argument("--cfsv2-rolling-state-dir", default=".cache/cfsv2/rolling")
    parser.add_argument("--cfsv2-anchor-init", default="latest", help="rolling CFSv2 anchor cycle or latest")
    parser.add_argument("--cfsv2-rolling-days", type=int, default=10)
    parser.add_argument("--cfsv2-rolling-member", type=int, default=1)
    parser.add_argument("--nmme-cache-dir", default=".cache/nmme")
    parser.add_argument("--geos-cache-dir", default=".cache/geos-s2s3")
    parser.add_argument("--border-cache-dir", default=".cache/superensemble")
    parser.add_argument("--output-dir", default="public/seasonal/superensemble")
    parser.add_argument("--manifest", default="public/seasonal/superensemble_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    parser.add_argument("--wgrib2", default="")
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--border-geojson", action="append", type=Path)
    parser.add_argument("--no-borders", action="store_true")
    parser.add_argument("--decode-only", action="store_true")
    parser.add_argument("--force-decode", action="store_true")
    parser.add_argument("--synthetic-preview", action="store_true", help="render deterministic style data without downloading forecasts")
    return parser


def run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    products = selected_products(args.product)
    init = c3s.parse_init(args.init)
    leads = c3s.parse_int_list(args.lead_months, "lead months", 1, 6)
    seasonal = c3s.parse_int_list(args.seasonal_window, "seasonal window", 1, 6) if args.seasonal_window else []
    if seasonal:
        if seasonal != list(range(min(seasonal), max(seasonal) + 1)):
            raise SuperEnsembleError("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal))
    if args.minimum_members < 2:
        raise SuperEnsembleError("--minimum-members must be at least 2")
    if not 1 <= args.cfsv2_rolling_days <= 30:
        raise SuperEnsembleError("--cfsv2-rolling-days must be between 1 and 30")
    if not 1 <= args.cfsv2_rolling_member <= 4:
        raise SuperEnsembleError("--cfsv2-rolling-member must be between 1 and 4")
    if args.climo_start < cansips.CANSIPS_HINDCAST_START or args.climo_end > cansips.CANSIPS_HINDCAST_END or args.climo_start > args.climo_end:
        raise SuperEnsembleError(
            f"CanSIPS climatology years must stay inside {cansips.CANSIPS_HINDCAST_START}-{cansips.CANSIPS_HINDCAST_END}"
        )

    c3s_cache = resolve_path(args.c3s_cache_dir, root)
    cansips_cache = resolve_path(args.cansips_cache_dir, root)
    cfsv2_cache = resolve_path(args.cfsv2_cache_dir, root)
    cfsv2_state = resolve_path(args.cfsv2_rolling_state_dir, root)
    nmme_cache = resolve_path(args.nmme_cache_dir, root)
    geos_cache = resolve_path(args.geos_cache_dir, root)
    border_cache = resolve_path(args.border_cache_dir, root)
    output_dir = resolve_path(args.output_dir, root)
    manifest = resolve_path(args.manifest, root)
    previous = resolve_path(args.previous_manifest, root) if args.previous_manifest else None
    needs_borders = not args.decode_only or "sea_surface_temperature_anomaly" in products
    borders = ensure_border_files(args, border_cache, root) if needs_borders else []
    systems = c3s.parse_system_overrides(args.systems)
    wgrib2 = "" if args.synthetic_preview else cansips.find_wgrib2(args.wgrib2)

    entries: list[dict[str, Any]] = []
    total_failures = 0
    for product in products:
        expected = canonical_members(product)
        errors = {lead: {} for lead in leads}
        if args.synthetic_preview:
            member_grids, height_grids, provenance = synthetic_members(product, leads, expected)
        else:
            member_grids = {lead: {} for lead in leads}
            height_grids = {lead: {} for lead in leads}
            provenance = {lead: {} for lead in leads}
            load_c3s_members(
                product=product,
                init=init,
                leads=leads,
                cache_dir=c3s_cache,
                root=root,
                systems=systems,
                member_grids=member_grids,
                height_grids=height_grids,
                provenance=provenance,
                errors=errors,
                decode_only=args.decode_only,
            )
            load_cfsv2_member(
                args=args,
                product=product,
                init=init,
                leads=leads,
                cache_dir=cfsv2_cache,
                state_dir=cfsv2_state,
                root=root,
                wgrib2=wgrib2,
                member_grids=member_grids,
                height_grids=height_grids,
                provenance=provenance,
                errors=errors,
            )
            load_cansips_member(
                args=args,
                product=product,
                init=init,
                leads=leads,
                cache_dir=cansips_cache,
                root=root,
                wgrib2=wgrib2,
                member_grids=member_grids,
                height_grids=height_grids,
                provenance=provenance,
                errors=errors,
            )
            load_geos_member(
                args=args,
                product=product,
                init=init,
                leads=leads,
                cache_dir=geos_cache,
                border_paths=borders,
                member_grids=member_grids,
                provenance=provenance,
                errors=errors,
            )
            load_nmme_members(
                product=product,
                init=init,
                leads=leads,
                cache_dir=nmme_cache,
                member_grids=member_grids,
                provenance=provenance,
                errors=errors,
            )
        entry, failures = render_product_run(
            args=args,
            product=product,
            init=init,
            leads=leads,
            seasonal_leads=seasonal,
            output_dir=output_dir,
            borders=borders,
            root=root,
            member_grids=member_grids,
            height_grids=height_grids,
            provenance=provenance,
            errors=errors,
        )
        entries.append(entry)
        total_failures += failures

    write_manifest(manifest, entries, previous, args.retain_cycles)
    usable = any(entry.get("status") in {"rendered", "decoded", "partial"} for entry in entries)
    print(f"wrote super-ensemble manifest: {manifest} ({len(entries)} product run{'s' if len(entries) != 1 else ''})")
    return 0 if usable else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (SuperEnsembleError, c3s.C3SError, cansips.CanSIPSError, geos.GEOSS2S3Error, nmme.NMMEError) as exc:
        print(f"SUPER ENSEMBLE ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

