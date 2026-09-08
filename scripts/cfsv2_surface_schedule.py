"""Select ready operational snowfall inputs and verify output before publication."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import requests
import cfsv2_seasonal as cf
import cfsv2_surface_phase as phase
from cfsv2_native_reference import historical_cycle

# Verified archive gaps; exclude the corresponding cycle on BOTH sides for all months.
KNOWN_GAPS = {('2019083118', '202003'), ('2019090418', '202002'), ('2018090200', '201812')}

def exclusions(init, cycles, targets):
    excluded = []
    for cycle in cycles:
        if any((hc, f'{year+int(t[:4])-int(init[:4])}{t[4:]}') in KNOWN_GAPS
               for year in phase.YEARS for t in targets
               for hc, _ in historical_cycle(cycle, year, init)):
            excluded.append(cycle)
    return excluded

def listed_complete(init, targets, get=requests.get):
    url = f'{cf.NOMADS_ROOT.rstrip("/")}/cfs.{init[:8]}/{init[8:]}/6hrly_grib_01/'
    response = get(url, timeout=(10, 40))
    if response.status_code == 404:
        return False
    response.raise_for_status()
    available = set(re.findall(r'href="([^"/]+\.grb2)"', response.text))
    needed = {f'pgbf{valid}.01.{init}.grb2' for t in targets for valid in phase.endpoints(t)}
    # Listing is a readiness screen. Acquisition still validates every field and interval.
    return needed.issubset(available)

def choose(candidates, ready=listed_complete):
    for init in candidates:
        try:
            leads, windows = cf.default_winter_snowfall_windows(init)
        except cf.CFSv2Error:
            continue
        targets = [cf.target_month(init, lead) for lead in leads]
        if not ready(init, targets):
            print(f'{init}: winter six-hour file listing incomplete', flush=True)
            continue
        cycles = cf.rolling_cycle_inits(init, 24)
        excluded = exclusions(init, cycles, targets)
        return dict(init=init, targets=targets, leads=leads, windows=windows,
                    excluded=excluded, cycles=phase.selected_cycles(cycles, excluded),
                    method=phase.METHOD, years=phase.YEARS)
    return None

def select():
    candidates = cf.filter_mature_cycle_inits(cf.listed_cycle_inits(), 660)[:12]
    plan = choose(candidates)
    output = Path(os.environ.get('GITHUB_OUTPUT', '/tmp/cfsv2-select-output'))
    if plan is None:
        with output.open('a') as f: f.write('ready=false\n')
        print('No complete winter forecast listed; retaining published corrected maps.')
        return
    Path('surface-plan.json').write_text(json.dumps(plan, indent=2))
    with output.open('a') as f:
        f.write('ready=true\n')
        for key, value in dict(init=plan['init'], targets=','.join(plan['targets']),
                               excluded=','.join(plan['excluded']), cycles=','.join(plan['cycles']),
                               leads=','.join(map(str,plan['leads'])),
                               windows=';'.join(','.join(map(str,w)) for w in plan['windows']),
                               years=json.dumps(phase.YEARS+[int(plan['init'][:4])]),
                               cache_period=plan['init'][4:6]+'-'+ '-'.join(plan['targets'])).items():
            f.write(f'{key}={value}\n')

def verify(manifest, plan_path):
    plan=json.loads(Path(plan_path).read_text())
    data=json.loads(Path(manifest).read_text())
    periods=set(plan['targets']) | {cf.target_month(plan['init'],w[0])+'-'+cf.target_month(plan['init'],w[-1]) for w in plan['windows']}
    for product in ('snowfall_accumulation','snowfall_anomaly'):
        matching=[r for r in data['runs'] if r['product']==product and r['id']==f"cfsv2-{plan['init']}-{product}"]
        if len(matching)!=1: raise ValueError('Missing corrected product')
        run=matching[0]
        if run.get('raw_field')!='APCP + CSNOW:surface': raise ValueError('Incorrect snowfall method')
        if {t['target_month'] for t in run['targets']}!=periods: raise ValueError('Incomplete winter periods')
        for target in run['targets']:
            if target.get('status')!='rendered' or target.get('ensemble_members')!=len(plan['cycles']):
                raise ValueError('Incomplete snowfall ensemble or image')
            if not Path(target['image']).is_file(): raise ValueError('Missing rendered image')
    print('Both snowfall products passed method, period, image, and sample checks.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['select','verify']);p.add_argument('--manifest');p.add_argument('--plan',default='surface-plan.json');a=p.parse_args()
    select() if a.mode=='select' else verify(a.manifest,a.plan)
