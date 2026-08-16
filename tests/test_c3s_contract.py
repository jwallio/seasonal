#!/usr/bin/env python3
"""Static C3S adapter, workflow, and dashboard contracts."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "c3s_seasonal.py"
GRIB_ADAPTER = ROOT / "scripts" / "seas5_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "c3s.yml"
PAGES = ROOT / ".github" / "workflows" / "publish-pages.yml"
PAGE = ROOT / "public" / "seasonal" / "c3s" / "index.html"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("c3s_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load C3S adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, GRIB_ADAPTER, WORKFLOW, PAGES, PAGE):
        check(path.exists(), f"missing C3S contract file: {path.name}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    grib_adapter = GRIB_ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    for term in (
        "seasonal-postprocessed-pressure-levels", "seasonal-postprocessed-single-levels",
        "seasonal-monthly-pressure-levels", "originating_centre", "product_type",
        "ensemble_mean", "multi-system", "component", "CDS_API_KEY",
        "retain-cycles", "systems", "system",
    ):
        check(term in adapter or term in workflow or term in pages, f"missing C3S term: {term}")
    check("cfgrib.open_datasets" in grib_adapter, "C3S/JMA GRIB decoder should discover heterogeneous raw pressure-level groups")
    check('"seasonal-monthly-pressure-levels"' in adapter, "C3S 500-mb product should retain the raw geopotential source")
    check("height_grid=height" in adapter, "C3S renderer should pass decoded absolute heights to the map renderer")
    module = load_adapter()
    check(set(module.CENTRES) == {"ecmwf", "ukmo", "meteo_france", "dwd", "cmcc", "ncep", "jma", "eccc", "bom"}, "C3S centre catalog is incomplete")
    check(module.target_month("2026080100", 4) == "202612", "C3S lead conversion should produce December")
    check(module.period_label("202612", "202702") == "DJF 2027", "C3S DJF period label should use the ending year")
    check(len(module.PRODUCT_SPECS["500mb_height_anomaly"]["anomaly_ticks"]) == len(module.PRODUCT_SPECS["500mb_height_anomaly"]["anomaly_palette"]) + 1, "C3S 500-mb color bounds must align with swatches")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [{"id": f"old-{year}", "init_utc": f"2025-{year:02d}-01T00:00:00Z"} for year in range(1, 5)]}), encoding="utf-8")
        module.write_manifest(output, [{"id": "current", "init_utc": "2026-08-01T00:00:00Z"}], previous, 4)
        payload = json.loads(output.read_text(encoding="utf-8"))
        check(len(payload["runs"]) == 4, "C3S retention should keep the current cycle plus three prior cycles")
    print("C3S CONTRACT OK: official centres/systems, native anomalies, blend metadata, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
