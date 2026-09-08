"""Render only a fully validated paired surface-phase snapshot; never use native fallback."""
import argparse,json,subprocess,sys
from pathlib import Path
import cfsv2_surface_phase as phase
import cfsv2_seasonal as cf
p=argparse.ArgumentParser();p.add_argument('--product',required=True);a=p.parse_args()
root=Path('.cache/cfsv2-surface-phase')
candidates={}
for path in (root/'reference').glob('*.json'):
    meta=json.loads(path.read_text())
    if meta.get('method')==phase.METHOD:
        candidates[meta['initialization']]=meta
for init,meta in sorted(candidates.items(),reverse=True):
    try:
        cycles=meta['forecast_cycles']
        leads,windows=cf.default_winter_snowfall_windows(init)
        months=[cf.target_month(init,lead) for lead in leads]
        for month in months:
            phase.load_reference(root/'reference',init,month,cycles,1)
            for cycle in cycles: phase.load_month(root/'months',cycle,month)
    except (ValueError,FileNotFoundError,KeyError,cf.CFSv2Error):
        continue
    excluded=','.join(c for c in cf.rolling_cycle_inits(init,24) if c not in cycles)
    args=[sys.executable,'scripts/cfsv2_seasonal.py','--product',a.product,'--init',init,
          '--lead-months',','.join(map(str,leads)),'--seasonal-window',';'.join(','.join(map(str,w)) for w in windows),'--rolling-days','6',
          '--surface-phase-exclude-cycles',excluded,
          '--surface-phase-bundle-dir',str(root/'months'),
          '--previous-manifest','.cache/cfsv2/previous_manifest.json']
    if a.product=='snowfall_anomaly':args+=['--snowfall-reference-dir',str(root/'reference')]
    print('Using latest complete paired surface-phase snapshot:',init,flush=True)
    subprocess.run(args,check=True)
    break
else:
    raise SystemExit('No complete paired surface-phase snapshot; preserving published maps')
