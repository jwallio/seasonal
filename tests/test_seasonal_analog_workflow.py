#!/usr/bin/env python3
"""Static contract checks for the local-archive analog workflow handoff."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "seasonal-analogs.yml"
PUBLISHER = ROOT / ".github" / "workflows" / "publish-pages.yml"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(WORKFLOW.exists(), "seasonal analog workflow is missing")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publisher = PUBLISHER.read_text(encoding="utf-8")
    for term in (
        "Seasonal 500-mb Pattern Analogs",
        "CFSv2 Rolling Seasonal Graphics",
        "Deduplicated Seasonal Super Ensemble",
        "schedule:",
        '35 2,14 * * *',
        "self-hosted",
        "wn2-analogwx",
        "ANALOGWX_DATA_ROOT",
        "D:\\analogwx\\data",
        "era5_proc\\z500_anom.zarr",
        "cfsv2-pages-",
        "superensemble-pages-",
        "seasonal_analogs.py",
        "analog_z500_manifest.json",
        "build_analog_products.py",
        "analog_products_manifest.json",
        "analog_products",
        "Analog manifest generation failed",
        "analog-pages-",
        "Check scheduled source freshness",
        "needs_build=",
        "steps.freshness.outputs.needs_build",
        "site\\seasonal\\cfsv2_manifest.json",
        "site\\seasonal\\superensemble_manifest.json",
    ):
        check(term in workflow, f"analog workflow is missing term: {term}")
    for term in (
        "Seasonal 500-mb Pattern Analogs",
        "analog-pages-",
        "incoming/analogs",
        "seasonal/analog_z500_manifest.json",
        "seasonal/analog_products_manifest.json",
        "seasonal/analog_products",
    ):
        check(term in publisher, f"Pages publisher is missing analog term: {term}")
    check("cancel-in-progress: false" in workflow, "analog builds must not cancel a prior archive read")
    check("timeout-minutes: 120" in workflow, "analog builds must allow slow MRCC map generation to finish")
    check("github.event_name == 'schedule'" in workflow, "analog workflow must reconcile scheduled source updates")
    check("github.event_name != 'schedule'" in workflow, "scheduled freshness checks must not affect source-triggered builds")
    check("site\\seasonal\\analog_products\\*" in workflow, "analog payload must copy product contents without an extra directory level")
    check("previous Pages artifact" in workflow, "workflow must document retained-artifact fallback")
    print("SEASONAL ANALOG WORKFLOW OK: self-hosted archive access, source handoff, and serialized Pages publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

