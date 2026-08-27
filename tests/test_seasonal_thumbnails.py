#!/usr/bin/env python3
"""Contract checks for generated seasonal Compare thumbnails."""

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_seasonal_thumbnails import (
    build_thumbnails,
    normalize_asset_path,
    thumbnail_asset_path,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_image(path: Path, size: tuple[int, int], color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG", quality=90)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        native_asset = Path("seasonal/cfsv2/2026082306/native.jpg")
        common_asset = Path("seasonal/common_reference/202608/common.jpg")
        ignored_asset = Path("seasonal/cfsv2/2026082306/ignored.jpg")
        write_image(site / native_asset, (1080, 900), "navy")
        write_image(site / common_asset, (900, 1080), "crimson")
        write_image(site / ignored_asset, (1080, 1080), "gray")

        manifest = {
            "runs": [
                {
                    "product": "2m_temperature_anomaly",
                    "targets": [
                        {
                            "status": "rendered",
                            "image": f"public/{native_asset.as_posix()}",
                            "comparison": {"common_1991_2020": {"image": common_asset.as_posix()}},
                        }
                    ],
                },
                {"product": "snowfall_anomaly", "targets": [{"image": ignored_asset.as_posix()}]},
            ]
        }
        manifest_path = site / "seasonal/cfsv2_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        first = build_thumbnails(site, max_width=560, quality=82)
        check(first["created"] == 2 and first["missing"] == 0, "expected two Compare thumbnails")
        native_thumbnail = site / "seasonal/thumbnails/cfsv2/2026082306/native.webp"
        common_thumbnail = site / "seasonal/thumbnails/common_reference/202608/common.webp"
        check(native_thumbnail.is_file() and common_thumbnail.is_file(), "expected deterministic thumbnail paths")
        check(not (site / "seasonal/thumbnails/cfsv2/2026082306/ignored.webp").exists(), "non-Compare products must be ignored")
        with Image.open(native_thumbnail) as image:
            check(image.format == "WEBP", "thumbnail must be WebP")
            check(max(image.size) <= 560, "thumbnail must respect the maximum dimension")
        check(native_thumbnail.stat().st_size < (site / native_asset).stat().st_size, "thumbnail should reduce transfer size")

        second = build_thumbnails(site, max_width=560, quality=82)
        check(second["created"] == 0 and second["skipped"] == 2, "existing thumbnails should be preserved")
        normalized = normalize_asset_path("public/seasonal/cfsv2/run/map.jpg")
        check(normalized is not None, "public seasonal path should normalize")
        check(str(thumbnail_asset_path(normalized)) == "seasonal/thumbnails/cfsv2/run/map.webp", "thumbnail path contract changed")
        check(normalize_asset_path("../outside.jpg") is None, "unsafe asset paths must be rejected")

    with tempfile.TemporaryDirectory() as temporary:
        flat_site = Path(temporary)
        asset = flat_site / "cfsv2" / "2026082306" / "native.jpg"
        write_image(asset, (1080, 900), "navy")
        manifest_path = flat_site / "cfsv2_manifest.json"
        manifest_path.write_text(json.dumps({
            "runs": [{
                "product": "2m_temperature_anomaly",
                "targets": [{"status": "rendered", "image": "seasonal/cfsv2/2026082306/native.jpg"}],
            }],
        }), encoding="utf-8")
        summary = build_thumbnails(flat_site, max_width=560, quality=82)
        check(summary["created"] == 1 and summary["missing"] == 0, "flat Pages tree should produce one Compare thumbnail")
        check((flat_site / "thumbnails/cfsv2/2026082306/native.webp").is_file(), "flat thumbnail should be published at the Pages root")

    print("SEASONAL THUMBNAIL CONTRACT OK: compare-only WebP generation, sizing, paths, and preservation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
