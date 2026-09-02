#!/usr/bin/env python3
"""Check CDS seasonal inventory readiness and live publication coverage.

The CDS catalogue exposes a small, unauthenticated constraints document for
each dataset.  It is updated when a new centre/system/month combination is
queryable through the CDS API.  Reading that inventory is substantially
cheaper and more reliable than submitting repeated GRIB retrieval jobs.

This checker keeps three states separate for each publishing worker:

* ``source_ready``: every source field needed by the configured suite exists;
* ``published``: the live manifest contains a rendered current-month suite;
* ``needs_dispatch``: the release window is open, the source is ready, and the
  live suite is not complete.

It intentionally uses only the Python standard library so the release monitor
can run quickly on a GitHub-hosted runner without CDS credentials.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CATALOGUE_ROOT = "https://cds.climate.copernicus.eu/api/catalogue/v1/collections"
COLLECTIONS = (
    "seasonal-postprocessed-pressure-levels",
    "seasonal-postprocessed-single-levels",
    "seasonal-monthly-pressure-levels",
)
LEADS = ("4", "5", "6")

# Keep this operational centre/system list aligned with c3s_seasonal.CENTRES.
CENTRE_SYSTEMS: dict[str, str] = {
    "ecmwf": "51",
    "ukmo": "610",
    "meteo_france": "9",
    "dwd": "22",
    "cmcc": "4",
    "ncep": "2",
    "jma": "4",
    "eccc": "5",
    "bom": "2",
}

# These centres currently publish the native snowfall anomaly used by the C3S
# multi-system snowfall blend.  NCEP, JMA, and BOM are intentionally excluded.
C3S_SNOWFALL_CENTRES = (
    "ecmwf",
    "ukmo",
    "meteo_france",
    "dwd",
    "cmcc",
    "eccc",
)

SINGLE_LEVEL_CORE_VARIABLES = (
    "2m_temperature_anomaly",
    "total_precipitation_anomalous_rate_of_accumulation",
    "mean_sea_level_pressure_anomaly",
)

EXPECTED_PRODUCTS: dict[str, tuple[str, ...]] = {
    "seas5": (
        "500mb_height_anomaly",
        "500mb_height_anomaly_nh",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "mslp_anomaly",
    ),
    "c3s": (
        "500mb_height_anomaly",
        "500mb_height_anomaly_nh",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "mslp_anomaly",
    ),
    "jma": (
        "500mb_height_anomaly",
        "500mb_height_anomaly_nh",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "mslp_anomaly",
    ),
}

MANIFEST_NAMES = {
    "seas5": "seas5_manifest.json",
    "c3s": "c3s_manifest.json",
    "jma": "jma_manifest.json",
}

# ECMWF's own member dissemination is on the 5th.  The C3S/CDS inventory used
# by this repository is operationally available on the 6th at 12 UTC.  Other
# C3S systems, including JMA, are published on the 10th at 12 UTC.
RELEASE_WINDOWS = {
    "seas5": (6, 12),
    "c3s": (10, 12),
    "jma": (10, 12),
}


class ReleaseCheckError(RuntimeError):
    """Raised when the release inventory cannot be checked safely."""


@dataclass(frozen=True)
class Requirement:
    collection: str
    dimensions: tuple[tuple[str, str], ...]
    label: str

    def selection(self, target: str) -> dict[str, str]:
        return {
            "year": target[:4],
            "month": target[4:],
            **dict(self.dimensions),
        }


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_target(value: str) -> str:
    if not re.fullmatch(r"\d{6}", value):
        raise ReleaseCheckError("target must use YYYYMM")
    try:
        dt.datetime(int(value[:4]), int(value[4:]), 1)
    except ValueError as exc:
        raise ReleaseCheckError(f"invalid target month {value!r}") from exc
    return value


def release_time(target: str, worker: str) -> dt.datetime:
    day, hour = RELEASE_WINDOWS[worker]
    return dt.datetime(
        int(target[:4]),
        int(target[4:]),
        day,
        hour,
        tzinfo=dt.timezone.utc,
    )


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_json(url: str, timeout: float) -> tuple[Any, dict[str, str | None]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "jwallio-seasonal-release-check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            metadata = {
                "url": response.geturl(),
                "last_modified": response.headers.get("Last-Modified"),
                "etag": response.headers.get("ETag"),
            }
            return payload, metadata
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReleaseCheckError(f"could not read {url}: {exc}") from exc


def constraints_link(collection: Mapping[str, Any], collection_url: str) -> str:
    for link in collection.get("links", []):
        if isinstance(link, Mapping) and link.get("rel") == "constraints" and link.get("href"):
            return urllib.parse.urljoin(collection_url, str(link["href"]))
    raise ReleaseCheckError(f"CDS catalogue collection has no constraints link: {collection_url}")


def load_constraints(timeout: float = 30.0) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    inventories: dict[str, list[dict[str, Any]]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for name in COLLECTIONS:
        collection_url = f"{CATALOGUE_ROOT}/{name}"
        collection, collection_metadata = fetch_json(collection_url, timeout)
        if not isinstance(collection, Mapping):
            raise ReleaseCheckError(f"unexpected CDS collection response for {name}")
        source_url = constraints_link(collection, collection_url)
        records, constraints_metadata = fetch_json(source_url, timeout)
        if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
            raise ReleaseCheckError(f"unexpected CDS constraints response for {name}")
        inventories[name] = [dict(item) for item in records]
        provenance[name] = {
            "collection_url": collection_metadata["url"],
            "constraints_url": constraints_metadata["url"],
            "constraints_last_modified": constraints_metadata["last_modified"],
            "constraints_etag": constraints_metadata["etag"],
            "record_count": len(records),
        }
    return inventories, provenance


def make_requirement(
    collection: str,
    centre: str,
    system: str,
    variable: str,
    lead: str,
    *,
    product_type: str,
    pressure_level: str | None = None,
) -> Requirement:
    dimensions = [
        ("originating_centre", centre),
        ("system", system),
        ("product_type", product_type),
        ("variable", variable),
        ("leadtime_month", lead),
    ]
    level_label = ""
    if pressure_level is not None:
        dimensions.append(("pressure_level", pressure_level))
        level_label = f"/{pressure_level}hPa"
    return Requirement(
        collection=collection,
        dimensions=tuple(dimensions),
        label=f"{centre}/system-{system}/{variable}{level_label}/lead-{lead}",
    )


def source_requirements(worker: str) -> list[Requirement]:
    if worker not in EXPECTED_PRODUCTS:
        raise ReleaseCheckError(f"unsupported worker {worker!r}")
    centres: Iterable[str]
    if worker == "seas5":
        centres = ("ecmwf",)
    elif worker == "jma":
        centres = ("jma",)
    else:
        centres = CENTRE_SYSTEMS

    requirements: list[Requirement] = []
    for centre in centres:
        system = CENTRE_SYSTEMS[centre]
        for lead in LEADS:
            requirements.extend(
                (
                    make_requirement(
                        "seasonal-postprocessed-pressure-levels",
                        centre,
                        system,
                        "geopotential_anomaly",
                        lead,
                        product_type="ensemble_mean",
                        pressure_level="500",
                    ),
                    make_requirement(
                        "seasonal-postprocessed-pressure-levels",
                        centre,
                        system,
                        "temperature_anomaly",
                        lead,
                        product_type="ensemble_mean",
                        pressure_level="850",
                    ),
                    make_requirement(
                        "seasonal-monthly-pressure-levels",
                        centre,
                        system,
                        "geopotential",
                        lead,
                        product_type="monthly_mean",
                        pressure_level="500",
                    ),
                )
            )
            for variable in SINGLE_LEVEL_CORE_VARIABLES:
                requirements.append(
                    make_requirement(
                        "seasonal-postprocessed-single-levels",
                        centre,
                        system,
                        variable,
                        lead,
                        product_type="ensemble_mean",
                    )
                )

    snowfall_centres: Iterable[str]
    if worker == "seas5":
        snowfall_centres = ("ecmwf",)
    elif worker == "c3s":
        snowfall_centres = C3S_SNOWFALL_CENTRES
    else:
        snowfall_centres = ()
    for centre in snowfall_centres:
        system = CENTRE_SYSTEMS[centre]
        for lead in LEADS:
            requirements.append(
                make_requirement(
                    "seasonal-postprocessed-single-levels",
                    centre,
                    system,
                    "snowfall_anomalous_rate_of_accumulation",
                    lead,
                    product_type="ensemble_mean",
                )
            )
    return requirements


def record_supports(record: Mapping[str, Any], selection: Mapping[str, str]) -> bool:
    for dimension, selected in selection.items():
        values = record.get(dimension, [])
        if isinstance(values, (str, int, float)):
            values = [values]
        if selected not in {str(value) for value in values}:
            return False
    return True


def missing_source_requirements(
    worker: str,
    target: str,
    inventories: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[str]:
    missing: list[str] = []
    for requirement in source_requirements(worker):
        records = inventories.get(requirement.collection, ())
        if not any(record_supports(record, requirement.selection(target)) for record in records):
            missing.append(requirement.label)
    return missing


def init_matches_target(value: Any, target: str) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 8 and digits[:4].isdigit():
        # ISO timestamps become YYYYMMDD..., while locale-formatted timestamps
        # become MMDDYYYY... and are intentionally not accepted here.
        return digits[:6] == target
    return False


def run_is_complete(run: Mapping[str, Any]) -> bool:
    if run.get("status") != "rendered":
        return False
    targets = run.get("targets")
    if not isinstance(targets, list) or len(targets) < 4:
        return False
    return all(isinstance(item, Mapping) and item.get("status") == "rendered" for item in targets)


def published_products(manifest: Mapping[str, Any], worker: str, target: str) -> set[str]:
    found: set[str] = set()
    runs = manifest.get("runs", [])
    if not isinstance(runs, list):
        return found
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        product = str(run.get("product") or "")
        if product not in EXPECTED_PRODUCTS[worker]:
            continue
        if not init_matches_target(run.get("init_utc"), target) or not run_is_complete(run):
            continue
        component = str(run.get("component") or "")
        if worker == "c3s" and component != "multisystem":
            continue
        if worker == "jma" and component != "jma":
            continue
        found.add(product)
    return found


def empty_manifest() -> dict[str, Any]:
    return {"runs": []}


def load_manifest(url: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload, metadata = fetch_json(url, timeout)
    except ReleaseCheckError as exc:
        cause = exc.__cause__
        if isinstance(cause, urllib.error.HTTPError) and cause.code == 404:
            return empty_manifest(), {"url": url, "status": "not_found"}
        raise
    if not isinstance(payload, Mapping):
        raise ReleaseCheckError(f"unexpected manifest response for {url}")
    return dict(payload), {**metadata, "status": "loaded"}


def evaluate_worker(
    worker: str,
    target: str,
    inventories: Mapping[str, Sequence[Mapping[str, Any]]],
    manifest: Mapping[str, Any],
    *,
    now: dt.datetime,
) -> dict[str, Any]:
    release = release_time(target, worker)
    missing_source = missing_source_requirements(worker, target, inventories)
    present = published_products(manifest, worker, target)
    missing_products = sorted(set(EXPECTED_PRODUCTS[worker]) - present)
    source_ready = not missing_source
    published = not missing_products
    window_open = now.astimezone(dt.timezone.utc) >= release
    return {
        "release_utc": iso_utc(release),
        "window_open": window_open,
        "source_ready": source_ready,
        "missing_source_count": len(missing_source),
        "missing_source": missing_source,
        "published": published,
        "published_products": sorted(present),
        "missing_products": missing_products,
        "needs_dispatch": window_open and source_ready and not published,
    }


def write_github_outputs(path: Path, target: str, workers: Mapping[str, Mapping[str, Any]]) -> None:
    lines = [f"target={target}"]
    for worker, status in workers.items():
        for field in ("window_open", "source_ready", "published", "needs_dispatch"):
            lines.append(f"{worker}_{field}={str(bool(status[field])).lower()}")
        lines.append(f"{worker}_missing_source_count={status['missing_source_count']}")
        lines.append(f"{worker}_missing_products={','.join(status['missing_products'])}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default=utc_now().strftime("%Y%m"),
        help="nominal initialization month as YYYYMM (default: current UTC month)",
    )
    parser.add_argument(
        "--worker",
        choices=("all", *EXPECTED_PRODUCTS),
        default="all",
        help="publishing worker to evaluate",
    )
    parser.add_argument(
        "--site-base",
        default="https://jwallio.github.io/seasonal",
        help="live site root containing provider manifests",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--json-output", type=Path, help="optional detailed JSON status path")
    parser.add_argument(
        "--github-output",
        type=Path,
        default=Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None,
        help="optional GitHub Actions output file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        target = parse_target(args.target)
        now = utc_now()
        selected = tuple(EXPECTED_PRODUCTS) if args.worker == "all" else (args.worker,)
        inventories, catalogue_provenance = load_constraints(args.timeout)
        workers: dict[str, dict[str, Any]] = {}
        manifests: dict[str, dict[str, Any]] = {}
        base = args.site_base.rstrip("/")
        for worker in selected:
            manifest_url = f"{base}/{MANIFEST_NAMES[worker]}"
            manifest, manifest_provenance = load_manifest(manifest_url, args.timeout)
            manifests[worker] = manifest_provenance
            workers[worker] = evaluate_worker(worker, target, inventories, manifest, now=now)

        report = {
            "schema_version": 1,
            "kind": "seasonal_release_check",
            "checked_utc": iso_utc(now),
            "target": target,
            "catalogue": catalogue_provenance,
            "manifests": manifests,
            "workers": workers,
        }
        encoded = json.dumps(report, indent=2, sort_keys=True)
        print(encoded)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(encoded + "\n", encoding="utf-8")
        if args.github_output:
            write_github_outputs(args.github_output, target, workers)
        return 0
    except ReleaseCheckError as exc:
        print(f"seasonal release check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
