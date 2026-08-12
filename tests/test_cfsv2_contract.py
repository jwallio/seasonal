#!/usr/bin/env python3
"""Static CFSv2 contract checks that do not require network or plotting libraries."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "cfsv2_seasonal.py"
WRAPPER = ROOT / "scripts" / "render_cfsv2.ps1"
DOC = ROOT / "docs" / "SEASONAL_CFSV2.md"
WORKFLOW = ROOT / ".github" / "workflows" / "cfsv2.yml"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(ADAPTER.exists(), "CFSv2 adapter missing")
    check(WRAPPER.exists(), "CFSv2 PowerShell wrapper missing")
    check(DOC.exists(), "CFSv2 documentation missing")
    check(WORKFLOW.exists(), "CFSv2 workflow missing")

    adapter = ADAPTER.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
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
        "z500_anomaly",
        "--baseline-file",
        "--baseline-dir",
        "--ncei-calibration",
        "--seasonal-window",
        "--absolute",
        "cfsv2_manifest",
        "seasonal mean",
        '"status"',
    ):
        check(term in adapter, f"adapter missing contract term: {term}")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for term in ("baseline", "reforecast", "monthly_grib_01", "lead_month", "GRIB2", "rolling", "NOMADS"):
        check(term in documentation, f"documentation missing contract term: {term}")
    for term in ("rolling-days", "rolling-state-dir", "actions/cache", "--ncei-calibration"):
        check(term in workflow, f"workflow missing contract term: {term}")

    print("CFSV2 CONTRACT OK: NOMADS source, HGT500 decode, baseline gate, manifest, wrapper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
