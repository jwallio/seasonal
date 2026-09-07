"""Offline, explicitly experimental February preview. Never writes site output."""
import argparse
import calendar
import json
from pathlib import Path
import numpy as np
from cfsv2_surface_phase import load_month, METHOD
from cfsv2_native_snow import lookup, sample, project
from cfsv2_seasonal import Grid, SNOWFALL_ANOMALY_PALETTE

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundles',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('preview-output'))
    args=p.parse_args()
    current,meta=load_month(args.bundles,'2026090612','202702')
    historical=[]; provenance=[meta]
    for year in range(2011,2019):
        a,m=load_month(args.bundles,f'{year}090612',f'{year+1}02')
        for axis in ('lats','lons'):
            if not np.array_equal(a[axis],current[axis]): raise ValueError('Mismatched grids')
        historical.append(a['lwe']*28/calendar.monthrange(year+1,2)[1])
        provenance.append(m)
    reference=np.mean(historical,axis=0)
    fields={'forecast':current['lwe'],'reference':reference,'departure':current['lwe']-reference}
    args.output.mkdir(parents=True,exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    geometry,_=lookup()
    lon,lat=np.meshgrid(geometry['display_lons'],geometry['display_lats'])
    x,y=project(lon,lat)
    for name,values in fields.items():
        grid=Grid(current['lons'].tolist(),current['lats'].tolist(),values.tolist())
        z=np.where(np.isfinite(geometry['display_ratios']),sample(grid,lon,lat)*10,np.nan)
        anomaly=name=='departure'
        bounds=list(range(-10,11)) if anomaly else [0,1,2,3,4,5,6,8,10,12,16,20,30,40,60]
        colors=(['#572308','#71310b','#8c4712','#a65e1c','#b97b39','#c99259','#d6ab79','#e1c59a','#ead8b4','#ffffff','#ffffff','#b9dce8','#91c7d8','#6bb0c5','#4a94af','#347b9a','#246180','#194765','#10344c','#092838'] if anomaly else ['#ffffff']+[plt.cm.Blues(v) for v in np.linspace(.2,1,len(bounds)-2)])
        cmap=ListedColormap(colors)
        fig=plt.figure(figsize=(10,8),dpi=140,facecolor='#f7f9fb')
        ax=fig.add_axes([.04,.19,.92,.66])
        plot=ax.contourf(x,y,np.ma.masked_invalid(z),levels=bounds,cmap=cmap,norm=BoundaryNorm(bounds,cmap.N),extend='both' if anomaly else 'max')
        points=geometry['states_points'];offsets=geometry['states_offsets'];extent=[]
        for a,b in zip(offsets[:-1],offsets[1:]):
            px,py=project(points[a:b,0],points[a:b,1]);ax.plot(px,py,color='#263c46',lw=.5);extent.extend(zip(px,py))
        extent=np.asarray(extent);ax.set_xlim(extent[:,0].min()-.006,extent[:,0].max()+.006);ax.set_ylim(extent[:,1].min()-.006,extent[:,1].max()+.006)
        ax.set_aspect('equal');ax.set_xticks([]);ax.set_yticks([])
        fig.colorbar(plot,cax=fig.add_axes([.05,.125,.9,.03]),orientation='horizontal',ticks=bounds)
        titles={'forecast':'Forecast Snowfall','reference':'Model Reference Snowfall','departure':'Snowfall Departure'}
        fig.text(.04,.955,'CFSv2 Estimated '+titles[name]+' (in)',fontsize=17,weight='bold')
        fig.text(.04,.912,'TEST PREVIEW • February 2027 • Init 06 Sep 2026 12Z • One cycle',fontsize=11)
        fig.text(.04,.875,'Surface precipitation type • 10:1 depth estimate • Reference: February 2012–2019',fontsize=10)
        fig.text(.5,.065,'Experimental single-cycle example — not the production seasonal forecast',ha='center',fontsize=10)
        fig.text(.5,.035,'Reference normalized to 28 days. Departure = forecast minus matching-method reference.',ha='center',fontsize=9)
        fig.savefig(args.output/f'{name}.png');plt.close(fig)
    np.savez_compressed(args.output/'preview-grids.npz',lons=current['lons'],lats=current['lats'],**fields)
    (args.output/'provenance.json').write_text(json.dumps({'method':METHOD,'units':'inches_water_equivalent in NPZ; PNG values multiplied by 10 once','sources':provenance},indent=2))
    (args.output/'README.md').write_text('Experimental February 2027 single-cycle preview, initialized September 6 2026 12Z. Reference: February 2012–2019, same initialization date/hour and method, normalized to 28 days. No observed bias correction. Not a DJF/JFM or 23-cycle product. No production publishing.\n')

if __name__=='__main__': main()
