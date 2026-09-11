"""Native snowfall unit, GRIB identity, calendar and publication regressions."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cansips_native_snow as native
import cansips_seasonal as can


def write_grib(path, *, system=4, param=173144, lead=4, date=20260901, rate=1e-8):
    import eccodes as ec
    h = ec.codes_grib_new_from_samples('regular_ll_sfc_grib1')
    try:
        for key, value in [('localDefinitionNumber', 16), ('paramId', param),
                           ('dataType', 'em'), ('systemNumber', system),
                           ('dataDate', date), ('forecastMonth', lead)]:
            ec.codes_set(h, key, value)
        count = ec.codes_get(h, 'numberOfPoints')
        ec.codes_set_values(h, np.full(count, rate))
        with Path(path).open('wb') as stream: ec.codes_write(h, stream)
    finally:
        ec.codes_release(h)


class NativeSnowTests(unittest.TestCase):
    def test_units_and_leap_calendar(self):
        for month, days in [('202612',31),('202701',31),('202702',28),('202802',29)]:
            # The source decoder remains in LWE; publication applies the single 10:1 conversion.
            np.testing.assert_allclose(native.rate_to_lwe([.0254/86400, -.0254/86400], month), [days,-days])

    def test_equal_weights_and_complete_models(self):
        a = can.Grid([0], [0], [[-2.]])
        b = can.Grid([0], [0], [[4.]])
        self.assertEqual(native.blend({'4':a,'5':b}).values, [[1.]])
        with self.assertRaises(ValueError): native.blend({'4':a})
        with self.assertRaises(ValueError): native.blend({'4':a,'5':can.Grid([1],[0],[[4.]])})

    def test_decode_real_grib_encoding_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'snow.grib'
            write_grib(path)
            grid, meta = native.decode(path,'4','2026090100',4,'202612')
            np.testing.assert_allclose(grid.values, 1e-8 * 31 * 86400 / .0254, rtol=1e-6)
            for system, init, lead in [('5','2026090100',4),('4','2026080100',4),('4','2026090100',5)]:
                with self.assertRaises(ValueError): native.decode(path,system,init,lead,'202612')
            path.write_bytes(path.read_bytes()*2)
            with self.assertRaises(ValueError): native.decode(path,'4','2026090100',4,'202612')
            write_grib(path,param=173228)  # Precipitation rate is not snowfall.
            with self.assertRaises(ValueError): native.decode(path,'4','2026090100',4,'202612')

    def test_archive_lead_mapping_and_no_legacy_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = native.NativeSnowArchive(tmp,render_only=True)
            for system, source in archive.archives.items():
                path = source.retrieve_path(native.SPEC,'2026090100',4)
                path.parent.mkdir(parents=True,exist_ok=True)
                write_grib(path,system=int(system))
            grid, source = archive.grid('2026090100',3)
            self.assertEqual([c['cds_lead'] for c in source['components']],[4,4])
            with self.assertRaises(ValueError): archive.grid('2026090100',4)
            with self.assertRaises(ValueError): archive.grid('2026090100',6)

    def test_inventory_requires_exact_system_run_variable(self):
        row = {'originating_centre':['eccc'],'system':['4'],'variable':[native.VARIABLE],
               'product_type':['ensemble_mean'],'year':['2026'],'month':['08'],'leadtime_month':['4']}
        self.assertTrue(native.available([row],'4','2026080100',4))
        self.assertFalse(native.available([row],'5','2026080100',4))
        self.assertFalse(native.available([row],'4','2026090100',4))

    def test_render_route_djf_sum_once_and_pending_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = can.build_parser().parse_args(['--product','snowfall_anomaly','--decode-only'])
            output = Path(tmp)/'out'
            def field(_, init, lead):
                return can.Grid([0],[0],[[float(lead-2)]]), {'components': []}
            with patch.object(native.NativeSnowArchive,'grid',field), patch.object(can,'load_snowfall_estimate',side_effect=AssertionError('legacy path')):
                run, errors = can.render_product_run(args,can.PRODUCT_SPECS['snowfall_anomaly'],
                    '2026090100',[3,4,5],[3,4,5],'',Path(tmp),output,[],None)
            self.assertEqual(errors,0)
            seasonal = run['targets'][0]
            self.assertEqual(seasonal['target_month'],'202612-202702')
            self.assertEqual(seasonal['quality_control']['maximum'],60.)
            self.assertEqual(seasonal['quality_control']['source_maximum'],6.)
            self.assertEqual(seasonal['quality_control']['display']['clipped_fraction'],1.)
            self.assertEqual(run['climatology']['years'],'1993-2016')
            with patch.object(native.NativeSnowArchive,'grid',side_effect=native.NotAvailable('not released')):
                run, errors = native.render_run(args,'2026090100',[3,4,5],[3,4,5],Path(tmp),output,[])
            self.assertEqual(errors,0)
            self.assertEqual(run['status'],'pending')
            self.assertTrue(all('image' not in t for t in run['targets']))

    def test_winter_periods_and_horizon(self):
        self.assertEqual(native.winter_leads('2026080100'), [4,5,6,7])
        self.assertEqual(native.winter_leads('2026090100'), [3,4,5,6])
        self.assertEqual(native.winter_leads('2026100100'), [2,3,4,5])
        self.assertEqual(native.winter_leads('2027010100'), [-1,0,1,2])
        with tempfile.TemporaryDirectory() as tmp:
            args = can.build_parser().parse_args(['--product','snowfall_anomaly','--decode-only'])
            def field(_, init, lead):
                self.assertIn(lead, range(6))
                return can.Grid([0],[0],[[float(lead)]]), {'components': []}
            with patch.object(native.NativeSnowArchive,'grid',field):
                august, errors = native.render_run(args,'2026080100',[0,1,2,3,4,5],[3,4,5],Path(tmp),Path(tmp)/'out',[])
                october, other_errors = native.render_run(args,'2026100100',[],[],Path(tmp),Path(tmp)/'out',[])
            self.assertEqual(errors + other_errors, 0)
            self.assertEqual([t['target_month'] for t in august['targets']],
                             ['202612-202702','202701-202703','202612-202701','202612','202701','202702','202703'])
            self.assertEqual(august['targets'][0]['missing_months'],['202702'])
            self.assertEqual(august['targets'][1]['missing_months'],['202702','202703'])
            self.assertTrue(all('image' not in t for t in august['targets'][:2]))
            self.assertEqual(august['targets'][2]['label'],'D+J 2026–27')
            self.assertEqual(august['targets'][2]['quality_control']['maximum'],90.)
            self.assertEqual(august['targets'][2]['aggregation'],'ensemble-mean 2-month total departure')
            self.assertEqual(october['targets'][0]['quality_control']['maximum'],90.)
            self.assertEqual(october['targets'][1]['quality_control']['maximum'],120.)

    def test_legacy_maps_removed_from_current_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'manifest.json'
            p.write_text(json.dumps({'runs':[{'id':'old','product':'snowfall_anomaly','init_utc':'2026-08-01T00:00:00Z'}]}))
            new = {'id':'new','product':'snowfall_anomaly','init_utc':'2026-09-01T00:00:00Z','method':native.METHOD}
            can.write_manifest(p,Path(tmp),new,None,4)
            self.assertEqual([r['id'] for r in json.loads(p.read_text())['runs']],['new'])


if __name__ == '__main__': unittest.main()
