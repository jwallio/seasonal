#!/usr/bin/env python3
"""Ensure equivalent seasonal products render with equivalent public styles."""

import ast
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cfsv2_seasonal as cfsv2
from height_display import HEIGHT_NH_FRAME, HEIGHT_ANOMALY_STYLE
from seasonal_rendering import canonical_artifact_token
from temperature_display import (
    TEMPERATURE_ANOMALY_MAX_C,
    TEMPERATURE_ANOMALY_MIN_C,
    TEMPERATURE_ANOMALY_PALETTE,
    TEMPERATURE_ANOMALY_TICKS,
)


ADAPTERS = (
    "cfsv2_seasonal",
    "c3s_seasonal",
    "cansips_seasonal",
    "cma_cpsv3_seasonal",
    "seas5_seasonal",
    "geos_s2s3_seasonal",
    "sfs_seasonal",
    "apcc_seasonal",
)


def _temperature_style(spec: dict) -> tuple:
    return (
        spec["anomaly_min"],
        spec["anomaly_max"],
        tuple(spec["anomaly_ticks"]),
        tuple(spec["anomaly_palette"]),
        spec["region"],
    )


class SharedMapStyleTests(unittest.TestCase):
    def test_direct_seasonal_render_calls_select_the_seasonal_contract(self):
        for path in sorted((ROOT / "scripts").glob("*_seasonal.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                function = call.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else ""
                )
                if name != "render_map":
                    continue
                ancestor = call
                seasonal_branch = False
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, ast.If) and "seasonal" in ast.unparse(ancestor.test):
                        seasonal_branch = True
                    if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        break
                if not seasonal_branch:
                    continue
                seasonal_keyword = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "seasonal"),
                    None,
                )
                with self.subTest(path=path.name, line=call.lineno):
                    self.assertIsNotNone(
                        seasonal_keyword,
                        "multi-month render calls must select the seasonal style explicitly",
                    )
                    self.assertFalse(
                        isinstance(seasonal_keyword, ast.Constant)
                        and seasonal_keyword.value is False,
                    )

    def test_cma_render_wrapper_forwards_its_aggregation(self):
        module = importlib.import_module("cma_cpsv3_seasonal")
        grid = cfsv2.Grid([0.0], [0.0], [[0.0]])
        for period, expected in (("", False), ("DJF 2026–27", True)):
            with self.subTest(period=period), patch.object(module, "render_map") as renderer:
                module.render_target(
                    grid,
                    "precipitation_anomaly",
                    "2026090100",
                    "202612",
                    "3–5" if expected else 3,
                    Path("unused.jpg"),
                    [],
                    "test climatology",
                    period=period,
                )
                self.assertEqual(renderer.call_args.kwargs["seasonal"], expected)
                aggregation = "seasonal" if expected else "monthly"
                self.assertEqual(
                    renderer.call_args.kwargs["product_spec"]["canonical_style_key"],
                    f"precipitation_anomaly|{aggregation}|conus",
                )

    def test_height_domain_variants_have_unique_public_artifact_tokens(self):
        for module_name in ADAPTERS:
            module = importlib.import_module(module_name)
            normal = module.PRODUCT_SPECS["500mb_height_anomaly"]
            northern = module.PRODUCT_SPECS["500mb_height_anomaly_nh"]
            with self.subTest(module=module_name):
                self.assertNotEqual(normal["artifact_token"], northern["artifact_token"])
                self.assertEqual(canonical_artifact_token(normal), normal["artifact_token"])
                self.assertEqual(canonical_artifact_token(northern), northern["artifact_token"])
                self.assertTrue(northern["artifact_token"].replace("_", "-").endswith("-nh"))

        superensemble = importlib.import_module("superensemble_seasonal")
        normal = superensemble.product_spec("500mb_height_anomaly")
        northern = superensemble.product_spec("500mb_height_anomaly_nh")
        self.assertNotEqual(normal["artifact_token"], northern["artifact_token"])

    def test_all_provider_adapters_share_temperature_style_and_frame(self):
        expected = (
            TEMPERATURE_ANOMALY_MIN_C,
            TEMPERATURE_ANOMALY_MAX_C,
            tuple(TEMPERATURE_ANOMALY_TICKS),
            tuple(TEMPERATURE_ANOMALY_PALETTE),
            cfsv2.CONUS_REGION,
        )
        for module_name in ADAPTERS:
            module = importlib.import_module(module_name)
            for product in ("850mb_temperature_anomaly", "2m_temperature_anomaly"):
                with self.subTest(module=module_name, product=product):
                    self.assertEqual(_temperature_style(module.PRODUCT_SPECS[product]), expected)
                    self.assertNotEqual(module.PRODUCT_SPECS[product]["anomaly_palette"][10], "#ffffff")
                    self.assertNotEqual(module.PRODUCT_SPECS[product]["anomaly_palette"][11], "#ffffff")

    def test_all_provider_adapters_share_500mb_style_and_views(self):
        expected_style = (
            HEIGHT_ANOMALY_STYLE["anomaly_min"],
            HEIGHT_ANOMALY_STYLE["anomaly_max"],
            tuple(HEIGHT_ANOMALY_STYLE["anomaly_ticks"]),
            tuple(HEIGHT_ANOMALY_STYLE["anomaly_palette"]),
        )
        for module_name in ADAPTERS:
            module = importlib.import_module(module_name)
            for product in ("500mb_height_anomaly", "500mb_height_anomaly_nh"):
                with self.subTest(module=module_name, product=product):
                    spec = module.PRODUCT_SPECS[product]
                    self.assertEqual(
                        (spec["anomaly_min"], spec["anomaly_max"], tuple(spec["anomaly_ticks"]), tuple(spec["anomaly_palette"])),
                        expected_style,
                    )
                    self.assertFalse(
                        spec.get("anomaly_continuous"),
                        "500-mb anomaly fills must retain discrete contour bands",
                    )
                    if product.endswith("_nh"):
                        self.assertEqual(spec["region"], cfsv2.NORTHERN_HEMISPHERE_REGION)
                        for key, value in HEIGHT_NH_FRAME.items():
                            self.assertEqual(spec[key], value)
                    else:
                        self.assertEqual(spec["region"], cfsv2.DEFAULT_REGION)

    def test_nmme_2m_and_superensemble_inherit_the_same_contract(self):
        nmme = importlib.import_module("nmme_seasonal")
        spec = nmme.spec_for("2m_temperature_anomaly", "2m_temperature_anomaly")
        self.assertEqual(spec["anomaly_min"], TEMPERATURE_ANOMALY_MIN_C)
        self.assertEqual(spec["anomaly_max"], TEMPERATURE_ANOMALY_MAX_C)
        self.assertEqual(tuple(spec["anomaly_ticks"]), tuple(TEMPERATURE_ANOMALY_TICKS))
        self.assertEqual(tuple(spec["anomaly_palette"]), tuple(TEMPERATURE_ANOMALY_PALETTE))
        self.assertEqual(spec["region"], cfsv2.CONUS_REGION)

        superensemble = importlib.import_module("superensemble_seasonal")
        for product in ("500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly"):
            with self.subTest(module="superensemble", product=product):
                spec = superensemble.product_spec(product)
                if product == "500mb_height_anomaly":
                    self.assertEqual(tuple(spec["anomaly_ticks"]), tuple(HEIGHT_ANOMALY_STYLE["anomaly_ticks"]))
                    self.assertFalse(spec.get("anomaly_continuous"))
                    self.assertEqual(spec["region"], cfsv2.DEFAULT_REGION)
                else:
                    self.assertEqual(tuple(spec["anomaly_ticks"]), tuple(TEMPERATURE_ANOMALY_TICKS))
                    self.assertEqual(spec["region"], cfsv2.CONUS_REGION)


    def test_height_palette_darkens_toward_both_extremes(self):
        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))

        palette = HEIGHT_ANOMALY_STYLE["anomaly_palette"]
        self.assertEqual(len(palette), 24)
        negative = [luminance(c) for c in palette[:12]]
        positive = [luminance(c) for c in palette[12:]]
        self.assertTrue(all(a < b for a, b in zip(negative, negative[1:])))
        self.assertTrue(all(a > b for a, b in zip(positive, positive[1:])))


if __name__ == "__main__":
    unittest.main()
