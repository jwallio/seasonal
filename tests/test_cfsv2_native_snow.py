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
        np.testing.assert_allclose(converted.values,np.where(np.isfinite(data['native_ratios']),10.,np.nan),equal_nan=True)
        self.assertEqual(np.isfinite(converted.values).sum(),925)
        self.assertEqual(len(meta['unsupported_cwas']),0)
        qc=cf.grid_quality_control('snowfall_accumulation',converted.values,units='in',field='snowfall_accumulation',seasonal=False)
        cf.require_quality_control(qc,ValueError)
        grid.lons[0]+=.01
        with self.assertRaises(ValueError):native.depth_grid(grid)

    def test_assumed_fills_preserve_measured_ratios(self):
        data,meta=native.lookup()
        with np.load(Path(cf.__file__).with_name('data')/'cfsv2_cwa_slr_v1.npz') as old:
            for name in ('native_ratios','display_ratios'):
                measured=np.isfinite(old[name])
                np.testing.assert_array_equal(data[name][measured],old[name][measured])
        self.assertEqual(meta['assumed_ratios']['PSR'],10.)
        self.assertEqual(meta['assumed_ratios']['BMX'],8.)
        self.assertEqual(meta['assumed_ratios']['MFL'],7.)
        self.assertEqual(meta['unsupported_cwas'],[])
        self.assertEqual(len(meta['measured_supported_cwas']),97)
        self.assertEqual(len(meta['assumed_ratios']),19)
        # Test geographic display points, not just configuration values.
        for lon,lat,expected in [(-112.07,33.45,10),(-86.8,33.5,8),
                                 (-80.19,25.76,7),(-90.07,29.95,7),
                                 (-95.37,29.76,7),(-98.49,29.42,8)]:
            x=np.abs(data['display_lons']-lon).argmin()
            y=np.abs(data['display_lats']-lat).argmin()
            self.assertEqual(data['display_ratios'][y,x],expected)

    def test_zero_style_applies_uniformly(self):
        from matplotlib.colors import BoundaryNorm, ListedColormap
        for seasonal in [False,True]:
            bounds,ticks,palette=native.accumulation_style(seasonal)
            self.assertIn(1.0,ticks)
            self.assertEqual(ticks, bounds)  # Every approved color transition is labeled.
            norm=BoundaryNorm(bounds,len(palette),clip=False)
            cmap=ListedColormap(palette)
            self.assertEqual(cmap(norm(0.)),(1.,1.,1.,1.))
            self.assertEqual(cmap(norm(0.999)),(1.,1.,1.,1.))
            self.assertNotEqual(cmap(norm(1.0)),(1.,1.,1.,1.))
            old=cf.absolute_style(cf.get_product_spec('snowfall_accumulation'),seasonal)
            oldnorm=BoundaryNorm(old[0],len(old[2]),clip=False)
            oldcmap=ListedColormap(old[2])
            for value in [1.,10.,50.,150.]:
                self.assertEqual(cmap(norm(value)),oldcmap(oldnorm(value)))
        data,meta=native.lookup()
        self.assertEqual(data['missing_points'].shape,(0,2))
        self.assertEqual(data['missing_offsets'].tolist(),[0])
        self.assertNotIn('florida_display_policy',meta)
        self.assertNotIn('florida_display_rings',data)
        # Positive Florida snowfall uses the same values and colors as elsewhere.
        g=native.depth_grid(self.grid(1.))
        x=np.abs(data['lons']+80.19).argmin();y=np.abs(data['lats']-25.76).argmin()
        self.assertGreater(g.values[y][x],0.)

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
