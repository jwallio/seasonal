#!/usr/bin/env python3
"""Contract checks for wall.cloud-branded seasonal share images."""

import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_seasonal_share_images import (
    BRAND_BACKGROUND,
    build_share_images,
    normalize_asset_path,
    share_asset_path,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_image(path: Path, size: tuple[int, int], color: str, image_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format=image_format)


def write_manifest(path: Path, images: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"runs": [{"targets": [{"image": image} for image in images]}]}),
        encoding="utf-8",
    )


def main() -> int:
    check(
        normalize_asset_path("public/seasonal/cfsv2/run/map.jpg")
        == PurePosixPath("seasonal/cfsv2/run/map.jpg"),
        "public seasonal paths should normalize to one logical asset root",
    )
    check(
        normalize_asset_path("analog_products/model/map.png")
        == PurePosixPath("seasonal/analog_products/model/map.png"),
        "flat analog paths should normalize into the seasonal asset root",
    )
    check(
        share_asset_path(PurePosixPath("seasonal/cfsv2/run/map.jpg"))
        == PurePosixPath("seasonal/share/cfsv2/run/map.jpg"),
        "share paths should retain the source hierarchy",
    )

    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        native = site / "seasonal/cfsv2/run/map.jpg"
        analog = site / "seasonal/analog_products/model/map.png"
        retired = site / "seasonal/cfsv2/run/retired-sst.jpg"
        write_image(native, (120, 90), "navy", "JPEG")
        write_image(analog, (100, 80), "white", "PNG")
        write_image(retired, (90, 70), "red", "JPEG")
        write_manifest(
            site / "seasonal/cfsv2_manifest.json",
            ["public/seasonal/cfsv2/run/map.jpg", "analog_products/model/map.png"],
        )
        manifest_path = site / "seasonal/cfsv2_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runs"].append(
            {
                "product": "sea_surface_temperature_anomaly",
                "targets": [{"image": "public/seasonal/cfsv2/run/retired-sst.jpg"}],
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        first = build_share_images(site)
        check(first["created"] == 2 and first["missing"] == 0, "expected two branded share images")
        check(not (site / "seasonal/share/cfsv2/run/retired-sst.jpg").exists(), "retired SST assets must not receive branded derivatives")
        native_share = site / "seasonal/share/cfsv2/run/map.jpg"
        analog_share = site / "seasonal/share/analog_products/model/map.png"
        check(native_share.is_file() and analog_share.is_file(), "branded images should use deterministic paths")
        with Image.open(native_share) as image:
            check(image.size == (120, 120), "branding should add a footer without cropping the source image")
            footer_pixel = image.convert("RGB").getpixel((2, image.height - 2))
            check(all(abs(left - right) <= 8 for left, right in zip(footer_pixel, BRAND_BACKGROUND)), "share footer should use wall.cloud styling")
        second = build_share_images(site)
        check(second["refreshed"] == 2, "repeat publication should refresh branded derivatives")

    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary)
        source = site / "cfsv2/run/map.jpg"
        write_image(source, (100, 100), "teal", "JPEG")
        write_manifest(site / "cfsv2_manifest.json", ["seasonal/cfsv2/run/map.jpg"])
        summary = build_share_images(site)
        check(summary["created"] == 1, "flat Pages output should produce one share image")
        check((site / "share/cfsv2/run/map.jpg").is_file(), "flat Pages share path changed")

    print("SEASONAL SHARE IMAGE CONTRACT OK: branded, uncropped nested and flat derivatives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

