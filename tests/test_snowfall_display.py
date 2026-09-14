import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from seasonal_products import grid_quality_control
from snowfall_display import (
    DISPLAY,
    MONTHLY_BOUNDS,
    MONTHLY_PALETTE,
    MONTHLY_WHITE_BAND,
    SEASONAL_BOUNDS,
    SEASONAL_PALETTE,
    SEASONAL_WHITE_BAND,
    depth_departure,
    display_metadata_for_product,
)


class FakeGrid:
    def __init__(self, lons, lats, values):
        self.lons = lons
        self.lats = lats
        self.values = values


class SnowfallDisplayTests(unittest.TestCase):
    def test_monthly_style_is_canonical_and_provider_independent(self):
        source = {
            "name": "snowfall_anomaly",
            "snowfall_display_profile": "obsolete_provider_profile",
            "monthly_anomaly_min": -2,
            "monthly_anomaly_max": 2,
        }
        grid, spec = depth_departure(
            FakeGrid([0.0], [0.0], [[0.75]]), source, ["#000000"], seasonal=False
        )
        self.assertEqual(grid.values, [[7.5]])
        self.assertEqual((spec["anomaly_min"], spec["anomaly_max"]), (-14.0, 14.0))
        self.assertEqual(spec["anomaly_bounds"], MONTHLY_BOUNDS)
        self.assertEqual(spec["anomaly_ticks"], MONTHLY_BOUNDS)
        self.assertEqual(spec["anomaly_palette"], MONTHLY_PALETTE)
        self.assertNotIn("snowfall_display_profile", spec)
        self.assertNotIn("monthly_anomaly_min", spec)

    def test_seasonal_style_follows_aggregation_not_provider(self):
        for provider in ("cfsv2", "c3s", "seas5", "sfs"):
            with self.subTest(provider=provider):
                grid, spec = depth_departure(
                    FakeGrid([0.0], [0.0], [[-0.4]]),
                    {"name": "snowfall_anomaly", "provider": provider},
                    seasonal=True,
                )
                self.assertEqual(grid.values, [[-4.0]])
                self.assertEqual((spec["anomaly_min"], spec["anomaly_max"]), (-20.0, 20.0))
                self.assertEqual(spec["anomaly_bounds"], SEASONAL_BOUNDS)
                self.assertEqual(spec["anomaly_palette"], SEASONAL_PALETTE)

    def test_display_metadata_ignores_provider_profile(self):
        monthly = display_metadata_for_product({"provider": "anything"})
        seasonal = display_metadata_for_product({"provider": "anything"}, seasonal=True)
        self.assertEqual(monthly["scale_inches"], [-14.0, 14.0])
        self.assertEqual(monthly["scale_bounds_inches"], MONTHLY_BOUNDS)
        self.assertEqual(monthly["white_band_inches"], MONTHLY_WHITE_BAND)
        self.assertEqual(seasonal["scale_inches"], [-20.0, 20.0])
        self.assertEqual(seasonal["scale_bounds_inches"], SEASONAL_BOUNDS)
        self.assertEqual(seasonal["white_band_inches"], SEASONAL_WHITE_BAND)
        self.assertEqual(DISPLAY["snow_to_liquid_ratio"], 10.0)

    def test_quality_control_uses_aggregation_scale_and_ignores_profile(self):
        monthly = grid_quality_control(
            "snowfall_anomaly", [[0.75]], units="in", field="snowfall_lwe",
            display_profile="obsolete_provider_profile",
        )
        seasonal = grid_quality_control(
            "snowfall_anomaly", [[0.75]], units="in", field="snowfall_lwe",
            seasonal=True, display_profile="obsolete_provider_profile",
        )
        self.assertEqual((monthly["display"]["minimum"], monthly["display"]["maximum"]), (-14.0, 14.0))
        self.assertEqual(monthly["display"]["breakpoints"], MONTHLY_BOUNDS)
        self.assertEqual((seasonal["display"]["minimum"], seasonal["display"]["maximum"]), (-20.0, 20.0))
        self.assertEqual(seasonal["display"]["breakpoints"], SEASONAL_BOUNDS)

    def test_depth_input_is_not_converted_twice(self):
        grid, spec = depth_departure(
            FakeGrid([0.0], [0.0], [[7.5]]),
            {"name": "snowfall_anomaly", "snowfall_values_are_depth": True},
        )
        self.assertEqual(grid.values, [[7.5]])
        self.assertTrue(spec["snowfall_values_are_depth"])

    def test_c3s_adapter_and_common_renderer_share_idempotent_conversion(self):
        import c3s_seasonal

        source = c3s_seasonal.product_spec("snowfall_anomaly", "ECMWF")
        with patch.object(c3s_seasonal, "render_map") as render:
            c3s_seasonal.render_target(
                FakeGrid([0.0], [0.0], [[0.75]]), source,
                "2026090100", "202612", 4, Path("out.png"), [], None, "ensemble mean",
            )
        rendered_grid = render.call_args.args[0]
        rendered_spec = render.call_args.kwargs["product_spec"]
        self.assertEqual(rendered_grid.values, [[7.5]])
        self.assertTrue(rendered_spec["snowfall_values_are_depth"])
        second_grid, _ = depth_departure(rendered_grid, rendered_spec)
        self.assertEqual(second_grid.values, [[7.5]])

    def test_title_and_subtitle_use_clean_depth_units(self):
        _, spec = depth_departure(
            FakeGrid([0.0], [0.0], [[0.0]]),
            {"name": "snowfall_anomaly", "title": "CFSv2 Snowfall Departure (in snow)"},
        )
        self.assertEqual(spec["title"], "CFSv2 Snowfall Departure (in)")
        self.assertIn("x 10 = estimated snow depth", spec["header_detail"])


if __name__ == "__main__":
    unittest.main()
