#!/usr/bin/env python3
"""Static NASA GEOS-S2S-3 adapter, workflow, and manifest contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "geos_s2s3_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "geos-s2s3.yml"
PAGE = ROOT / "public" / "seasonal" / "geos_s2s3" / "index.html"
DOC = ROOT / "docs" / "SEASONAL_GEOS_S2S3.md"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGE, DOC):
        check(path.exists(), f"missing NASA contract file: {path}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    for term in (
        "GEOS-S2S-3", "NASA_LOOKUP_URL", "atmospheric-anomalies", "T2M", "Precip",
        "geos_s2s3_manifest.json", "geos-s2s3-pages-", "pre-rendered", "500-mb",
    ):
        check(term in adapter or term in workflow or term in page or term in doc, f"missing NASA term: {term}")
    check('"comparison_products": []' in adapter, "NASA source should declare its non-comparison scope explicitly")
    print("NASA GEOS-S2S-3 CONTRACT OK: official lookup, supported products, image provenance, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
