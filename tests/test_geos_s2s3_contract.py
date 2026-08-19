#!/usr/bin/env python3
"""Static and unit contracts for the numerical NASA GEOS-S2S-3 adapter."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "geos_s2s3_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "geos-s2s3.yml"
PAGE = ROOT / "public" / "seasonal" / "geos_s2s3" / "index.html"
DOC = ROOT / "docs" / "SEASONAL_GEOS_S2S3.md"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("geos_s2s3_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load NASA GEOS-S2S-3 adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGE, DOC):
        check(path.exists(), f"missing NASA contract file: {path}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    module = load_adapter()

    check(module.EXPECTED_TOTAL_MEMBERS == 40, "NASA package must require all 40 member files")
    check(module.EXPECTED_LONG_RANGE_MEMBERS == 10, "NASA long range must require ten selected members")
    check(module.PRODUCT_Z500_ANOMALY not in module.DEFAULT_PRODUCTS, "unverified z500 must not run by default")
    height_spec = module.PRODUCT_SPECS[module.PRODUCT_Z500_ANOMALY]
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "a future validated GEOS 500-mb map should use the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "a future validated GEOS 500-mb map should use 10-metre labelled bounds")
    check(module.SUPERENSEMBLE_PRODUCTS == frozenset(module.DEFAULT_PRODUCTS), "only validated NASA products may enter the blend")
    mslp_spec = module.PRODUCT_SPECS[module.PRODUCT_MSLP_ANOMALY]
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-10.0, 10.0), "NASA MSLP should use ±10 hPa")
    sst_spec = module.PRODUCT_SPECS[module.PRODUCT_SST_ANOMALY]
    check((sst_spec["anomaly_min"], sst_spec["anomaly_max"]) == (-3.0, 3.0), "NASA SST should use ±3°C")
    check(sst_spec["map_domain"] == "ocean", "NASA SST must retain an ocean-only render mask")
    for product in (
        module.PRODUCT_T850_ANOMALY,
        module.PRODUCT_T2M_ANOMALY,
        module.PRODUCT_PRECIPITATION_ANOMALY,
        module.PRODUCT_MSLP_ANOMALY,
        module.PRODUCT_SST_ANOMALY,
    ):
        check(product in module.DEFAULT_PRODUCTS, f"validated NASA product missing: {product}")
    check(module.target_month("202608", 4) == "202612", "NASA offset 4 should align to December")
    check(module.archive_url("202608", module.PRODUCT_SPECS[module.PRODUCT_T2M_ANOMALY]).endswith("/202608/202608_at.tar.xz"), "NASA archive URL is misaligned")
    check(module.drift_url("202608", "202701").endswith("/aug.APCN.monthly.drift.01.nc4"), "NASA drift URL is misaligned")

    for term in (
        "NRT/APCN/", "Drift/for_APCN/", "provider drift climatology", "refusing to publish",
        "200 hPa", "500 hPa", "xarray", "netCDF4", "geos_s2s3_manifest.json",
        "geos-s2s3-pages-", "850mb_temperature_anomaly", "sea_surface_temperature_anomaly",
    ):
        check(term in adapter or term in workflow or term in page or term in doc, f"missing NASA term: {term}")
    check("pre-rendered" not in page, "NASA page must not describe the numerical adapter as pre-rendered")
    check("timeout-minutes: 180" in workflow, "NASA numerical workflow needs the extended runtime")

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [
            {"id": f"old-{month}", "init_utc": f"2025-{month:02d}-01T00:00:00Z", "product": module.PRODUCT_T2M_ANOMALY, "targets": []}
            for month in range(1, 5)
        ]}), encoding="utf-8")
        module.write_manifest(output, [{
            "id": "current", "init_utc": "2026-08-01T00:00:00Z",
            "product": module.PRODUCT_T2M_ANOMALY, "targets": [],
        }], previous, 4)
        payload = json.loads(output.read_text(encoding="utf-8"))
        check(len(payload["runs"]) == 4, "NASA retention should keep current plus three prior cycles")
        check(payload["comparison_products"] == [], "NASA must stay out of 500-mb compare until z500 passes")

    print("NASA GEOS-S2S-3 CONTRACT OK: numerical archives, member counts, drift, products, safety guard, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
