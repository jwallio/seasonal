#!/usr/bin/env python3
"""Remove retired seasonal product entries and assets from a Pages tree.

Provider workers stop emitting retired products, but the Pages publisher merges
new payloads into retained history.  This small migration keeps an old product
from surviving in manifests, branded copies, thumbnails, or provider folders.
The legacy WeatherNext tree is intentionally outside the removal roots.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from seasonal_products import is_retired_product


SEASONAL_MODELS = frozenset({
    "apcc", "c3s", "cansips", "cma_cpsv3", "cfsv2", "geos_s2s3", "jma",
    "nmme", "seas5", "superensemble",
})
RETIRED_ASSET_PATTERN = re.compile(
    r"(?:^|[_./-])(?:sst|ssta|swea|weasd)(?:[_./-]|$)"
    r"|sea[_-]?surface[_-]?temperature|snow[_-]?water[_-]?equivalent",
    re.IGNORECASE,
)


def _manifest_paths(site_root: Path) -> list[Path]:
    paths = set(site_root.glob("*_manifest.json"))
    nested = site_root / "seasonal"
    if nested.is_dir():
        paths.update(nested.glob("*_manifest.json"))
    return sorted(path for path in paths if path.is_file())


def _filter_manifest(payload: dict[str, Any]) -> bool:
    changed = False
    runs = payload.get("runs")
    if isinstance(runs, list):
        retained = [
            run for run in runs
            if not isinstance(run, dict) or not is_retired_product(run.get("product"))
        ]
        if retained != runs:
            payload["runs"] = retained
            changed = True
    labels = payload.get("product_labels")
    if isinstance(labels, dict):
        retained_labels = {
            key: value for key, value in labels.items()
            if not is_retired_product(key)
        }
        if retained_labels != labels:
            payload["product_labels"] = retained_labels
            changed = True
    comparison_products = payload.get("comparison_products")
    if isinstance(comparison_products, list):
        retained_products = [
            product for product in comparison_products
            if not is_retired_product(product)
        ]
        if retained_products != comparison_products:
            payload["comparison_products"] = retained_products
            changed = True
    return changed


def _rewrite_manifest(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    payload = json.loads(original)
    if not isinstance(payload, dict) or not _filter_manifest(payload):
        return False
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return True


def _asset_roots(site_root: Path) -> list[Path]:
    roots: list[Path] = []
    for prefix in (site_root, site_root / "seasonal"):
        roots.extend(prefix / model for model in SEASONAL_MODELS)
        roots.extend((prefix / "share", prefix / "thumbnails"))
    return [root for root in roots if root.is_dir()]


def _retired_asset(path: Path) -> bool:
    return bool(RETIRED_ASSET_PATTERN.search(path.name))


def purge(site_root: Path) -> dict[str, int]:
    site_root = site_root.resolve()
    manifests_changed = sum(_rewrite_manifest(path) for path in _manifest_paths(site_root))
    assets_removed = 0
    for root in _asset_roots(site_root):
        for path in root.rglob("*"):
            if path.is_file() and _retired_asset(path):
                path.unlink()
                assets_removed += 1
    return {"manifests_changed": manifests_changed, "assets_removed": assets_removed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True, help="Merged GitHub Pages tree")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(json.dumps(purge(args.site_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
