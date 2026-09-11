"""Cross-provider snowfall-depth conversion and display contracts."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cfsv2_seasonal as cf
import c3s_seasonal as c3s
import cansips_seasonal as can
import seas5_seasonal as seas
import superensemble_seasonal as superensemble
from snowfall_display import DISPLAY, convert_lwe_to_snow_depth, depth_departure


class SnowfallDisplay(unittest.TestCase):
    def test_c3s_calendar_matches_the_label_and_invalidates_old_cache(self):
        from unittest.mock import patch
        archive = c3s.CDSArchive(Path('/tmp/c3s-calendar-test'), 'ecmwf', '51')
        spec = c3s.PRODUCT_SPECS['snowfall_anomaly']
        grid = cf.Grid([0, 1], [0], [[1, 2]])
        for lead, target, cds_month in [(0, '202608', 1), (4, '202612', 5), (5, '202701', 6)]:
            with patch.object(archive, '_cached_grid', return_value=None), \
                 patch.object(archive, 'retrieve', return_value=Path('source.grib')) as retrieve, \
                 patch.object(archive, '_save_grid'), \
                 patch.object(c3s, 'grid_from_grib', return_value=grid) as decode:
                archive.grid(spec, '2026080100', target, lead)
                self.assertEqual(retrieve.call_args.args[-1], cds_month)
                self.assertEqual(decode.call_args.args[-2:], (target, cds_month))
                self.assertIn('valid_month_v2', str(archive.decoded_grid_path(spec, '2026080100', cds_month)))
        with self.assertRaisesRegex(c3s.C3SError, 'unavailable'):
            archive.grid(spec, '2026080100', '202702', 6)
        with self.assertRaisesRegex(c3s.C3SError, 'disagree'):
            archive.grid(spec, '2026080100', '202701', 4)

    def test_all_providers_use_depth_once_and_common_scale(self):
        from matplotlib.colors import BoundaryNorm, ListedColormap
        original = cf.Grid([0, 1, 2, 3, 4], [0], [[-.2, -.05, 0, .05, .2]])
        specs = [cf.get_product_spec('snowfall_anomaly'),
                 c3s.product_spec('snowfall_anomaly', 'UK Met Office'),
                 can.PRODUCT_SPECS['snowfall_anomaly'],
                 seas.PRODUCT_SPECS['snowfall_anomaly'],
                 superensemble.product_spec('snowfall_anomaly')]
        for spec in specs:
            with self.subTest(title=spec.get('title')):
                grid, display = depth_departure(original, spec, cf.SNOWFALL_ANOMALY_PALETTE)
                self.assertEqual(grid.values, [[-2, -.5, 0, .5, 2]])
                self.assertEqual(original.values, [[-.2, -.05, 0, .05, .2]])
                self.assertIs(depth_departure(grid, display, cf.SNOWFALL_ANOMALY_PALETTE)[0], grid)
                for seasonal in (False, True):
                    low, high, ticks, colors = cf.anomaly_style(display, seasonal)
                    self.assertEqual((low, high), (-10, 10))
                    self.assertEqual(ticks, list(range(-10, 11)))
                    cmap = ListedColormap(colors)
                    norm = BoundaryNorm(ticks, cmap.N)
                    for value in (-.99, 0, .99):
                        self.assertEqual(cmap(norm(value)), (1, 1, 1, 1))
                    for value in (-1.01, 1):
                        self.assertNotEqual(cmap(norm(value)), (1, 1, 1, 1))
                self.assertIn('10:1', display['header_detail'])

    def test_published_grid_is_snow_depth_and_preconverted_inputs_are_idempotent(self):
        original = cf.Grid([0, 1], [0], [[0.5, -0.25]])
        converted = convert_lwe_to_snow_depth(original)
        self.assertEqual(converted.values, [[5.0, -2.5]])
        self.assertEqual(DISPLAY["numeric_grid_units"], "inches snow")
        already_converted = dict(cf.get_product_spec("snowfall_anomaly"), snowfall_values_are_depth=True)
        same, display = depth_departure(converted, already_converted, cf.SNOWFALL_ANOMALY_PALETTE)
        self.assertIs(same, converted)
        self.assertEqual(display["snowfall_output_units"], "inches snow")

    def test_conversion_commutes_with_monthly_sum(self):
        months = [cf.Grid([0, 1], [0], [[v, -v]]) for v in (.2, .3, .5)]
        spec = cf.get_product_spec('snowfall_anomaly')
        combined = depth_departure(cf.sum_grids(months), spec, cf.SNOWFALL_ANOMALY_PALETTE)[0]
        separately = cf.sum_grids([depth_departure(g, spec, cf.SNOWFALL_ANOMALY_PALETTE)[0] for g in months])
        self.assertEqual(combined.values, separately.values)
        self.assertEqual(combined.values, [[10, -10]])

    def test_catalog_holds_unverified_images(self):
        import json
        import tempfile
        import build_seasonal_catalog as catalog
        from snowfall_display import DISPLAY
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'cfsv2' / 'snow.jpg'
            image.parent.mkdir()
            image.write_bytes(b'image fixture')
            target = {'id': 'snow', 'target_month': '202701', 'status': 'rendered',
                      'field': 'snowfall_lwe', 'units': 'in',
                      'valid_start_utc': '2027-01-01T00:00:00Z',
                      'valid_end_utc': '2027-02-01T00:00:00Z',
                      'image': 'public/seasonal/cfsv2/snow.jpg'}
            def normalize():
                return catalog._target_catalog_state('snowfall_anomaly', target,
                    site_root=root, check_assets=True, collector=catalog.IssueCollector(), path='test')[0]
            self.assertNotIn('image', normalize())
            self.assertEqual(normalize()['status'], 'pending')
            image.with_suffix('.snow.json').write_text(json.dumps(DISPLAY))
            verified = normalize()
            self.assertIn('image', verified)
            self.assertEqual(verified['display']['snow_to_liquid_ratio'], 10)
            self.assertTrue(verified['numeric_grid'].endswith('.snow.csv.gz'))


if __name__ == '__main__':
    unittest.main()
