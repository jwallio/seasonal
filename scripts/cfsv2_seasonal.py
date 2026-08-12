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
             ë½:¶‰žËkºwµçUÍÑ¥¹œˆ(€€€€€€€€¤(€€€ÝÉ¥ˆÈ€ô™¥¹‘}ÝÉ¥ˆÈ¡…ÉÌ¹ÝÉ¥ˆÈ¤(€€€…¡•}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹…¡•}‘¥È°É•Á½}É½½Ð¤(€€€ÍÑ…Ñ•}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹É½±±¥¹}ÍÑ…Ñ•}‘¥È°É•Á½}É½½Ð¤(€€€½ÕÑÁÕÑ}‘¥È€ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤(€€€µ…¹¥™•ÍÑ}Á…Ñ €ôÉ•Í½±Ù•}É•Á½}Á…Ñ ¡…ÉÌ¹µ…¹¥™•ÍÐ°É•Á½}É½½Ð¤(€€€…¡•}‘¥È¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€‰½É‘•É}Á…Ñ¡Ì€ômt¥˜…ÉÌ¹‘•½‘•}½¹±ä•±Í”•¹ÍÕÉ•}‰½É‘•É}™¥±•Ì¡…ÉÌ°…¡•}‘¥È°É•Á½}É½½Ð¤((€€€¥¹¥Ñ}‘…Ñ”€ô‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡¥¹¥Ð°€ˆ•d•´•• ˆ¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤(€€€ÉÕ¹}¥€ô˜‰™ÍØÈµí¥¹¥Ñôˆ(€€€É½±±¥¹}µ½‘”€ô‰½½°¡É½±±¥¹}¥¹¥ÑÌ¤(€€€•¹Í•µ‰±•}•áÁ•Ñ•€ô±•¸¡É½±±¥¹}¥¹¥ÑÌ¤¥˜É½±±¥¹}µ½‘”•±Í”±•¸¡µ•µ‰•ÉÌ¤(€€€ÉÕ¹}•¹ÑÉä€ôì(€€€€€€€€‰¥ˆèÉÕ¹}¥°(€€€€€€€€‰Í½ÕÉ”ˆè€‰9=MØÈ9=5Lˆ°(€€€€€€€€‰Í½ÕÉ•}ÕÉ°ˆè9=5M}I==P°(€€€€€€€€‰µ½‘•°ˆè€‰MØÈˆ°(€€€€€€€€‰¥¹¥Ñ}ÕÑŒˆè¥Í½}ÕÑŒ¡¥¹¥Ñ}‘…Ñ”¤°(€€€€€€€€‰‘•½‘•Èˆèì‰Ñ½½°ˆè€‰ÝÉ¥ˆÈˆ°€‰•á•ÕÑ…‰±”ˆèÝÉ¥ˆÉô°(€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€‰µ•µ‰•ÉÌˆèm…ÉÌ¹É½±±¥¹}µ•µ‰•Ét¥˜É½±±¥¹}µ½‘”•±Í”µ•µ‰•ÉÌ°(€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè•¹Í•µ‰±•}•áÁ•Ñ•°(€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€‰É½±±¥¹}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹Ìˆ¥˜É½±±¥¹}µ½‘”•±Í”€‰Í¥¹±•}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹}å±”ˆ°(€€€€€€€€‰…É•…Ñ¥½¸ˆè€ (€€€€€€€€€€€˜‰í…ÉÌ¹É½±±¥¹}‘…åÍôµ‘…äÉ½±±¥¹œ¥¹¥Ñ¥…°µ½¹‘¥Ñ¥½¸µ•…¸ˆ(€€€€€€€€€€€¥˜É½±±¥¹}µ½‘”(€€€€€€€€€€€•±Í”€‰µ½¹Ñ¡±ä™½É•…ÍÐ…Ù•É…”ˆ(€€€€€€€€¤€¬€ ˆì½ÁÑ¥½¹…°Í•…Í½¹…°µ•…¸ˆ¥˜Í•…Í½¹…±}±•…‘Ì•±Í”€ˆˆ¤°(€€€€€€€€‰™¥•±ˆè€‰èÔÀÀˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”€‰èÔÀÁ}…¹½µ…±äˆ°(€€€€€€€€‰Õ¹¥ÑÌˆè€‰´ˆ°(€€€€€€€€‰‰½É‘•É}Í½ÕÉ•Ìˆè€ (€€€€€€€€€€€mt(€€€€€€€€€€€¥˜…ÉÌ¹¹½}‰½É‘•ÉÌ(€€€€€€€€€€€•±Í”€ (€€€€€€€€€€€€€€€mì‰™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡É•Í½±Ù•}É•Á½}Á…Ñ ¡Á…Ñ °É•Á½}É½½Ð¤°É•Á½}É½½Ð¥ô™½ÈÁ…Ñ ¥¸…ÉÌ¹‰½É‘•É}•½©Í½¹t(€€€€€€€€€€€€€€€¥˜…ÉÌ¹‰½É‘•É}•½©Í½¸(€€€€€€€€€€€€€€€•±Í”mì‰¹…µ”ˆè¹…µ”°€‰ÕÉ°ˆèÕÉ±ô™½È¹…µ”°ÕÉ°¥¸U1Q}	=II}UI1Mt(€€€€€€€€€€€€¤(€€€€€€€€¤°(€€€€€€€€‰‰…Í•±¥¹”ˆè9½¹”°(€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€€‰Ñ…É•ÑÌˆèmt°(€€€ô(€€€¥˜É½±±¥¹}µ½‘”è(€€€€€€€ÉÕ¹}•¹ÑÉål‰É½±±¥¹}Ý¥¹‘½Ü‰t€ôì(€€€€€€€€€€€€‰‘…åÌˆè…ÉÌ¹É½±±¥¹}‘…åÌ°(€€€€€€€€€€€€‰•áÁ•Ñ•‘}å±•Ìˆè±•¸¡É½±±¥¹}¥¹¥ÑÌ¤°(€€€€€€€€€€€€‰å±•}¥¹Ñ•ÉÙ…±}¡½ÕÉÌˆè€Ø°(€€€€€€€€€€€€‰µ•µ‰•Èˆè…ÉÌ¹É½±±¥¹}µ•µ‰•È°(€€€€€€€€€€€€‰ÍÑ…ÉÑ}¥¹¥Ñ}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡É½±±¥¹}¥¹¥ÑÍlÁt°€ˆ•d•´•• ˆ¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€€€€€‰•¹‘}¥¹¥Ñ}ÕÑŒˆè¥Í½}ÕÑŒ¡‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡É½±±¥¹}¥¹¥ÑÍl´Åt°€ˆ•d•´•• ˆ¤¹É•Á±…”¡Ñé¥¹™¼õ‘Ð¹Ñ¥µ•é½¹”¹ÕÑŒ¤¤°(€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰±…•MØÈ¥¹¥Ñ¥…°½¹‘¥Ñ¥½¹Ìˆ°(€€€€€€€ô(€€€¥˜…ÉÌ¹…‰Í½±ÕÑ”è(€€€€€€€ÉÕ¹}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}…ÁÁ±¥…‰±”ˆ°€‰É•…Í½¸ˆè€‰…‰Í½±ÕÑ”Íµ½­”½ÕÑÁÕÐ‰ô(€€€•±¥˜…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€ÉÕ¹}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€‰Í½ÕÉ”ˆè½¹™¥ÕÉ•‘}‰…Í•±¥¹•}±…‰•°¡…ÉÌ¤°(€€€€€€€€€€€€‰å•…ÉÌˆè…ÉÌ¹‰…Í•±¥¹•}å•…ÉÌ½È9½¹”°(€€€€€€€€€€€€‰É•ÅÕ¥É•ˆèQÉÕ”°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}…ÁÁ±¥•‘}‘•½‘•}½¹±äˆ°(€€€€€€€ô(€€€•±¥˜…ÉÌ¹¹•¥}…±¥‰É…Ñ¥½¸è(€€€€€€€ÉÕ¹}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€‰Í½ÕÉ”ˆè9%}1%	IQ%=9}1	0°(€€€€€€€€€€€€‰å•…ÉÌˆè9%}1%	IQ%=9}eIL°(€€€€€€€€€€€€‰ÕÉ±}É½½Ðˆè9%}1%	IQ%=9}I==P°(€€€€€€€€€€€€‰É•ÅÕ¥É•ˆèQÉÕ”°(€€€€€€€ô(€€€•±Í”è(€€€€€€€ÉÕ¹}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€‰Í½ÕÉ”ˆè½¹™¥ÕÉ•‘}‰…Í•±¥¹•}±…‰•°¡…ÉÌ¤°(€€€€€€€€€€€€‰å•…ÉÌˆè…ÉÌ¹‰…Í•±¥¹•}å•…ÉÌ½È9½¹”°(€€€€€€€€€€€€‰É•ÅÕ¥É•ˆèQÉÕ”°(€€€€€€€ô(€€€¥˜É½±±¥¹}µ½‘”…¹¹½Ð…ÉÌ¹…‰Í½±ÕÑ”è(€€€€€€€ÉÕ¹}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰É½±±¥¹}Á½±¥ä‰t€ô€‰…¹¡½É}¥¹¥Ñ¥…±¥é…Ñ¥½¸ˆ((€€€±…ÍÑ}É•ÅÕ•ÍÐ€ô€À¸À(€€€™…¥±ÕÉ•Ì€ô€À(€€€™½É•…ÍÑ}É¥‘Ìè‘¥Ñm¥¹Ð°É¥‘t€ôíô(€€€‰…Í•±¥¹•}É¥‘Ìè‘¥Ñm¥¹Ð°É¥‘t€ôíô(€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…è‘¥Ñm¥¹Ð°‘¥Ñt€ôíô(€€€™½È±•…¥¸±•…‘Ìè(€€€€€€€Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±•…¤(€€€€€€€Ù…±¥‘}ÍÑ…ÉÐ°Ù…±¥‘}•¹€ôÑ…É•Ñ}Á•É¥½¡Ñ…É•Ð¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉä€ôì(€€€€€€€€€€€€‰¥ˆè˜‰™ÍØÈµíÑ…É•ÑôµèÔÀÁìœµ…‰Í½±ÕÑ”œ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”€„ôµ±•…‘í±•…èÀÉ‘ôˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÙ…±¥‘}ÍÑ…ÉÐ°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÙ…±¥‘}•¹°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè±•…°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆèÑ…É•Ð°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè€‰µ½¹Ñ¡±ä™½É•…ÍÐ…Ù•É…”ˆ°(€€€€€€€€€€€€‰™¥•±ˆè€‰èÔÀÀˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”€‰èÔÀÁ}…¹½µ…±äˆ°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆè€‰´ˆ°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰µ•µ‰•ÉÌˆèm…ÉÌ¹É½±±¥¹}µ•µ‰•Ét¥˜É½±±¥¹}µ½‘”•±Í”µ•µ‰•ÉÌ°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè•¹Í•µ‰±•}•áÁ•Ñ•°(€€€€€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€‰É½±±¥¹}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹Ìˆ¥˜É½±±¥¹}µ½‘”•±Í”€‰Í¥¹±•}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹}å±”ˆ°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±•Ìˆèmt°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€•¹Í•µ‰±”°Í½ÕÉ•}™¥±•Ì°•¹Í•µ‰±•}½Õ¹Ð°•¹Í•µ‰±•}•áÁ•Ñ•‘}™½É}Ñ…É•Ð°•¹Í•µ‰±•}±…‰•°°±…ÍÑ}É•ÅÕ•ÍÐ€ô‘•½‘•}Ñ…É•Ñ}•¹Í•µ‰±” (€€€€€€€€€€€€€€€…ÉÌ°(€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€µ•µ‰•ÉÌ°(€€€€€€€€€€€€€€€É½±±¥¹}¥¹¥ÑÌ°(€€€€€€€€€€€€€€€…¡•}‘¥È°(€€€€€€€€€€€€€€€ÍÑ…Ñ•}‘¥È°(€€€€€€€€€€€€€€€ÝÉ¥ˆÈ°(€€€€€€€€€€€€€€€É•Á½}É½½Ð°(€€€€€€€€€€€€€€€±…ÍÑ}É•ÅÕ•ÍÐ°(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰Í½ÕÉ•}™¥±•Ì‰t€ôÍ½ÕÉ•}™¥±•Ì(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}µ•µ‰•ÉÌ‰t€ô•¹Í•µ‰±•}½Õ¹Ð(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}•áÁ•Ñ•‘}µ•µ‰•ÉÌ‰t€ô•¹Í•µ‰±•}•áÁ•Ñ•‘}™½É}Ñ…É•Ð(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}½µÁ±•Ñ”‰t€ô•¹Í•µ‰±•}½Õ¹Ð€ôô•¹Í•µ‰±•}•áÁ•Ñ•‘}™½É}Ñ…É•Ð(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}±…‰•°‰t€ô•¹Í•µ‰±•}±…‰•°(€€€€€€€€€€€™½É•…ÍÑ}É¥‘Ím±•…‘t€ô•¹Í•µ‰±”(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰Á…ÉÑ¥…°ˆ¥˜•¹Í•µ‰±•}½Õ¹Ð€ð•¹Í•µ‰±•}•áÁ•Ñ•‘}™½É}Ñ…É•Ð•±Í”€‰‘•½‘•ˆ(€€€€€€€€€€€¥˜…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€€€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t€ôÑ…É•Ñ}•¹ÑÉä(€€€€€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰‘•½‘•MØÈíÑ…É•Ñô±•…í±•…‘ô™É½´í•¹Í•µ‰±•}½Õ¹Ñô½í•¹Í•µ‰±•}•áÁ•Ñ•‘}™½É}Ñ…É•Ñôµ•µ‰•È¡Ì¤ˆ¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”((€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°€ô€‰…‰Í½±ÕÑ”™¥•±Íµ½­”½ÕÑÁÕÐˆ(€€€€€€€€€€€…¹½µ…±å}É¥€ô•¹Í•µ‰±”(€€€€€€€€€€€¥˜¹½Ð…ÉÌ¹…‰Í½±ÕÑ”è(€€€€€€€€€€€€€€€‰…Í•±¥¹•}ÕÉ°€ô9½¹”(€€€€€€€€€€€€€€€‰…Í•±¥¹•}‘½Ý¹±½…‘•€ô…±Í”(€€€€€€€€€€€€€€€¥˜…ÉÌ¹¹•¥}…±¥‰É…Ñ¥½¸è(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}ÕÉ°€ô¹•¥}…±¥‰É…Ñ¥½¹}ÕÉ°¡¥¹¥Ð°±•…¤(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}Á…Ñ €ô…¡•‘}…±¥‰É…Ñ¥½¹}Á…Ñ ¡…¡•}‘¥È°¥¹¥Ð°±•…¤(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}‘½Ý¹±½…‘•°±…ÍÑ}É•ÅÕ•ÍÐ€ô‘½Ý¹±½…‘}™¥±” (€€€€€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}ÕÉ°°(€€€€€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}Á…Ñ °(€€€€€€€€€€€€€€€€€€€€€€€µ…à À¸À°…ÉÌ¹É•ÅÕ•ÍÑ}‘•±…ä¤°(€€€€€€€€€€€€€€€€€€€€€€€±…ÍÑ}É•ÅÕ•ÍÐ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°€ô½¹™¥ÕÉ•‘}‰…Í•±¥¹•}±…‰•°¡…ÉÌ¤(€€€€€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€€€€€‰…Í•±¥¹•}Á…Ñ °‰…Í•±¥¹•}±…‰•°€ô‰…Í•±¥¹•}™½É}Ñ…É•Ð¡…ÉÌ°Ñ…É•Ð°É•Á½}É½½Ð¤(€€€€€€€€€€€€€€€‰…Í•±¥¹•}É¥€ô±½…‘}‰…Í•±¥¹”¡‰…Í•±¥¹•}Á…Ñ °ÝÉ¥ˆÈ¤(€€€€€€€€€€€€€€€‰…Í•±¥¹•}É¥‘Ím±•…‘t€ô‰…Í•±¥¹•}É¥(€€€€€€€€€€€€€€€…¹½µ…±å}É¥€ôÍÕ‰ÑÉ…Ñ}É¥‘Ì¡•¹Í•µ‰±”°‰…Í•±¥¹•}É¥¤(€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰™¥±”ˆèÉ•±…Ñ¥Ù•}Á…Ñ ¡‰…Í•±¥¹•}Á…Ñ °É•Á½}É½½Ð¤°(€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè9%}1%	IQ%=9}eIL¥˜…ÉÌ¹¹•¥}…±¥‰É…Ñ¥½¸•±Í”€¡…ÉÌ¹‰…Í•±¥¹•}å•…ÉÌ½È9½¹”¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€¥˜É½±±¥¹}µ½‘”è(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰É½±±¥¹}Á½±¥ä‰t€ô€‰…¹¡½É}¥¹¥Ñ¥…±¥é…Ñ¥½¸ˆ(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰…¹¡½É}¥¹¥Ð‰t€ô¥¹¥Ð(€€€€€€€€€€€€€€€¥˜‰…Í•±¥¹•}ÕÉ°è(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰ÕÉ°‰t€ô‰…Í•±¥¹•}ÕÉ°(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰‘½Ý¹±½…‘•‰t€ô‰…Í•±¥¹•}‘½Ý¹±½…‘•((€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ð€¼˜‰™ÍØÉ}èÔÀÁì„œ¥˜¹½Ð…ÉÌ¹…‰Í½±ÕÑ”•±Í”€œõ}íÑ…É•Ñô¹©Áœˆ(€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€…¹½µ…±å}É¥°(€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€Ñ…É•Ð°(€€€€€€€€€€€€€€€±•…°(€€€€€€€€€€€€€€€µ•µ‰•ÉÌ°(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€…¹½µ…±äõ¹½Ð…ÉÌ¹…‰Í½±ÕÑ”°(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°õ•¹Í•µ‰±•}±…‰•°°(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰Á…ÉÑ¥…°ˆ¥˜¹½ÐÑ…É•Ñ}•¹ÑÉål‰•¹Í•µ‰±•}½µÁ±•Ñ”‰t•±Í”€‰É•¹‘•É•ˆ(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰É•¹‘•É•MØÈíÑ…É•Ñô±•…í±•…‘ôèí½ÕÑÁÕÑ}Á…Ñ¡ôˆ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰MØÈÑ…É•ÐíÑ…É•Ñô±•…í±•…‘ô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Ñ…É•Ñ}•¹ÑÉä¤(€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t€ôÑ…É•Ñ}•¹ÑÉä((€€€¥˜Í•…Í½¹…±}±•…‘Ì…¹¹½Ð…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€™¥ÉÍÑ}±•…€ôÍ•…Í½¹…±}±•…‘ÍlÁt(€€€€€€€±…ÍÑ}±•…€ôÍ•…Í½¹…±}±•…‘Íl´Åt(€€€€€€€™¥ÉÍÑ}Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°™¥ÉÍÑ}±•…¤(€€€€€€€±…ÍÑ}Ñ…É•Ð€ôÑ…É•Ñ}µ½¹Ñ ¡¥¹¥Ð°±…ÍÑ}±•…¤(€€€€€€€Í•…Í½¹…±}¥‘}ÍÕ™™¥à€ô€ˆµ…‰Í½±ÕÑ”ˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”€‰„ˆ(€€€€€€€Í•…Í½¹…±}•¹ÑÉä€ôì(€€€€€€€€€€€€‰¥ˆè˜‰™ÍØÈµí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•ÑôµèÔÀÁíÍ•…Í½¹…±}¥‘}ÍÕ™™¥áôµÍ•…Í½¹…°ˆ°(€€€€€€€€€€€€‰Ù…±¥‘}ÍÑ…ÉÑ}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡™¥ÉÍÑ}Ñ…É•Ð¥lÁt°(€€€€€€€€€€€€‰Ù…±¥‘}•¹‘}ÕÑŒˆèÑ…É•Ñ}Á•É¥½¡±…ÍÑ}Ñ…É•Ð¥lÅt°(€€€€€€€€€€€€‰±•…‘}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}±•…‘ôµí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€‰Ñ…É•Ñ}µ½¹Ñ ˆè˜‰í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôˆ°(€€€€€€€€€€€€‰…É•…Ñ¥½¸ˆè˜‰í±•¸¡Í•…Í½¹…±}±•…‘Ì¥ôµµ½¹Ñ Í•…Í½¹…°µ•…¸ˆ°(€€€€€€€€€€€€‰™¥•±ˆè€‰èÔÀÀˆ¥˜…ÉÌ¹…‰Í½±ÕÑ”•±Í”€‰èÔÀÁ}…¹½µ…±äˆ°(€€€€€€€€€€€€‰Õ¹¥ÑÌˆè€‰´ˆ°(€€€€€€€€€€€€‰ÍÑ…Ñ¥ÍÑ¥Œˆè€‰•¹Í•µ‰±•}µ•…¸ˆ°(€€€€€€€€€€€€‰µ•µ‰•ÉÌˆèm…ÉÌ¹É½±±¥¹}µ•µ‰•Ét¥˜É½±±¥¹}µ½‘”•±Í”µ•µ‰•ÉÌ°(€€€€€€€€€€€€‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆè•¹Í•µ‰±•}•áÁ•Ñ•°(€€€€€€€€€€€€‰•¹Í•µ‰±•}Í½Á”ˆè€‰É½±±¥¹}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹Ìˆ¥˜É½±±¥¹}µ½‘”•±Í”€‰Í¥¹±•}¥¹¥Ñ¥…±}½¹‘¥Ñ¥½¹}å±”ˆ°(€€€€€€€€€€€€‰µ½¹Ñ¡±å}±•…‘ÌˆèÍ•…Í½¹…±}±•…‘Ì°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±•Ìˆèmt°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰Á±…¹¹•ˆ°(€€€€€€€ô(€€€€€€€ÑÉäè(€€€€€€€€€€€µ¥ÍÍ¥¹}™½É•…ÍÑÌ€ôm±•…™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¥˜±•…¹½Ð¥¸™½É•…ÍÑ}É¥‘Ít(€€€€€€€€€€€¥˜µ¥ÍÍ¥¹}™½É•…ÍÑÌè(€€€€€€€€€€€€€€€É…¥Í”MØÉÉÉ½È¡˜‰Í•…Í½¹…°Ý¥¹‘½Ü¥Ìµ¥ÍÍ¥¹œ‘•½‘•±•…¡Ì¤èíµ¥ÍÍ¥¹}™½É•…ÍÑÍôˆ¤(€€€€€€€€€€€Í•…Í½¹…±}™½É•…ÍÐ€ôµ•…¹}É¥‘Ì¡m™½É•…ÍÑ}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€Í•…Í½¹…±}É¥€ôÍ•…Í½¹…±}™½É•…ÍÐ(€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°€ô€‰…‰Í½±ÕÑ”™¥•±Íµ½­”½ÕÑÁÕÐˆ(€€€€€€€€€€€¥˜¹½Ð…ÉÌ¹…‰Í½±ÕÑ”è(€€€€€€€€€€€€€€€µ¥ÍÍ¥¹}‰…Í•±¥¹•Ì€ôm±•…™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì¥˜±•…¹½Ð¥¸‰…Í•±¥¹•}É¥‘Ít(€€€€€€€€€€€€€€€¥˜µ¥ÍÍ¥¹}‰…Í•±¥¹•Ìè(€€€€€€€€€€€€€€€€€€€É…¥Í”MØÉÉÉ½È¡˜‰Í•…Í½¹…°Ý¥¹‘½Ü¥Ìµ¥ÍÍ¥¹œ‰…Í•±¥¹”±•…¡Ì¤èíµ¥ÍÍ¥¹}‰…Í•±¥¹•Íôˆ¤(€€€€€€€€€€€€€€€Í•…Í½¹…±}‰…Í•±¥¹”€ôµ•…¹}É¥‘Ì¡m‰…Í•±¥¹•}É¥‘Ím±•…‘t™½È±•…¥¸Í•…Í½¹…±}±•…‘Ít¤(€€€€€€€€€€€€€€€Í•…Í½¹…±}É¥€ôÍÕ‰ÑÉ…Ñ}É¥‘Ì¡Í•…Í½¹…±}™½É•…ÍÐ°Í•…Í½¹…±}‰…Í•±¥¹”¤(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°€ô½¹™¥ÕÉ•‘}‰…Í•±¥¹•}±…‰•°¡…ÉÌ¤(€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì(€€€€€€€€€€€€€€€€€€€€‰™¥±•Ìˆèl(€€€€€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘ul‰‰…Í•±¥¹”‰ul‰™¥±”‰t(€€€€€€€€€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€€€€€€€€€€€€¥˜€‰‰…Í•±¥¹”ˆ¥¸Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…¹•Ð¡±•…°íô¤(€€€€€€€€€€€€€€€€€€€t°(€€€€€€€€€€€€€€€€€€€€‰±…‰•°ˆè‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€€€€€€‰å•…ÉÌˆè9%}1%	IQ%=9}eIL¥˜…ÉÌ¹¹•¥}…±¥‰É…Ñ¥½¸•±Í”€¡…ÉÌ¹‰…Í•±¥¹•}å•…ÉÌ½È9½¹”¤°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€¥˜É½±±¥¹}µ½‘”è(€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰É½±±¥¹}Á½±¥ä‰t€ô€‰…¹¡½É}¥¹¥Ñ¥…±¥é…Ñ¥½¸ˆ(€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰…¹¡½É}¥¹¥Ð‰t€ô¥¹¥Ð(€€€€€€€€€€€€€€€‰…Í•±¥¹•}ÕÉ±Ì€ôl(€€€€€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘ul‰‰…Í•±¥¹”‰t¹•Ð ‰ÕÉ°ˆ¤(€€€€€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€€€€€€€€¥˜Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t¹•Ð ‰‰…Í•±¥¹”ˆ°íô¤¹•Ð ‰ÕÉ°ˆ¤(€€€€€€€€€€€€€€€t(€€€€€€€€€€€€€€€¥˜‰…Í•±¥¹•}ÕÉ±Ìè(€€€€€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰ul‰ÕÉ±Ì‰t€ô‰…Í•±¥¹•}ÕÉ±Ì(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰‰…Í•±¥¹”‰t€ôì‰ÍÑ…ÑÕÌˆè€‰¹½Ñ}…ÁÁ±¥…‰±”ˆ°€‰É•…Í½¸ˆè€‰…‰Í½±ÕÑ”Íµ½­”½ÕÑÁÕÐ‰ô(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰Í½ÕÉ•}™¥±•Ì‰t€ôl(€€€€€€€€€€€€€€€Í½ÕÉ•}™¥±”(€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€€€€™½ÈÍ½ÕÉ•}™¥±”¥¸Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t¹•Ð ‰Í½ÕÉ•}™¥±•Ìˆ°mt¤(€€€€€€€€€€€t(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰•¹Í•µ‰±•}½µÁ±•Ñ”‰t€ô…±° (€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t¹•Ð ‰•¹Í•µ‰±•}½µÁ±•Ñ”ˆ°…±Í”¤(€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰•¹Í•µ‰±•}µ•µ‰•ÉÌ‰t€ôµ¥¸ (€€€€€€€€€€€€€€€Ñ…É•Ñ}•¹ÑÉ¥•Í}‰å}±•…‘m±•…‘t¹•Ð ‰•¹Í•µ‰±•}µ•µ‰•ÉÌˆ°€À¤(€€€€€€€€€€€€€€€™½È±•…¥¸Í•…Í½¹…±}±•…‘Ì(€€€€€€€€€€€€¤(€€€€€€€€€€€ÍÑ…ÉÑ}‘…Ñ”€ô‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡™¥ÉÍÑ}Ñ…É•Ð°€ˆ•d•´ˆ¤(€€€€€€€€€€€•¹‘}‘…Ñ”€ô‘Ð¹‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡±…ÍÑ}Ñ…É•Ð°€ˆ•d•´ˆ¤(€€€€€€€€€€€¥˜ÍÑ…ÉÑ}‘…Ñ”¹å•…È€ôô•¹‘}‘…Ñ”¹å•…Èè(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°€ô˜‰íÍÑ…ÉÑ}‘…Ñ”è•‰õqÔÈÀÄÍí•¹‘}‘…Ñ”è•ˆ€•eôˆ(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°€ô˜‰íÍÑ…ÉÑ}‘…Ñ”è•ˆ€•eõqÔÈÀÄÍí•¹‘}‘…Ñ”è•ˆ€•eôˆ(€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ €ô½ÕÑÁÕÑ}‘¥È€¼¥¹¥Ð€¼˜‰™ÍØÉ}èÔÀÁì„œ¥˜¹½Ð…ÉÌ¹…‰Í½±ÕÑ”•±Í”€œõ}í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô¹©Áœˆ(€€€€€€€€€€€É•¹‘•É}µ…À (€€€€€€€€€€€€€€€Í•…Í½¹…±}É¥°(€€€€€€€€€€€€€€€¥¹¥Ð°(€€€€€€€€€€€€€€€™¥ÉÍÑ}Ñ…É•Ð°(€€€€€€€€€€€€€€€˜‰í™¥ÉÍÑ}±•…‘õqÔÈÀÄÍí±…ÍÑ}±•…‘ôˆ°(€€€€€€€€€€€€€€€µ•µ‰•ÉÌ°(€€€€€€€€€€€€€€€½ÕÑÁÕÑ}Á…Ñ °(€€€€€€€€€€€€€€€…¹½µ…±äõ¹½Ð…ÉÌ¹…‰Í½±ÕÑ”°(€€€€€€€€€€€€€€€‰…Í•±¥¹•}±…‰•°õ‰…Í•±¥¹•}±…‰•°°(€€€€€€€€€€€€€€€‰½É‘•É}Á…Ñ¡Ìõ‰½É‘•É}Á…Ñ¡Ì°(€€€€€€€€€€€€€€€Á•É¥½‘}±…‰•°õÁ•É¥½‘}±…‰•°°(€€€€€€€€€€€€€€€•¹Í•µ‰±•}±…‰•°ô (€€€€€€€€€€€€€€€€€€€˜‰í•¹Í•µ‰±•}•áÁ•Ñ•‘ôµµ•µ‰•ÈÉ½±±¥¹œµ•…¸ˆ(€€€€€€€€€€€€€€€€€€€¥˜É½±±¥¹}µ½‘”(€€€€€€€€€€€€€€€€€€€•±Í”˜‰í±•¸¡µ•µ‰•ÉÌ¥ôµµ•µ‰•Èµ•…¸ˆ(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰¥µ…”‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}Á…Ñ °É•Á½}É½½Ð¤(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ¥˜Í•…Í½¹…±}•¹ÑÉål‰•¹Í•µ‰±•}½µÁ±•Ñ”‰t•±Í”€‰Á…ÉÑ¥…°ˆ(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰É•¹‘•É•MØÈÍ•…Í½¹…°µ•…¸í™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñôèí½ÕÑÁÕÑ}Á…Ñ¡ôˆ¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€™…¥±ÕÉ•Ì€¬ô€Ä(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰™…¥±•ˆ(€€€€€€€€€€€Í•…Í½¹…±}•¹ÑÉål‰•ÉÉ½È‰t€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÁÉ¥¹Ð¡˜‰MØÈÍ•…Í½¹…°Ý¥¹‘½Üí™¥ÉÍÑ}Ñ…É•Ñôµí±…ÍÑ}Ñ…É•Ñô™…¥±•èí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰t¹…ÁÁ•¹¡Í•…Í½¹…±}•¹ÑÉä¤((€€€ÍÑ…ÑÕÍ•Ì€ômÑ…É•Ñl‰ÍÑ…ÑÕÌ‰t™½ÈÑ…É•Ð¥¸ÉÕ¹}•¹ÑÉål‰Ñ…É•ÑÌ‰ut(€€€Á…ÉÑ¥…±}Ñ…É•ÑÌ€ô…¹ä¡ÍÑ…ÑÕÌ€ôô€‰Á…ÉÑ¥…°ˆ™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤(€€€¥˜™…¥±ÕÉ•Ì½ÈÁ…ÉÑ¥…±}Ñ…É•ÑÌè(€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰Á…ÉÑ¥…°ˆ¥˜…¹ä¡ÍÑ…ÑÕÌ€„ô€‰™…¥±•ˆ™½ÈÍÑ…ÑÕÌ¥¸ÍÑ…ÑÕÍ•Ì¤•±Í”€‰™…¥±•ˆ(€€€•±¥˜…ÉÌ¹‘•½‘•}½¹±äè(€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰‘•½‘•ˆ(€€€•±Í”è(€€€€€€€ÉÕ¹}•¹ÑÉål‰ÍÑ…ÑÕÌ‰t€ô€‰É•¹‘•É•ˆ(€€€ÉÕ¹}•¹ÑÉål‰½ÕÑÁÕÑ}‘¥È‰t€ôÉ•±…Ñ¥Ù•}Á…Ñ ¡½ÕÑÁÕÑ}‘¥È°É•Á½}É½½Ð¤(€€€ÝÉ¥Ñ•}µ…¹¥™•ÍÐ¡µ…¹¥™•ÍÑ}Á…Ñ °É•Á½}É½½Ð°ÉÕ¹}•¹ÑÉä¤(€€€ÁÉ¥¹Ð¡˜‰ÝÉ½Ñ”MØÈµ…¹¥™•ÍÐèíµ…¹¥™•ÍÑ}Á…Ñ¡ôˆ¤(€€€É•ÑÕÉ¸€È¥˜™…¥±ÕÉ•Ì•±Í”€À(()‘•˜µ…¥¸ ¤€´ø¥¹Ðè(€€€Á…ÉÍ•È€ô‰Õ¥±‘}Á…ÉÍ•È ¤(€€€…ÉÌ€ôÁ…ÉÍ•È¹Á…ÉÍ•}…ÉÌ ¤(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ÉÕ¸¡…ÉÌ¤(€€€•á•ÁÐMØÉÉÉ½È…Ì•áŒè(€€€€€€€ÁÉ¥¹Ð¡˜‰MØÈ•ÉÉ½Èèí•áôˆ°™¥±”õÍåÌ¹ÍÑ‘•ÉÈ¤(€€€€€€€É•ÑÕÉ¸€È(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€É…¥Í”MåÍÑ•µá¥Ð¡µ…¥¸ ¤¤