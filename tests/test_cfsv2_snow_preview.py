"""Offline arithmetic and display contracts for the unpublished preview."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cfsv2_seasonal as cf
import preview_cfsv2_snowfall as preview


def grid(v):
    return cf.Grid([-90., -89.], [39., 40.], [[v, v], [v, v]])


def fields(t=273.15, p=3):
    return {'t2': grid(t), 't850': grid(t), 'pr': grid(p)}


class PreviewTests(unittest.TestCase):
    def test_identical_mean_fields_have_zero_departure(self):
        for month in preview.MONTHS:
            result = preview.matched_month(fields(), fields(), month)
            np.testing.assert_array_equal(result['departure'].values, np.zeros((2, 2)))

    def test_reference_plus_departure_equals_total(self):
        result = preview.matched_month(fields(272.15, 4), fields(275.15, 2), '202701')
        np.testing.assert_allclose(np.asarray(result['reference'].values) + result['departure'].values, result['total'].values)

    def test_zero_precipitation_has_zero_total(self):
        result = preview.matched_month(fields(p=0), fields(), '202701')
        np.testing.assert_array_equal(result['total'].values, np.zeros((2, 2)))

    def test_seasonal_sum_preserves_missing_month(self):
        result = preview.strict_sum([grid(1), grid(float('nan')), grid(3)])
        self.assertTrue(np.isnan(result.values).all())

    def test_march_ratio_applied_before_seasonal_sum(self):
        months = [preview.matched_month(fields(), fields(274.15), m) for m in preview.MONTHS]
        total = preview.strict_sum([m['total'] for m in months])
        reference = preview.strict_sum([m['reference'] for m in months])
        departure = preview.strict_sum([m['departure'] for m in months])
        np.testing.assert_allclose(np.asarray(total.values) - reference.values, departure.values)

    def test_identical_total_and_reference_styles(self):
        for seasonal in (False, True):
            a = cf.absolute_style(preview.render_spec('total', seasonal), seasonal)
            b = cf.absolute_style(preview.render_spec('reference', seasonal), seasonal)
            self.assertEqual(a, b)

    def test_signed_palette_boundaries_are_complete(self):
        for seasonal in (False, True):
            spec = preview.render_spec('departure', seasonal)
            low, high, ticks, palette = cf.anomaly_style(spec, seasonal)
            self.assertEqual(len(spec['anomaly_bounds']), len(palette) + 1)
            self.assertTrue(np.all(np.diff(spec['anomaly_bounds']) > 0))
            self.assertEqual(low, -high)


if __name__ == '__main__':
    unittest.main()
