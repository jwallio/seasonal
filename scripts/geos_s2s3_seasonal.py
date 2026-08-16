#!/usr/bin/env python3
"""Download official NASA GEOS-S2S-3 seasonal North America charts.

NASA's public GEOS-S2S-3 seasonal interface publishes pre-rendered North
American T2M and precipitation anomaly panels.  The public interface does not
expose a 500-mb product, so this adapter preserves the official chart instead
of pretending it is a field rendered by the common 500-mb pipeline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any, Iterable


NASA_PAGE_URL = "https://gmao.gsfc.nasa.gov/seasonal-forecasts/seasonal-decadal-analysis-prediction-v3/"
NASA_ANOMALY_PAGE_URL = "https://gmao.gsfc.nasa.gov/seasonal-forecasts/seasonal-decadal-analysis-prediction-v3/forecast-data_atmospheric-anomalies/"
NASA_LOOKUP_URL = "https://gmao.gsfc.nasa.gov/lookup_images/"

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "2m_temperature_anomaly": {
        "field": "2-m temperature anomaly", "units": "°C", "api_variable": "T2M", "token": "t2m",
        "label": "2-m Temperature Anomaly", "region": "NA",
    },
    "precipitation_anomaly": {
        "field": "precipitation anomaly", "units": "mm", "api_variable": "Precip", "token": "precip",
        "label": "Precipitation Anomaly", "region": "NA",
    },
}


class GEOSS2S3Error(RuntimeError):
    """A user-actionable NASA source error."""


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
            raise GEOSS2S3Error(f"invalid NASA initialization month: {value}") from exc
        return value
    raise GEOSS2S3Error("--init must be latest or YYYYMM")


def month_after(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def target_range(init: str, months: int) -> tuple[str, str, str]:
    if months < 1:
        raise GEOSS2S3Error("--months must be positive")
    date = dt.datetime.strptime(init, "%Y%m")
    last = month_after(date.year, date.month, months - 1)
    first_code = init
    last_code = f"{last[0]:04d}{last[1]:02d}"
    return f"{first_code}-{last_code}", f"{date:%b %Y}–{dt.datetime(last[0], last[1], 1):%b %Y}", last_code


def lookup_image(product: dict[str, Any], init: str, args: argparse.Namespace) -> str:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - workflow installs requests
        raise GEOSS2S3Error("NASA GEOS-S2S-3 download requires requests") from exc
    date = dt.datetime.strptime(init, "%Y%m")
    params = {
        "slug": "seasonal-decadal-analysis-prediction-v3",
        "type": "atmospheric-anomalies",
        "month": date.strftime("%b"),
        "year": str(date.year),
        "field1": product["api_variable"],
        "field2": product["region"],
        "field3": "Seasonal",
    }
    try:
        response = requests.get(args.lookup_url, params=params, timeout=(30, 60))
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise GEOSS2S3Error(f"NASA image lookup failed: {exc}") from exc
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, list) or not images or not isinstance(images[0], str):
        note = payload.get("note", "no chart was returned") if isinstance(payload, dict) else "invalid response"
        raise GEOSS2S3Error(f"NASA has no {product['api_variable']} North America chart for {init}: {note}")
    return images[0]


def download_image(url: str, output: Path) -> None:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - workflow installs requests
        raise GEOSS2S3Error("NASA GEOS-S2S-3 download requires requests") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        with requests.get(url, stream=True, timeout=(30, 180)) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type.lower() and not url.lower().endswith((".png", ".jpg", ".jpeg")):
                raise GEOSS2S3Error(f"NASA returned a non-image response for {url}")
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if temporary.stat().st_size < 1024:
            raise GEOSS2S3Error("NASA image response was unexpectedly small")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_manifest(path: Path, entries: Iterable[dict[str, Any]], previous: Path | None, retain_cycles: int) -> None:
    all_entries: list[dict[str, Any]] = []
    for existing_path in (previous, path):
        if not existing_path or not existing_path.exists():
            continue
        try:
            payload = json.loads(existing_path.read_text(encoding="utf-8"))
            all_entries.extend(run for run in payload.get("runs", []) if isinstance(run, dict))
        except (OSError, ValueError) as exc:
            raise GEOSS2S3Error(f"could not read previous NASA manifest {existing_path}: {exc}") from exc
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
        "kind": "geos_s2s3_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NASA GEOS-S2S-3 official seasonal charts",
        "source_url": NASA_PAGE_URL,
        "source_urls": [NASA_PAGE_URL, NASA_ANOMALY_PAGE_URL, NASA_LOOKUP_URL],
        "rendering": "official NASA pre-rendered chart; not a common 500-mb field renderer",
        "comparison_products": [],
        "product_labels": {key: value["label"] for key, value in PRODUCT_SPECS.items()},
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
    parser.add_argument("--init", default="latest", help="NASA release month as YYYYMM or latest")
    parser.add_argument("--months", type=int, default=9, help="number of months represented by NASA's seasonal panel")
    parser.add_argument("--lookup-url", default=NASA_LOOKUP_URL)
    parser.add_argument("--output-dir", default="public/seasonal/geos_s2s3")
    parser.add_argument("--manifest", default="public/seasonal/geos_s2s3_manifest.json")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--retain-cycles", type=int, default=4)
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    init = parse_init(args.init)
    target_code, panel_label, last_code = target_range(init, args.months)
    selected = list(PRODUCT_SPECS) if args.product == "all" else [args.product]
    output_dir = Path(args.output_dir) if Path(args.output_dir).is_absolute() else repo_root / args.output_dir
    manifest_path = Path(args.manifest) if Path(args.manifest).is_absolute() else repo_root / args.manifest
    previous = args.previous_manifest if not args.previous_manifest or args.previous_manifest.is_absolute() else repo_root / args.previous_manifest
    init_utc = f"{init[:4]}-{init[4:]}-01T00:00:00Z"
    end_date = dt.datetime.strptime(last_code, "%Y%m")
    end_next = month_after(end_date.year, end_date.month, 1)
    valid_end = f"{end_next[0]:04d}-{end_next[1]:02d}-01T00:00:00Z"
    entries: list[dict[str, Any]] = []
    successes = 0
    for product_name in selected:
        product = PRODUCT_SPECS[product_name]
        target: dict[str, Any] = {
            "id": f"geos-s2s3-{init}-{product['token']}",
            "label": f"North America seasonal panel ({panel_label})",
            "valid_start_utc": f"{init[:4]}-{init[4:]}-01T00:00:00Z",
            "valid_end_utc": valid_end,
            "lead_month": "seasonal panel",
            "target_month": target_code,
            "period_label": panel_label,
            "field": product["field"],
            "units": product["units"],
            "statistic": "NASA GEOS-S2S-3 ensemble mean",
            "ensemble_scope": "NASA GEOS-S2S-3 official panel",
            "status": "planned",
        }
        run_entry: dict[str, Any] = {
            "id": f"geos-s2s3-{init}-{product_name}",
            "init_utc": init_utc,
            "product": product_name,
            "status": "planned",
            "source": "NASA GEOS-S2S-3 official seasonal charts",
            "source_url": NASA_PAGE_URL,
            "targets": [target],
            "output_dir": str(output_dir.relative_to(repo_root)).replace("\\", "/") if output_dir.is_relative_to(repo_root) else str(output_dir),
        }
        try:
            source_url = lookup_image(product, init, args)
            suffix = ".png" if source_url.lower().split("?")[0].endswith(".png") else ".jpg"
            output = output_dir / init / f"geos_s2s3_{product['token']}_{init}{suffix}"
            download_image(source_url, output)
            target["image"] = str(output.relative_to(repo_root)).replace("\\", "/") if output.is_relative_to(repo_root) else str(output)
            target["source_url"] = source_url
            target["status"] = "rendered"
            run_entry["status"] = "rendered"
            successes += 1
            print(f"saved NASA GEOS-S2S-3 {product_name}: {output}")
        except Exception as exc:
            target["status"] = "failed"
            target["error"] = str(exc)
            run_entry["status"] = "failed"
            print(f"NASA GEOS-S2S-3 {product_name} failed: {exc}")
        entries.append(run_entry)
    write_manifest(manifest_path, entries, previous, args.retain_cycles)
    print(f"wrote NASA GEOS-S2S-3 manifest: {manifest_path} ({len(entries)} product run(s))")
    return 0 if successes else 2


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except GEOSS2S3Error as exc:
        print(f"NASA GEOS-S2S-3 ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
