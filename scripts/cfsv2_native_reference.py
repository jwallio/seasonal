"""Native SRWEQ reference from archived operational seasonal forecasts.

The reference is explicitly 2011-2025 operational forecasts, not reforecasts
or observed normals. Only complete years shared by every requested month
are retained. Raw snowfall messages and their provenance are cached.
"""
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import os
import io
import json
from pathlib import Path
import re
import time
import threading

import eccodes as ec
import numpy as np
import requests

METHOD = 'native_srweq_operational_2011_2025_v1'
YEARS = list(range(2011, 2026))
MIN_YEARS = 12
NCEI = 'https://www.ncei.noaa.gov/oa/prod-cfs-operational-forecast/monthly-means/'
AWS = 'https://noaa-cfs-pds.s3.amazonaws.com/'
HTTP = threading.local()

def session():
    if not hasattr(HTTP, 'session'):
        HTTP.session = requests.Session()
    return HTTP.session


def historical_cycle(cycle, year, anchor):
    moment = datetime.strptime(cycle, '%Y%m%d%H')
    mapped = year + moment.year - int(anchor[:4])
    if moment.month == 2 and moment.day == 29 and not calendar.isleap(mapped):
        return [(f'{mapped}0228{moment:%H}', .5), (f'{mapped}0301{moment:%H}', .5)]
    return [(moment.replace(year=mapped).strftime('%Y%m%d%H'), 1.)]


def urls(cycle, target):
    filename = f'flxf.01.{cycle}.{target}.avrg.grib.grb2'
    ncei = NCEI + f'{cycle[:4]}/{cycle[:6]}/{cycle[:8]}/{cycle}/{filename}'
    aws = AWS + f'cfs.{cycle[:8]}/{cycle[8:]}/monthly_grib_01/{filename}'
    archives = [aws, ncei] if int(cycle[:4]) >= 2019 else [ncei]
    if cycle == os.environ.get('CFSV2_LIVE_INIT'):
        live = f'https://nomads.ncep.noaa.gov/pub/data/nccf/com/cfs/prod/cfs.{cycle[:8]}/{cycle[8:]}/monthly_grib_01/{filename}'
        return [live] + archives
    return archives


def snowfall_message(data):
    found = []
    pos = 0
    while (start := data.find(b'GRIB', pos)) >= 0:
        pos = start + 4
        if len(data) < start + 16 or data[start + 7] != 2:
            continue
        length = int.from_bytes(data[start + 8:start + 16], 'big')
        if length < 20 or start + length > len(data) or data[start + length - 4:start + length] != b'7777':
            continue
        message = data[start:start + length]
        h = ec.codes_new_from_message(message)
        try:
            if all(ec.codes_get(h, k) == v for k, v in
                   [('discipline', 0), ('parameterCategory', 1), ('parameterNumber', 12)]):
                found.append(message)
        finally:
            ec.codes_release(h)
    if len(found) != 1:
        raise ValueError(f'Expected exactly one native snowfall message; found {len(found)}')
    return found[0]


def decode(message, cycle, target):
    h = ec.codes_new_from_message(message)
    try:
        lead = (int(target[:4]) - int(cycle[:4])) * 12 + int(target[4:]) - int(cycle[4:6])
        checks = dict(discipline=0, parameterCategory=1, parameterNumber=12,
                      typeOfLevel='surface', units='kg m**-2 s**-1', stepType='avg',
                      dataDate=int(cycle[:8]), dataTime=int(cycle[8:]) * 100,
                      indicatorOfUnitOfTimeRange=3, forecastTime=lead,
                      indicatorOfUnitForTimeRange=3, lengthOfTimeRange=1,
                      productDefinitionTemplateNumber=8,
                      numberOfMissing=0)
        for key, value in checks.items():
            if ec.codes_get(h, key) != value:
                raise ValueError(f'Native snowfall {cycle}/{target}: incorrect {key}')
        values = ec.codes_get_values(h)
        lat = ec.codes_get_array(h, 'latitudes')
        lon = (ec.codes_get_array(h, 'longitudes') + 180) % 360 - 180
        xs, ys = np.unique(lon), np.unique(lat)
        if len(values) != len(xs) * len(ys) or not np.isfinite(values).all() or values.min() < 0:
            raise ValueError('Invalid native snowfall grid')
        grid = np.empty((len(ys), len(xs)))
        grid[np.searchsorted(ys, lat), np.searchsorted(xs, lon)] = values * 86400 / 25.4
        return dict(lons=xs, lats=ys, inches_lwe_per_day=grid)
    finally:
        ec.codes_release(h)


