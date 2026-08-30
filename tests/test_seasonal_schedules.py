#!/usr/bin/env python3
"""Static checks for provider-aligned seasonal GitHub Actions schedules."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ".github/workflows/cfsv2.yml": "35 10,22 * * *",
    ".github/workflows/cansips.yml": "30 16 2 * *",
    ".github/workflows/cma-cpsv3.yml": "30 18 21 * *",
    ".github/workflows/nmme.yml": "30 15 9 * *",
    ".github/workflows/superensemble.yml": "30 20 22 * *",
}

RELEASE_CHECK_CRONS = {
    "*/15 12-18 6 * *",
    "17 * 7-9 * *",
    "*/15 12-18 10 * *",
    "17 * 11-12 * *",
    "17 12 13-31 * *",
}

SCHEDULED_SUITES = {
    ".github/workflows/c3s.yml": ("SCHEDULED_C3S_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "snowfall_anomaly", "sea_surface_temperature_anomaly", "mslp_anomaly"}),
    ".github/workflows/jma.yml": ("SCHEDULED_JMA_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "sea_surface_temperature_anomaly", "mslp_anomaly"}),
    ".github/workflows/seas5.yml": ("SCHEDULED_SEAS5_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "snowfall_anomaly", "sst_anomaly", "mslp_anomaly"}),
}

HISTORY_WORKFLOWS = (
    ".github/workflows/apcc.yml",
    ".github/workflows/c3s.yml",
    ".github/workflows/cansips.yml",
    ".github/workflows/cfsv2.yml",
    ".github/workflows/cma-cpsv3.yml",
    ".github/workflows/geos-s2s3.yml",
    ".github/workflows/jma.yml",
    ".github/workflows/nmme.yml",
    ".github/workflows/seas5.yml",
    ".github/workflows/superensemble.yml",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    for relative_path, expected_cron in EXPECTED.items():
        path = ROOT / relative_path
        check(path.exists(), f"missing scheduled workflow: {relative_path}")
        text = path.read_text(encoding="utf-8")
        crons = re.findall(r'^\s*- cron:\s*"([^"]+)"', text, re.MULTILINE)
        check(expected_cron in crons, f"{relative_path} is missing {expected_cron}")

    release_checker = ROOT / ".github/workflows/seasonal-release-check.yml"
    check(release_checker.exists(), "missing CDS seasonal release checker workflow")
    release_text = release_checker.read_text(encoding="utf-8")
    release_crons = set(re.findall(r'^\s*- cron:\s*"([^"]+)"', release_text, re.MULTILINE))
    check(release_crons == RELEASE_CHECK_CRONS, "CDS seasonal release checker polling windows changed unexpectedly")
    for term in (
        "scripts/check_seasonal_releases.py",
        "actions: write",
        "product=all",
        'init="$TARGET"',
        "Existing run is active",
        "Current suite is already live",
        "2700",
    ):
        check(term in release_text, f"seasonal release checker is missing its {term!r} contract")

    analog_workflow = ROOT / ".github/workflows/seasonal-analogs.yml"
    check(analog_workflow.exists(), "missing seasonal analog workflow")
    analog_crons = re.findall(r'^\s*- cron:\s*"([^"]+)"', analog_workflow.read_text(encoding="utf-8"), re.MULTILINE)
    check("35 2,14 * * *" in analog_crons, "seasonal analog workflow is missing its delayed reconciliation schedule")

    for relative_path, (variable, expected_products) in SCHEDULED_SUITES.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        match = re.search(rf"^\s*{variable}:\s*([^\n]+)$", text, re.MULTILINE)
        check(match is not None, f"{relative_path} is missing {variable}")
        check(set(match.group(1).strip().split(",")) == expected_products, f"{relative_path} scheduled suite is incomplete")
        check(re.search(r"^\s+- all$", text, re.MULTILINE) is not None, f"{relative_path} must expose full-suite dispatch mode")
        check('== "all"' in text, f"{relative_path} must distinguish full-suite from targeted reruns")
        check('for product in "${products[@]}"' in text, f"{relative_path} must render every scheduled product")
        check("cancel-in-progress: false" in text, f"{relative_path} must serialize full-suite refreshes")

    for relative_path in HISTORY_WORKFLOWS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        check('published_manifest=' in text, f"{relative_path} must name the retained manifest")
        check('temporary_manifest="${published_manifest}.tmp"' in text, f"{relative_path} must restore manifests through a temporary file")
        check(' -o "$temporary_manifest"' in text, f"{relative_path} must not truncate the retained manifest during download")
        check('mv "$temporary_manifest" "$published_manifest"' in text, f"{relative_path} must atomically replace a retained manifest after a successful download")
        check('rm -f "$temporary_manifest"' in text, f"{relative_path} must clean up a failed temporary download")
        check(not re.search(r"rm -f [^\n]*previous_manifest\.json", text), f"{relative_path} must not delete last-known-good history")
        check("retaining cached last-known-good history" in text, f"{relative_path} must report retained history when Pages is unavailable")

    super_workflow = (ROOT / ".github/workflows/superensemble.yml").read_text(encoding="utf-8")
    check("SCHEDULED_SUPER_PRODUCT: all" in super_workflow, "scheduled super ensemble must render all supported parameters")
    check('product="$SCHEDULED_SUPER_PRODUCT"' in super_workflow, "scheduled super ensemble must select all-product mode")

    doc = (ROOT / "docs/SEASONAL_SCHEDULES.md").read_text(encoding="utf-8")
    for term in ("UTC", "ECMWF SEAS5", "CMA CPSv3", "C3S multi-system", "JMA / MRI-CPS4", "Deduplicated super ensemble", "cancel-in-progress: false"):
        check(term in doc, f"schedule documentation missing {term}")

    check("22nd of each month at 20:30" in doc, "super-ensemble documentation must reflect its post-CMA schedule")
    check("full advertised anomaly suite" in doc, "schedule documentation must describe the multi-product refresh contract")
    check("two-hour readiness retry" in doc, "schedule documentation must describe the CFSv2 delayed readiness retry")
    check("scheduled reconciliation" in doc, "schedule documentation must describe the analog catch-up")

    print("SEASONAL SCHEDULE CONTRACT OK: release-aligned UTC workflows, full scheduled suites, and documentation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

