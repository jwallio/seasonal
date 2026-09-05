from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cfsv2_seasonal as cf
import cfsv2_native_snow as native


class NativeSnowTests(unittest.TestCase):
    def grid(self, value=1):
        data, _ = native.lookup()
        return cf.Grid(data['lons'].tolist(), data['lats'].tolist(), np.full(data['native_ratios'].shape,value,dtype=float).tolist())

    def test_units_leap_month_and_rollover(self):
        self.assertAlmostEqual(cf.monthly_precipitation_total_inches(self.grid(1e-6),'202802').values[0][0],29*86400e-6/25.4)
        index='1:0:d=2026120100:SRWEQ:surface:1-2 month ave fcst:\n2:40:d=2026120100:OTHER:surface:'
        self.assertEqual(native.snowfall_record(index,'2026120100','202701'),(0,39))
        with self.assertRaises(ValueError): native.snowfall_record(index,'2026120100','202702')
        with self.assertRaises(ValueError): native.snowfall_record(index.replace('surface','850 mb'),'2026120100','202701')

    def test_grid_identity_masks_and_qc(self):
        grid=self.grid(); converted=native.depth_grid(grid);data,meta=native.lookup()
        np.testing.assert_allclose(converted.values,data['native_ratios'],equal_nan=True)
        self.assertEqual(np.isfinite(converted.values).sum(),829)
        self.assertEqual(len(meta['unsupported_cwas']),19)
        qc=cf.grid_quality_control('snowfall_accumulation',converted.values,units='in',field='snowfall_accumulation',seasonal=False)
        cf.require_quality_control(qc,ValueError)
        grid.lons[0]+=.01
        with self.assertRaises(ValueError):native.depth_grid(grid)

    def test_missing_values_and_members_fail_closed(self):
        for value in [float('nan'),-1,float('inf')]:
            with self.assertRaises(ValueError):native.depth_grid(self.grid(value))
        with self.assertRaises(ValueError):native.strict_mean([self.grid()],expected=2)

    def test_nonlinearity_not_reintroduced(self):
        result=native.strict_mean([self.grid(1),self.grid(3)],expected=2)
        np.testing.assert_allclose(native.depth_grid(result).values,np.asarray(native.depth_grid(self.grid()).values)*2,equal_nan=True)
        months=[native.depth_grid(self.grid(x)) for x in [1,2,3]]
        np.testing.assert_allclose(cf.sum_grids(months).values,native.depth_grid(self.grid(6)).values,equal_nan=True)

    def test_normal_product_routes_to_native_decoder(self):
        args=SimpleNamespace(rolling_member=1)
        with patch.object(native,'decode',return_value='native') as decoder:
            value=cf.decode_snowfall_target_ensemble(args,'2026090500','202701',[1],[],Path('.'),Path('.'),'wgrib2',Path('.'),0,'snowfall_accumulation')
        self.assertEqual(value,'native');decoder.assert_called_once()
        self.assertEqual(cf.get_product_spec('snowfall_accumulation')['raw_field'],'SRWEQ:surface')
        self.assertNotIn('dependencies',cf.get_product_spec('snowfall_accumulation'))

    def test_partial_cycle_is_not_published(self):
        args=SimpleNamespace(rolling_member=1,allow_partial_rolling=True)
        with patch.object(native,'cached_cycle',side_effect=ValueError('missing native cycle')):
            with self.assertRaises(ValueError):native.decode(args,'2026090500','202701',[1],['2026090418','2026090500'],Path('.'),Path('.'),'wgrib2')


if __name__=='__main__':unittest.main()
