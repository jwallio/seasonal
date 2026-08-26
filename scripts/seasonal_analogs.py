"""Pattern-based seasonal 500-mb analog matching.

The matcher ranks normalized anomaly patterns so forecast fields may use their
native model baseline while the historical fields come from the AnalogWX ERA5
archive.  It also reports the relative anomaly amplitude and uses that metric
only for the documented multi-analog composite weights.
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


SCHEMA_VERSION = "seasonal_z500_analogs_v1"
ARCHIVE_LABEL = "AnalogWX ERA5 daily DJF Z500 anomaly archive"
ANALOG_MONTHS = (12, 1, 2)
NH_WEIGHT = 0.7
CONUS_WEIGHT = 0.3
DEFAULT_TOP_N = 10
COMPOSITE_ANALOG_COUNT = 5
COMPOSITE_PATTERN_WEIGHT = 0.8
COMPOSITE_AMPLITUDE_WEIGHT = 0.2
COMPOSITE_MIN_DISTANCE = 0.05
PATTERN_METHOD = "cosine-latitude-weighted centered pattern correlation"
AMPLITUDE_METHOD = "cosine-latitude-weighted anomaly RMS amplitude similarity"
COMPOSITE_METHOD = "inverse similarity-distance weighting over the top analogs"

_MONTH_TARGET = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})$")
_DJF_TARGET = re.compile(
    r"^(?P<start_year>\d{4})12-(?P<end_year>\d{4})02$"
)


class SeasonalAnalogError(ValueError):
    """A requested analog period or field cannot be matched."""


@dataclass(frozen=True)
class HistoricalField:
    """One complete historical monthly or DJF mean field."""

    label: str
    winter_year: int
    values: np.ndarray
    sample_count: int


@dataclass(frozen=True)
class HistoricalSeasonalFields:
    """Aggregated historical fields sharing one latitude/longitude grid."""

    target: str
    period_type: str
    lats: np.ndarray
    lons: np.ndarray
    records: tuple[HistoricalField, ...]
    source: str = ARCHIVE_LABEL
    climatology_years: str = "1981-2010"


def parse_target(target: str) -> dict[str, Any]:
    """Validate a dashboard target and return its period metadata."""

    value = str(target).strip()
    month_match = _MONTH_TARGET.fullmatch(value)
    if month_match:
        year = int(month_match.group("year"))
        month = int(month_match.group("month"))
        if month not in ANALOG_MONTHS:
            raise SeasonalAnalogError(
                f"analog search supports December, January, February, or DJF; got {value}"
            )
        month_name = calendar.month_name[month]
        return {
            "target": value,
            "period_type": "month",
            "month": month,
            "year": year,
            "label": f"{month_name} {year}",
        }

    djf_match = _DJF_TARGET.fullmatch(value)
    if djf_match:
        start_year = int(djf_match.group("start_year"))
        end_year = int(djf_match.group("end_year"))
        if end_year != start_year + 1:
            raise SeasonalAnalogError(f"DJF target must span consecutive years; got {value}")
        return {
            "target": value,
            "period_type": "djf",
            "start_year": start_year,
            "end_year": end_year,
            "label": f"DJF {start_year}-{str(end_year)[-2:]}",
        }

    raise SeasonalAnalogError(f"unsupported analog target {value!r}")


def _date_parts(value: Any) -> tuple[int, int, int]:
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            return int(value[0]), int(value[1]), int(value[2])
        except (TypeError, ValueError) as exc:
            raise SeasonalAnalogError(f"could not parse archive date {value!r}") from exc
    if isinstance(value, np.datetime64):
        text = np.datetime_as_string(value, unit="D")
    elif isinstance(value, (dt.datetime, dt.date)):
        text = value.strftime("%Y-%m-%d")
    else:
        text = str(value)[:10]
    try:
        return int(text[:4]), int(text[5:7]), int(text[8:10])
    except (TypeError, ValueError) as exc:
        raise SeasonalAnalogError(f"could not parse archive date {value!r}") from exc


def _winter_year(year: int, month: int) -> int:
    return year + 1 if month == 12 else year


def _expected_sample_count(winter_year: int, period_type: str, month: int | None) -> int:
    if period_type == "month":
        if month is None:
            raise SeasonalAnalogError("monthly analog period is missing its month")
        source_year = winter_year - 1 if month == 12 else winter_year
        return calendar.monthrange(source_year, month)[1]
    return (
        calendar.monthrange(winter_year - 1, 12)[1]
        + calendar.monthrange(winter_year, 1)[1]
        + calendar.monthrange(winter_year, 2)[1]
    )


def _historical_label(winter_year: int, period_type: str, month: int | None) -> str:
    if period_type == "month":
        if month is None:
            raise SeasonalAnalogError("monthly analog period is missing its month")
        source_year = winter_year - 1 if month == 12 else winter_year
        return f"{calendar.month_name[month]} {source_year}"
    return f"DJF {winter_year - 1}-{str(winter_year)[-2:]}"


def complete_period_groups(
    times: Iterable[Any],
    target: str,
) -> list[tuple[int, list[int]]]:
    """Return complete historical index groups without loading field values."""

    metadata = parse_target(target)
    dates = [_date_parts(value) for value in times]
    groups: dict[int, list[int]] = {}
    for index, (year, month, _day) in enumerate(dates):
        if metadata["period_type"] == "month":
            if month != metadata["month"]:
                continue
            winter_year = _winter_year(year, month)
        else:
            if month not in ANALOG_MONTHS:
                continue
            winter_year = _winter_year(year, month)
        groups.setdefault(winter_year, []).append(index)

    complete: list[tuple[int, list[int]]] = []
    for winter_year in sorted(groups):
        indices = groups[winter_year]
        expected = _expected_sample_count(
            winter_year,
            metadata["period_type"],
            metadata.get("month"),
        )
        if len(indices) == expected:
            complete.append((winter_year, indices))
    return complete


def build_historical_fields(
    times: Iterable[Any],
    values: np.ndarray,
    lats: Iterable[float],
    lons: Iterable[float],
    target: str,
    *,
    source: str = ARCHIVE_LABEL,
    climatology_years: str = "1981-2010",
) -> HistoricalSeasonalFields:
    """Aggregate complete historical month or DJF fields from daily data."""

    metadata = parse_target(target)
    dates = [_date_parts(value) for value in times]
    data = np.asarray(values, dtype=float)
    if data.ndim != 3 or data.shape[0] != len(dates):
        raise SeasonalAnalogError("historical values must have shape (time, lat, lon)")
    lat_values = np.asarray(list(lats), dtype=float)
    lon_values = np.asarray(list(lons), dtype=float)
    if data.shape[1:] != (len(lat_values), len(lon_values)):
        raise SeasonalAnalogError("historical grid coordinates do not match field shape")

    records: list[HistoricalField] = []
    for winter_year, indices in complete_period_groups(dates, target):
        with np.errstate(invalid="ignore"):
            aggregate = np.nanmean(data[indices], axis=0)
        records.append(
            HistoricalField(
                label=_historical_label(
                    winter_year,
                    metadata["period_type"],
                    metadata.get("month"),
                ),
                winter_year=winter_year,
                values=aggregate,
                sample_count=len(indices),
            )
        )

    if not records:
        raise SeasonalAnalogError(f"no complete historical periods available for {target}")
    return HistoricalSeasonalFields(
        target=str(target),
        period_type=metadata["period_type"],
        lats=lat_values,
        lons=lon_values,
        records=tuple(records),
        source=source,
        climatology_years=climatology_years,
    )


def load_zarr_historical_fields(
    path: Path,
    target: str,
    *,
    variable: str = "z500_anom",
    source: str = ARCHIVE_LABEL,
    climatology_years: str = "1981-2010",
) -> HistoricalSeasonalFields:
    """Load only the grouped means needed for one target from an AnalogWX Zarr archive."""

    try:
        import xarray as xr
    except ImportError as exc:  # pragma: no cover - environment-specific dependency
        raise SeasonalAnalogError(
            "xarray and zarr are required to read the AnalogWX archive"
        ) from exc

    dataset = xr.open_zarr(str(path), consolidated=False)
    try:
        if variable not in dataset:
            raise SeasonalAnalogError(f"AnalogWX archive is missing variable {variable!r}")
        data = dataset[variable].transpose("time", "lat", "lon")
        groups = complete_period_groups(data["time"].values, target)
        if not groups:
            raise SeasonalAnalogError(f"no complete historical periods available for {target}")
        flat_indices = [index for _winter_year, indices in groups for index in indices]
        group_years = np.asarray(
            [winter_year for winter_year, indices in groups for _index in indices],
            dtype=int,
        )
        selected = data.isel(time=flat_indices)
        selected = selected.assign_coords(winter_year=("time", group_years))
        grouped = selected.groupby("winter_year").mean("time").load()
        metadata = parse_target(target)
        records = tuple(
            HistoricalField(
                label=_historical_label(
                    winter_year,
                    metadata["period_type"],
                    metadata.get("month"),
                ),
                winter_year=winter_year,
                values=np.asarray(grouped.sel(winter_year=winter_year).values, dtype=float),
                sample_count=len(indices),
            )
            for winter_year, indices in groups
        )
        return HistoricalSeasonalFields(
            target=str(target),
            period_type=metadata["period_type"],
            lats=np.asarray(data["lat"].values, dtype=float),
            lons=np.asarray(data["lon"].values, dtype=float),
            records=records,
            source=source,
            climatology_years=climatology_years,
        )
    finally:
        dataset.close()


def _normalized_lons(values: Iterable[float]) -> np.ndarray:
    return np.mod(np.asarray(list(values), dtype=float), 360.0)


def regrid_nearest(
    values: np.ndarray,
    source_lats: Iterable[float],
    source_lons: Iterable[float],
    target_lats: Iterable[float],
    target_lons: Iterable[float],
) -> np.ndarray:
    """Regrid a field by nearest latitude and circular longitude."""

    data = np.asarray(values, dtype=float)
    source_lat_values = np.asarray(list(source_lats), dtype=float)
    source_lon_values = _normalized_lons(source_lons)
    target_lat_values = np.asarray(list(target_lats), dtype=float)
    target_lon_values = _normalized_lons(target_lons)
    if data.shape != (len(source_lat_values), len(source_lon_values)):
        raise SeasonalAnalogError("forecast grid coordinates do not match field shape")
    lat_indices = np.abs(source_lat_values[:, None] - target_lat_values[None, :]).argmin(axis=0)
    lon_distance = np.abs(
        (source_lon_values[None, :] - target_lon_values[:, None] + 180.0) % 360.0 - 180.0
    )
    lon_indices = lon_distance.argmin(axis=1)
    return data[np.ix_(lat_indices, lon_indices)]


def _region_mask(
    lats: np.ndarray,
    lons: np.ndarray,
    region: str,
) -> np.ndarray:
    lat_mask = (lats >= 20.0) & (lats <= 90.0)
    lon_values = _normalized_lons(lons)
    if region == "nh":
        lon_mask = np.ones(len(lon_values), dtype=bool)
    elif region == "conus":
        lat_mask &= (lats >= 24.0) & (lats <= 50.0)
        lon_mask = (lon_values >= 235.0) & (lon_values <= 295.0)
    else:
        raise SeasonalAnalogError(f"unknown analog region {region!r}")
    return lat_mask[:, None] & lon_mask[None, :]


def _pattern_metrics(
    forecast: np.ndarray,
    historical: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    region: str,
) -> dict[str, float]:
    """Return centered pattern correlation and anomaly-amplitude metrics."""

    region_mask = _region_mask(lats, lons, region)
    valid = region_mask & np.isfinite(forecast) & np.isfinite(historical)
    total = int(region_mask.sum())
    count = int(valid.sum())
    valid_fraction = count / total if total else 0.0
    empty = {
        "correlation": float("nan"),
        "amplitude_similarity": float("nan"),
        "forecast_amplitude": float("nan"),
        "historical_amplitude": float("nan"),
        "valid_fraction": valid_fraction,
    }
    if total == 0 or count < 2 or count / total < 0.5:
        return empty
    weights = np.cos(np.deg2rad(lats))[:, None]
    weights = np.broadcast_to(weights, forecast.shape)[valid]
    left = forecast[valid]
    right = historical[valid]
    weight_sum = float(weights.sum())
    if weight_sum <= 0:
        return empty

    # Amplitude is the area-weighted RMS of the anomaly itself.  It is kept
    # separate from the centered correlation so a map with the right shape
    # but a much larger or smaller signal is visible to the user.
    forecast_amplitude = float(np.sqrt(np.sum(weights * left * left) / weight_sum))
    historical_amplitude = float(np.sqrt(np.sum(weights * right * right) / weight_sum))
    amplitude_max = max(forecast_amplitude, historical_amplitude)
    if amplitude_max == 0.0:
        amplitude_similarity = 1.0
    else:
        amplitude_similarity = min(forecast_amplitude, historical_amplitude) / amplitude_max

    centered_left = left - float(np.sum(weights * left) / weight_sum)
    centered_right = right - float(np.sum(weights * right) / weight_sum)
    denominator = float(
        np.sqrt(
            np.sum(weights * centered_left * centered_left)
            * np.sum(weights * centered_right * centered_right)
        )
    )
    if denominator <= 0:
        empty.update(
            {
                "amplitude_similarity": amplitude_similarity,
                "forecast_amplitude": forecast_amplitude,
                "historical_amplitude": historical_amplitude,
            }
        )
        return empty
    return {
        "correlation": float(np.sum(weights * centered_left * centered_right) / denominator),
        "amplitude_similarity": float(amplitude_similarity),
        "forecast_amplitude": forecast_amplitude,
        "historical_amplitude": historical_amplitude,
        "valid_fraction": valid_fraction,
    }


def _pattern_correlation(
    forecast: np.ndarray,
    historical: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    region: str,
) -> tuple[float, float]:
    """Keep the original compact correlation helper for callers and tests."""

    metrics = _pattern_metrics(forecast, historical, lats, lons, region)
    return metrics["correlation"], metrics["valid_fraction"]


def composite_weights(
    results: Iterable[dict[str, Any]],
    *,
    count: int = COMPOSITE_ANALOG_COUNT,
) -> list[float]:
    """Return normalized inverse-distance weights for the first ``count`` results.

    Pattern correlation remains the ranking metric.  The amplitude term only
    controls how much each selected analog contributes to the composite.  A
    missing amplitude field is treated as neutral for old manifests.
    """

    if count < 1:
        raise SeasonalAnalogError("composite analog count must be positive")
    selected = list(results)[:count]
    if not selected:
        return []
    distances: list[float] = []
    for result in selected:
        try:
            pattern = float(result.get("pattern_correlation", 0.0))
        except (TypeError, ValueError):
            pattern = 0.0
        try:
            amplitude = float(result.get("amplitude_similarity", 1.0))
        except (TypeError, ValueError):
            amplitude = 1.0
        if not np.isfinite(pattern):
            pattern = 0.0
        if not np.isfinite(amplitude):
            amplitude = 1.0
        pattern = float(np.clip(pattern, -1.0, 1.0))
        amplitude = float(np.clip(amplitude, 0.0, 1.0))
        distance = (
            # Normalize correlation's [-1, 1] range to the same [0, 1]
            # distance scale used by the amplitude similarity.
            COMPOSITE_PATTERN_WEIGHT * ((1.0 - pattern) / 2.0)
            + COMPOSITE_AMPLITUDE_WEIGHT * (1.0 - amplitude)
        )
        distances.append(max(COMPOSITE_MIN_DISTANCE, distance))
    inverse_distances = 1.0 / np.asarray(distances, dtype=float)
    total = float(inverse_distances.sum())
    if total <= 0.0 or not np.isfinite(total):
        return [1.0 / len(selected)] * len(selected)
    return [float(value / total) for value in inverse_distances]


def match_forecast(
    forecast_values: np.ndarray,
    forecast_lats: Iterable[float],
    forecast_lons: Iterable[float],
    historical: HistoricalSeasonalFields,
    *,
    top_n: int = DEFAULT_TOP_N,
    nh_weight: float = NH_WEIGHT,
    conus_weight: float = CONUS_WEIGHT,
) -> list[dict[str, Any]]:
    """Rank historical fields by the AnalogWX NH/CONUS pattern score."""

    if top_n < 1:
        raise SeasonalAnalogError("top_n must be positive")
    if nh_weight < 0 or conus_weight < 0 or nh_weight + conus_weight <= 0:
        raise SeasonalAnalogError("analog region weights must be non-negative and non-zero")
    forecast = regrid_nearest(
        np.asarray(forecast_values, dtype=float),
        forecast_lats,
        forecast_lons,
        historical.lats,
        historical.lons,
    )
    scores: list[dict[str, Any]] = []
    weight_total = nh_weight + conus_weight
    for record in historical.records:
        if record.values.shape != forecast.shape:
            raise SeasonalAnalogError("historical fields do not share the archive grid")
        nh = _pattern_metrics(
            forecast, record.values, historical.lats, historical.lons, "nh"
        )
        conus = _pattern_metrics(
            forecast, record.values, historical.lats, historical.lons, "conus"
        )
        components = [(nh["correlation"], nh_weight), (conus["correlation"], conus_weight)]
        available = [(score, weight) for score, weight in components if np.isfinite(score)]
        if not available:
            continue
        score = sum(value * weight for value, weight in available) / sum(
            weight for _value, weight in available
        )
        amplitude_components = [
            (nh["amplitude_similarity"], nh_weight),
            (conus["amplitude_similarity"], conus_weight),
        ]
        amplitude_available = [
            (value, weight) for value, weight in amplitude_components if np.isfinite(value)
        ]
        amplitude_similarity = (
            sum(value * weight for value, weight in amplitude_available)
            / sum(weight for _value, weight in amplitude_available)
            if amplitude_available
            else float("nan")
        )
        scores.append(
            {
                "label": record.label,
                "winter_year": record.winter_year,
                "pattern_correlation": round(float(score), 6),
                "nh_correlation": round(float(nh["correlation"]), 6) if np.isfinite(nh["correlation"]) else None,
                "conus_correlation": round(float(conus["correlation"]), 6) if np.isfinite(conus["correlation"]) else None,
                "amplitude_similarity": round(float(amplitude_similarity), 6) if np.isfinite(amplitude_similarity) else None,
                "nh_amplitude_similarity": round(float(nh["amplitude_similarity"]), 6) if np.isfinite(nh["amplitude_similarity"]) else None,
                "conus_amplitude_similarity": round(float(conus["amplitude_similarity"]), 6) if np.isfinite(conus["amplitude_similarity"]) else None,
                "nh_valid_fraction": round(float(nh["valid_fraction"]), 4),
                "conus_valid_fraction": round(float(conus["valid_fraction"]), 4),
                "sample_count": record.sample_count,
                "_sort_score": float(score),
                "_weight_total": weight_total,
            }
        )
    scores.sort(key=lambda item: (-item["_sort_score"], item["winter_year"]))
    selected = scores[:top_n]
    weights = composite_weights(selected)
    for index, item in enumerate(selected):
        item["composite_weight"] = round(weights[index], 6) if index < len(weights) else 0.0
        item.pop("_sort_score", None)
        item.pop("_weight_total", None)
    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank
    return selected


def build_artifact(
    *,
    model_key: str,
    run_id: str,
    init_utc: str,
    target: str,
    forecast_values: np.ndarray,
    forecast_lats: Iterable[float],
    forecast_lons: Iterable[float],
    historical: HistoricalSeasonalFields,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Build the compact JSON object consumed by the Compare page."""

    metadata = parse_target(target)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "seasonal_z500_analog_match",
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": {
            "label": historical.source,
            "climatology_years": historical.climatology_years,
            "period_coverage": "complete historical month/DJF groups only",
        },
        "query": {
            "model": str(model_key),
            "run_id": str(run_id),
            "init_utc": str(init_utc),
            "target": str(target),
            "period_type": metadata["period_type"],
            "label": metadata["label"],
            "method": PATTERN_METHOD,
            "regional_weights": {"nh": NH_WEIGHT, "conus": CONUS_WEIGHT},
            "amplitude_method": AMPLITUDE_METHOD,
            "composite": {
                "count": min(COMPOSITE_ANALOG_COUNT, top_n),
                "method": COMPOSITE_METHOD,
                "pattern_weight": COMPOSITE_PATTERN_WEIGHT,
                "amplitude_weight": COMPOSITE_AMPLITUDE_WEIGHT,
            },
            "historical_grid": {"lat_min": 20.0, "lat_max": 90.0, "resolution": "1 degree"},
        },
        "results": match_forecast(
            forecast_values,
            forecast_lats,
            forecast_lons,
            historical,
            top_n=top_n,
        ),
    }


def write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    """Atomically write one compact analog result."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_grid_state(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read the compressed grid format used by the seasonal renderers."""

    lons: list[float] = []
    lats: list[float] = []
    rows: dict[tuple[float, float], float] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["lon", "lat", "value"]:
            raise SeasonalAnalogError(f"unsupported numeric grid header in {path}")
        for row in reader:
            lon = float(row["lon"])
            lat = float(row["lat"])
            if lon not in lons:
                lons.append(lon)
            if lat not in lats:
                lats.append(lat)
            rows[(lat, lon)] = float(row["value"])
    if not lons or not lats or len(rows) != len(lons) * len(lats):
        raise SeasonalAnalogError(f"incomplete numeric grid in {path}")
    values = np.asarray(
        [[rows[(lat, lon)] for lon in lons] for lat in lats],
        dtype=float,
    )
    return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float), values
