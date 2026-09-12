import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cfsv2_seasonal import SNOWFALL_ANOMALY_PALETTE
from snowfall_display import (
    BROAD_BOUNDS,
    COMPACT_BOUNDS,
    COMPACT_DISPLAY_PROFILE,
    DISPLAY,
    depth_departure,
    display_metadata_for_product,
)


class FakeGrid:
    def __init__(self, lons, lats, values):
        self.lons = lons
        self.lats = lats
        self.values = values


class SnowfallDisplayTests(unittest.TestCase):
    def test_default_profile_keeps_broad_scale(self):
        grid, spec = depth_departure(
            FakeGrid([0.0], [0.0], [[1.0]]),
            {"name": "snowfall_anomaly"},
            SNOWFALL_ANOMALY_PALETTE,
        )
        self.assertEqual(grid.values, [[10.0]])
        self.assertEqual(spec["anomaly_min"], -100)
        self.assertEqual(spec["anomaly_max"], 100)
        self.assertEqual(spec["anomaly_bounds"], BROAD_BOUNDS)
        self.assertEqual(DISPLAY["scale_inches"], [-100, 100])

    def test_c3s_profile_uses_one_inch_discrete_bands(self):
        source = {
            "name": "snowfall_anomaly",
            "snowfall_display_profile": COMPACT_DISPLAY_PROFILE,
        }
        grid, spec = depth_departure(
            FakeGrid([0.0], [0.0], [[0.75]]), source, SNOWFALL_ANOMALY_PALETTE
        )
        self.assertEqual(grid.values, [[7.5]])
        self.assertEqual(spec["anomaly_min"], -10)
        self.assertEqual(spec["anomaly_max"], 10)
        self.assertEqual(spec["anomaly_bounds"], COMPACT_BOUNDS)
        self.assertEqual(spec["anomaly_ticks"], COMPACT_BOUNDS)
        self.assertEqual(len(spec["anomaly_palette"]), len(COMPACT_BOUNDS) - 1)
        self.assertFalse(spec.get("anomaly_continuous", False))
        self.assertEqual(
            display_metadata_for_product(source)["scale_inches"], [-10, 10]
        )
        self.assertEqual(
            display_metadata_for_product(source)["legend_ticks_inches"], COMPACT_BOUNDS
        )

    def test_provider_specs_opt_into_compact_profile(self):
        import c3s_seasonal
        import cfsv2_seasonal

        self.assertEqual(
            cfsv2_seasonal.PRODUCT_SPECS[
                cfsv2_seasonal.PRODUCT_SNOWFALL_ANOMALY
            ]["snowfall_display_profile"],
            COMPACT_DISPLAY_PROFILE,
        )
        self.assertEqual(
            c3s_seasonal.PRODUCT_SPECS["snowfall_anomaly"][
                "snowfall_display_profile"
            ],
            COMPACT_DISPLAY_PROFILE,
        )


if __name__ == "__main__":
    unittest.main()
