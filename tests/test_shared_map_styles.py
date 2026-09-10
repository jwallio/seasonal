#!/usr/bin/env python3
"""Ensure equivalent seasonal products render with equivalent public styles."""

import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cfsv2_seasonal as cfsv2
from height_display import HEIGHT_NH_FRAME, HEIGHT_ANOMALY_STYLE
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
        spec = nmme.BASE_PRODUCTS["2m_temperature_anomaly"]
        self.assertEqual(spec["min"], TEMPERATURE_ANOMALY_MIN_C)
        self.assertEqual(spec["max"], TEMPERATURE_ANOMALY_MAX_C)
        self.assertEqual(tuple(spec["ticks"]), tuple(TEMPERATURE_ANOMALY_TICKS))
        self.assertEqual(tuple(spec["palette"]), tuple(TEMPERATURE_ANOMALY_PALETTE))
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
