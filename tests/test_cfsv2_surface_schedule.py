import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import cfsv2_surface_schedule as schedule
import cfsv2_surface_phase as phase
from merge_cfsv2_surface_release import merge, preserve_surface, FIELD

class ScheduleTests(unittest.TestCase):
    def test_skip_requires_exact_method_and_rendering(self):
        plan=schedule.choose(['2026090812'], lambda *_: True)
        plan['render_signature']='current-code'
        self.assertTrue(schedule.same_published_plan(plan, deepcopy(plan)))
        for key in ('init', 'method', 'cycles', 'targets', 'render_signature'):
            old=deepcopy(plan);old[key]='different'
            self.assertFalse(schedule.same_published_plan(plan, old))
        old=deepcopy(plan);del old['render_signature']
        self.assertFalse(schedule.same_published_plan(plan, old))
        self.assertFalse(schedule.same_published_plan(plan, None))

    def test_falls_back_from_incomplete_newest(self):
        plan=schedule.choose(['2026090700','2026090612'],lambda init,targets:init=='2026090612')
        self.assertEqual(plan['init'],'2026090612')
        self.assertEqual(len(plan['cycles']),22)
        self.assertEqual(plan['targets'],['202612','202701','202702','202703'])
        self.assertEqual(plan['years'],list(range(2011,2026)))
    def test_no_ready_cycle(self):
        self.assertIsNone(schedule.choose(['2026090700'],lambda *_:False))
    def test_recoverable_archive_gap_is_included(self):
        plan=schedule.choose(['2026090812'],lambda *_:True)
        self.assertEqual(plan['excluded'],[])
        self.assertEqual(len(plan['cycles']),24)
    def test_listing_requires_every_endpoint(self):
        class Response:
            status_code=200
            def raise_for_status(self): pass
        r=Response();init='2026090612';times=schedule.phase.endpoints('202702')
        r.text=''.join(f'<a href="pgbf{v}.01.{init}.grb2">file</a>' for v in times)
        self.assertTrue(schedule.listed_complete(init,['202702'],lambda *a,**k:r))
        r.text=r.text.replace(times[-1],'missing')
        output=StringIO()
        with redirect_stdout(output):
            self.assertFalse(schedule.listed_complete(init,['202702'],lambda *a,**k:r))
        self.assertIn('1/113 required pgbf files missing', output.getvalue())
        self.assertIn(f'first=pgbf{times[-1]}.01.{init}.grb2', output.getvalue())

    def test_listing_treats_transient_source_errors_as_not_ready(self):
        class ErrorResponse:
            status_code = 403
            text = ''
            def raise_for_status(self):
                raise schedule.requests.HTTPError('source is still indexing')
        self.assertFalse(schedule.listed_complete('2026090918', ['202701'], lambda *a, **k: ErrorResponse()))

    def test_surface_phase_label_keeps_requested_denominator(self):
        args = SimpleNamespace(
            rolling_member=1,
            allow_partial_rolling=False,
            product='snowfall_anomaly',
            surface_phase_exclude_cycles='2026090418',
            surface_phase_bundle_dir=Path('.cache/test-surface-phase'),
        )
        cycles = ['2026090412', '2026090418', '2026090500']
        month = ({'lons': np.array([0.0]), 'lats': np.array([0.0]), 'lwe': np.array([[1.0]])},
                 {'initialization': 'cycle', 'target_month': '202612', 'method': phase.METHOD,
                  'grid_sha256': 'hash', 'intervals': 1})
        with patch.object(phase, 'load_month', return_value=month), \
             patch('cfsv2_native_snow.strict_mean', side_effect=lambda grids, expected: grids[0]):
            result = phase.decode(args, '2026091012', '202612', [1], cycles)
        self.assertEqual(result[2:5], (2, 3, '2/3-cycle matched mean'))

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.runs=[dict(id=f'cfsv2-2026090612-{p}',product=p,raw_field=FIELD,init_utc='2026-09-06T12:00:00Z',targets=[dict(ensemble_members=21)]) for p in ('snowfall_anomaly','snowfall_accumulation')]
    def test_merge_preserves_weather_and_used_count(self):
        weather=dict(id='weather',product='precipitation_anomaly')
        result=merge({'runs':[weather]},{'runs':self.runs},'2026090612')
        self.assertIn(weather,result['runs'])
        self.assertEqual(result['runs'][0]['ensemble_members'],21)
    def test_no_regression(self):
        newer=deepcopy(self.runs[0]);newer['init_utc']='2026-09-07T12:00:00Z'
        with self.assertRaises(ValueError): merge({'runs':[newer]},{'runs':self.runs},'2026090612')
    def test_general_publisher_cannot_replace_corrected_snow(self):
        native=deepcopy(self.runs[0]);native['raw_field']='SRWEQ'
        result=preserve_surface({'runs':self.runs},{'runs':[native]})
        self.assertEqual(result['runs'],self.runs)
    def test_snowfall_payload_preserves_all_weather_products(self):
        weather=dict(id='weather-500',product='500mb_height_anomaly',init_utc='2026-09-06T12:00:00Z')
        incoming=deepcopy(self.runs)
        for run in incoming:
            run['id']=run['id'].replace('2026090612', '2026090806')
            run['init_utc']='2026-09-08T06:00:00Z'
        result=preserve_surface({'runs':[weather, *self.runs]}, {'runs':incoming})
        self.assertIn(weather, result['runs'])
        self.assertEqual(
            {run['id'] for run in result['runs']},
            {weather['id'], *(run['id'] for run in self.runs), *(run['id'] for run in incoming)},
        )
    def test_reject_incomplete_pair(self):
        with self.assertRaises(ValueError): merge({'runs':[]},{'runs':self.runs[:1]},'2026090612')

if __name__=='__main__': unittest.main()
