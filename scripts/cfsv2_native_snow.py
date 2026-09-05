"""Native SRWEQ accumulation with exact, precomputed CIPS CWA ratios."""
import calendar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import numpy as np
import requests
import cfsv2_seasonal as cf
from functools import lru_cache


def snowfall_record(index, init, month):
    lines = [line for line in index.splitlines() if line.strip()]
    matches = [i for i, line in enumerate(lines) if ':SRWEQ:surface:' in line]
    if len(matches) != 1:
        raise ValueError('Exactly one surface SRWEQ record is required')
    i = matches[0]
    lead = (int(month[:4]) - int(init[:4])) * 12 + int(month[4:]) - int(init[4:6])
    if f':d={init}:' not in lines[i] or f':{lead}-{lead + 1} month ave fcst:' not in lines[i]:
        raise ValueError('Initialization or monthly lead does not match requested source')
    start = int(lines[i].split(':')[1])
    end = int(lines[i + 1].split(':')[1]) - 1 if i + 1 < len(lines) else None
    return start, end


def get_bounded(session, url, limit, headers=None):
    # Serial, bounded requests. Never silently accept a full file for a range.
    with session.get(url, headers=headers, timeout=(15, 60), stream=True) as response:
        response.raise_for_status()
        if headers and response.status_code != 206:
            raise ValueError('Provider did not honor the byte range')
        data = bytearray()
        for chunk in response.iter_content(32768):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Source exceeds bounded download limit')
        return bytes(data), dict(response.headers)


def strict_mean(grids, expected=24):
    if len(grids) != expected:
        raise ValueError(f'Incomplete ensemble: {len(grids)}/{expected}')
    first = grids[0]
    for grid in grids:
        first.assert_compatible(grid, 'native snowfall preview')
    values = np.asarray([g.values for g in grids])
    if not np.isfinite(values).all() or np.min(values) < 0:
        raise ValueError('Missing or negative native snowfall input')
    return cf.Grid(first.lons[:], first.lats[:], np.mean(values, axis=0).tolist())


@lru_cache(maxsize=1)
def lookup():
    path = Path(__file__).with_name('data') / 'cfsv2_cwa_slr_v1.npz'
    meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta['lookup_sha256']:
        raise ValueError('CIPS lookup checksum differs from verified geometry')
    with np.load(path, allow_pickle=False) as data:
        return {k: data[k].copy() for k in data.files}, meta


def depth_grid(lwe):
    data, _ = lookup()
    if (np.shape(lwe.values) != data['native_ratios'].shape or
        not np.allclose(lwe.lons, data['lons'], rtol=0, atol=1e-6) or
        not np.allclose(lwe.lats, data['lats'], rtol=0, atol=1e-6)):
        raise ValueError('Native CFSv2 axes changed; CWA lookup must be rebuilt')
    values = np.asarray(lwe.values)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Native snowfall must be complete and nonnegative before masking')
    return cf.Grid(lwe.lons[:], lwe.lats[:], (values * data['native_ratios']).tolist())


