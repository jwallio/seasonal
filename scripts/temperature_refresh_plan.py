"""Plan a style-only refresh from the product already published on Pages."""
import argparse
import json
import re
from pathlib import Path


def plan(manifest, workflow, product):
    candidates = []
    for run in manifest.get('runs', []):
        targets = [t for t in run.get('targets', [])
                   if t.get('image') and t.get('status') in ('rendered', 'partial')]
        if run.get('product') == product and targets:
            candidates.append((run, targets))
    if not candidates:
        raise ValueError(f'No published {product}; refusing to guess an initialization')
    run, targets = max(candidates, key=lambda item: item[0]['init_utc'])
    init = re.sub(r'[-:T]', '', run['init_utc'])[:10]
    if not re.fullmatch(r'\d{10}', init):
        raise ValueError('Invalid published initialization')
    if workflow == 'apcc.yml':
        request_month = run.get('request_target_month', '')
        window = run.get('requested_target_window', '')
        if not re.fullmatch(r'\d{6}', request_month) or not re.fullmatch(r'\d+,\d+,\d+', window):
            raise ValueError('APCC requires its published request month and native target window')
        return dict(product=product, init=request_month, target_window=window,
                    dataset=run['dataset'], resolution=str(run['resolution']))
    if workflow in ('seas5.yml', 'geos-s2s3.yml'):
        init = init[:6]
    monthly = {str(t['target_month']): t['lead_month'] for t in targets
               if isinstance(t.get('lead_month'), int)}
    leads = sorted(set(monthly.values()))
    if not leads:
        raise ValueError('No published monthly leads')
    windows = []
    for target in targets:
        period = str(target.get('target_month', ''))
        if '-' not in period:
            continue
        start, end = period.split('-', 1)
        window = sorted(lead for month, lead in monthly.items() if start <= month <= end)
        if (start not in monthly or end not in monthly or not window
                or window != list(range(window[0], window[-1] + 1))):
            raise ValueError('Published seasonal window has missing monthly components')
        windows.append(','.join(map(str, window)))
    if len(set(windows)) != 1:
        raise ValueError('Expected one complete published seasonal window')
    result = {'products' if workflow == 'nmme.yml' else 'product': product,
            'init': init, 'lead_months': ','.join(map(str, leads)),
            'seasonal_window': windows[0]}
    if workflow == 'cfsv2.yml':
        result.pop('init')  # This dispatcher selects the latest available rolling cycle.
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('workflow')
    parser.add_argument('product')
    args = parser.parse_args()
    print(json.dumps(plan(json.loads(args.manifest.read_text()), args.workflow, args.product)))
