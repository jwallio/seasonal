#!/usr/bin/env python3
"""Static SEAS5 adapter and viewer contract checks."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ADAPTER = ROOT / "scripts" / "seas5_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_seas5.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "seas5.yml"
DOC = ROOT / "docs" / "SEASONAL_SEAS5.md"
PAGE = ROOT / "public" / "seasonal" / "seas5" / "index.html"


def load_adapter():
    spec = importlib.util.spec_from_file_location("seas5_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load SEAS5 adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    for path, label in ((ADAPTER, "adapter"), (WRAPPER, "wrapper"), (WORKFLOW, "workflow"), (DOC, "documentation"), (PAGE, "viewer")):
        check(path.exists(), f"SEAS5 {label} missing")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
    for term in (
        "planette-c3s-seasonal-forecasts",
        "AWS_REGION = \"us-east-2\"",
        "icechunk",
        "readonly_session(\"main\")",
        "z500",
        "m**2 s**-2",
        "GEOPOTENTIAL_GRAVITY = 9.80665",
        "init_time",
        "valid_time",
        "hindcast_climatology",
        "HINDCAST_START = 1981",
        "HINDCAST_END = 2016",
        "500mb_height_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "sst_anomaly",
        "mslp_anomaly",
        "dask[array]",
        "seasonal_period_label",
        "write_manifest",
        "archive_latest_init",
        "source_store_year",
        "Retaining",
    ):
        check(term in adapter or term in workflow or term in page, f"missing SEAS5 contract term: {term}")
    for term in (
        "product:",
        "SEAS5_PRODUCT",
        "Restore SEAS5 climatology cache",
        "Restore published SEAS5 run history",
        "--previous-manifest",
        "--retain-runs 4",
        "peaceiris/actions-gh-pages@v4",
    ):
        check(term in workflow, f"workflow missing SEAS5 term: {term}")
    for term in ("id=\"product-select\"", "id=\"run-select\"", "seas5_manifest.json", "timeZone:'UTC'"):
        check(term in page, f"viewer missing SEAS5 term: {term}")
    module = load_adapter()
    check(module.target_month("2025080100", 4) == "202512", "lead-month target conversion should produce December")
    check(module.target_month("2025080100", 5) == "202601", "lead-month target conversion should cross the year boundary")
    check(module.seasonal_period_label("202512", "202602") == "DJF 2026", "DJF label should use the ending year")
    check(round(float(module.convert_values([[module.GEOPOTENTIAL_GRAVITY]], module.PRODUCT_SPECS[module.Z500_ANOMALY], "202512")[0][0]), 5) == 1.0, "z500 conversion should divide by gravity")
    check(round(float(module.convert_values([[1.0]], module.PRODUCT_SPECS[module.PRECIP_ANOMALY], "202601")[0][0]), 5) == round(31 * 86400 / 25.4, 5), "precipitation conversion should use calendar-month seconds")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [{"id": f"old-{index}", "init_utc": f"2025-0{index}-01T00:00:00Z"} for index in range(1, 5)]}), encoding="utf-8")
        module.write_manifest(output, ROOT, {"id": "current", "init_utc": "2026-08-13T00:00:00Z"}, previous, 4)
        retained = json.loads(output.read_text(encoding="utf-8"))["runs"]
        check([run["id"] for run in retained] == ["current", "old-4", "old-3", "old-2"], "manifest should retain current plus three prior runs")
    print("SEAS5 CONTRACT OK: public archive, Zarr access, conversions, hindcast baseline, viewer, workflow, retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