def cached_cycle(session, args, cycle, member, target, state_dir, cache_dir, wgrib2):
    url = cf.cfs_file_url(cycle, member, target, 'flxf')
    state = state_dir / 'native-srweq-v1' / f'{cycle}-{member}' / f'{target}.csv.gz'
    sidecar = state.with_suffix('.json')
    if state.exists() and sidecar.exists() and not args.force_decode:
        source = json.loads(sidecar.read_text(encoding='utf-8'))
        if (source['url'] == url and source['target_month'] == target and
            source['initialization'] == cycle and source.get('decoded_field') == 'SRWEQ:surface' and
            source.get('raw_units') == 'kg m-2 s-1' and
            hashlib.sha256(state.read_bytes()).hexdigest() == source['state_sha256']):
            grid = cf.read_grid_state(state)
            depth_grid(grid)  # grid/finite contract applies to retained data too
            return grid, {**source, 'storage': 'retained_decoded_grid', 'downloaded': False}
    raw = cache_dir / cycle / f'{target}-{member}-SRWEQ.grb2'
    raw.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            time.sleep(max(0., args.request_delay))
            index, _ = get_bounded(session, url + '.idx', 100000)
            start, end = snowfall_record(index.decode('ascii'), cycle, target)
            time.sleep(max(0., args.request_delay))
            content, headers = get_bounded(session, url, 200000,
                {'Range': f'bytes={start}-{end if end is not None else ""}'})
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', headers.get('Content-Range', ''))
            if (not match or int(match[1]) != start or int(match[2])-start+1 != len(content)
                or (end is not None and int(match[2]) != end)):
                raise ValueError('Invalid native snowfall Content-Range')
            if not content.startswith(b'GRIB') or not content.endswith(b'7777') or int.from_bytes(content[8:16], 'big') != len(content):
                raise ValueError('Incomplete native snowfall GRIB2 message')
            temporary = raw.with_suffix('.part')
            temporary.write_bytes(content)
            temporary.replace(raw)
            break
        except Exception:
            if attempt == 2: raise
            time.sleep(2 ** attempt)
    metadata = subprocess.run([wgrib2, str(raw), '-V'], capture_output=True, text=True, check=True).stdout
    if 'SRWEQ Snowfall Rate Water Equivalent [kg/m^2/s]' not in metadata or ':surface:' not in metadata:
        raise ValueError('Wrong native snowfall variable, level or units')
    times = subprocess.run([wgrib2, str(raw), '-fix_CFSv2_fcst', 'daily', '1', '1',
        '-start_ft', '-end_ft'], capture_output=True, text=True, check=True).stdout
    days = calendar.monthrange(int(target[:4]), int(target[4:]))[1]
    if f'start_ft={target}0100' not in times or f'end_ft={target}{days}18' not in times:
        raise ValueError('Native snowfall valid month does not match request')
    rate = cf.decode_grib(raw, wgrib2, force=True, match_pattern=':SRWEQ:surface:',
                          cache_tag='native-srweq-v1', expected_shape=(384,190))
    grid = cf.monthly_precipitation_total_inches(rate, target)
    depth_grid(grid)
    cf.write_grid_state(grid, state)
    source = dict(url=url, initialization=cycle, member=member, target_month=target,
        initialization_utc=cf.iso_utc(datetime.strptime(cycle,'%Y%m%d%H').replace(tzinfo=timezone.utc)),
        lead_month=cf.lead_for_target(cycle,target), source_kind='FLXF',
        decoded_field='SRWEQ:surface', raw_units='kg m-2 s-1', units='in LWE',
        byte_start=start, byte_end=int(match[2]), bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(), state_sha256=hashlib.sha256(state.read_bytes()).hexdigest(),
        retrieved_utc=datetime.now(timezone.utc).isoformat(), status='available',
        storage='nomads_grib2_range', downloaded=True, corrected_time_metadata=times.strip())
    temp = sidecar.with_suffix('.part')
    temp.write_text(json.dumps(source, indent=2), encoding='utf-8'); temp.replace(sidecar)
    return grid, source


def decode(args, init, target, members, rolling_inits, cache_dir, state_dir, wgrib2):
    pairs = [(cycle, args.rolling_member) for cycle in rolling_inits] if rolling_inits else [(init, m) for m in members]
    if not pairs or len(set(pairs)) != len(pairs):
        raise ValueError('Native snowfall requires unique complete cycles/members')
    with requests.Session() as session:
        decoded = [cached_cycle(session,args,c,m,target,state_dir,cache_dir,wgrib2) for c,m in pairs]
    lwe = strict_mean([g for g,_ in decoded], expected=len(pairs))
    depth = depth_grid(lwe)
    _, meta = lookup()
    diagnostics = dict(method='native_SRWEQ_times_CIPS_CWA_mean_v1',
        snow_to_liquid_ratio=meta, native_departure_status='unavailable',
        bias_correction='none', unsupported_cwas=meta['unsupported_cwas'],
        display_method='Bilinear native LWE then exact CWA ratio; no added smoothing',
        _native_lwe=lwe)
    count = len(pairs)
    label = f'{count}/{count}-cycle rolling mean' if rolling_inits else f'{count}-member mean'
    return depth, [s for _,s in decoded], count, count, label, time.monotonic(), diagnostics


