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
    check(module.target_window("202608", "4,5,6")[0] == "202612-202702", "APCC DJF target labeling is incorrect")
    for name, spec in module.PRODUCT_SPECS.items():
        check(len(spec["anomaly_ticks"]) == len(spec["anomaly_palette"]) + 1, f"APCC {name} palette bounds are misaligned")
    print("APCC CONTRACT OK: authenticated CLIK request, native anomalies, products, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
