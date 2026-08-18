#!/usr/bin/env python3
"""Static and unit contracts for the deduplicated seasonal super ensemble."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


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
    t850_members = module.canonical_members("850mb_temperature_anomaly")
    height_keys = [member.key for member in height_members]
    surface_keys = [member.key for member in surface_members]
    t850_keys = [member.key for member in t850_members]
    check(len(height_keys) == 9 and len(height_keys) == len(set(height_keys)), "height roster must contain nine unique source families")
    check(len(surface_keys) == 12 and len(surface_keys) == len(set(surface_keys)), "surface roster must add GEOS and only two unique NMME components")
    check(len(t850_keys) == 10 and len(t850_keys) == len(set(t850_keys)), "850-mb roster must add the standalone GEOS family")
    check("c3s_eccc_system5" not in height_keys, "C3S ECCC must not duplicate CanSIPS")
    check("c3s_jma_system4" in height_keys, "JMA should be represented once through C3S")
    check("eccc_cansips_v3" in height_keys, "CanSIPS should represent the ECCC family once")
    check(module.CFSV2_MEMBER_KEY in height_keys, "500-mb roster should use the standalone rolling CFSv2 family")
    check("c3s_ncep_system2" not in height_keys, "C3S NCEP must not duplicate standalone rolling CFSv2")
    check(module.CFSV2_MEMBER_KEY in surface_keys and "c3s_ncep_system2" not in surface_keys, "surface roster should contain one rolling CFSv2 family vote")
    check("c3s_ncep_system2" in t850_keys and module.CFSV2_MEMBER_KEY not in t850_keys, "unsupported standalone parameters should retain one C3S NCEP family vote")
    check(module.GEOS_MEMBER_KEY not in height_keys, "GEOS must stay out of 500-mb height until its source passes the pressure check")
    check(module.GEOS_MEMBER_KEY in surface_keys and module.GEOS_MEMBER_KEY in t850_keys, "validated products should include one standalone GEOS family vote")
    check("nmme_nasa_geos5v2" not in surface_keys, "the older NMME NASA copy must not double-count GEOS")
    check(set(surface_keys) - set(height_keys) == {module.GEOS_MEMBER_KEY, "nmme_ncar_ccsm4", "nmme_ncar_cesm1"}, "surface extensions must be GEOS plus the two unique NCAR NMME systems")
    check(abs(sum(item["weight"] for item in module.weights_for(height_keys, {member.key: member for member in height_members})) - 1.0) < 1e-8, "equal weights should sum to one")
    check(module.resolve_cfsv2_anchor("2026081818", "2026080100") == "2026081818", "CFSv2 anchor should align within the shared initialization month")
    try:
        module.resolve_cfsv2_anchor("2026073118", "2026080100")
    except module.SuperEnsembleError:
        pass
    else:
        raise AssertionError("CFSv2 anchor must not silently drift into another initialization month")
    height_exclusions = module.membership_ledger("500mb_height_anomaly")["excluded"]
    check(any(item["package"] == "C3S NCEP System 2" and item["represented_by"] == module.CFSV2_MEMBER_KEY for item in height_exclusions), "height ledger must document the C3S NCEP substitution")
    check(any(item["package"] == "NASA GEOS-S2S-3 APCN z500 archive" and item["represented_by"] is None for item in height_exclusions), "height ledger must document the rejected NASA pressure level")
    surface_exclusions = module.membership_ledger("2m_temperature_anomaly")["excluded"]
    check(any(item["package"] == "NMME NASA_GEOS5v2" and item["represented_by"] == module.GEOS_MEMBER_KEY for item in surface_exclusions), "surface ledger must document the NASA deduplication")
    check(module.c3s.target_month("2026080100", 4) == "202612", "lead 4 should align to December")
    check(module.nmme.target_month("2026080800", 5) == "202612", "NMME lead alignment should match December")
    for term in ("intersection of canonical members", "APCC MME", "C3S multi-system mean", "NMME CFSv2", "native_model_baselines", "NASA GEOS-S2S-3"):
        check(term in adapter_text, f"adapter is missing deduplication term: {term}")
    for term in ("name: Deduplicated Seasonal Super Ensemble", "CDS_API_KEY", "Restore rolling CFSv2 state", "Restore NASA GEOS-S2S-3 numerical cache", "--geos-cache-dir", "cfsv2-rolling-", "superensemble-pages-", "30 18 16 * *"):
        check(term in workflow, f"workflow is missing term: {term}")
    for term in ("Deduplicated Seasonal Super Ensemble", "superensemble_manifest.json", "incoming/superensemble"):
        check(term in pages, f"Pages publisher is missing term: {term}")

    with tempfile.TemporaryDirectory() as temporary:
        originals = {
            name: getattr(module.cfsv2, name)
            for name in (
                "get_product_spec", "rolling_cycle_inits", "decode_target_ensemble",
                "ncei_calibration_url", "cached_calibration_path", "download_file", "load_baseline",
            )
        }
        try:
            module.cfsv2.get_product_spec = lambda product: {
                "source_kind": "pgbf", "height_contours": True, "baseline_label": "test NCEI baseline"
            }
            module.cfsv2.rolling_cycle_inits = lambda anchor, count: ["2026081812"] * count
            module.cfsv2.decode_target_ensemble = lambda *args, **kwargs: (
                module.Grid([0.0], [0.0], [[100.0]]), [], 39, 40, "39/40-cycle rolling mean", 0.0
            )
            module.cfsv2.ncei_calibration_url = lambda *args: "https://example.test/baseline.grb2"
            module.cfsv2.cached_calibration_path = lambda *args: Path(temporary) / "baseline.grb2"
            module.cfsv2.download_file = lambda *args, **kwargs: (False, 0.0)
            module.cfsv2.load_baseline = lambda *args, **kwargs: module.Grid([0.0], [0.0], [[10.0]])
            grids = {4: {}}
            heights = {4: {}}
            provenance = {4: {}}
            errors = {4: {}}
            module.load_cfsv2_member(
                args=SimpleNamespace(
                    cfsv2_anchor_init="2026081812", cfsv2_rolling_days=10,
                    cfsv2_rolling_member=1, request_delay=0.0,
                    force_decode=False, decode_only=False,
                ),
                product="500mb_height_anomaly", init="2026080100", leads=[4],
                cache_dir=Path(temporary), state_dir=Path(temporary) / "rolling",
                root=ROOT, wgrib2="wgrib2", member_grids=grids,
                height_grids=heights, provenance=provenance, errors=errors,
            )
            check(grids[4][module.CFSV2_MEMBER_KEY].values == [[90.0]], "rolling CFSv2 anomaly should subtract its NCEI baseline")
            check(heights[4][module.CFSV2_MEMBER_KEY].values == [[100.0]], "rolling CFSv2 absolute height should supply contours")
            check(provenance[4][module.CFSV2_MEMBER_KEY]["rolling_window"]["available_cycles"] == 39, "rolling CFSv2 provenance should retain partial-cycle counts")
            check(not errors[4], "mock rolling CFSv2 load should not record an error")
        finally:
            for name, value in originals.items():
                setattr(module.cfsv2, name, value)

    original_geos_loader = module.geos.load_anomaly_bundle
    try:
        module.geos.load_anomaly_bundle = lambda **kwargs: {
            4: SimpleNamespace(
                anomaly=module.Grid([0.0], [0.0], [[2.5]]),
                archive_url="https://example.test/geos.tar.xz",
                source_files=("member.nc4",),
                members=("member-1", "member-2"),
                init_dates=("20260730",),
                drift_years=(2001, 2021),
                drift_url="https://example.test/geos-drift.nc4",
            )
        }
        grids = {4: {}}
        provenance = {4: {}}
        errors = {4: {}}
        module.load_geos_member(
            args=SimpleNamespace(request_delay=0.0),
            product="2m_temperature_anomaly",
            init="2026080100",
            leads=[4],
            cache_dir=ROOT,
            border_paths=[],
            member_grids=grids,
            provenance=provenance,
            errors=errors,
        )
        check(grids[4][module.GEOS_MEMBER_KEY].values == [[2.5]], "GEOS anomaly should enter the canonical member grid")
        check(provenance[4][module.GEOS_MEMBER_KEY]["internal_members"] == 2, "GEOS provenance should retain its member count")
        check(not errors[4], "mock GEOS load should not record an error")
    finally:
        module.geos.load_anomaly_bundle = original_geos_loader

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
