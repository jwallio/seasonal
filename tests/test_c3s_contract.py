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
CDS_CLIENT = ROOT / "scripts" / "cds_client.py"
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
    for path in (ADAPTER, GRIB_ADAPTER, CDS_CLIENT, WORKFLOW, PAGES, PAGE):
        check(path.exists(), f"missing C3S contract file: {path.name}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    grib_adapter = GRIB_ADAPTER.read_text(encoding="utf-8")
    cds_client = CDS_CLIENT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    for term in (
        "max-parallel: 4",
        "matrix:",
        "c3s-product-",
        "actions/download-artifact@v4",
        "merge_seasonal_payloads.py",
        "write_seasonal_fragment.py",
        "cds-v2",
    ):
        check(term in workflow, f"C3S workflow missing speed-up term: {term}")
    for term in (
        "seasonal-postprocessed-pressure-levels", "seasonal-postprocessed-single-levels",
        "seasonal-monthly-pressure-levels", "originating_centre", "product_type",
        "ensemble_mean", "multi-system", "component", "CDS_API_KEY",
        "snowfall_anomalous_rate_of_accumulation", "snowfall_anomaly",
        "retain-cycles", "systems", "system", "cds_client", "retry_max", "sleep_max",
    ):
        check(term in adapter or term in workflow or term in pages or term in cds_client, f"missing C3S term: {term}")
    check("cfgrib.open_datasets" in grib_adapter, "C3S/JMA GRIB decoder should discover heterogeneous raw pressure-level groups")
    check('"seasonal-monthly-pressure-levels"' in adapter, "C3S 500-mb product should retain the raw geopotential source")
    check("height_grid=height" in adapter, "C3S renderer should pass decoded absolute heights to the map renderer")
    check("component_names_by_lead" in adapter and "component_count_by_lead" in adapter, "C3S blends must record the actual contributing systems by lead")
    check("seasonal_component_names" in adapter and "seasonal_grid_override" in adapter, "C3S seasonal blends must use only systems complete across the full window")
    check('f"{len(available_components)}-system mean"' in adapter, "C3S monthly image labels must use the actual contributor count")
    check('f"{len(complete_components)}-system mean"' in adapter, "C3S seasonal image labels must use the actual contributor count")
    module = load_adapter()
    check(set(module.CENTRES) == {"ecmwf", "ukmo", "meteo_france", "dwd", "cmcc", "ncep", "jma", "eccc", "bom"}, "C3S centre catalog is incomplete")
    check(module.CENTRES["ukmo"]["system"] == "610", "C3S UKMO must use operational GloSea6-GC5.1 system 610")
    check(module.CENTRES["ukmo"]["model_version"] == "GloSea6-GC5.1", "C3S UKMO model version metadata is stale")
    check(module.target_month("2026080100", 4) == "202612", "C3S lead conversion should produce December")
    check(module.period_label("202612", "202702") == "DJF 2026–27", "C3S DJF period label should identify both winter years")
    height_spec = module.PRODUCT_SPECS["500mb_height_anomaly"]
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "C3S and JMA 500-mb maps should use the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "C3S and JMA 500-mb maps should use 10-metre labelled bounds")
    check(len(height_spec["anomaly_ticks"]) == len(height_spec["anomaly_palette"]) + 1, "C3S 500-mb color bounds must align with swatches")
    northern_height = module.PRODUCT_SPECS["500mb_height_anomaly_nh"]
    check(northern_height["region"] == module.NORTHERN_HEMISPHERE_REGION, "C3S Northern Hemisphere 500-mb view must use the polar region")
    check(northern_height["projection"] == "north_polar_stereographic", "C3S Northern Hemisphere 500-mb view must use the polar projection")
    for product in ("850mb_temperature_anomaly", "2m_temperature_anomaly"):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"C3S {product} should use the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"C3S {product} should use 1 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"C3S {product} bounds must align with colors")
    mslp_spec = module.PRODUCT_SPECS["mslp_anomaly"]
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-10.0, 10.0), "C3S and super-ensemble MSLP should use ±10 hPa")
    check(len(mslp_spec["anomaly_ticks"]) == len(mslp_spec["anomaly_palette"]) + 1, "C3S MSLP bounds must align with swatches")
    snowfall_spec = module.PRODUCT_SPECS["snowfall_anomaly"]
    expected_snowfall_ticks = [-4.0, -3.5, -3.0, -2.5, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, 0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0]
    check((snowfall_spec["anomaly_min"], snowfall_spec["anomaly_max"]) == (-4.0, 4.0), "C3S snowfall should use a nonlinear ±4.0 inch water-equivalent range")
    check(snowfall_spec["anomaly_ticks"] == expected_snowfall_ticks, "C3S snowfall should use the approved nonlinear labelled breakpoints")
    check(snowfall_spec["anomaly_tick_format"] == "signed_trimmed" and snowfall_spec["anomaly_tick_decimals"] == 2, "C3S snowfall labels should preserve quarter-inch breakpoints")
    check((snowfall_spec["monthly_anomaly_min"], snowfall_spec["monthly_anomaly_max"]) == (-2.0, 2.0), "C3S monthly snowfall should use the tighter ±2.0 inch range")
    check(len(snowfall_spec["monthly_anomaly_ticks"]) == len(snowfall_spec["monthly_anomaly_palette"]) + 1, "C3S monthly snowfall bounds must align with swatches")
    check(snowfall_spec["monthly_anomaly_endpoint_labels"] == {"minimum": "≤−2.0", "maximum": "≥+2.0"}, "C3S monthly snowfall legend should mark clipped endpoints")
    check(snowfall_spec["region"] == module.CONUS_PRECIP_REGION, "C3S snowfall must use the CONUS crop")
    check(len(snowfall_spec["anomaly_ticks"]) == len(snowfall_spec["anomaly_palette"]) + 1, "C3S snowfall bounds must align with swatches")
    ukmo_snowfall = module.product_spec("snowfall_anomaly", "UK Met Office")
    check(ukmo_snowfall["title"] == "C3S UKMO Snowfall Departure", "C3S snowfall image title should omit the parenthetical LWE unit")
    check("(in LWE)" not in ukmo_snowfall["absolute_title"], "C3S snowfall absolute image title should omit the parenthetical LWE unit")
    check(ukmo_snowfall["map_domain"] == "land" and ukmo_snowfall["fit_frame_to_domain"], "C3S snowfall should use a fitted lower-48 land frame")
    check(len(ukmo_snowfall["mask_states"]) == 48, "C3S snowfall lower-48 mask should include all 48 states")
    check(ukmo_snowfall["anomaly_endpoint_labels"] == {"minimum": "≤−4.0", "maximum": "≥+4.0"}, "C3S snowfall seasonal legend should mark clipped endpoints")
    converted = module.convert_product_grid(
        module.Grid([0.0], [0.0], [[0.001]]), snowfall_spec, "202601"
    )
    check(round(converted.values[0][0], 5) == round(0.001 * 31 * 86400 * module.M_TO_INCH, 5), "C3S snowfall conversion should use calendar-month seconds and metres-to-inches")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [{"id": f"old-{year}", "init_utc": f"2025-{year:02d}-01T00:00:00Z"} for year in range(1, 5)]}), encoding="utf-8")
        module.write_manifest(output, [{"id": "current-z500", "init_utc": "2026-08-01T00:00:00Z"}], previous, 4)
        module.write_manifest(output, [{"id": "current-t2m", "init_utc": "2026-08-01T00:00:00Z"}], previous, 4)
        payload = json.loads(output.read_text(encoding="utf-8"))
        run_ids = {run["id"] for run in payload["runs"]}
        check({"current-z500", "current-t2m"}.issubset(run_ids), "C3S repeated product renders must accumulate in the current manifest")
        check(len({run["init_utc"] for run in payload["runs"]}) == 4, "C3S retention should keep the current cycle plus three prior cycles")
    print("C3S CONTRACT OK: official centres/systems, native anomalies, blend metadata, multi-product accumulation, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
