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
        "WeatherNext 2",
        "CFSv2",
        "ECMWF SEAS5",
        "runs_manifest.json",
        "seasonal/cfsv2_manifest.json",
        "seasonal/seas5_manifest.json",
        'id="model-select"',
        'id="product-select"',
        'id="run-select"',
        "product_hours",
        "target_month",
        "source_url",
        "preferredTarget",
    ):
        check(term in page, f"unified dashboard missing term: {term}")

    check("WN2 /" not in page, "unified dashboard should use a generic dashboard title")
    check('id="availability"' not in page, "dashboard should not show the manifest availability sentence")
    check("model manifests available" not in page, "dashboard should not show the manifest availability sentence")
    check('id="map-title"' not in page, "map card should not duplicate titles already rendered in the image")
    check('id="run-status"' not in page, "map card should not duplicate image status in a header badge")
    for term in (
        "name: Publish Seasonal Model Dashboard Pages",
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
