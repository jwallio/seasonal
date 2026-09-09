import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cfsv2_availability as availability


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_published_cycle_is_scoped_to_products():
    payload = {"runs": [
        {"product": product, "init_utc": "2026-09-09T06:00:00Z"}
        for product in availability.STANDARD_PRODUCTS
    ] + [{"product": "snowfall_anomaly", "init_utc": "2026-09-09T12:00:00Z"}]}
    get = lambda *args, **kwargs: Response(payload)
    assert availability.published_cycle("https://example.invalid/manifest.json", availability.STANDARD_PRODUCTS, get=get) == "2026090906"
    assert availability.published_cycle("https://example.invalid/manifest.json", ("snowfall_anomaly",), get=get) == "2026090912"


def test_new_cycle_only_advances_forward():
    assert availability._is_newer("2026090912", "2026090906")
    assert not availability._is_newer("2026090906", "2026090912")
    assert availability._is_newer("2026090906", None)
    assert not availability._is_newer(None, None)


def test_inspect_reports_readiness_separately(monkeypatch):
    monkeypatch.setattr(availability, "published_cycle", lambda url, products: "2026090906")
    monkeypatch.setattr(availability.cf, "listed_cycle_inits", lambda: ["2026090912", "2026090906"])
    monkeypatch.setattr(availability, "_probe_monthly_cycle", lambda candidates: candidates[0])
    monkeypatch.setattr(availability, "_probe_snow_cycle", lambda candidates: None)
    result = availability.inspect(manifest_url="https://example.invalid/manifest.json")
    assert result["standard_ready_init"] == "2026090912"
    assert result["snow_ready_init"] is None
    assert result["standard_published_init"] == "2026090906"
