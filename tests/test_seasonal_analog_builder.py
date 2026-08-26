#!/usr/bin/env python3
"""Offline contract tests for seasonal analog manifest selection."""

import gzip
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analogs = load_module("seasonal_analogs_builder_contract_core", SCRIPTS / "seasonal_analogs.py")
builder = load_module("seasonal_analogs_builder_contract", SCRIPTS / "build_seasonal_analogs.py")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_grid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("lon,lat,value\n")
        for lat, row in zip((20.0, 30.0), ((1.0, 2.0), (2.0, 4.0))):
            for lon, value in zip((0.0, 10.0), row):
                handle.write(f"{lon},{lat},{value}\n")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / "z500_anom.zarr"
        archive.mkdir()
        grid = root / "public/seasonal/cfsv2/202608/cfsv2_z500a_202701.csv.gz"
        write_grid(grid)
        for model_key in ("cfsv2", "superensemble"):
            manifest_path = root / builder.MODEL_SPECS[model_key]["manifest"]
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "id": f"{model_key}-failed-new",
                                "status": "failed",
                                "init_utc": "2026-08-24T18:00:00Z",
                                "targets": [
                                    {
                                        "status": "failed",
                                        "target_month": "202701",
                                        "numeric_grid": "public/seasonal/cfsv2/202608/cfsv2_z500a_202701.csv.gz",
                                    }
                                ],
                            },
                            {
                                "id": f"{model_key}-usable-old",
                                "status": "rendered",
                                "init_utc": "2026-08-18T18:00:00Z",
                                "targets": [
                                    {
                                        "status": "rendered",
                                        "target_month": "202701",
                                        "numeric_grid": "public/seasonal/cfsv2/202608/cfsv2_z500a_202701.csv.gz",
                                        "image": "public/seasonal/example.jpg",
                                    }
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

        historical = analogs.HistoricalSeasonalFields(
            target="202701",
            period_type="month",
            lats=np.asarray([20.0, 30.0]),
            lons=np.asarray([0.0, 10.0]),
            records=(
                analogs.HistoricalField(
                    label="January 2021",
                    winter_year=2021,
                    values=np.asarray([[1.0, 2.0], [2.0, 4.0]]),
                    sample_count=31,
                ),
            ),
        )

        def fake_loader(_path, _target, *, variable):
            check(variable == "z500_anom", "builder should request the AnalogWX variable")
            return historical

        payload = builder.build_manifest(
            root=root,
            archive_path=archive,
            output_path=root / "output.json",
            archive_loader=fake_loader,
        )
        check(len(payload["entries"]) == 2, "both scoped models should produce an entry")
        check(
            all(entry["run_id"].endswith("usable-old") for entry in payload["entries"]),
            "the newest failed run must never be selected",
        )
        check(payload["source"]["failed_runs_excluded"], "manifest should record failed-run exclusion")
        check(payload["source"]["amplitude_method"] == analogs.AMPLITUDE_METHOD, "manifest should record amplitude matching")
        check(payload["source"]["composite"]["count"] == analogs.COMPOSITE_ANALOG_COUNT, "manifest should record the composite count")
        check(payload["entries"][0]["results"][0]["rank"] == 1, "analog result rank is missing")
        check("amplitude_similarity" in payload["entries"][0]["results"][0], "analog result amplitude similarity is missing")
        check("composite_weight" in payload["entries"][0]["results"][0], "analog result composite weight is missing")

    print("SEASONAL ANALOG BUILDER OK: scoped models, numeric grids, amplitude metadata, and failed-run fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
