#!/usr/bin/env python3
"""Provider-independent scales, boundaries, geometry, and render regressions."""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cfsv2_seasonal as cfsv2
from seasonal_rendering import (
    DOMAIN_STYLES,
    canonical_bin,
    canonical_render_style,
    canonicalize_product_spec,
    prepare_capped_values,
    public_render_style_registry,
    render_style_fingerprint,
)
from snowfall_display import MONTHLY_BOUNDS, SEASONAL_BOUNDS


def rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


class CanonicalRenderingTests(unittest.TestCase):
    def test_fixed_scales_by_product_and_aggregation(self):
        expectations = {
            ("500mb_height_anomaly", False): (-120.0, 120.0, 10.0),
            ("500mb_height_anomaly", True): (-120.0, 120.0, 10.0),
            ("850mb_temperature_anomaly", False): (-6.0, 6.0, 0.5),
            ("2m_temperature_anomaly", True): (-6.0, 6.0, 0.5),
            ("precipitation_anomaly", False): (-4.0, 4.0, 0.5),
            ("precipitation_anomaly", True): (-8.0, 8.0, 1.0),
            ("mslp_anomaly", False): (-5.0, 5.0, 0.5),
            ("mslp_anomaly", True): (-5.0, 5.0, 0.5),
        }
        for (product, seasonal), (minimum, maximum, ordinary_step) in expectations.items():
            with self.subTest(product=product, seasonal=seasonal):
                style = canonical_render_style(product, seasonal=seasonal)
                self.assertEqual((style["bounds"][0], style["bounds"][-1]), (minimum, maximum))
                self.assertEqual(len(style["bounds"]), len(style["palette"]) + 1)
                differences = np.diff(style["bounds"])
                if product != "mslp_anomaly":
                    self.assertTrue(np.allclose(differences, ordinary_step))

        self.assertEqual(canonical_render_style("snowfall_anomaly")["bounds"], MONTHLY_BOUNDS)
        self.assertEqual(canonical_render_style("snowfall_anomaly", seasonal=True)["bounds"], SEASONAL_BOUNDS)
        self.assertEqual(canonical_render_style("snowfall_accumulation")["bounds"][-1], 180)
        self.assertEqual(canonical_render_style("snowfall_accumulation", seasonal=True)["bounds"][-1], 180)

    def test_mslp_neutral_and_cap_boundaries_are_exact(self):
        style = canonical_render_style("mslp_anomaly")
        self.assertEqual(style["bounds"][9:11], [-0.5, 0.5])
        neutral = canonical_bin(style, 0.0)
        self.assertEqual(canonical_bin(style, -0.5), neutral)
        self.assertEqual(canonical_bin(style, 0.5), neutral)
        self.assertEqual(canonical_bin(style, -5.0)["kind"], "normal")
        self.assertEqual(canonical_bin(style, 5.0)["kind"], "normal")
        self.assertEqual(canonical_bin(style, -5.0001)["kind"], "under")
        self.assertEqual(canonical_bin(style, 5.0001)["kind"], "over")
        self.assertNotEqual(canonical_bin(style, -1.0)["color"], neutral["color"])
        self.assertNotEqual(canonical_bin(style, 1.0)["color"], neutral["color"])

    def test_boundary_samples_match_fixed_bin_contracts(self):
        mslp = canonical_render_style("mslp_anomaly")
        mslp_samples = {
            -5.1: ("under", -1), -5.0: ("normal", 0), -4.9: ("normal", 0),
            -0.6: ("normal", 8), -0.5: ("normal", 9), 0.0: ("normal", 9),
            0.5: ("normal", 9), 0.6: ("normal", 10), 4.9: ("normal", 18),
            5.0: ("normal", 18), 5.1: ("over", 19),
        }
        for value, expected in mslp_samples.items():
            with self.subTest(product="mslp_anomaly", value=value):
                result = canonical_bin(mslp, value)
                self.assertEqual((result["kind"], result["index"]), expected)

        temperature = canonical_render_style("2m_temperature_anomaly")
        temperature_samples = {
            -6.1: ("under", -1), -6.0: ("normal", 0), -5.999: ("normal", 0),
            -5.5001: ("normal", 0), -5.5: ("normal", 1),
            5.4999: ("normal", 22), 5.5: ("normal", 23),
            5.999: ("normal", 23), 6.0: ("normal", 23), 6.1: ("over", 24),
        }
        for value, expected in temperature_samples.items():
            with self.subTest(product="2m_temperature_anomaly", value=value):
                result = canonical_bin(temperature, value)
                self.assertEqual((result["kind"], result["index"]), expected)

        height = canonical_render_style("500mb_height_anomaly")
        height_samples = {
            -120.1: ("under", -1), -120.0: ("normal", 0),
            -119.9: ("normal", 0), -110.1: ("normal", 0),
            -110.0: ("normal", 1), 119.9: ("normal", 23),
            120.0: ("normal", 23), 120.1: ("over", 24),
        }
        for value, expected in height_samples.items():
            with self.subTest(product="500mb_height_anomaly", value=value):
                result = canonical_bin(height, value)
                self.assertEqual((result["kind"], result["index"]), expected)

    def test_every_fixed_scale_has_distinct_rectangular_overflow(self):
        for style in public_render_style_registry().values():
            with self.subTest(style=style["style_key"]):
                self.assertTrue(style["extendrect"])
                self.assertEqual(style["extendfrac"], "auto")
                self.assertIn(style["extend"], {"both", "max"})
                if style["extend"] == "both":
                    self.assertNotEqual(style["under_color"], style["palette"][0])
                self.assertNotEqual(style["over_color"], style["palette"][-1])
                self.assertEqual(canonical_bin(style, style["bounds"][-1])["kind"], "normal")
                self.assertEqual(canonical_bin(style, style["bounds"][-1] + 0.001)["kind"], "over")

    def test_provider_visual_fields_cannot_change_style_or_fingerprint(self):
        source = {
            "name": "precipitation_anomaly",
            "title": "Provider title retained",
            "region": (0, 1, 2, 3),
            "projection": "provider_projection",
            "anomaly_min": -999,
            "anomaly_max": 999,
            "anomaly_ticks": [-999, 999],
            "anomaly_palette": ["#000000"],
            "map_axes_bounds": [0.1, 0.2, 0.3, 0.4],
            "colorbar_axes_bounds": [0.2, 0.3, 0.4, 0.5],
        }
        monthly = canonicalize_product_spec(source, seasonal=False)
        seasonal = canonicalize_product_spec(source, seasonal=True)
        self.assertEqual(monthly["title"], source["title"])
        self.assertEqual((monthly["anomaly_min"], monthly["anomaly_max"]), (-4.0, 4.0))
        self.assertEqual((seasonal["anomaly_min"], seasonal["anomaly_max"]), (-8.0, 8.0))
        self.assertEqual(monthly["map_axes_bounds"], DOMAIN_STYLES["conus"]["map_axes_bounds"])
        self.assertEqual(monthly["render_style_fingerprint"], canonical_render_style("precipitation_anomaly")["fingerprint"])
        self.assertNotEqual(monthly["render_style_fingerprint"], seasonal["render_style_fingerprint"])

    def test_all_adapter_templates_resolve_the_canonical_fingerprint(self):
        adapters = (
            "cfsv2_seasonal", "c3s_seasonal", "cansips_seasonal",
            "cma_cpsv3_seasonal", "seas5_seasonal", "geos_s2s3_seasonal",
            "sfs_seasonal", "apcc_seasonal",
        )
        for module_name in adapters:
            module = importlib.import_module(module_name)
            static_is_seasonal = module_name == "apcc_seasonal"
            for product, spec in module.PRODUCT_SPECS.items():
                expected = canonical_render_style(product, seasonal=static_is_seasonal)
                if expected is None:
                    continue
                with self.subTest(module=module_name, product=product):
                    self.assertEqual(spec["canonical_style_key"], expected["style_key"])
                    self.assertEqual(spec["render_style_fingerprint"], expected["fingerprint"])

        superensemble = importlib.import_module("superensemble_seasonal")
        for product in ("500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly", "precipitation_anomaly", "snowfall_anomaly", "mslp_anomaly"):
            spec = superensemble.product_spec(product)
            self.assertEqual(spec["render_style_fingerprint"], canonical_render_style(product)["fingerprint"])
        nmme = importlib.import_module("nmme_seasonal")
        spec = nmme.spec_for("2m_temperature_anomaly", "2m_temperature_anomaly")
        self.assertEqual(spec["render_style_fingerprint"], canonical_render_style("2m_temperature_anomaly")["fingerprint"])

    def test_style_fingerprint_is_canonical_and_self_verifying(self):
        first = canonical_render_style("500mb_height_anomaly")
        second = canonical_render_style("500mb_height_anomaly")
        self.assertEqual(first, second)
        self.assertEqual(first["fingerprint"], render_style_fingerprint(first))
        changed = dict(first, tick_width=first["tick_width"] + 0.1)
        self.assertNotEqual(first["fingerprint"], render_style_fingerprint(changed))
        self.assertNotIn("provider", json.dumps(first, sort_keys=True).lower())

    def test_cap_preparation_preserves_masks_and_true_overflow(self):
        style = canonical_render_style("mslp_anomaly")
        values = np.ma.array([-5.0, 0.5, 5.0, 5.1], mask=[False, False, True, False])
        prepared = prepare_capped_values(values, style)
        self.assertTrue(np.array_equal(np.ma.getmaskarray(prepared), np.ma.getmaskarray(values)))
        self.assertLess(prepared[1], 0.5)
        self.assertEqual(prepared[3], 5.1)

    def test_integer_tick_formatter_keeps_significant_zeroes(self):
        self.assertEqual(cfsv2.format_anomaly_tick(-10, 0, "signed_trimmed"), "−10")
        self.assertEqual(cfsv2.format_anomaly_tick(10, 0, "signed_trimmed"), "+10")
        self.assertEqual(cfsv2.format_anomaly_tick(100, 0, "signed_trimmed"), "+100")
        self.assertEqual(cfsv2.format_anomaly_tick(0.5, 1, "signed_trimmed"), "+0.5")

    def test_height_without_contour_source_keeps_height_metadata(self):
        spec = {**cfsv2.PRODUCT_SPECS["500mb_height_anomaly"], "height_contours": False}
        detail = cfsv2.default_header_detail(spec, "Provider", "1991–2020", anomaly=True)
        self.assertIn("500-mb height anomaly (m)", detail)
        self.assertNotIn("Precipitation", detail)
        self.assertNotIn("Height contours", detail)

    def test_long_provider_title_wraps_inside_the_fixed_header(self):
        import c3s_seasonal

        lons = list(range(-180, 180, 12))
        lats = list(range(-90, 91, 12))
        grid = cfsv2.Grid(
            lons,
            lats,
            [[80.0 * math.sin(math.radians(lon)) for lon in lons] for _ in lats],
        )
        spec = c3s_seasonal.product_spec(
            "500mb_height_anomaly_nh",
            "C3S multi-system",
            multisystem=True,
            includes_cfsv2=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "long-header.png"
            result = cfsv2.render_map(
                grid,
                "2026091400",
                "202612",
                3,
                [1],
                output,
                True,
                "1991–2020",
                [],
                period_label="December 2026–February 2027",
                seasonal=True,
                product_spec=spec,
            )
            self.assertGreaterEqual(result["header_gap_pixels"], 16.0)
            self.assertGreaterEqual(result["header_title_fontsize"], 8.5)
            self.assertEqual(result["pixel_dimensions"], [1080, 1080])
            self.assertEqual(
                result["map_axes_bounds"],
                DOMAIN_STYLES["northern_hemisphere"]["map_axes_bounds"],
            )

    def test_actual_provider_renders_keep_identical_geometry_and_overflow(self):
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - CI installs requirements
            self.skipTest(str(exc))

        import c3s_seasonal

        lons = list(range(-180, 180, 6))
        lats = list(range(-90, 91, 6))
        values = [
            [7.2 * math.sin(math.radians(lon)) * math.cos(math.radians(lat)) for lon in lons]
            for lat in lats
        ]
        grid = cfsv2.Grid(lons, lats, values)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfs_path = root / "cfsv2.png"
            c3s_path = root / "c3s.png"
            cfs_result = cfsv2.render_map(
                grid, "2026091400", "202612", 3, [1], cfs_path, True,
                "1991–2020", [], product_spec=cfsv2.PRODUCT_SPECS["2m_temperature_anomaly"],
                footer_text="short",
            )
            provider_spec = c3s_seasonal.product_spec("2m_temperature_anomaly", "ECMWF")
            provider_spec.update(
                anomaly_min=-999, anomaly_max=999, anomaly_palette=["#000000"],
                map_axes_bounds=[0.1, 0.1, 0.2, 0.2],
            )
            c3s_result = cfsv2.render_map(
                grid, "2026090100", "202612", 4, [1], c3s_path, True,
                "1991–2020", [], product_spec=provider_spec,
                footer_text="A deliberately long provider footer cannot resize the canonical map or legend.",
            )
            for key in ("style_key", "style_fingerprint", "pixel_dimensions", "map_axes_bounds", "colorbar_axes_bounds"):
                self.assertEqual(cfs_result[key], c3s_result[key], key)
            style = canonical_render_style("2m_temperature_anomaly")
            with Image.open(cfs_path) as cfs_image, Image.open(c3s_path) as c3s_image:
                self.assertEqual(cfs_image.size, (1080, 1080))
                self.assertEqual(c3s_image.size, (1080, 1080))
                colors = {color for _, color in cfs_image.convert("RGB").getcolors(maxcolors=2_000_000)}
            self.assertIn(rgb(style["under_color"]), colors)
            self.assertIn(rgb(style["over_color"]), colors)

    def test_actual_snow_renders_keep_fixed_land_frame_and_crop(self):
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - CI installs requirements
            self.skipTest(str(exc))

        import c3s_seasonal

        lons = list(range(-180, 180, 6))
        lats = list(range(-90, 91, 6))
        values = [
            [1.6 * math.sin(math.radians(lon * 2)) * math.cos(math.radians(lat)) for lon in lons]
            for lat in lats
        ]
        complete = cfsv2.Grid(lons, lats, values)
        incomplete = cfsv2.Grid(
            lons, lats,
            [[value if lon > -105 else float("nan") for lon, value in zip(lons, row)] for row in values],
        )
        state_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "California"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-125, 24], [-67, 24], [-67, 50], [-125, 50], [-125, 24],
                ]]},
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            borders = root / "us-states.geojson"
            borders.write_text(json.dumps(state_geojson), encoding="utf-8")
            cfs_path = root / "cfsv2-snow.png"
            c3s_path = root / "c3s-snow.png"
            cfs_result = cfsv2.render_map(
                complete, "2026091400", "202612", 3, [1], cfs_path, True,
                "1991–2020", [borders], product_spec=cfsv2.PRODUCT_SPECS["snowfall_anomaly"],
            )
            provider_spec = c3s_seasonal.product_spec("snowfall_anomaly", "ECMWF")
            provider_spec["domain_frame_padding_fraction"] = 0.25
            c3s_result = cfsv2.render_map(
                incomplete, "2026090100", "202612", 4, [1], c3s_path, True,
                "1991–2020", [borders], product_spec=provider_spec,
                footer_text="Provider availability and text cannot alter the fitted lower-48 frame.",
            )
            for key in ("style_key", "style_fingerprint", "pixel_dimensions", "map_axes_bounds", "colorbar_axes_bounds"):
                self.assertEqual(cfs_result[key], c3s_result[key], key)
            with Image.open(cfs_path) as cfs_image, Image.open(c3s_path) as c3s_image:
                self.assertEqual(cfs_image.size, (1080, 845))
                self.assertEqual(c3s_image.size, (1080, 845))


if __name__ == "__main__":
    unittest.main()
