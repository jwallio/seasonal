#!/usr/bin/env python3
"""Static contract checks for the unified seasonal model dashboard."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "public" / "seasonal" / "index.html"
STYLESHEET = ROOT / "public" / "seasonal" / "dashboard.css"
DASHBOARD_SCRIPT = ROOT / "public" / "seasonal" / "dashboard.js"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pages.yml"
THUMBNAIL_SCRIPT = ROOT / "scripts" / "build_seasonal_thumbnails.py"
CATALOG_SCRIPT = ROOT / "scripts" / "build_seasonal_catalog.py"
PRODUCT_REGISTRY = ROOT / "scripts" / "seasonal_products.py"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(PAGE.exists(), "unified seasonal dashboard is missing")
    check(STYLESHEET.exists(), "seasonal dashboard stylesheet is missing")
    check(DASHBOARD_SCRIPT.exists(), "seasonal dashboard script is missing")
    check(PUBLISH_WORKFLOW.exists(), "central Pages workflow is missing")
    check(THUMBNAIL_SCRIPT.exists(), "seasonal thumbnail builder is missing")
    check(CATALOG_SCRIPT.exists(), "seasonal catalog builder is missing")
    check(PRODUCT_REGISTRY.exists(), "canonical seasonal product registry is missing")
    page_markup = PAGE.read_text(encoding="utf-8")
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    dashboard_script = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    page = "\n".join((page_markup, stylesheet, dashboard_script))
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    check('href="dashboard.css"' in page_markup, "dashboard must load its external stylesheet")
    check('src="dashboard.js" defer' in page_markup, "dashboard must defer its external script")
    check("<style>" not in page_markup and "<script>" not in page_markup, "dashboard must not restore large inline assets")

    for term in (
        "<title>Seasonal Model Dashboard</title>",
        "<h1>Seasonal Model Dashboard</h1>",
        "CFSv2",
        "ECMWF SEAS5",
        "CanSIPS v3",
        "CMA CPSv3",
        "C3S multi-system",
        "JMA",
        "APCC MME",
        "NASA GEOS-S2S-3",
        "NOAA NMME",
        "Super Ensemble",
        "seasonal/cfsv2_manifest.json",
        "seasonal/seas5_manifest.json",
        "seasonal/cansips_manifest.json",
        "seasonal/cma_cpsv3_manifest.json",
        "seasonal/c3s_manifest.json",
        "seasonal/jma_manifest.json",
        "seasonal/apcc_manifest.json",
        "seasonal/geos_s2s3_manifest.json",
        "seasonal/nmme_manifest.json",
        "seasonal/superensemble_manifest.json",
        'id="model-select"',
        'id="product-select"',
        'id="run-select"',
        'id="overview-tab"',
        'id="overview-view"',
        'id="overview-matrix"',
        'id="overview-matrix-body"',
        'id="compare-tab"',
        'id="compare-product-select"',
        'id="compare-target-select"',
        'id="compare-baseline-select"',
        'id="compare-role-select"',
        'id="compare-available-only"',
        'id="compare-controls-toggle"',
        'id="compare-control-fields"',
        'id="compare-missing"',
        'id="compare-grid"',
        'id="copy-link"',
        'id="download-link"',
        'id="map-dialog"',
        'id="provenance-details"',
        'role="tablist"',
        'role="tabpanel"',
        "const DEFAULT_COMPARE_PRODUCT = '500mb_height_anomaly';",
        "const COMPARE_PRODUCTS = [",
        "function compareProductAliases(productKey)",
        "function compareProductOptions()",
        "function comparePeriodOptions(productKey",
        "function compareBaselineOptions(productKey",
        "function compareFilteredModels()",
        "const COMPARE_MIN_VALID_MONTH = 202612;",
        "function compareTargetMeetsValidCutoff(target)",
        "common_1991_2020",
        "Common 1991–2020 (limited)",
        "function freshnessState(modelKey, productKey)",
        "const CATALOG_URL = assetPath('seasonal/catalog.json');",
        "function catalogProductConfig(productKey)",
        "function canonicalProductKey(productKey)",
        "function productSupport(modelKey, productKey)",
        "function productSurface(modelKey, productKey)",
        "run?._catalog?.comparable !== false",
        "applicable: false",
        "supported model-parameter surfaces",
        "intentionally unsupported or quarantined",
        "Excluded until regenerated with canonical units/metadata:",
        "function loadDashboardData()",
        "seasonal_dashboard_catalog",
        "function renderOverview()",
        "function readUrlState()",
        "function syncUrlState()",
        "history.replaceState",
        "navigator.clipboard.writeText",
        "function openMapDialog(src, title)",
        "function thumbnailPath(value)",
        "seasonal/thumbnails/",
        "image.src = thumbnailPath(asset.image)",
        "openMapDialog(fullImage, image.alt)",
        "usedFullImageFallback",
        "window.matchMedia('(min-width: 901px)')",
        "function syncProvenanceDisclosure",
        "const compareControlsMedia = window.matchMedia('(max-width: 600px)');",
        "function setCompareControlsCollapsed",
        "function syncCompareControlsDisclosure",
        "dialog.showModal()",
        "event.key === 'ArrowRight'",
        "position:sticky",
        "function renderCompare()",
        "selection.compareProduct = event.target.value",
        "product_hours",
        "target_month",
        "source_url",
        "preferredTarget",
        "const COMPONENT_LABELS = {",
        "function componentLabel(run)",
        "function runDisplayName(model, run)",
        "function preferredComponent(modelKey, productKey)",
        "return 'ENSMEAN';",
        "function runCoverageCounts(run, target = null)",
        "function defaultEligibleRun(run)",
        "function isFailedRun(run)",
        "function preferredRun(runs,",
        "if (!usable.length) return null;",
        "&& !isFailedRun(run)",
        "document.createElement('optgroup')",
        "forecast surfaces available",
        "Not published for this selection:",
        "probability_above_normal",
        "multi_model_consensus",
        "const DEFAULT_PRODUCT_PRIORITY = [",
        "const DEFAULT_PERIOD_PRIORITY = ['djf', 'december'];",
        "function defaultTargetPeriod(target)",
        "function defaultSelectionForModel(model, products)",
        "function genericSelectionForModel(model, products)",
        "preferredTargetKey",
    ):
        check(term in page, f"unified dashboard missing term: {term}")

    priority_terms = (
        "defaultTargetPeriod(target)",
        "defaultSelectionForModel(model, products)",
        "genericSelectionForModel(model, products)",
    )
    priority_positions = [page.index(term) for term in priority_terms]
    check(priority_positions == sorted(priority_positions), "default seasonal fallback helpers should be defined in execution order")

    cutoff_match = re.search(r"const COMPARE_MIN_VALID_MONTH = (\d+);", page)
    check(cutoff_match is not None, "compare valid-period cutoff is missing")
    check(int(cutoff_match.group(1)) == 202612, "compare valid-period cutoff must keep December 2026 and later")
    check("/^(\\d{6})(?:-\\d{6})?$/" in page, "compare cutoff must recognize monthly and seasonal target keys")
    check("Number(match[1]) >= COMPARE_MIN_VALID_MONTH" in page, "compare cutoff must use the target period's starting month")
    compare_target_block = page[page.index("function compareTarget(run"):page.index("function compareTargetKeys")]
    compare_keys_block = page[page.index("function compareTargetKeys"):page.index("function comparePeriodSort")]
    check("compareTargetMeetsValidCutoff(target)" in compare_target_block, "compare cards must reject targets before the valid-period cutoff")
    check("compareTargetMeetsValidCutoff(target)" in compare_keys_block, "compare dropdown must reject targets before the valid-period cutoff")

    for product in (
        "500mb_height_anomaly",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "precipitation_anomaly",
        "mslp_anomaly",
        "sea_surface_temperature_anomaly",
    ):
        check(product in page, f"compare parameter menu is missing {product}")
    check("'sea_surface_temperature_anomaly', 'sst_anomaly'" in page, "compare SST must normalize the two published manifest product names")
    check("'geos_s2s3'" in page and "'nmme'" in page, "parameter comparison must include GEOS and NMME when they publish the selected field")
    check("preferredComponent: 'multisystem'" in page, "C3S comparisons should prefer the multi-system blend")
    check("preferredComponent: 'ENSMEAN'" in page, "NMME comparisons should prefer the official ensemble mean")
    check("runDisplayName(model, run)" in page[page.index("function runLabel"):page.index("function isFailedRun")], "run-history labels must use the selected blend or component identity")
    check(".toolbar{position:static" in stylesheet, "Explore selectors must not stay pinned over maps on phones")
    check(".compare-toolbar{position:static" in stylesheet, "Compare options must not stay pinned over maps on phones")
    check(".compare-toolbar.is-collapsed .compare-control-fields{display:none}" in stylesheet, "mobile Compare options must collapse to expose the maps")
    check("runs[0]" not in page[page.index("function preferredRun"):page.index("function selectedRun")], "failed history must not be used as a default fallback")
    overview_block = page[page.index("function renderOverview()"):page.index("function compareEmpty")]
    check("states.length" not in overview_block, "coverage denominator must exclude intentional unsupported and quarantined surfaces")
    check("applicable.length" in overview_block, "coverage denominator must count only supported surfaces")
    compare_period_block = page[page.index("function comparePeriodOptions"):page.index("function compareBaselineOptions")]
    check("const keys = new Set();" in compare_period_block, "compare periods must collect a union of published months")
    check("COMPARE_MODELS.forEach" in compare_period_block and "keys.add(key)" in compare_period_block, "compare periods must include months published by any compared model")
    check("availableSets.every" not in compare_period_block, "individual compare months must not be hidden by an all-model intersection")
    compare_baseline_block = page[page.index("function compareBaselineOptions"):page.index("function compareRunForTarget")]
    check("productKey === DEFAULT_COMPARE_PRODUCT" in compare_baseline_block, "common-reference comparison must remain limited to 500-mb height")
    compare_card_block = page[page.index("function renderCompareCard"):page.index("function renderCompare()")]
    check("image.src = thumbnailPath(asset.image)" in compare_card_block, "Compare cards must load compact WebP thumbnails")
    check("openMapDialog(fullImage, image.alt)" in compare_card_block, "Compare lightbox must retain the full-resolution image")

    check("WN2 /" not in page, "unified dashboard should use a generic dashboard title")
    check("WeatherNext 2" not in page, "seasonal dashboard should not present WeatherNext 2 as a seasonal model")
    check("weathernext:" not in page, "seasonal dashboard should not register a WeatherNext model selector")
    check("commonComplete ? 'common_1991_2020' : 'native'" not in page, "comparison should default to native model maps")
    check('id="availability"' not in page, "dashboard should not show the manifest availability sentence")
    check("model manifests available" not in page, "dashboard should not show the manifest availability sentence")
    check("'model_spread': 'Model Spread'" not in page, "dashboard must not expose retired NMME model spread")
    check('id="map-title"' not in page, "map card should not duplicate titles already rendered in the image")
    check('id="run-status"' not in page, "map card should not duplicate image status in a header badge")
    for term in (
        "name: Publish Seasonal Model Dashboard Pages",
        "WeatherNext Runner",
        "WeatherNext Runner Custom",
        "C3S Multi-System Seasonal Graphics",
        "CMA CPSv3 Seasonal Graphics",
        "JMA Seasonal Graphics",
        "APCC MME Seasonal Graphics",
        "NASA GEOS-S2S-3 Seasonal Graphics",
        "NOAA NMME Seasonal Graphics",
        "Deduplicated Seasonal Super Ensemble",
        "name: Checkout dashboard source",
        "ref: main",
        "public/seasonal/index.html",
        "cp dashboard-source/public/seasonal/index.html site/seasonal/index.html",
        "public/seasonal/dashboard.css",
        "public/seasonal/dashboard.js",
        "cp dashboard-source/public/seasonal/dashboard.css site/seasonal/dashboard.css",
        "cp dashboard-source/public/seasonal/dashboard.js site/seasonal/dashboard.js",
        "scripts/build_seasonal_thumbnails.py",
        "scripts/seasonal_products.py",
        "scripts/build_seasonal_catalog.py",
        "name: Validate seasonal manifests and build catalog",
        "--strict",
        "--source-revision \"${GITHUB_SHA}\"",
        "name: Build seasonal Compare thumbnails",
        "--site-root site --max-width 560 --quality 82",
        "pip install --disable-pip-version-check --quiet Pillow",
    ):
        check(term in workflow, f"Pages workflow missing dashboard term: {term}")

    check(
        workflow.index("name: Validate seasonal manifests and build catalog")
        < workflow.index("name: Build seasonal Compare thumbnails")
        < workflow.index("name: Publish the merged Pages tree"),
        "catalog validation must block thumbnail generation and publication",
    )

    print("SEASONAL DASHBOARD CONTRACT OK: canonical catalog, supported-surface coverage, selectors, and guarded Pages publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
