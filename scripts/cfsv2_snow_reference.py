"""Load an explicitly supplied, model-only snowfall departure reference.

This adjusts the reference calculation, never native snowfall accumulation.
There is deliberately no nearest-date or alternate-month fallback.
"""
from datetime import datetime, timedelta
import calendar
import hashlib
import json
from pathlib import Path

import numpy as np

METHOD = "derive_each_forecast_then_same_hour_interpolate_v1"
NORMALIZED_METHOD = "derive_each_forecast_daily_rate_then_same_hour_interpolate_v2"
LABEL = "CFS reforecasts · snowfall derived before averaging"


def reference_years(cycles):
    """Use complete brackets within the documented 1982–2010 archive."""
    anchor_year = int(cycles[-1][:4])
    years = []
    for year in range(1982, 2011):
        moments = []
        for cycle in cycles:
            moment = datetime.strptime(cycle, "%Y%m%d%H")
            try:
                moments.append(moment.replace(year=year + moment.year - anchor_year))
            except ValueError:
                moments.append(moment.replace(year=year + moment.year - anchor_year, day=28) + timedelta(hours=12))
        if (min(moments) - timedelta(days=5)).year >= 1982 and (max(moments) + timedelta(days=5)).year <= 2010:
            years.append(year)
    return years


def match_forecast_grid(reference, forecast):
    """Allow decoder coordinate rounding, never spatial interpolation."""
    from cfsv2_seasonal import CFSv2Error, Grid
    # wgrib2 -csv writes coordinates with C %g (six significant digits).
    # Across [-180, 180] longitude / [-90, 90] latitude, rounding can reach
    # 0.0005 / 0.00005 degrees. Include 1e-6 for decoder/CSV normalization.
    # Keep rtol=0: these are absolute encoding tolerances, not grid spacing.
    if (np.shape(reference.values) != np.shape(forecast.values)
            or np.shape(reference.lons) != np.shape(forecast.lons)
            or np.shape(reference.lats) != np.shape(forecast.lats)
            or not np.allclose(reference.lons, forecast.lons, rtol=0, atol=0.000501)
            or not np.allclose(reference.lats, forecast.lats, rtol=0, atol=0.000051)):
        raise CFSv2Error('Snowfall reference grid does not match the forecast grid')
    return Grid(forecast.lons[:], forecast.lats[:], reference.values)


