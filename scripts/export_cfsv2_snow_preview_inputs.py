"""Extract a bounded, read-only snapshot from retained CFS cache; no NOAA calls."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

import numpy as np
import cfsv2_seasonal as cf


def contained(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to((root / ".cache/cfsv2").resolve()):
        raise ValueError("Input is outside the CFS cache")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", default="2026090412")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Use a new preview output directory")
    url = "https://jwallio.github.io/seasonal/cfsv2_manifest.json"
    with urllib.request.urlopen(url, timeout=45) as response:
        manifest = json.load(response)
    run = next(r for r in manifest["runs"] if r["id"] == f"cfsv2-{args.init}-snowfall_anomaly")
    output.mkdir(parents=True)
    (output / "source-run.json").write_text(json.dumps(run, indent=2))
    records = {"init": args.init, "manifest_url": url, "months": {}, "files": []}
    products = [cf.PRODUCT_2M_TEMPERATURE_ANOMALY, cf.PRODUCT_850_TEMPERATURE_ANOMALY, cf.PRODUCT_PRECIPITATION_ANOMALY]
    for month in ("202701", "202702", "202703"):
        entry = next(t for t in run["targets"] if t["target_month"] == month)
        if not entry["ensemble_complete"] or entry["ensemble_members"] != 24:
            raise ValueError("Preview requires all 24 source cycles")
        fields = {}
        for product in products:
            files = [f for f in entry["source_files"] if f["derived_dependency"] == product]
            grids = {}
            for item in files:
                path = contained(root, item["state_file"])
                grid = cf.read_grid_state(path)
                if not np.isfinite(np.asarray(grid.values)).all():
                    raise ValueError(f"Incomplete input grid: {path.name}")
                grids[item["initialization"]] = grid
                records["files"].append({"path": item["state_file"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            if len(grids) != 24:
                raise ValueError("Duplicate or missing source cycle")
            fields[product] = grids
        t2 = fields[products[0]]
        t850 = {key: cf.regrid_nearest(grid, t2[key].lons, t2[key].lats, "preview T850") for key, grid in fields[products[1]].items()}
        pr = fields[products[2]]
        cycle_lwe, _ = cf.derive_snowfall_lwe_grid(t2, t850, pr, month)
        cf.write_grid_state(cycle_lwe, output / f"{month}-cycle-lwe.csv.gz")
        for name, grids in (("t2", t2), ("t850", t850), ("pr", pr)):
            cf.write_grid_state(cf.mean_grids(list(grids.values())), output / f"{month}-forecast-{name}.csv.gz")
        for name, product in zip(("t2", "t850", "pr"), products):
            metadata = next(d for d in entry["baseline"]["dependencies"] if d["product"] == product)
            if metadata.get("fallback") or metadata["used_initialization"] != args.init:
                raise ValueError("Preview requires the exact calibration initialization")
            spec = cf.get_product_spec(product)
            relative = metadata["file"] + f".{spec['cache_tag']}_baseline.csv"
            path = contained(root, relative)
            # Decoded calibration CSV is raw; rolling states are already converted.
            grid = cf.prepare_product_grid(cf.read_grid_csv(path), spec, month)
            if not np.isfinite(np.asarray(grid.values)).all():
                raise ValueError("Incomplete calibration grid")
            cf.write_grid_state(grid, output / f"{month}-reference-{name}.csv.gz")
            records["files"].append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        records["months"][month] = {"cycles": 24, "baseline": entry["baseline"]}
        print(f"Extracted {month}: 24 paired cycles and three exact calibration fields", flush=True)
    (output / "provenance.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
