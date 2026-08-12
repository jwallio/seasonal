#!/usr/bin/env python3
"""Fetch and render CFSv2 monthly 500-mb height products.

This is intentionally a standalone seasonal adapter.  WeatherNext frames use
Earth Engine and forecast-hour metadata; CFSv2 seasonal frames use the NOAA
NOMADS monthly ``pgbf`` GRIB2 files and calendar-month lead metadata.

The production anomaly path requires a caller-supplied CFSv2/reforecast
baseline.  The script never substitutes a WeatherNext, ERA5, or MERRA-2
climatology.  ``--absolute`` is available only for source/decoder smoke tests
and is labelled as an absolute-height product in the manifest and image.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence
from urllib.parse import urljoin


NOMADS_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/"
NCEI_CALIBRATION_ROOT = "https://www.ncei.noaa.gov/thredds/fileServer/model-cfs_refor_calclim_mm_9m_pgbf/"
NCEI_CALIBRATION_YEARS = "1982-2010"
NCEI_CALIBRATION_LABEL = "NCEI CFS reforecast calibration climatology; 1982-2010"
CFS_CYCLE_HOURS = (0, 6, 12, 18)
ROLLING_MEMBER_DEFAULT = 1
GRID_LON_COUNT = 360
GRID_LAT_COUNT = 181
ANOMALY_MIN_M = -140.0
ANOMALY_MAX_M = 140.0
ANOMALY_PALETTE = [
    "#32678e",
    "#3f80a7",
    "#5796b8",
    "#72abc6",
    "#98c1d2",
    "#c0d8e1",
    "#e8eef0",
    "#f9e4e2",
    "#f2c2c0",
    "#e39a97",
    "#d87573",
    "#c45257",
    "#a83b49",
]
ANOMALY_TICKS = [-140, -100, -60, -20, 0, 20, 60, 100, 140]
# North America plus the adjacent Atlantic/Pacific; keep Europe out of the
# default frame so the result follows the existing WN2 North America products.
DEFAULT_REGION = (-170.0, -15.0, 5.0, 75.0)
DEFAULT_BORDER_URLS = (
    (
        "countries.geojson",
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
    ),
    (
        "us-states.geojson",
        "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json",
    ),
)


class CFSv2Error(RuntimeError):
    """A user-actionable CFSv2 pipeline error."""


@dataclass
class Grid:
    """A regular longitude/latitude grid represented without a hard dependency."""

    lons: list[float]
    lats: list[float]
    values: list[list[float]]

    def assert_compatible(self, other: "Grid", label: str) -> None:
        if self.lons != other.lons or self.lats != other.lats:
            raise CFSv2Error(f"{label} grid does not match the CFSv2 1-degree grid")


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_repo_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def parse_init(value: str) -> str:
    if not re.fullmatch(r"\d{10}", value):
        raise CFSv2Error("--init must be YYYYMMDDHH or latest")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d%H")
    except ValueError as exc:
        raise CFSv2Error(f"invalid CFSv2 initialization time: {value}") from exc
    if parsed.hour not in (0, 6, 12, 18):
        raise CFSv2Error("CFSv2 initialization hour must be 00, 06, 12, or 18")
    return value


def parse_int_list(value: str, label: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError as exc:
            raise CFSv2Error(f"invalid {label}: {item}") from exc
        if not minimum <= number <= maximum:
            raise CFSv2Error(f"{label} must be between {minimum} and {maximum}")
        if number not in result:
            result.append(number)
    if not result:
        raise CFSv2Error(f"{label} cannot be empty")
    return result


def month_after(year: int, month: int, lead_months: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + lead_months
    return absolute // 12, absolute % 12 + 1


def target_month(init: str, lead_months: int) -> str:
    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    year, month = month_after(init_date.year, init_date.month, lead_months)
    return f"{year:04d}{month:02d}"


def target_period(target: str) -> tuple[str, str]:
    start = dt.datetime.strptime(target, "%Y%m")
    end_year, end_month = month_after(start.year, start.month, 1)
    end = dt.datetime(end_year, end_month, 1)
    return iso_utc(start.replace(tzinfo=dt.timezone.utc)), iso_utc(end.replace(tzinfo=dt.timezone.utc))


def discover_latest_init(root: str = NOMADS_ROOT) -> str:
    """Select the newest listed cycle from the official NOMADS directory."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required when --init latest is used") from exc

    try:
        response = requests.get(root, timeout=(20, 60))
        response.raise_for_status()
    except Exception as exc:
        raise CFSv2Error(f"could not read the NOMADS CFSv2 directory: {exc}") from exc
    dates = sorted(set(re.findall(r'href="cfs\.(\d{8})/"', response.text)), reverse=True)
    if not dates:
        raise CFSv2Error("could not find a cfs.YYYYMMDD cycle in the NOMADS index")
    for date_text in dates:
        for hour in (18, 12, 6, 0):
            candidate = f"{date_text}{hour:02d}"
            if dt.datetime.strptime(candidate, "%Y%m%d%H") <= dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
                return candidate
    raise CFSv2Error("NOMADS listed no usable CFSv2 cycle")


def find_wgrib2(explicit: str) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CFSV2_WGRIB2"):
        candidates.append(os.environ["CFSV2_WGRIB2"])
    found = shutil.which("wgrib2")
    if found:
        candidates.append(found)
    candidates.append(r"C:\wgrib2\wgrib2.exe")
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    raise CFSv2Error(
        "wgrib2 was not found; install it or set CFSV2_WGRIB2/--wgrib2 to the executable path"
    )


