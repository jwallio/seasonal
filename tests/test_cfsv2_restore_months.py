import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cfsv2_restore_months as restore

class RestoreTests(unittest.TestCase):
    def test_old_artifact_cannot_overwrite_valid_new_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'src', Path(tmp)/'dst'
            src.mkdir(); dst.mkdir()
            name = 'surface-phase-2026090800-202702'
            (src/f'{name}.json').write_text(json.dumps(dict(initialization='2026090800',target_month='202702')))
            (src/f'{name}.npz').write_bytes(b'old')
            (dst/f'{name}.npz').write_bytes(b'new')
            with patch.object(restore, 'valid', side_effect=lambda directory,*_: directory == dst):
                self.assertEqual(restore.restore(src,dst),(0,1,0))
            self.assertEqual((dst/f'{name}.npz').read_bytes(),b'new')

    def test_invalid_artifact_is_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'src', Path(tmp)/'dst'
            src.mkdir()
            (src/'surface-phase-2026090800-202702.json').write_text(json.dumps(dict(initialization='2026090800',target_month='202702')))
            with patch.object(restore,'valid',return_value=False):
                self.assertEqual(restore.restore(src,dst),(0,0,1))
            self.assertEqual(list(dst.iterdir()),[])
