#!/usr/bin/env python3
"""Static and unit contracts for the WMO GPC Beijing CMA CPSv3 adapter."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "cma_cpsv3_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "cma-cpsv3.yml"
PAGES = ROOT / ".github" / "workflows" / "publish-pages.yml"
PAGE = ROOT / "public" / "seasonal" / "cma_cpsv3" / "index.html"
DASHBOARD = ROOT / "public" / "seasonal" / "index.html"
DOC = ROOT / "docs" / "SEASONAL_CMA_CPSV3.md"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("cma_cpsv3_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load CMA CPSv3 adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGES, PAGE, DASHBOARD, DOC):
        check(path.exists(), f"missing CMA CPSv3 contract file: {path}")
    adapter_text = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")
    module = load_adapter()

    check(module.CMA_MODEL == "CMA CPSv3", "the public model name must be CMA CPSv3")
    check(module.CMA_ENSEMBLE_MEMBERS == 21, "CMA CPSv3 must retain the documented 21-member ensemble")
    check(module.SUPPORTED_LEADS == frozenset({1, 2, 3}), "WMO redistribution must be limited to forecast months 1-3")
    check((module.EXPECTED_LONGITUDES, module.EXPECTED_LATITUDES) == (144, 73), "CMA source grid contract must remain 2.5-degree global")
    check("kg/m^2" in module.SOURCE_UNIT_ALIASES["prec"], "the adapter must recognize the Beijing file's abbreviated precipitation units")
    check(module.WMOLC_INTERMEDIATE_CA.exists(), "the WMO TLS intermediate is missing")
    certificate = module.ssl.PEM_cert_to_DER_cert(module.WMOLC_INTERMEDIATE_CA.read_text(encoding="ascii"))
    check(module.hashlib.sha256(certificate).hexdigest() == module.WMOLC_INTERMEDIATE_CA_SHA256, "the WMO TLS intermediate fingerprint changed")
    tls_context = module.wmolc_ssl_context()
    check(tls_context.verify_mode == module.ssl.CERT_REQUIRED, "WMO TLS must require a valid certificate")
    check(tls_context.check_hostname, "WMO TLS must verify the server hostname")
    trusted_fingerprints = {
        module.hashlib.sha256(item).hexdigest()
        for item in tls_context.get_ca_certs(binary_form=True)
    }
    check(module.WMOLC_INTERMEDIATE_CA_SHA256 in trusted_fingerprints, "the WMO TLS context did not load the pinned intermediate")
    check(len(trusted_fingerprints) > 1, "the WMO TLS context must retain the platform root store")
    source_session = module.requests_session()
    source_adapter = source_session.get_adapter(f"{module.WMOLC_ROOT}/")
    check(source_adapter.max_retries.total == 4, "WMO TLS requests must retry transient connection failures")
    check("POST" in source_adapter.max_retries.allowed_methods, "idempotent WMO listing/download POST requests must be retryable")
    check(module.bundle_name("202608") == "beijing_202608_202609_202611.nc", "WMO Beijing bundle name is misaligned")
    check(module.issue_directory("202608") == "/forecast/Beijing/2026/08", "WMO Beijing issue directory is incorrect")
    check(module.parse_init("2026080100") == "202608", "full issue initialization should normalize to YYYYMM")
    run_source = ADAPTER.read_text(encoding="utf-8")
    check('source_token = ""' in run_source, "explicit WMO issue runs must discover their download token")
    check('source_token = "local source file"' in run_source, "local source-file runs must retain their provenance label")
    check(module.parse_leads("1,2,3", "test") == [1, 2, 3], "supported WMO leads should parse in order")
    try:
        module.parse_leads("4", "test")
    except (module.CMACPSv3Error, module.c3s.C3SError):
        pass
    else:
        raise AssertionError("the adapter must reject unavailable WMO forecast month 4")

    token = "beijing_202608_202609_202611.nc|12345"
    listing = f'<input name="fnames[]" value="{token}">'
    check(module.listing_entries(listing) == {module.bundle_name("202608"): token}, "WMO listing token parsing failed")
    check(set(module.selected_products("all")) == set(module.PRODUCT_SPECS), "all must select all six CMA products")
    check(set(module.PRODUCT_SPECS) == {
        "500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly",
        "precipitation_anomaly", "sea_surface_temperature_anomaly", "mslp_anomaly",
    }, "CMA product suite must contain the six shared anomaly fields")

    height = module.PRODUCT_SPECS["500mb_height_anomaly"]
    check((height["anomaly_min"], height["anomaly_max"]) == (-100.0, 100.0), "CMA 500-mb height must use the shared ±100 m scale")
    check(height["anomaly_ticks"] == list(range(-100, 101, 10)), "CMA 500-mb height must use 10-m bounds")
    check(height["height_contours"] is False, "CMA anomalies must not fabricate absolute-height contours")
    for product in ("850mb_temperature_anomaly", "2m_temperature_anomaly"):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"CMA {product} should inherit the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"CMA {product} should inherit 1 °C labelled bounds")
        check(len(temperature_spec["anomaly_ticks"]) == len(temperature_spec["anomaly_palette"]) + 1, f"CMA {product} bounds must align with colors")
    check(module.PRODUCT_SPECS["sea_surface_temperature_anomaly"]["map_domain"] == "ocean", "CMA SST must remain ocean-only")
    mslp = module.PRODUCT_SPECS["mslp_anomaly"]
    check((mslp["anomaly_min"], mslp["anomaly_max"]) == (-10.0, 10.0), "CMA MSLP must use the shared ±10 hPa scale")
    monthly_precip = module.render_product_spec("precipitation_anomaly", seasonal=False)
    seasonal_precip = module.render_product_spec("precipitation_anomaly", seasonal=True)
    check((monthly_precip["anomaly_min"], monthly_precip["anomaly_max"]) == (-4.0, 4.0), "monthly CMA precipitation must use ±4 in")
    check((seasonal_precip["anomaly_min"], seasonal_precip["anomaly_max"]) == (-8.0, 8.0), "seasonal CMA precipitation must use ±8 in")
    check(np.allclose(module.convert_values(np.asarray([100.0]), "mslp_anomaly", "202609"), [1.0]), "CMA MSLP conversion must convert Pa to hPa")
    expected_inches = module.c3s.month_seconds("202609") / 25.4
    check(np.allclose(module.convert_values(np.asarray([1.0]), "precipitation_anomaly", "202609"), [expected_inches]), "CMA precipitation rate conversion is incorrect")
    check(module.baseline_label({"hindcast_start_year": "2001", "hindcast_end_year": "2024"}) == "CMA CPSv3 2001-2024 hindcast climatology", "provider hindcast years should remain visible")

    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        expected_name = module.bundle_name("202608")
        archive = temporary_root / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(expected_name, b"netcdf-placeholder")
        extracted = module.safe_extract_bundle(archive, temporary_root / "decoded", expected_name)
        check(extracted.read_bytes() == b"netcdf-placeholder", "safe WMO archive extraction changed the payload")

        unsafe = temporary_root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as bundle:
            bundle.writestr(f"../{expected_name}", b"unsafe")
        try:
            module.safe_extract_bundle(unsafe, temporary_root / "unsafe-decoded", expected_name)
        except module.CMACPSv3Error:
            pass
        else:
            raise AssertionError("WMO archive extraction must reject parent traversal")

        output = temporary_root / "manifest.json"
        previous = temporary_root / "previous.json"
        previous.write_text(json.dumps({"runs": [
            {"id": f"old-{month}", "init_utc": f"2025-{month:02d}-01T00:00:00Z"}
            for month in range(1, 5)
        ]}), encoding="utf-8")
        module.write_manifest(output, [{"id": "current", "init_utc": "2026-08-01T00:00:00Z"}], previous, 4)
        payload = json.loads(output.read_text(encoding="utf-8"))
        check(len(payload["runs"]) == 4, "CMA retention should keep the current cycle plus three prior cycles")
        check(payload["kind"] == "cma_cpsv3_seasonal_manifest", "CMA manifest kind is incorrect")
        check(payload["source_horizon"]["redistributed_forecast_months"] == [1, 2, 3], "manifest must disclose the WMO source horizon")

    for term in (
        "WMO LC-SPMME", "GPC Beijing", "directDownload", "forecast_time0", "hindcast_start_year",
        "kg m-2 s-1", "xarray", "netCDF4", "--previous-manifest", "--retain-cycles",
        "RapidSSLTLSRSACAG1.crt.pem", "WMOLC_INTERMEDIATE_CA_SHA256", "ssl.create_default_context",
        '"Origin": WMOLC_ROOT', '"Referer": WMOLC_DIRECT_URL',
    ):
        check(term in adapter_text or term in workflow or term in documentation, f"missing CMA source contract term: {term}")
    check("verify=False" not in adapter_text, "the CMA adapter must never disable TLS verification")
    for term in (
        "name: CMA CPSv3 Seasonal Graphics", 'default: "all"', '30 18 21 * *',
        "cma-cpsv3-pages-${{ github.run_id }}", "cma_cpsv3_manifest.json",
    ):
        check(term in workflow, f"CMA workflow is missing term: {term}")
    for term in ("CMA CPSv3 Seasonal Graphics", "Download CMA CPSv3 payload", "incoming/cma_cpsv3", "cma_cpsv3_manifest.json"):
        check(term in pages, f"Pages publisher is missing CMA term: {term}")
    for term in ("CMA CPSv3", "cma_cpsv3_manifest.json", "WMO LC-SPMME / GPC Beijing"):
        check(term in page or term in dashboard, f"CMA page/dashboard is missing term: {term}")

    print("CMA CPSV3 CONTRACT OK: WMO source, six products, units, lead horizon, workflow, Pages, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
