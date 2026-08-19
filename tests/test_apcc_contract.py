#!/usr/bin/env python3
"""Static APCC MME adapter, workflow, and manifest contracts."""

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "apcc_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "apcc.yml"
PAGE = ROOT / "public" / "seasonal" / "apcc" / "index.html"
DOC = ROOT / "docs" / "SEASONAL_APCC.md"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("apcc_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load APCC adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGE, DOC):
        check(path.exists(), f"missing APCC contract file: {path}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    for term in (
        "APCC_API_KEY", "MME_3MONTH", "MME_6MONTH", "3-MON", "SEASONAL",
        "APCC_REQUEST_URL", "APCC_STATUS_URL", "safe_extract", "netCDF4",
        "apcc_manifest.json", "apcc-pages-", "APCC CLIK API",
    ):
        check(term in adapter or term in workflow or term in page or term in doc, f"missing APCC term: {term}")
    module = load_adapter()
    check(module.target_window("202608", "0,1,2")[0] == "202608-202610", "APCC fallback target labeling is incorrect")
    aso = module.source_period_from_metadata(
        {"MME_Forecast_Info": "Seasonal Mean Forecast for ASO (2026ASO)"},
        "202608",
    )
    check(aso["period_label"] == "ASO 2026", "APCC source season metadata was not honored")
    check(aso["target_code"] == "202608-202610", "APCC ASO target window is incorrect")
    djf = module.source_period_from_metadata(
        {"MME_Forecast_Info": "Seasonal Mean Forecast for DJF (2026SONDJF)"},
        "202608",
    )
    check(djf["period_label"] == "DJF 2027", "APCC cross-year DJF label is incorrect")
    check(djf["target_code"] == "202612-202702", "APCC cross-year DJF target window is incorrect")
    height_spec = module.PRODUCT_SPECS["500mb_height_anomaly"]
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "APCC 500-mb should retain the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "APCC 500-mb should retain 10-metre labelled bounds")
    check(module.PRODUCT_SPECS["precipitation_anomaly"]["raw_units"] == "mm/day", "APCC precipitation units are incorrect")
    check(module.PRODUCT_SPECS["precipitation_anomaly"]["anomaly_max"] == 200.0, "APCC precipitation scale is not native")
    check("6-MON" in module.dataset_url("MME_6MONTH"), "APCC 6-month provenance URL is incorrect")
    for name, spec in module.PRODUCT_SPECS.items():
        check(len(spec["anomaly_ticks"]) == len(spec["anomaly_palette"]) + 1, f"APCC {name} palette bounds are misaligned")
    print("APCC CONTRACT OK: authenticated CLIK request, native anomalies, products, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
