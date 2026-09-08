"""Migrate retained native snowfall accumulations using published numerical inputs."""
import argparse
import json
from pathlib import Path
import cfsv2_seasonal as cf
import cfsv2_native_snow as native


def asset(root, value):
    relative = Path(value.removeprefix('public/seasonal/').removeprefix('seasonal/'))
    result = (root / relative).resolve()
    result.relative_to(root.resolve())
    return result


def refresh(root):
    # The main numerical download follows the image's snowfall-depth units;
    # the separately exposed LWE file retains the canonical blending quantity.
    for marker in root.rglob('*.snow.json'):
        display = json.loads(marker.read_text())
        display['canonical_grid_quantity'] = 'snowfall liquid-water-equivalent departure'
        display['numeric_grid_quantity'] = 'estimated snowfall depth departure'
        marker.write_text(json.dumps(display))
    manifest = root / 'cfsv2_manifest.json'
    if not manifest.exists():
        return
    document = json.loads(manifest.read_text())
    count = 0
    for run in document.get('runs', []):
        if run.get('raw_field') == 'APCP + CSNOW:surface':
            continue  # Preserve validated surface-phase values and labels.
        if run.get('product') != 'snowfall_accumulation':
            continue
        if (run.get('display') or {}).get('snow_to_liquid_ratio') == 10:
            for target in run.get('targets', []):
                if target.get('image'):
                    target['source_warning'] = 'Unadjusted native snowfall at fixed 10:1; native departures unavailable.'
            continue
        init = run['init_utc'].replace('-', '').replace(':', '').replace('T', '')[:10]
        for target in run.get('targets', []):
            if not target.get('image'):
                continue
            if not target.get('native_lwe_grid') or not target.get('numeric_grid'):
                raise ValueError('Cannot convert a native accumulation without its retained snowfall input')
            lwe = cf.read_grid_state(asset(root, target['native_lwe_grid']))
            depth = native.depth_grid(lwe)
            cf.write_grid_state(depth, asset(root, target['numeric_grid']))
            period = target['target_month']
            native.render(lwe, init, period[:6], target.get('lead_month', ''),
                          asset(root, target['image']), '-' in period,
                          target.get('label') or target.get('period_label') or (cf.seasonal_period_label(*period.split('-')) if '-' in period else ''),
                          f"{target.get('ensemble_members') or run.get('ensemble_members') or 24}-cycle mean")
            target['quality_control'] = cf.grid_quality_control('snowfall_accumulation', depth.values, units='in', field='snowfall_accumulation', seasonal='-' in period)
            cf.require_quality_control(target['quality_control'], ValueError)
            target['source_warning'] = 'Unadjusted native snowfall at fixed 10:1; native departures unavailable.'
            target['derivation'] = {'method': 'native_SRWEQ_times_fixed_10_to_1_v1',
                                    'snow_to_liquid_ratio': 10, 'native_departure_status': 'unavailable'}
            target['baseline'] = {'status': 'not_applicable', 'reason': 'Native snowfall accumulation at 10:1; no native departure reference'}
            count += 1
        run['display'] = {'quantity': 'estimated accumulated snowfall depth', 'units': 'in',
                          'snow_to_liquid_ratio': 10, 'white_below_inches': 1}
        run['source_warning'] = 'Unadjusted native snowfall at fixed 10:1; native snowfall departures unavailable.'
        run['baseline'] = {'status': 'not_applicable', 'reason': run['source_warning']}
    # The matching native departure now exists for repaired runs. Do not leave
    # the old 'native departures unavailable' note on their accumulation cards.
    native_departures = {}
    for run in document.get('runs', []):
        if run.get('product') == 'snowfall_anomaly':
            for target in run.get('targets', []):
                if (target.get('baseline') or {}).get('method') == 'native_srweq_operational_2011_2025_v1':
                    native_departures[run.get('init_utc'), target.get('target_month')] = target
    for run in document.get('runs', []):
        if run.get('raw_field') == 'APCP + CSNOW:surface':
            continue  # Preserve validated surface-phase values and labels.
        if run.get('product') != 'snowfall_accumulation':
            continue
        paired = False
        for target in run.get('targets', []):
            if (run.get('init_utc'), target.get('target_month')) in native_departures:
                target['source_warning'] = 'Native model snowfall at fixed 10:1. Paired departures use a matched 2011–2025 native operational reference.'
                target.setdefault('derivation', {})['native_departure_status'] = 'available'
                paired = True
        if paired:
            run['source_warning'] = 'Native model snowfall at fixed 10:1; matching native departure maps are available.'
    manifest.write_text(json.dumps(document, indent=2))
    print(f'Refreshed {count} retained native snowfall accumulation maps at 10:1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-root', type=Path, required=True)
    refresh(parser.parse_args().site_root)
