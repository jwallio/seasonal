"""Publish only the checked surface-phase pair; preserve unrelated runs and newer data."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

FIELD='APCP + CSNOW:surface'
SNOW={'snowfall_anomaly','snowfall_accumulation'}

def merge(existing, incoming, init):
    selected=[r for r in incoming['runs'] if r.get('product') in SNOW and r.get('id')==f"cfsv2-{init}-{r['product']}"]
    if len(selected)!=2 or {r['product'] for r in selected}!=SNOW or any(r.get('raw_field')!=FIELD for r in selected):
        raise ValueError('Release must contain the complete reconstructed snowfall pair')
    if any(r.get('raw_field')==FIELD and r.get('init_utc','')>selected[0]['init_utc'] for r in existing['runs']):
        raise ValueError('A newer corrected run is already published; refusing regression')
    result=deepcopy(existing)
    incoming_ids={r['id'] for r in selected}
    result['runs']=[r for r in existing['runs'] if r['id'] not in incoming_ids]+selected
    result['runs'].sort(key=lambda r:r.get('init_utc',''),reverse=True)
    # The run-level count should describe the used sample, as the targets already do.
    for r in selected:
        counts={t['ensemble_members'] for t in r['targets']}
        if len(counts)!=1: raise ValueError('Seasonal/monthly samples differ')
        r['ensemble_members']=counts.pop()
    result['generated_utc']=incoming.get('generated_utc',existing.get('generated_utc'))
    return result

def preserve_surface(existing, incoming):
    """General weather publications cannot replace independently published snowfall."""
    result=deepcopy(incoming)
    if any(r.get('raw_field')==FIELD for r in existing.get('runs',[])):
        result['runs']=[r for r in result['runs'] if r.get('product') not in SNOW]
        result['runs'] += [r for r in existing['runs'] if r.get('product') in SNOW]
        result['runs'].sort(key=lambda r:r.get('init_utc',''),reverse=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--incoming',type=Path,required=True);p.add_argument('--site',type=Path,required=True);p.add_argument('--preserve',action='store_true');a=p.parse_args()
    destination=a.site/'cfsv2_manifest.json'
    existing=json.loads(destination.read_text()) if destination.exists() else {'runs':[]}
    incoming=json.loads((a.incoming/'cfsv2_manifest.json').read_text())
    if a.preserve:
        result=preserve_surface(existing,incoming)
        protected=any(r.get('raw_field')==FIELD for r in existing.get('runs',[]))
        for source in (a.incoming/'cfsv2').rglob('*'):
            if not source.is_file() or source.is_symlink(): continue
            if protected and 'snow' in source.name.lower(): continue
            target=a.site/'cfsv2'/source.relative_to(a.incoming/'cfsv2')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    else:
        plan=json.loads((a.incoming/'surface-plan.json').read_text())
        result=merge(existing,incoming,plan['init'])
        shutil.copytree(a.incoming/'cfsv2'/plan['init'],a.site/'cfsv2'/plan['init'],dirs_exist_ok=True)
        (a.site/'cfsv2'/'surface-phase-latest.json').write_text(json.dumps(plan,indent=2))
    destination.write_text(json.dumps(result,indent=2)+'\n')
