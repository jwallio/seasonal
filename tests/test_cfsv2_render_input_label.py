import tempfile
from pathlib import Path
import unittest
import numpy as np
import cfsv2_seasonal as cf
import cfsv2_native_snow as native
from unittest.mock import patch

class RenderIntegrationTest(unittest.TestCase):
    def test_surface_accumulation_renders_through_production_caller(self):
        grid=cf.Grid(list(np.arange(-180,181,10)),list(np.arange(-90,91,10)),np.ones((19,37)).tolist())
        spec=dict(cf.PRODUCT_SPECS[cf.PRODUCT_SNOWFALL_ACCUMULATION],snowfall_input_kind='Surface precipitation type • six-hour reconstruction')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'map.png'
            # Static display geometry is read from the existing repository fixture.
            with patch.object(native,'lookup', wraps=native.lookup):
                cf.render_map(grid,'2026090800','202612',3,[1],output,False,'',[],product_spec=spec,native_lwe=grid)
            self.assertGreater(output.stat().st_size,10000)
