"""Unit/contract checks, not a claim of forecast skill or calibration."""
import argparse
import calendar
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cfsv2_surface_phase as phase
import cfsv2_seasonal as cf
from snowfall_display import depth_departure


class SurfacePhaseTests(unittest.TestCase):
    def test_exclusions_are_explicit_and_preserve_order(self):
        cycles = cf.rolling_cycle_inits("2026090612", 24)
        selected = phase.selected_cycles(cycles, "2026090418")
        self.assertEqual(len(selected), 23)
        self.assertNotIn("2026090418", selected)
        self.assertEqual(selected, sorted(selected))
        from cfsv2_native_reference import historical_cycle
        historical = [h for c in selected for h, w in historical_cycle(c, 2019, "2026090612")]
        self.assertNotIn("2019090418", historical)
        self.assertEqual(len(historical), 23)
        for excluded in ("2025090418", "2026090418,2026090418", ",".join(cycles)):
            with self.assertRaises(ValueError):
                phase.selected_cycles(cycles, excluded)

    def test_precipitation_weighting_precedes_time_average(self):
        # A dry snowy interval must not turn a rainy wet interval into snow.
        wet_rain = phase.interval_amount([25.4], [0], [0])
        dry_snow = phase.interval_amount([0], [1], [1])
        np.testing.assert_array_equal(wet_rain+dry_snow, [0])
        np.testing.assert_allclose(phase.interval_amount([25.4], [1], [0]), [.5])
        # One inch of LWE, not 21600 inches or ten inches at this boundary.
        np.testing.assert_allclose(phase.interval_amount([25.4], [1], [1]), [1])

    def test_invalid_or_missing_input_is_not_zero_filled(self):
        for p, a, b in [([-1], [0], [1]), ([1], [np.nan], [0]), ([1], [.5], [0]),
                        ([1, 2], [0], [1])]:
            with self.assertRaises(ValueError):
                phase.interval_amount(p, a, b)

    def test_full_calendar_endpoints_include_next_month_midnight(self):
        self.assertEqual(len(phase.endpoints("202702")), 113)
        self.assertEqual(phase.endpoints("202702")[-1], "2027030100")
        self.assertEqual(len(phase.endpoints("202402")), 117)

    def test_reference_normalizes_leap_year_days_and_loads_only_same_method(self):
        def month(directory, init, target):
            days = calendar.monthrange(int(target[:4]), int(target[4:]))[1]
            return dict(lons=np.array([0., 1.]), lats=np.array([0., 1.]),
                        lwe=np.full((2, 2), days)), {"grid_sha256": "a"*64}
        with tempfile.TemporaryDirectory() as d, patch.object(phase, "load_month", side_effect=month):
            phase.build_reference(d, "2026090612", "202702", ["2026090606", "2026090612"], d)
            grid, meta = phase.load_reference(d, "2026090612", "202702", ["2026090606", "2026090612"], 1)
            np.testing.assert_allclose(grid.values, 28.)
            self.assertEqual(meta["historical_cycles"], 30)
            path = Path(d)/"snowfall-reference-2026090612-202702.json"
            raw = json.loads(path.read_text()); raw["method"] = "native_srweq_operational_2011_2025_v1"
            path.write_text(json.dumps(raw))
            with self.assertRaises(cf.CFSv2Error):
                phase.load_reference(d, "2026090612", "202702", ["2026090606", "2026090612"], 1)

    def test_leap_initialization_brackets_are_combined_before_reference_load(self):
        def month(directory, init, target):
            return dict(lons=np.array([0., 1.]), lats=np.array([0., 1.]),
                        lwe=np.ones((2, 2))), {"grid_sha256": "a"*64}
        cycles = ["2024022800", "2024022900", "2024030100"]
        with tempfile.TemporaryDirectory() as d, patch.object(phase, "load_month", side_effect=month):
            phase.build_reference(d, "2024030100", "202412", cycles, d)
            grid, _ = phase.load_reference(d, "2024030100", "202412", cycles, 1)
            np.testing.assert_allclose(grid.values, 1.)

    def test_incomplete_reference_cannot_be_used(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cf.CFSv2Error):
                phase.load_reference(d, "2026090612", "202702", ["2026090612"], 1)

    def test_season_cannot_mix_srweq_and_surface_phase_references(self):
        a = dict(method=phase.METHOD, historical_years=phase.YEARS, forecast_cycles=["2026090612"],
                 years="2011-2025", label="phase")
        with self.assertRaises(cf.CFSv2Error):
            cf.seasonal_baseline_manifest([a, dict(a, method="native_srweq_operational_2011_2025_v1")], "", None)
        info = cf.seasonal_baseline_manifest([a, a], "", None)
        self.assertEqual(info["method"], phase.METHOD)

    def test_corrected_title_and_one_display_conversion(self):
        g = cf.Grid([0, 1], [0, 1], [[.1, .2], [0, -.1]])
        spec = dict(cf.get_product_spec("snowfall_anomaly"), estimated_snow_depth=True,
                    title="CFSv2 Estimated Snowfall Departure")
        rendered, style = depth_departure(g, spec, cf.SNOWFALL_ANOMALY_PALETTE)
        np.testing.assert_allclose(rendered.values, [[1, 2], [0, -1]])
        self.assertIn("Estimated", style["title"])
        self.assertEqual(style["anomaly_ticks"], list(range(-10, 11)))
        again, _ = depth_departure(rendered, style, cf.SNOWFALL_ANOMALY_PALETTE)
        np.testing.assert_allclose(again.values, rendered.values)


if __name__ == "__main__":
    unittest.main()
