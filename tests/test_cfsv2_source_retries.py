"""Regression checks for retry handling without live NOAA traffic."""
import ast
from pathlib import Path
import re
import types
import unittest
import requests

source = Path(__file__).resolve().parents[1] / 'scripts/cfsv2_surface_schedule.py'
node = next(n for n in ast.parse(source.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == 'listed_complete')
namespace = dict(requests=requests, re=re,
    time=types.SimpleNamespace(sleep=lambda _: None),
    cf=types.SimpleNamespace(NOMADS_ROOT='https://example.test'),
    phase=types.SimpleNamespace(endpoints=lambda _: ['2027010100']))
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)

def response(code, body=''):
    value = requests.Response()
    value.status_code = code
    value._content = body.encode()
    return value

class RetryTests(unittest.TestCase):
    def check(self, responses):
        seq = iter(responses)
        pauses = []
        result = namespace['listed_complete']('2026091006', ['202701'],
            get=lambda *a, **k: next(seq), sleep=pauses.append)
        return result, pauses

    def test_recovers(self):
        ready = response(200, '<a href="pgbf2027010100.01.2026091006.grb2">x</a>')
        self.assertEqual(self.check([response(403), response(503), ready]), (True, [15,30]))

    def test_missing_is_not_retried(self):
        self.assertEqual(self.check([response(404)]), (False, []))

    def test_incomplete_is_not_ready(self):
        self.assertEqual(self.check([response(200)]), (False, []))

    def test_exhaustion(self):
        self.assertEqual(self.check([response(429)] * 3), (False, [15,30]))
