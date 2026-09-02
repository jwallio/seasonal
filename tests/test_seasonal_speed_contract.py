#!/usr/bin/env python3
"""Unit contracts for the product-matrix seasonal payload merge helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    fragment_module = load_module(
        "seasonal_fragment_contract",
        ROOT / "scripts" / "write_seasonal_fragment.py",
    )
    merge_module = load_module(
        "seasonal_payload_merge_contract",
        ROOT / "scripts" / "merge_seasonal_payloads.py",
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        incoming = root / "incoming"
        output = root / "output"
        current_init = "2026-09-01T00:00:00Z"
        prior_init = "2026-08-01T00:00:00Z"

        for product in ("500mb_height_anomaly", "2m_temperature_anomaly"):
            fragment_dir = incoming / f"artifact-{product}" / product
            assets = fragment_dir / "c3s"
            assets.mkdir(parents=True)
            (assets / f"{product}.jpg").write_bytes(product.encode("ascii"))
            manifest = {
                "kind": "c3s_seasonal_manifest",
                "runs": [
                    {
                        "id": f"{product}-prior",
                        "product": product,
                        "init_utc": prior_init,
                        "marker": "retained",
                    },
                    {
                        "id": f"{product}-current",
                        "product": product,
                        "init_utc": current_init,
                        "marker": "fresh",
                    },
                ],
            }
            manifest_path = fragment_dir / "c3s_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            fragment_module.write_fragment(
                manifest_path,
                product,
                "202609",
                fragment_dir / "fragment.json",
            )

        summary = merge_module.merge_payloads(
            incoming,
            output,
            "c3s",
            "c3s_manifest.json",
            retain_cycles=2,
        )
        payload = json.loads((output / "c3s_manifest.json").read_text(encoding="utf-8"))
        runs = {run["id"]: run for run in payload["runs"]}
        check(summary == {"fragments": 2, "products": 2, "assets": 2, "runs": 4}, "merge summary should count every product fragment and asset")
        check(set(runs) == {
            "500mb_height_anomaly-current",
            "500mb_height_anomaly-prior",
            "2m_temperature_anomaly-current",
            "2m_temperature_anomaly-prior",
        }, "merged manifest should retain current and prior product runs")
        check(all(run["marker"] == "fresh" for run_id, run in runs.items() if run_id.endswith("-current")), "current fragment runs must win over retained history")
        check((output / "c3s" / "500mb_height_anomaly.jpg").is_file(), "first product asset should be copied")
        check((output / "c3s" / "2m_temperature_anomaly.jpg").is_file(), "second product asset should be copied")

    print("SEASONAL SPEED CONTRACT OK: fragments validate current runs and merge product-scoped payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
