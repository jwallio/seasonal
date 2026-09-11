#!/usr/bin/env python3
"""Contract checks for the NOAA SFS beta2 seasonal adapter."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cfsv2_seasonal as cfsv2  # noqa: E402
import sfs_seasonal as sfs  # noqa: E402


class SFSContractTests(unittest.TestCase):
    def test_archive_schema_and_default_djf_window(self):
        self.assertEqual(sfs.SFS_EXPERIMENT, "beta2")
        self.assertEqual(sfs.SFS_FORECAST_MEMBERS, 31)
        self.assertEqual(sfs.SFS_REFORECAST_MEMBERS, 11)
        self.assertEqual(sfs.SFS_MAX_LEAD, 11)
        self.assertEqual(sfs.SFS_DEFAULT_LEADS, (3, 4, 5))
        self.assertEqual(sfs.target_month("202609", 3), "202612")
        self.assertEqual(sfs.target_month("202609", 5), "202702")
        self.assertTrue(sfs.store_url("beta2", "forecast", "202609").endswith("/experiments/beta2/forecast/202609/atm_monthly.zarr"))
        self.assertTrue(sfs.store_url("beta2", "reforecast", "202609").endswith("/experiments/beta2/reforecast/09/atm_monthly.zarr"))

    def test_products_use_canonical_discrete_styles_and_native_snowfall(self):
        expected = {
            "500mb_height_anomaly", "850mb_temperature_anomaly", "2m_temperature_anomaly",
            "precipitation_anomaly", "snowfall_anomaly", "mslp_anomaly",
        }
        self.assertEqual(set(sfs.DEFAULT_PRODUCTS), expected)
        for product in expected:
            spec = sfs.PRODUCT_SPECS[product]
            self.assertFalse(spec.get("anomaly_continuous"), product)
            self.assertIn("anomaly_ticks", spec, product)
            self.assertIn("anomaly_palette", spec, product)
        self.assertEqual(
            (sfs.PRODUCT_SPECS["850mb_temperature_anomaly"]["anomaly_min"], sfs.PRODUCT_SPECS["850mb_temperature_anomaly"]["anomaly_max"]),
            (cfsv2.PRODUCT_SPECS["850mb_temperature_anomaly"]["anomaly_min"], cfsv2.PRODUCT_SPECS["850mb_temperature_anomaly"]["anomaly_max"]),
        )
        self.assertEqual(sfs.PRODUCT_SPECS["snowfall_anomaly"]["source_variable"], "tsnowpsfc")
        self.assertNotIn("swe", sfs.PRODUCT_SPECS["snowfall_anomaly"]["source_variable"])
        snowfall = sfs.PRODUCT_SPECS["snowfall_anomaly"]
        self.assertEqual(snowfall["snowfall_input_kind"], "Native NOAA SFS TSNOWP snowfall")
        self.assertTrue(snowfall["snowfall_values_are_depth"])
        self.assertEqual(snowfall["field"], "snowfall_depth_anomaly")
        self.assertEqual(snowfall["conversion_kind"], "snowfall_lwe_to_snow_depth_10_to_1")
        self.assertEqual(snowfall["source_accumulation"], "TSNOWP monthly mean daily accumulation × calendar-month days")
        self.assertIn("calendar-month days", snowfall["conversion"])
        self.assertEqual(sfs._convert_anomaly(25.4, sfs.PRODUCT_SNOWFALL_ANOMALY, "202612"), 310.0)
        self.assertEqual(sfs._convert_anomaly(25.4, sfs.PRODUCT_SNOWFALL_ANOMALY, "202702"), 280.0)
        self.assertEqual(sfs.PRODUCT_SPECS["500mb_height_anomaly"]["region"], cfsv2.DEFAULT_REGION)
        self.assertEqual(sfs.PRODUCT_SPECS["500mb_height_anomaly_nh"]["region"], cfsv2.NORTHERN_HEMISPHERE_REGION)

    def test_operational_files_register_the_worker_and_viewer(self):
        workflow = (ROOT / ".github/workflows/sfs.yml").read_text(encoding="utf-8")
        page = (ROOT / "public/seasonal/sfs/index.html").read_text(encoding="utf-8")
        doc = (ROOT / "docs/SEASONAL_SFS.md").read_text(encoding="utf-8")
        self.assertIn('name: NOAA SFS Beta2 Seasonal Graphics', workflow)
        self.assertIn('cron: "30 18 10 * *"', workflow)
        self.assertIn("SCHEDULED_SFS_PRODUCTS:", workflow)
        self.assertIn("numcodecs", workflow)
        self.assertIn("sfs-pages-${{ github.run_id }}", workflow)
        self.assertIn("source-workflow: NOAA SFS Beta2 Seasonal Graphics", workflow)
        self.assertIn("sfs_manifest.json", page)
        self.assertIn("NOAA SFS archive", page)
        self.assertIn("BoundaryNorm", doc)
        self.assertIn("eastern Maine", doc)


if __name__ == "__main__":
    unittest.main()
