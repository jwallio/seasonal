import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cfsv2_surface_phase_build as build

class RetryTests(unittest.TestCase):
    def response(self, status, headers=None):
        r = requests.Response()
        r.status_code = status
        r.headers.update(headers or {})
        r._content = b'ok'
        return r

    def test_rate_limit_honors_retry_after(self):
        client = Mock()
        client.get.side_effect = [self.response(429, {'Retry-After': '120'}), self.response(200)]
        with patch.object(build, 'session', return_value=client), patch.object(build.time, 'sleep') as sleep:
            self.assertEqual(build.get('https://example.test'), b'ok')
            self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), 120)

    def test_missing_file_is_not_retried(self):
        client = Mock()
        client.get.return_value = self.response(404)
        with patch.object(build, 'session', return_value=client), patch.object(build.time, 'sleep') as sleep:
            with self.assertRaises(requests.HTTPError): build.get('https://example.test')
            sleep.assert_not_called()
            self.assertEqual(client.get.call_count, 1)

    def test_repeated_timeout_is_bounded(self):
        client = Mock()
        client.get.side_effect = requests.Timeout('timeout')
        with patch.object(build, 'session', return_value=client), patch.object(build.time, 'sleep'):
            with self.assertRaises(requests.Timeout): build.get('https://example.test')
            self.assertEqual(client.get.call_count, 8)

class AcquisitionContinuationTests(unittest.TestCase):
    def test_missing_cycle_does_not_stop_remaining_cycles_or_report_success(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as directory:
            argv = ['build', '--init', '2026090612', '--targets', '202702',
                    '--rolling-days', '1', '--workers', '1', '--month-pause-seconds', '0',
                    '--bundles', directory]
            with patch.object(sys, 'argv', argv), patch.object(build, 'build_month',
                    side_effect=[requests.HTTPError('404 missing'), None, None, None]) as acquire:
                with self.assertRaises(SystemExit):
                    build.main()
            self.assertEqual(acquire.call_count, 4)
            report = json.loads((Path(directory)/'acquisition-status.json').read_text())
            self.assertFalse(report['complete'])
            self.assertEqual(len(report['failures']), 1)
            self.assertEqual(len(report['completed']), 3)
