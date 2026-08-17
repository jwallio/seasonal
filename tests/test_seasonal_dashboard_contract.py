#!/usr/bin/env python3
"""Static contract checks for the unified seasonal model dashboard."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "public" / "seasonal" / "index.html"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pages.yml"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(PAGE.exists(), "unified seasonal dashboard is missing")
    check(PUBLISH_WORKFLOW.exists(), "central Pages workflow is missing")
    page = PAGE.read_text(encoding="utf-8")
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    for term in (
        "<title>Seasonal Model Dashboard</title>",
        "<h1>Seasonal Model Dashboard</h1>",
        "CFSv2",
        "ECMWF SEAS5",
        "CanSIPS v3",
        "C3S multi-system",
        "JMA",
        "APCC MME",
        "NASA GEOS-S2S-3",
        "NOAA NMME",
        "seasonal/cfsv2_manifest.json",
        "seasonal/seas5_manifest.json",
        "seasonal/cansips_manifest.json",
        "seasonal/c3s_manifest.json",
        "seasonal/jma_manifest.json",
        "seasonal/apcc_manifest.json",
        "seasonal/geos_s2s3_manifest.json",
        "seasonal/nmme_manifest.json",
        'id="model-select"',
        'id="product-select"',
        'id="run-select"',
        'id="compare-tab"',
        'id="compare-target-select"',
        'id="compare-baseline-select"',
        'id="compare-grid"',
        "COMPARE_PRODUCT = '500mb_height_anomaly'",
        "common_1991_2020",
        "function renderCompare()",
        "if (!selection.compareBaseline) selection.compareBaseline = 'native';",
        "product_hours",
        "target_month",
        "source_url",
        "preferredTarget",
        "run.model || run.component_label || model.label",
        "function isFailedRun(run)",
        "function preferredRun(runs)",
        "usable.find(run => String(run.component || '') === 'multisystem')",
        "probability_above_normal",
        "multi_model_consensus",
    ):
        check(term in page, f"unified dashboard missing term: {term}")

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
        "JMA Seasonal Graphics",
        "APCC MME Seasonal Graphics",
        "NASA GEOS-S2S-3 Seasonal Graphics",
        "NOAA NMME Seasonal Graphics",
        "name: Checkout dashboard source",
        "ref: main",
        "public/seasonal/index.html",
        "cp dashboard-source/public/seasonal/index.html site/seasonal/index.html",
    ):
        check(term in workflow, f"Pages workflow missing dashboard term: {term}")

    print("SEASONAL DASHBOARD CONTRACT OK: unified model selectors, manifests, direct links, and Pages publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
