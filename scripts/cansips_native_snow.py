"""Native CanSIPS snowfall via the two ECCC C3S components.

Uses provider-postprocessed anomalies (matching system hindcasts), not a
precipitation/temperature proxy. Comparison grids stay in inches of LWE, while
published numeric grids are converted to estimated snow depth at 10:1.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from c3s_seasonal import CDSArchive, month_seconds
from cfsv2_seasonal import Grid, CONUS_REGION, mean_grids, sum_grids, relative_path, write_grid_state
from snowfall_display import DISPLAY as SNOWFALL_DISPLAY, depth_departure

DATASET = 'seasonal-postprocessed-single-levels'
VARIABLE = 'snowfall_anomalous_rate_of_accumulation'
SOURCE_URL = 'https://cds.climate.copernicus.eu/datasets/' + DATASET
METHOD = 'eccc_native_snowfall_c3s_v1'
SYSTEMS = {'4': 'CanESM5.1p1bc', '5': 'GEM5.2-NEMO'}
BASELINE = 'C3S 1993–2016 system-matched hindcast climatology'
SPEC = {'name': 'snowfall_anomaly', 'variable': 'sf', 'cds_dataset': DATASET,
        'cds_variable': VARIABLE, 'region': CONUS_REGION}


class NotAvailable(ValueError):
    """The exact requested initialization/lead is not in the provider inventory."""


def available(rows, system, init, cds_lead):
    requested = {'originating_centre': 'eccc', 'system': system,
                 'variable': VARIABLE, 'product_type': 'ensemble_mean',
                 'year': init[:4], 'month': init[4:6], 'leadtime_month': str(cds_lead)}
    return any(all(value in row.get(key, []) for key, value in requested.items()) for row in rows)


def constraints():
    import requests
    response = requests.get('https://cds.climate.copernicus.eu/api/catalogue/v1/collections/' + DATASET,
                            timeout=(15, 45))
    response.raise_for_status()
    url = next(link['href'] for link in response.json()['links'] if link['rel'] == 'constraints')
    response = requests.get(url, timeout=(15, 45))
    response.raise_for_status()
    return response.json()


def rate_to_lwe(rate, target):
    """Metres water/second -> inches water for the target calendar month."""
    return np.asarray(rate, dtype=float) * month_seconds(target) / 0.0254


def decode(path, system, init, cds_lead, target):
    """Reject wrong fields, units, systems, times and multi-message responses."""
    import eccodes as ec
    with Path(path).open('rb') as stream:
        handle = ec.codes_grib_new_from_file(stream)
        if handle is None:
            raise ValueError('Empty native snowfall GRIB')
        try:
            meta = {key: ec.codes_get(handle, key) for key in
                    ('name', 'units', 'systemNumber', 'dataDate', 'forecastMonth', 'dataType')}
            name = str(meta['name']).lower()
            units = str(meta['units']).replace(' ', '').replace('**', '^')
            if 'snowfall' not in name or 'anomal' not in name:
                raise ValueError(f'Expected native snowfall anomaly, got {meta}')
            if units not in {'ms^-1', 'ms-1', 'mofwaterequivalents^-1'}:
                raise ValueError(f'Expected snowfall LWE rate in m/s, got {meta}')
            if (str(meta['systemNumber']) != system or str(meta['dataDate']) != init[:8]
                    or int(meta['forecastMonth']) != cds_lead or meta['dataType'] != 'em'):
                raise ValueError(f'Wrong native snowfall system/date/lead/statistic: {meta}')
            lat = ec.codes_get_array(handle, 'latitudes')
            lon = (ec.codes_get_array(handle, 'longitudes') + 180) % 360 - 180
            vals = ec.codes_get_values(handle)
            lats, ilat = np.unique(lat, return_inverse=True)
            lons, ilon = np.unique(lon, return_inverse=True)
            if len(vals) != len(lats) * len(lons):
                raise ValueError('Native snowfall must be a complete regular lat/lon grid')
            if len(np.unique(ilat * len(lons) + ilon)) != len(vals):
                raise ValueError('Duplicate native snowfall grid coordinates')
            if ec.codes_get(handle, 'numberOfMissing') or not np.isfinite(vals).all():
                raise ValueError('Missing values in native snowfall component')
            data = np.empty((len(lats), len(lons)))
            data[ilat, ilon] = rate_to_lwe(vals, target)
        finally:
            ec.codes_release(handle)
        extra = ec.codes_grib_new_from_file(stream)
        if extra is not None:
            ec.codes_release(extra)
            raise ValueError('Expected exactly one ensemble-mean snowfall field')
    return Grid(lons.tolist(), lats.tolist(), data.tolist()), meta


def blend(components):
    if set(components) != set(SYSTEMS):
        raise ValueError('Both ECCC native snowfall components are required')
    grids = list(components.values())
    if grids[0].lons != grids[1].lons or grids[0].lats != grids[1].lats:
        raise ValueError('Canadian component grids differ')
    if not all(np.isfinite(grid.values).all() for grid in grids):
        raise ValueError('Cannot silently blend incomplete Canadian components')
    return mean_grids(grids)  # 20 members per model, equal model weights.


class NativeSnowArchive:
    def __init__(self, cache_dir, *, render_only=False):
        self.archives = {s: CDSArchive(Path(cache_dir) / METHOD, 'eccc', s) for s in SYSTEMS}
        self.rows = None
        self.render_only = render_only

    def grid(self, init, lead):
        # Datamart lead 0 = init month; CDS forecastMonth 1 = init month.
        if lead not in range(6):
            raise ValueError('C3S native snowfall supports CanSIPS leads 0–5 (CDS months 1–6).')
        from cansips_seasonal import target_month
        target = target_month(init, lead)
        components, sources = {}, []
        for system, archive in self.archives.items():
            path = archive.retrieve_path(SPEC, init, lead + 1)
            if not path.is_file() or path.stat().st_size == 0:
                if self.render_only:
                    raise ValueError('Native snowfall cache missing; render-only cannot use legacy derived grids')
                if self.rows is None:
                    self.rows = constraints()
                if not available(self.rows, system, init, lead + 1):
                    raise NotAvailable(f'Native snowfall for {init[:6]} / {target} is not yet available '
                                       f'for ECCC system {system}. C3S normally releases on the 10th at 12 UTC.')
                path = archive.retrieve(SPEC, init, lead + 1)
            grid, metadata = decode(path, system, init, lead + 1, target)
            components[system] = grid
            sources.append({'system': system, 'model': SYSTEMS[system], 'members': 20,
                            'weight': 0.5, 'dataset': DATASET, 'variable': VARIABLE,
                            'init': init, 'cds_lead': lead + 1, 'metadata': metadata})
        return blend(components), {'method': METHOD, 'baseline': BASELINE, 'components': sources,
                                   'units': 'in LWE', 'quantity': 'snowfall LWE departure'}


def quality(grid, snow_depth=None):
    vals = np.asarray(grid.values, dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError('Incomplete native snowfall blend')
    depth = np.asarray((snow_depth if snow_depth is not None else grid).values, dtype=float)
    return {'status': 'passed', 'field': 'snowfall_depth_anomaly', 'units': 'in',
            'quantity': 'estimated snowfall depth departure', 'minimum': float(depth.min()),
            'maximum': float(depth.max()), 'finite_fraction': 1.0,
            'source_minimum': float(vals.min()), 'source_maximum': float(vals.max()),
            'display': {'quantity': 'estimated snowfall depth departure', 'units': 'in',
                        'snow_to_liquid_ratio': 10, 'minimum': -10, 'maximum': 10,
                        'clipped_fraction': float(np.mean(np.abs(depth) > 10))},
            'issues': [], 'validation_scope': 'data integrity and units; not observational forecast skill'}


def winter_leads(init):
    """December–March for the winter containing/following this initialization."""
    year, month = int(init[:4]), int(init[4:6])
    december_year = year - 1 if month <= 3 else year
    december_lead = (december_year - year) * 12 + 12 - month
    return list(range(december_lead, december_lead + 4))


def render_run(args, init, leads, seasonal_leads, cache_dir, output_dir, border_paths):
    import cansips_seasonal as can
    root = Path(__file__).resolve().parents[1]
    leads = winter_leads(init)
    windows = [('DJF', leads[:3]), ('JFM', leads[1:]), ('D+J', leads[:2])]
    run_id = f'cansips-{init}-snowfall_anomaly'
    entry = {'id': run_id, 'model': 'CanSIPS v3', 'product': 'snowfall_anomaly',
             'presentation': 'winter_djf_jfm_v1', 'method': METHOD, 'source': 'ECCC CanSIPS v3 / Copernicus C3S', 'source_url': SOURCE_URL,
             'init_utc': can.iso_utc(dt.datetime.strptime(init, '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)),
             'generated_utc': can.iso_utc(dt.datetime.now(dt.timezone.utc)),
             'field': 'snowfall_anomaly', 'units': 'in', 'raw_field': VARIABLE, 'raw_units': 'm s-1',
             'statistic': 'ensemble_mean', 'ensemble_members': 40,
             'aggregation': 'ensemble-mean period total departure; sum included monthly departures',
             'ensemble_scope': '20 CanESM5 + 20 GEM5.2-NEMO members; equal component weights',
             'climatology': {'source': BASELINE, 'years': '1993-2016', 'method': 'provider postprocessed; no second subtraction'},
             'display': {'quantity': 'estimated snowfall depth departure', 'units': 'in',
                         'snow_to_liquid_ratio': 10, 'scale_inches': [-10, 10],
                         'white_band_inches': [-1, 1], 'numeric_grid_quantity': 'estimated snowfall depth departure',
                         'numeric_grid_units': 'inches snow', 'canonical_grid_quantity': 'snowfall LWE departure'},
             'conversion': 'Native snowfall anomalous rate × target-month seconds ÷ 0.0254 to obtain LWE inches; '
                           'equal component mean; sum months; multiply signed departures by 10 for estimated snow-depth inches.', 'targets': []}
    archive = NativeSnowArchive(cache_dir, render_only=getattr(args, 'render_only', False))
    grids, monthly, failures = {}, {}, 0
    product = dict(can.PRODUCT_SPECS[can.PRODUCT_SNOWFALL_ANOMALY])
    product.update(source_label=entry['source'])

    def target_entry(target, lead):
        first, last = target.split('-') if '-' in target else (target, target)
        return {'id': f'{run_id}-{target}' if '-' in target else f'{run_id}-lead{lead:02d}',
                'target_month': target, 'lead_month': lead, 'field': 'snowfall_depth_anomaly', 'units': 'in',
                'valid_start_utc': can.target_period(first)[0], 'valid_end_utc': can.target_period(last)[1],
                'ensemble_members': 40, 'statistic': 'ensemble_mean',
                'baseline': {'source': BASELINE, 'method': METHOD}}

    def output(grid, t, lead, seasonal=False):
        target = t['target_month']
        snow_depth, display_spec = depth_departure(grid, product, can.SNOWFALL_ANOMALY_PALETTE)
        t['quality_control'] = quality(grid, snow_depth)
        t['display'] = dict(SNOWFALL_DISPLAY)
        t['ensemble_complete'] = True
        t['status'] = 'decoded'
        gridpath = output_dir / init[:8] / f'cansips_native_snow_{target}.csv.gz'
        write_grid_state(snow_depth, gridpath)
        t['numeric_grid'] = relative_path(gridpath, root)
        lwe_path = output_dir / init[:8] / f'cansips_native_snow_{target}.lwe.csv.gz'
        write_grid_state(grid, lwe_path)
        t['native_lwe_grid'] = relative_path(lwe_path, root)
        if not args.decode_only:
            imagepath = output_dir / init[:8] / f'cansips_native_snowfalla_winter_{target}.jpg'
            can.render_standalone(snow_depth, init, target[:6], lead, list(range(1, 41)), imagepath,
                anomaly=True, baseline_label=BASELINE, border_paths=border_paths,
                ensemble_label='40-member mean' + (f"  •  {len(t['monthly_leads'])}-month total departure" if seasonal else ''), product_spec=display_spec,
                seasonal=seasonal,
                period_label=can.seasonal_period_label(*target.split('-')) if seasonal else None)
            t.update(image=relative_path(imagepath, root), status='rendered')

    for lead in leads:
        target = can.target_month(init, lead)
        t = target_entry(target, lead)
        try:
            if lead not in range(6):
                raise NotAvailable(f'{can.target_month(init, lead)} is outside this initialization’s native snowfall range. '
                                   'Use an initialization covering this month; months from different runs are not combined.')
            grid, source = archive.grid(init, lead)
            t['source_files'] = source['components']
            output(grid, t, lead)
            grids[lead] = grid
        except NotAvailable as exc:
            t.update(status='pending', error=str(exc))
        except Exception as exc:
            failures += 1
            t.update(status='failed', error=str(exc))
        monthly[lead] = t
        entry['targets'].append(t)
    for season, seasonal_leads in windows:
        target = f'{can.target_month(init, seasonal_leads[0])}-{can.target_month(init, seasonal_leads[-1])}'
        t = target_entry(target, f'{seasonal_leads[0]}–{seasonal_leads[-1]}')
        t['monthly_leads'] = seasonal_leads
        t['season'] = season
        t['aggregation'] = f'ensemble-mean {len(seasonal_leads)}-month total departure'
        if season == 'D+J':
            t['label'] = f'D+J {target[:4]}–{target[-4:-2]}'
        if all(l in grids for l in seasonal_leads):
            try:
                output(sum_grids([grids[l] for l in seasonal_leads]), t, t['lead_month'], True)
            except Exception as exc:
                failures += 1
                t.update(status='failed', error=str(exc))
        else:
            missing = [l for l in seasonal_leads if l not in grids]
            labels = ', '.join(dt.datetime.strptime(can.target_month(init, l), '%Y%m').strftime('%b %Y') for l in missing)
            outside = any(l not in range(6) for l in missing)
            t.update(status='failed' if any(monthly[l]['status'] == 'failed' for l in missing) else 'pending',
                     missing_months=[can.target_month(init, l) for l in missing],
                     error=f'{season} unavailable: missing {labels}. ' +
                           ('This initialization does not cover the complete period; use a run covering every included month.' if outside else
                            'Awaiting native snowfall from both Canadian models.'))
        entry['targets'].append(t)
    entry['targets'] = entry['targets'][-len(windows):] + entry['targets'][:-len(windows)]
    states = {t['status'] for t in entry['targets']}
    entry['status'] = ('failed' if failures else 'pending' if states == {'pending'}
                       else 'partial' if 'pending' in states else 'decoded' if args.decode_only else 'rendered')
    return entry, failures


if __name__ == '__main__':
    # Small live-data integration check, using an inventory-confirmed available month.
    archive = NativeSnowArchive(Path('.cache/cansips'))
    archive.rows = constraints()
    dates = sorted({y + m for r in archive.rows for y in r.get('year', []) for m in r.get('month', [])}, reverse=True)
    init = next(d + '0100' for d in dates if all(available(archive.rows, s, d + '0100', 6) for s in SYSTEMS))
    grid, provenance = archive.grid(init, 5)
    print(json.dumps({'init': init, 'source': provenance, 'quality_control': quality(grid)}, indent=2))
