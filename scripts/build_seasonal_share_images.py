#!/usr/bin/env python3
"""Build protected in-map-branded copies of every seasonal map referenced by a manifest.

The wordmark is anchored to the detected map frame rather than the legend.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL.PngImagePlugin import PngInfo

try:
    from seasonal_products import is_retired_product
except ModuleNotFoundError:  # Imported as ``scripts.build_seasonal_share_images`` in tests.
    from scripts.seasonal_products import is_retired_product


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
BRAND_FOREGROUND = (17, 24, 32)
BRAND_ACCENT = (27, 181, 176)
BRAND_STROKE = (247, 249, 251)
RIGHTS_HOLDER = "wall.cloud"
USAGE_TERMS = "No reproduction, adaptation, or commercial redistribution without permission."
PROVENANCE_FILENAME = "provenance.json"
SHARE_IMAGE_CACHE_VERSION = 7


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _map_id(asset_path: str, source_hash: str) -> str:
    digest = hashlib.sha256(f"{asset_path}\0{source_hash}".encode("utf-8")).hexdigest()
    return f"map-{digest[:12]}"


def _provenance_path(site_root: Path) -> Path:
    share_root = site_root / "seasonal" / "share" if (site_root / "seasonal").is_dir() else site_root / "share"
    return share_root / PROVENANCE_FILENAME


def _save_provenance(path: Path, *, generated_utc: str, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_utc": generated_utc,
        "rights_holder": RIGHTS_HOLDER,
        "usage_terms": USAGE_TERMS,
        "assets": records,
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


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

    Require a neutral dark horizontal border with tall vertical sides at
    both ends. Filled weather bands and short colorbar boxes are not frames.
    """

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    scan_floor = max(1, int(round(height * 0.52)))
    edge_margin = max(2, int(round(width * 0.01)))
    border_run_minimum = max(100, int(round(width * 0.40)))

    pixels = rgb.astype(np.int16)
    # JPEG/PNG antialiasing can lift a one-pixel frame line well above the
    # original spine color. Keep the neutral-color test strict enough to
    # reject weather shading while allowing that edge smoothing.
    neutral_dark = (pixels.max(axis=2) <= 190) & (np.ptp(pixels, axis=2) <= 45)
    side_height = max(20, int(round(height * 0.20)))
    candidates: list[tuple[int, tuple[int, int]]] = []
    for row_index in range(height - 2, scan_floor, -1):
        dark = neutral_dark[row_index]
        run = _longest_run(dark)
        if run is None or run[1] - run[0] + 1 < border_run_minimum:
            continue
        if run[0] <= edge_margin or run[1] >= width - 1 - edge_margin:
            continue
        # A map frame has two near-continuous vertical sides extending well
        # above its bottom edge. Allow a pixel of JPEG/antialiasing tolerance.
        top = max(0, row_index - side_height)
        side_window = max(1, row_index - top)
        if any(
            (
                (side_run := _longest_run(
                    neutral_dark[top:row_index, max(0, x - 1):min(width, x + 2)]
                    .any(axis=1)
                ))
                is None
                or side_run[1] - side_run[0] + 1 < round(side_window * 0.90)
            )
            for x in run
        ):
            continue
        candidates.append((row_index, run))

    if candidates:
        # Choose the lowest valid frame, not the first candidate encountered
        # above it. This matters when an image also contains a long internal
        # contour or a second panel with a partial border.
        row_index, run = max(candidates, key=lambda item: item[0])
        return run[1], row_index

    # Fallback for non-seasonal or diagnostic images without a detectable map
    # frame. This still preserves the source canvas and keeps the mark visible.
    # Seasonal source canvases reserve the lower strip for the color scale;
    # keep the fallback on the map frame rather than placing the mark in that
    # strip. The estimate is only used when a frame is too faint to detect.
    return (
        width - max(4, int(round(width * 0.045))),
        height - max(8, int(round(height * 0.115))),
    )