def sample(cycle, target, cache):
    stem = Path(cache) / 'native-v1' / f'{cycle}-{target}'
    stem.parent.mkdir(parents=True, exist_ok=True)
    raw, sidecar = stem.with_suffix('.grb2'), stem.with_suffix('.json')
    if raw.exists() and sidecar.exists():
        message, meta = raw.read_bytes(), json.loads(sidecar.read_text())
        if meta.get('sha256') != hashlib.sha256(message).hexdigest():
            raise ValueError('Native snowfall cache checksum mismatch')
        return {**decode(message, cycle, target), 'source': meta}
    errors = []
    for url in urls(cycle, target):
        for attempt in range(3):
            try:
                # Snowfall is near the file end in this archive. Validate the
                # extracted message; if absent, inspect the bounded full file.
                r = session().get(url, headers={'Range': 'bytes=-250000'}, timeout=(10, 40))
                r.raise_for_status()
                if r.status_code != 206 or not re.fullmatch(r'bytes \d+-\d+/\d+', r.headers.get('Content-Range', '')):
                    raise ValueError('Archive did not return a validated byte range')
                if len(r.content) > 250000:
                    raise ValueError('Archive range exceeds requested size')
                try:
                    message = snowfall_message(r.content)
                except ValueError:
                    with session().get(url, stream=True, timeout=(10, 40)) as full:
                        full.raise_for_status()
                        data = bytearray()
                        for chunk in full.iter_content(65536):
                            data.extend(chunk)
                            if len(data) > 12_000_000:
                                raise ValueError('Native source exceeds bounded file size')
                    message = snowfall_message(bytes(data))
                result = decode(message, cycle, target)
                meta = dict(url=url, initialization=cycle, target_month=target,
                            field='SRWEQ:surface', units='kg m-2 s-1',
                            sha256=hashlib.sha256(message).hexdigest(), method=METHOD)
                raw.with_suffix('.part').write_bytes(message)
                raw.with_suffix('.part').replace(raw)
                sidecar.with_suffix('.json.part').write_text(json.dumps(meta))
                sidecar.with_suffix('.json.part').replace(sidecar)
                return {**result, 'source': meta}
            except requests.HTTPError as exc:
                if exc.response.status_code == 404:
                    errors.append(url)
                    break
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    raise FileNotFoundError(json.dumps(errors))


def build(init, targets, cycles, cache, output, workers=4):
    tasks, plans = {}, {}
    for year in YEARS:
        for target in targets:
            ht = f'{year + int(target[:4]) - int(init[:4])}{target[4:]}'
            weights = {}
            for cycle in cycles:
                for hc, weight in historical_cycle(cycle, year, init):
                    weights[(hc, ht)] = weights.get((hc, ht), 0.) + weight / len(cycles)
            plans[year, target] = weights
            tasks.update(dict.fromkeys(weights))
    results, missing = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(sample, *key, cache): key for key in tasks}
        for count, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            try:
                results[key] = future.result()
            except FileNotFoundError as exc:
                missing[key] = str(exc)
            if count % 48 == 0 or count == len(tasks):
                print(f'Native reference: {count}/{len(tasks)} source grids checked', flush=True)
    excluded = []
    years = []
    for year in YEARS:
        absent = sorted({key for target in targets for key in plans[year, target] if key in missing})
        if absent:
            excluded.append(dict(year=year, reason='source_http_404', sources=[missing[k] for k in absent]))
        else:
            years.append(year)
    if len(years) < MIN_YEARS:
        raise ValueError(f'Only {len(years)} complete native years; need {MIN_YEARS}. Excluded: {excluded}')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for target in targets:
        annual, sources = [], []
        first = next(iter(results.values()))
        for year in years:
            weighted = []
            for key, weight in plans[year, target].items():
                s = results[key]
                if not np.array_equal(s['lons'], first['lons']) or not np.array_equal(s['lats'], first['lats']):
                    raise ValueError('Native historical grids differ')
                weighted.append(s['inches_lwe_per_day'] * weight)
                sources.append({**s['source'], 'year_weight': 1 / len(years), 'cycle_weight': weight})
            days = calendar.monthrange(int(target[:4]), int(target[4:]))[1]
            annual.append(np.sum(weighted, axis=0) * days)
        stem = output / f'snowfall-reference-{init}-{target}'
        np.savez_compressed(stem.with_suffix('.npz'), lons=first['lons'], lats=first['lats'],
                            reference=np.mean(annual, axis=0), annual=np.array(annual))
        meta = dict(schema_version=1, method=METHOD, initialization=init, target_month=target,
                    forecast_cycles=cycles, member=1, units='inches_water_equivalent',
                    historical_years=years, candidate_years=YEARS, excluded_years=excluded,
                    historical_cycles=len(sources), source_records=sources,
                    target_calendar_days=days, grid_sha256=hashlib.sha256(stem.with_suffix('.npz').read_bytes()).hexdigest())
        stem.with_suffix('.json').write_text(json.dumps(meta, indent=2))
        load_reference(output, init, target, cycles, 1)
        print(f'Built native reference {target}: {len(years)} complete years, {len(sources)} forecasts', flush=True)


