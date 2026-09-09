"""Regression checks for refreshing published, rather than unreleased, products."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from temperature_refresh_plan import plan

PRODUCT = "850mb_temperature_anomaly"


def published(init="2026-08-01T00:00:00Z"):
    return {"product": PRODUCT, "init_utc": init, "targets": [
        {"target_month": month, "lead_month": lead, "status": "rendered", "image": "map.jpg"}
        for month, lead in [("202612", 4), ("202701", 5), ("202702", 6)]
    ] + [{"target_month": "202612-202702", "lead_month": "4–6",
          "status": "rendered", "image": "season.jpg"}]}


class RefreshTests(unittest.TestCase):
    def test_unreleased_failed_run_does_not_replace_published_init(self):
        unavailable = {"product": PRODUCT, "init_utc": "2026-09-01T00:00:00Z",
                       "targets": [{"status": "failed"}]}
        result = plan({"runs": [unavailable, published()]}, "jma.yml", PRODUCT)
        self.assertEqual(result, {"product": PRODUCT, "init": "2026080100",
                                 "lead_months": "4,5,6", "seasonal_window": "4,5,6"})

    def test_monthly_initialization_contracts(self):
        for workflow in ('seas5.yml', 'geos-s2s3.yml'):
            self.assertEqual(plan({'runs': [published()]}, workflow, PRODUCT)['init'], '202608')

    def test_apcc_native_season_uses_request_month_not_issue_date(self):
        run = published('2026-08-18T00:00:00Z')
        run.update(request_target_month='202609', requested_target_window='3,4,5',
                   dataset='MME_6MONTH', resolution='2.5')
        run['targets'] = [dict(target_month='202612-202702', lead_month='6-MON',
                               image='season.jpg', status='rendered')]
        self.assertEqual(plan({'runs': [run]}, 'apcc.yml', PRODUCT),
                         dict(product=PRODUCT, init='202609', target_window='3,4,5',
                              dataset='MME_6MONTH', resolution='2.5'))

    def test_nmme_uses_plural_input(self):
        run = published("2026-08-08T00:00:00Z")
        result = plan({"runs": [run]}, "nmme.yml", PRODUCT)
        self.assertIn("products", result)
        self.assertNotIn("product", result)
        self.assertEqual(result["init"], "2026080800")

    def test_missing_month_fails_closed(self):
        run = published()
        del run["targets"][1]
        with self.assertRaises(ValueError):
            plan({"runs": [run]}, "superensemble.yml", PRODUCT)

    def test_no_published_run_does_not_guess_latest(self):
        with self.assertRaises(ValueError):
            plan({"runs": []}, "jma.yml", PRODUCT)


if __name__ == "__main__":
    unittest.main()
