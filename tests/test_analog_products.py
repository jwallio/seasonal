#!/usr/bin/env python3
"""Offline tests for top-analog map periods, URLs, and retained products."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import numpy as np


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
    import cfsv2_seasonal as seasonal

    december = module._period_for_result("202612", {"label": "December 1940", "winter_year": 1941})
    check(december["start_date"] == "1940-12-01", "December analog start date is wrong")
    check(december["end_date"] == "1940-12-31", "December analog end date is wrong")
    check(december["psl_year"] == 1940 and december["psl_start_month"] == 12, "December PSL controls are wrong")

    djf = module._period_for_result("202612-202702", {"label": "DJF 1940-41", "winter_year": 1941})
    check(djf["start_date"] == "1940-12-01", "DJF analog start date is wrong")
    check(djf["end_date"] == "1941-02-28", "DJF analog end date is wrong")
    check(djf["psl_year"] == 1941 and djf["psl_end_month"] == 2, "DJF PSL controls are wrong")

    psl_query = parse_qs(urlsplit(module._psl_url(december, module.PRODUCT_SPECS["psl_500mb_height_anomaly"])).query)
    check(psl_query["dataset1"] == [module.WRIT_EARLY_DATASET], "pre-1979 PSL analog maps must use the WRIT fallback archive")
    check(psl_query["iy"] == ["1940"] and psl_query["fmonth"] == ["11"], "PSL monthly year controls are wrong")
    check(psl_query["type"] == ["1"] and psl_query["level"] == ["500mb"], "PSL anomaly controls are wrong")
    check(psl_query["mapt"] == ["6"] and psl_query["proj"] == ["North America"], "PSL 500-mb projection controls are wrong")
    check(psl_query["colortable"] == ["default"], "PSL 500-mb maps should use the default color table")
    cfsr_december = module._period_for_result("202612", {"label": "December 2015", "winter_year": 2016})
    cfsr_query = parse_qs(urlsplit(module._psl_url(cfsr_december, module.PRODUCT_SPECS["psl_500mb_height_anomaly"])).query)
    check(cfsr_query["dataset1"] == [module.WRIT_DATASET], "post-1978 PSL analog maps must use the CFSR archive family")
    temperature_query = parse_qs(urlsplit(module._psl_url(cfsr_december, module.PRODUCT_SPECS["psl_2m_temperature_anomaly"])).query)
    check(temperature_query["dataset1"] == [module.WRIT_DATASET], "PSL temperature maps must use the CFSR archive family")
    check(temperature_query["proj"] == ["USA(CONUS)"], "PSL 2-m temperature maps should use the CONUS region")
    check(temperature_query["colortable"] == ["testcmap"], "PSL 2-m temperature maps should use testcmap")
    writ_render_spec = module._writ_render_product_spec(
        "psl_500mb_height_anomaly",
        seasonal,
        module.WRIT_DATASET,
    )
    check(
        writ_render_spec["resampling_method"] == "bicubic",
        "WRIT maps should smooth coarse-grid contour geometry with bicubic resampling",
    )
    check(
        writ_render_spec["source_smoothing_sigma"] == module.WRIT_SOURCE_SMOOTHING_SIGMA,
        "WRIT maps should use the configured sub-cell source smoothing",
    )
    rendering_metadata = module._writ_rendering_metadata(
        seasonal,
        region=tuple(writ_render_spec["region"]),
        map_region=module.WRIT_NORTH_AMERICA_REGION,
        product_spec=writ_render_spec,
    )
    check(rendering_metadata["id"] == module.WRIT_RENDERER_ID, "WRIT renderer version is stale")
    check(
        rendering_metadata["resampling_method"] == "bicubic"
        and rendering_metadata["source_smoothing_sigma_grid_cells"]
        == module.WRIT_SOURCE_SMOOTHING_SIGMA,
        "WRIT rendering metadata should disclose its display interpolation",
    )

    source_lons = np.arange(-180.0, 180.0, 30.0)
    source_lats = np.arange(-90.0, 91.0, 30.0)
    source_values = (
        np.sin(np.deg2rad(source_lats))[:, None]
        + np.cos(np.deg2rad(source_lons))[None, :]
    )
    source_copy = source_values.copy()
    sample_lons = np.asarray([[-179.9, -45.0, 45.0, 180.1]])
    sample_lats = np.asarray([[0.0, 15.0, -15.0, 0.0]])
    sampled = seasonal._bicubic_sample_grid(
        source_lons,
        source_lats,
        source_values,
        sample_lons,
        sample_lats,
        smoothing_sigma=module.WRIT_SOURCE_SMOOTHING_SIGMA,
    )
    check(sampled.shape == sample_lons.shape and np.isfinite(sampled).all(), "bicubic WRIT sampling failed")
    check(abs(float(sampled[0, 0] - sampled[0, 3])) < 0.000001, "bicubic WRIT sampling has a dateline seam")
    check(np.array_equal(source_values, source_copy), "display smoothing must not modify the source grid")
    check(
        float(np.min(sampled)) >= float(np.min(source_values))
        and float(np.max(sampled)) <= float(np.max(source_values)),
        "bicubic WRIT sampling should not invent extrema",
    )
    psl_image_url = module._extract_psl_image_url(
        b'<img src="/img/icons/us_flag_small.png"><IMG src="/tmp/generated_map.png">'
    )
    check(psl_image_url == "https://psl.noaa.gov/tmp/generated_map.png", "PSL should select the generated map instead of the NOAA flag icon")
    psl_netcdf_url = module._extract_psl_netcdf_url(
        b'<a href="/tmp/generated_map.nc">NetCDF</a>'
    )
    check(psl_netcdf_url == "https://psl.noaa.gov/tmp/generated_map.nc", "PSL should select the generated NetCDF asset")

    mrcc_query = parse_qs(urlsplit(module._mrcc_url(djf)).query)
    check(mrcc_query["loc"] == ["ER"], "MRCC must target the NWS Eastern Region")
    check(mrcc_query["ds"] == ["19401201"] and mrcc_query["de"] == ["19410228"], "MRCC DJF dates are wrong")
    check(mrcc_query["var"] == ["snow"] and mrcc_query["calc"] == ["departure"], "MRCC snowfall departure controls are wrong")
    check(mrcc_query["stat"] == ["total"], "MRCC snowfall must use accumulated totals")
    check(mrcc_query["lakes"] == ["F"] and mrcc_query["oceans"] == ["F"], "MRCC overlays must match the current interpolated-map UI")
    check(
        mrcc_query["sids"] == ["wban coop faa ghcn cocorahs wmo icao nwsli"],
        "MRCC snowfall must request all station networks used by the current UI",
    )
    check(mrcc_query["gddB"] == ["null"] and mrcc_query["gddC"] == ["null"], "MRCC snowfall degree-day fields must match the current UI")

    station_query = parse_qs(urlsplit(module._mrcc_station_data_url(djf)).query)
    station_payload = json.loads(station_query["params"][0])
    check(
        station_payload["state"] == list(module.MRCC_SNOWFALL_SOURCE_STATES),
        "ACIS snowfall must query the NWS Eastern Region plus adjacent-frame states",
    )
    check(
        station_payload["sdate"] == "1940-12"
        and station_payload["edate"] == "1941-02",
        "ACIS snowfall DJF month controls are wrong",
    )
    station_element = station_payload["elems"][0]
    check(
        station_element["name"] == "snow"
        and station_element["interval"] == "mly"
        and station_element["duration"] == "mly"
        and station_element["normal"] == "departure"
        and station_element["reduce"] == "sum",
        "ACIS snowfall departure controls are wrong",
    )
    check(module.MRCC_SNOWFALL_REGION == (-88.5, -65.5, 31.0, 47.5), "snowfall map should use the tight clipped eastern frame")
    check(
        len(module.MRCC_SNOWFALL_DEPARTURE_PALETTE) == 21
        and module.MRCC_SNOWFALL_DEPARTURE_PALETTE[10] == "#ffffff",
        "snowfall departure palette should have 21 intervals with white at zero",
    )
    check(
        "Michigan" in module.MRCC_SNOWFALL_MASK_STATE_NAMES
        and "Tennessee" in module.MRCC_SNOWFALL_MASK_STATE_NAMES
        and "Georgia" in module.MRCC_SNOWFALL_MASK_STATE_NAMES,
        "snowfall state mask should include the adjacent Great Lakes and Southeast frame",
    )
    import cfsv2_seasonal as seasonal

    snow_spec = module._mrcc_snowfall_render_product_spec(seasonal, djf, 5)
    check(
        snow_spec["projection_standard_parallel_1"] == 33.0
        and snow_spec["projection_standard_parallel_2"] == 45.0
        and snow_spec["projection_central_longitude"] == -77.5
        and snow_spec["projection_latitude_origin"] == 39.0
        and snow_spec["projected_x_shift_fraction"] == 0.0,
        "snowfall map should use the centered eastern projection",
    )
    check(
        snow_spec["fit_frame_to_domain"] is True
        and snow_spec["domain_frame_padding_fraction"] == 0.0,
        "snowfall map should fit its frame to the selected-state land mask",
    )
    check(
        snow_spec["header_summary"]
        == "MRCC / ACIS  •  Top-5 weighted analog blend  •  NWS Eastern Region"
        and snow_spec["suppress_header_detail"] is True,
        "snowfall map should use one concise subtitle without duplicate provider text",
    )
    check(
        snow_spec["border_files"] == ("us-states.geojson",)
        and snow_spec["mask_states"] == list(module.MRCC_SNOWFALL_MASK_STATE_NAMES),
        "snowfall map should render and mask only the selected U.S. states",
    )
    check(
        snow_spec["anomaly_ticks"] == list(range(-40, 41, 4))
        and snow_spec["anomaly_bounds"]
        == list(range(-40, 0, 4)) + [-2, 2] + list(range(4, 41, 4)),
        "seasonal snowfall should keep labelled ticks separate from its white zero interval",
    )
    month_spec = module._mrcc_snowfall_render_product_spec(
        seasonal,
        {"period_type": "month", "label": "December 1997"},
        5,
    )
    check(
        month_spec["anomaly_ticks"] == list(range(-20, 21, 2))
        and month_spec["anomaly_bounds"]
        == list(range(-20, 0, 2)) + [-1, 1] + list(range(2, 21, 2)),
        "monthly snowfall should keep a dedicated white zero interval",
    )
    check(module._parse_mrcc_numeric_value("T") == 0.0, "ACIS trace snowfall should be treated as zero")
    check(module._parse_mrcc_numeric_value("M") != module._parse_mrcc_numeric_value("M"), "ACIS missing snowfall should remain missing")
    station_records = [
        {
            "meta": {"ll": [-84.0 + (index % 4) * 4.0, 31.0 + (index // 4) * 4.0]},
            "data": [["1.0"], ["2.0"], ["3.0"]],
        }
        for index in range(module.MRCC_MIN_STATIONS_FOR_COMPOSITE)
    ]
    decoded_stations = module._read_mrcc_station_values(
        json.dumps({"data": station_records}).encode("utf-8"),
        djf,
    )
    check(decoded_stations["station_count"] == module.MRCC_MIN_STATIONS_FOR_COMPOSITE, "ACIS station count is wrong")
    check(decoded_stations["values"][0] == 6.0, "ACIS DJF snowfall departures should be summed by station")

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

    timeout_calls: list[tuple[str, int]] = []

    def timed_out_mrcc_fetcher(url: str, request_timeout: int) -> bytes:
        timeout_calls.append((url, request_timeout))
        raise module.AnalogProductError("source request timed out for MRCC test endpoint")

    try:
        module._fetch_mrcc_image(
            timed_out_mrcc_fetcher,
            module._mrcc_url(djf),
            module.MRCC_GENERATION_TIMEOUT_SECONDS,
        )
    except module.AnalogProductError:
        pass
    else:
        raise AssertionError("MRCC timeout test should fail after the generation window")
    check(len(timeout_calls) == 1, "MRCC should not repeat another full generation wait after a timeout")

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
    request_timeouts: list[tuple[str, int]] = []

    original_renderer = module._render_writ_netcdf

    def fake_renderer(*, content: bytes, product_key: str, period: dict, output_path: Path, root: Path) -> dict:
        check(content == b"netcdf-test", "WRIT NetCDF payload was not passed to the renderer")
        module._write_png(output_path, png)
        return {"id": module.WRIT_RENDERER_ID, "projection": "Lambert Conformal Conic"}

    module._render_writ_netcdf = fake_renderer

    def fetcher(url: str, request_timeout: int) -> bytes:
        calls.append(url)
        request_timeouts.append((url, request_timeout))
        if url.startswith(module.PSL_MAP_URL):
            return b'<html><IMG src="/tmp/test.png"><a href="/tmp/test.nc">NetCDF</a></html>'
        if url.endswith(".nc"):
            return b"netcdf-test"
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
        check(len(calls) == 5, "PSL NetCDF and MRCC assets should each be requested once per product")
        check(
            [timeout for url, timeout in request_timeouts if url.startswith(module.MRCC_MAP_URL)]
            == [module.MRCC_GENERATION_TIMEOUT_SECONDS],
            "MRCC should receive the extended generation timeout",
        )
        module.write_manifest(output_path, first)
        for product in first["entries"][0]["products"].values():
            check(product["status"] == "ready" and (root / product["image"]).exists(), "product image was not retained")
        check(
            first["entries"][0]["products"]["psl_500mb_height_anomaly"]["rendering"]["projection"]
            == "Lambert Conformal Conic",
            "WRIT product should record the shared seasonal projection",
        )
        check(
            first["entries"][0]["products"]["psl_500mb_height_anomaly"]["dataset"] == module.WRIT_EARLY_DATASET,
            "pre-1979 WRIT product should record the fallback dataset",
        )
        check(
            first["entries"][0]["products"]["psl_2m_temperature_anomaly"]["map_region"] == "USA(CONUS)",
            "WRIT temperature product should record the CONUS map region",
        )

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

        legacy = json.loads(json.dumps(first))
        legacy["entries"][0]["products"]["psl_500mb_height_anomaly"]["provider_asset_url"] = "https://psl.noaa.gov/img/icons/us_flag_small.png"
        module.write_manifest(output_path, legacy)
        calls.clear()
        refreshed = module.build_manifest(
            root=root,
            analog_manifest_path=analog_path,
            output_manifest_path=output_path,
            output_dir=output_dir,
            fetcher=fetcher,
        )
        refreshed_psl = refreshed["entries"][0]["products"]["psl_500mb_height_anomaly"]
        check(refreshed_psl["provider_asset_url"] == "https://psl.noaa.gov/tmp/test.nc", "legacy PSL icon assets should be refreshed")
        check(refreshed_psl["provider_image_url"] == "https://psl.noaa.gov/tmp/test.png", "WRIT provider image URL should be retained")
        check(len(calls) == 2, "only legacy PSL assets should be fetched again")

        color_change = json.loads(json.dumps(first))
        color_change["entries"][0]["products"]["psl_500mb_height_anomaly"]["source_url"] = color_change["entries"][0]["products"]["psl_500mb_height_anomaly"]["source_url"].replace("colortable=default", "colortable=MPL_BrBG")
        module.write_manifest(output_path, color_change)
        calls.clear()
        refreshed_color = module.build_manifest(
            root=root,
            analog_manifest_path=analog_path,
            output_manifest_path=output_path,
            output_dir=output_dir,
            fetcher=fetcher,
        )
        refreshed_color_psl = refreshed_color["entries"][0]["products"]["psl_500mb_height_anomaly"]
        check("colortable=default" in refreshed_color_psl["source_url"], "changed PSL color tables should refresh cached maps")
        check(len(calls) == 2, "only PSL assets with changed source controls should be fetched again")

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
        module._render_writ_netcdf = original_renderer

    members = module._composite_members(
        "202612",
        [
            {"rank": 1, "label": "December 2015", "winter_year": 2016, "pattern_correlation": 0.91, "amplitude_similarity": 0.90},
            {"rank": 2, "label": "December 2005", "winter_year": 2006, "pattern_correlation": 0.80, "amplitude_similarity": 0.75},
        ],
    )
    check(len(members) == 2, "two ranked analogs should form a composite selection")
    check(abs(sum(member["weight"] for member in members) - 1.0) < 0.000001, "composite member weights must sum to one")
    check(members[0]["weight"] > members[1]["weight"], "the closer analog should receive more composite weight")
    grids = [
        SimpleNamespace(lons=[0.0, 10.0], lats=[20.0, 30.0], values=[[1.0, 2.0], [3.0, 4.0]]),
        SimpleNamespace(lons=[0.0, 10.0], lats=[20.0, 30.0], values=[[5.0, 6.0], [7.0, 8.0]]),
    ]
    averaged = module._average_writ_grids(grids, [0.75, 0.25])
    check(averaged.values[0][0] == 2.0 and averaged.values[1][1] == 5.0, "WRIT composite grid averaging is wrong")

    original_grid_fetch = module._fetch_writ_grid
    original_grid_renderer = module._render_writ_grid
    original_station_fetch = module._fetch_mrcc_station_grid
    original_station_renderer = module._render_mrcc_snowfall_grid
    try:
        def fake_grid_fetch(_fetcher, source_url: str, _timeout: int, cache: dict) -> dict:
            if source_url not in cache:
                cache[source_url] = {
                    "source_url": source_url,
                    "provider_image_url": source_url + "&image=1",
                    "provider_asset_url": source_url + "&asset=1",
                    "grid": SimpleNamespace(lons=[0.0, 10.0], lats=[20.0, 30.0], values=[[1.0, 2.0], [3.0, 4.0]]),
                }
            return cache[source_url]

        def fake_grid_renderer(*, output_path: Path, **_kwargs) -> dict:
            module._write_png(output_path, png)
            return {"id": module.WRIT_RENDERER_ID, "projection": "Lambert Conformal Conic"}

        def fake_station_fetch(_fetcher, period: dict, _timeout: int, cache: dict) -> dict:
            source_url = module._mrcc_station_data_url(period)
            if source_url not in cache:
                cache[source_url] = {
                    "source_url": source_url,
                    "provider_asset_url": source_url,
                    "station_count": module.MRCC_MIN_STATIONS_FOR_COMPOSITE,
                    "interpolation": "test station interpolation",
                    "grid": SimpleNamespace(
                        lons=[-86.0, -75.0, -64.0],
                        lats=[30.0, 40.0, 50.0],
                        values=[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [3.0, 4.0, 5.0]],
                    ),
                }
            return cache[source_url]

        def fake_station_renderer(*, output_path: Path, **_kwargs) -> dict:
            module._write_png(output_path, png)
            return {"id": module.WRIT_RENDERER_ID, "projection": "Lambert Conformal Conic"}

        module._fetch_writ_grid = fake_grid_fetch
        module._render_writ_grid = fake_grid_renderer
        module._fetch_mrcc_station_grid = fake_station_fetch
        module._render_mrcc_snowfall_grid = fake_station_renderer
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composite = module._build_composite_product(
                root=root,
                output_dir=root / "seasonal" / "analog_products",
                model="cfsv2",
                target="202612",
                target_label="December 2026",
                members=members,
                product_key="psl_500mb_height_anomaly",
                old=None,
                fetcher=lambda _url, _timeout: b"unused",
                timeout=1,
                grid_cache={},
            )
            check(composite["status"] == "ready", "WRIT composite product should render")
            check(composite["composite_count"] == 2, "WRIT composite member count is wrong")
            check((root / composite["image"]).exists(), "WRIT composite image was not written")
            retained = module._build_composite_product(
                root=root,
                output_dir=root / "seasonal" / "analog_products",
                model="cfsv2",
                target="202612",
                target_label="December 2026",
                members=members,
                product_key="psl_500mb_height_anomaly",
                old=composite,
                fetcher=lambda _url, _timeout: (_ for _ in ()).throw(module.AnalogProductError("should not fetch")),
                timeout=1,
                grid_cache={},
            )
            check(retained is composite, "unchanged WRIT composite should be reused")
            old_renderer = {**composite, "rendering": {"id": "wn2-seasonal-lcc-v1"}}
            refreshed_renderer = module._build_composite_product(
                root=root,
                output_dir=root / "seasonal" / "analog_products",
                model="cfsv2",
                target="202612",
                target_label="December 2026",
                members=members,
                product_key="psl_500mb_height_anomaly",
                old=old_renderer,
                fetcher=lambda _url, _timeout: b"unused",
                timeout=1,
                grid_cache={},
            )
            check(
                refreshed_renderer is not old_renderer
                and refreshed_renderer["rendering"]["id"] == module.WRIT_RENDERER_ID,
                "WRIT composites should rebuild when the display renderer changes",
            )
            analog_path = root / "seasonal" / "analog_z500_manifest.json"
            analog_path.parent.mkdir(parents=True, exist_ok=True)
            analog_path.write_text(
                json.dumps(
                    {
                        "source": {"climatology_years": "1981-2010"},
                        "entries": [{
                            "model": "cfsv2",
                            "model_label": "CFSv2",
                            "target": "202612",
                            "target_label": "December 2026",
                            "init_utc": "2026-08-20T06:00:00Z",
                            "results": [
                                {"rank": 1, "label": "December 2015", "winter_year": 2016, "pattern_correlation": 0.91, "amplitude_similarity": 0.90},
                                {"rank": 2, "label": "December 2005", "winter_year": 2006, "pattern_correlation": 0.80, "amplitude_similarity": 0.75},
                            ],
                        }],
                    }
                ),
                encoding="utf-8",
            )
            original_netcdf_renderer = module._render_writ_netcdf

            def fake_netcdf_renderer(*, output_path: Path, **_kwargs) -> dict:
                module._write_png(output_path, png)
                return {"id": module.WRIT_RENDERER_ID, "projection": "Lambert Conformal Conic"}

            module._render_writ_netcdf = fake_netcdf_renderer
            try:
                integrated = module.build_manifest(
                    root=root,
                    analog_manifest_path=analog_path,
                    output_manifest_path=root / "seasonal" / "analog_products_manifest.json",
                    output_dir=root / "seasonal" / "analog_products",
                    fetcher=lambda url, _timeout: (
                        b'<html><IMG src="/tmp/test.png"><a href="/tmp/test.nc">NetCDF</a></html>'
                        if url.startswith(module.PSL_MAP_URL)
                        else b"netcdf-test" if url.endswith(".nc") else png
                    ),
                )
            finally:
                module._render_writ_netcdf = original_netcdf_renderer
            integrated_entry = integrated["entries"][0]
            check(integrated_entry["composite"]["count"] == 2, "integrated manifest composite metadata is missing")
            check(set(integrated_entry["composites"]) == set(module.ANALOG_COMPOSITE_PRODUCT_KEYS), "integrated manifest composite products are missing")
            check(all(product["status"] == "ready" for product in integrated_entry["composites"].values()), "integrated composite products should be ready")
            check(
                integrated_entry["composites"][module.MRCC_SNOWFALL_COMPOSITE_KEY]["station_counts"]
                == [module.MRCC_MIN_STATIONS_FOR_COMPOSITE] * 2,
                "integrated snowfall composite station metadata is missing",
            )
    finally:
        module._fetch_writ_grid = original_grid_fetch
        module._render_writ_grid = original_grid_renderer
        module._fetch_mrcc_station_grid = original_station_fetch
        module._render_mrcc_snowfall_grid = original_station_renderer

    print("ANALOG PRODUCTS OK: source controls, retained products, amplitude weights, and WRIT/MRCC composites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