def load_reference(directory, init, target, cycles, member):
    from cfsv2_seasonal import CFSv2Error, Grid

    stem = Path(directory) / f"snowfall-reference-{init}-{target}"
    try:
        meta = json.loads(stem.with_suffix('.json').read_text(encoding='utf-8'))
        method = meta.get('method')
        if method not in (METHOD, NORMALIZED_METHOD):
            raise ValueError('Unrecognized snowfall reference method')
        expected = dict(schema_version=1, initialization=init,
                        target_month=target, forecast_cycles=list(cycles),
                        member=member, units='inches_water_equivalent',
                        historical_years=(list(range(1982, 2011)) if method == METHOD else meta.get('historical_years')))
        for key, value in expected.items():
            if meta.get(key) != value:
                raise ValueError(f"Reference {key} does not match the requested forecast")
        if method == METHOD and meta.get('historical_cycles') != 348:
            raise ValueError('Reviewed v1 reference requires 348 forecasts')
        if method == NORMALIZED_METHOD:
            candidates = reference_years(cycles)
            years = meta.get('historical_years', [])
            excluded = meta.get('excluded_years', [])
            if (meta.get('candidate_years') != candidates or len(years) < 25
                    or years != sorted(set(years)) or not set(years).issubset(candidates)
                    or sorted(e['year'] for e in excluded) != sorted(set(candidates) - set(years))
                    or any(e.get('reason') != 'source_http_404' or not e.get('urls') for e in excluded)):
                raise ValueError('Incomplete or inconsistent historical year coverage')
            if meta.get('target_calendar_days') != calendar.monthrange(int(target[:4]), int(target[4:]))[1]:
                raise ValueError('Reference target calendar days mismatch')
            plans = meta.get('annual_weights', [])
            if [p.get('year') for p in plans] != expected['historical_years']:
                raise ValueError('Reference annual plans do not match historical years')
            count = 0
            for plan in plans:
                weights = list(plan['weights'].values())
                if not weights or not all(np.isfinite(w) and 0 <= w <= 1 for w in weights) or not np.isclose(sum(weights), 1.):
                    raise ValueError('Invalid historical cycle weights')
                count += len(weights)
            if count != meta.get('historical_cycles'):
                raise ValueError('Historical cycle count does not match the annual plans')
        if not cycles or len(set(cycles)) != len(cycles):
            raise ValueError('Reference requires an explicit, unique cycle window')
        path = stem.with_suffix('.npz')
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta['grid_sha256']:
            raise ValueError('Reference grid checksum mismatch')
        with np.load(path, allow_pickle=False) as data:
            lons, lats, values = (data[k].copy() for k in ('lons', 'lats', 'reference'))
        if (lons.ndim != 1 or lats.ndim != 1 or min(len(lons), len(lats)) < 2
                or values.shape != (len(lats), len(lons))
                or not all(np.isfinite(a).all() for a in (lons, lats, values))
                or not (np.diff(lons) > 0).all() or not (np.diff(lats) > 0).all()
                or lons.min() < -180 or lons.max() > 180
                or lats.min() < -90 or lats.max() > 90 or values.min() < 0):
            raise ValueError('Invalid snowfall reference grid')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CFSv2Error(f'Cannot use snowfall reference {stem.name}: {exc}') from exc
    years = f"{meta['historical_years'][0]}-{meta['historical_years'][-1]}"
    return Grid(lons.tolist(), lats.tolist(), values.tolist()), {
        'source': 'NCEI CFS reforecasts; model-only snowfall reference',
        'label': f'{years} CFS reforecasts ({len(meta["historical_years"])} years)', 'years': years, 'required': True, 'status': 'applied',
        'file': str(path), 'grid_sha256': meta['grid_sha256'],
        'method': method, 'target_calendar_days': meta.get('target_calendar_days'), 'rolling_policy': 'reference_matched_to_each_forecast_cycle',
        'anchor_init': init, 'target_month': target, 'forecast_cycles': list(cycles),
        'historical_cycles': meta['historical_cycles'], 'historical_years': meta['historical_years'],
        'excluded_years': meta.get('excluded_years', []),
        'reference_interpolation': ('same hour; five-day brackets, six only across leap day; no extrapolation'
                                    if method == NORMALIZED_METHOD else 'same hour; bracket gap at most five days; no extrapolation'),
        'observation_bias_adjustment': False,
    }


def validate_options(args, product, init, targets, repo_root):
    """Preflight every requested month before any downloads or rendering."""
    from cfsv2_seasonal import CFSv2Error, rolling_cycle_inits, resolve_repo_path

    directory = getattr(args, 'snowfall_reference_dir', None)
    if not directory:
        return
    if (product != 'snowfall_anomaly' or args.absolute or args.decode_only
            or not 1 <= args.rolling_days <= 6 or args.rolling_member != 1
            or args.allow_partial_rolling or args.allow_stale_calibration
            or args.baseline_label or args.baseline_years):
        raise CFSv2Error('An explicit snowfall reference requires snowfall_anomaly, '
                         'a complete 1-6 day member-1 window, and its own reference labels')
    directory = resolve_repo_path(directory, repo_root)
    loader = load_reference
    if getattr(args, 'surface_phase_bundle_dir', None):
        from cfsv2_surface_phase import load_reference as loader
    elif getattr(args, 'native_snowfall_departure', False):
        from cfsv2_native_reference import load_reference as loader
    cycles = rolling_cycle_inits(init, args.rolling_days * 4)
    if getattr(args, "surface_phase_bundle_dir", None):
        from cfsv2_surface_phase import selected_cycles
        cycles = selected_cycles(cycles, getattr(args, "surface_phase_exclude_cycles", ""))
    for target in targets:
        loader(directory, init, target, cycles, 1)
