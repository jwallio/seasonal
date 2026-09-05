"""Reproduce the reviewed frozen snapshot from retained local inputs, without downloads.

Offline only: requires the existing map environment (numpy, matplotlib, shapely,
pyshp). The publisher copies the checked assets and does not run this renderer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def build(root, output):
    import numpy as np
    import cfsv2_seasonal as cf
    import render_cfsv2_cwa_snapshot as cwa

    if output.exists():
        raise ValueError('Use a new snapshot directory; never overwrite a released run')
    native = root / 'native-inputs'
    provenance = json.loads((native / 'native-provenance.json').read_text(encoding='utf-8'))
    expected = {f'202608{day:02d}{hour:02d}' for day in range(30, 32) for hour in (0, 6, 12, 18)}
    expected |= {'2026082918'}
    expected |= {f'202609{day:02d}{hour:02d}' for day in range(1, 4) for hour in (0, 6, 12, 18)}
    expected |= {'2026090400', '2026090406', '2026090412'}
    assert len(expected) == 24
    for month in ('202701', '202702', '202703'):
        records = [s for s in provenance['sources'] if s['target_month'] == month]
        if len(records) != 24 or {s['initialization'] for s in records} != expected:
            raise ValueError('Snapshot requires the reviewed complete 24-cycle window')
    old = json.loads((root / 'v3-cwa-final/preview.json').read_text(encoding='utf-8'))
    if hashlib.sha256((root / 'cwa-inputs/w_16ap26.zip').read_bytes()).hexdigest() != old['boundary_sha256']:
        raise ValueError('CWA geometry provenance differs')
    ratios = json.loads(cwa.REGISTRY.read_text(encoding='utf-8'))['mean_ratios']
    if ratios != old['source_registry']['mean_ratios']:
        raise ValueError('CIPS registry differs from reviewed snapshot')
    geometry = cwa.read_cwas(root / 'cwa-inputs/w_16ap26.shp')
    states = json.loads((root / 'native-probe/us-states.geojson').read_text(encoding='utf-8'))
    lons, lats = np.meshgrid(np.linspace(-127, -65, 1241), np.linspace(23, 51, 561))
    display_ratios, _ = cwa.ratio_grid(lons, lats, geometry, ratios)
    validation = json.loads((root / 'v6-style-a-hatching/validation.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    files = {}
    for period in cwa.LABELS:
        grid = cf.read_grid_state(native / f'{period}-lwe.csv.gz')
        values = cwa.convert(cwa.sample(grid, lons, lats), display_ratios)
        array_hash = hashlib.sha256(np.nan_to_num(values, nan=-9999).tobytes()).hexdigest()
        if array_hash != validation['periods'][period]['display_values_sha256']:
            raise ValueError('Display values differ from approved map')
        cwa.render(values, lons, lats, geometry, ratios, states, period, output)
        for kind, source in [('total', root / 'v6-style-a-hatching'), ('lwe', native)]:
            name = f'{period}-{kind}.csv.gz'
            shutil.copyfile(source / name, output / name)
        actual = hashlib.sha256((output / f'{period}-total.csv.gz').read_bytes()).hexdigest()
        if actual != validation['periods'][period]['grid_sha256']:
            raise ValueError('Download differs from approved map')
        for name in (f'{period}-total.png', f'{period}-total.csv.gz', f'{period}-lwe.csv.gz'):
            files[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
        print(f'Rendered and verified {period}', flush=True)
    # Build a public provenance record, with no workstation paths or inferred freshness.
    record = {key: old[key] for key in ('method', 'source_registry', 'chart_sources',
        'boundary_source', 'boundary_valid_date', 'boundary_sha256', 'boundary_limitation',
        'supported_cwas', 'unsupported_cwas', 'display_method', 'numerical_download_method')}
    record.update(schema_version=1, status='experimental_frozen_snapshot',
        init_utc='2026-09-04T12:00:00Z', generated_utc=datetime.now(timezone.utc).isoformat(),
        automatic_refresh=False, native_reference_status='unavailable', departure_status='unavailable',
        native_provenance=provenance, files_sha256=files, periods=list(cwa.LABELS),
        units={'total': 'inches of estimated accumulated snowfall depth', 'lwe': 'inches of snowfall water equivalent'},
        presentation={'style': 'A', 'lighter_hatching': True, 'added_contours': False,
                      'added_smoothing': False, 'saturation_inches': 200},
        limitations=['Unadjusted model snowfall; excessive totals remain unresolved.',
            'CIPS ratios are pooled 1971-2000 event means, not monthly or storm-specific ratios.',
            'Missing ratios remain unavailable, never zero.',
            'Display sampling does not increase model resolution.'])
    (output / 'provenance.json').write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retained-inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.retained_inputs, args.output)
