#!/usr/bin/env python3
"""Static and unit contracts for the deduplicated seasonal super ensemble."""

import importlib.util
import inspect
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

    height_spec = module.product_spec("500mb_height_anomaly", synthetic=True)
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "super-ensemble 500-mb maps should use the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "super-ensemble 500-mb maps should use 10-metre labelled bounds")
    northern_height = module.product_spec("500mb_height_anomaly_nh", synthetic=True)
    check(northern_height["projection"] == "north_polar_stereographic", "super-ensemble Northern Hemisphere 500-mb view must use the polar projection")
    for product in ("850mb_temperature_anomaly", "2m_temperature_anomaly"):
        temperature_spec = module.product_spec(product)
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"super-ensemble {product} should use the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"super-ensemble {product} should use 1 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"super-ensemble {product} bounds must align with colors")
    snowfall_spec = module.product_spec("snowfall_anomaly")
    check(snowfall_spec["region"] == module.c3s.CONUS_PRECIP_REGION, "super-ensemble snowfall must use the tight CONUS crop")
    check(snowfall_spec["map_domain"] == "land" and snowfall_spec["fit_frame_to_domain"], "super-ensemble snowfall must use the fitted lower-48 land frame")
    check(snowfall_spec["seasonal_reducer"] == "sum", "super-ensemble snowfall seasons must sum monthly departures")
    check((snowfall_spec["anomaly_min"], snowfall_spec["anomaly_max"]) == (-4.0, 4.0), "super-ensemble snowfall should use a nonlinear ±4.0 inch water-equivalent range")
    check(snowfall_spec["anomaly_ticks"] == module.c3s.SNOWFALL_ANOMALY_TICKS, "super-ensemble snowfall should use the approved nonlinear labelled breakpoints")
    check(snowfall_spec["anomaly_tick_format"] == "signed_trimmed" and snowfall_spec["anomaly_tick_decimals"] == 2, "super-ensemble snowfall labels should preserve quarter-inch breakpoints")
    check((snowfall_spec["monthly_anomaly_min"], snowfall_spec["monthly_anomaly_max"]) == (-2.0, 2.0), "super-ensemble monthly snowfall should use the tighter ±2.0 inch range")
    check(snowfall_spec["monthly_anomaly_endpoint_labels"] == {"minimum": "≤−2.0", "maximum": "≥+2.0"}, "super-ensemble monthly snowfall legend should mark clipped endpoints")
    check("(in LWE)" not in snowfall_spec["title"], "super-ensemble snowfall title must not use the obsolete in-LWE wording")
    check("sea_surface_temperature_anomaly" not in module.PRODUCTS, "super-ensemble product registry must not generate SST")
    check("sea_surface_temperature_anomaly" not in module.PRODUCT_LABELS, "super-ensemble manifest labels must not expose SST")
    mslp_spec = module.product_spec("mslp_anomaly")
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-10.0, 10.0), "super-ensemble MSLP should use ±10 hPa")
    height_members = module.canonical_members("500mb_height_anomaly")
    surface_members = module.canonical_members("2m_temperature_anomaly")
    t850_members = module.canonical_members("850mb_temperature_anomaly")
    near_height_members = module.canonical_members("500mb_height_anomaly", include_cma=True)
    near_surface_members = module.canonical_members("2m_temperature_anomaly", include_cma=True)
    height_keys = [member.key for member in height_members]
    surface_keys = [member.key for member in surface_members]
    t850_keys = [member.key for member in t850_members]
    near_height_keys = [member.key for member in near_height_members]
    near_surface_keys = [member.key for member in near_surface_members]
    check(len(height_keys) == 9 and len(height_keys) == len(set(height_keys)), "height roster must contain nine unique source families")
    check(len(surface_keys) == 12 and len(surface_keys) == len(set(surface_keys)), "surface roster must add GEOS and only two unique NMME components")
    check(len(t850_keys) == 10 and len(t850_keys) == len(set(t850_keys)), "850-mb roster must add the standalone GEOS family")
    check(set(near_height_keys) - set(height_keys) == {module.CMA_MEMBER_KEY}, "near-term height roster should add CMA exactly once")
    check(set(near_surface_keys) - set(surface_keys) == {module.CMA_MEMBER_KEY}, "near-term surface roster should add CMA exactly once")
    check(len(near_surface_keys) == len(set(near_surface_keys)), "near-term surface roster must remain deduplicated")
    check("c3s_eccc_system5" not in height_keys, "C3S ECCC must not duplicate CanSIPS")
    check("c3s_jma_system4" in height_keys, "JMA should be represented once through C3S")
    check("eccc_cansips_v3" in height_keys, "CanSIPS should represent the ECCC family once")
    snowfall_keys = [member.key for member in module.canonical_members("snowfall_anomaly")]
    check("eccc_cansips_v3" in snowfall_keys, "snowfall roster should include the CanSIPS-derived family vote")
    check(module.CFSV2_MEMBER_KEY in snowfall_keys, "snowfall roster should include the standalone rolling CFSv2 family vote")
    check(len(snowfall_keys) == 7 and len(snowfall_keys) == len(set(snowfall_keys)), "snowfall roster must contain seven supported unique source families")
    check("c3s_ncep_system2" not in snowfall_keys, "snowfall roster must not duplicate rolling CFSv2 through C3S NCEP")
    check("c3s_jma_system4" not in snowfall_keys and "c3s_bom_system2" not in snowfall_keys, "provider-unsupported C3S snowfall systems must not create permanent partial coverage")
    check(module.CFSV2_MEMBER_KEY in height_keys, "500-mb roster should use the standalone rolling CFSv2 family")
    check("c3s_ncep_system2" not in height_keys, "C3S NCEP must not duplicate standalone rolling CFSv2")
    check(module.CFSV2_MEMBER_KEY in surface_keys and "c3s_ncep_system2" not in surface_keys, "surface roster should contain one rolling CFSv2 family vote")
    check("c3s_ncep_system2" in t850_keys and module.CFSV2_MEMBER_KEY not in t850_keys, "unsupported standalone parameters should retain one C3S NCEP family vote")
    check(module.GEOS_MEMBER_KEY not in height_keys, "GEOS must stay out of 500-mb height until its source passes the pressure check")
    check(module.GEOS_MEMBER_KEY in surface_keys and module.GEOS_MEMBER_KEY in t850_keys, "validated products should include one standalone GEOS family vote")
    check("nmme_nasa_geos5v2" not in surface_keys, "the older NMME NASA copy must not double-count GEOS")
    check(set(surface_keys) - set(height_keys) == {module.GEOS_MEMBER_KEY, "nmme_ncar_ccsm4", "nmme_ncar_cesm1"}, "surface extensions must be GEOS plus the two unique NCAR NMME systems")
    height_definitions = {member.key: member for member in height_members}
    height_footer = module.included_models_footer(height_keys, height_definitions)
    height_footer_labels = [member.footer_label or member.label for member in height_members]
    check(height_footer.startswith("Included models: "), "image footer should identify its model roster")
    check(all(label in height_footer for label in height_footer_labels), "image footer should name every included height family")
    check(len(height_footer.splitlines()) == 2, "full height roster should fit the reserved two-line footer")
    check(all(len(line) <= module.MEMBER_FOOTER_MAX_CHARS for line in height_footer.splitlines()), "image footer lines should stay inside the layout budget")
    surface_definitions = {member.key: member for member in surface_members}
    surface_footer = module.included_models_footer(surface_keys, surface_definitions)
    check(all((member.footer_label or member.label) in surface_footer for member in surface_members), "image footer should name every included surface family")
    check(len(surface_footer.splitlines()) == 2, "longest model roster should fit the reserved two-line footer")
    check(all(len(line) <= module.MEMBER_FOOTER_MAX_CHARS for line in surface_footer.splitlines()), "longest footer lines should stay inside the layout budget")
    near_surface_footer = module.included_models_footer(near_surface_keys, {member.key: member for member in near_surface_members})
    check("CMA CPSv3" in near_surface_footer, "near-term super-ensemble footer should name CMA CPSv3")
    check(len(near_surface_footer.splitlines()) == 2, "CMA-expanded roster should fit the reserved two-line footer")
    check(all(len(line) <= module.MEMBER_FOOTER_MAX_CHARS for line in near_surface_footer.splitlines()), "CMA-expanded footer lines should stay inside the layout budget")
    check(height_footer_labels[-1] not in module.included_models_footer(height_keys[:-1], height_definitions), "partial-map footer should omit an unavailable family")
    footer_parameter = inspect.signature(module.render_map).parameters.get("footer_text")
    check(footer_parameter is not None and footer_parameter.default == "", "shared renderer should expose an optional footer without changing other model maps")
    check(adapter_text.count("footer_text=included_models_footer(keys, definitions)") == 2, "monthly and seasonal super-ensemble maps should both receive the included-model footer")
    check(abs(sum(item["weight"] for item in module.weights_for(height_keys, {member.key: member for member in height_members})) - 1.0) < 1e-8, "equal weights should sum to one")
    check(module.resolve_cfsv2_anchor("2026081818", "2026080100") == "2026081818", "CFSv2 anchor should align within the shared initialization month")
    original_latest_init = module.cfsv2.discover_latest_init
    try:
        module.cfsv2.discover_latest_init = lambda: "2026090318"
        check(
            module.resolve_cfsv2_anchor("latest", "2026080100") == "2026090318",
            "latest CFSv2 should remain target-aligned when monthly systems are still on the prior release month",
        )
    finally:
        module.cfsv2.discover_latest_init = original_latest_init
    try:
        module.resolve_cfsv2_anchor("2026073118", "2026080100")
    except module.SuperEnsembleError:
        pass
    else:
        raise AssertionError("an explicit CFSv2 anchor must not silently drift into another initialization month")
    height_exclusions = module.membership_ledger("500mb_height_anomaly")["excluded"]
    check(any(item["package"] == "C3S NCEP System 2" and item["represented_by"] == module.CFSV2_MEMBER_KEY for item in height_exclusions), "height ledger must document the C3S NCEP substitution")
    check(any(item["package"] == "NASA GEOS-S2S-3 APCN z500 archive" and item["represented_by"] is None for item in height_exclusions), "height ledger must document the rejected NASA pressure level")
    surface_exclusions = module.membership_ledger("2m_temperature_anomaly")["excluded"]
    check(any(item["package"] == "NMME NASA_GEOS5v2" and item["represented_by"] == module.GEOS_MEMBER_KEY for item in surface_exclusions), "surface ledger must document the NASA deduplication")
    snowfall_ledger = module.membership_ledger("snowfall_anomaly")
    check(snowfall_ledger["expected_count"] == 7, "snowfall membership should expect only the seven supported families")
    check(any(item["package"] == "C3S NCEP System 2" and item["represented_by"] == module.CFSV2_MEMBER_KEY for item in snowfall_ledger["excluded"]), "snowfall ledger must document the C3S NCEP substitution")
    check(any(item["package"].startswith("C3S JMA") and item["represented_by"] is None for item in snowfall_ledger["excluded"]), "snowfall ledger must document unavailable JMA data")
    check(any(item["package"] == "C3S BOM System 2" and item["represented_by"] is None for item in snowfall_ledger["excluded"]), "snowfall ledger must document unavailable BOM data")
    check(module.c3s.target_month("2026080100", 4) == "202612", "lead 4 should align to December")
    check(module.nmme.target_month("2026080800", 4) == "202612", "NMME lead alignment should match December")
    for term in ("intersection of canonical members", "APCC MME", "C3S multi-system mean", "NMME CFSv2", "native_model_baselines", "NASA GEOS-S2S-3"):
        check(term in adapter_text, f"adapter is missing deduplication term: {term}")
    for term in ("numeric_grid", "numeric_grid_format", "write_grid_state(anomaly", "csv.gz"):
        check(term in adapter_text, f"adapter is missing analog numeric-grid export term: {term}")
    check(adapter_text.count("write_grid_state(anomaly") == 2, "monthly and seasonal super-ensemble maps should both export numeric grids")
    check("NOAA CFSv2 24-cycle rolling blend" in adapter_text, "super-ensemble should label the operational CFSv2 blend accurately")
    check(
        'if args.synthetic_preview\n        else cansips.find_wgrib2(args.wgrib2)' in adapter_text,
        "non-synthetic snowfall-only runs must resolve wgrib2 for their CFSv2 member",
    )
    check(
        'not any(product != "snowfall_anomaly" for product in products)' not in adapter_text,
        "snowfall-only runs must not replace the configured wgrib2 path with an empty executable",
    )
    check("--cfsv2-rolling-days 6" in workflow, "super-ensemble workflow should use the archive-safe CFSv2 window")
    for term in (
        "name: Deduplicated Seasonal Super Ensemble", "CDS_API_KEY", "Restore rolling CFSv2 state",
        "Restore CMA CPSv3 source cache", "--cma-cache-dir", "Restore NASA GEOS-S2S-3 numerical cache",
        "--geos-cache-dir", "cfsv2-rolling-", "superensemble-pages-", "30 20 22 * *",
        "SCHEDULED_SUPER_PRODUCT: all", "max-parallel: 4", "matrix:",
        "SUPER_PRODUCT: ${{ matrix.product }}", "superensemble-product-",
        "actions/download-artifact@v4", "merge_seasonal_payloads.py", "write_seasonal_fragment.py",
        "Set up wgrib2 once for the product matrix", "Restore wgrib2 for this product",
    ):
        check(term in workflow, f"workflow is missing term: {term}")
    check(
        workflow.index("            cfsv2-rolling-\n")
        < workflow.index("            superensemble-cfsv2-${{ runner.os }}-${{ env.SUPER_PRODUCT }}-\n"),
        "super-ensemble should restore the newest standalone CFSv2 rolling state before its older product cache",
    )
    check(
        'required: "true"\n          build: "false"' in workflow,
        "every super-ensemble product, including manual snowfall runs, must restore wgrib2",
    )
    check(
        "SUPER_PRODUCT != 'snowfall_anomaly'" not in workflow,
        "snowfall must not bypass wgrib2 now that the blend includes derived CFSv2 data",
    )
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

    with tempfile.TemporaryDirectory() as temporary:
        originals = {
            name: getattr(module.cfsv2, name)
            for name in (
                "rolling_cycle_inits", "decode_target_ensemble",
                "decode_snowfall_target_ensemble", "load_snowfall_baseline",
            )
        }
        snowfall_calls = {"forecast": 0, "baseline": 0}
        try:
            module.cfsv2.rolling_cycle_inits = lambda anchor, count: ["2026081812"] * count

            def fail_generic_decoder(*args, **kwargs):
                raise AssertionError("snowfall must use the derived-field decoder")

            def mock_snowfall_decoder(*args, **kwargs):
                snowfall_calls["forecast"] += 1
                return (
                    module.Grid([0.0], [0.0], [[3.5]]), [], 24, 24,
                    "24/24-cycle derived snowfall mean", 0.0,
                    {"method": "temperature phase gate"},
                )

            def mock_snowfall_baseline(*args, **kwargs):
                snowfall_calls["baseline"] += 1
                return (
                    module.Grid([0.0], [0.0], [[1.0]]),
                    {"source": "test derived snowfall climatology", "years": "1982-2010"},
                    0.0,
                )

            module.cfsv2.decode_target_ensemble = fail_generic_decoder
            module.cfsv2.decode_snowfall_target_ensemble = mock_snowfall_decoder
            module.cfsv2.load_snowfall_baseline = mock_snowfall_baseline
            grids = {4: {}}
            heights = {4: {}}
            provenance = {4: {}}
            errors = {4: {}}
            module.load_cfsv2_member(
                args=SimpleNamespace(
                    cfsv2_anchor_init="2026081812", cfsv2_rolling_days=6,
                    cfsv2_rolling_member=1, request_delay=0.0,
                    force_decode=False, decode_only=False,
                ),
                product="snowfall_anomaly", init="2026080100", leads=[4],
                cache_dir=Path(temporary), state_dir=Path(temporary) / "rolling",
                root=ROOT, wgrib2="wgrib2", member_grids=grids,
                height_grids=heights, provenance=provenance, errors=errors,
            )
            check(snowfall_calls == {"forecast": 1, "baseline": 1}, "rolling CFSv2 snowfall should use both derived forecast and derived baseline adapters")
            check(grids[4][module.CFSV2_MEMBER_KEY].values == [[2.5]], "rolling CFSv2 snowfall should subtract the matching derived climatology")
            check(provenance[4][module.CFSV2_MEMBER_KEY]["derivation"]["method"] == "temperature phase gate", "snowfall provenance should retain the derivation method")
            check(not heights[4], "derived snowfall should not populate 500-mb height contours")
            check(not errors[4], "mock rolling CFSv2 snowfall load should not record an error")
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

    original_cma_download = module.cma.download_bundle
    original_cma_decode = module.cma.decode_product_bundle
    try:
        module.cma.download_bundle = lambda *args, **kwargs: (ROOT / "mock-cma.nc", "mock-token")
        module.cma.decode_product_bundle = lambda *args, **kwargs: (
            {1: module.Grid([0.0], [0.0], [[1.5]])},
            {"hindcast_start_year": "2001", "hindcast_end_year": "2024"},
            {"units": "K"},
        )
        grids = {1: {}}
        provenance = {1: {}}
        errors = {1: {}}
        module.load_cma_member(
            product="2m_temperature_anomaly", init="2026080100", leads=[1],
            cache_dir=ROOT, root=ROOT, member_grids=grids,
            provenance=provenance, errors=errors,
        )
        check(grids[1][module.CMA_MEMBER_KEY].values == [[1.5]], "CMA anomaly should enter the canonical member grid")
        check(provenance[1][module.CMA_MEMBER_KEY]["internal_members"] == 21, "CMA provenance should retain its member count")
        check(provenance[1][module.CMA_MEMBER_KEY]["baseline"]["label"] == "CMA CPSv3 2001-2024 hindcast climatology", "CMA provenance should retain provider hindcast years")
        check(not errors[1], "mock CMA load should not record an error")
    finally:
        module.cma.download_bundle = original_cma_download
        module.cma.decode_product_bundle = original_cma_decode

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
