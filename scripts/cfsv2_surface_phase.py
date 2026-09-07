"""Estimated snowfall from six-hour APCP and surface precipitation type.

Reconstruct each cycle before ensemble/reference averaging. The trapezoid
uses the two endpoint CSNOW indicators; it is a sampling approximation,
not a measured within-interval snow fraction. All amounts stay in inches
water equivalent until the existing 10:1 display boundary.
"""
import calendar
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import eccodes as ec
import numpy as np

METHOD = "surface_phase_apcp_endpoint_trapezoid_v1"
YEARS = list(range(2011, 2026))
PARAMETERS = {"apcp": 8, "crain": 192, "cfrzr": 193, "cicep": 194, "csnow": 195}


def endpoints(target):
    start = datetime.strptime(target, "%Y%m")
    days = calendar.monthrange(start.year, start.month)[1]
    return [(start + timedelta(hours=6*i)).strftime("%Y%m%d%H")
            for i in range(days*4 + 1)]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interval_amount(apcp, snow_start, snow_end):
    """APCP is already kg/m² per six hours: do NOT multiply by seconds."""
    p, a, b = (np.asarray(x, dtype=float) for x in (apcp, snow_start, snow_end))
    if p.shape != a.shape or p.shape != b.shape:
        raise ValueError("Precipitation and phase grids differ")
    if not all(np.isfinite(x).all() for x in (p, a, b)) or (p < 0).any():
        raise ValueError("Missing or negative reconstruction input")
    if not all(np.isin(x, [0., 1.]).all() for x in (a, b)):
        raise ValueError("CSNOW must contain categorical 0/1 values")
    return p * (a+b) / (2*25.4)


def read_field(path, field, init, valid):
    h = ec.codes_new_from_message(Path(path).read_bytes())
    try:
        expected = dict(discipline=0, parameterCategory=1,
                        parameterNumber=PARAMETERS[field], typeOfLevel="surface",
                        dataDate=int(init[:8]), dataTime=int(init[8:])*100,
                        validityDate=int(valid[:8]), validityTime=int(valid[8:])*100,
                        numberOfMissing=0)
        for key, value in expected.items():
            if ec.codes_get(h, key) != value:
                raise ValueError(f"{path}: incorrect {key}")
        # CFS legacy GRIB conversion labels APCP instant despite its six-hour
        # accumulation. This exception is restricted to this source contract.
        if field == "apcp" and ec.codes_get(h, "units") != "kg m**-2":
            raise ValueError("APCP must be accumulated water mass, not a rate")
        if ec.codes_get(h, "gridType") != "regular_ll":
            raise ValueError("Expected the CFS pressure-product regular grid")
        v = ec.codes_get_values(h)
        lat = ec.codes_get_array(h, "latitudes")
        lon = (ec.codes_get_array(h, "longitudes") + 180) % 360 - 180
        x, y = np.unique(lon), np.unique(lat)
        if len(v) != len(x)*len(y) or not np.isfinite(v).all():
            raise ValueError("Incomplete source grid")
        if field != "apcp" and not np.isin(v, [0., 1.]).all():
            raise ValueError("Invalid precipitation-type flag")
        if (v < 0).any():
            raise ValueError("Negative precipitation")
        a = np.empty((len(y), len(x)))
        a[np.searchsorted(y, lat), np.searchsorted(x, lon)] = v
        return x, y, a
    finally:
        ec.codes_release(h)


def reconstruct(init, target, source):
    """source(valid, field) supplies a validated single-message local file."""
    times = endpoints(target)
    if datetime.strptime(init, "%Y%m%d%H") >= datetime.strptime(times[0], "%Y%m%d%H"):
        raise ValueError("A complete future calendar month is required")
    records, axes = [], None

    def field(valid, name):
        nonlocal axes
        path = source(valid, name)
        x, y, a = read_field(path, name, init, valid)
        if axes is None:
            axes = x, y
        elif not np.array_equal(x, axes[0]) or not np.array_equal(y, axes[1]):
            raise ValueError("Source coordinates changed within a month")
        records.append(dict(valid=valid, field=name, sha256=digest(path)))
        return a

    def phase(valid):
        return np.array([field(valid, name) for name in
                         ("csnow", "crain", "cicep", "cfrzr")])

    previous = phase(times[0])
    amounts = {k: np.zeros(previous.shape[1:]) for k in
               ("lwe", "precip_lwe", "start_phase_lwe", "end_phase_lwe",
                "transition_precip_lwe", "no_type_precip_lwe", "mixed_precip_lwe")}
    for valid in times[1:]:
        current = phase(valid)
        p = field(valid, "apcp")
        amounts["lwe"] += interval_amount(p, previous[0], current[0])
        amounts["precip_lwe"] += p/25.4
        amounts["start_phase_lwe"] += p*previous[0]/25.4
        amounts["end_phase_lwe"] += p*current[0]/25.4
        amounts["transition_precip_lwe"] += p*(previous[0] != current[0])/25.4
        amounts["no_type_precip_lwe"] += p*((previous.sum(axis=0) == 0) &
                                             (current.sum(axis=0) == 0))/25.4
        amounts["mixed_precip_lwe"] += p*((previous.sum(axis=0) > 1) |
                                           (current.sum(axis=0) > 1))/25.4
        previous = current
    if (amounts["lwe"] > amounts["precip_lwe"] + 1e-12).any():
        raise ValueError("Snow water exceeds total precipitation")
    meta = dict(schema_version=1, method=METHOD, initialization=init,
                target_month=target, member=1, units="inches_water_equivalent",
                interval_hours=6, intervals=len(times)-1,
                first_endpoint=times[0], last_endpoint=times[-1],
                source_records=records, quantity="estimated snowfall water equivalent",
                temporal_assumption="linear interpolation of endpoint CSNOW indicators",
                observation_bias_adjustment=False)
    return dict(lons=axes[0], lats=axes[1], **amounts), meta


