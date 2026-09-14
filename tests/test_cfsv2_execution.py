from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cfsv2_seasonal as cf
import cfsv2_native_snow as native
from cfsv2_execution import RequestLimiter


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.args = SimpleNamespace(decode_workers=1, rolling_member=1,
            request_delay=0., force_decode=False, allow_partial_rolling=False,
            keep_source_cache=False)
        self.spec = dict(cf.get_product_spec(cf.PRODUCT_PRECIPITATION_ANOMALY))
        self.spec['grid_shape'] = (2, 2)
        self.cycles = ['2026090500', '2026090506', '2026090512']
        self.grid = cf.Grid([-100., -99.], [30., 31.], [[1., 2.], [3., 4.]])

    def decode(self):
        return cf.decode_target_ensemble(self.args, self.cycles[-1], '202612', [1],
            self.cycles, self.root/'raw', self.root/'state', 'wgrib2', self.root,
            0., self.spec, True)

    def seed(self, bad=False):
        for c in self.cycles:
            p = cf.rolling_state_path(self.root/'state', c, 1, '202612', self.spec['state_tag'])
            cf.write_grid_state(self.grid, p)
            if bad: p.write_bytes(b'broken gzip')

    def test_cache_first_preserves_converted_values_and_order(self):
        self.seed()
        with patch.object(cf, 'download_file', side_effect=AssertionError('network')), patch.object(cf, 'decode_grib', side_effect=AssertionError('decode')):
            serial = self.decode()
            self.args.decode_workers = 2
            parallel = self.decode()
        self.assertEqual(serial[0].values, self.grid.values)  # no second monthly conversion
        self.assertEqual(serial[0].values, parallel[0].values)
        self.assertEqual(list(parallel[6]), self.cycles)
        self.assertTrue(all(s['storage'] == 'retained_decoded_grid' for s in parallel[1]))

    def test_cold_parallel_equals_serial_and_is_bounded(self):
        active = peak = 0
        lock = threading.Lock()
        def decode(*a, **kw):
            nonlocal active, peak
            with lock:
                active += 1; peak = max(peak, active)
            time.sleep(.02)
            with lock: active -= 1
            return self.grid
        with patch.object(cf, 'download_file', return_value=(True, 1.)), patch.object(cf, 'decode_grib', side_effect=decode):
            self.args.force_decode = True
            serial = self.decode()
            self.args.decode_workers = 2
            parallel = self.decode()
        self.assertEqual(peak, 2)
        self.assertEqual(serial[0].values, parallel[0].values)
        self.assertEqual(serial[1], parallel[1])

    def test_bad_cache_repaired_and_force_bypasses_cache(self):
        self.seed(bad=True)
        with patch.object(cf, 'download_file', return_value=(True, 1.)), patch.object(cf, 'decode_grib', return_value=self.grid) as decoder:
            self.decode()
            self.assertEqual(decoder.call_count, 3)
            self.args.force_decode = True
            self.decode()
            self.assertEqual(decoder.call_count, 6)
        with patch.object(cf, 'download_file', side_effect=RuntimeError('offline')):
            with self.assertRaises(cf.CFSv2Error): self.decode()

    def test_monthly_memo_isolates_native_diagnostics(self):
        self.args._monthly_snow_results = {}
        result = (self.grid, [], 3, 3, 'mean', 1., {'_native_lwe': self.grid})
        params = (self.args, self.cycles[-1], '202612', [1], self.cycles,
                  self.root, self.root, 'wgrib2', self.root, 0., cf.PRODUCT_SNOWFALL_ACCUMULATION)
        with patch.object(cf, '_decode_snowfall_target_ensemble', return_value=result) as decode:
            first = cf.decode_snowfall_target_ensemble(*params)
            first[6].pop('_native_lwe')
            second = cf.decode_snowfall_target_ensemble(*params)
            self.assertIn('_native_lwe', second[6])
            self.assertEqual(decode.call_count, 1)

    def test_shared_request_spacing(self):
        from concurrent.futures import ThreadPoolExecutor
        limiter = RequestLimiter(.02)
        def one(_):
            return limiter.wait()
        with ThreadPoolExecutor(max_workers=2) as pool:
            starts = sorted(pool.map(one, range(4)))
        self.assertTrue(all(b-a >= .018 for a,b in zip(starts, starts[1:])))

    def test_native_parallel_preserves_order_and_complete_mean(self):
        meta = {'unsupported_cwas': []}
        def one(session, args, cycle, *rest):
            return self.grid, {'initialization': cycle}
        with patch.object(native, 'cached_cycle', side_effect=one), patch.object(native, 'depth_grid', side_effect=lambda g:g), patch.object(native, 'lookup', return_value=({}, meta)):
            self.args.decode_workers = 2
            result = native.decode(self.args, self.cycles[-1], '202612', [1],
                self.cycles, self.root, self.root, 'wgrib2')
        self.assertEqual(result[0].values, self.grid.values)
        self.assertEqual([s['initialization'] for s in result[1]], self.cycles)


if __name__ == '__main__': unittest.main()
