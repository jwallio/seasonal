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
    """Union a payload with the existing manifest without dropping products.

    The Pages publisher uses this path for both the normal CFSv2 suite and
    the independently rebuilt surface-phase snowfall pair.  The old
    implementation started with the incoming manifest and then appended
    retained snowfall runs.  That works for a normal weather payload, but a
    snowfall-only payload consequently erased every 500-mb, 850-mb, 2-m, MSLP,
    and precipitation run from the shared manifest.  Merge by run identity so
    either payload can safely update the common manifest.

    A non-corrected snowfall entry must not overwrite a corrected entry with
    the same id.  This protects the reviewed 10-to-1 surface-phase product
    when an older/general CFSv2 artifact is published later.
    """
    existing_runs = [r for r in existing.get('runs', []) if isinstance(r, dict)]
    incoming_runs = [r for r in incoming.get('runs', []) if isinstance(r, dict)]
    existing_corrected = {
        str(r.get('id'))
        for r in existing_runs
        if r.get('product') in SNOW and r.get('raw_field') == FIELD and r.get('id')
    }
    accepted = []
    for run in incoming_runs:
        run_id = str(run.get('id')) if run.get('id') else ''
        if (
            run.get('product') in SNOW
            and run.get('raw_field') != FIELD
            and run_id in existing_corrected
        ):
            continue
        accepted.append(deepcopy(run))

    accepted_ids = {str(r.get('id')) for r in accepted if r.get('id')}
    result = deepcopy(existing) if existing else deepcopy(incoming)
    for key, value in incoming.items():
        if key != 'runs':
            result[key] = deepcopy(value)
    result['runs'] = [
        deepcopy(run) for run in existing_runs
        if not run.get('id') or str(run.get('id')) not in accepted_ids
    ] + accepted
    result['runs'].sort(key=lambda r:r.get('init_utc',''),reverse=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--incoming',type=Path,required=True);p.add_argument('--site',type=Path,required=True);p.add_argument('--preserve',action='store_true');a=p.parse_args()
    destination=a.site/'cfsv2_manifest.json'
    existing=json.loads(destination.read_text()) if destination.exists() else {'runs':[]}
    incoming=json.loads((a.incoming/'cfsv2_manifest.json').read_text())
    if a.preserve:
        result=preserve_surface(existing,incoming)
        for source in (a.incoming/'cfsv2').rglob('*'):
            if not source.is_file() or source.is_symlink(): continue
            target=a.site/'cfsv2'/source.relative_to(a.incoming/'cfsv2')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    else:
        plan=json.loads((a.incoming/'surface-plan.json').read_text())
        result=merge(existing,incoming,plan['init'])
        shutil.copytree(a.incoming/'cfsv2'/plan['init'],a.site/'cfsv2'/plan['init'],dirs_exist_ok=True)
        (a.site/'cfsv2'/'surface-phase-latest.json').write_text(json.dumps(plan,indent=2))
    destination.write_text(json.dumps(result,indent=2)+'\n')
