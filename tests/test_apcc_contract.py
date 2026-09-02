#!/usr/bin/env python3
"""Static APCC MME adapter, workflow, and manifest contracts."""

import importlib.util
import datetime as dt
from pathlib import Path
import sys
import tempfile

import numpy as np


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
    check(module.latest_target_month(dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc)) == "202608", "APCC pre-release target month should remain current")
    check(module.latest_target_month(dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)) == "202609", "APCC post-release target month should advance one month")
    check(module.target_window("202608", "0,1,2")[0] == "202608-202610", "APCC fallback target labeling is incorrect")
    check(module.target_window("202609", "3,4,5")[:2] == ("202612-202702", "DJF 2027"), "APCC far 6-month window should select DJF")
    check(module.target_window("202610", "3,4,5")[:2] == ("202701-202703", "JFM 2027"), "APCC far window should support rolling three-month seasons")
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
    issue = module.source_issue_datetime({"Issued_Date": "18 Aug 2026"}, "202609")
    check(issue.isoformat() == "2026-08-18T00:00:00+00:00", "APCC provider issue date was not preserved")
    fallback_issue = module.source_issue_datetime({}, "202609")
    check(fallback_issue.isoformat() == "2026-08-15T00:00:00+00:00", "APCC issue fallback must precede the API target month")
    height_spec = module.PRODUCT_SPECS["500mb_height_anomaly"]
    check((height_spec["anomaly_min"], height_spec["anomaly_max"]) == (-100.0, 100.0), "APCC 500-mb should retain the shared ±100 m range")
    check(height_spec["anomaly_ticks"] == list(range(-100, 101, 10)), "APCC 500-mb should retain 10-metre labelled bounds")
    for product in ("850mb_temperature_anomaly", "2m_temperature_anomaly"):
        temperature_spec = module.PRODUCT_SPECS[product]
        check((temperature_spec["anomaly_min"], temperature_spec["anomaly_max"]) == (-7.0, 7.0), f"APCC {product} should use the shared ±7 °C range")
        check(temperature_spec["anomaly_ticks"] == list(range(-7, 8)), f"APCC {product} should use 1 °C labelled bounds")
    precipitation_spec = module.PRODUCT_SPECS["precipitation_anomaly"]
    check(precipitation_spec["raw_units"] == "mm/day", "APCC raw precipitation units are incorrect")
    check(precipitation_spec["units"] == "in", "APCC comparison precipitation must use inches")
    check(
        (precipitation_spec["anomaly_min"], precipitation_spec["anomaly_max"]) == (-8.0, 8.0),
        "APCC seasonal precipitation should use the shared ±8-inch range",
    )
    converted = module._convert_values(
        np.array([25.4]), precipitation_spec, {"units": "mm/day"}, precip_days=3
    )
    check(np.allclose(converted, np.array([3.0])), "APCC precipitation conversion should accumulate and convert mm to inches")
    northern_height = module.PRODUCT_SPECS["500mb_height_anomaly_nh"]
    check(northern_height["region"] == module.NORTHERN_HEMISPHERE_REGION, "APCC Northern Hemisphere 500-mb view must use the polar region")
    mslp_spec = module.PRODUCT_SPECS["mslp_anomaly"]
    check((mslp_spec["anomaly_min"], mslp_spec["anomaly_max"]) == (-10.0, 10.0), "APCC MSLP should use the shared ±10 hPa range")
    check("6-MON" in module.dataset_url("MME_6MONTH"), "APCC 6-month provenance URL is incorrect")
    parser_defaults = module.build_parser().parse_args([])
    check(parser_defaults.dataset == "MME_6MONTH" and parser_defaults.target_window == "", "APCC defaults should derive the far 6-month season")
    check("default: \"MME_6MONTH\"" in workflow and "default: \"3,4,5\"" in workflow, "APCC workflow should request the far 6-month window")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        aso = root / "FORECAST_SCM_SEP_SON_2026_t2m.nc"
        djf_file = root / "FORECAST_SCM_SEP_DJF_2026_t2m.nc"
        aso.touch()
        djf_file.touch()
        selected = module.find_product_file([aso, djf_file], module.PRODUCT_SPECS["2m_temperature_anomaly"], "DJF")
        check(selected == djf_file, "APCC archive selection must use the requested season rather than the first file")
    for name, spec in module.PRODUCT_SPECS.items():
        check(len(spec["anomaly_ticks"]) == len(spec["anomaly_palette"]) + 1, f"APCC {name} palette bounds are misaligned")
    print("APCC CONTRACT OK: target-month indexing, canonical comparison units/scales, source issue date, native anomalies, workflow, and retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
