#!/usr/bin/env python3
"""Build current compact WebP thumbnails for seasonal Compare cards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image, ImageOps


COMPARE_PRODUCT_ALIASES = frozenset(
    {
        "500mb_height_anomaly",
        "850mb_temperature_anomaly",
        "2m_temperature_anomaly",
        "surface_temperature_anomaly",
        "temperature_anomaly",
        "precipitation_anomaly",
        "snowfall_anomaly",
        "mslp_anomaly",
        "sea_surface_temperature_anomaly",
        "sst_anomaly",
    }
)


def normalize_asset_path(value: Any) -> PurePosixPath | None:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("public/"):
        text = text[len("public/") :]
    text = text.lstrip("/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return None
    if not path.parts or path.parts[0] != "seasonal" or "thumbnails" in path.parts:
        return None
    return path


def thumbnail_asset_path(asset_path: PurePosixPath) -> PurePosixPath:
    relative = PurePosixPath(*asset_path.parts[1:]).with_suffix(".webp")
    return PurePosixPath("seasonal", "thumbnails", relative)


def _published_asset_path(site_root: Path, asset_path: PurePosixPath) -> PurePosixPath:
    """Resolve a logical seasonal asset in nested or flat Pages output."""

    nested = site_root.joinpath(*asset_path.parts)
    if nested.is_file() or (site_root / "seasonal").is_dir():
        return asset_path
    return PurePosixPath(*asset_path.parts[1:])


def _published_thumbnail_path(site_root: Path, asset_path: PurePosixPath) -> PurePosixPath:
    if (site_root / "seasonal").is_dir():
        return thumbnail_asset_path(asset_path)
    relative = PurePosixPath(*asset_path.parts[1:]).with_suffix(".webp")
    return PurePosixPath("thumbnails", relative)


def _published_share_source(site_root: Path, asset_path: PurePosixPath) -> PurePosixPath:
    relative = PurePosixPath(*asset_path.parts[1:])
    if (site_root / "seasonal").is_dir():
        return PurePosixPath("seasonal", "share", relative)
    return PurePosixPath("share", relative)


def iter_target_images(target: dict[str, Any]) -> Iterable[Any]:
    if str(target.get("status", "")).lower() not in {"failed", "error"}:
        yield target.get("image")
    comparisons = target.get("comparison")
    if isinstance(comparisons, dict):
        for comparison in comparisons.values():
            if isinstance(comparison, dict):
                yield comparison.get("image")


def manifest_compare_assets(manifest: dict[str, Any]) -> set[PurePosixPath]:
    assets: set[PurePosixPath] = set()
    for run in manifest.get("runs", []):
        if not isinstance(run, dict) or str(run.get("product", "")) not in COMPARE_PRODUCT_ALIASES:
            continue
        for target in run.get("targets", []):
            if not isinstance(target, dict):
                continue
            for value in iter_target_images(target):
                path = normalize_asset_path(value)
                if path is not None:
                    assets.add(path)
    return assets


def load_compare_assets(site_root: Path) -> set[PurePosixPath]:
    assets: set[PurePosixPath] = set()
    manifest_paths = set((site_root / "seasonal").glob("*_manifest.json"))
    manifest_paths.update(
        path for path in site_root.glob("*_manifest.json")
        if path.name != "runs_manifest.json"
    )
    for manifest_path in sorted(manifest_paths):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assets.update(manifest_compare_assets(manifest))
    return assets


def save_webp_thumbnail(source: Path, destination: Path, max_width: int, quality: int) -> None:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((max_width, max_width), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="WEBP", quality=quality, method=6)


def build_thumbnails(site_root: Path, max_width: int = 560, quality: int = 82) -> dict[str, Any]:
    site_root = site_root.resolve()
    if max_width < 100:
        raise ValueError("max_width must be at least 100 pixels")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")

    summary: dict[str, Any] = {"created": 0, "refreshed": 0, "missing": 0, "missing_assets": []}
    for asset_path in sorted(load_compare_assets(site_root), key=str):
        source_path = _published_asset_path(site_root, asset_path)
        source = site_root.joinpath(*source_path.parts)
        share_source_path = _published_share_source(site_root, asset_path)
        share_source = site_root.joinpath(*share_source_path.parts)
        if share_source.is_file():
            source = share_source
        destination_path = _published_thumbnail_path(site_root, asset_path)
        destination = site_root.joinpath(*destination_path.parts)
        if not source.is_file():
            summary["missing"] += 1
            summary["missing_assets"].append(str(asset_path))
            continue
        had_thumbnail = destination.is_file() and destination.stat().st_size > 0
        save_webp_thumbnail(source, destination, max_width=max_width, quality=quality)
        summary["refreshed" if had_thumbnail else "created"] += 1
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True, help="Merged GitHub Pages tree")
    parser.add_argument("--max-width", type=int, default=560)
    parser.add_argument("--quality", type=int, default=82)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_thumbnails(args.site_root, max_width=args.max_width, quality=args.quality)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

