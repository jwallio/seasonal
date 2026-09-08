"""Nonproduction paired snow-only sensitivity test. Never publishes site assets."""
import argparse
import calendar
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import cfsv2_surface_phase as phase
from cfsv2_surface_phase_build import Source

def strict_amount(p, previous, current):
    a = previous[0] * ((previous[2] == 0) & (previous[3] == 0))
    b = current[0] * ((current[2] == 0) & (current[3] == 0))
    return phase.interval_amount(p, a, b)

def acquire(init, target, year, output, raw):
    historical_init = str(year) + init[4:]
    historical_target = str(year + int(target[:4]) - int(init[:4])) + target[4:]
    if year == int(init[:4]):
        os.environ['CFSV2_LIVE_INIT'] = init
    source = Source(raw, historical_init)
    times = phase.endpoints(historical_target)
    axes = None
    records = []
    def read(valid, field):
        nonlocal axes
        path = source(valid, field)
        x, y, a = phase.read_field(path, field, historical_init, valid)
        if axes is None: axes = (x, y)
        if not np.array_equal(x, axes[0]) or not np.array_equal(y, axes[1]):
            raise ValueError('Source axes differ')
        records.append(dict(valid=valid, field=field, sha256=phase.digest(path)))
        return a
    def flags(valid):
        return np.array([read(valid, field) for field in ('csnow','crain','cicep','cfrzr')])
    previous = flags(times[0])
    baseline = np.zeros(previous.shape[1:]); strict = baseline.copy()
    for valid in times[1:]:
        current = flags(valid); p = read(valid, 'apcp')
        baseline += phase.interval_amount(p, previous[0], current[0])
        strict += strict_amount(p, previous, current)
        previous = current
    assert np.all(strict <= baseline + 1e-12)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f'{year}.npz'
    np.savez_compressed(path, lons=axes[0], lats=axes[1], baseline=baseline, strict=strict)
    path.with_suffix('.json').write_text(json.dumps(dict(init=historical_init,target=historical_target,
        year=year,forecast_init=init,forecast_target=target,intervals=len(times)-1,
        sha256=phase.digest(path),records=records,method='endpoint_snow_excluding_icep_frzr_v1'),indent=2))

def render(init, target, directory, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    import cfsv2_native_snow as n
    from cfsv2_seasonal import Grid
    bundles = {}
    for year in [*phase.YEARS, int(init[:4])]:
        path=directory/f'{year}.npz';meta=json.loads(path.with_suffix('.json').read_text())
        assert meta['forecast_init']==init and meta['forecast_target']==target
        assert meta['sha256']==phase.digest(path)
        with np.load(path) as z: bundles[year]={k:z[k].copy() for k in z.files}
        assert meta['intervals']==calendar.monthrange(int(meta['target'][:4]),int(target[4:]))[1]*4
    forecast=bundles[int(init[:4])]; days=calendar.monthrange(int(target[:4]),int(target[4:]))[1]
    for z in bundles.values():
        assert np.array_equal(z['lons'],forecast['lons']) and np.array_equal(z['lats'],forecast['lats'])
    references={k:np.mean([bundles[y][k]/calendar.monthrange(y+int(target[:4])-int(init[:4]),int(target[4:]))[1]*days for y in phase.YEARS],axis=0) for k in ('baseline','strict')}
    data,_=n.lookup();lon,lat=np.meshgrid(data['display_lons'],data['display_lats']);x,y=n.project(lon,lat)
    fig,axs=plt.subplots(2,2,figsize=(18,13)); stats={}
    for col,k in enumerate(('baseline','strict')):
        stats[k]={}
        for row in range(2):
            values=(forecast[k] if row==0 else forecast[k]-references[k])*10
            grid=Grid(forecast['lons'].tolist(),forecast['lats'].tolist(),values.tolist())
            field=np.where(np.isfinite(data['display_ratios']),n.sample(grid,lon,lat),np.nan)
            if row==0:
                bounds,_,colors=n.accumulation_style(False);cmap=ListedColormap(colors);cmap.set_over(colors[-1])
            else:
                bounds=list(range(-10,11));cmap=plt.get_cmap('BrBG',20)
            ax=axs[row,col]
            z=ax.contourf(x,y,field,levels=bounds,cmap=cmap,norm=BoundaryNorm(bounds,cmap.N),extend='both' if row else 'max')
            pts=data['states_points']; offsets=data['states_offsets']; extent=[]
            for a,b in zip(offsets[:-1],offsets[1:]):
                xx,yy=n.project(pts[a:b,0],pts[a:b,1]);ax.plot(xx,yy,color='#172735',linewidth=.6);extent.extend(zip(xx,yy))
            extent=np.array(extent);ax.set_xlim(extent[:,0].min()-.006,extent[:,0].max()+.006);ax.set_ylim(extent[:,1].min()-.006,extent[:,1].max()+.006)
            ax.set_aspect('equal');ax.set_xticks([]);ax.set_yticks([])
            ax.set_title(('Current method' if col==0 else 'Exclude sleet/freezing rain')+' — '+('accumulation' if row==0 else 'departure')+' (in)')
            fig.colorbar(z,ax=ax,orientation='horizontal',ticks=bounds,spacing='uniform',fraction=.05,pad=.025).ax.tick_params(labelsize=6)
            stats[k]['accumulation' if row==0 else 'departure']={'min':float(np.nanmin(field)),'max':float(np.nanmax(field))}
    fig.suptitle(f'CFSv2 STRICT SNOW-ONLY TEST — NOT OPERATIONAL\nInit {init}; valid {target}; ONE forecast cycle; 15 matched historical initializations (2011–2025)\nEach method uses its own reference; fixed 10:1 ratio; endpoint transition approximation',fontsize=13)
    output.mkdir(parents=True,exist_ok=True);fig.savefig(output/'comparison.png',dpi=150,bbox_inches='tight');plt.close(fig)
    (output/'results.json').write_text(json.dumps(stats,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--init',default='2026090806');p.add_argument('--target',default='202702');p.add_argument('--year',type=int);p.add_argument('--render',action='store_true');p.add_argument('--output',type=Path,default=Path('strict-test'));p.add_argument('--raw',type=Path,default=Path('strict-raw'));a=p.parse_args()
    if a.render: render(a.init,a.target,a.output,a.output/'results')
    else: acquire(a.init,a.target,a.year,a.output,a.raw)
if __name__=='__main__': main()
