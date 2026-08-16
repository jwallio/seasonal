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
        "prob_above", "prob_norm", "prob_below", "model_spread", "multi_model_consensus",
        "netCDF4", "probability-period", "retain-cycles", "components", "CPC NMME",
    ):
        check(term in adapter or term in workflow or term in pages, f"missing NMME term: {term}")
    module = load_adapter()
    check(module.target_month("2026080800", 1) == "202608", "NMME lead 1 should represent the initialization month in the CPC file")
    check(module.target_month("2026080800", 6) == "202701", "NMME lead conversion should cross the year boundary")
    for product in ("probability_above_normal", "probability_near_normal", "probability_below_normal", "model_spread", "multi_model_consensus"):
        base = module.spec_for(product, "2m_temperature_anomaly")
        check(len(base["anomaly_ticks"]) == len(base["anomaly_palette"]) + 1, f"NMME {product} color bounds must align with swatches")
    print("NMME CONTRACT OK: official anomaly/probability feeds, derived spread/consensus, workflow, and viewer paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
