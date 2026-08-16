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
        "PRECIP_ANOMALY_MIN_IN = -8.0",
        "PRECIP_ANOMALY_MAX_IN = 8.0",
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
    check("ANOMALY_MIN_M = -200.0" in adapter, "anomaly lower scale bound should be -200 m")
    check("ANOMALY_MAX_M = 200.0" in adapter, "anomaly upper scale bound should be +200 m")
    check("PRECIP_ANOMALY_TICKS = list(range(-8, 9))" in adapter, "precipitation scale should label every inch from -8 to +8")
    check("ANOMALY_TICKS = list(range(-200, 201, 20))" in adapter, "anomaly scale should label every 20 m from -200 to +200")
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
    check('href="../">Seasonal dashboard</a>' in page, "CFSv2 dire