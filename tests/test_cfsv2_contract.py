#!/usr/bin/env python3
"""Static CFSv2 contract checks that do not require network or plotting libraries."""

import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "cfsv2_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_cfsv2.ps1"
DOC = ROOT / "docs" / "SEASONAL_CFSV2.md"
WORKFLOW = ROOT / ".github" / "workflows" / "cfsv2.yml"
SNOW_WORKFLOW = ROOT / ".github" / "workflows" / "cfsv2-snow.yml"
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
    check(SNOW_WORKFLOW.exists(), "CFSv2 snow-products workflow missing")
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
        "PRODUCT_850_TEMPERATURE_ANOMALY",
        "850mb_temperature_anomaly",
        "TMP:850 mb",
        "tmp850",
        "PRODUCT_SNOWFALL_ANOMALY",
        "snowfall_anomaly",
        "derive_snowfall_lwe_grid",
        "Dai_2008_land_seasonal_hyperbolic_tangent",
        "max(2-m, 850-hPa)",
        "dependencies",
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
        "CONUS_PRECIP_REGION = (-126.0, -66.0, 24.0, 50.0)",
        "CFSv2 Precipitation Anomaly (in)",
        "CONUS domain",
        "numeric_grid",
        "numeric_grid_format",
        "write_grid_state(anomaly_grid",
        "csv.gz",
        "PRODUCT_SWE_ANOMALY",
        "snow_water_equivalent_anomaly",
        "WEASD:surface",
        "snow_water_equivalent_inches",
        "SWE_ANOMALY_PALETTE",
        "SWE_ANOMALY_MIN_IN = -8.0",
        "SWE_ANOMALY_MAX_IN = 8.0",
        "monthly snow-water-equivalent average",
        "Snow-water equivalent (in)",
        "PRODUCT_SNOWFALL_ANOMALY",
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
        "--keep-source-cache",
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
        "map_inverse",
        "north_polar_stereographic",
        "PRODUCT_HEIGHT_ANOMALY_NH",
        "sample_source",
        "full global field",
        "header_detail",
        "Init {init_date:%d %b %Y %HZ}",
        "Height contours in dam",
        "seasonal_period_label",
        "DJF {start.year}\\u2013{end.year % 100:02d}",
        '"status"',
    ):
        check(term in adapter, f"adapter missing contract term: {term}")
    check("colorbar.set_label" not in adapter, "footer colorbar description should be absent")
    check("figure.text(0.035, 0.045" not in adapter, "footer text position should be absent")
    check("ANOMALY_MIN_M = -100.0" in adapter, "shared seasonal height renderer should use a -100 m lower bound")
    check("ANOMALY_MAX_M = 100.0" in adapter, "shared seasonal height renderer should use a +100 m upper bound")
    check("ANOMALY_TICKS = list(range(-100, 101, 10))" in adapter, "shared seasonal height renderer should label every 10 m")
    check("PRECIP_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "shared precipitation scale should remain available to dependent adapters")
    check("TEMPERATURE_ANOMALY_MIN_C = -7.0" in adapter, "shared temperature scale should use a -7 °C lower bound")
    check("TEMPERATURE_ANOMALY_MAX_C = 7.0" in adapter, "shared temperature scale should use a +7 °C upper bound")
    check("TEMPERATURE_ANOMALY_TICKS = list(range(-7, 8))" in adapter, "shared temperature scale should label every degree")
    check("MSLP_ANOMALY_TICKS = list(range(-20, 21, 2))" in adapter, "shared MSLP scale should remain available to dependent adapters")
    check("CFSV2_HEIGHT_ANOMALY_MIN_M = -100.0" in adapter, "CFSv2 height anomaly lower scale bound should be -100 m")
    check("CFSV2_HEIGHT_ANOMALY_MAX_M = 100.0" in adapter, "CFSv2 height anomaly upper scale bound should be +100 m")
    check("CFSV2_HEIGHT_ANOMALY_TICKS = list(range(-100, 101, 10))" in adapter, "CFSv2 height anomaly scale should label every 10 m")
    check("PRECIP_MONTHLY_ANOMALY_TICKS = [value / 2.0 for value in range(-8, 9)]" in adapter, "monthly precipitation should use 0.5-inch intervals from -4 to +4")
    check("PRECIP_SEASONAL_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "seasonal precipitation should retain 1-inch intervals from -8 to +8")
    check("CFSV2_TEMPERATURE_ANOMALY_TICKS = TEMPERATURE_ANOMALY_TICKS" in adapter, "CFSv2 2-m temperature should use the shared scale")
    check("CFSV2_MSLP_ANOMALY_TICKS = list(range(-10, 11))" in adapter, "CFSv2 MSLP should use 1 hPa intervals from -10 to +10")
    check('boundary_values = product_spec.get("anomaly_bounds", colorbar_ticks)' in adapter, "anomaly bounds should support product-specific neutral intervals")
    check("bounds = np.asarray(boundary_values, dtype=float)" in adapter, "anomaly bounds should be built from the configured boundaries")
    check('colorbar_options["boundaries"] = bounds' in adapter, "colorbar should use the labelled anomaly bounds")
    check("title_box = title_text.get_window_extent" in adapter, "header should prevent title/valid overlap")
    check("available_title_width" in adapter, "header should fit long valid-period labels")
    check("header_summary" in adapter and "suppress_header_detail" in adapter, "product headers should support concise subtitles")
    check('pad=1.2 if dense_tick_labels else 1.8' in adapter, "dense snowfall colorbar labels should sit close to the scale")
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
    check('mask_label = "selected-state" if mask_states else "countries"' in adapter, "domain-specific products should identify the selected-state land mask")
    check("fit_frame_to_domain" in adapter and "domain_frame_padding_fraction" in adapter, "regional land products should be able to fit their frame to the domain mask")
    check("_geojson_feature_records" in adapter, "state-specific masks should preserve GeoJSON feature metadata")
    check("_geojson_feature_matches" in adapter, "state-specific borders should match the land mask selection")
    check("projection_central_longitude" in adapter, "regional products should be able to center their projection")
    check("border_files" in adapter, "regional products should be able to limit rendered borders")
    check('axes.set_facecolor("#ffffff" if product_spec["name"] == PRODUCT_SWE_ANOMALY' in adapter, "SWE ocean background should be white")
    check(adapter.count("x_min -= projected_x_shift") == 1 and adapter.count("x_max -= projected_x_shift") == 1, "projected map window should apply the horizontal centering shift once")
    check("figsize=(9.0, 9.0)" in adapter, "seasonal graphic should use the 1080x1080 social canvas")
    check("map_height = map_width * (y_max - y_min) / (x_max - x_min)" in adapter, "seasonal map box should preserve projection aspect")
    check("horizontal_x, _ = map_project" in adapter, "projected map window should be centered from projection coordinates")
    check("top_edge_lons = np.linspace(lon_min, lon_max, 240)" in adapter, "projected map window should retain the full Greenland edge")
    check("border_lat_min = max(0.0, min(14.0, lat_min))" in adapter, "border rendering should respect the selected map latitude floor")
    check("if not (lon_min <= longitude <= lon_max) or latitude < border_lat_min:" in adapter, "border rendering should stay inside the North America/Greenland window")
    check("timeZone:'UTC'" in page, "Pages month labels should remain aligned with UTC map validity dates")
    check('<title>CFSv2 Model Viewer</title>' in page, "Pages title should use generic CFSv2 model-viewer branding")
    check('WN2 / CFSv2' not in page, "CFSv2 direct viewer should not use the umbrella dashboard branding")
    check('href="../">Seasonal dashboard</a>' in page, "CFSv2 direct viewer should link to the unified seasonal dashboard")
    check("preferredTargetIndex" in page, "CFSv2 viewer should default to the seasonal aggregate when one is present")
    check('id="product-select"' in page, "Pages viewer missing parameter selector")
    check('id="run-select"' in page, "Pages viewer missing run-history selector")
    check("'2m_temperature_anomaly': '2-m Temperature Anomaly'" in page, "Pages viewer missing 2-m temperature label")
    check("'850mb_temperature_anomaly': '850-mb Temperature Anomaly'" in page, "Pages viewer missing 850-mb temperature label")
    check("'mslp_anomaly': 'Mean Sea-Level Pressure Anomaly'" in page, "Pages viewer missing MSLP label")
    check("'snowfall_anomaly': 'CONUS Snowfall Departure'" in page, "Pages viewer missing snowfall label")
    check("Retaining ${history} prior run${history === 1 ? '' : 's'} per parameter" in page, "Pages viewer should report per-parameter run history")
    check("available.find(run => !isFailedRun(run))" in page, "Pages viewer should default to the latest non-failed run")
    check("if (target?.label) return target.label;" in page, "Pages viewer should honor manifest labels for DJF and JFM")
    adapter_module = load_adapter()
    august_snowfall = adapter_module.default_winter_snowfall_windows("2026080100")
    september_snowfall = adapter_module.default_winter_snowfall_windows("2026090400")
    check(august_snowfall == ([4, 5, 6, 7], [[4, 5, 6], [5, 6, 7]]), "August snowfall defaults should target Dec-Mar plus DJF/JFM")
    check(september_snowfall == ([3, 4, 5, 6], [[3, 4, 5], [4, 5, 6]]), "snowfall target months must remain fixed when the initialization month advances")
    check(adapter_module.parse_seasonal_windows("3,4,5;4,5,6") == [[3, 4, 5], [4, 5, 6]], "multiple snowfall seasonal windows should parse independently")
    check(adapter_module.seasonal_period_label("202701", "202703") == "JFM 2027", "JFM seasonal targets should use a concise label")
    try:
        adapter_module.default_winter_snowfall_windows("2026050100")
    except adapter_module.CFSv2Error:
        pass
    else:
        raise AssertionError("the operational snowfall preset should reject an incomplete Dec-Mar horizon")
    merged_snowfall = adapter_module.merge_seasonal_window_runs(
        [
            {
                "id": "cfsv2-2026090400-snowfall_anomaly",
                "status": "rendered",
                "targets": [
                    {"id": "dec", "status": "rendered", "image": "dec.jpg"},
                    {"id": "jan", "status": "rendered", "image": "jan.jpg"},
                    {"id": "feb", "status": "rendered", "image": "feb.jpg"},
                    {"id": "mar", "status": "rendered", "image": "mar.jpg"},
                    {"id": "djf", "status": "rendered", "image": "djf.jpg"},
                ],
            },
            {
                "id": "cfsv2-2026090400-snowfall_anomaly",
                "status": "rendered",
                "targets": [
                    {"id": "jan", "status": "decoded"},
                    {"id": "feb", "status": "decoded"},
                    {"id": "mar", "status": "decoded"},
                    {"id": "jfm", "status": "rendered", "image": "jfm.jpg"},
                ],
            },
        ],
        [[3, 4, 5], [4, 5, 6]],
    )
    check([target["id"] for target in merged_snowfall["targets"]] == ["dec", "jan", "feb", "mar", "djf", "jfm"], "snowfall manifest should retain four monthly maps and both seasonal maps")
    check(merged_snowfall["targets"][1]["image"] == "jan.jpg", "seasonal-window merging must not replace a rendered monthly map with a decode-only fragment")
    check(merged_snowfall["seasonal_windows"] == [[3, 4, 5], [4, 5, 6]], "snowfall manifest should record both configured seasonal windows")
    with tempfile.TemporaryDirectory() as temporary:
        wrapper_calls = []
        final_run = {}
        original_single_window = adapter_module._run_single_window
        original_write_manifest = adapter_module.write_manifest
        try:
            def fake_single_window(child):
                seasonal_id = "djf" if child.seasonal_window == "3,4,5" else "jfm"
                wrapper_calls.append((child.lead_months, child.seasonal_window, getattr(child, "_seasonal_only", False)))
                monthly_targets = (
                    [
                        {"id": "dec", "status": "rendered", "image": "dec.jpg"},
                        {"id": "jan", "status": "rendered", "image": "jan.jpg"},
                        {"id": "feb", "status": "rendered", "image": "feb.jpg"},
                        {"id": "mar", "status": "rendered", "image": "mar.jpg"},
                    ]
                    if seasonal_id == "djf"
                    else [
                        {"id": "jan", "status": "decoded"},
                        {"id": "feb", "status": "decoded"},
                        {"id": "mar", "status": "decoded"},
                    ]
                )
                payload = {
                    "runs": [{
                        "id": "cfsv2-2026090400-snowfall_anomaly",
                        "status": "rendered",
                        "targets": monthly_targets + [{"id": seasonal_id, "status": "rendered", "image": f"{seasonal_id}.jpg"}],
                    }]
                }
                Path(child.manifest).write_text(json.dumps(payload), encoding="utf-8")
                return 0

            def capture_manifest(_path, _repo_root, run_entry, _previous_manifest, _retain_runs):
                final_run.update(run_entry)

            adapter_module._run_single_window = fake_single_window
            adapter_module.write_manifest = capture_manifest
            wrapper_result = adapter_module.run(SimpleNamespace(
                product=adapter_module.PRODUCT_SNOWFALL_ANOMALY,
                absolute=False,
                decode_only=False,
                init="2026090400",
                lead_months="3,4,5,6",
                seasonal_window="3,4,5;4,5,6",
                manifest=Path(temporary) / "manifest.json",
                previous_manifest=None,
                retain_runs=4,
            ))
        finally:
            adapter_module._run_single_window = original_single_window
            adapter_module.write_manifest = original_write_manifest
        check(wrapper_result == 0, "multi-window snowfall wrapper should return success")
        check(wrapper_calls == [("3,4,5,6", "3,4,5", False), ("4,5,6", "4,5,6", True)], "second snowfall window should reuse decoded inputs without re-rendering monthly maps")
        check([target["id"] for target in final_run["targets"]] == ["dec", "jan", "feb", "mar", "djf", "jfm"], "multi-window snowfall wrapper should publish exactly six default maps")
    mature_cycles = adapter_module.filter_mature_cycle_inits(
        ["2026090412", "2026090406", "2026090400"],
        660,
        now=adapter_module.dt.datetime(2026, 9, 4, 17, 45, tzinfo=adapter_module.dt.timezone.utc),
    )
    check(mature_cycles == ["2026090406", "2026090400"], "maturity filter should exclude the newer incomplete cycle directory")
    readiness_calls = []

    def readiness_probe(url):
        readiness_calls.append(url)
        return 403 if ".2026082518." in url else 200

    selected_ready_init = adapter_module.discover_latest_ready_init(
        [
            adapter_module.PRODUCT_HEIGHT_ANOMALY,
            adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY,
            adapter_module.PRODUCT_MSLP_ANOMALY,
            adapter_module.PRODUCT_PRECIPITATION_ANOMALY,
        ],
        [4, 5, 6],
        candidate_inits=["2026082518", "2026082512"],
        probe=readiness_probe,
    )
    check(selected_ready_init == "2026082512", "readiness discovery should fall back to the newest complete cycle")
    check(len(readiness_calls) == 12, "readiness discovery should probe six unique source files for each candidate")
    try:
        adapter_module.discover_latest_ready_init(
            [adapter_module.PRODUCT_HEIGHT_ANOMALY],
            [4, 5, 6],
            candidate_inits=["2026082518"],
            probe=lambda _url: 404,
        )
    except adapter_module.CFSv2Error:
        pass
    else:
        raise AssertionError("readiness discovery should fail when no candidate has all required files")
    retry_clock = [0.0]
    retry_attempts = {}

    def delayed_readiness_probe(url):
        retry_attempts[url] = retry_attempts.get(url, 0) + 1
        return 404 if retry_attempts[url] == 1 else 200

    def delayed_readiness_sleep(seconds):
        retry_clock[0] += seconds

    delayed_init = adapter_module.discover_latest_ready_init(
        [adapter_module.PRODUCT_HEIGHT_ANOMALY],
        [4],
        candidate_inits=["2026082606", "2026082600"],
        probe=delayed_readiness_probe,
        wait_for_latest_minutes=1,
        retry_seconds=30,
        sleep_fn=delayed_readiness_sleep,
        clock_fn=lambda: retry_clock[0],
    )
    check(delayed_init == "2026082606", "readiness retry should wait for the newest listed cycle")
    check(retry_clock[0] == 30, "readiness retry should use the configured retry interval")
    check(all(count == 2 for count in retry_attempts.values()), "readiness retry should re-probe the newest cycle")
    check(len(adapter_module.ANOMALY_PALETTE) == len(adapter_module.ANOMALY_TICKS) - 1, "height anomaly colors should align with labelled transitions")
    check(len(adapter_module.ANOMALY_PALETTE) == len(adapter_module.CFSV2_HEIGHT_ANOMALY_TICKS) - 1, "CFSv2 height anomaly colors should align with labelled transitions")
    check(len(adapter_module.TEMPERATURE_ANOMALY_PALETTE) == len(adapter_module.CFSV2_TEMPERATURE_ANOMALY_TICKS) - 1, "CFSv2 2-m temperature colors should align with labelled transitions")
    check(len(adapter_module.MSLP_ANOMALY_PALETTE) == len(adapter_module.CFSV2_MSLP_ANOMALY_TICKS) - 1, "CFSv2 MSLP colors should align with labelled transitions")
    check(len(adapter_module.PRECIP_ANOMALY_PALETTE) == len(adapter_module.PRECIP_MONTHLY_ANOMALY_TICKS) - 1, "monthly precipitation colors should align with labelled transitions")
    check(len(adapter_module.PRECIP_ANOMALY_PALETTE) == len(adapter_module.PRECIP_SEASONAL_ANOMALY_TICKS) - 1, "seasonal precipitation colors should align with labelled transitions")
    check(len(adapter_module.SWE_ANOMALY_PALETTE) == len(adapter_module.SWE_ANOMALY_TICKS) - 1, "SWE colors should align with labelled transitions")
    t2m_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY]
    t850_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_850_TEMPERATURE_ANOMALY]
    mslp_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_MSLP_ANOMALY]
    precip_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_PRECIPITATION_ANOMALY]
    height_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_HEIGHT_ANOMALY]
    northern_height_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_HEIGHT_ANOMALY_NH]
    swe_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_SWE_ANOMALY]
    snowfall_spec = adapter_module.PRODUCT_SPECS[adapter_module.PRODUCT_SNOWFALL_ANOMALY]
    check(height_spec["region"] == adapter_module.DEFAULT_REGION, "the established 500-mb view must retain its North America frame")
    check(northern_height_spec["region"] == adapter_module.NORTHERN_HEMISPHERE_REGION, "the Northern Hemisphere 500-mb view must use the hemisphere frame")
    check(northern_height_spec["projection"] == "north_polar_stereographic", "the Northern Hemisphere 500-mb view must use the polar projection")
    check(t2m_spec["region"] == adapter_module.CONUS_REGION, "non-height CFSv2 maps must use the CONUS frame")
    check(t850_spec["region"] == adapter_module.CONUS_REGION, "non-height CFSv2 maps must use the CONUS frame")
    check(mslp_spec["region"] == adapter_module.CONUS_REGION, "non-height CFSv2 maps must use the CONUS frame")
    check(adapter_module.anomaly_style(height_spec)[:2] == (-100.0, 100.0), "height anomaly style should use the tighter ±100 m range")
    check(adapter_module.anomaly_style(t2m_spec)[:2] == (-7.0, 7.0), "temperature anomaly style should use the shared ±7 °C range")
    check(t2m_spec["anomaly_ticks"] == list(range(-7, 8)), "temperature anomaly style should use 1 °C labelled bounds")
    check(adapter_module.anomaly_style(mslp_spec)[:2] == (-10.0, 10.0), "MSLP anomaly style should use the tighter ±10 hPa range")
    check(adapter_module.anomaly_style(precip_spec, seasonal=False)[:2] == (-4.0, 4.0), "monthly precipitation should use the tighter ±4-inch range")
    check(adapter_module.anomaly_style(precip_spec, seasonal=True)[:2] == (-8.0, 8.0), "seasonal precipitation should retain the ±8-inch total range")
    check(swe_spec["map_domain"] == "land", "SWE must retain a land-only render domain")
    check(adapter_module.manifest_product_key({"field": "z500_anomaly"}) == adapter_module.PRODUCT_HEIGHT_ANOMALY, "legacy manifests should map height fields to the current product key")
    check(t2m_spec["source_kind"] == "flxf" and t2m_spec["grid_shape"] == (384, 190), "2-m temperature should use the FLXF flux grid")
    check(t850_spec["source_kind"] == "pgbf" and t850_spec["grid_shape"] == (360, 181), "850-mb temperature should use the PGBF pressure grid")
    check(t850_spec["match"] == ":TMP:850 mb:", "850-mb temperature should decode the pressure-level TMP field")
    check(mslp_spec["source_kind"] == "pgbf" and mslp_spec["grid_shape"] == (360, 181), "MSLP should use the PGBF pressure grid")
    check(snowfall_spec["source_kind"] == "derived", "CFSv2 snowfall should be explicitly derived")
    check(snowfall_spec["dependencies"] == (
        adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY,
        adapter_module.PRODUCT_850_TEMPERATURE_ANOMALY,
        adapter_module.PRODUCT_PRECIPITATION_ANOMALY,
    ), "CFSv2 snowfall should require 2-m temperature, 850-mb temperature, and precipitation")
    check(snowfall_spec["map_domain"] == "land" and snowfall_spec["region"] == adapter_module.CONUS_PRECIP_REGION, "CFSv2 snowfall should use a CONUS land-only map")
    check(adapter_module.product_dependency_names(adapter_module.PRODUCT_SNOWFALL_ANOMALY) == snowfall_spec["dependencies"], "readiness should expand snowfall to its raw dependencies")
    snowfall_seasonal_baseline = adapter_module.seasonal_baseline_manifest(
        [
            {
                "label": "derived snowfall baseline",
                "dependencies": [
                    {
                        "product": adapter_module.PRODUCT_2M_TEMPERATURE_ANOMALY,
                        "file": "tmp2m-l04.grb2",
                        "url": "https://example.test/tmp2m-l04.grb2",
                        "fallback": "cached_prior_initialization",
                        "requested_initialization": "2026090300",
                        "used_initialization": "2026082700",
                        "requested_url": "https://example.test/requested-tmp2m-l04.grb2",
                        "fallback_error": "HTTP 503",
                    },
                    {
                        "product": adapter_module.PRODUCT_850_TEMPERATURE_ANOMALY,
                        "file": "tmp850-l04.grb2",
                        "url": "https://example.test/tmp850-l04.grb2",
                        "fallback": None,
                    },
                    {
                        "product": adapter_module.PRODUCT_PRECIPITATION_ANOMALY,
                        "file": "prate-l04.grb2",
                        "url": "https://example.test/prate-l04.grb2",
                        "fallback": None,
                    },
                ],
            }
        ],
        "fallback label",
        adapter_module.NCEI_CALIBRATION_YEARS,
        rolling_init="2026090300",
    )
    check(
        snowfall_seasonal_baseline["files"] == ["tmp2m-l04.grb2", "tmp850-l04.grb2", "prate-l04.grb2"],
        "seasonal snowfall should flatten dependency baseline files without requiring baseline.file",
    )
    check(
        snowfall_seasonal_baseline["label"] == "derived snowfall baseline"
        and snowfall_seasonal_baseline["anchor_init"] == "2026090300",
        "seasonal snowfall should retain its derived baseline label and rolling anchor",
    )
    check(
        len(snowfall_seasonal_baseline["fallbacks"]) == 1
        and snowfall_seasonal_baseline["fallbacks"][0]["error"] == "HTTP 503",
        "seasonal snowfall should retain nested calibration fallback provenance",
    )
    direct_seasonal_baseline = adapter_module.seasonal_baseline_manifest(
        [{"file": "z500-l04.grb2", "label": "direct baseline", "url": "https://example.test/z500-l04.grb2"}],
        "fallback label",
        adapter_module.NCEI_CALIBRATION_YEARS,
    )
    check(
        direct_seasonal_baseline["files"] == ["z500-l04.grb2"]
        and direct_seasonal_baseline["urls"] == ["https://example.test/z500-l04.grb2"],
        "ordinary products should retain their direct baseline provenance",
    )
    converted_mslp = adapter_module.prepare_product_grid(
        adapter_module.Grid([0.0], [0.0], [[101325.0]]), mslp_spec, "202608"
    )
    check(converted_mslp.values == [[1013.25]], "PRES should convert from Pa to hPa")
    converted = adapter_module.snow_water_equivalent_inches(
        adapter_module.Grid([0.0], [0.0], [[25.4]])
    )
    check(converted.values == [[1.0]], "WEASD should convert from kg m-2 to inches")
    snowfall_inputs = {
        "member-1": adapter_module.Grid([0.0, 1.0], [0.0, 1.0], [[271.15, 271.15], [271.15, 271.15]]),
        "member-2": adapter_module.Grid([0.0, 1.0], [0.0, 1.0], [[271.15, 271.15], [271.15, 271.15]]),
    }
    snowfall_850 = {
        key: adapter_module.Grid([0.0, 1.0], [0.0, 1.0], [[270.15, 270.15], [270.15, 270.15]])
        for key in snowfall_inputs
    }
    snowfall_precip = {
        key: adapter_module.Grid([0.0, 1.0], [0.0, 1.0], [[1.0, 1.0], [1.0, 1.0]])
        for key in snowfall_inputs
    }
    derived_snowfall, snowfall_diagnostics = adapter_module.derive_snowfall_lwe_grid(
        snowfall_inputs,
        snowfall_850,
        snowfall_precip,
        "202612",
    )
    expected_snowfall = adapter_module.snowfall_fraction_from_temperature_c(-2.0, "DJF")
    check(math.isclose(derived_snowfall.values[0][0], expected_snowfall, rel_tol=1e-9), "CFSv2 snowfall should apply the warmer-level Dai phase fraction to monthly precipitation")
    check(snowfall_diagnostics["member_or_cycle_count"] == 2, "snowfall diagnostics should record the contributing members")
    check("regridded" in snowfall_diagnostics["regridding"], "snowfall diagnostics should disclose the 850-mb regrid")
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
    snow_workflow = SNOW_WORKFLOW.read_text(encoding="utf-8")
    pages_workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
    update_workflow = UPDATE_WORKFLOW.read_text(encoding="utf-8")
    for term in (
        "product:",
        "- all",
        "500mb_height_anomaly",
        "500mb_height_absolute",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "mslp_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "CFSV2_PRODUCT",
    ):
        check(term in workflow, f"workflow missing product selector term: {term}")
    for term in ("baseline", "reforecast", "monthly_grib_01", "lead_month", "GRIB2", "rolling", "NOMADS"):
        check(term in documentation, f"documentation missing contract term: {term}")
    for term in ("rolling-days", "rolling-state-dir", "actions/cache", "--ncei-calibration", "--allow-stale-calibration", "--common-reference-dir", "--common-reference-url"):
        check(term in workflow, f"workflow missing contract term: {term}")
    for term in (
        "discover_latest_ready_init",
        "readiness_products",
        "readiness_wait_minutes",
        "readiness_retry_seconds",
        "wait_for_latest_minutes",
        "retry_seconds",
        "filter_mature_cycle_inits",
        "minimum_anchor_age_minutes=\"$CFSV2_MINIMUM_ANCHOR_AGE_MINUTES\"",
        '45 5,11,17,23 * * *',
        "default_winter_snowfall_windows",
        "snowfall_lead_months",
        "snowfall_seasonal_windows",
        "product_lead_months",
    ):
        check(term in workflow, f"workflow missing CFSv2 readiness term: {term}")
    for term in (
        "concurrency:",
        "cancel-in-progress: true",
        "Set up wgrib2",
        "./.github/actions/setup-wgrib2",
        "readiness_wait_minutes=30",
        "readiness_retry_seconds=180",
    ):
        check(term in workflow, f"workflow missing speed-up term: {term}")
    check("--allow-partial-rolling" not in workflow, "scheduled CFSv2 workflow must not publish an incomplete rolling blend")
    check("SCHEDULED_CFSV2_PRODUCTS: 500mb_height_anomaly,500mb_height_anomaly_nh,850mb_temperature_anomaly,2m_temperature_anomaly,mslp_anomaly,precipitation_anomaly,snowfall_anomaly" in workflow, "four-times-daily workflow should refresh the complete CFSv2 anomaly suite")
    check("ALL_CFSV2_PRODUCTS: 500mb_height_anomaly,500mb_height_anomaly_nh,500mb_height_absolute,850mb_temperature_anomaly,2m_temperature_anomaly,mslp_anomaly,precipitation_anomaly,snowfall_anomaly" in workflow, "manual all action should cover every validated CFSv2 menu field")
    check("- snow_water_equivalent_anomaly" not in workflow, "quarantined CFSv2 SWE must not appear in the Actions menu")
    check("is_retired_product(product_name)" in adapter, "the CFSv2 adapter must block quarantined products before downloading data")
    check("choices=tuple(product for product in PRODUCT_SPECS if not is_retired_product(product))" in adapter, "the CFSv2 CLI must hide quarantined products")
    check("snow_water_equivalent_anomaly" not in WRAPPER.read_text(encoding="utf-8"), "the CFSv2 PowerShell menu must hide quarantined SWE")
    check('headers={"Range": "bytes=0-0"}' in adapter and "stream=True" in adapter, "CFSv2 readiness must fall back to a ranged GET when NOMADS rejects HEAD")
    check('elif [[ "$CFSV2_PRODUCT" == "all" ]]' in workflow, "manual CFSv2 all action should expand to the full product list")
    check('github.event_name }}" == "schedule" || "$CFSV2_PRODUCT" == "all"' in workflow, "manual CFSv2 all action should use delayed-file readiness retries")
    check(workflow.count('github.event_name }}" == "schedule" || "$CFSV2_PRODUCT" == "all"') >= 2, "scheduled and manual all-field runs should both use the target-aware snowfall preset")
    check("ROLLING_DAYS: ${{ inputs.rolling_days || '6' }}" in workflow, "scheduled CFSv2 workflow should default to six rolling days")
    dispatch_inputs = workflow.split("  workflow_dispatch:", 1)[1].split("  workflow_call:", 1)[0]
    check("rolling_days:" not in dispatch_inputs, "manual CFSv2 Actions must not offer archive-unsafe rolling windows")
    check("Validate archive-safe rolling window" in workflow and "^[1-6]$" in workflow, "reusable CFSv2 calls must reject rolling windows outside the live archive before downloading")
    check("github.workflow" in workflow and "scheduled-suite" in workflow and "inputs.product" in workflow, "CFSv2 concurrency must isolate scheduled, all-fields, and focused product runs")
    check("workflow_call:" in workflow, "CFSv2 workflow should be reusable by focused product menus")
    for term in (
        "name: CFSv2 Snowfall Graphics",
        "CFSv2 snowfall departure",
        "snowfall_anomaly",
        "uses: ./.github/workflows/cfsv2.yml",
        "lead_months: ${{ inputs.lead_months }}",
        "seasonal_window: ${{ inputs.seasonal_window }}",
        'rolling_days: "6"',
    ):
        check(term in snow_workflow, f"CFSv2 snow-products workflow missing term: {term}")
    snow_dispatch_inputs = snow_workflow.split("  workflow_dispatch:", 1)[1].split("\npermissions:", 1)[0]
    check(snow_dispatch_inputs.count('default: "operational-winter"') == 2, "focused snowfall Actions should default to Dec-Mar plus DJF/JFM")
    check("semicolons" in snow_dispatch_inputs and "DJF and JFM" in snow_dispatch_inputs, "focused snowfall Actions should explain multiple seasonal windows")
    check("rolling_days:" not in snow_dispatch_inputs, "focused snowfall Actions must always use the supported 24-cycle window")
    check("snow_water_equivalent_anomaly" not in snow_workflow, "the focused snow workflow must not offer quarantined SWE")
    check("actions/cache/save@v4" in workflow and "if: always()" in workflow, "CFSv2 workflow should retain warmed rolling state after failed attempts")
    check("--keep-source-cache" in workflow and "Trim transient CFSv2 source cache" in workflow, "scheduled CFSv2 products should reuse and then trim source downloads")
    check('if [[ "${{ github.event_name }}" == "schedule" ]]' in workflow, "scheduled suite should remain distinct from manual single-product dispatch")
    for term in ("Restore published CFSv2 run history", "previous_manifest.json", "--previous-manifest", "--retain-runs 4"):
        check(term in workflow, f"workflow missing history-retention term: {term}")
    check("peaceiris/actions-gh-pages" not in workflow, "CFSv2 workflow must not publish Pages directly")
    check("peaceiris/actions-gh-pages" not in update_workflow, "WeatherNext workflow must not publish Pages directly")
    check("|| 40" not in PAGE.read_text(encoding="utf-8"), "CFSv2 viewer must not hardcode a rolling cycle count")
    with tempfile.TemporaryDirectory() as temporary:
        cache_dir = Path(temporary)
        fallback_path = adapter_module.cached_calibration_path(cache_dir, "2026082306", 4, "pgbf")
        fallback_path.parent.mkdir(parents=True)
        fallback_path.write_bytes(b"cached calibration")
        fallback = adapter_module.cached_calibration_fallback(cache_dir, "2026082506", 4, "pgbf")
        check(fallback == (fallback_path, "2026082306"), "calibration fallback should select a recent prior same-cycle cache")
        original_download = adapter_module.download_file
        download_kwargs = {}
        try:
            def failed_calibration_download(*args, **kwargs):
                download_kwargs.update(kwargs)
                raise RuntimeError("HTTP 503 from NCEI test endpoint")

            adapter_module.download_file = failed_calibration_download
            loaded = adapter_module.load_ncei_calibration(
                cache_dir=cache_dir,
                init="2026082506",
                lead=4,
                source_kind="pgbf",
                request_delay=0.0,
                last_request=0.0,
                allow_stale=True,
            )
            check(loaded[0] == fallback_path and loaded[1] == "2026082306", "NCEI loader should use the selected cached fallback")
            check(loaded[4] == "HTTP 503 from NCEI test endpoint", "NCEI loader should preserve the triggering error")
            check(download_kwargs.get("attempts") == 3, "NCEI calibration should use bounded retries")
        finally:
            adapter_module.download_file = original_download
    for term in ("Package WN2 Pages payload", "wn2-pages-${{ github.run_id }}"):
        check(term in update_workflow, f"WeatherNext workflow missing Pages payload term: {term}")
    for term in (
        "workflow_run:",
        "CFSv2 Rolling Seasonal Graphics",
        "CFSv2 Snowfall Graphics",
        "ECMWF SEAS5 Seasonal Graphics",
        "WeatherNext Runner",
        "actions/download-artifact@v4",
        "wn2-pages-publish",
        "keep_files: false",
    ):
        check(term in pages_workflow, f"central Pages workflow missing term: {term}")
    for term in (
        "Normalize source workflow name",
        '"CFSv2 snowfall departure"*',
        'echo "SOURCE_WORKFLOW=CFSv2 Snowfall Graphics" >> "$GITHUB_ENV"',
    ):
        check(term in pages_workflow, f"central Pages workflow missing dynamic snow-run normalization: {term}")

    print("CFSV2 CONTRACT OK: NOMADS source, HGT500/FLXF fields, conversions, baseline gate, manifest, wrapper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

