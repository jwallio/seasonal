#!/usr/bin/env python3
"""Static NOAA NMME adapter, workflow, and dashboard contracts."""

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "nmme_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nmme.yml"
PAGES = ROOT / ".github" / "workflows" / "publish-pages.yml"
PAGE = ROOT / "public" / "seasonal" / "nmme" / "index.html"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("nmme_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load NMME adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGES, PAGE):
        check(path.exists(), f"missing NMME contract file: {path.name}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    for term in (
        "realtime_anom", "prob/netcdf", "tmp2m", "prate", "z200",
        "prob_above", "prob_norm", "prob_below", "multi_model_consensus", "RETIRED_PRODUCTS",
        "netCDF4", "probability-period", "retain-cycles", "components", "CPC NMME",
    ):
        check(term in adapter or term in workflow or term in pages, f"missing NMME term: {term}")
    module = load_adapter()
    check(module.target_month("2026080800", 1) == "202609", "NMME public lead 1 should be one month after initialization")
    check(module.target_month("2026080800", 4) == "202612", "NMME public lead 4 should align with the shared December target")
    check(module.target_month("2026080800", 6) == "202702", "NMME lead conversion should cross the year boundary")
    temperature_spec = module.BASE_PRODUCTS["2m_temperature_anomaly"]
    check((temperature_spec["min"], temperature_spec["max"]) == (-7.0, 7.0), "NMME 2-m temperature should use the shared ±7 °C range")
    check(temperature_spec["ticks"] == list(range(-7, 8)), "NMME 2-m temperature should use 1 °C labelled bounds")
    check(len(temperature_spec["ticks"]) == len(temperature_spec["palette"]) + 1, "NMME 2-m temperature bounds must align with colors")
    for product in ("probability_above_normal", "probability_near_normal", "probability_below_normal", "multi_model_consensus"):
        base = module.spec_for(product, "2m_temperature_anomaly")
        check(len(base["anomaly_ticks"]) == len(base["anomaly_palette"]) + 1, f"NMME {product} color bounds must align with swatches")
    above = module.spec_for("probability_above_normal", "2m_temperature_anomaly")
    near = module.spec_for("probability_near_normal", "2m_temperature_anomaly")
    below = module.spec_for("probability_below_normal", "2m_temperature_anomaly")
    check("(°C)" not in above["title"] and above["title"].endswith("(%)"), "NMME probability title must use percent rather than temperature units")
    check(len({tuple(above["anomaly_palette"]), tuple(near["anomaly_palette"]), tuple(below["anomaly_palette"])}) == 3, "NMME probability categories need distinct semantic palettes")
    triplet = {
        "prob_above": module.Grid([0.0, 1.0], [0.0], [[20.0, 60.0]]),
        "prob_norm": module.Grid([0.0, 1.0], [0.0], [[30.0, 25.0]]),
        "prob_below": module.Grid([0.0, 1.0], [0.0], [[50.0, 15.0]]),
    }
    integrity = module.probability_triplet_check(triplet)
    check(integrity["maximum_sum_error_percent"] == 0.0, "valid NMME category probabilities should pass the sum check")
    triplet["prob_below"] = module.Grid([0.0, 1.0], [0.0], [[40.0, 15.0]])
    try:
        module.probability_triplet_check(triplet)
    except module.NMMEError:
        pass
    else:
        raise AssertionError("NMME probability triplets that do not sum to 100 must fail closed")
    check("model_spread" in module.RETIRED_PRODUCTS, "retired NMME spread product must be purged from retained manifests")
    check("model_spread" not in workflow, "NMME workflow must not schedule or expose model spread")
    check("model_spread" not in pages, "Pages workflow must not expose model spread")
    print("NMME CONTRACT OK: aligned public leads, validated category triplets, semantic palettes, official feeds, and workflow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
