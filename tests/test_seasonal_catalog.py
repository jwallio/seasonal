#!/usr/bin/env python3
"""Canonical seasonal product, numerical-QC, and catalog contracts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_seasonal_catalog import build_catalog, validate_manifest  # noqa: E402
from seasonal_products import grid_quality_control, issue_codes, require_quality_control  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def target(
    *,
    product: str = "500mb_height_anomaly",
    field: str = "z500_anomaly",
    units: str = "m",
    image: str = "seasonal/cfsv2/map.jpg",
) -> dict:
    return {
        "id": f"target-{product}",
        "target_month": "202612-202702",
        "valid_start_utc": "2026-12-01T00:00:00Z",
        "valid_end_utc": "2027-03-01T00:00:00Z",
        "lead_month": "4-6",
        "field": field,
        "units": units,
        "status": "rendered",
        "image": image,
        "quality_control": {
            "registry_version": 1,
            "status": "passed",
            "product": product,
        },
    }


def manifest(product: str = "500mb_height_anomaly", target_value: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "kind": "cfsv2_seasonal",
        "generated_utc": "2026-08-23T12:00:00Z",
        "runs": [{
            "id": f"cfsv2-{product}",
            "init_utc": "2026-08-23T00:00:00Z",
            "product": product,
            "status": "rendered",
            "targets": [target_value or target(product=product)],
        }],
    }


def main() -> int:
    passed = grid_quality_control(
        "2m_temperature_anomaly",
        np.array([[-2.0, 0.0], [1.5, 3.0]]),
        units="°C",
        field="t2m_anomaly",
    )
    check(passed["status"] == "passed", "ordinary temperature anomalies should pass numerical QC")

    clipping = grid_quality_control(
        "2m_temperature_anomaly",
        np.array([0.0] * 19 + [8.0]),
        units="°C",
        field="t2m_anomaly",
    )
    check(clipping["status"] == "warning", "limited display-scale clipping should be reported as a warning")
    check("display_scale_clipping" in issue_codes(clipping["issues"]), "display clipping warning code is missing")

    rejected = grid_quality_control(
        "2m_temperature_anomaly",
        np.array([60.0, 61.0]),
        units="°C",
        field="t2m_anomaly",
    )
    check(rejected["status"] == "failed", "physically implausible temperature anomalies must fail QC")
    try:
        require_quality_control(rejected)
    except ValueError:
        pass
    else:
        raise AssertionError("failed numerical QC must stop a provider render")

    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        image = site / "seasonal" / "cfsv2" / "map.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"test-image")
        manifest_path = site / "seasonal" / "cfsv2_manifest.json"
        manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")

        catalog = build_catalog(
            site,
            model_keys=["cfsv2"],
            generated_utc="2026-08-23T12:00:00Z",
            source_revision="test-revision",
        )
        summary = catalog["summary"]
        check(summary["models_online"] == 1, "valid model should be online")
        check(summary["supported_surfaces"] == 4, "CFSv2 should have four supported core surfaces")
        check(summary["available_surfaces"] == 1, "one synthetic CFSv2 surface should be available")
        check(summary["intentional_unavailable_surfaces"] == 2, "CFSv2 t850 and SST should be intentional N/A")
        check(catalog["models"]["cfsv2"]["surfaces"]["500mb_height_anomaly"]["available"], "z500 surface should be available")

        image.unlink()
        _, validation = validate_manifest("cfsv2", manifest(), site_root=site, check_assets=True)
        check("asset_missing" in validation["issue_codes"], "missing rendered assets must fail catalog validation")

        wrong_level = manifest(target_value=target(field="z200_anomaly"))
        _, validation = validate_manifest("cfsv2", wrong_level, site_root=site, check_assets=False)
        check("forbidden_field_identity" in validation["issue_codes"], "z200 data must not pass as z500")

        probability_target = target(
            product="probability_above_normal",
            field="above_normal_probability",
            units="%",
        )
        probability_target["probability_integrity"] = {"maximum_sum_error_percent": 1.0}
        _, validation = validate_manifest(
            "nmme",
            manifest("probability_above_normal", probability_target),
            site_root=site,
            check_assets=False,
        )
        check("probability_sum_mismatch" in validation["issue_codes"], "invalid probability sums must be rejected")

        legacy_precip = target(product="precipitation_anomaly", field="precipitation_anomaly", units="mm")
        runs, validation = validate_manifest(
            "apcc",
            manifest("precipitation_anomaly", legacy_precip),
            site_root=site,
            check_assets=False,
        )
        check("noncanonical_units" in validation["issue_codes"], "legacy APCC millimetres should be identified")
        check(runs[0]["_catalog"]["comparable"] is False, "legacy-unit maps must not enter same-scale comparison")

    print("SEASONAL CATALOG CONTRACT OK: support matrix, metadata, assets, probability integrity, and numerical QC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
