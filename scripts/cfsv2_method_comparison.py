"""Same-cycle DJF method comparison; artifacts only."""
import argparse, calendar, json, os
from pathlib import Path
import numpy as np
import cfsv2_seasonal as cf
import cfsv2_native_reference as native
import cfsv2_surface_phase as phase
from cfsv2_surface_phase_build import build_month
p=argparse.ArgumentParser()
p.add_argument('--init',required=True)
p.add_argument('--year',type=int)
a=p.parse_args()
root=Path('comparison');root.mkdir(exist_ok=True)
months=[f'{a.init[:4]}12',f'{int(a.init[:4])+1}01',f'{int(a.init[:4])+1}02']
cycles=phase.selected_cycles(cf.rolling_cycle_inits(a.init,24),'2026083118,2026090200,2026090418')
assert len(cycles)==21
if a.year:
    arrays={}; sources=[]
    for target in months:
        ht=str(a.year+int(target[:4])-int(a.init[:4]))+target[4:]
        collected={'native':[],'phase':[]}
        for forecast_cycle in cycles:
            mapped=native.historical_cycle(forecast_cycle,a.year,a.init)
            assert len(mapped)==1 and mapped[0][1]==1
            cycle=mapped[0][0]
            # Reconstruction is already complete: missing bundles must fail, not redownload.
            f,meta=phase.load_month(Path('.cache/compare/months'),cycle,ht)
            if a.year==int(a.init[:4]): os.environ['CFSV2_LIVE_INIT']=cycle
            n=native.sample(cycle,ht,Path('.cache/compare/native'))
            days=calendar.monthrange(int(target[:4]),int(target[4:]))[1]
            historical_days=calendar.monthrange(int(ht[:4]),int(ht[4:]))[1]
            for method,values,lons,lats in [
                ('phase',f['lwe']*days/historical_days,f['lons'],f['lats']),
                ('native',n['inches_lwe_per_day']*days,n['lons'],n['lats'])]:
                if f'{method}_lons' in arrays:
                    assert np.array_equal(arrays[f'{method}_lons'],lons) and np.array_equal(arrays[f'{method}_lats'],lats)
                arrays[f'{method}_lons']=lons;arrays[f'{method}_lats']=lats
                collected[method].append(values)
            sources.append({'phase':meta,'native':n['source']})
        for method,values in collected.items():
            assert len(values)==21
            arrays[f'{method}_{target}']=np.mean(values,axis=0)
        print(f'Completed paired {a.year}/{ht}: 21 cycles',flush=True)
    np.savez_compressed(root/f'{a.year}.npz',**arrays)
    (root/f'{a.year}.json').write_text(json.dumps(sources))

else:
    borders=cf.ensure_border_files(argparse.Namespace(border_geojson=[],no_borders=False),Path('.cache/borders'),Path.cwd())
    report={'init':a.init,'cycles':cycles,'reference_initialization_years':list(range(2011,2026)),'period':'DJF','ratio':10,'methods':{}}
    for method in ['native','phase']:
        with np.load(root/f'{a.init[:4]}.npz') as f:
            forecast=sum(f[f'{method}_{m}'] for m in months)
            lons=f[f'{method}_lons'];lats=f[f'{method}_lats']
        history=[]
        for year in range(2011,2026):
            with np.load(root/f'{year}.npz') as h:
                assert np.array_equal(lons,h[f'{method}_lons']) and np.array_equal(lats,h[f'{method}_lats'])
                history.append(sum(h[f'{method}_{m}'] for m in months))
        reference=np.mean(history,axis=0);departure=forecast-reference
        assert np.isfinite(departure).all()
        spec=dict(cf.get_product_spec('snowfall_anomaly'),title='CFSv2 Estimated Snowfall Departure',
            snowfall_input_kind=('Native SRWEQ' if method=='native' else 'Surface precipitation type'))
        grid=cf.Grid(lons.tolist(),lats.tolist(),departure.tolist())
        cf.render_map(grid,a.init,months[0],3,[1],root/f'{method}-DJF.jpg',True,'2011–2025 matched operational reference',borders,
            period_label='DJF '+a.init[:4]+'–'+str(int(a.init[:4])+1)[-2:],seasonal=True,ensemble_label='21-cycle matched mean · member 1',product_spec=spec)
        np.savez_compressed(root/f'{method}-fields.npz',lons=lons,lats=lats,forecast_inches=forecast*10,reference_inches=reference*10,departure_inches=departure*10)
        report['methods'][method]={'departure_min_inches':float(departure.min()*10),'departure_max_inches':float(departure.max()*10)}
    from PIL import Image
    images=[Image.open(root/f'{m}-DJF.jpg').convert('RGB') for m in ['native','phase']]
    sheet=Image.new('RGB',(sum(i.width for i in images),max(i.height for i in images)),'white')
    offset=0
    for im in images:sheet.paste(im,(offset,0));offset+=im.width
    sheet.save(root/'side-by-side.jpg')
    (root/'comparison.json').write_text(json.dumps(report,indent=2))
