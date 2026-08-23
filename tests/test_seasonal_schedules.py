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
    ".github/workflows/seas5.yml": "30 15 5 * *",
    ".github/workflows/c3s.yml": "30 15 10 * *",
    ".github/workflows/jma.yml": "30 15 10 * *",
    ".github/workflows/superensemble.yml": "30 20 22 * *",
}

SCHEDULED_SUITES = {
    ".github/workflows/c3s.yml": ("SCHEDULED_C3S_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "sea_surface_temperature_anomaly", "mslp_anomaly"}),
    ".github/workflows/jma.yml": ("SCHEDULED_JMA_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "sea_surface_temperature_anomaly", "mslp_anomaly"}),
    ".github/workflows/seas5.yml": ("SCHEDULED_SEAS5_PRODUCTS", {"500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "sst_anomaly", "mslp_anomaly"}),
}


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

    for relative_path, (variable, expected_products) in SCHEDULED_SUITES.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        match = re.search(rf"^\s*{variable}:\s*([^\n]+)$", text, re.MULTILINE)
        check(match is not None, f"{relative_path} is missing {variable}")
        check(set(match.group(1).strip().split(",")) == expected_products, f"{relative_path} scheduled suite is incomplete")
        check('github.event_name }}" == "schedule"' in text, f"{relative_path} must distinguish scheduled suites from manual product reruns")
        check('for product in "${products[@]}"' in text, f"{relative_path} must render every scheduled product")

    super_workflow = (ROOT / ".github/workflows/superensemble.yml").read_text(encoding="utf-8")
    check("SCHEDULED_SUPER_PRODUCT: all" in super_workflow, "scheduled super ensemble must render all supported parameters")
    check('product="$SCHEDULED_SUPER_PRODUCT"' in super_workflow, "scheduled super ensemble must select all-product mode")

    doc = (ROOT / "docs/SEASONAL_SCHEDULES.md").read_text(encoding="utf-8")
    for term in ("UTC", "ECMWF SEAS5", "CMA CPSv3", "C3S multi-system", "JMA / MRI-CPS4", "Deduplicated super ensemble", "cancel-in-progress: false"):
        check(term in doc, f"schedule documentation missing {term}")

    check("22nd of each month at 20:30" in doc, "super-ensemble documentation must reflect its post-CMA schedule")
    check("full advertised anomaly suite" in doc, "schedule documentation must describe the multi-product refresh contract")

    print("SEASONAL SCHEDULE CONTRACT OK: release-aligned UTC workflows, full scheduled suites, and documentation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
