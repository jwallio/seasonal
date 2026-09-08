import unittest
from unittest.mock import patch, Mock
import cfsv2_surface_phase_build as build

class ResponseRetryTests(unittest.TestCase):
    def response(self, status, content):
        r=Mock(status_code=status,content=content,headers={})
        r.raise_for_status.return_value=None
        return r
    def test_html_index_retried(self):
        good='\n'.join(f'{i}:{i*10}:d=2026090718:{p.upper()}:surface:x' for i,p in enumerate(build.phase.PARAMETERS)).encode()
        client=Mock();client.get.side_effect=[self.response(200,b'<html>temporarily unavailable</html>'),self.response(200,good)]
        with patch.object(build,'session',return_value=client),patch.object(build.time,'sleep') as sleep:
            self.assertEqual(build.get('https://example.test/file.idx'),good)
            sleep.assert_called_once_with(60)
    def test_ignored_range_retried(self):
        client=Mock();client.get.side_effect=[self.response(200,b'ignored'),self.response(206,b'GRIB')]
        with patch.object(build,'session',return_value=client),patch.object(build.time,'sleep'):
            self.assertEqual(build.get('https://example.test/file',0,3),b'GRIB')
    def test_persistent_bad_index_rejected(self):
        client=Mock();client.get.return_value=self.response(200,b'bad index')
        with patch.object(build,'session',return_value=client),patch.object(build.time,'sleep'):
            with self.assertRaises(build.SourceResponseError):build.get('https://example.test/file.idx')
        self.assertEqual(client.get.call_count,8)
