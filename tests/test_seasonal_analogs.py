#!/usr/bin/env python3
"""Focused offline tests for the seasonal 500-mb analog matcher."""

import importlib.util
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "seasonal_analogs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("seasonal_analogs_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load seasonal analog matcher")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def daily_dates(start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def main() -> int:
    module = load_module()
    check(module.parse_target("202612")["label"] == "December 2026", "December target label is wrong")
    check(module.parse_target("202701")["period_type"] == "month", "January should be a monthly target")
    check(module.parse_target("202612-202702")["label"] == "DJF 2026-27", "DJF target label is wrong")
    try:
        module.parse_target("202611")
    except module.SeasonalAnalogError:
        pass
    else:
        raise AssertionError("November should not be an analog target")

    lats = np.array([20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0])
    lons = np.arange(0.0, 360.0, 10.0)
    base = np.sin(np.deg2rad(lats))[:, None] + np.cos(np.deg2rad(lons))[None, :]
    december_pattern = base + (lats[:, None] >= 45.0) * 0.7
    january_pattern = base - (lons[None, :] >= 180.0) * 0.6
    dates = daily_dates(date(2020, 12, 1), date(2022, 2, 28))
    fields = []
    for current in dates:
        if current.year == 2020:
            pattern = december_pattern
        elif current.month == 1:
            pattern = january_pattern
        else:
            pattern = december_pattern * 0.2
        fields.append(pattern)
    historical = module.build_historical_fields(
        dates,
        np.asarray(fields),
        lats,
        lons,
        "202012",
    )
    check(len(historical.records) == 2, "complete December groups should be retained")
    check(historical.records[0].label == "December 2020", "December history label is wrong")

    january_dates = daily_dates(date(2021, 1, 1), date(2021, 1, 31))
    january_values = np.asarray([january_pattern for _ in january_dates])
    january_historical = module.build_historical_fields(
        january_dates,
        january_values,
        lats,
        lons,
        "202101",
    )
    result = module.match_forecast(
        january_pattern * 4.0 + 12.0,
        lats,
        lons,
        january_historical,
        top_n=1,
    )
    check(len(result) == 1 and result[0]["winter_year"] == 2021, "pattern match missed the exact analog")
    check(result[0]["pattern_correlation"] > 0.999, "pattern matching should ignore offset and scale")
    check(result[0]["rank"] == 1 and result[0]["sample_count"] == 31, "analog rank metadata is wrong")

    djf_dates = daily_dates(date(2020, 12, 1), date(2021, 2, 28))
    djf_values = np.asarray([base for _ in djf_dates])
    djf_historical = module.build_historical_fields(
        djf_dates,
        djf_values,
        lats,
        lons,
        "202012-202102",
    )
    check(len(djf_historical.records) == 1, "one complete DJF group should be retained")
    check(djf_historical.records[0].sample_count == 90, "DJF sample count should include Dec-Jan-Feb")
    artifact = module.build_artifact(
        model_key="cfsv2",
        run_id="cfsv2-2026081818",
        init_utc="2026-08-18T18:00:00Z",
        target="202012-202102",
        forecast_values=base,
        forecast_lats=lats,
        forecast_lons=lons,
        historical=djf_historical,
        top_n=1,
    )
    check(artifact["schema_version"] == module.SCHEMA_VERSION, "artifact schema is missing")
    check(artifact["query"]["period_type"] == "djf", "artifact period type is wrong")
    check(artifact["results"][0]["label"] == "DJF 2020-21", "artifact result label is wrong")

    print("SEASONAL ANALOGS OK: periods, complete groups, normalized matching, and artifact schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