def save_bundle(stem, arrays, meta):
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(stem.with_suffix(".npz"), **arrays)
    meta = dict(meta, grid_sha256=digest(stem.with_suffix(".npz")))
    stem.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def month_stem(directory, init, target):
    return Path(directory) / f"surface-phase-{init}-{target}"


def load_month(directory, init, target):
    stem = month_stem(directory, init, target)
    meta = json.loads(stem.with_suffix(".json").read_text())
    times = endpoints(target)
    for k, v in dict(schema_version=1, method=METHOD, initialization=init,
                     target_month=target, member=1, units="inches_water_equivalent",
                     intervals=len(times)-1, interval_hours=6,
                     first_endpoint=times[0], last_endpoint=times[-1]).items():
        if meta.get(k) != v:
            raise ValueError(f"Surface-phase bundle {k} mismatch")
    expected = {(v, f) for v in times for f in ("csnow", "crain", "cicep", "cfrzr")}
    expected.update((v, "apcp") for v in times[1:])
    records = meta.get("source_records", [])
    if len(records) != len(expected) or {(r["valid"], r["field"]) for r in records} != expected:
        raise ValueError("Incomplete source coverage")
    if digest(stem.with_suffix(".npz")) != meta.get("grid_sha256"):
        raise ValueError("Surface-phase checksum mismatch")
    with np.load(stem.with_suffix(".npz"), allow_pickle=False) as z:
        arrays = {k: z[k].copy() for k in z.files}
    validate_arrays(arrays, "lwe")
    for key in ("precip_lwe", "start_phase_lwe", "end_phase_lwe",
                "transition_precip_lwe", "no_type_precip_lwe", "mixed_precip_lwe"):
        validate_arrays(arrays, key)
    if (arrays["lwe"] > arrays["precip_lwe"] + 1e-12).any():
        raise ValueError("Snow exceeds precipitation")
    return arrays, meta


def validate_arrays(a, key):
    x, y, v = a["lons"], a["lats"], a[key]
    if (x.ndim != 1 or y.ndim != 1 or v.shape != (len(y), len(x))
            or min(len(x), len(y)) < 2 or not np.isfinite(x).all() or not np.isfinite(y).all() or not (np.diff(x) > 0).all()
            or not (np.diff(y) > 0).all() or not np.isfinite(v).all()
            or (v < 0).any() or x.min() < -180 or x.max() > 180
            or y.min() < -90 or y.max() > 90):
        raise ValueError("Invalid surface-phase grid")


def build_reference(directory, init, target, cycles, output):
    """All 15 years and exactly the same lagged-cycle window are required."""
    from cfsv2_native_reference import historical_cycle
    if not cycles or len(set(cycles)) != len(cycles) or cycles != sorted(cycles):
        raise ValueError("A unique ordered cycle window is required")
    annual, sources, axes = [], [], None
    target_days = calendar.monthrange(int(target[:4]), int(target[4:]))[1]
    for year in YEARS:
        historical_target = f"{year + int(target[:4])-int(init[:4])}{target[4:]}"
        days = calendar.monthrange(int(historical_target[:4]), int(historical_target[4:]))[1]
        values, weights = [], {}
        for cycle in cycles:
            for hc, weight in historical_cycle(cycle, year, init):
                weights[hc] = weights.get(hc, 0.) + weight/len(cycles)
        for hc, weight in weights.items():
            a, meta = load_month(directory, hc, historical_target)
            if axes is None:
                axes = a["lons"], a["lats"]
            elif not np.array_equal(axes[0], a["lons"]) or not np.array_equal(axes[1], a["lats"]):
                raise ValueError("Historical grids differ")
            values.append(a["lwe"] * weight / days * target_days)
            sources.append(dict(initialization=hc, target_month=historical_target,
                                year=year, cycle_weight=weight,
                                grid_sha256=meta["grid_sha256"]))
        annual.append(np.sum(values, axis=0))
    meta = dict(schema_version=1, method=METHOD, initialization=init, target_month=target,
                forecast_cycles=cycles, historical_years=YEARS, member=1,
                units="inches_water_equivalent", target_calendar_days=target_days,
                historical_cycles=len(sources), source_records=sources)
    stem = Path(output) / f"snowfall-reference-{init}-{target}"
    return save_bundle(stem, dict(lons=axes[0], lats=axes[1],
                                  annual=np.array(annual), reference=np.mean(annual, axis=0)), meta)