def cfs_file_url(init: str, member: int, target: str) -> str:
    date_text, hour_text = init[:8], init[8:]
    filename = f"pgbf.{member:02d}.{init}.{target}.avrg.grib.grb2"
    return urljoin(
        NOMADS_ROOT,
        f"cfs.{date_text}/{hour_text}/monthly_grib_{member:02d}/{filename}",
    )


def cached_source_path(cache_dir: Path, init: str, member: int, target: str) -> Path:
    filename = Path(cfs_file_url(init, member, target)).name
    return cache_dir / init / f"member_{member:02d}" / filename


def ncei_calibration_url(init: str, lead: int) -> str:
    month, day, hour = init[4:6], init[6:8], init[8:]
    filename = f"pgbf.{month}.{day}.{hour}.l{lead:02d}.fclm.{NCEI_CALIBRATION_YEARS.replace('-', '.')}.grb2"
    return urljoin(NCEI_CALIBRATION_ROOT, f"{month}/{filename}")


def cached_calibration_path(cache_dir: Path, init: str, lead: int) -> Path:
    return cache_dir / "calibration" / init / Path(ncei_calibration_url(init, lead)).name


def rolling_cycle_inits(end_init: str, cycle_count: int) -> list[str]:
    """Return the most recent six-hourly cycles, oldest first."""

    if cycle_count < 1:
        raise CFSv2Error("rolling cycle count must be positive")
    end_date = dt.datetime.strptime(end_init, "%Y%m%d%H")
    return [
        (end_date - dt.timedelta(hours=6 * offset)).strftime("%Y%m%d%H")
        for offset in range(cycle_count - 1, -1, -1)
    ]


def lead_for_target(init: str, target: str) -> int:
    """Find the monthly lead that reaches a fixed target month."""

    for lead in range(1, 10):
        if target_month(init, lead) == target:
            return lead
    raise CFSv2Error(f"CFSv2 cycle {init} has no 1-9 month lead for target {target}")


def rolling_state_path(state_dir: Path, init: str, member: int, target: str) -> Path:
    return state_dir / target / f"hgt500.{init}.m{member:02d}.csv.gz"


