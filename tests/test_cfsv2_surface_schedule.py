import unittest
from copy import deepcopy
import cfsv2_surface_schedule as schedule
from merge_cfsv2_surface_release import merge, preserve_surface, FIELD

class ScheduleTests(unittest.TestCase):
    def test_falls_back_from_incomplete_newest(self):
        plan=schedule.choose(['2026090700','2026090612'],lambda init,targets:init=='2026090612')
        self.assertEqual(plan['init'],'2026090612')
        self.assertEqual(len(plan['cycles']),21)
        self.assertEqual(plan['targets'],['202612','202701','202702','202703'])
        self.assertEqual(plan['years'],list(range(2011,2026)))
    def test_no_ready_cycle(self):
        self.assertIsNone(schedule.choose(['2026090700'],lambda *_:False))
    def test_gaps_move_out_of_window(self):
        plan=schedule.choose(['2026090812'],lambda *_:True)
        self.assertEqual(plan['excluded'],['2026090418'])
        self.assertEqual(len(plan['cycles']),23)
    def test_listing_requires_every_endpoint(self):
        class Response:
            status_code=200
            def raise_for_status(self): pass
        r=Response();init='2026090612';times=schedule.phase.endpoints('202702')
        r.text=''.join(f'<a href="pgbf{v}.01.{init}.grb2">file</a>' for v in times)
        self.assertTrue(schedule.listed_complete(init,['202702'],lambda *a,**k:r))
        r.text=r.text.replace(times[-1],'missing')
        self.assertFalse(schedule.listed_complete(init,['202702'],lambda *a,**k:r))

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
    def test_reject_incomplete_pair(self):
        with self.assertRaises(ValueError): merge({'runs':[]},{'runs':self.runs[:1]},'2026090612')

if __name__=='__main__': unittest.main()