def _draw_trace_marks(image: Image.Image, map_bottom: int, map_id: str) -> Image.Image:
    """Add low-opacity repeated provenance marks inside the map area.

    These marks are intentionally human-visible on inspection. They are a
    crop-resistant attribution layer, not an instruction to an image model.
    """
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_size = max(10, round(min(width, height) * 0.012))
    font = _brand_font(font_size)
    label = f"wall.cloud · {map_id[-8:]}"
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    max_x = max(2, width - text_width - 2)
    max_y = max(2, min(height - text_height - 2, map_bottom - text_height - 2))
    stroke_width = max(1, round(font_size * 0.05))
    positions = (
        (round(width * 0.06), round(height * 0.27)),
        (round(width * 0.48), round(height * 0.48)),
        (round(width * 0.08), round(height * 0.68)),
    )
    for raw_x, raw_y in positions:
        x = max(2, min(raw_x, max_x))
        y = max(2, min(raw_y, max_y))
        draw.text(
            (x, y),
            label,
            fill=(255, 255, 255, 72),
            font=font,
            stroke_width=stroke_width,
            stroke_fill=(17, 24, 32, 48),
        )
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _image_metadata(
    *,
    asset_path: str,
    source_hash: str,
    map_id: str,
    generated_utc: str,
) -> dict[str, str]:
    return {
        "Author": RIGHTS_HOLDER,
        "Copyright": f"© {datetime.now(timezone.utc).year} {RIGHTS_HOLDER}",
        "MapID": map_id,
        "SourceAsset": asset_path,
        "SourceSHA256": source_hash,
        "GeneratedUTC": generated_utc,
        "UsageTerms": USAGE_TERMS,
        "Provenance": PROVENANCE_FILENAME,
    }


def _metadata_comment(metadata: dict[str, str]) -> bytes:
    return "\n".join(f"{key}: {value}" for key, value in metadata.items()).encode("utf-8")


def save_branded_image(
    source: Path,
    destination: Path,
    *,
    source_hash: str | None = None,
    asset_path: str | None = None,
    generated_utc: str | None = None,
) -> None:
    """Preserve the source canvas and add visible and metadata provenance layers."""

    source_hash = source_hash or _sha256(source)
    logical_asset_path = asset_path or source.name
    generated_utc = generated_utc or _utc_now()
    map_id = _map_id(logical_asset_path, source_hash)

    with Image.open(source) as opened:
        image = _rgb_image(opened)
        width, height = image.size
        canvas = image.copy()

        # Anchor the repeated marks to the detected map frame rather than the
        # legend/footer. They remain legible but low-opacity over map shading.
        map_right, map_bottom = _map_corner(canvas)
        canvas = _draw_trace_marks(canvas, map_bottom, map_id)
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

        # Keep the wordmark just inside the frame corner; the small inset is
        # intentional so the outline does not get clipped by the spine.
        right_inset = max(2, round(font_size * 0.10))
        bottom_inset = max(2, round(font_size * 0.10))
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

        metadata = _image_metadata(
            asset_path=logical_asset_path,
            source_hash=source_hash,
            map_id=map_id,
            generated_utc=generated_utc,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        suffix = destination.suffix.lower()
        if suffix == ".png":
            pnginfo = PngInfo()
            for key, value in metadata.items():
                pnginfo.add_text(key, value)
            canvas.save(destination, format="PNG", optimize=True, pnginfo=pnginfo)
        elif suffix == ".webp":
            canvas.save(destination, format="WEBP", quality=94, method=6)
        else:
            canvas.save(
                destination,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True,
                comment=_metadata_comment(metadata),
            )


def build_share_images(site_root: Path) -> dict[str, Any]:
    site_root = site_root.resolve()
    cache_path = _cache_path(site_root)
    previous_cache = _load_cache(cache_path)
    generated_utc = _utc_now()
    current_cache: dict[str, str] = {}
    provenance_records: list[dict[str, str]] = []
    summary: dict[str, Any] = {
        "created": 0,
        "refreshed": 0,
        "skipped": 0,
        "missing": 0,
        "missing_assets": [],
    }
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
        map_id = _map_id(cache_key, source_hash)
        if existed and previous_cache.get(cache_key) == source_hash:
            summary["skipped"] += 1
        else:
            save_branded_image(
                source,
                destination,
                source_hash=source_hash,
                asset_path=cache_key,
                generated_utc=generated_utc,
            )
            summary["refreshed" if existed else "created"] += 1
        if destination.is_file():
            provenance_records.append(
                {
                    "map_id": map_id,
                    "source_asset": cache_key,
                    "published_share_asset": destination_path.as_posix(),
                    "source_sha256": source_hash,
                    "share_sha256": _sha256(destination),
                    "generated_utc": generated_utc,
                }
            )
    _save_cache(cache_path, current_cache)
    _save_provenance(
        _provenance_path(site_root),
        generated_utc=generated_utc,
        records=provenance_records,
    )
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