def write_grid_state(grid: Grid, path: Path) -> None:
    """Persist a decoded grid compactly so it survives the 7-day NOMADS rotation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("lon", "lat", "value"))
        for lat, row in zip(grid.lats, grid.values):
            for lon, value in zip(grid.lons, row):
                writer.writerow((lon, lat, value))
    temporary.replace(path)


def read_grid_state(path: Path) -> Grid:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(path))


def download_file(url: str, destination: Path, request_delay: float, last_request: float) -> tuple[bool, float]:
    if destination.exists() and destination.stat().st_size > 0:
        return False, last_request
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only on minimal installs
        raise CFSv2Error("requests is required to download CFSv2 files") from exc

    elapsed = time.monotonic() - last_request if last_request else request_delay
    if last_request and elapsed < request_delay:
        time.sleep(request_delay - elapsed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        response = requests.get(url, stream=True, timeout=(30, 300))
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        if partial.stat().st_size == 0:
            raise CFSv2Error(f"empty download from {url}")
        partial.replace(destination)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise
    return True, time.monotonic()


def _float_or_nan(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def _normalize_lon(value: float) -> float:
    lon = value % 360.0
    if lon > 180.0:
        lon -= 360.0
    return round(lon, 6)


def grid_from_rows(rows: Iterable[Sequence[str]], source: str) -> Grid:
    points: dict[tuple[float, float], float] = {}
    for row in rows:
        if len(row) < 3:
            continue
        lon = _float_or_nan(row[-3])
        lat = _float_or_nan(row[-2])
        value = _float_or_nan(row[-1])
        if not all(math.isfinite(item) for item in (lon, lat)):
            continue
        points[(_normalize_lon(lon), round(lat, 6))] = value

    lons = sorted({lon for lon, _ in points})
    lats = sorted({lat for _, lat in points})
    if len(lons) != GRID_LON_COUNT or len(lats) != GRID_LAT_COUNT:
        raise CFSv2Error(
            f"{source} did not decode a 360x181 grid (got {len(lons)}x{len(lats)})"
        )
    values = []
    for lat in lats:
        row = []
        for lon in lons:
            if (lon, lat) not in points:
                raise CFSv2Error(f"{source} has a missing grid point at {lon},{lat}")
            row.append(points[(lon, lat)])
        values.append(row)
    return Grid(lons=lons, lats=lats, values=values)


def read_grid_csv(csv_path: Path) -> Grid:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return grid_from_rows(csv.reader(handle), str(csv_path))


def decode_grib(grib_path: Path, wgrib2: str, force: bool = False) -> Grid:
    csv_path = grib_path.with_name(grib_path.name + ".hgt500.csv")
    if force or not csv_path.exists() or csv_path.stat().st_size == 0:
        command = [wgrib2, str(grib_path), "-match", ":HGT:500 mb:", "-csv", str(csv_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "wgrib2 failed").strip()
            raise CFSv2Error(f"wgrib2 failed for {grib_path.name}: {detail[-800:]}")
    return read_grid_csv(csv_path)


def mean_grids(grids: Sequence[Grid]) -> Grid:
    if not grids:
        raise CFSv2Error("cannot average an empty CFSv2 member set")
    first = grids[0]
    for grid in grids[1:]:
        first.assert_compatible(grid, "ensemble member")
    values: list[list[float]] = []
    for row_index in range(len(first.lats)):
        mean_row = []
        for column_index in range(len(first.lons)):
            samples = [grid.values[row_index][column_index] for grid in grids]
            finite = [sample for sample in samples if math.isfinite(sample)]
            mean_row.append(sum(finite) / len(finite) if finite else math.nan)
        values.append(mean_row)
    return Grid(first.lons[:], first.lats[:], values)


def decode_target_ensemble(
    args: argparse.Namespace,
    init: str,
    target: str,
    members: Sequence[int],
    rolling_inits: Sequence[str],
    cache_dir: Path,
    state_dir: Path,
    wgrib2: str,
    repo_root: Path,
    last_request: float,
) -> tuple[Grid, list[dict], int, int, str, float]:
    """Decode either the original single-cycle ensemble or a rolling blend."""

    grids: list[Grid] = []
    source_files: list[dict] = []
    if rolling_inits:
        expected_count = len(rolling_inits)
        rolling_member = args.rolling_member
        for cycle in rolling_inits:
            cycle_lead = lead_for_target(cycle, target)
            url = cfs_file_url(cycle, rolling_member, target)
            cache_path = cached_source_path(cache_dir, cycle, rolling_member, target)
            state_path = rolling_state_path(state_dir, cycle, rolling_member, target)
            source_file = {
                "initialization": cycle,
                "initialization_utc": iso_utc(dt.datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
                "lead_month": cycle_lead,
                "member": rolling_member,
                "url": url,
                "cache_file": relative_path(cache_path, repo_root),
                "state_file": relative_path(state_path, repo_root),
            }
            try:
                downloaded, last_request = download_file(
                    url,
                    cache_path,
                    max(0.0, args.request_delay),
                    last_request,
                )
                grid = decode_grib(cache_path, wgrib2, force=args.force_decode)
                write_grid_state(grid, state_path)
                if rolling_inits:
                    # The compressed decoded state is the durable rolling input;
                    # do not grow the CI cache with dozens of 25-MB GRIB2 files.
                    decoded_csv = cache_path.with_name(cache_path.name + ".hgt500.csv")
                    for temporary_source in (cache_path, decoded_csv):
                        try:
                            temporary_source.unlink()
                        except FileNotFoundError:
                            pass
                source_file.update(
                    {
                        "storage": "nomads_grib2",
                        "downloaded": downloaded,
                        "decoded_field": "HGT:500 mb",
                    }
                )
            except Exception as exc:
                if state_path.exists():
                    grid = read_grid_state(state_path)
                    source_file.update(
                        {
                            "storage": "retained_decoded_grid",
                            "downloaded": False,
                            "decoded_field": "HGT:500 mb",
                            "download_error": str(exc),
                        }
                    )
                elif args.allow_partial_rolling:
                    source_file.update({"status": "missing", "error": str(exc)})
                    source_files.append(source_file)
                    continue
                else:
                    raise CFSv2Error(
                        f"rolling CFSv2 cycle {cycle} is unavailable and has no retained grid; "
                        "the NOMADS archive rotates after seven days, so run the scheduled job "
                        "daily or use --allow-partial-rolling"
                    ) from exc
            source_file["status"] = "available"
            source_files.append(source_file)
            grids.append(grid)
        if not grids:
            raise CFSv2Error("rolling CFSv2 window produced no usable member grids")
        if len(grids) < expected_count and not args.allow_partial_rolling:
            raise CFSv2Error(
                f"rolling CFSv2 window has {len(grids)} of {expected_count} members; "
                "use --allow-partial-rolling only for an explicitly incomplete product"
            )
        label = f"{len(grids)}-member rolling mean"
        return mean_grids(grids), source_files, len(grids), expected_count, label, last_request

    for member in members:
        url = cfs_file_url(init, member, target)
        cache_path = cached_source_path(cache_dir, init, member, target)
        downloaded, last_request = download_file(
            url,
            cache_path,
            max(0.0, args.request_delay),
            last_request,
        )
        grid = decode_grib(cache_path, wgrib2, force=args.force_decode)
        grids.append(grid)
        source_files.append(
            {
                "initialization": init,
                "initialization_utc": iso_utc(dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
                "lead_month": lead_for_target(init, target),
                "member": member,
                "url": url,
                "cache_file": relative_path(cache_path, repo_root),
                "downloaded": downloaded,
                "decoded_field": "HGT:500 mb",
                "status": "available",
            }
        )
    return mean_grids(grids), source_files, len(grids), len(grids), f"{len(grids)}-member mean", last_request


def subtract_grids(left: Grid, right: Grid) -> Grid:
    left.assert_compatible(right, "baseline")
    values = []
    for left_row, right_row in zip(left.values, right.values):
        values.append(
            [
                (a - b) if math.isfinite(a) and math.isfinite(b) else math.nan
                for a, b in zip(left_row, right_row)
            ]
        )
    return Grid(left.lons[:], left.lats[:], values)


def load_baseline(path: Path, wgrib2: str) -> Grid:
    suffix = path.suffix.lower()
    if suffix in {".grb2", ".grib2", ".grib"}:
        return decode_grib(path, wgrib2)
    return read_grid_csv(path)


def baseline_for_target(args: argparse.Namespace, target: str, repo_root: Path) -> tuple[Path, str]:
    if args.baseline_file:
        path = resolve_repo_path(args.baseline_file, repo_root)
        if not path.exists():
            raise CFSv2Error(f"baseline file does not exist: {path}")
        return path, args.baseline_label or path.name
    if args.baseline_dir:
        directory = resolve_repo_path(args.baseline_dir, repo_root)
        candidates = (
            f"z500_{target}.csv",
            f"z500_{target}.grb2",
            f"z500_{target}.grib2",
            f"baseline_{target}.csv",
            f"baseline_{target}.grb2",
            f"{target}.csv",
            f"{target}.grb2",
        )
        for name in candidates:
            path = directory / name
            if path.exists():
                return path, args.baseline_label or name
        raise CFSv2Error(f"no baseline grid for target month {target} in {directory}")
    raise CFSv2Error(
        "anomaly rendering requires --baseline-file or --baseline-dir; "
        "use --ncei-calibration or --absolute for a clearly labelled alternative"
    )


def configured_baseline_label(args: argparse.Namespace) -> str:
    if args.baseline_label:
        return args.baseline_label
    if args.ncei_calibration:
        return NCEI_CALIBRATION_LABEL
    return "user-supplied CFSv2/reforecast baseline"


def _finite_values(grid: Grid) -> Iterator[float]:
    for row in grid.values:
        for value in row:
            if math.isfinite(value):
                yield value


def _geojson_rings(geometry: dict) -> Iterator[list[list[float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        if coordinates:
            yield coordinates[0]
    elif kind == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                yield polygon[0]
    elif kind == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _geojson_rings(child)


def geojson_features(payload: dict) -> Iterator[list[list[float]]]:
    if payload.get("type") == "FeatureCollection":
        for feature in payload.get("features", []):
            geometry = feature.get("geometry") or {}
            yield from _geojson_rings(geometry)
    elif payload.get("type") == "Feature":
        yield from _geojson_rings(payload.get("geometry") or {})
    else:
        yield from _geojson_rings(payload)


def ensure_border_files(args: argparse.Namespace, cache_dir: Path, repo_root: Path) -> list[Path]:
    if args.no_borders:
        return []
    if args.border_geojson:
        paths = [resolve_repo_path(item, repo_root) for item in args.border_geojson]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise CFSv2Error(f"border GeoJSON does not exist: {', '.join(missing)}")
        return paths
    try:
        import requests
    except ImportError:
        print("warning: requests unavailable; continuing without map borders", file=sys.stderr)
        return []
    border_dir = cache_dir / "borders"
    paths: list[Path] = []
    for filename, url in DEFAULT_BORDER_URLS:
        destination = border_dir / filename
        if not destination.exists() or destination.stat().st_size == 0:
            try:
                border_dir.mkdir(parents=True, exist_ok=True)
                response = requests.get(url, timeout=(20, 120))
                response.raise_for_status()
                destination.write_bytes(response.content)
            except Exception as exc:
                print(f"warning: could not download {filename}; continuing without it: {exc}", file=sys.stderr)
                continue
        paths.append(destination)
    return paths


def render_map(
    grid: Grid,
    init: str,
    target: str,
    lead: int | str,
    members: Sequence[int],
    output_path: Path,
    anomaly: bool,
    baseline_label: str,
    border_paths: Sequence[Path],
    period_label: str = "",
    ensemble_label: str = "",
    region: tuple[float, float, float, float] = DEFAULT_REGION,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.colors as mcolors
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:  # pragma: no cover - target installs requirements.txt
        raise CFSv2Error("rendering requires numpy and matplotlib; install requirements.txt") from exc

    lon_min, lon_max, lat_min, lat_max = region
    lon_indices = [index for index, value in enumerate(grid.lons) if lon_min <= value <= lon_max]
    lat_indices = [index for index, value in enumerate(grid.lats) if lat_min <= value <= lat_max]
    if not lon_indices or not lat_indices:
        raise CFSv2Error("map region does not intersect the decoded grid")
    lons = np.array([grid.lons[index] for index in lon_indices])
    lats = np.array([grid.lats[index] for index in lat_indices])
    data = np.array(
        [[grid.values[lat_index][lon_index] for lon_index in lon_indices] for lat_index in lat_indices],
        dtype=float,
    )

    # Match the compact ~1080x810 footprint of the reference seasonal graphic.
    figure = plt.figure(figsize=(9.0, 6.75), facecolor="#f7f9fb")
    axes = figure.add_axes([0.035, 0.19, 0.93, 0.69])
    axes.set_facecolor("#dce8ee")
    if anomaly:
        cmap = mcolors.ListedColormap(ANOMALY_PALETTE)
        bounds = np.linspace(ANOMALY_MIN_M, ANOMALY_MAX_M, len(ANOMALY_PALETTE) + 1)
        norm = mcolors.BoundaryNorm(bounds, cmap.N, clip=True)
        image = axes.imshow(
            data,
            origin="lower",
            extent=(float(lons[0] - 0.5), float(lons[-1] + 0.5), float(lats[0] - 0.5), float(lats[-1] + 0.5)),
            cmap=cmap,
            norm=norm,
            interpolation="nearest",
            aspect="auto",
        )
        contour_levels = [level for level in range(-120, 121, 20)]
        masked = np.ma.masked_invalid(data)
        if len(contour_levels) > 1:
            axes.contour(lons, lats, masked, levels=contour_levels, colors="#26323a", linewidths=0.25, alpha=0.42)
        colorbar_ticks = ANOMALY_TICKS
        colorbar_label = "500-mb height anomaly (m)"
    else:
        finite = np.asarray(list(_finite_values(grid)), dtype=float)
        if finite.size == 0:
            raise CFSv2Error("decoded grid contains no finite values")
        vmin = float(np.nanpercentile(finite, 2))
        vmax = float(np.nanpercentile(finite, 98))
        if vmin == vmax:
            vmin -= 1.0
            vmax += 1.0
        image = axes.imshow(
            data,
            origin="lower",
            extent=(float(lons[0] - 0.5), float(lons[-1] + 0.5), float(lats[0] - 0.5), float(lats[-1] + 0.5)),
            cmap="viridis",
            norm=mcolors.Normalize(vmin=vmin, vmax=vmax),
            interpolation="nearest",
            aspect="auto",
        )
        colorbar_ticks = np.linspace(vmin, vmax, 7)
        colorbar_label = "500-mb geopotential height (m)"

    for border_path in border_paths:
        try:
            payload = json.loads(border_path.read_text(encoding="utf-8"))
            for ring in geojson_features(payload):
                if len(ring) > 1:
                    axes.plot(
                        [point[0] for point in ring],
                        [point[1] for point in ring],
                        color="#17232c",
                        linewidth=0.55,
                        alpha=0.92,
                        solid_capstyle="round",
                        zorder=3,
                    )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: could not draw borders from {border_path}: {exc}", file=sys.stderr)

    axes.set_xlim(lon_min, lon_max)
    axes.set_ylim(lat_min, lat_max)
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H")
    target_date = dt.datetime.strptime(target, "%Y%m")
    display_period = period_label or target_date.strftime("%B %Y")
    mean_label = ensemble_label or f"{len(members)}-member mean"
    title = "CFSv2 500-mb Height Anomaly" if anomaly else "CFSv2 500-mb Geopotential Height"
    figure.text(0.5, 0.935, title, ha="center", va="center", fontsize=20, fontweight="bold", color="#172735")
    figure.text(
        0.5,
        0.897,
        f"{display_period}  •  Init {init_date:%d %b %Y %HZ}  •  Lead {lead}  •  {mean_label}",
        ha="center",
        va="center",
        fontsize=10.5,
        color="#42515d",
    )
    colorbar = figure.colorbar(image, ax=axes, orientation="horizontal", pad=0.08, fraction=0.055, aspect=48)
    colorbar.set_ticks(colorbar_ticks)
    if anomaly:
        colorbar.set_ticklabels(
            [f"+{int(tick)}" if tick > 0 else str(int(tick)) for tick in colorbar_ticks]
        )
    colorbar.ax.tick_params(labelsize=8.5, length=4, colors="#40515e")
    colorbar.set_label(colorbar_label, fontsize=11.5, fontweight="bold", color="#172735", labelpad=10)
    figure.text(
        0.5,
        0.045,
        (f"NOAA CFSv2 / NOMADS  •  {baseline_label}" if anomaly else "NOAA CFSv2 / NOMADS  •  absolute field smoke output"),
        ha="center",
        va="center",
        fontsize=8.5,
        color="#5d6b75",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=120, facecolor=figure.get_facecolor())
    plt.close(figure)


def relative_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_manifest(path: Path, repo_root: Path, run_entry: dict) -> None:
    payload = {
        "schema_version": 1,
        "kind": "cfsv2_seasonal_manifest",
        "generated_utc": iso_utc(dt.datetime.now(dt.timezone.utc)),
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "runs": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                payload.update({key: existing[key] for key in ("schema_version", "kind", "source", "source_url") if key in existing})
                payload["runs"] = existing["runs"]
        except (OSError, ValueError) as exc:
            raise CFSv2Error(f"could not read existing CFSv2 manifest {path}: {exc}") from exc
    payload["generated_utc"] = iso_utc(dt.datetime.now(dt.timezone.utc))
    payload["runs"] = [run for run in payload["runs"] if run.get("id") != run_entry.get("id")]
    payload["runs"].append(run_entry)
    payload["runs"].sort(key=lambda item: str(item.get("id", "")), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", default="latest", help="CFSv2 cycle as YYYYMMDDHH, or latest")
    parser.add_argument("--lead-months", default="1,2,3", help="comma-separated target leads, usually 1,2,3")
    parser.add_argument("--seasonal-window", default="", help="optional comma-separated leads for an additional seasonal mean, e.g. 1,2,3")
    parser.add_argument("--members", default="1,2,3,4", help="comma-separated monthly_grib member directories")
    parser.add_argument("--rolling-days", type=int, default=0, help="use a lagged initial-condition blend covering this many days; 10 gives CPC-style 40 cycles")
    parser.add_argument("--rolling-member", type=int, default=ROLLING_MEMBER_DEFAULT, help="monthly_grib member used for each rolling six-hourly cycle (default: 1)")
    parser.add_argument("--rolling-state-dir", default=".cache/cfsv2/rolling", help="retained decoded grids used after NOMADS rotates old cycles")
    parser.add_argument("--allow-partial-rolling", action="store_true", help="render with available rolling cycles when the requested window is incomplete")
    parser.add_argument("--cache-dir", default=".cache/cfsv2", help="raw GRIB2/decoder/border cache")
    parser.add_argument("--output-dir", default="public/seasonal/cfsv2", help="rendered image directory")
    parser.add_argument("--manifest", default="public/seasonal/cfsv2_manifest.json", help="seasonal manifest path")
    parser.add_argument("--baseline-file", type=Path, help="one CFSv2/reforecast baseline CSV or GRIB2 grid")
    parser.add_argument("--baseline-dir", type=Path, help="directory containing a baseline grid for each YYYYMM target")
    parser.add_argument("--ncei-calibration", action="store_true", help="fetch the matching official NCEI CFS reforecast calibration baseline (1982-2010)")
    parser.add_argument("--baseline-label", default="", help="human-readable baseline source and period for metadata")
    parser.add_argument("--baseline-years", default="", help="optional baseline years for manifest provenance")
    parser.add_argument("--wgrib2", default="", help="path to wgrib2.exe; CFSV2_WGRIB2 is also honored")
    parser.add_argument("--request-delay", type=float, default=2.0, help="seconds between NOAA downloads")
    parser.add_argument("--border-geojson", action="append", type=Path, help="local GeoJSON border file; repeatable")
    parser.add_argument("--no-borders", action="store_true", help="skip optional border downloads/drawing")
    parser.add_argument("--decode-only", action="store_true", help="download/decode/average but do not render")
    parser.add_argument("--absolute", action="store_true", help="render absolute heights; never label them as anomalies")
    parser.add_argument("--force-decode", action="store_true", help="rerun wgrib2 even when a decoded CSV is cached")
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    init = discover_latest_init() if args.init == "latest" else parse_init(args.init)
    leads = parse_int_list(args.lead_months, "lead months", 1, 9)
    seasonal_leads = parse_int_list(args.seasonal_window, "seasonal window", 1, 9) if args.seasonal_window else []
    if seasonal_leads:
        expected_window = list(range(min(seasonal_leads), max(seasonal_leads) + 1))
        if seasonal_leads != expected_window:
            raise CFSv2Error("--seasonal-window must contain consecutive lead months")
        leads = sorted(set(leads).union(seasonal_leads))
    members = parse_int_list(args.members, "members", 1, 4)
    if args.rolling_days < 0 or args.rolling_days > 30:
        raise CFSv2Error("--rolling-days must be between 0 and 30")
    if not 1 <= args.rolling_member <= 4:
        raise CFSv2Error("--rolling-member must be between 1 and 4")
    rolling_inits = rolling_cycle_inits(init, args.rolling_days * 4) if args.rolling_days else []
    configured_baselines = sum(
        bool(value) for value in (args.baseline_file, args.baseline_dir, args.ncei_calibration)
    )
    if configured_baselines > 1:
        raise CFSv2Error("use only one of --baseline-file, --baseline-dir, and --ncei-calibration")
    if args.ncei_calibration and args.baseline_years and args.baseline_years != NCEI_CALIBRATION_YEARS:
        raise CFSv2Error(
            f"--ncei-calibration uses the published {NCEI_CALIBRATION_YEARS} baseline"
        )
    if not args.absolute and not args.decode_only and configured_baselines == 0:
        raise CFSv2Error(
            "production anomaly rendering needs a CFSv2/reforecast baseline; "
            "provide --baseline-file/--baseline-dir, use --ncei-calibration, or use --absolute for smoke testing"
        )
    wgrib2 = find_wgrib2(args.wgrib2)
    cache_dir = resolve_repo_path(args.cache_dir, repo_root)
    state_dir = resolve_repo_path(args.rolling_state_dir, repo_root)
    output_dir = resolve_repo_path(args.output_dir, repo_root)
    manifest_path = resolve_repo_path(args.manifest, repo_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    border_paths = [] if args.decode_only else ensure_border_files(args, cache_dir, repo_root)

    init_date = dt.datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    run_id = f"cfsv2-{init}"
    rolling_mode = bool(rolling_inits)
    ensemble_expected = len(rolling_inits) if rolling_mode else len(members)
    run_entry = {
        "id": run_id,
        "source": "NOAA CFSv2 NOMADS",
        "source_url": NOMADS_ROOT,
        "model": "CFSv2",
        "init_utc": iso_utc(init_date),
        "decoder": {"tool": "wgrib2", "executable": wgrib2},
        "statistic": "ensemble_mean",
        "members": [args.rolling_member] if rolling_mode else members,
        "ensemble_members": ensemble_expected,
        "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
        "aggregation": (
            f"{args.rolling_days}-day rolling initial-condition mean"
            if rolling_mode
            else "monthly forecast average"
        ) + ("; optional seasonal mean" if seasonal_leads else ""),
        "field": "z500" if args.absolute else "z500_anomaly",
        "units": "m",
        "border_sources": (
            []
            if args.no_borders
            else (
                [{"file": relative_path(resolve_repo_path(path, repo_root), repo_root)} for path in args.border_geojson]
                if args.border_geojson
                else [{"name": name, "url": url} for name, url in DEFAULT_BORDER_URLS]
            )
        ),
        "baseline": None,
        "status": "planned",
        "targets": [],
    }
    if rolling_mode:
        run_entry["rolling_window"] = {
            "days": args.rolling_days,
            "expected_cycles": len(rolling_inits),
            "cycle_interval_hours": 6,
            "member": args.rolling_member,
            "start_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[0], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "end_init_utc": iso_utc(dt.datetime.strptime(rolling_inits[-1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)),
            "source": "lagged CFSv2 initial conditions",
        }
    if args.absolute:
        run_entry["baseline"] = {"status": "not_applicable", "reason": "absolute smoke output"}
    elif args.decode_only:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
            "status": "not_applied_decode_only",
        }
    elif args.ncei_calibration:
        run_entry["baseline"] = {
            "source": NCEI_CALIBRATION_LABEL,
            "years": NCEI_CALIBRATION_YEARS,
            "url_root": NCEI_CALIBRATION_ROOT,
            "required": True,
        }
    else:
        run_entry["baseline"] = {
            "source": configured_baseline_label(args),
            "years": args.baseline_years or None,
            "required": True,
        }
    if rolling_mode and not args.absolute:
        run_entry["baseline"]["rolling_policy"] = "anchor_initialization"

    last_request = 0.0
    failures = 0
    forecast_grids: dict[int, Grid] = {}
    baseline_grids: dict[int, Grid] = {}
    target_entries_by_lead: dict[int, dict] = {}
    for lead in leads:
        target = target_month(init, lead)
        valid_start, valid_end = target_period(target)
        target_entry = {
            "id": f"cfsv2-{target}-z500{'-absolute' if args.absolute else 'a'}-lead{lead:02d}",
            "valid_start_utc": valid_start,
            "valid_end_utc": valid_end,
            "lead_month": lead,
            "target_month": target,
            "aggregation": "monthly forecast average",
            "field": "z500" if args.absolute else "z500_anomaly",
            "units": "m",
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "source_files": [],
            "status": "planned",
        }
        try:
            ensemble, source_files, ensemble_count, ensemble_expected_for_target, ensemble_label, last_request = decode_target_ensemble(
                args,
                init,
                target,
                members,
                rolling_inits,
                cache_dir,
                state_dir,
                wgrib2,
                repo_root,
                last_request,
            )
            target_entry["source_files"] = source_files
            target_entry["ensemble_members"] = ensemble_count
            target_entry["ensemble_expected_members"] = ensemble_expected_for_target
            target_entry["ensemble_complete"] = ensemble_count == ensemble_expected_for_target
            target_entry["ensemble_label"] = ensemble_label
            forecast_grids[lead] = ensemble
            target_entry["status"] = "partial" if ensemble_count < ensemble_expected_for_target else "decoded"
            if args.decode_only:
                run_entry["targets"].append(target_entry)
                target_entries_by_lead[lead] = target_entry
                print(f"decoded CFSv2 {target} lead {lead} from {ensemble_count}/{ensemble_expected_for_target} member(s)")
                continue

            baseline_label = "absolute field smoke output"
            anomaly_grid = ensemble
            if not args.absolute:
                baseline_url = None
                baseline_downloaded = False
                if args.ncei_calibration:
                    baseline_url = ncei_calibration_url(init, lead)
                    baseline_path = cached_calibration_path(cache_dir, init, lead)
                    baseline_downloaded, last_request = download_file(
                        baseline_url,
                        baseline_path,
                        max(0.0, args.request_delay),
                        last_request,
                    )
                    baseline_label = configured_baseline_label(args)
                else:
                    baseline_path, baseline_label = baseline_for_target(args, target, repo_root)
                baseline_grid = load_baseline(baseline_path, wgrib2)
                baseline_grids[lead] = baseline_grid
                anomaly_grid = subtract_grids(ensemble, baseline_grid)
                target_entry["baseline"] = {
                    "file": relative_path(baseline_path, repo_root),
                    "label": baseline_label,
                    "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                }
                if rolling_mode:
                    target_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                    target_entry["baseline"]["anchor_init"] = init
                if baseline_url:
                    target_entry["baseline"]["url"] = baseline_url
                    target_entry["baseline"]["downloaded"] = baseline_downloaded

            output_path = output_dir / init / f"cfsv2_z500{'a' if not args.absolute else ''}_{target}.jpg"
            render_map(
                anomaly_grid,
                init,
                target,
                lead,
                members,
                output_path,
                anomaly=not args.absolute,
                baseline_label=baseline_label,
                border_paths=border_paths,
                ensemble_label=ensemble_label,
            )
            target_entry["image"] = relative_path(output_path, repo_root)
            target_entry["status"] = "partial" if not target_entry["ensemble_complete"] else "rendered"
            print(f"rendered CFSv2 {target} lead {lead}: {output_path}")
        except Exception as exc:
            failures += 1
            target_entry["status"] = "failed"
            target_entry["error"] = str(exc)
            print(f"CFSv2 target {target} lead {lead} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(target_entry)
        target_entries_by_lead[lead] = target_entry

    if seasonal_leads and not args.decode_only:
        first_lead = seasonal_leads[0]
        last_lead = seasonal_leads[-1]
        first_target = target_month(init, first_lead)
        last_target = target_month(init, last_lead)
        seasonal_id_suffix = "-absolute" if args.absolute else "a"
        seasonal_entry = {
            "id": f"cfsv2-{first_target}-{last_target}-z500{seasonal_id_suffix}-seasonal",
            "valid_start_utc": target_period(first_target)[0],
            "valid_end_utc": target_period(last_target)[1],
            "lead_month": f"{first_lead}-{last_lead}",
            "target_month": f"{first_target}-{last_target}",
            "aggregation": f"{len(seasonal_leads)}-month seasonal mean",
            "field": "z500" if args.absolute else "z500_anomaly",
            "units": "m",
            "statistic": "ensemble_mean",
            "members": [args.rolling_member] if rolling_mode else members,
            "ensemble_members": ensemble_expected,
            "ensemble_scope": "rolling_initial_conditions" if rolling_mode else "single_initial_condition_cycle",
            "monthly_leads": seasonal_leads,
            "source_files": [],
            "status": "planned",
        }
        try:
            missing_forecasts = [lead for lead in seasonal_leads if lead not in forecast_grids]
            if missing_forecasts:
                raise CFSv2Error(f"seasonal window is missing decoded lead(s): {missing_forecasts}")
            seasonal_forecast = mean_grids([forecast_grids[lead] for lead in seasonal_leads])
            seasonal_grid = seasonal_forecast
            baseline_label = "absolute field smoke output"
            if not args.absolute:
                missing_baselines = [lead for lead in seasonal_leads if lead not in baseline_grids]
                if missing_baselines:
                    raise CFSv2Error(f"seasonal window is missing baseline lead(s): {missing_baselines}")
                seasonal_baseline = mean_grids([baseline_grids[lead] for lead in seasonal_leads])
                seasonal_grid = subtract_grids(seasonal_forecast, seasonal_baseline)
                baseline_label = configured_baseline_label(args)
                seasonal_entry["baseline"] = {
                    "files": [
                        target_entries_by_lead[lead]["baseline"]["file"]
                        for lead in seasonal_leads
                        if "baseline" in target_entries_by_lead.get(lead, {})
                    ],
                    "label": baseline_label,
                    "years": NCEI_CALIBRATION_YEARS if args.ncei_calibration else (args.baseline_years or None),
                }
                if rolling_mode:
                    seasonal_entry["baseline"]["rolling_policy"] = "anchor_initialization"
                    seasonal_entry["baseline"]["anchor_init"] = init
                baseline_urls = [
                    target_entries_by_lead[lead]["baseline"].get("url")
                    for lead in seasonal_leads
                    if target_entries_by_lead[lead].get("baseline", {}).get("url")
                ]
                if baseline_urls:
                    seasonal_entry["baseline"]["urls"] = baseline_urls
            else:
                seasonal_entry["baseline"] = {"status": "not_applicable", "reason": "absolute smoke output"}
            seasonal_entry["source_files"] = [
                source_file
                for lead in seasonal_leads
                for source_file in target_entries_by_lead[lead].get("source_files", [])
            ]
            seasonal_entry["ensemble_complete"] = all(
                target_entries_by_lead[lead].get("ensemble_complete", False)
                for lead in seasonal_leads
            )
            seasonal_entry["ensemble_members"] = min(
                target_entries_by_lead[lead].get("ensemble_members", 0)
                for lead in seasonal_leads
            )
            start_date = dt.datetime.strptime(first_target, "%Y%m")
            end_date = dt.datetime.strptime(last_target, "%Y%m")
            if start_date.year == end_date.year:
                period_label = f"{start_date:%b}\u2013{end_date:%b %Y}"
            else:
                period_label = f"{start_date:%b %Y}\u2013{end_date:%b %Y}"
            output_path = output_dir / init / f"cfsv2_z500{'a' if not args.absolute else ''}_{first_target}-{last_target}.jpg"
            render_map(
                seasonal_grid,
                init,
                first_target,
                f"{first_lead}\u2013{last_lead}",
                members,
                output_path,
                anomaly=not args.absolute,
                baseline_label=baseline_label,
                border_paths=border_paths,
                period_label=period_label,
                ensemble_label=(
                    f"{ensemble_expected}-member rolling mean"
                    if rolling_mode
                    else f"{len(members)}-member mean"
                ),
            )
            seasonal_entry["image"] = relative_path(output_path, repo_root)
            seasonal_entry["status"] = "rendered" if seasonal_entry["ensemble_complete"] else "partial"
            print(f"rendered CFSv2 seasonal mean {first_target}-{last_target}: {output_path}")
        except Exception as exc:
            failures += 1
            seasonal_entry["status"] = "failed"
            seasonal_entry["error"] = str(exc)
            print(f"CFSv2 seasonal window {first_target}-{last_target} failed: {exc}", file=sys.stderr)
        run_entry["targets"].append(seasonal_entry)

    statuses = [target["status"] for target in run_entry["targets"]]
    partial_targets = any(status == "partial" for status in statuses)
    if failures or partial_targets:
        run_entry["status"] = "partial" if any(status != "failed" for status in statuses) else "failed"
    elif args.decode_only:
        run_entry["status"] = "decoded"
    else:
        run_entry["status"] = "rendered"
    run_entry["output_dir"] = relative_path(output_dir, repo_root)
    write_manifest(manifest_path, repo_root, run_entry)
    print(f"wrote CFSv2 manifest: {manifest_path}")
    return 2 if failures else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except CFSv2Error as exc:
        print(f"CFSv2 error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
