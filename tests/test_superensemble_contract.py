#!/usr/bin/env python3
"""Static and unit contracts for the deduplicated seasonal super ensemble."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "superensemble_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "superensemble.yml"
PAGES = ROOT / ".github" / "workflows" / "publish-pages.yml"
PAGE = ROOT / "public" / "seasonal" / "superensemble" / "index.html"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("superensemble_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load super-ensemble adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGES, PAGE):
        check(path.exists(), f"missing super-ensemble contract file: {path.name}")
    adapter_text = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    module = load_adapter()

    height_members = module.canonical_members("500mb_height_anomaly")
    surface_members = module.canonical_members("2m_temperature_anomaly")
    height_keys = [member.key for member in height_members]
    surface_keys = [member.key for member in surface_members]
    check(len(height_keys) == 9 and len(height_keys) == len(set(height_keys)), "height roster must contain nine unique source families")
    check(len(surface_keys) == 12 and len(surface_keys) == len(set(surface_keys)), "surface roster must add only three unique NMME components")
    check("c3s_eccc_system5" not in height_keys, "C3S ECCC must not duplicate CanSIPS")
    check("c3s_jma_system4" in height_keys, "JMA should be represented once through C3S")
    check("eccc_cansips_v3" in height_keys, "CanSIPS should represent the ECCC family once")
    check(not any("cfsv2" in key for key in surface_keys), "standalone/NMME CFSv2 must not be added twice")
    check(set(surface_keys) - set(height_keys) == {"nmme_nasa_geos5v2", "nmme_ncar_ccsm4", "nmme_ncar_cesm1"}, "only unique NMME systems may extend supported surface products")
    check(abs(sum(item["weight"] for item in module.weights_for(height_keys, {member.key: member for member in height_members})) - 1.0) < 1e-8, "equal weights should sum to one")
    check(module.c3s.target_month("2026080100", 4) == "202612", "lead 4 should align to December")
    check(module.nmme.target_month("2026080800", 5) == "202612", "NMME lead alignment should match December")
    for term in ("intersection of canonical members", "APCC MME", "C3S multi-system mean", "NMME CFSv2", "native_model_baselines"):
        check(term in adapter_text, f"adapter is missing deduplication term: {term}")
    for term in ("name: Deduplicated Seasonal Super Ensemble", "CDS_API_KEY", "superensemble-pages-", "30 18 16 * *"):
        check(term in workflow, f"workflow is missing term: {term}")
    for term in ("Deduplicated Seasonal Super Ensemble", "superensemble_manifest.json", "incoming/superensemble"):
        check(term in pages, f"Pages publisher is missing term: {term}")

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "manifest.json"
        previous = Path(temporary) / "previous.json"
        previous.write_text(json.dumps({"runs": [{"id": f"old-{month}", "init_utc": f"2025-{month:02d}-01T00:00:00Z"} for month in range(1, 5)]}), encoding="utf-8")
        module.write_manifest(output, [{"id": "current", "init_utc": "2026-08-01T00:00:00Z"}], previous, 4)
        payload = json.loads(output.read_text(encoding="utf-8"))
        check(len(payload["runs"]) == 4, "retention should keep the current cycle plus three prior cycles")
        check(payload["kind"] == "deduplicated_seasonal_superensemble_manifest", "manifest kind should identify the package")

    print("SUPER ENSEMBLE CONTRACT OK: unique membership, equal weights, aligned leads, workflow, Pages, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
