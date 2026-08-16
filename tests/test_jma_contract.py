#!/usr/bin/env python3
"""Static contract checks for the standalone JMA seasonal component."""

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "c3s_seasonal.py"
WORKFLOW = ROOT / ".github" / "workflows" / "jma.yml"
PAGES = ROOT / ".github" / "workflows" / "publish-pages.yml"
PAGE = ROOT / "public" / "seasonal" / "jma" / "index.html"
DOC = ROOT / "docs" / "SEASONAL_JMA.md"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("jma_contract", ADAPTER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load C3S adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (ADAPTER, WORKFLOW, PAGES, PAGE, DOC):
        check(path.exists(), f"missing JMA contract file: {path}")
    adapter = ADAPTER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pages = PAGES.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    for term in (
        '"jma"', '"system": "4"', '"members": 55', "JMA/MRI-CPS4",
        "--no-blend", "--centres jma", "public/seasonal/jma",
        "jma_manifest.json", "JMA Seasonal Graphics", "jma-pages-",
    ):
        check(term in adapter or term in workflow or term in pages or term in page or term in doc, f"missing JMA term: {term}")
    module = load_adapter()
    check(module.CENTRES["jma"]["system"] == "4", "JMA must use current C3S CPS4/System 4")
    check(module.CENTRES["jma"]["members"] == 55, "JMA C3S member metadata must match the CPS4 qualifying subset")
    check(module.CENTRES["jma"]["model_version"] == "JMA/MRI-CPS4", "JMA model version must remain explicit in the manifest")
    print("JMA CONTRACT OK: C3S CPS4/System 4 component, standalone workflow, viewer, Pages merge, and provenance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
