"""Generate source-backed maps for the current top seasonal analogs.

The analog matcher is intentionally independent from these external map
services.  This builder consumes its published manifest, requests the maps
for a new top historical period and its weighted top-five composite, and
retains the previous image when a provider is unavailable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import re
from pathlib import Path
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import seasonal_analogs as analogs


SCHEMA_VERSION = "seasonal_analog_products_v1"
PSL_MAP_URL = "https://psl.noaa.gov/cgi-bin/data/atmoswrit/map.proc.pl"
PSL_MAP_PAGE = "https://psl.noaa.gov/data/atmoswrit/map/"
MRCC_MAP_URL = "https://gridded.geddes.rcac.purdue.edu/generate-map"
MRCC_MAP_PAGE = "https://mrcc.purdue.edu/CLIMATE/maps/interpolated"
MRCC_ACIS_MULTI_STATION_URL = "https://data.rcc-acis.org/MultiStnData"
NWS_EASTERN_REGION = "ER"
MRCC_EASTERN_STATES = (
    "ME",
    "NH",
    "VT",
    "NY",
    "MA",
    "CT",
    "RI",
    "PA",
    "NJ",
    "DE",
    "MD",
    "VA",
    "WV",
    "NC",
    "SC",
    "OH",
)
MRCC_SNOWFALL_SOURCE_STATES = MRCC_EASTERN_STATES + ("MI", "IN", "KY", "TN", "AL", "GA")
MRCC_SNOWFALL_STATE_NAMES = {
    "ME": "Maine",
    "NH": "New Hampshire",
    "VT": "Vermont",
    "NY": "New York",
    "MA": "Massachusetts",
    "CT": "Connecticut",
    "RI": "Rhode Island",
    "PA": "Pennsylvania",
    "NJ": "New Jersey",
    "DE": "Delaware",
    "MD": "Maryland",
    "VA": "Virginia",
    "WV": "West Virginia",
    "NC": "North Carolina",
    "SC": "South Carolina",
    "OH": "Ohio",
    "MI": "Michigan",
    "IN": "Indiana",
    "KY": "Kentucky",
    "TN": "Tennessee",
    "AL": "Alabama",
    "GA": "Georgia",
}
MRCC_SNOWFALL_MASK_STATE_NAMES = tuple(
    MRCC_SNOWFALL_STATE_NAMES[state] for state in MRCC_SNOWFALL_SOURCE_STATES
)
MRCC_SNOWFALL_MAP_REGION = "NWS Eastern Region"
MRCC_SNOWFALL_MAP_EXTENT = "Domain-fitted eastern U.S. frame through the Great Lakes and Southeast"
MRCC_SNOWFALL_REGION = (-88.5, -65.5, 31.0, 47.5)
MRCC_SNOWFALL_GRID_STEP = 0.25
MRCC_MIN_STATIONS_FOR_COMPOSITE = 12
MRCC_SNOWFALL_COMPOSITE_KEY = "mrcc_snowfall_departure_composite"
MRCC_SNOWFALL_COMPOSITE_VERSION = "mrcc-acis-snow-v6-eastern-domain-fit"
MRCC_SNOWFALL_PROVIDER_LABEL = "MRCC / ACIS station-interpolated snowfall departure"
MRCC_SNOWFALL_BASELINE_LABEL = "MRCC / ACIS provider snowfall departure (normal supplied by ACIS)"
MRCC_SNOWFALL_RENDERER_ID = "wn2-seasonal-eastern-snow-v6"
MRCC_SNOWFALL_RENDERER_LABEL = "WN2 centered domain-fitted eastern U.S. snowfall departure renderer"
# Signed departures use the requested warm-below-normal and cool-above-normal
# sequence, with a dedicated white neutral interval around zero. Keeping the
# center interval explicit prevents small signals from being washed out while
# preserving an honest zero reference.
MRCC_SNOWFALL_DEPARTURE_PALETTE = [
    "#704214",
    "#7f1d1d",
    "#a61b1b",
    "#c53022",
    "#dc4b1f",
    "#ed6a1f",
    "#f28e2b",
    "#f6ad3d",
    "#f8c44f",
    "#f5df75",
    "#ffffff",
    "#c9e9f2",
    "#94d5e6",
    "#62b6df",
    "#3c8ecb",
    "#225ca8",
    "#2f3b9d",
    "#593caa",
    "#8d3db7",
    "#d34ba9",
    "#18c5d5",
]
MRCC_SNOWFALL_STATION_NETWORKS = (
    "wban",
    "coop",
    "faa",
    "ghcn",
    "cocorahs",
    "wmo",
    "icao",
    "nwsli",
)
MRCC_REQUEST_ATTEMPTS = 2
MRCC_RETRY_DELAY_SECONDS = 5.0
MRCC_GENERATION_TIMEOUT_SECONDS = 600
WRIT_DATASET = "NCEP/CFSR"
WRIT_DATASET_LABEL = "NCEP CFSR"
WRIT_EARLY_DATASET = "20th Century Reanalysis V3"
WRIT_EARLY_DATASET_LABEL = "20CRv3"
WRIT_CFSR_START_YEAR = 1979
WRIT_CLIMATOLOGY_YEARS = "1981-2010"
WRIT_CLIMATOLOGY_LABEL = "NCEP/CFSR native climatology; 1981-2010"
WRIT_NORTH_AMERICA_REGION = "North America"
WRIT_CONUS_REGION = "USA(CONUS)"
WRIT_RENDERER_ID = "wn2-seasonal-lcc-v1"
WRIT_RENDERER_LABEL = "WN2 shared seasonal Lambert Conformal Conic renderer"
COMPOSITE_PRODUCT_KEYS = (
    "psl_500mb_height_anomaly",
    "psl_2m_temperature_anomaly",
)
ANALOG_COMPOSITE_PRODUCT_KEYS = COMPOSITE_PRODUCT_KEYS + (MRCC_SNOWFALL_COMPOSITE_KEY,)

PRODUCT_SPECS: dict[str, dict[str, str]] = {
    "psl_500mb_height_anomaly": {
        "label": "500-mb Geopotential Height Anomaly",
        "provider": "NOAA PSL WRIT",
        "source": PSL_MAP_PAGE,
        "variable": "Geopotential Height",
        "level": "500mb",
        "map_region": WRIT_NORTH_AMERICA_REGION,
        "contourtype": "Shaded w/overlying contours",
        "colortable": "default",
    },
    "psl_2m_temperature_anomaly": {
        "label": "2-m Temperature Anomaly",
        "provider": "NOAA PSL WRIT",
        "source": PSL_MAP_PAGE,
        "variable": "2m Air Temperature",
        "level": "1000mb",
        "map_region": WRIT_CONUS_REGION,
        "contourtype": "Shaded",
        "colortable": "testcmap",
    },
    "mrcc_snowfall_departure": {
        "label": "Snowfall Departure · NWS Eastern Region",
        "provider": "MRCC / ACIS",
        "source": MRCC_MAP_PAGE,
    },
}
COMPOSITE_PRODUCT_SPECS: dict[str, dict[str, str]] = {
    MRCC_SNOWFALL_COMPOSITE_KEY: {
        "label": "Snowfall Departure Composite · NWS Eastern Region",
        "provider": "MRCC / ACIS",
        "source": MRCC_MAP_PAGE,
    },
}
MODEL_LABELS = {"cfsv2": "CFSv2", "superensemble": "Super Ensemble"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MONTH_LABEL = re.compile(r"^(?:December|January|February)\s+(?P<year>\d{4})$")
_DJF_LABEL = re.compile(r"^DJF\s+(?P<start>\d{4})-(?P<end>\d{2})$")


class AnalogProductError(RuntimeError):
    """The analog product manifest or a source response is unusable."""


def _now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_rooted(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    options = [root / path]
    text = path.as_posix()
    if text.startswith("public/"):
        published = text.removeprefix("public/")
        options.append(root / published)
        if published.startswith("seasonal/"):
            options.append(root / published.removeprefix("seasonal/"))
    elif text.startswith("seasonal/"):
        options.append(root / text.removeprefix("seasonal/"))
    for option in options:
        if option.exists():
            return option
    return options[0]


def _relative_asset(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise AnalogProductError(f"manifest does not exist: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalogProductError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalogProductError(f"manifest is not an object: {path}")
    return payload


def _default_fetch(url: str, timeout: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "text/html,image/png,application/json,application/octet-stream",
            "User-Agent": "wall.cloud seasonal analog products/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed HTTPS sources
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AnalogProductError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise AnalogProductError(f"source request failed for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise AnalogProductError(f"source request timed out for {url}") from exc


def _period_for_result(target: str, result: dict[str, Any]) -> dict[str, Any]:
    """Translate an analog result into the source service's date controls."""

    metadata = analogs.parse_target(target)
    try:
        winter_year = int(result["winter_year"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalogProductError(f"analog result has no valid winter_year: {result}") from exc

    if metadata["period_type"] == "month":
        month = int(metadata["month"])
        source_year = winter_year - 1 if month == 12 else winter_year
        start = dt.date(source_year, month, 1)
        end = dt.date(source_year, month, _days_in_month(source_year, month))
        label = str(result.get("label") or f"{start.strftime('%B')} {source_year}")
        return {
            "period_type": "month",
            "label": label,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "psl_year": source_year,
            "psl_start_month": month,
            "psl_end_month": month,
            "winter_year": winter_year,
        }

    start = dt.date(winter_year - 1, 12, 1)
    end = dt.date(winter_year, 2, _days_in_month(winter_year, 2))
    label = str(result.get("label") or f"DJF {winter_year - 1}-{str(winter_year)[-2:]}")
    return {
        "period_type": "djf",
        "label": label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        # PSL asks for the year of the last month when a season crosses years.
        "psl_year": winter_year,
        "psl_start_month": 12,
        "psl_end_month": 2,
        "winter_year": winter_year,
    }


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.timedelta(days=1)).day


def _writ_dataset_for_period(period: dict[str, Any]) -> str:
    """Select a WRIT dataset that covers the entire historical analog period."""

    start_year = dt.date.fromisoformat(str(period["start_date"])).year
    return WRIT_DATASET if start_year >= WRIT_CFSR_START_YEAR else WRIT_EARLY_DATASET


def _writ_dataset_label(dataset: str) -> str:
    if dataset == WRIT_DATASET:
        return WRIT_DATASET_LABEL
    if dataset == WRIT_EARLY_DATASET:
        return WRIT_EARLY_DATASET_LABEL
    return str(dataset)


def _writ_climatology_label(dataset: str) -> str:
    return f"{dataset} native climatology; {WRIT_CLIMATOLOGY_YEARS}"


def _psl_url(period: dict[str, Any], spec: dict[str, str]) -> str:
    query = {
        "dataset1": _writ_dataset_for_period(period),
        "var": spec["variable"],
        "level": spec["level"],
        "iy": str(period["psl_year"]),
        "fmonth": str(int(period["psl_start_month"]) - 1),
        "fmonth2": str(int(period["psl_end_month"]) - 1),
        "type": "1",
        "map": "0",
        # Keep the provider-side plot close to the dashboard view as well;
        # the published image itself is re-rendered locally from WRIT NetCDF.
        "mapt": "6",
        "proj": spec.get("map_region", WRIT_NORTH_AMERICA_REGION),
        "colortable": spec["colortable"],
        "labelc": "0",
        "contourtype": spec["contourtype"],
        "scale": "100",
        "labelcon": "1",
        "switch": "0",
        "gridfill": "0",
        "google": "0",
        "Submit": "Create Plot",
    }
    return f"{PSL_MAP_URL}?{urlencode(query)}"


def _mrcc_url(period: dict[str, Any]) -> str:
    query = {
        "s": "station",
        "a": "region",
        "loc": NWS_EASTERN_REGION,
        "var": "snow",
        "ds": str(period["start_date"]).replace("-", ""),
        "de": str(period["end_date"]).replace("-", ""),
        "stat": "total",
        "calc": "departure",
        # Match the current MRCC interpolated-map form.  These null degree-day
        # fields are emitted by the UI even though snowfall does not use them.
        "gddB": "null",
        "gddC": "null",
        "con": "5",
        "lta": "F",
        "cwa": "F",
        "cities": "T",
        "counties": "T",
        "state": "F",
        "mask": "F",
        "lakes": "F",
        "oceans": "F",
        "roads": "F",
        "sids": " ".join(MRCC_SNOWFALL_STATION_NETWORKS),
        "output": "map_btd.png",
    }
    return f"{MRCC_MAP_URL}?{urlencode(query)}"


def _mrcc_retryable(error: AnalogProductError) -> bool:
    """Retry quick transient provider errors, not a timeout after the full wait."""

    message = str(error)
    if "timed out" in message.lower():
        return False
    return bool(
        re.search(r"\bHTTP (?:429|500|502|503|504)\b", message)
        or "source request failed" in message.lower()
    )


def _fetch_mrcc_image(
    fetcher: Callable[[str, int], bytes],
    url: str,
    timeout: int,
) -> bytes:
    """Retry transient MRCC generator failures before retaining a prior map."""

    errors: list[str] = []
    for attempt in range(1, MRCC_REQUEST_ATTEMPTS + 1):
        try:
            return fetcher(url, timeout)
        except AnalogProductError as exc:
            errors.append(str(exc))
            if attempt >= MRCC_REQUEST_ATTEMPTS or not _mrcc_retryable(exc):
                raise
            time.sleep(MRCC_RETRY_DELAY_SECONDS)
    raise AnalogProductError(
        f"MRCC request failed after {MRCC_REQUEST_ATTEMPTS} attempts: {' | '.join(errors)}"
    )


def _mrcc_station_data_url(period: dict[str, Any]) -> str:
    """Build an ACIS request for ER and adjacent-frame snowfall departures."""

    payload = {
        "state": list(MRCC_SNOWFALL_SOURCE_STATES),
        "sdate": str(period["start_date"])[:7],
        "edate": str(period["end_date"])[:7],
        "elems": [
            {
                "name": "snow",
                "interval": "mly",
                "duration": "mly",
                "reduce": "sum",
                "normal": "departure",
                "maxmissing": 5,
            }
        ],
        "meta": ["ll"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{MRCC_ACIS_MULTI_STATION_URL}?{urlencode({'params': encoded})}"


def _parse_mrcc_numeric_value(raw: Any) -> float:
    """Convert one ACIS value while preserving missing observations."""

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw or "").strip()
    upper = text.upper()
    if upper in {"", "M", "NA", "N/A", "NULL", "-", "--"}:
        return float("nan")
    if upper in {"T", "TRACE"}:
        return 0.0
    cleaned = text.rstrip("*")
    cleaned = re.sub(r"(?<=\d)[A-Za-z]+$", "", cleaned).strip()
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def _read_mrcc_station_values(content: bytes, period: dict[str, Any]) -> dict[str, Any]:
    """Decode ACIS monthly station departures and aggregate each station."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - workflow installs requirements.txt
        raise AnalogProductError("MRCC/ACIS snowfall compositing requires numpy") from exc
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AnalogProductError(f"MRCC/ACIS response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalogProductError("MRCC/ACIS response was not a JSON object")
    if payload.get("error"):
        raise AnalogProductError(f"MRCC/ACIS returned an error: {payload['error']}")
    records = payload.get("data")
    if not isinstance(records, list):
        raise AnalogProductError("MRCC/ACIS response did not contain station data")
    period_type = str(period.get("period_type", ""))
    expected_months = 1 if period_type == "month" else 3 if period_type == "djf" else 0
    if expected_months == 0:
        raise AnalogProductError(f"unsupported snowfall analog period: {period_type}")

    longitudes: list[float] = []
    latitudes: list[float] = []
    values: list[float] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        metadata = record.get("meta")
        location = metadata.get("ll") if isinstance(metadata, dict) else None
        if not isinstance(location, (list, tuple)) or len(location) < 2:
            continue
        try:
            longitude = float(location[0])
            latitude = float(location[1])
        except (TypeError, ValueError):
            continue
        rows = record.get("data")
        if not isinstance(rows, list) or len(rows) != expected_months:
            continue
        monthly_values: list[float] = []
        for row in rows:
            raw = row[0] if isinstance(row, (list, tuple)) and row else row
            value = _parse_mrcc_numeric_value(raw)
            monthly_values.append(value)
        if not np.isfinite(longitude) or not np.isfinite(latitude) or not np.isfinite(monthly_values).all():
            continue
        longitudes.append(longitude)
        latitudes.append(latitude)
        values.append(float(sum(monthly_values)))

    if len(values) < MRCC_MIN_STATIONS_FOR_COMPOSITE:
        raise AnalogProductError(
            f"MRCC/ACIS returned only {len(values)} complete stations; "
            f"at least {MRCC_MIN_STATIONS_FOR_COMPOSITE} are required"
        )
    return {
        "longitudes": longitudes,
        "latitudes": latitudes,
        "values": values,
        "station_count": len(values),
    }


def _inverse_distance_station_grid(
    points: Any,
    station_values: Any,
    lons: Any,
    lats: Any,
    np: Any,
) -> Any:
    """Provide a NumPy-only fallback when the optional SciPy wheel is unusable."""

    mesh_lons, mesh_lats = np.meshgrid(lons, lats)
    query = np.column_stack((mesh_lons.ravel(), mesh_lats.ravel()))
    distances = np.hypot(
        query[:, None, 0] - points[None, :, 0],
        query[:, None, 1] - points[None, :, 1],
    )
    neighbor_count = min(16, points.shape[0])
    indices = np.argpartition(distances, neighbor_count - 1, axis=1)[:, :neighbor_count]
    selected_distances = np.take_along_axis(distances, indices, axis=1)
    selected_values = station_values[indices]
    zero_distance = selected_distances <= 1.0e-12
    weights = 1.0 / np.maximum(selected_distances, 1.0e-12) ** 2
    weighted = (selected_values * weights).sum(axis=1) / weights.sum(axis=1)
    exact_values = np.take_along_axis(
        selected_values,
        zero_distance.argmax(axis=1)[:, None],
        axis=1,
    )[:, 0]
    return np.where(zero_distance.any(axis=1), exact_values, weighted).reshape(
        (lats.size, lons.size)
    )


def _interpolate_mrcc_station_grid(stations: dict[str, Any]) -> tuple[Any, str]:
    """Interpolate station departures to the Eastern Region seasonal grid."""

    try:
        import cfsv2_seasonal as seasonal
        import numpy as np
    except ImportError as exc:  # pragma: no cover - workflow installs requirements.txt
        raise AnalogProductError(
            "MRCC/ACIS snowfall compositing requires numpy and the seasonal renderer"
        ) from exc

    points = np.column_stack(
        (
            np.asarray(stations["longitudes"], dtype=float),
            np.asarray(stations["latitudes"], dtype=float),
        )
    )
    station_values = np.asarray(stations["values"], dtype=float)
    finite = np.isfinite(points).all(axis=1) & np.isfinite(station_values)
    points = points[finite]
    station_values = station_values[finite]
    if points.shape[0] < 3:
        raise AnalogProductError("MRCC/ACIS snowfall data has too few finite station points")

    unique_points, inverse = np.unique(points, axis=0, return_inverse=True)
    unique_values = np.bincount(inverse, weights=station_values) / np.bincount(inverse)
    if unique_points.shape[0] < 3:
        raise AnalogProductError("MRCC/ACIS snowfall data has too few unique station points")

    lon_min, lon_max, lat_min, lat_max = MRCC_SNOWFALL_REGION
    lons = np.arange(lon_min, lon_max + MRCC_SNOWFALL_GRID_STEP * 0.5, MRCC_SNOWFALL_GRID_STEP)
    lats = np.arange(lat_min, lat_max + MRCC_SNOWFALL_GRID_STEP * 0.5, MRCC_SNOWFALL_GRID_STEP)
    mesh_lons, mesh_lats = np.meshgrid(lons, lats)
    interpolation_method = "linear station interpolation with nearest-neighbor edge fill"
    try:
        from scipy.interpolate import griddata
        from scipy.spatial import QhullError
    except ImportError:
        griddata = None
        QhullError = None
    if griddata is None:
        interpolated = _inverse_distance_station_grid(unique_points, unique_values, lons, lats, np)
        interpolation_method = "inverse-distance station interpolation (NumPy fallback; SciPy unavailable)"
    else:
        try:
            linear = griddata(unique_points, unique_values, (mesh_lons, mesh_lats), method="linear")
        except (QhullError, ValueError):
            linear = None
        if linear is None:
            interpolated = _inverse_distance_station_grid(unique_points, unique_values, lons, lats, np)
            interpolation_method = "inverse-distance station interpolation (linear SciPy interpolation unavailable)"
        else:
            nearest = griddata(unique_points, unique_values, (mesh_lons, mesh_lats), method="nearest")
            interpolated = np.where(np.isfinite(linear), linear, nearest)
    if not np.isfinite(interpolated).any():
        raise AnalogProductError("MRCC/ACIS snowfall interpolation produced no finite grid cells")
    return seasonal.Grid(lons.tolist(), lats.tolist(), interpolated.tolist()), interpolation_method


def _fetch_mrcc_station_grid(
    fetcher: Callable[[str, int], bytes],
    period: dict[str, Any],
    timeout: int,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Fetch, decode, and interpolate one ACIS snowfall departure field."""

    source_url = _mrcc_station_data_url(period)
    if source_url in cache:
        return cache[source_url]
    content = _fetch_mrcc_json(fetcher, source_url, timeout)
    stations = _read_mrcc_station_values(content, period)
    grid, interpolation_method = _interpolate_mrcc_station_grid(stations)
    asset = {
        "source_url": source_url,
        "provider_asset_url": source_url,
        "grid": grid,
        "station_count": stations["station_count"],
        "interpolation": interpolation_method,
    }
    cache[source_url] = asset
    return asset


def _fetch_mrcc_json(
    fetcher: Callable[[str, int], bytes],
    url: str,
    timeout: int,
) -> bytes:
    """Retry transient ACIS responses without repeating a full map wait."""

    errors: list[str] = []
    for attempt in range(1, MRCC_REQUEST_ATTEMPTS + 1):
        try:
            return fetcher(url, timeout)
        except AnalogProductError as exc:
            errors.append(str(exc))
            if attempt >= MRCC_REQUEST_ATTEMPTS or not _mrcc_retryable(exc):
                raise
            time.sleep(MRCC_RETRY_DELAY_SECONDS)
    raise AnalogProductError(
        f"MRCC/ACIS request failed after {MRCC_REQUEST_ATTEMPTS} attempts: {' | '.join(errors)}"
    )


def _extract_psl_image_url(page: bytes) -> str:
    text = page.decode("latin-1", errors="replace")
    sources = re.findall(r"<img[^>]+src=[\"']([^\"']+\.png)[\"']", text, re.IGNORECASE)
    generated = next(
        (
            source
            for source in sources
            if "/tmp/" in source.lower() or "plot" in source.lower() or "map" in source.lower()
        ),
        None,
    )
    if generated is None:
        generated = next(
            (source for source in sources if "icon" not in source.lower()),
            None,
        )
    if generated is None:
        raise AnalogProductError("PSL response did not contain a generated PNG")
    return urljoin(PSL_MAP_URL, html.unescape(generated))


def _extract_psl_netcdf_url(page: bytes) -> str:
    """Return WRIT's generated NetCDF asset rather than its final PNG."""

    text = page.decode("latin-1", errors="replace")
    sources = re.findall(
        r"<a[^>]+href=[\"']([^\"']+\.nc(?:\?[^\"']*)?)[\"']",
        text,
        re.IGNORECASE,
    )
    generated = next(
        (source for source in sources if "/tmp/" in source.lower()),
        sources[0] if sources else None,
    )
    if generated is None:
        raise AnalogProductError("PSL response did not contain a generated NetCDF file")
    return urljoin(PSL_MAP_URL, html.unescape(generated))


def _read_writ_grid(content: bytes):
    """Decode a WRIT NetCDF grid into the shared seasonal ``Grid`` type."""

    try:
        import numpy as np
        import xarray as xr
        import cfsv2_seasonal as seasonal
    except ImportError as exc:  # pragma: no cover - workflow installs these
        raise AnalogProductError(
            "WRIT re-rendering requires numpy, xarray, scipy, and the seasonal renderer"
        ) from exc

    stream = io.BytesIO(content)
    dataset = None
    try:
        # WRIT returns classic NetCDF. xarray's file-like path selects its
        # scipy backend, which lets us decode the response without creating a
        # second provider-specific file format or a persistent temp artifact.
        dataset = xr.open_dataset(stream, decode_times=False, mask_and_scale=True)
        if "lat" not in dataset or "lon" not in dataset:
            raise AnalogProductError("WRIT NetCDF is missing lat/lon coordinates")
        data_array = dataset.data_vars.get("VAR")
        if data_array is None:
            candidates = [
                value
                for value in dataset.data_vars.values()
                if "lat" in value.dims and "lon" in value.dims
            ]
            data_array = candidates[0] if candidates else None
        if data_array is None:
            raise AnalogProductError("WRIT NetCDF has no lat/lon data variable")
        data_array = data_array.squeeze(drop=True)
        extra_dims = [dimension for dimension in data_array.dims if dimension not in {"lat", "lon"}]
        if extra_dims:
            raise AnalogProductError(
                f"WRIT NetCDF data has unsupported dimensions: {', '.join(extra_dims)}"
            )
        data_array = data_array.transpose("lat", "lon")
        values = data_array.values
        if np.ma.isMaskedArray(values):
            values = np.ma.filled(values, np.nan)
        values = np.asarray(values, dtype=float)
        values[np.abs(values) > 1.0e30] = np.nan
        lats = np.asarray(dataset["lat"].values, dtype=float)
        lons = np.asarray(dataset["lon"].values, dtype=float)
    except AnalogProductError:
        raise
    except Exception as exc:
        raise AnalogProductError(f"could not decode WRIT NetCDF: {exc}") from exc
    finally:
        if dataset is not None:
            dataset.close()

    if lats.ndim != 1 or lons.ndim != 1 or values.shape != (lats.size, lons.size):
        raise AnalogProductError("WRIT NetCDF has inconsistent coordinate and data dimensions")
    if lats.size < 2 or lons.size < 2:
        raise AnalogProductError("WRIT NetCDF grid is too small to render")
    if not np.isfinite(values).any():
        raise AnalogProductError("WRIT NetCDF contains no finite data")

    lat_order = np.argsort(lats, kind="stable")
    lats = lats[lat_order]
    values = values[lat_order, :]
    # The shared renderer accepts either -180..180 or 0..360, but normalizing
    # here makes the longitude ordering explicit and avoids a seam-dependent
    # result when WRIT changes its lonFlip convention.
    lons = np.mod(lons + 180.0, 360.0) - 180.0
    lon_order = np.argsort(lons, kind="stable")
    lons = lons[lon_order]
    values = values[:, lon_order]
    if np.any(np.diff(lats) <= 0.0) or np.any(np.diff(lons) <= 0.0):
        raise AnalogProductError("WRIT NetCDF coordinates are not strictly increasing")
    return seasonal.Grid(lons.tolist(), lats.tolist(), values.tolist())


def _writ_rendering_metadata(
    seasonal: Any,
    *,
    region: tuple[float, float, float, float],
    map_region: str,
    product_spec: dict[str, Any] | None = None,
    renderer_id: str = WRIT_RENDERER_ID,
    renderer_label: str = WRIT_RENDERER_LABEL,
) -> dict[str, Any]:
    product_spec = product_spec or {}
    metadata = {
        "id": renderer_id,
        "label": renderer_label,
        "projection": seasonal.SEASONAL_LCC_PROJECTION_NAME,
        "standard_parallels": [
            float(product_spec.get("projection_standard_parallel_1", seasonal.SEASONAL_LCC_STANDARD_PARALLEL_1)),
            float(product_spec.get("projection_standard_parallel_2", seasonal.SEASONAL_LCC_STANDARD_PARALLEL_2)),
        ],
        "latitude_origin": float(product_spec.get("projection_latitude_origin", seasonal.SEASONAL_LCC_LATITUDE_ORIGIN)),
        "central_longitude": float(product_spec.get("projection_central_longitude", seasonal.SEASONAL_LCC_CENTRAL_LONGITUDE)),
        "projected_x_shift_fraction": float(product_spec.get("projected_x_shift_fraction", seasonal.PROJECTED_X_SHIFT_FRACTION)),
        "map_region": map_region,
        "region": list(region),
        "canvas": "1080x1080",
    }
    if product_spec.get("mask_states"):
        metadata["mask_states"] = list(product_spec["mask_states"])
    if product_spec.get("border_files") is not None:
        metadata["border_files"] = [Path(str(item)).name for item in product_spec["border_files"]]
    if product_spec.get("map_extent"):
        metadata["map_extent"] = str(product_spec["map_extent"])
    return metadata


def _writ_render_product_spec(product_key: str, seasonal: Any, dataset: str) -> dict[str, Any]:
    is_height = product_key == "psl_500mb_height_anomaly"
    source_product = (
        seasonal.PRODUCT_HEIGHT_ANOMALY
        if is_height
        else seasonal.PRODUCT_2M_TEMPERATURE_ANOMALY
    )
    spec = dict(seasonal.PRODUCT_SPECS[source_product])
    dataset_label = _writ_dataset_label(dataset)
    title = (
        f"{dataset_label} 500-mb Geopotential Height Anomaly (m)"
        if is_height
        else f"{dataset_label} 2-m Temperature Anomaly (°C)"
    )
    map_region = PRODUCT_SPECS[product_key]["map_region"]
    region = seasonal.DEFAULT_REGION if is_height else seasonal.CONUS_PRECIP_REGION
    spec.update(
        {
            "title": title,
            "absolute_title": title,
            "height_contours": False,
            "source_label": f"NOAA PSL WRIT / {dataset_label}",
            "header_detail": (
                "{source_label}  •  {baseline_label}  •  "
                f"{seasonal.SEASONAL_LCC_PROJECTION_NAME}  •  {map_region} domain"
            ),
            "lead_label": "Historical analog",
            "region": region,
        }
    )
    return spec


_WRIT_BORDER_PATHS: dict[Path, list[Path]] = {}


def _writ_border_paths(root: Path, seasonal: Any) -> list[Path]:
    key = root.resolve()
    if key not in _WRIT_BORDER_PATHS:
        options = argparse.Namespace(no_borders=False, border_geojson=None)
        cache_dir = key / ".cache" / "seasonal_analogs"
        _WRIT_BORDER_PATHS[key] = seasonal.ensure_border_files(options, cache_dir, key)
    return _WRIT_BORDER_PATHS[key]


def _render_writ_grid(
    *,
    grid: Any,
    product_key: str,
    period: dict[str, Any],
    output_path: Path,
    root: Path,
    dataset: str,
    period_label: str | None = None,
    baseline_label: str | None = None,
    title: str | None = None,
    source_label: str | None = None,
    lead_label: str | None = None,
    ensemble_label: str | None = None,
    initialization_label: str | None = None,
    footer_text: str = "",
) -> dict[str, Any]:
    """Render a WRIT grid with the operational seasonal map geometry."""

    try:
        import cfsv2_seasonal as seasonal
    except ImportError as exc:  # pragma: no cover - workflow imports this module
        raise AnalogProductError("the shared seasonal renderer is unavailable") from exc
    start_date = dt.date.fromisoformat(str(period["start_date"]))
    product_spec = _writ_render_product_spec(product_key, seasonal, dataset)
    if title:
        product_spec["title"] = title
        product_spec["absolute_title"] = title
    if source_label:
        product_spec["source_label"] = source_label
    if lead_label:
        product_spec["lead_label"] = lead_label
    region = tuple(product_spec["region"])
    seasonal.render_map(
        grid=grid,
        init=f"{start_date:%Y%m%d}00",
        target=f"{start_date:%Y%m}",
        lead=period["label"],
        members=(1,),
        output_path=output_path,
        anomaly=True,
        baseline_label=baseline_label or _writ_climatology_label(dataset),
        border_paths=_writ_border_paths(root, seasonal),
        period_label=period_label or str(period["label"]),
        seasonal=period["period_type"] == "djf",
        ensemble_label=ensemble_label or f"{_writ_dataset_label(dataset)} WRIT composite",
        product_spec=product_spec,
        initialization_label=initialization_label or f"Historical analog {period['label']}",
        footer_text=footer_text,
    )
    return _writ_rendering_metadata(
        seasonal,
        region=region,
        map_region=PRODUCT_SPECS[product_key]["map_region"],
        product_spec=product_spec,
    )


def _mrcc_snowfall_render_product_spec(seasonal: Any, period: dict[str, Any], member_count: int) -> dict[str, Any]:
    """Use the shared renderer with a fixed, readable snowfall-departure scale."""

    if period["period_type"] == "month":
        anomaly_min, anomaly_max, tick_step = -20.0, 20.0, 2
        anomaly_ticks = list(range(int(anomaly_min), int(anomaly_max) + tick_step, tick_step))
        anomaly_bounds = (
            list(range(int(anomaly_min), 0, tick_step))
            + [-1, 1]
            + list(range(2, int(anomaly_max) + 1, tick_step))
        )
    else:
        anomaly_min, anomaly_max, tick_step = -40.0, 40.0, 4
        anomaly_ticks = list(range(int(anomaly_min), int(anomaly_max) + tick_step, tick_step))
        anomaly_bounds = (
            list(range(int(anomaly_min), 0, tick_step))
            + [-2, 2]
            + list(range(4, int(anomaly_max) + 1, tick_step))
        )
    product_spec = dict(seasonal.PRODUCT_SPECS[seasonal.PRODUCT_PRECIPITATION_ANOMALY])
    title = f"Weighted Top {member_count}-Analog Snowfall Departure (in)"
    product_spec.update(
        {
            "title": title,
            "absolute_title": title,
            "height_contours": False,
            "source_label": "MRCC / ACIS",
            "header_summary": (
                f"MRCC / ACIS  •  {member_count}-member inverse-distance analog blend  •  "
                f"{MRCC_SNOWFALL_MAP_REGION}"
            ),
            "suppress_header_detail": True,
            "map_extent": MRCC_SNOWFALL_MAP_EXTENT,
            "lead_label": "Inverse-distance analog composite",
            "region": MRCC_SNOWFALL_REGION,
            "anomaly_min": anomaly_min,
            "anomaly_max": anomaly_max,
            "anomaly_ticks": anomaly_ticks,
            "anomaly_bounds": anomaly_bounds,
            "anomaly_palette": list(MRCC_SNOWFALL_DEPARTURE_PALETTE),
            "anomaly_tick_decimals": 0,
            "anomaly_tick_format": "signed",
            "map_domain": "land",
            "fit_frame_to_domain": True,
            "domain_frame_padding_fraction": 0.0,
            "mask_states": list(MRCC_SNOWFALL_MASK_STATE_NAMES),
            "border_files": ("us-states.geojson",),
            "projection_standard_parallel_1": 33.0,
            "projection_standard_parallel_2": 45.0,
            "projection_latitude_origin": 39.0,
            "projection_central_longitude": -77.5,
            "projected_x_shift_fraction": 0.0,
        }
    )
    return product_spec


def _render_mrcc_snowfall_grid(
    *,
    grid: Any,
    period: dict[str, Any],
    output_path: Path,
    root: Path,
    target_label: str,
    member_count: int,
    footer_text: str,
) -> dict[str, Any]:
    """Render an ACIS snowfall grid with the shared seasonal map geometry."""

    try:
        import cfsv2_seasonal as seasonal
    except ImportError as exc:  # pragma: no cover - workflow imports this module
        raise AnalogProductError("the shared seasonal renderer is unavailable") from exc
    start_date = dt.date.fromisoformat(str(period["start_date"]))
    product_spec = _mrcc_snowfall_render_product_spec(seasonal, period, member_count)
    seasonal.render_map(
        grid=grid,
        init=f"{start_date:%Y%m%d}00",
        target=f"{start_date:%Y%m}",
        lead=period["label"],
        members=(1,),
        output_path=output_path,
        anomaly=True,
        baseline_label=MRCC_SNOWFALL_BASELINE_LABEL,
        border_paths=_writ_border_paths(root, seasonal),
        period_label=target_label,
        seasonal=period["period_type"] == "djf",
        ensemble_label=f"{member_count}-analog weighted composite",
        product_spec=product_spec,
        initialization_label=f"Historical analog composite · {target_label}",
        footer_text=footer_text,
    )
    return _writ_rendering_metadata(
        seasonal,
        region=MRCC_SNOWFALL_REGION,
        map_region=MRCC_SNOWFALL_MAP_REGION,
        product_spec=product_spec,
        renderer_id=MRCC_SNOWFALL_RENDERER_ID,
        renderer_label=MRCC_SNOWFALL_RENDERER_LABEL,
    )


def _render_writ_netcdf(
    *,
    content: bytes,
    product_key: str,
    period: dict[str, Any],
    output_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Render a WRIT data asset with the operational seasonal map geometry."""

    dataset = _writ_dataset_for_period(period)
    return _render_writ_grid(
        grid=_read_writ_grid(content),
        product_key=product_key,
        period=period,
        output_path=output_path,
        root=root,
        dataset=dataset,
    )


def _write_png(path: Path, data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise AnalogProductError(f"source response is not a PNG: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _top_key(model: str, target: str, result: dict[str, Any]) -> str:
    return f"{model}:{target}:{int(result['winter_year'])}"


def _composite_members(target: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the ranked top-five analogs and their normalized weights."""

    candidates = [result for result in results if isinstance(result, dict)]
    candidates.sort(
        key=lambda result: (
            int(result.get("rank", 9999)) if str(result.get("rank", "")).isdigit() else 9999,
            int(result.get("winter_year", 9999)) if str(result.get("winter_year", "")).isdigit() else 9999,
        )
    )
    selected = candidates[:analogs.COMPOSITE_ANALOG_COUNT]
    if len(selected) < 2:
        return []
    weights = analogs.composite_weights(selected, count=len(selected))
    members: list[dict[str, Any]] = []
    for index, (result, weight) in enumerate(zip(selected, weights, strict=True), start=1):
        try:
            winter_year = int(result["winter_year"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalogProductError(f"analog result has no valid winter_year: {result}") from exc
        members.append(
            {
                "result": result,
                "rank": int(result.get("rank") or index),
                "label": str(result.get("label") or f"Historical analog {winter_year}"),
                "winter_year": winter_year,
                "period": _period_for_result(target, result),
                "weight": float(weight),
            }
        )
    return members


def _composite_key(model: str, target: str, members: list[dict[str, Any]]) -> str:
    """Build a stable cache key from the selected analogs and their weights."""

    selection = [
        {
            "rank": member["rank"],
            "winter_year": member["winter_year"],
            "pattern_correlation": member["result"].get("pattern_correlation"),
            "amplitude_similarity": member["result"].get("amplitude_similarity"),
            "weight": round(float(member["weight"]), 8),
        }
        for member in members
    ]
    digest = hashlib.sha256(
        json.dumps(selection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"{model}:{target}:top{len(members)}:composite-{digest}"


def _composite_member_summary(member: dict[str, Any]) -> dict[str, Any]:
    result = member["result"]
    return {
        "rank": member["rank"],
        "label": member["label"],
        "winter_year": member["winter_year"],
        "pattern_correlation": result.get("pattern_correlation"),
        "amplitude_similarity": result.get("amplitude_similarity"),
        "weight": round(float(member["weight"]), 6),
    }


def _fetch_writ_grid(
    fetcher: Callable[[str, int], bytes],
    source_url: str,
    timeout: int,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Fetch and decode one WRIT NetCDF product, retaining it in-run."""

    if source_url in cache:
        return cache[source_url]
    page = fetcher(source_url, timeout)
    image_url = _extract_psl_image_url(page)
    data_url = _extract_psl_netcdf_url(page)
    data = fetcher(data_url, timeout)
    asset = {
        "source_url": source_url,
        "provider_image_url": image_url,
        "provider_asset_url": data_url,
        "grid": _read_writ_grid(data),
    }
    cache[source_url] = asset
    return asset


def _average_writ_grids(grids: list[Any], weights: list[float]) -> Any:
    """Return a finite, coordinate-aligned weighted average of WRIT grids."""

    try:
        import cfsv2_seasonal as seasonal
        import numpy as np
    except ImportError as exc:  # pragma: no cover - workflow imports these modules
        raise AnalogProductError("WRIT composite rendering requires numpy and the seasonal renderer") from exc
    if not grids or len(grids) != len(weights):
        raise AnalogProductError("WRIT composite has no matching grids and weights")
    reference = grids[0]
    reference_lons = np.asarray(reference.lons, dtype=float)
    reference_lats = np.asarray(reference.lats, dtype=float)
    arrays: list[np.ndarray] = []
    for grid in grids:
        values = np.asarray(grid.values, dtype=float)
        if list(grid.lons) != list(reference.lons) or list(grid.lats) != list(reference.lats):
            values = analogs.regrid_nearest(
                values,
                grid.lats,
                grid.lons,
                reference.lats,
                reference.lons,
            )
        if values.shape != (reference_lats.size, reference_lons.size):
            raise AnalogProductError("WRIT composite grids do not share a compatible shape")
        arrays.append(values)
    stack = np.asarray(arrays, dtype=float)
    weight_array = np.asarray(weights, dtype=float)[:, None, None]
    finite = np.isfinite(stack)
    numerator = np.where(finite, stack, 0.0) * weight_array
    denominator = np.where(finite, weight_array, 0.0).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        composite = numerator.sum(axis=0) / denominator
    composite[denominator <= 0.0] = np.nan
    if not np.isfinite(composite).any():
        raise AnalogProductError("WRIT composite contains no finite values")
    return seasonal.Grid(
        reference_lons.tolist(),
        reference_lats.tolist(),
        composite.tolist(),
    )


def _existing_entries(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = _read_json(path, required=False)
    return {
        (str(entry.get("model", "")), str(entry.get("target", ""))): entry
        for entry in payload.get("entries", [])
        if isinstance(entry, dict) and entry.get("model") and entry.get("target")
    }


def _product_spec(product_key: str) -> dict[str, str]:
    spec = PRODUCT_SPECS.get(product_key) or COMPOSITE_PRODUCT_SPECS.get(product_key)
    if spec is None:
        raise AnalogProductError(f"unknown analog product: {product_key}")
    return spec


def _retained_or_unavailable(
    *,
    root: Path,
    old: dict[str, Any] | None,
    product_key: str,
    top_key: str,
    source_url: str,
    error: str,
) -> dict[str, Any]:
    old_image = str((old or {}).get("image", ""))
    old_path = _resolve_rooted(root, old_image) if old_image else None
    if old_path and old_path.exists():
        return {
            **old,
            "status": "stale",
            "requested_top_analog_key": top_key,
            "retained_top_analog_key": old.get("top_analog_key"),
            "source_url": source_url,
            "checked_utc": _now_iso(),
            "error": error,
        }
    return {
        "product": product_key,
        "label": _product_spec(product_key)["label"],
        "provider": _product_spec(product_key)["provider"],
        "status": "unavailable",
        "top_analog_key": top_key,
        "source_url": source_url,
        "checked_utc": _now_iso(),
        "error": error,
    }


def _retained_composite_or_unavailable(
    *,
    root: Path,
    old: dict[str, Any] | None,
    product_key: str,
    composite_key: str,
    source_urls: list[str],
    member_count: int,
    error: str,
) -> dict[str, Any]:
    """Retain an earlier composite when one of its source maps fails."""

    old_image = str((old or {}).get("image", ""))
    old_path = _resolve_rooted(root, old_image) if old_image else None
    if old_path and old_path.exists():
        return {
            **old,
            "status": "stale",
            "requested_composite_key": composite_key,
            "retained_composite_key": old.get("composite_key"),
            "source_urls": source_urls,
            "checked_utc": _now_iso(),
            "error": error,
        }
    return {
        "product": product_key,
        "label": f"Weighted Top {member_count}-Analog Composite · {_product_spec(product_key)['label']}",
        "provider": _product_spec(product_key)["provider"],
        "status": "unavailable",
        "composite_key": composite_key,
        "source_urls": source_urls,
        "checked_utc": _now_iso(),
        "error": error,
    }


def _build_composite_product(
    *,
    root: Path,
    output_dir: Path,
    model: str,
    target: str,
    target_label: str,
    members: list[dict[str, Any]],
    product_key: str,
    old: dict[str, Any] | None,
    fetcher: Callable[[str, int], bytes],
    timeout: int,
    grid_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one weighted numerical WRIT composite for the selected analogs."""

    if product_key not in COMPOSITE_PRODUCT_KEYS:
        raise AnalogProductError(f"unsupported analog composite product: {product_key}")
    spec = PRODUCT_SPECS[product_key]
    composite_key = _composite_key(model, target, members)
    source_urls = [_psl_url(member["period"], spec) for member in members]
    image_path = output_dir / model / target / "composite" / f"{product_key}.png"
    if (
        old
        and old.get("composite_key") == composite_key
        and old.get("composite_method") == analogs.COMPOSITE_METHOD
        and old.get("status") == "ready"
        and old.get("image")
        and _resolve_rooted(root, str(old.get("image", ""))).exists()
    ):
        return old

    try:
        assets = [
            _fetch_writ_grid(fetcher, source_url, timeout, grid_cache)
            for source_url in source_urls
        ]
        grids = [asset["grid"] for asset in assets]
        weights = [float(member["weight"]) for member in members]
        composite_grid = _average_writ_grids(grids, weights)
        datasets = sorted(
            {
                _writ_dataset_for_period(member["period"])
                for member in members
            }
        )
        dataset_labels = [_writ_dataset_label(dataset) for dataset in datasets]
        dataset_label = " + ".join(dataset_labels)
        product_label = f"Weighted Top {len(members)}-Analog Composite · {spec['label']}"
        if product_key == "psl_500mb_height_anomaly":
            title = f"Weighted Top {len(members)}-Analog 500-mb Height Anomaly (m)"
        else:
            title = f"Weighted Top {len(members)}-Analog 2-m Temperature Anomaly (°C)"
        footer_lines = [
            "Weighted analog members: "
            + "  •  ".join(
                f"{member['rank']} {member['label']} ({member['weight'] * 100:.1f}%)"
                for member in members[:3]
            )
        ]
        if len(members) > 3:
            footer_lines.append(
                "  •  ".join(
                    f"{member['rank']} {member['label']} ({member['weight'] * 100:.1f}%)"
                    for member in members[3:]
                )
            )
        rendering = _render_writ_grid(
            grid=composite_grid,
            product_key=product_key,
            period=members[0]["period"],
            output_path=image_path,
            root=root,
            dataset=dataset_label,
            period_label=target_label,
            baseline_label=f"WRIT native climatologies; {WRIT_CLIMATOLOGY_YEARS}",
            title=title,
            source_label=f"NOAA PSL WRIT / {dataset_label}",
            lead_label="Inverse-distance analog composite",
            ensemble_label=f"{len(members)}-analog weighted composite",
            initialization_label=f"Historical analog composite · {target_label}",
            footer_text="\n".join(footer_lines),
        )
        return {
            "product": product_key,
            "label": product_label,
            "provider": spec["provider"],
            "status": "ready",
            "image": _relative_asset(root, image_path),
            "composite_key": composite_key,
            "composite_method": analogs.COMPOSITE_METHOD,
            "composite_pattern_weight": analogs.COMPOSITE_PATTERN_WEIGHT,
            "composite_amplitude_weight": analogs.COMPOSITE_AMPLITUDE_WEIGHT,
            "composite_count": len(members),
            "analog_members": [_composite_member_summary(member) for member in members],
            "valid_target": target,
            "valid_target_label": target_label,
            "source_url": PSL_MAP_PAGE,
            "source_urls": source_urls,
            "provider_asset_urls": [asset["provider_asset_url"] for asset in assets],
            "provider_image_urls": [asset["provider_image_url"] for asset in assets],
            "dataset": dataset_label,
            "datasets": dataset_labels,
            "climatology_years": WRIT_CLIMATOLOGY_YEARS,
            "climatology_label": f"WRIT native climatologies; {WRIT_CLIMATOLOGY_YEARS}",
            "rendering": rendering,
            "map_region": spec["map_region"],
            "generated_utc": _now_iso(),
        }
    except (AnalogProductError, OSError, ValueError) as exc:
        return _retained_composite_or_unavailable(
            root=root,
            old=old,
            product_key=product_key,
            composite_key=composite_key,
            source_urls=source_urls,
            member_count=len(members),
            error=str(exc),
        )


def _build_snowfall_composite_product(
    *,
    root: Path,
    output_dir: Path,
    model: str,
    target: str,
    target_label: str,
    members: list[dict[str, Any]],
    old: dict[str, Any] | None,
    fetcher: Callable[[str, int], bytes],
    timeout: int,
    station_grid_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a weighted snowfall-departure composite from ACIS stations."""

    product_key = MRCC_SNOWFALL_COMPOSITE_KEY
    spec = COMPOSITE_PRODUCT_SPECS[product_key]
    composite_key = f"{_composite_key(model, target, members)}:{MRCC_SNOWFALL_COMPOSITE_VERSION}"
    source_urls = [_mrcc_station_data_url(member["period"]) for member in members]
    image_path = output_dir / model / target / "composite" / f"{product_key}.png"
    if (
        old
        and old.get("composite_key") == composite_key
        and old.get("composite_method") == analogs.COMPOSITE_METHOD
        and old.get("composite_version") == MRCC_SNOWFALL_COMPOSITE_VERSION
        and old.get("status") == "ready"
        and old.get("image")
        and _resolve_rooted(root, str(old.get("image", ""))).exists()
    ):
        return old

    try:
        station_timeout = max(timeout, MRCC_GENERATION_TIMEOUT_SECONDS)
        assets = [
            _fetch_mrcc_station_grid(fetcher, member["period"], station_timeout, station_grid_cache)
            for member in members
        ]
        composite_grid = _average_writ_grids(
            [asset["grid"] for asset in assets],
            [float(member["weight"]) for member in members],
        )
        interpolation_methods = sorted({str(asset["interpolation"]) for asset in assets})
        interpolation = (
            interpolation_methods[0]
            if len(interpolation_methods) == 1
            else "mixed: " + "; ".join(interpolation_methods)
        )
        footer_lines = [
            "Weighted analog members: "
            + "  •  ".join(
                f"{member['rank']} {member['label']} ({member['weight'] * 100:.1f}%)"
                for member in members[:3]
            )
        ]
        if len(members) > 3:
            footer_lines.append(
                "  •  ".join(
                    f"{member['rank']} {member['label']} ({member['weight'] * 100:.1f}%)"
                    for member in members[3:]
                )
            )
        rendering = _render_mrcc_snowfall_grid(
            grid=composite_grid,
            period=members[0]["period"],
            output_path=image_path,
            root=root,
            target_label=target_label,
            member_count=len(members),
            footer_text="\n".join(footer_lines),
        )
        return {
            "product": product_key,
            "label": f"Weighted Top {len(members)}-Analog Composite · {spec['label']}",
            "provider": spec["provider"],
            "status": "ready",
            "image": _relative_asset(root, image_path),
            "composite_key": composite_key,
            "composite_method": analogs.COMPOSITE_METHOD,
            "composite_version": MRCC_SNOWFALL_COMPOSITE_VERSION,
            "composite_pattern_weight": analogs.COMPOSITE_PATTERN_WEIGHT,
            "composite_amplitude_weight": analogs.COMPOSITE_AMPLITUDE_WEIGHT,
            "composite_count": len(members),
            "analog_members": [_composite_member_summary(member) for member in members],
            "valid_target": target,
            "valid_target_label": target_label,
            "source_url": MRCC_MAP_PAGE,
            "source_urls": source_urls,
            "provider_asset_urls": source_urls,
            "dataset": "MRCC / ACIS MultiStnData monthly snowfall departures",
            "climatology_years": "provider-defined",
            "climatology_label": MRCC_SNOWFALL_BASELINE_LABEL,
            "interpolation": f"{interpolation} on a 0.25° grid",
            "station_counts": [asset["station_count"] for asset in assets],
            "station_states": list(MRCC_SNOWFALL_SOURCE_STATES),
            "map_mask_states": list(MRCC_SNOWFALL_MASK_STATE_NAMES),
            "rendering": rendering,
            "map_region": MRCC_SNOWFALL_MAP_REGION,
            "region": list(MRCC_SNOWFALL_REGION),
            "generated_utc": _now_iso(),
        }
    except (AnalogProductError, OSError, TypeError, ValueError) as exc:
        return _retained_composite_or_unavailable(
            root=root,
            old=old,
            product_key=product_key,
            composite_key=composite_key,
            source_urls=source_urls,
            member_count=len(members),
            error=str(exc),
        )


def _build_product(
    *,
    root: Path,
    output_dir: Path,
    model: str,
    target: str,
    top: dict[str, Any],
    period: dict[str, Any],
    product_key: str,
    old: dict[str, Any] | None,
    fetcher: Callable[[str, int], bytes],
    timeout: int,
    climatology_years: str,
) -> dict[str, Any]:
    spec = PRODUCT_SPECS[product_key]
    top_key = _top_key(model, target, top)
    source_url = _psl_url(period, spec) if product_key.startswith("psl_") else _mrcc_url(period)
    if old and old.get("top_analog_key") == top_key:
        old_path = _resolve_rooted(root, str(old.get("image", ""))) if old.get("image") else None
        provider_asset_url = str(old.get("provider_asset_url", "")).lower()
        legacy_psl_icon = product_key.startswith("psl_") and "/img/icons/" in provider_asset_url
        source_changed = str(old.get("source_url", "")) != source_url
        old_rendering = old.get("rendering") if isinstance(old.get("rendering"), dict) else {}
        renderer_current = (
            not product_key.startswith("psl_")
            or old_rendering.get("id") == WRIT_RENDERER_ID
        )
        if (
            old_path
            and old_path.exists()
            and old.get("status") == "ready"
            and not legacy_psl_icon
            and not source_changed
            and renderer_current
        ):
            return old

    image_path = output_dir / model / target / str(period["winter_year"]) / f"{product_key}.png"
    try:
        image: bytes | None = None
        writ_dataset = _writ_dataset_for_period(period) if product_key.startswith("psl_") else ""
        if product_key.startswith("psl_"):
            page = fetcher(source_url, timeout)
            image_url = _extract_psl_image_url(page)
            data_url = _extract_psl_netcdf_url(page)
            data = fetcher(data_url, timeout)
            rendering = _render_writ_netcdf(
                content=data,
                product_key=product_key,
                period=period,
                output_path=image_path,
                root=root,
            )
            provider_asset_url = data_url
        else:
            mrcc_timeout = max(timeout, MRCC_GENERATION_TIMEOUT_SECONDS)
            image = _fetch_mrcc_image(fetcher, source_url, mrcc_timeout)
            provider_asset_url = source_url
            rendering = None
        if image is not None:
            _write_png(image_path, image)
        product = {
            "product": product_key,
            "label": spec["label"],
            "provider": spec["provider"],
            "status": "ready",
            "image": _relative_asset(root, image_path),
            "top_analog_key": top_key,
            "period": period,
            "source_url": source_url,
            "provider_asset_url": provider_asset_url,
            "dataset": writ_dataset if product_key.startswith("psl_") else "MRCC station-interpolated snowfall",
            "climatology_years": WRIT_CLIMATOLOGY_YEARS if product_key.startswith("psl_") else climatology_years,
            "generated_utc": _now_iso(),
        }
        if product_key.startswith("psl_"):
            product["provider_image_url"] = image_url
            product["rendering"] = rendering
            product["climatology_label"] = _writ_climatology_label(writ_dataset)
            product["map_region"] = spec["map_region"]
        return product
    except (AnalogProductError, OSError, ValueError) as exc:
        return _retained_or_unavailable(
            root=root,
            old=old,
            product_key=product_key,
            top_key=top_key,
            source_url=source_url,
            error=str(exc),
        )


def build_manifest(
    *,
    root: Path,
    analog_manifest_path: Path,
    output_manifest_path: Path,
    output_dir: Path,
    timeout: int = 180,
    fetcher: Callable[[str, int], bytes] = _default_fetch,
) -> dict[str, Any]:
    """Build products for every published CFSv2/Super Ensemble top analog."""

    analog_manifest = _read_json(analog_manifest_path)
    old_entries = _existing_entries(output_manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = analog_manifest.get("source") if isinstance(analog_manifest.get("source"), dict) else {}
    climatology_years = str(source.get("climatology_years") or "unspecified")
    entries: list[dict[str, Any]] = []
    writ_grid_cache: dict[str, dict[str, Any]] = {}
    station_grid_cache: dict[str, dict[str, Any]] = {}
    for raw in analog_manifest.get("entries", []):
        if not isinstance(raw, dict) or str(raw.get("model")) not in MODEL_LABELS:
            continue
        results = raw.get("results")
        if not isinstance(results, list) or not results:
            continue
        top = next((item for item in results if isinstance(item, dict) and int(item.get("rank", 0)) == 1), None)
        if not top:
            top = next((item for item in results if isinstance(item, dict)), None)
        if not top:
            continue
        model = str(raw["model"])
        target = str(raw["target"])
        period = _period_for_result(target, top)
        old_entry = old_entries.get((model, target))
        old_products = old_entry.get("products", {}) if isinstance(old_entry, dict) else {}
        old_composites = old_entry.get("composites", {}) if isinstance(old_entry, dict) else {}
        products = {
            key: _build_product(
                root=root,
                output_dir=output_dir,
                model=model,
                target=target,
                top=top,
                period=period,
                product_key=key,
                old=old_products.get(key) if isinstance(old_products, dict) else None,
                fetcher=fetcher,
                timeout=timeout,
                climatology_years=climatology_years,
            )
            for key in PRODUCT_SPECS
        }
        composite_members = _composite_members(target, results)
        composites: dict[str, dict[str, Any]] = {}
        if composite_members:
            for key in COMPOSITE_PRODUCT_KEYS:
                composites[key] = _build_composite_product(
                    root=root,
                    output_dir=output_dir,
                    model=model,
                    target=target,
                    target_label=str(raw.get("target_label") or target),
                    members=composite_members,
                    product_key=key,
                    old=old_composites.get(key) if isinstance(old_composites, dict) else None,
                    fetcher=fetcher,
                    timeout=timeout,
                    grid_cache=writ_grid_cache,
                )
            composites[MRCC_SNOWFALL_COMPOSITE_KEY] = _build_snowfall_composite_product(
                root=root,
                output_dir=output_dir,
                model=model,
                target=target,
                target_label=str(raw.get("target_label") or target),
                members=composite_members,
                old=old_composites.get(MRCC_SNOWFALL_COMPOSITE_KEY) if isinstance(old_composites, dict) else None,
                fetcher=fetcher,
                timeout=timeout,
                station_grid_cache=station_grid_cache,
            )
        statuses = {str(product.get("status")) for product in products.values()}
        statuses.update(str(product.get("status")) for product in composites.values())
        entry_status = "ready" if statuses == {"ready"} else "stale" if "stale" in statuses else "partial" if "ready" in statuses else "unavailable"
        composite_info = (
            {
                "count": len(composite_members),
                "method": analogs.COMPOSITE_METHOD,
                "pattern_weight": analogs.COMPOSITE_PATTERN_WEIGHT,
                "amplitude_weight": analogs.COMPOSITE_AMPLITUDE_WEIGHT,
                "members": [_composite_member_summary(member) for member in composite_members],
                "products": list(composites),
            }
            if composite_members
            else None
        )
        entries.append(
            {
                "model": model,
                "model_label": str(raw.get("model_label") or MODEL_LABELS[model]),
                "target": target,
                "target_label": str(raw.get("target_label") or target),
                "init_utc": raw.get("init_utc"),
                "top_analog_key": _top_key(model, target, top),
                "top_analog": top,
                "period": period,
                "status": entry_status,
                "products": products,
                "composite": composite_info,
                "composites": composites,
            }
        )

    statuses = {str(entry.get("status")) for entry in entries}
    overall = "ready" if entries and statuses == {"ready"} else "partial" if entries and ("ready" in statuses or "stale" in statuses or "partial" in statuses) else "unavailable"
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "seasonal_analog_products_manifest",
        "generated_utc": _now_iso(),
        "source": {
            "analog_manifest": _relative_asset(root, analog_manifest_path),
            "climatology_years": climatology_years,
            "analog_matching_climatology_years": climatology_years,
            "writ_dataset": WRIT_DATASET,
            "writ_pre_1979_fallback_dataset": WRIT_EARLY_DATASET,
            "writ_climatology_years": WRIT_CLIMATOLOGY_YEARS,
            "writ_climatology_label": WRIT_CLIMATOLOGY_LABEL,
            "psl": PSL_MAP_PAGE,
            "mrcc": MRCC_MAP_PAGE,
            "nws_region": "NWS Eastern Region (ER)",
            "period_rule": "monthly analogs use that calendar month; DJF analogs use December through February",
            "retained_on_source_failure": True,
            "composite": {
                "count": analogs.COMPOSITE_ANALOG_COUNT,
                "method": analogs.COMPOSITE_METHOD,
                "pattern_weight": analogs.COMPOSITE_PATTERN_WEIGHT,
                "amplitude_weight": analogs.COMPOSITE_AMPLITUDE_WEIGHT,
                "products": list(ANALOG_COMPOSITE_PRODUCT_KEYS),
                "snowfall": "weighted top-N composite uses MRCC/ACIS station departures; rendered MRCC map remains rank-1",
                "snowfall_provider": MRCC_SNOWFALL_PROVIDER_LABEL,
                "snowfall_data": MRCC_ACIS_MULTI_STATION_URL,
                "snowfall_interpolation": "linear station interpolation with nearest-neighbor edge fill on a 0.25° grid; NumPy inverse-distance fallback is recorded per product when SciPy is unavailable",
            },
        },
        "status": overall,
        "entries": entries,
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Pages tree root")
    parser.add_argument("--analog-manifest", default="seasonal/analog_z500_manifest.json")
    parser.add_argument("--output", default="seasonal/analog_products_manifest.json")
    parser.add_argument("--output-dir", default="seasonal/analog_products")
    parser.add_argument("--timeout", type=int, default=300)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    analog_manifest_path = _resolve_rooted(root, args.analog_manifest)
    output_manifest_path = _resolve_rooted(root, args.output)
    output_dir = _resolve_rooted(root, args.output_dir)
    try:
        payload = build_manifest(
            root=root,
            analog_manifest_path=analog_manifest_path,
            output_manifest_path=output_manifest_path,
            output_dir=output_dir,
            timeout=args.timeout,
        )
        write_manifest(output_manifest_path, payload)
    except AnalogProductError as exc:
        print(f"SEASONAL ANALOG PRODUCTS ERROR: {exc}")
        return 2
    print(f"wrote seasonal analog products manifest: {output_manifest_path} ({len(payload['entries'])} entries; {payload['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

