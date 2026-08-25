#!/usr/bin/env python3
"""Offline tests for top-analog map periods, URLs, and retained products."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MODULE_PATH = SCRIPTS / "build_analog_products.py"


def load_module():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("analog_products_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load analog product builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    module = load_module()

    december = module._period_for_result("202612", {"label": "December 1940", "winter_year": 1941})
    check(december["start_date"] == "1940-12-01", "December analog start date is wrong")
    check(december["end_date"] == "1940-12-31", "December analog end date is wrong")
    check(december["psl_year"] == 1940 and december["psl_start_month"] == 12, "December PSL controls are wrong")

    djf = module._period_for_result("202612-202702", {"label": "DJF 1940-41", "winter_year": 1941})
    check(djf["start_date"] == "1940-12-01", "DJF analog start date is wrong")
    check(djf["end_date"] == "1941-02-28", "DJF analog end date is wrong")
    check(djf["psl_year"] == 1941 and djf["psl_end_month"] == 2, "DJF PSL controls are wrong")

    psl_query = parse_qs(urlsplit(module._psl_url(december, module.PRODUCT_SPECS["psl_500mb_height_anomaly"])).query)
    check(psl_query["dataset1"] == ["ERA5"], "PSL must use the same ERA5 archive family")
    check(psl_query["iy"] == ["1940"] and psl_query["fmonth"] == ["11"], "PSL monthly year controls are wrong")
    check(psl_query["type"] == ["1"] and psl_query["level"] == ["500mb"], "PSL anomaly controls are wrong")
    psl_image_url = module._extract_psl_image_url(
        b'<img src="/img/icons/us_flag_small.png"><IMG src="/tmp/generated_map.png">'
    )
    check(psl_image_url == "https://psl.noaa.gov/tmp/generated_map.png", "PSL should select the generated map instead of the NOAA flag icon")

    mrcc_query = parse_qs(urlsplit(module._mrcc_url(djf)).query)
    check(mrcc_query["loc"] == ["ER"], "MRCC must target the NWS Eastern Region")
    check(mrcc_query["ds"] == ["19401201"] and mrcc_query["de"] == ["19410228"], "MRCC DJF dates are wrong")
    check(mrcc_query["var"] == ["snow"] and mrcc_query["calc"] == ["departure"], "MRCC snowfall departure controls are wrong")

    retry_calls: list[str] = []
    original_retry_delay = module.MRCC_RETRY_DELAY_SECONDS
    module.MRCC_RETRY_DELAY_SECONDS = 0
    try:
        def transient_mrcc_fetcher(url: str, _timeout: int) -> bytes:
            retry_calls.append(url)
            if len(retry_calls) == 1:
                raise module.AnalogProductError("HTTP 500 from MRCC test endpoint")
            return module.PNG_SIGNATURE + b"retry-test"

        retry_image = module._fetch_mrcc_image(transient_mrcc_fetcher, module._mrcc_url(djf), 1)
        check(retry_image.startswith(module.PNG_SIGNATURE), "MRCC retry should return the recovered PNG")
        check(len(retry_calls) == 2, "MRCC transient 500 should be retried once")
    finally:
        module.MRCC_RETRY_DELAY_SECONDS = original_retry_delay

    analog_manifest = {
        "source": {"climatology_years": "1981-2010"},
        "entries": [{
            "model": "cfsv2",
            "model_label": "CFSv2",
            "target": "202612",
            "target_label": "December 2026",
            "init_utc": "2026-08-20T06:00:00Z",
            "results": [{"rank": 1, "label": "December 1940", "winter_year": 1941, "pattern_correlation": 0.91}],
        }],
    }
    png = module.PNG_SIGNATURE + b"offline-test"
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> bytes:
        calls.append(url)
        if url.startswith(module.PSL_MAP_URL):
            return b'<html><IMG src="/tmp/test.png"></html>'
        return png

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        analog_path = root / "seasonal" / "analog_z500_manifest.json"
        output_path = root / "seasonal" / "analog_products_manifest.json"
        output_dir = root / "seasonal" / "analog_products"
        analog_path.parent.mkdir(parents=True)
        analog_path.write_text(json.dumps(analog_manifest), encoding="utf-8")
        first = module.build_manifest(
            root=root,
            analog_manifest_path=analog_path,
            output_manifest_path=output_path,
            output_dir=output_dir,
            fetcher=fetcher,
        )
        check(first["status"] == "ready", "offline product build should be ready")
        check(len(calls) == 5, "PSL and MRCC should each be requested once per product")
        module.write_manifest(output_path, first)
        for product in first["entries"][0]["products"].values():
            check(product["status"] == "ready" and (root / product["image"]).exists(), "product image was not retained")

        def failing_fetcher(_url: str, _timeout: int) -> bytes:
            raise module.AnalogProductError("simulated source outage")

        retained = module.build_manifest(
            root=root,
            analog_manifest_path=analog_path,
            output_manifest_path=output_path,
            output_dir=output_dir,
            fetcher=failing_fetcher,
        )
        check(all(product["status"] == "ready" for product in retained["entries"][0]["products"].values()), "unchanged top analog should reuse good products")

        changed = json.loads(json.dumps(analog_manifest))
        changed["entries"][0]["results"][0]["winter_year"] = 1942
        changed["entries"][0]["results"][0]["label"] = "December 1941"
        analog_path.write_text(json.dumps(changed), encoding="utf-8")
        stale = module.build_manifest(
            root=root,
            analog_manifest_path=analog_path,
            output_manifest_path=output_path,
            output_dir=output_dir,
            fetcher=failing_fetcher,
        )
        check(all(product["status"] == "stale" for product in stale["entries"][0]["products"].values()), "a changed top analog should retain the last good products")

    print("ANALOG PRODUCTS OK: monthly/DJF windows, PSL/MRCC controls, and retained source failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