def load_reference(directory, init, target, cycles, member):
    from cfsv2_seasonal import Grid, CFSv2Error
    stem = Path(directory) / f'snowfall-reference-{init}-{target}'
    try:
        meta = json.loads(stem.with_suffix('.json').read_text())
        for k, v in dict(schema_version=1, method=METHOD, initialization=init, target_month=target,
                         forecast_cycles=list(cycles), member=member, units='inches_water_equivalent',
                         candidate_years=YEARS).items():
            if meta.get(k) != v:
                raise ValueError(f'Native reference {k} mismatch')
        years = meta['historical_years']
        if years != sorted(set(years)) or not set(years).issubset(YEARS) or len(years) < MIN_YEARS:
            raise ValueError('Insufficient or invalid native reference years')
        if meta.get('target_calendar_days') != calendar.monthrange(int(target[:4]), int(target[4:]))[1]:
            raise ValueError('Native reference target calendar mismatch')
        excluded = meta.get('excluded_years', [])
        if (sorted(e['year'] for e in excluded) != sorted(set(YEARS) - set(years))
                or any(e.get('reason') != 'source_http_404' or not e.get('sources') for e in excluded)):
            raise ValueError('Native reference exclusions are inconsistent')
        expected = {}
        for year in years:
            ht = f'{year + int(target[:4]) - int(init[:4])}{target[4:]}'
            for cycle in cycles:
                for hc, weight in historical_cycle(cycle, year, init):
                    expected[hc, ht] = expected.get((hc, ht), 0.) + weight / len(cycles)
        records = meta.get('source_records', [])
        if len(records) != len(expected) or meta.get('historical_cycles') != len(expected):
            raise ValueError('Native reference source count mismatch')
        seen = set()
        for record in records:
            key = record['initialization'], record['target_month']
            if (key in seen or key not in expected or record.get('field') != 'SRWEQ:surface'
                    or not np.isclose(record['cycle_weight'], expected[key])
                    or not np.isclose(record['year_weight'], 1 / len(years))):
                raise ValueError('Native reference source or weight mismatch')
            seen.add(key)
        if hashlib.sha256(stem.with_suffix('.npz').read_bytes()).hexdigest() != meta['grid_sha256']:
            raise ValueError('Native reference checksum mismatch')
        with np.load(stem.with_suffix('.npz'), allow_pickle=False) as z:
            xs, ys, values, annual = (z[k].copy() for k in ['lons', 'lats', 'reference', 'annual'])
        if (values.shape != (len(ys), len(xs)) or annual.shape != (len(years), len(ys), len(xs))
                or not np.isfinite(annual).all() or annual.min() < 0
                or not np.array_equal(values, annual.mean(axis=0))
                or not (np.diff(xs) > 0).all() or not (np.diff(ys) > 0).all()):
            raise ValueError('Invalid native reference grid or annual mean')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CFSv2Error(f'Cannot load native snowfall reference: {exc}') from exc
    label = f'{years[0]}–{years[-1]} native operational reference ({len(years)} years)'
    return Grid(xs.tolist(), ys.tolist(), values.tolist()), {
        'source': 'NOAA archived CFSv2 operational forecasts; native SRWEQ',
        'label': label, 'years': f'{years[0]}-{years[-1]}', 'method': METHOD,
        'status': 'applied', 'required': True, 'file': str(stem.with_suffix('.npz')),
        'grid_sha256': meta['grid_sha256'], 'historical_years': years,
        'historical_cycles': meta['historical_cycles'], 'excluded_years': meta['excluded_years'],
        'target_month': target, 'forecast_cycles': list(cycles), 'anchor_init': init,
        'rolling_policy': 'reference_matched_to_each_forecast_cycle',
        'observation_bias_adjustment': False,
    }


def main():
    import cfsv2_seasonal as cf
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--init', required=True)
    p.add_argument('--lead-months', required=True)
    p.add_argument('--seasonal-window', default='')
    p.add_argument('--rolling-days', type=int, default=6)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', type=int, choices=range(1, 9), default=8)
    a = p.parse_args()
    init = cf.parse_init(a.init)
    if not 1 <= a.rolling_days <= 6:
        raise ValueError('Native reference requires 1-6 rolling days')
    leads = set(cf.parse_int_list(a.lead_months, 'leads', 1, 9))
    for window in a.seasonal_window.split(';'):
        if window:
            leads.update(cf.parse_int_list(window, 'seasonal leads', 1, 9))
    build(init, [cf.target_month(init, lead) for lead in sorted(leads)],
          cf.rolling_cycle_inits(init, a.rolling_days * 4), a.cache, a.output, a.workers)


if __name__ == '__main__':
    main()
