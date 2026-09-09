import unittest
import numpy as np
from cfsv2_surface_phase import operational_interval_amount


class IntervalVetoTests(unittest.TestCase):
    def test_transitions_and_sleet(self):
        snow = np.array([1, 0, 0, 0])
        rain = np.array([0, 1, 0, 0])
        ice = np.array([0, 0, 0, 1])
        sleet = np.array([0, 0, 1, 0])
        dry = np.zeros(4)
        for a, b, expected in [(snow, snow, 1), (snow, rain, 0),
                               (rain, snow, 0), (snow, ice, 0),
                               (ice, snow, 0), (snow, dry, .5),
                               (sleet, snow, .5), (snow+rain, snow, 0)]:
            with self.subTest(start=a, end=b):
                self.assertAlmostEqual(float(operational_interval_amount(25.4, a, b)), expected)

    def test_missing_flag_rejected(self):
        with self.assertRaises(ValueError):
            operational_interval_amount(25.4, [1, np.nan, 0, 0], [1, 0, 0, 0])

    def test_veto_is_per_grid_cell(self):
        start = np.array([[1, 1], [0, 0], [0, 0], [0, 0]])
        end = np.array([[0, 1], [1, 0], [0, 0], [0, 0]])
        np.testing.assert_array_equal(operational_interval_amount([25.4, 25.4], start, end), [0, 1])
