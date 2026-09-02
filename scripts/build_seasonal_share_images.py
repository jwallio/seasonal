#!/usr/bin/env python3
"""Build wall.cloud-branded copies of every seasonal map referenced by a manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from seasonal_products import is_retired_product
except ModuleNotFoundError:  # Imported as ``scripts.build_seasonal_share_images`` in tests.
    from scripts.seasonal_products import is_retired_product


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
BRAND_BACKGROUND = (8, 20, 27)
BRAND_FOREGROUND = (243, 247, 249)
BRAND_ACCENT = (89, 212, 192)


def normalize_asset_path(value: Any) -> PurePosixPath | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text or "://" in text:
        return None
    if text.startswith("public/"):
        text = text[len("public/") :]
    text = text.lstrip("/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    if path.parts and path.parts[0] == "share":
        return None
    if not path.parts or path.parts[0] != "seasonal":
        path = PurePosixPath("seasonal", path)
    if "share" in path.parts or "thumbnails" in path.parts:
        return None
    return path


def iter_image_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        status = str(value.get("status", "")).lower()
        for key, nested in value.items():
            if key == "image" and status not in {"failed", "error"}:
                yield nested
            elif key != "image":
                yield from iter_image_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_image_values(nested)


def iter_manifest_image_values(manifest: Any) -> Iterable[Any]:
    """Yield image references while excluding retired seasonal products."""

    if isinstance(manifest, dict) and isinstance(manifest.get("runs"), list):
        for key, nested in manifest.items():
            if key != "runs":
                yield from iter_image_values(nested)
        for run in manifest["runs"]:
            if isinstance(run, dict) and is_retired_product(run.get("product")):
                continue
            yield from iter_image_values(run)
        return
    yield from iter_image_values(manifest)


def load_share_assets(site_root: Path) -> set[PurePosixPath]:
    manifest_paths = set(site_root.glob("*_manifest.json"))
    manifest_paths.update((site_root / "seasonal").glob("*_manifest.json"))
    assets: set[PurePosixPath] = set()
    for manifest_path in sorted(manifest_paths):
        if manifest_path.name == "runs_manifest.json":
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for value in iter_manifest_image_values(manifest):
            path = normalize_asset_path(value)
            if path is not None:
                assets.add(path)
    return assets


def share_asset_path(asset_path: PurePosixPath) -> PurePosixPath:
    relative = PurePosixPath(*asset_path.parts[1:])
    return PurePosixPath("seasonal", "share", relative)


def _published_asset_path(site_root: Path, asset_path: PurePosixPath) -> PurePosixPath:
    if (site_root / "seasonal").is_dir():
        return asset_path
    return PurePosixPath(*asset_path.parts[1:])


def _published_share_path(site_root: Path, asset_path: PurePosixPath) -> PurePosixPath:
    share_path = share_asset_path(asset_path)
    if (site_root / "seasonal").is_dir():
        return share_path
    return PurePosixPath(*share_path.parts[1:])


def _brand_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb_image(opened: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(opened)
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def save_branded_image(source: Path, destination: Path) -> None:
    with Image.open(source) as opened:
        image = _rgb_image(opened)
        width, height = image.size
        footer_height = max(30, round(height * 0.036))
        canvas = Image.new("RGB", (width, height + footer_height), BRAND_BACKGROUND)
        canvas.paste(image, (0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.line((0, height, width, height), fill=(43, 72, 85), width=max(1, width // 900))
        font = _brand_font(max(14, round(footer_height * 0.48)))
        wall = "wall"
        dot = "."
        cloud = "cloud"
        wall_width = draw.textlength(wall, font=font)
        dot_width = draw.textlength(dot, font=font)
        cloud_width = draw.textlength(cloud, font=font)
        text_width = wall_width + dot_width + cloud_width
        text_box = draw.textbbox((0, 0), "wall.cloud", font=font)
        text_height = text_box[3] - text_box[1]
        x = width - max(14, round(width * 0.016)) - text_width
        y = height + (footer_height - text_height) / 2 - text_box[1]
        draw.text((x, y), wall, fill=BRAND_FOREGROUND, font=font)
        draw.text((x + wall_width, y), dot, fill=BRAND_ACCENT, font=font)
        draw.text((x + wall_width + dot_width, y), cloud, fill=BRAND_FOREGROUND, font=font)

        destination.parent.mkdir(parents=True, exist_ok=True)
        suffix = destination.suffix.lower()
        if suffix == ".png":
            canvas.save(destination, format="PNG", optimize=True)
        elif suffix == ".webp":
            canvas.save(destination, format="WEBP", quality=94, method=6)
        else:
            canvas.save(destination, format="JPEG", quality=95, subsampling=0, optimize=True)


def build_share_images(site_root: Path) -> dict[str, Any]:
    site_root = site_root.resolve()
    summary: dict[str, Any] = {"created": 0, "refreshed": 0, "missing": 0, "missing_assets": []}
    for asset_path in sorted(load_share_assets(site_root), key=str):
        source_path = _published_asset_path(site_root, asset_path)
        source = site_root.joinpath(*source_path.parts)
        destination_path = _published_share_path(site_root, asset_path)
        destination = site_root.joinpath(*destination_path.parts)
        if not source.is_file():
            summary["missing"] += 1
            summary["missing_assets"].append(str(asset_path))
            continue
        existed = destination.is_file() and destination.stat().st_size > 0
        save_branded_image(source, destination)
        summary["refreshed" if existed else "created"] += 1
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True, help="Merged GitHub Pages tree")
    parser.add_argument("--strict", action="store_true", help="Fail when a referenced source image is missing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_share_images(args.site_root)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if args.strict and summary["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

