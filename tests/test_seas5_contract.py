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
        "mslp_anomaly",
        "CDS_API_KEY",
        "CDS_LICENSE_URL",
        "official_postprocessed",
        "seasonal_period_label",
        "write_manifest",
        "archive_latest_init",
        "archive_age_days",
        "canonicalize_product_spec",
        "COMMON_REFERENCE_YEARS",
        "load_common_reference",
        "regrid_nearest",
        "--common-reference-dir",
        "--common-reference-url",
        "common_1991_2020",
        "grid_quality_control",
        "require_quality_control",
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
    check(adapter.count('target_entry["quality_control"] = grid_quality_control') == 1, "SEAS5 monthly anomalies must publish numeric QC")
    check(adapter.count('seasonal_entry["quality_control"] = grid_quality_control') == 1, "SEAS5 seasonal anomalies must publish numeric QC")
    check(adapter.count("require_quality_control(") >= 2, "SEAS5 must fail closed when numeric QC fails")
    check('href="../">Seasonal dashboard</a>' in page, "SEAS5 direct viewer should link to the unified seasonal dashboard")
    check("preferredTargetIndex" in page, "SEAS5 viewer should default to the seasonal aggregate when one is present")
    module = load_adapter()
    height_spec = module.PRODUCT_SPECS[module.Z500_ANOMALY]
    check(height_spec["anomaly_palette"] == module.HEIGHT_ANOMALY_STYLE["anomaly_palette"], "SEAS5 500-mb should use the shared 500-mb anomaly palette")
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-120.0, 120.0), "SEAS5 500-mb should use the shared ±120 m range")
    check(height_spec["anomaly_ticks"] == list(range(-120, 121, 10)), "SEAS5 500-mb should use the shared 10-m labelled bounds")
    northern_height = module.PRODUCT_SPECS["500mb_height_anomaly_nh"]
    check(northern_height["region"] == module.NORTHERN_HEMISPHERE_REGION, "SEAS5 Northern Hemisphere 500-mb view must use the polar region")
    check(northern_height["projection"] == "north_polar_stereographic", "SEAS5 Northern Hemisphere 500-mb view must use the polar projection")
    check(northern_height["projection_central_longitude"] == -100., "North America must face the bottom")
    check(northern_height["polar_frame_latitude"] == 24., "NH frame must include southern Texas and Florida")
    check(len(northern_height["anomaly_palette"])+1 == len(northern_height["anomaly_ticks"]), "NH colors must align with bounds")
    check(height_spec["artifact_token"] != northern_height["artifact_token"] and adapter.count("product['artifact_token']") >= 4,
          "SEAS5 must publish the two height domains to distinct image paths")
    for product in (module.T850_ANOMALY, module.T2M_ANOMALY):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-6.0, 6.0), f"SEAS5 {product} should use the shared ±6 °C range")
        check(temperature_spec["anomaly_ticks"] == [value / 2.0 for value in range(-12, 13)], f"SEAS5 {product} should use 0.5 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"SEAS5 {product} bounds must align with colors")
    check(module.PRODUCT_SPECS[module.PRECIP_ANOMALY]["canonical_style_key"] == "precipitation_anomaly|monthly|conus", "SEAS5 precipitation should use the provider-independent canonical style")
    snowfall_spec = module.PRODUCT_SPECS[module.SNOWFALL_ANOMALY]
    from snowfall_display import MONTHLY_BOUNDS
    check("snowfall_display_profile" not in snowfall_spec, "SEAS5 snowfall should not retain a provider display profile")
    check((snowfall_spec["anomaly_min"], snowfall_spec["anomaly_max"]) == (-14.0, 14.0), "SEAS5 monthly snowfall should use the canonical snow-depth range")
    check(snowfall_spec["anomaly_ticks"] == MONTHLY_BOUNDS, "SEAS5 snowfall should use canonical monthly breakpoints")
    check(not any(key.startswith("monthly_anomaly_") for key in snowfall_spec), "SEAS5 must not retain a provider-local monthly style branch")
    check(len(snowfall_spec["anomaly_ticks"]) == len(snowfall_spec["anomaly_palette"]) + 1, "SEAS5 snowfall bounds must align with swatches")
    mslp_spec = module.PRODUCT_SPECS[module.MSLP_ANOMALY]
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-5.0, 5.0), "SEAS5 MSLP should use ±5 hPa")
    check(len(mslp_spec["anomaly_bounds"]) == len(mslp_spec["anomaly_palette"]) + 1, "SEAS5 MSLP bounds must align with swatches")
    check(mslp_spec["anomaly_under_color"] != mslp_spec["anomaly_palette"][0], "MSLP underflow color should be distinct from its endpoint bin")
    check(mslp_spec["anomaly_over_color"] != mslp_spec["anomaly_palette"][-1], "MSLP overflow color should be distinct from its endpoint bin")
    check(module.latest_cds_init(dt.datetime(2026, 8, 6, 12, 0)) == "2026080100", "release-time init should use the current ECMWF month")
    check(module.latest_cds_init(dt.datetime(2026, 8, 6, 11, 59)) == "2026070100", "pre-release init should use the prior ECMWF month")
    check(module.target_month("2025080100", 4) == "202511", "CDS month 4 from August should produce November")
    check(module.target_month("2025080100", 6) == "202601", "lead-month target conversion should cross the year boundary")
    check(module.seasonal_period_label("202512", "202602") == "DJF 2025–26", "DJF label should identify both winter years")
    # Independent calendar and signed-conversion regressions.
    check([module.target_month("2026090100", k) for k in (4,5,6)] ==
          ["202612","202701","202702"], "September CDS months 4-6 must be DJF")
    check(module.target_month("2026090100",1) == "202609", "CDS month 1 includes initialization")
    check(module.month_seconds("202802") == 29*86400, "leap February must use 29 days")
    try:
        module.parse_cds_leads("4,5,6,7","lead months","2026090100")
        raise AssertionError("unsupported March must not be silently requested or skipped")
    except module.SEAS5Error as exc:
        check("Feb 2027" in str(exc), "unsupported lead error must explain the actual endpoint")
    check(module.parse_cds_leads("4,5,6","lead months","2026090100") == [4,5,6], "DJF must remain valid")
    original = module.Grid([0.,1.,2.],[0.],[[-0.4,0.,0.4]])
    from snowfall_display import COMPACT_TICKS, SEASONAL_TICKS
    for seasonal in (False,True):
        display,spec = module.snowfall_display(original,snowfall_spec,seasonal)
        expected_display_ticks = list(SEASONAL_TICKS if seasonal else COMPACT_TICKS)
        check(spec["anomaly_ticks"] == expected_display_ticks, "snow-depth display must use the shared monthly or seasonal labelled bands")
        check((spec["anomaly_min"], spec["anomaly_max"]) == ((-20,20) if seasonal else (-14,14)), "snow-depth display must use the correct seasonal or monthly endpoints")
        check(len(spec["anomaly_bounds"]) == len(spec["anomaly_palette"]) + 1, "snow-depth boundaries must align with palette")
        check(display.values == [[-4.,0.,4.]], "signed LWE departures must convert exactly once")
        check(original.values == [[-0.4,0.,0.4]], "conversion must not mutate canonical LWE")
        from matplotlib.colors import BoundaryNorm,ListedColormap
        cmap=ListedColormap(spec["anomaly_palette"])
        norm=BoundaryNorm(spec["anomaly_bounds"],cmap.N)
        check(cmap(norm(0.09)) == (1.,1.,1.,1.), "near-zero snow departure must be white")
        check(cmap(norm(0.6)) == (1.,1.,1.,1.), "positive departures under one inch must be white")
        check(cmap(norm(-0.6)) == (1.,1.,1.,1.), "negative departures under one inch must be white")
    rain = module.PRODUCT_SPECS["precipitation_anomaly"]
    check(module.snowfall_display(original,rain)[0] is original, "other fields must not be converted")
    snowfall_title = module.PRODUCT_SPECS[module.SNOWFALL_ANOMALY]
    check(snowfall_title["title"] == "SEAS5 CONUS Snowfall Departure", "SEAS5 snowfall image title should omit the parenthetical LWE unit")
    check("(in LWE)" not in snowfall_title["absolute_title"], "SEAS5 snowfall absolute image title should omit the parenthetical LWE unit")
    check(snowfall_title["map_domain"] == "land" and snowfall_title["fit_frame_to_domain"], "SEAS5 snowfall should use a fitted lower-48 land frame")
    check(len(snowfall_title["mask_states"]) == 48, "SEAS5 snowfall lower-48 mask should include all 48 states")
    check(snowfall_title["anomaly_endpoint_labels"] == {"minimum": "≤−14", "maximum": "14+"}, "SEAS5 snowfall legend should mark canonical overflow endpoints")
    check(round(float(module.convert_values([[module.GEOPOTENTIAL_GRAVITY]], module.PRODUCT_SPECS[module.Z500_ANOMALY], "202512")[0][0]), 5) == 1.0, "z500 conversion should divide by gravity")
    check(round(float(module.convert_values([[0.001]], module.PRODUCT_SPECS[module.PRECIP_ANOMALY], "202601")[0][0]), 5) == round(0.001 * 31 * 86400 * 1000 / 25.4, 5), "precipitation conversion should use calendar-month seconds and metres-to-inches")
    check(round(float(module.convert_values([[2.5]], module.PRODUCT_SPECS[module.T850_ANOMALY], "202601")[0][0]), 5) == 2.5, "850-mb temperature anomaly should preserve Kelvin increments in Celsius")
    check(round(float(module.convert_values([[0.0254]], module.PRODUCT_SPECS[module.SNOW_DEPTH_ANOMALY], "202601")[0][0]), 5) == 1.0, "snow-depth conversion should convert metres of water equivalent to inches")
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        products = (module.T850_ANOMALY, module.T2M_ANOMALY)
        previous.write_text(json.dumps({"runs": [
            *[
                {
                    "id": f"{product}-current",
                    "product": product,
                    "init_utc": "2026-08-13T00:00:00Z",
                    "marker": "stale-published-copy",
                }
                for product in products
            ],
            *[
                {
                    "id": f"{product}-old-{index}",
                    "product": product,
                    "init_utc": f"2025-0{index}-01T00:00:00Z",
                }
                for product in products
                for index in range(1, 5)
            ],
        ]}), encoding="utf-8")
        for product in products:
            module.write_manifest(
                output,
                ROOT,
                {
                    "id": f"{product}-current",
                    "product": product,
                    "init_utc": "2026-08-13T00:00:00Z",
                    "marker": "fresh-workflow-copy",
                },
                previous,
                4,
            )
        retained_payload = json.loads(output.read_text(encoding="utf-8"))
        retained = retained_payload["runs"]
        for product in products:
            product_runs = [run["id"] for run in retained if run["product"] == product]
            check(product_runs == [f"{product}-current", f"{product}-old-4", f"{product}-old-3", f"{product}-old-2"], "manifest should retain current plus three prior runs for each product")
            current = next(run for run in retained if run["id"] == f"{product}-current")
            check(current["marker"] == "fresh-workflow-copy", "later product writes must not restore a stale published copy over earlier staged work")
        check(retained_payload["retention"]["scope"] == "per_product", "manifest retention should identify its per-product scope")
        check(retained_payload["retention"]["max_runs_per_product"] == 4, "manifest should retain four runs for each product")
    print("SEAS5 CONTRACT OK: CDS source, GRIB access, conversions, official anomalies, viewer, workflow, retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
