"""Calendar, caching, and workflow coverage for routine snowfall references."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_cfsv2_snow_reference as builder
import cfsv2_seasonal as cf
import cfsv2_snow_reference as ref


class BuilderTests(unittest.TestCase):
    def test_september_calendar_preserves_reviewed_years(self):
        self.assertEqual(ref.reference_years(cf.rolling_cycle_inits('2026090506', 24)), list(range(1982, 2011)))

    def test_archive_boundaries_use_declared_complete_years(self):
        self.assertEqual(ref.reference_years(cf.rolling_cycle_inits('2026010306', 24)), list(range(1983, 2011)))
        self.assertEqual(ref.reference_years(cf.rolling_cycle_inits('2026123018', 24)), list(range(1982, 2010)))

    def test_leap_day_interpolates_in_nonleap_year(self):
        moment = builder.historical_time('2028022906', 1983, 2028)
        self.assertEqual(moment, datetime(1983, 2, 28, 18))
        weights = builder.weights_for_time(moment, '06', ['19830228', '19830301'])
        self.assertEqual(weights, {'1983022806': .5, '1983030106': .5})

    def test_year_crossing_keeps_target_relative_to_anchor(self):
        self.assertEqual(builder.historical_time('2025123118', 1983, 2026), datetime(1982, 12, 31, 18))

    def test_actual_leap_year_archive_gap(self):
        # NOAA uses Feb25 then Mar02, including in leap years.
        weights = builder.weights_for_time(datetime(1984, 2, 28, 6), '06', ['19840225', '19840302'])
        self.assertEqual(weights, {'1984022506': .5, '1984030206': .5})

    def test_exact_hour_and_no_extrapolation(self):
        self.assertEqual(builder.weights_for_time(datetime(1982, 9, 3, 12), '12', ['19820829', '19820903']), {'1982090312': 1.})
        with self.assertRaises(ValueError):
            builder.weights_for_time(datetime(1982, 9, 5, 12), '12', ['19820829', '19820903'])
        with self.assertRaises(ValueError):
            builder.weights_for_time(datetime(1982, 9, 5, 12), '12', ['19820901', '19820908'])

    def test_target_month_days_and_warm_bundle(self):
        cycles = cf.rolling_cycle_inits('2026090506', 24)
        plans = [(y, {f'{y}090306': 1.}) for y in ref.reference_years(cycles)]
        sample = dict(lons=np.array([-100., -99.]), lats=np.array([30., 31.]),
                      snow_per_day=np.ones((2, 2)), sources=[])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with patch.object(builder, 'cached_sample', return_value=sample):
                builder.build_target('2026090506', '202702', cycles, plans, out, out, 1)
            grid, info = ref.load_reference(out, '2026090506', '202702', cycles, 1)
            np.testing.assert_allclose(grid.values, 28.)
            self.assertEqual(info['target_calendar_days'], 28)
            with patch.object(builder, 'cached_sample', side_effect=AssertionError('Must use cached bundle')):
                builder.build_target('2026090506', '202702', cycles, plans, out, out, 1)

    def test_derived_cache_avoids_source_fetch_and_normalizes_leap_february(self):
        source = dict(lons=np.array([-100., -99.]), lats=np.array([30., 31.]),
                      snow=np.full((2, 2), 29.), sources=[])
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(builder.source, 'cycle', return_value=source):
                sample = builder.cached_sample('1983090306', '198402', Path(temp))
            np.testing.assert_allclose(sample['snow_per_day'], 1.)
            with patch.object(builder.source, 'cycle', side_effect=AssertionError('Must use derived cache')):
                repeated = builder.cached_sample('1983090306', '198402', Path(temp))
            np.testing.assert_array_equal(repeated['snow_per_day'], sample['snow_per_day'])

    def test_workflow_uses_correction_only_for_departure(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/cfsv2-snow.yml').read_text()
        self.assertIn('cfsv2_surface_phase_build.py --reference', workflow)
        self.assertIn('reference=()', workflow)
        self.assertIn('if [[ "$product" == snowfall_anomaly ]]; then reference=(--snowfall-reference-dir .cache/cfsv2-surface-phase/reference); fi', workflow)
        self.assertIn('--surface-phase-bundle-dir .cache/cfsv2-surface-phase/months "${reference[@]}"', workflow)
        self.assertIn('--seasonal-window "$WINDOWS"', workflow)
        self.assertNotIn('--ncei-calibration', workflow)
        self.assertIn('cfsv2_surface_schedule.py verify', workflow)

    def test_missing_year_is_explicit_and_network_errors_are_fatal(self):
        cycles = cf.rolling_cycle_inits('2026090506', 24)
        plans = [(y, {f'{y}090306': 1.}) for y in ref.reference_years(cycles)]
        sample = dict(lons=np.array([-100., -99.]), lats=np.array([30., 31.]),
                      snow_per_day=np.ones((2, 2)), sources=[])
        def missing(init, target, cache):
            if init.startswith('1983'):
                raise builder.MissingHistoricalCycle('https://www.ncei.noaa.gov/missing-example')
            return sample
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with patch.object(builder, 'cached_sample', side_effect=missing):
                builder.build_target('2026090506', '202702', cycles, plans, out, out, 1)
            _, info = ref.load_reference(out, '2026090506', '202702', cycles, 1)
            self.assertEqual(len(info['historical_years']), 28)
            self.assertEqual(info['excluded_years'][0]['year'], 1983)
            with patch.object(builder, 'cached_sample', side_effect=TimeoutError('network timeout')):
                with self.assertRaises(TimeoutError):
                    builder.build_target('2026090506', '202701', cycles, plans, out, out, 1)

    def test_lead_zero_only_allowed_explicitly_for_historical_bracket(self):
        inventory = '1:0:d=1982120200:TMP:2 m above ground:0-1 month ave fcst:\n2:100:other'
        with self.assertRaises(ValueError):
            builder.source.record_range(inventory, 'TMP:2 m above ground', '1982120200', '198212')
        self.assertEqual(builder.source.record_range(inventory, 'TMP:2 m above ground',
                         '1982120200', '198212', allow_lead_zero=True), (0, 99))


if __name__ == '__main__':
    unittest.main()
