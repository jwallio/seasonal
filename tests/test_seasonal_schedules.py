#!/usr/bin/env python3
"""Static checks for provider-aligned seasonal GitHub Actions schedules."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ".github/workflows/cfsv2.yml": "35 10,22 * * *",
    ".github/workflows/cansips.yml": "30 16 2 * *",
    ".github/workflows/nmme.yml": "30 15 9 * *",
    ".github/workflows/seas5.yml": "30 15 5 * *",
    ".github/workflows/c3s.yml": "30 15 10 * *",
    ".github/workflows/jma.yml": "30 15 10 * *",
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

    doc = (ROOT / "docs/SEASONAL_SCHEDULES.md").read_text(encoding="utf-8")
    for term in ("UTC", "ECMWF SEAS5", "C3S multi-system", "JMA / MRI-CPS4", "cancel-in-progress: false"):
        check(term in doc, f"schedule documentation missing {term}")

    print("SEASONAL SCHEDULE CONTRACT OK: release-aligned UTC workflows and documentation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
