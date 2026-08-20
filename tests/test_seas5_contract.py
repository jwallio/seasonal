#!/usr/bin/env python3
"""Static SEAS5 adapter and viewer contract checks."""

import importlib.util
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ADAPTER = ROOT / "scripts" / "seas5_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_seas5.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "seas5.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pages.yml"
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
    for path, label in ((ADAPTER, "adapter"), (WRAPPER, "wrapper"), (WORKFLOW, "workflow"), (PAGES_WORKFLOW, "central Pages workflow"), (DOC, "documentation"), (PAGE, "viewer")):
        check(path.exists(), f"SEAS5 {label} missing")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    pages_workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
    for term in (
        "cds.climate.copernicus.eu",
        "CDS_API_ROOT",
        "seasonal-postprocessed-pressure-levels",
        "seasonal-postprocessed-single-levels",
        "seasonal-monthly-pressure-levels",
        "originating_centre",
        "system",
        "data_format",
        "cfgrib",
        "z500",
        "m**2 s**-2",
        "GEOPOTENTIAL_GRAVITY = 9.80665",
        "HINDCAST_START = 1981",
        "HINDCAST_END = 2016",
        "500mb_height_anomaly",
        "2m_temperature_anomaly",
        "850mb_temperature_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "snow_depth_anomaly",
        "sst_anomaly",
        "mslp_anomaly",
        "CDS_API_KEY",
        "CDS_LICENSE_URL",
        "official_postprocessed",
        "seasonal_period_label",
        "write_manifest",
        "archive_latest_init",
        "archive_age_days",
        "SEAS5_PRECIP_ANOMALY_PALETTE",
        "COMMON_REFERENCE_YEARS",
        "load_common_reference",
        "regrid_nearest",
        "--common-reference-dir",
        "--common-reference-url",
        "common_1991_2020",
    ):
        check(term in adapter or term in workflow or term in page, f"missing SEAS5 contract term: {term}")
    for term in (
        "product:",
        "SEAS5_PRODUCT",
        "Configure Copernicus CDS API",
        "Restore SEAS5 CDS cache",
        "Restore published SEAS5 run history",
        "--previous-manifest",
        "--retain-runs 4",
        "--common-reference-dir",
        "--common-reference-url",
    ):
        check(term in workflow, f"workflow missing SEAS5 term: {term}")
    check("peaceiris/actions-gh-pages" not in workflow, "SEAS5 workflow must not publish Pages directly")
    for term in ("workflow_run:", "ECMWF SEAS5 Seasonal Graphics", "actions/download-artifact@v4", "keep_files: false"):
        check(term in pages_workflow, f"central Pages workflow missing term: {term}")
    for term in (
        "id=\"product-select\"",
        "id=\"run-select\"",
        "seas5_manifest.json",
        "timeZone:'UTC'",
        "850mb_temperature_anomaly",
        "snow_depth_anomaly",
        "Copernicus CDS",
    ):
        check(term in page, f"viewer missing SEAS5 term: {term}")
    check("WN2 / ECMWF SEAS5" not in page, "SEAS5 direct viewer should not use the umbrella dashboard branding")
    check('href="../">Seasonal dashboard</a>' in page, "SEAS5 direct viewer should link to the unified seasonal dashboard")
    check("preferredTargetIndex" in page, "SEAS5 viewer should default to the seasonal aggregate when one is present")
    module = load_adapter()
    height_spec = module.PRODUCT_SPECS[module.Z500_ANOMALY]
    check(height_spec["anomaly_palette"] == module.ANOMALY_PALETTE, "SEAS5 500-mb should use the shared 500-mb anomaly palette")
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "SEAS5 500-mb should use the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "SEAS5 500-mb should use 10-metre labelled bounds")
    for product in (module.T850_ANOMALY, module.T2M_ANOMALY):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"SEAS5 {product} should use the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"SEAS5 {product} should use 1 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"SEAS5 {product} bounds must align with colors")
    check(module.PRODUCT_SPECS[module.PRECIP_ANOMALY]["anomaly_palette"] == module.SEAS5_PRECIP_ANOMALY_PALETTE, "SEAS5 precipitation should use its darker negative palette")
    sst_spec = module.PRODUCT_SPECS[module.SST_ANOMALY]
    check(sst_spec["map_domain"] == "ocean", "SEAS5 SST must mask land")
    check(len(sst_spec["anomaly_ticks"]) == len(sst_spec["anomaly_palette"]) + 1, "SEAS5 SST bounds must align with swatches")
    mslp_spec = module.PRODUCT_SPECS[module.MSLP_ANOMALY]
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-10.0, 10.0), "SEAS5 MSLP should use ±10 hPa")
    check(len(mslp_spec["anomaly_ticks"]) == len(mslp_spec["anomaly_palette"]) + 1, "SEAS5 MSLP bounds must align with swatches")
    check(module.SEAS5_PRECIP_ANOMALY_PALETTE[7] == "#dfbd91", "SEAS5 weak negative precipitation colors should be muted")
    check(module.SEAS5_PRECIP_ANOMALY_PALETTE[8] == "#dcebd7", "SEAS5 0-to-1 precipitation anomaly should use a visible pale-sage transition")
    check(module.SEAS5_PRECIP_ANOMALY_PALETTE[9] == "#c8e4bf", "SEAS5 positive precipitation anomaly should retain a smooth first positive bin")
    check(module.SEAS5_PRECIP_ANOMALY_PALETTE[-1] == "#006d2c", "SEAS5 positive precipitation anomaly should retain a dark endpoint")
    check(module.latest_cds_init(dt.datetime(2026, 8, 6, 12, 0)) == "2026080100", "release-time init should use the current ECMWF month")
    check(module.latest_cds_init(dt.datetime(2026, 8, 6, 11, 59)) == "2026070100", "pre-release init should use the prior ECMWF month")
    check(module.target_month("2025080100", 4) == "202512", "lead-month target conversion should produce December")
    check(module.target_month("2025080100", 5) == "202601", "lead-month target conversion should cross the year boundary")
    check(module.seasonal_period_label("202512", "202602") == "DJF 2026", "DJF label should use the ending year")
    check(round(float(module.convert_values([[module.GEOPOTENTIAL_GRAVITY]], module.PRODUCT_SPECS[module.Z500_ANOMALY], "202512")[0][0]), 5) == 1.0, "z500 conversion should divide by gravity")
    check(round(float(module.convert_values([[0.001]], module.PRODUCT_SPECS[module.PRECIP_ANOMALY], "202601")[0][0]), 5) == round(0.001 * 31 * 86400 * 1000 / 25.4, 5), "precipitation conversion should use calendar-month seconds and metres-to-inches")
    check(round(float(module.convert_values([[2.5]], module.PRODUCT_SPECS[module.T850_ANOMALY], "202601")[0][0]), 5) == 2.5, "850-mb temperature anomaly should preserve Kelvin increments in Celsius")
    check(round(float(module.convert_values([[0.0254]], module.PRODUCT_SPECS[module.SNOW_DEPTH_ANOMALY], "202601")[0][0]), 5) == 1.0, "snow-depth conversion should convert metres of water equivalent to inches")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        products = (module.T850_ANOMALY, module.T2M_ANOMALY)
        previous.write_text(json.dumps({"runs": [
            {
                "id": f"{product}-old-{index}",
                "product": product,
                "init_utc": f"2025-0{index}-01T00:00:00Z",
            }
            for product in products
            for index in range(1, 5)
        ]}), encoding="utf-8")
        for product in products:
            module.write_manifest(
                output,
                ROOT,
                {
                    "id": f"{product}-current",
                    "product": product,
                    "init_utc": "2026-08-13T00:00:00Z",
                },
                previous,
                4,
            )
        retained_payload = json.loads(output.read_text(encoding="utf-8"))
        retained = retained_payload["runs"]
        for product in products:
            product_runs = [run["id"] for run in retained if run["product"] == product]
            check(product_runs == [f"{product}-current", f"{product}-old-4", f"{product}-old-3", f"{product}-old-2"], "manifest should retain current plus three prior runs for each product")
        check(retained_payload["retention"]["scope"] == "per_product", "manifest retention should identify its per-product scope")
        check(retained_payload["retention"]["max_runs_per_product"] == 4, "manifest should retain four runs for each product")
    print("SEAS5 CONTRACT OK: CDS source, GRIB access, conversions, official anomalies, viewer, workflow, retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
