#!/usr/bin/env python3
"""Static CFSv2 contract checks that do not require network or plotting libraries."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "cfsv2_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_cfsv2.ps1"
DOC = ROOT / "docs" / "SEASONAL_CFSV2.md"
WORKFLOW = ROOT / ".github" / "workflows" / "cfsv2.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pages.yml"
UPDATE_WORKFLOW = ROOT / ".github" / "workflows" / "update.yml"
PAGE = ROOT / "public" / "seasonal" / "cfsv2" / "index.html"


def load_adapter():
    spec = importlib.util.spec_from_file_location("cfsv2_seasonal_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load CFSv2 adapter for conversion check")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(ADAPTER.exists(), "CFSv2 adapter missing")
    check(WRAPPER.exists(), "CFSv2 PowerShell wrapper missing")
    check(DOC.exists(), "CFSv2 documentation missing")
    check(WORKFLOW.exists(), "CFSv2 workflow missing")
    check(PAGES_WORKFLOW.exists(), "central Pages workflow missing")
    check(UPDATE_WORKFLOW.exists(), "WeatherNext workflow missing")
    check(PAGE.exists(), "CFSv2 Pages index missing")

    adapter = ADAPTER.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    for term in (
        "NOMADS_ROOT",
        "pgbf.",
        "HGT:500 mb",
        "wgrib2",
        "ensemble_mean",
        "single_initial_condition_cycle",
        "rolling_initial_conditions",
        "rolling_cycle_inits",
        "--rolling-days",
        "--rolling-state-dir",
        "allow_partial_rolling",
        "NCEI_CALIBRATION_ROOT",
        "NCEI_FLUX_CALIBRATION_ROOT",
        "flux-1982-2010",
        "z500_anomaly",
        "PRODUCT_2M_TEMPERATURE_ANOMALY",
        "2m_temperature_anomaly",
        "TMP:2 m above ground",
        "tmp2m",
        "TEMPERATURE_ANOMALY_PALETTE",
        "Kelvin offset cancels in forecast-minus-calibration anomalies",
        "PRODUCT_MSLP_ANOMALY",
        "mslp_anomaly",
        "PRES:mean sea level",
        "mslp",
        "pascals_to_hectopascals",
        "MSLP_ANOMALY_PALETTE",
        "Mean sea-level pressure anomaly (hPa)",
        "PRODUCT_PRECIPITATION_ANOMALY",
        "precipitation_anomaly",
        "FLXF",
        "PRATE:surface",
        "kg m-2 s-1",
        "monthly_precipitation_total_inches",
        "FLUX_GRID_LON_COUNT = 384",
        "FLUX_GRID_LAT_COUNT = 190",
        "monthly total precipitation",
        "PRECIP_ANOMALY_PALETTE",
        "PRECIP_MONTHLY_ANOMALY_MIN_IN = -4.0",
        "PRECIP_MONTHLY_ANOMALY_MAX_IN = 4.0",
        "PRECIP_SEASONAL_ANOMALY_MIN_IN = -8.0",
        "PRECIP_SEASONAL_ANOMALY_MAX_IN = 8.0",
        "CONUS_PRECIP_REGION = (-128.0, -65.0, 22.0, 52.0)",
        "CFSv2 CONUS Precipitation Anomaly (in)",
        "CONUS domain",
        "PRODUCT_SWE_ANOMALY",
        "snow_water_equivalent_anomaly",
        "WEASD:surface",
        "snow_water_equivalent_inches",
        "SWE_ANOMALY_PALETTE",
        "SWE_ANOMALY_MIN_IN = -8.0",
        "SWE_ANOMALY_MAX_IN = 8.0",
        "monthly snow-water-equivalent average",
        "Snow-water equivalent (in)",
        "drawedges=product_spec[\"name\"] in {PRODUCT_PRECIPITATION_ANOMALY, PRODUCT_SWE_ANOMALY}",
        "sum_grids",
        "--product",
        "--baseline-file",
        "--baseline-dir",
        "--ncei-calibration",
        "COMMON_REFERENCE_YEARS = \"1991-2020\"",
        "COMMON_REFERENCE_LABEL",
        "load_common_reference",
        "regrid_nearest",
        "--common-reference-dir",
        "--common-reference-url",
        "common_1991_2020",
        "--previous-manifest",
        "--retain-runs",
        "--seasonal-window",
        "--absolute",
        "cfsv2_manifest",
        "seasonal mean",
        "contourf",
        "height_grid",
        "500-mb Geopotential Height & Anomaly",
        "clabel",
        "cycle rolling mean",
        "Lambert Conformal Conic",
        "standard_parallel_1",
        "graticules",
        "lcc_inverse",
        "sample_source",
        "full global field",
        "header_detail",
        "Init {init_date:%d %b %Y %HZ}",
        "Height contours in dam",
        "seasonal_period_label",
        "DJF {end.year}",
        '"status"',
    ):
        check(term in adapter, f"adapter missing contract term: {term}")
    check("colorbar.set_label" not in adapter, "footer colorbar description should be absent")
    check("figure.text(0.035, 0.045" not in adapter, "footer text position should be absent")
    check("ANOMALY_MIN_M = -100.0" in adapter, "shared seasonal height renderer should use a -100 m lower bound")
    check("ANOMALY_MAX_M = 100.0" in adapter, "shared seasonal height renderer should use a +100 m upper bound")
    check("ANOMALY_TICKS = list(range(-100, 101, 10))" in adapter, "shared seasonal height renderer should label every 10 m")
    check("PRECIP_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "shared precipitation scale should remain available to dependent adapters")
    check("TEMPERATURE_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "shared temperature scale should remain available to dependent adapters")
    check("MSLP_ANOMALY_TICKS = list(range(-20, 21, 2))" in adapter, "shared MSLP scale should remain available to dependent adapters")
    check("CFSV2_HEIGHT_ANOMALY_MIN_M = -100.0" in adapter, "CFSv2 height anomaly lower scale bound should be -100 m")
    check("CFSV2_HEIGHT_ANOMALY_MAX_M = 100.0" in adapter, "CFSv2 height anomaly upper scale bound should be +100 m")
    check("CFSV2_HEIGHT_ANOMALY_TICKS = list(range(-100, 101, 10))" in adapter, "CFSv2 height anomaly scale should label every 10 m")
    check("PRECIP_MONTHLY_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]" in adapter, "monthly precipitation should use 0.5-inch intervals from -4 to +4")
    check("PRECIP_SEASONAL_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "seasonal precipitation should retain 1-inch intervals from -8 to +8")
    check("CFSV2_TEMPERATURE_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]" in adapter, "CFSv2 2-m temperature should use 0.5-degree intervals from -4 to +4")
    check("CFSV2_MSLP_ANOMALY_TICKS = list(range(-10, 11))" in adapter, "CFSv2 MSLP should use 1 hPa intervals from -10 to +10")
    check("bounds = np.asarray(colorbar_ticks, dtype=float)" in adapter, "anomaly bounds should be anchored to labelled ticks")
    check('colorbar_options["boundaries"] = bounds' in adapter, "colorbar should use the labelled anomaly bounds")
    check("title_box = title_text.get_window_extent" in adapter, "header should prevent title/valid overlap")
    check("available_title_width" in adapter, "header should fit long valid-period labels")
    check('pad=1.8' in adapter, "colorbar labels should sit close to the scale")
    check('width=0.85' in adapter, "colorbar tick marks should remain legible")
    check("DEFAULT_REGION = (-160.0, -10.0, 22.0, 85.0)" in adapter, "seasonal graphic should use the centered North America and Greenland region")
    check("PROJECTED_X_SHIFT_FRACTION = 0.035" in adapter, "seasonal graphic should shift the projected window to center the CONUS")
    swe_block = adapter.split("PRODUCT_SWE_ANOMALY:", 1)[1].split("    },", 1)[0]
    check('"region": CONUS_PRECIP_REGION' in swe_block, "SWE map should use the same CONUS crop as precipitation")
    check("Snow-water equivalent (in)  •  CONUS domain" in adapter, "SWE header should identify the CONUS crop")
    swe_palette = adapter.split("SWE_ANOMALY_PALETTE =", 1)[1].split("]", 1)[0]
    check('"#ffffff"' in swe_palette, "SWE zero anomaly color should be white")
    check(swe_palette.count('"#ffffff"') >= 2, "SWE -1 to 0 inch interval should also be white")
    check("land_mask_from_borders" in adapter, "SWE renderer should mask ocean cells using the land geometry")
    check('map_domain not in {"land", "ocean"}' in adapter, "shared renderer should validate domain-specific products")
    check("requires the countries land mask" in adapter, "domain-specific products should fail closed without a land mask")
    check('axes.set_facecolor("#ffffff" if product_spec["name"] == PRODUCT_SWE_ANOMALY' in adapter, "SWE ocean background should be white")
    check("x_min -= projected_x_shift" in adapter and "x_max -= projected_x_shift" in adapter, "projected map window should shift west to move the CONUS right")
    check("figsize=(9.0, 9.0)" in adapter, "seasonal graphic should use the 1080x1080 social canvas")
    check("map_height = map_width * (y_max - y_min) / (x_max - x_min)" in adapter, "seasonal map box should preserve projection aspect")
    check("horizontal_x, _ = lcc_project" in adapter, "projected map window should be centered from projection coordinates")
    check("top_edge_lons = np.linspace(lon_min, lon_max, 240)" in adapter, "projected map window should retain the full Greenland edge")
    check("border_lat_min = 14.0" in adapter, "border rendering should exclude South America")
    check("if not (lon_min <= longitude <= lon_max) or latitude < border_lat_min:" in adapter, "border rendering should stay inside the North America/Greenland window")
    check("timeZone:'UTC'" in page, "Pages month labels should remain aligned with UTC map validity dates")
    check('<title>CFSv2 Model Viewer</title>' in page, "Pages title should use generic CFSv2 model-viewer branding")
    check('WN2 / CFSv2' not in page, "CFSv2 direct viewer should not use the umbrella dashboard branding")
    check('href="../">Seasonal dashboard</a>' in page, "CFSv2 direct viewer should link to the unified seasonal dashboard")
    check("preferredTargetIndex" in page, "CFSv2 viewer should default to the seasonal aggregate when one is present")
    check('id="product-select"' in page, "Pages viewer missing parameter selector")
    check('id="run-select"' in page, "Pages viewer missing run-history selector")
    check("'2m_temperature_anomaly': '2-m Temperature Anomaly'" in page, "Pages viewer missing 2-m temperature label")
    check("'mslp_anomaly': 'Mean Sea-Level Pressure Anomaly'" in page, "Pages viewer missing MSLP label")
    check("Retaining ${history} prior run${history === 1 ? '' : 's'} per parameter" in page, "Pages viewer should report per-parameter run history")
    check("available.find(run => !isFailedRun(run))" in page, "Pages viewer should default to the latest non-failed run")
    adapter_module = load_adapter()
    check(len(adapter_module.ANOMALY_PALETTE) == len(adapter_module.ANOMALY_TICKS) - 1, "height anomaly colors should align with labelled transitions")
    check(len(adapter_module.ANOMALY_PALETTE) == len(adapter_module.CFSV2_HEIGHT_ANOMALY_TICKS) - 1, "CFSv2 height anomaly colors should align with labelled transitions")
    check(len(adapter_module.TEMPERATURE_ANOMALY_PALETTE) == len(adapter_module.CFSV2_TEMPERATURE_ANOMALY_TICKS) - 1, "CFSv2 2-m temperature colors should align with labelled transitions")
    check(len(adapter_module.MSLP_ANOMALY_PALETTE) == len(adapter_module.CFSV2_MSLP_ANOMALY_TICKS) - 1, "CFSv2 MSLP colors should align with labelled transitions")
    check(len(adapter_module.PRECIP_ANOMALY_PALETTE) == len(adapter_module.PRECIP_MONTHLY_ANOMALY_TICKS) - 1, "monthly precipitation colors should align with labelled transitions")
    check(len(adapter_module.PRECIP_ANOMALY_PALETTE) == len(adapter_module.PRECIP_SEASONAL_ANOMALY_TICKS) - 1, "seasonal precipitation colors should align with labelled transitions")
    check(len(adapter_module.SWE_ANOMALY_PALETTE) == len(adapter_module.SWE_ANOMALY_TICKS) - 1, "SWE colors should align with labelled transitions")
    t2m_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY]
    mslp_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_MSLP_ANOMALY]
    precip_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_PRECIPITATION_ANOMALY]
    height_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_HEIGHT_ANOMALY]
    swe_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_SWE_ANOMALY]
    check(adapter_module.anomaly_style(height_spec)[:2] == (-100.0, 100.0), "height anomaly style should use the tighter ±100 m range")
    check(adapter_module.anomaly_style(t2m_spec)[:2] == (-4.0, 4.0), "temperature anomaly style should use the tighter ±4 °C range")
    check(adapter_module.anomaly_style(mslp_spec)[:2] == (-10.0, 10.0), "MSLP anomaly style should use the tighter ±10 hPa range")
    check(adapter_module.anomaly_style(precip_spec, seasonal=False)[:2] == (-4.0, 4.0), "monthly precipitation should use the tighter ±4-inch range")
    check(adapter_module.anomaly_style(precip_spec, seasonal=True)[:2] == (-8.0, 8.0), "seasonal precipitation should retain the ±8-inch total range")
    check(swe_spec["map_domain"] == "land", "SWE must retain a land-only render domain")
    check(adapter_module.manifest_product_key({"field": "z500_anomaly"}) == adapter_module.PRODUCT_HEIGHT_ANOMALY, "legacy manifests should map height fields to the current product key")
    check(t2m_spec["source_kind"] == "flxf" and t2m_spec["grid_shape"] == (384, 190), "2-m temperature should use the FLXF flux grid")
    check(mslp_spec["source_kind"] == "pgbf" and mslp_spec["grid_shape"] == (360, 181), "MSLP should use the PGBF pressure grid")
    converted_mslp = adapter_module.prepare_product_grid(
        adapter_module.Grid([0.0], [0.0], [[101325.0]]), mslp_spec, "202608"
    )
    check(converted_mslp.values == [[1013.25]], "PRES should convert from Pa to hPa")
    converted = adapter_module.snow_water_equivalent_inches(
        adapter_module.Grid([0.0], [0.0], [[25.4]])
    )
    check(converted.values == [[1.0]], "WEASD should convert from kg m-2 to inches")
    reference = adapter_module.Grid([0.0, 1.0], [0.0, 1.0], [[500.0, 501.0], [502.0, 503.0]])
    regridded = adapter_module.regrid_nearest(reference, [0.1, 0.9], [0.1, 0.9], "test")
    check(regridded.values == [[500.0, 501.0], [502.0, 503.0]], "common reference regrid should preserve nearest source values")
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        common_dir = temporary_root / "common"
        common_dir.mkdir()
        adapter_module.write_grid_state(reference, common_dir / "z500_202612.csv.gz")
        loaded, loaded_path, loaded_url, downloaded, _ = adapter_module.load_common_reference(
            "202612", common_dir, "", 0.0, 0.0
        )
        check(loaded.values == reference.values, "common reference loader should read compressed grid state")
        check(loaded_path.name == "z500_202612.csv.gz" and not loaded_url and not downloaded, "common reference metadata should identify local state")
        previous = temporary_root / "previous.json"
        output = temporary_root / "manifest.json"
        prior_runs = []
        for product, field in (
            (adapter_module.PRODUCT_HEIGHT_ANOMALY, "z500_anomaly"),
            (adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY, "t2m_anomaly"),
        ):
            prior_runs.extend(
                {
                    "id": f"{product}-old-{index}",
                    "product": product,
                    "field": field,
                    "init_utc": f"2026-08-{index:02d}T00:00:00Z",
                }
                for index in range(1, 5)
            )
        prior_runs.append(
            {
                "id": "current",
                "product": adapter_module.PRODUCT_HEIGHT_ANOMALY,
                "field": "z500_anomaly",
                "init_utc": "2026-08-13T00:00:00Z",
                "marker": "stale-published-copy",
            }
        )
        previous.write_text(
            json.dumps({"runs": prior_runs}),
            encoding="utf-8",
        )
        adapter_module.write_manifest(
            output,
            ROOT,
            {
                "id": "current",
                "product": adapter_module.PRODUCT_HEIGHT_ANOMALY,
                "field": "z500_anomaly",
                "init_utc": "2026-08-13T00:00:00Z",
                "marker": "fresh-workflow-copy",
            },
            previous,
            4,
        )
        adapter_module.write_manifest(
            output,
            ROOT,
            {
                "id": "temperature-current",
                "product": adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY,
                "field": "t2m_anomaly",
                "init_utc": "2026-08-13T00:00:00Z",
            },
            previous,
            4,
        )
        retained_payload = json.loads(output.read_text(encoding="utf-8"))
        retained = retained_payload["runs"]
        retained_height = [run["id"] for run in retained if run["product"] == adapter_module.PRODUCT_HEIGHT_ANOMALY]
        retained_temperature = [run["id"] for run in retained if run["product"] == adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY]
        check(retained_height == ["current", "500mb_height_anomaly-old-4", "500mb_height_anomaly-old-3", "500mb_height_anomaly-old-2"], "height should retain current plus three prior height runs")
        check(retained_temperature == ["temperature-current", "2m_temperature_anomaly-old-4", "2m_temperature_anomaly-old-3", "2m_temperature_anomaly-old-2"], "each product should retain its current run plus three prior runs")
        check(next(run for run in retained if run["id"] == "current")["marker"] == "fresh-workflow-copy", "sequential suite renders should not be overwritten by the older published fallback")
        check(retained_payload["retention"]["scope"] == "per_product", "manifest retention should identify its per-product scope")
        check(retained_payload["retention"]["max_runs_per_product"] == 4, "manifest should retain four runs for each product")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages_workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
    update_workflow = UPDATE_WORKFLOW.read_text(encoding="utf-8")
    for term in (
        "product:",
        "500mb_height_anomaly",
        "500mb_height_absolute",
        "2m_temperature_anomaly",
        "mslp_anomaly",
        "precipitation_anomaly",
        "snow_water_equivalent_anomaly",
        "CFSV2_PRODUCT",
    ):
        check(term in workflow, f"workflow missing product selector term: {term}")
    for term in ("baseline", "reforecast", "monthly_grib_01", "lead_month", "GRIB2", "rolling", "NOMADS"):
        check(term in documentation, f"documentation missing contract term: {term}")
    for term in ("rolling-days", "rolling-state-dir", "actions/cache", "--ncei-calibration", "--common-reference-dir", "--common-reference-url"):
        check(term in workflow, f"workflow missing contract term: {term}")
    check("SCHEDULED_CFSV2_PRODUCTS: 500mb_height_anomaly,2m_temperature_anomaly,mslp_anomaly,precipitation_anomaly" in workflow, "twice-daily workflow should refresh the complete core anomaly suite")
    check('if [[ "${{ github.event_name }}" == "schedule" ]]' in workflow, "scheduled suite should remain distinct from manual single-product dispatch")
    for term in ("Restore published CFSv2 run history", "previous_manifest.json", "--previous-manifest", "--retain-runs 4"):
        check(term in workflow, f"workflow missing history-retention term: {term}")
    check("peaceiris/actions-gh-pages" not in workflow, "CFSv2 workflow must not publish Pages directly")
    check("peaceiris/actions-gh-pages" not in update_workflow, "WeatherNext workflow must not publish Pages directly")
    for term in ("Package WN2 Pages payload", "wn2-pages-${{ github.run_id }}"):
        check(term in update_workflow, f"WeatherNext workflow missing Pages payload term: {term}")
    for term in (
        "workflow_run:",
        "CFSv2 Rolling Seasonal Graphics",
        "ECMWF SEAS5 Seasonal Graphics",
        "WeatherNext Runner",
        "actions/download-artifact@v4",
        "wn2-pages-publish",
        "keep_files: false",
    ):
        check(term in pages_workflow, f"central Pages workflow missing term: {term}")

    print("CFSV2 CONTRACT OK: NOMADS source, HGT500/FLXF fields, conversions, baseline gate, manifest, wrapper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
