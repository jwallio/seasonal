#!/usr/bin/env python3
"""Build protected in-map-branded copies of every seasonal map referenced by a manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from seasonal_products import is_retired_product
except ModuleNotFoundError:  # Imported as ``scripts.build_seasonal_share_images`` in tests.
    from scripts.seasonal_products import is_retired_product


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
BRAND_FOREGROUND = (17, 24, 32)
BRAND_ACCENT = (27, 181, 176)
BRAND_STROKE = (247, 249, 251)
SHARE_IMAGE_CACHE_VERSION = 3


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


def _cache_path(site_root: Path) -> Path:
    share_root = site_root / "seasonal" / "share" if (site_root / "seasonal").is_dir() else site_root / "share"
    return share_root / ".source-hashes.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cache(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != SHARE_IMAGE_CACHE_VERSION or not isinstance(payload.get("assets"), dict):
        return {}
    return {str(key): str(value) for key, value in payload["assets"].items()}


def _save_cache(path: Path, assets: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps({"version": SHARE_IMAGE_CACHE_VERSION, "assets": assets}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


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


def _longest_run(mask: np.ndarray) -> tuple[int, int] | None:
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return None
    breaks = np.flatnonzero(np.diff(indices) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [indices.size - 1]))
    lengths = indices[ends] - indices[starts] + 1
    choice = int(np.argmax(lengths))
    return int(indices[starts[choice]]), int(indices[ends[choice]])


def _map_corner(image: Image.Image) -> tuple[int, int]:
    """Find the lower-right inside corner of a seasonal map frame.

    The shared seasonal renderer draws a dark rectangular map border above a
    horizontal colorbar. Detecting those two stable layout features keeps the
    watermark in the map on square, CONUS, and Northern Hemisphere variants.
    """

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    scan_floor = max(1, int(round(height * 0.52)))
    minimum_run = max(80, int(round(width * 0.35)))
    colorbar_top = None

    for row_index in range(height - 2, scan_floor, -1):
        row = rgb[row_index].astype(np.int16)
        spread = row.max(axis=1) - row.min(axis=1)
        brightness = row.mean(axis=1)
        colored = (spread >= 12) | (brightness <= 235)
        run = _longest_run(colored)
        if run is None or run[1] - run[0] + 1 < minimum_run:
            continue
        edge_margin = max(2, int(round(width * 0.01)))
        if run[0] <= edge_margin or run[1] >= width - 1 - edge_margin:
            continue
        colorbar_top = row_index
        for upper in range(row_index - 1, scan_floor, -1):
            upper_row = rgb[upper].astype(np.int16)
            upper_spread = upper_row.max(axis=1) - upper_row.min(axis=1)
            upper_brightness = upper_row.mean(axis=1)
            upper_colored = (upper_spread >= 12) | (upper_brightness <= 235)
            upper_run = _longest_run(upper_colored)
            if upper_run is None or upper_run[1] - upper_run[0] + 1 < minimum_run:
                break
            if upper_run[0] <= edge_margin or upper_run[1] >= width - 1 - edge_margin:
                break
            colorbar_top = upper
        break

    if colorbar_top is not None:
        border_floor = max(scan_floor, colorbar_top - int(round(height * 0.22)))
        border_run_minimum = max(100, int(round(width * 0.40)))
        for row_index in range(colorbar_top - 2, border_floor, -1):
            row = rgb[row_index].astype(np.int16)
            dark = row.max(axis=1) <= 130
            run = _longest_run(dark)
            if run is not None and run[1] - run[0] + 1 >= border_run_minimum:
                return run[1], row_index

        colorbar_row = rgb[colorbar_top].astype(np.int16)
        spread = colorbar_row.max(axis=1) - colorbar_row.min(axis=1)
        brightness = colorbar_row.mean(axis=1)
        run = _longest_run((spread >= 12) | (brightness <= 235))
        if run is not None:
            right = run[1]
            if height / max(width, 1) < 0.9:
                right -= int(round(width * 0.015))
            return right, colorbar_top - max(8, int(round(height * 0.012)))

    # Fallback for non-seasonal or diagnostic images without a detectable map
    # frame. This still preserves the source canvas and keeps the mark visible.
    return (
        width - max(6, int(round(width * 0.012))),
        height - max(8, int(round(height * (0.16 if height / max(width, 1) < 0.9 else 0.23)))),
    )


def save_branded_image(source: Path, destination: Path) -> None:
    """Preserve the source canvas and add the protected in-map wall.cloud mark."""

    with Image.open(source) as opened:
        image = _rgb_image(opened)
        width, height = image.size
        canvas = image.copy()
        draw = ImageDraw.Draw(canvas)

        font_size = max(14, round(min(width, height) * 0.019))
        font = _brand_font(font_size)
        wall = "wall"
        dot = "."
        cloud = "cloud"
        wall_width = draw.textlength(wall, font=font)
        dot_width = draw.textlength(dot, font=font)
        cloud_width = draw.textlength(cloud, font=font)
        text_width = wall_width + dot_width + cloud_width
        text_box = draw.textbbox((0, 0), "wall.cloud", font=font)
        text_height = text_box[3] - text_box[1]

        # Anchor to the detected map frame, not the full image canvas.
        map_right, map_bottom = _map_corner(canvas)
        right_inset = max(4, round(font_size * 0.28))
        bottom_inset = max(4, round(font_size * 0.22))
        x = map_right - right_inset - text_width
        y = map_bottom - bottom_inset - text_height - text_box[1]
        stroke_width = max(1, round(font_size * 0.06))

        draw.text(
            (x, y),
            wall,
            fill=BRAND_FOREGROUND,
            font=font,
            stroke_width=stroke_width,
            stroke_fill=BRAND_STROKE,
        )
        draw.text(
            (x + wall_width, y),
            dot,
            fill=BRAND_ACCENT,
            font=font,
            stroke_width=stroke_width,
            stroke_fill=BRAND_STROKE,
        )
        draw.text(
            (x + wall_width + dot_width, y),
            cloud,
            fill=BRAND_FOREGROUND,
            font=font,
            stroke_width=stroke_width,
            stroke_fill=BRAND_STROKE,
        )

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
    cache_path = _cache_path(site_root)
    previous_cache = _load_cache(cache_path)
    current_cache: dict[str, str] = {}
    summary: dict[str, Any] = {"created": 0, "refreshed": 0, "skipped": 0, "missing": 0, "missing_assets": []}
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
        source_hash = _sha256(source)
        cache_key = asset_path.as_posix()
        current_cache[cache_key] = source_hash
        if existed and previous_cache.get(cache_key) == source_hash:
            summary["skipped"] += 1
            continue
        save_branded_image(source, destination)
        summary["refreshed" if existed else "created"] += 1
    _save_cache(cache_path, current_cache)
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
