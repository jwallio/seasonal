"""Regression checks for removing retired seasonal products from Pages."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "purge_retired_seasonal_products.py"


def load_purger():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("retired_seasonal_products_contract", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load retired-product purger")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    purger = load_purger()
    with tempfile.TemporaryDirectory() as temporary:
        site_root = Path(temporary) / "site"
        seasonal_root = site_root / "seasonal"
        (seasonal_root / "cfsv2").mkdir(parents=True)
        (seasonal_root / "share").mkdir(parents=True)
        (seasonal_root / "thumbnails").mkdir(parents=True)
        (site_root / "wn2").mkdir(parents=True)

        manifest_path = seasonal_root / "cfsv2_manifest.json"
        manifest_path.write_text(json.dumps({
            "runs": [
                {"product": "sea_surface_temperature_anomaly", "id": "retired"},
                {"product": "2m_temperature_anomaly", "id": "retained"},
            ],
            "product_labels": {
                "sea_surface_temperature_anomaly": "SST",
                "2m_temperature_anomaly": "2-m Temperature Anomaly",
            },
            "comparison_products": ["sea_surface_temperature_anomaly", "2m_temperature_anomaly"],
        }, indent=2), encoding="utf-8")

        retired_assets = (
            seasonal_root / "cfsv2" / "retired_sst-map.jpg",
            seasonal_root / "share" / "cfsv2_sst-map.jpg",
            seasonal_root / "thumbnails" / "cfsv2_sst-map.webp",
        )
        for asset in retired_assets:
            asset.write_bytes(b"retired")
        legacy_asset = site_root / "wn2" / "conus_sst.jpg"
        legacy_asset.write_bytes(b"legacy")

        summary = purger.purge(site_root)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert summary == {"manifests_changed": 1, "assets_removed": 3}
        assert [run["id"] for run in payload["runs"]] == ["retained"]
        assert list(payload["product_labels"]) == ["2m_temperature_anomaly"]
        assert payload["comparison_products"] == ["2m_temperature_anomaly"]
        assert all(not asset.exists() for asset in retired_assets)
        assert legacy_asset.exists(), "the legacy WeatherNext SST tree must not be purged"

    print("RETIRED SEASONAL PRODUCTS OK: manifests and seasonal assets are removed without touching legacy WeatherNext")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
