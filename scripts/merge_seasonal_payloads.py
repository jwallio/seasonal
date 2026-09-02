#!/usr/bin/env python3
"""Merge product-scoped seasonal artifacts into one Pages payload.

Each matrix worker receives the last published manifest and writes a fragment
for one product.  The merge must explicitly prefer the run IDs named by that
fragment; otherwise a retained old copy from another worker could overwrite a
new current product while the manifests are combined.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
from typing import Any


RETIRED_PRODUCTS = {"sea_surface_temperature_anomaly", "sst_anomaly"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def _copy_assets(source: Path, destination: Path) -> None:
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(source)
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"unsafe asset path in {source}: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def merge_payloads(
    input_root: Path,
    output_root: Path,
    asset_dir: str,
    manifest_name: str,
    retain_cycles: int,
) -> dict[str, int]:
    if retain_cycles < 1:
        raise ValueError("retention must keep at least one cycle")
    fragments = sorted(input_root.rglob("fragment.json"))
    if not fragments:
        raise ValueError(f"no product fragments found under {input_root}")

    base: dict[str, Any] | None = None
    history: dict[str, dict[str, Any]] = {}
    current: dict[str, dict[str, Any]] = {}
    copied_assets = 0
    products: set[str] = set()
    target_init: str | None = None

    destination_assets = output_root / asset_dir
    destination_assets.mkdir(parents=True, exist_ok=True)
    for fragment_path in fragments:
        fragment = _read_json(fragment_path)
        product = str(fragment.get("product", "")).strip()
        init_utc = str(fragment.get("init_utc", "")).strip()
        run_ids = {str(value) for value in fragment.get("run_ids", []) if value}
        if not product or not init_utc or not run_ids:
            raise ValueError(f"incomplete product fragment: {fragment_path}")
        if product in RETIRED_PRODUCTS:
            continue
        if target_init is None:
            target_init = init_utc
        elif init_utc != target_init:
            raise ValueError(
                f"product fragments target different initializations: {target_init} and {init_utc}"
            )
        products.add(product)
        manifest_path = fragment_path.parent / manifest_name
        asset_path = fragment_path.parent / asset_dir
        if not manifest_path.is_file() or not asset_path.is_dir():
            raise ValueError(f"fragment is missing its manifest or assets: {fragment_path.parent}")
        manifest = _read_json(manifest_path)
        if base is None:
            base = manifest
        runs = [
            run
            for run in manifest.get("runs", [])
            if isinstance(run, dict)
            and run.get("id")
            and run.get("product") not in RETIRED_PRODUCTS
        ]
        found_current = {
            str(run["id"]): run
            for run in runs
            if str(run["id"]) in run_ids and str(run.get("init_utc", "")) == init_utc
        }
        if set(found_current) != run_ids:
            missing = ", ".join(sorted(run_ids - set(found_current)))
            raise ValueError(f"fragment {fragment_path} does not identify current runs: {missing}")
        for run in runs:
            history.setdefault(str(run["id"]), run)
        current.update(found_current)
        before = sum(1 for item in asset_path.rglob("*") if item.is_file())
        _copy_assets(asset_path, destination_assets)
        copied_assets += before

    if base is None or not products:
        raise ValueError("all seasonal fragments were retired or empty")

    merged_by_id = dict(history)
    merged_by_id.update(current)
    ordered = sorted(
        merged_by_id.values(),
        key=lambda run: (str(run.get("init_utc", "")), str(run.get("id", ""))),
        reverse=True,
    )
    cycles: list[str] = []
    for run in ordered:
        cycle = str(run.get("init_utc", ""))
        if cycle and cycle not in cycles:
            cycles.append(cycle)
    keep = set(cycles[:retain_cycles])
    merged = dict(base)
    merged["generated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    merged["retention"] = {
        "max_cycles": retain_cycles,
        "history_cycles": max(0, retain_cycles - 1),
    }
    merged["runs"] = [run for run in ordered if str(run.get("init_utc", "")) in keep]

    output_root.mkdir(parents=True, exist_ok=True)
    output_manifest = output_root / manifest_name
    temporary = output_manifest.with_name(output_manifest.name + ".tmp")
    temporary.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_manifest)
    return {"fragments": len(fragments), "products": len(products), "assets": copied_assets, "runs": len(merged["runs"])}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--asset-dir", required=True)
    parser.add_argument("--manifest-name", required=True)
    parser.add_argument("--retain-cycles", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        summary = merge_payloads(
            args.input_root,
            args.output_root,
            args.asset_dir,
            args.manifest_name,
            args.retain_cycles,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SEASONAL PAYLOAD MERGE ERROR: {exc}")
        return 2
    print(
        f"merged {summary['products']} product fragments, {summary['assets']} assets, "
        f"and {summary['runs']} retained manifest runs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