def load_reference(directory, init, target, cycles, member):
    from cfsv2_seasonal import CFSv2Error, Grid
    from cfsv2_native_reference import historical_cycle
    stem = Path(directory) / f"snowfall-reference-{init}-{target}"
    try:
        meta = json.loads(stem.with_suffix(".json").read_text())
        expected = dict(schema_version=1, method=METHOD, initialization=init,
                        target_month=target, forecast_cycles=list(cycles), member=member,
                        historical_years=YEARS, units="inches_water_equivalent",
                        target_calendar_days=calendar.monthrange(int(target[:4]), int(target[4:]))[1])
        for k, v in expected.items():
            if meta.get(k) != v:
                raise ValueError(f"Reference {k} mismatch")
        if member != 1 or not cycles or len(set(cycles)) != len(cycles):
            raise ValueError("Complete member-1 cycles required")
        planned = {}
        for year in YEARS:
            ht = f"{year+int(target[:4])-int(init[:4])}{target[4:]}"
            for cycle in cycles:
                for hc, w in historical_cycle(cycle, year, init):
                    planned[hc, ht] = planned.get((hc, ht), 0) + w/len(cycles)
        records = meta["source_records"]
        if len(records) != len(planned) or meta["historical_cycles"] != len(planned):
            raise ValueError("Incomplete historical cycle coverage")
        seen = set()
        for r in records:
            key = r["initialization"], r["target_month"]
            if key in seen or key not in planned or not np.isclose(r["cycle_weight"], planned[key]):
                raise ValueError("Historical cycle/weight mismatch")
            seen.add(key)
        if digest(stem.with_suffix(".npz")) != meta["grid_sha256"]:
            raise ValueError("Reference checksum mismatch")
        with np.load(stem.with_suffix(".npz"), allow_pickle=False) as z:
            a = {k: z[k].copy() for k in z.files}
        validate_arrays(a, "reference")
        if (a["annual"].shape != (len(YEARS), *a["reference"].shape)
                or not np.isfinite(a["annual"]).all() or (a["annual"] < 0).any()):
            raise ValueError("Historical annual grids incomplete")
        if not np.allclose(a["reference"], np.mean(a["annual"], axis=0), rtol=0, atol=1e-12):
            raise ValueError("Reference does not equal historical mean")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise CFSv2Error(f"Cannot use surface-phase reference: {exc}") from exc
    info = dict(source="CFSv2 surface-phase reconstructed operational forecasts",
                label="2011–2025 surface-phase operational reference", years="2011-2025",
                file=str(stem.with_suffix(".npz")), anchor_init=init, target_month=target,
                required=True, status="applied", method=METHOD, **{k: meta[k] for k in
                ("historical_years", "historical_cycles", "forecast_cycles", "grid_sha256", "target_calendar_days")},
                rolling_policy="reference_matched_to_each_forecast_cycle",
                observation_bias_adjustment=False)
    return Grid(a["lons"].tolist(), a["lats"].tolist(), a["reference"].tolist()), info


def decode(args, init, target, members, rolling_inits, *unused):
    from cfsv2_seasonal import Grid
    from cfsv2_native_snow import strict_mean, depth_grid
    import time
    if not rolling_inits or args.rolling_member != 1 or args.allow_partial_rolling:
        raise ValueError("Surface-phase reconstruction needs a complete member-1 rolling window")
    grids, sources = [], []
    for cycle in rolling_inits:
        a, meta = load_month(args.surface_phase_bundle_dir, cycle, target)
        grids.append(Grid(a["lons"].tolist(), a["lats"].tolist(), a["lwe"].tolist()))
        sources.append({k: meta[k] for k in ("initialization", "target_month", "method", "grid_sha256", "intervals")})
    lwe = strict_mean(grids, expected=len(rolling_inits))
    diagnostics = dict(method=METHOD, snow_to_liquid_ratio=10.,
                       temporal_assumption="trapezoid of six-hour endpoint CSNOW flags",
                       observation_bias_adjustment=False)
    result = lwe
    if args.product == "snowfall_accumulation":
        result = depth_grid(lwe)
        diagnostics["_native_lwe"] = lwe
    n = len(rolling_inits)
    return result, sources, n, n, f"{n}/{n}-cycle rolling mean", time.monotonic(), diagnostics
