#!/usr/bin/env python3
"""Static CanSIPS v3 contract checks without network or plotting libraries."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ADAPTER = ROOT / "scripts" / "cansips_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_cansips.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "cansips.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pages.yml"
DOC = ROOT / "docs" / "SEASONAL_CANSIPS.md"
PAGE = ROOT / "public" / "seasonal" / "cansips" / "index.html"
DASHBOARD = ROOT / "public" / "seasonal" / "index.html"


def load_adapter():
    spec = importlib.util.spec_from_file_location("cansips_seasonal_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load CanSIPS adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    for path, label in (
        (ADAPTER, "adapter"), (WRAPPER, "wrapper"), (WORKFLOW, "workflow"),
        (PAGES_WORKFLOW, "central Pages workflow"), (DOC, "documentation"),
        (PAGE, "viewer"), (DASHBOARD, "dashboard"),
    ):
        check(path.exists(), f"CanSIPS {label} missing")

    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages_workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    for term in (
        "CANSIPS_FORECAST_ROOT", "CANSIPS_HINDCAST_ROOT", "GeopotentialHeight",
        "ISBL-0500", "MM-ENS", "CANSIPS_ENSEMBLE_MEMBERS = 40",
        "GEM5.2-NEMO", "CanESM5", "CANSIPS_HINDCAST_START = 1991",
        "CANSIPS_HINDCAST_END = 2020", "ens_processing", "forecast mean",
        "matching-initialization-month", "500mb_height_anomaly", "850mb_temperature_anomaly",
        "2m_temperature_anomaly", "precipitation_anomaly", "mslp_anomaly", "sst_anomaly",
        "sea_surface_height_anomaly", "AirTemp", "AGL-2m", "ISBL-0850", "PrecipRate",
        "Pressure", "WaterTemp", "SeaSfcHeight-Geoid", "PRODUCT_ALL", "ANOMALY_PALETTE",
        "ANOMALY_TICKS", "seasonal_period_label", "DJF", "write_manifest",
        "--climo-start", "--climo-end", "--previous-manifest", "--retain-runs",
        "CANSIPS_DOWNLOAD_ATTEMPTS", "CANSIPS_DOWNLOAD_TIMEOUT",
        "--common-reference-dir", "common_reference", "write_grid_state", "common_1991_2020",
    ):
        check(term in adapter or term in workflow or term in documentation, f"missing CanSIPS contract term: {term}")
    for term in (
        "CanSIPS v3 Seasonal Graphics", "cansips-pages-${{ github.run_id }}", 'default: "all"',
        "Restore CanSIPS decoded-grid cache", "Restore published CanSIPS run history",
        "--climo-start", "--climo-end", "--retain-runs 4", "--common-reference-dir", "CANSIPS_WGRIB2",
    ):
        check(term in workflow, f"workflow missing CanSIPS term: {term}")
    for term in (
        "CanSIPS v3 Seasonal Graphics", "Download CanSIPS payload",
        "cansips_manifest.json", "incoming/cansips",
    ):
        check(term in pages_workflow, f"Pages workflow missing CanSIPS term: {term}")
    for term in (
        "cansips_manifest.json", "CanSIPS v3", "500mb_height_anomaly", "common_1991_2020",
        "850mb_temperature_anomaly", "sea_surface_height_anomaly",
    ):
        check(term in page or term in dashboard, f"viewer/dashboard missing CanSIPS term: {term}")
    module = load_adapter()
    check(module.parse_init("202608") == "2026080100", "YYYYMM initialization should normalize to 00Z on day 1")
    check(module.target_month("2026080100", 4) == "202612", "lead 4 from August should target December")
    check(module.target_month("2026080100", 6) == "202702", "lead 6 from August should target February")
    check(module.file_name("2026080100", 4, False).endswith("P04M.grib2"), "forecast lead filename is incorrect")
    check(module.file_name("1991080100", 4, True).startswith("199108_MSC_CanSIPS-Hindcast_"), "hindcast filename is incorrect")
    check(module.source_url("2026080100", 4, False).endswith("forecast/2026/08/202608_MSC_CanSIPS_GeopotentialHeight_ISBL-0500_LatLon1.0_P04M.grib2"), "forecast source URL is incorrect")
    check(module.file_name("2026080100", 4, False, module.PRODUCT_SPECS[module.PRODUCT_2M_TEMPERATURE_ANOMALY]).endswith("AirTemp_AGL-2m_LatLon1.0_P04M.grib2"), "2-m temperature filename is incorrect")
    check(module.file_name("2026080100", 4, False, module.PRODUCT_SPECS[module.PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY]).endswith("SeaSfcHeight-Geoid_LatLon1.0_P04M.grib2"), "sea-surface height filename is incorrect")
    check([product["name"] for product in module.selected_products(module.PRODUCT_ALL)] == list(module.PRODUCT_SPECS), "all-product selection should include every CanSIPS scalar product")
    height_spec = module.PRODUCT_SPECS[module.PRODUCT_Z500_ANOMALY]
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "CanSIPS 500-mb should use the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "CanSIPS 500-mb should use 10-metre labelled bounds")
    check(len(module.ANOMALY_PALETTE) == len(module.ANOMALY_TICKS) - 1, "CanSIPS height anomaly colors must align with labelled bounds")
    check(len(module.SSH_ANOMALY_PALETTE) == len(module.SSH_ANOMALY_TICKS) - 1, "CanSIPS sea-surface height colors must align with labelled bounds")
    for product in (module.PRODUCT_850MB_TEMPERATURE_ANOMALY, module.PRODUCT_2M_TEMPERATURE_ANOMALY):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"CanSIPS {product} should use the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"CanSIPS {product} should use 1 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"CanSIPS {product} bounds must align with colors")
    check((module.PRODUCT_SPECS[module.PRODUCT_MSLP_ANOMALY]["anomaly_min"], module.PRODUCT_SPECS[module.PRODUCT_MSLP_ANOMALY]["anomaly_max"]) == (-10.0, 10.0), "CanSIPS MSLP should use the readable shared ±10 hPa range")
    check((module.PRODUCT_SPECS[module.PRODUCT_SST_ANOMALY]["anomaly_min"], module.PRODUCT_SPECS[module.PRODUCT_SST_ANOMALY]["anomaly_max"]) == (-3.0, 3.0), "CanSIPS SST should use a seasonal-scale ±3°C range")
    for ocean_product in (module.PRODUCT_SST_ANOMALY, module.PRODUCT_SEA_SURFACE_HEIGHT_ANOMALY):
        check(module.PRODUCT_SPECS[ocean_product]["map_domain"] == "ocean", f"CanSIPS {ocean_product} must mask land")
        check(len(module.PRODUCT_SPECS[ocean_product]["anomaly_ticks"]) == len(module.PRODUCT_SPECS[ocean_product]["anomaly_palette"]) + 1, f"CanSIPS {ocean_product} bounds must align with colors")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [{"id": f"old-{index}", "init_utc": f"2025-0{index}-01T00:00:00Z"} for index in range(1, 5)]}), encoding="utf-8")
        module.write_manifest(output, ROOT, {"id": "current", "init_utc": "2026-08-01T00:00:00Z"}, previous, 4)
        retained = json.loads(output.read_text(encoding="utf-8"))["runs"]
        check([run["id"] for run in retained] == ["current", "old-4", "old-3", "old-2"], "manifest should retain current plus three prior runs")
        legacy = Path(temporary) / "legacy.json"
        legacy.write_text(json.dumps({"runs": [{"id": "cansips-2026080100", "init_utc": "2026-08-01T00:00:00Z"}]}), encoding="utf-8")
        replacement = Path(temporary) / "replacement.json"
        module.write_manifest(
            replacement,
            ROOT,
            {"id": "cansips-2026080100-500mb_height_anomaly", "product": module.PRODUCT_Z500_ANOMALY, "init_utc": "2026-08-01T00:00:00Z"},
            legacy,
            4,
        )
        migrated = json.loads(replacement.read_text(encoding="utf-8"))["runs"]
        check([run["id"] for run in migrated] == ["cansips-2026080100-500mb_height_anomaly"], "legacy z500 run should be replaced by the product-aware entry")
    print("CANSIPS CONTRACT OK: ECCC Datamart, 40-member means, hindcast anomalies, workflow, viewer, retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
