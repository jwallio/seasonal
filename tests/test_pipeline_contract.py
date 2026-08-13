#!/usr/bin/env python3
"""Static contract checks that do not initialize Earth Engine."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
WORKFLOWS = "\n".join(
    (ROOT / path).read_text(encoding="utf-8")
    for path in (".github/workflows/runner.yml", ".github/workflows/update.yml")
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parameter_keys = {
        "conus_wind10",
        "conus_t850",
        "conus_t500",
        "conus_omega500",
        "conus_sst",
    }
    for key in sorted(parameter_keys):
        check(f"'{key}'" in MAIN, f"missing parameter product in main.py: {key}")
        check(key in WORKFLOWS, f"missing parameter product in workflow inputs: {key}")

    for mode in ("first", "mean", "median", "member"):
        check(mode in MAIN, f"missing ensemble mode: {mode}")
    for env_name in ("WN2_ENSEMBLE_MODE", "WN2_ENSEMBLE_MEMBER", "WN2_Z500_STYLE"):
        check(env_name in MAIN and env_name in WORKFLOWS, f"missing runtime control: {env_name}")

    check("Z500_CLASSIC_MIN_M = -140" in MAIN, "classic anomaly lower bound changed")
    check("Z500_CLASSIC_MAX_M = 140" in MAIN, "classic anomaly upper bound changed")
    check("Z500_CLASSIC_PALETTE" in MAIN, "classic anomaly palette missing")
    check("select_ensemble_image" in MAIN, "explicit ensemble selector missing")
    check("ensemble_mode" in MAIN and "z500_style" in MAIN, "manifest provenance fields missing")

    seasonal_doc = ROOT / "docs/SEASONAL_CFSV2.md"
    check(seasonal_doc.exists(), "seasonal CFSv2 contract document missing")
    seasonal_text = seasonal_doc.read_text(encoding="utf-8")
    for term in ("forecast_hour", "500-mb", "GRIB2", "baseline", "lead_month"):
        check(term in seasonal_text, f"seasonal contract missing term: {term}")

    print("PIPELINE CONTRACT OK: parameter products, ensemble controls, classic style, and seasonal boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