def render(lwe, init, target, lead, output, seasonal=False, period_label='', ensemble_label=''):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Polygon
    # Pure NumPy sampling/projection helpers from the approved offline renderer.
    data, _ = lookup()
    xlon, ylat = np.meshgrid(data['display_lons'], data['display_lats'])
    x, y = project(xlon, ylat)
    field = sample(lwe, xlon, ylat) * data['display_ratios']
    bounds, ticks, palette = cf.absolute_style(cf.get_product_spec(cf.PRODUCT_SNOWFALL_ACCUMULATION), seasonal)
    cmap = ListedColormap(palette); cmap.set_over(palette[-1])
    fig = plt.figure(figsize=(9,7.35), dpi=120, facecolor='#f7f9fb')
    ax = fig.add_axes([.038,.15,.924,.70], facecolor='#edf3f5')
    filled = ax.contourf(x,y,np.ma.masked_invalid(field), levels=bounds, cmap=cmap,
        norm=BoundaryNorm(bounds,cmap.N,clip=False), extend='max', antialiased=True, corner_mask=False)
    state_points=[]
    for kind in ['missing','states']:
        points, offsets = data[kind+'_points'], data[kind+'_offsets']
        for a,b in zip(offsets[:-1],offsets[1:]):
            px,py=project(points[a:b,0],points[a:b,1])
            if kind=='missing':
                ax.add_patch(Polygon(np.column_stack([px,py]),facecolor='#e5eaed',edgecolor='#acb7bd',hatch='////',linewidth=.25,zorder=3))
            else:
                ax.plot(px,py,color='#263c46',linewidth=.55,zorder=4); state_points.append(np.column_stack([px,py]))
    extent=np.concatenate(state_points)
    ax.set_xlim(extent[:,0].min()-.006,extent[:,0].max()+.006);ax.set_ylim(extent[:,1].min()-.006,extent[:,1].max()+.006)
    ax.set_aspect('equal');ax.set_xticks([]);ax.set_yticks([])
    cb=fig.colorbar(filled,cax=fig.add_axes([.038,.100,.924,.034]),orientation='horizontal',ticks=ticks,spacing='uniform',drawedges=True,extendrect=True,extendfrac=0)
    cb.ax.tick_params(labelsize=9,length=3);cb.outline.set_linewidth(.5)
    label=period_label or datetime.strptime(target,'%Y%m').strftime('%b %Y')
    fig.text(.038,.955,'CFSv2 Estimated Snowfall Accumulation (in)',fontsize=15.5,weight='bold',color='#172735')
    fig.text(.962,.955,label,fontsize=13,weight='bold',ha='right',color='#172735')
    initialized=datetime.strptime(init,'%Y%m%d%H').strftime('%d %b %Y %HZ')
    fig.text(.038,.912,f'Init {initialized}  •  Lead {lead}  •  {ensemble_label}',fontsize=10,color='#43535d')
    fig.text(.038,.878,'Native snowfall × CIPS CWA mean ratio  •  Hatched areas: ratio unavailable',fontsize=9.5,color='#536875')
    fig.text(.5,.052,'Accumulated snowfall depth (inches)  •  Not standing snowpack',ha='center',fontsize=10,color='#43535d')
    fig.text(.5,.028,'Unadjusted estimate  •  CWA steps possible  •  Colors saturate at 200 in; higher values retained',ha='center',fontsize=8.5,color='#536875')
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=120,pil_kwargs={'quality':95,'subsampling':0} if output.suffix=='.jpg' else {})
    plt.close(fig)


def sample(grid, lons, lats):
    xs, ys, z = np.asarray(grid.lons), np.asarray(grid.lats), np.asarray(grid.values)
    if np.any(np.diff(xs) <= 0) or np.any(np.diff(ys) <= 0):
        raise ValueError('Source axes must be sorted')
    if np.min(lons) < xs[0] or np.max(lons) > xs[-1] or np.min(lats) < ys[0] or np.max(lats) > ys[-1]:
        raise ValueError('Display interpolation must not extrapolate')
    ix = np.clip(np.searchsorted(xs, lons, side='right'), 1, len(xs)-1)
    iy = np.clip(np.searchsorted(ys, lats, side='right'), 1, len(ys)-1)
    wx = (lons-xs[ix-1])/(xs[ix]-xs[ix-1])
    wy = (lats-ys[iy-1])/(ys[iy]-ys[iy-1])
    return z[iy-1, ix-1]*(1-wx)*(1-wy) + z[iy-1, ix]*wx*(1-wy) + z[iy, ix-1]*(1-wx)*wy + z[iy, ix]*wx*wy


def project(lons, lats):
    # Same Lambert constants as the production renderer, normalized radius.
    p1, p2 = np.deg2rad([cf.SEASONAL_LCC_STANDARD_PARALLEL_1, cf.SEASONAL_LCC_STANDARD_PARALLEL_2])
    p0 = np.deg2rad(cf.SEASONAL_LCC_LATITUDE_ORIGIN)
    n = np.log(np.cos(p1) / np.cos(p2)) / np.log(np.tan(np.pi/4+p2/2) / np.tan(np.pi/4+p1/2))
    f = np.cos(p1) * np.tan(np.pi/4+p1/2)**n / n
    r0 = f / np.tan(np.pi/4+p0/2)**n
    r = f / np.tan(np.pi/4+np.deg2rad(lats)/2)**n
    a = n * np.deg2rad(np.asarray(lons)-cf.SEASONAL_LCC_CENTRAL_LONGITUDE)
    return r*np.sin(a), r0-r*np.cos(a)
