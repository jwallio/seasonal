#!/usr/bin/env python3
"""Migrate legacy NH 500-mb image paths before strict catalog validation."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path, PurePosixPath


def asset_path(site_root: Path, image: str) -> Path:
    path = PurePosixPath(image.lstrip("/"))
    parts = path.parts
    if parts[:2] == ("public", "seasonal"):
        parts = parts[2:]
    elif parts[:1] == ("seasonal",):
        parts = parts[1:]
    return site_root.joinpath(*parts)


def migrate_manifest(manifest_path: Path, site_root: Path) -> tuple[int, int]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = 0
    missing = 0
    for run in payload.get("runs", []):
        if run.get("product") != "500mb_height_anomaly_nh":
            continue
        for target in run.get("targets", []):
            image = target.get("image")
            if not isinstance(image, str) or "_z500_" not in image or "_z500-nh_" in image:
                continue
            replacement = image.replace("_z500_", "_z500-nh_", 1)
            source = asset_path(site_root, image)
            destination = asset_path(site_root, replacement)
            if not source.is_file():
                missing += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            for sidecar in source.parent.glob(source.name + ".*"):
                shutil.copy2(sidecar, destination.parent / (destination.name + sidecar.name[len(source.name):]))
            target["image"] = replacement
            changed += 1
    if changed:
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    total_changed = 0
    total_missing = 0
    for manifest in sorted(args.site.glob("*_manifest.json")):
        changed, missing = migrate_manifest(manifest, args.site)
        total_changed += changed
        total_missing += missing
    print(f"Legacy NH height migration: {total_changed} targets rewritten; {total_missing} assets missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
