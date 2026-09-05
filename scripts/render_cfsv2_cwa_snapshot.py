"""Offline renderer for the reviewed September 4, 2026 native snowfall snapshot.

Uses existing Conda numpy/matplotlib/shapely/pyshp; no production imports are
modified. CWA polygons apply to display coordinates before coloring, avoiding
interpolation of ratios or extrapolation into unsupported CWAs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Polygon as PlotPolygon

import cfsv2_seasonal as cf
LABELS = {'202701': 'January 2027', '202702': 'February 2027', '202703': 'March 2027', 'JFM': 'JFM 2027'}
from build_seasonal_share_images import save_branded_image

REGISTRY = Path(__file__).with_name('cips_cwa_mean_slr.json')


def read_cwas(path):
    reader = shapefile.Reader(str(path))
    result = {}
    for item in reader.iterShapeRecords():
        record = item.record.as_dict()
        if record['REGION'] not in ('CR', 'ER', 'WR', 'SR') or record['CWA'] == 'SJU':
            continue
        code = record['CWA']
        if code in result:
            raise ValueError('Duplicate CWA geometry: ' + code)
        geometry = shape(item.shape.__geo_interface__)
        if not geometry.is_valid:
            raise ValueError('Invalid CWA geometry: ' + code)
        result[code] = geometry
    return result


def ratio_grid(lons, lats, cwas, ratios):
    """Exact CWA membership; deterministic edge tie, unknown remains NaN."""
    result = np.full(np.shape(lons), np.nan)
    codes = np.full(np.shape(lons), '', dtype='<U3')
    for code, geometry in sorted(cwas.items()):
        minx, miny, maxx, maxy = geometry.bounds
        candidate = (lons >= minx) & (lons <= maxx) & (lats >= miny) & (lats <= maxy)
        y, x = np.where(candidate)
        if not len(x):
            continue
        inside = shapely.covers(geometry, shapely.points(lons[y, x], lats[y, x]))
        y, x = y[inside], x[inside]
        # A shared polygon edge belongs deterministically to the first CWA.
        empty = codes[y, x] == ''
        y, x = y[empty], x[empty]
        codes[y, x] = code
        if code in ratios:
            result[y, x] = ratios[code]
    return result, codes


def convert(lwe, ratios):
    values = np.asarray(lwe, dtype=float)
    if values.shape != np.shape(ratios):
        raise ValueError('Ratio and snowfall grids differ')
    if np.any(values[np.isfinite(values)] < 0):
        raise ValueError('Negative native snowfall')
    return values * ratios


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


def render(values, lons, lats, cwas, supported, states, period, output, ratio=False, accumulation_style=None):
    x, y = project(lons, lats)
    fig = plt.figure(figsize=(9, 7.35), dpi=120, facecolor='#f7f9fb')
    ax = fig.add_axes([.038, .15, .924, .70], facecolor='#edf3f5')
    if ratio:
        bounds = list(range(7, 19))
        palette = [matplotlib.colors.to_hex(plt.get_cmap('viridis')(i/10)) for i in range(11)]
        ticks = bounds
    else:
        bounds, ticks, palette = cf.absolute_style(cf.get_product_spec(cf.PRODUCT_SNOWFALL_ACCUMULATION), period == 'JFM')
        if accumulation_style is not None:
            bounds, ticks, palette = (accumulation_style[k] for k in ('bounds','ticks','palette'))
    cmap = ListedColormap(palette)
    cmap.set_over(palette[-1])
    norm = BoundaryNorm(bounds, cmap.N, clip=False)
    # Explicit over-range color/legend: preserve arrays and discrete intervals.
    field = ax.contourf(x, y, np.ma.masked_invalid(values),
                        levels=bounds, cmap=cmap, norm=norm, extend='neither' if ratio else 'max',
                        antialiased=True, corner_mask=False)
    for code, geometry in cwas.items():
        if code in supported:
            continue
        polygons = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
        for polygon in polygons:
            coords = np.asarray(polygon.exterior.coords)
            px, py = project(coords[:, 0], coords[:, 1])
            ax.add_patch(PlotPolygon(np.column_stack([px, py]), facecolor='#e5eaed', edgecolor='#acb7bd',
                                    hatch='////', linewidth=.25, zorder=3))
    all_state_points = []
    for feature in states['features']:
        if feature['properties'].get('name') not in cf.CONUS_STATE_NAMES:
            continue
        polygons = feature['geometry']['coordinates']
        if feature['geometry']['type'] == 'Polygon':
            polygons = [polygons]
        for polygon in polygons:
            coords = np.asarray(polygon[0])
            px, py = project(coords[:, 0], coords[:, 1])
            ax.plot(px, py, color='#263c46', linewidth=.55, zorder=4)
            all_state_points.append(np.column_stack([px, py]))
    # Fit the complete lower 48, including unsupported areas; never fit only
    # finite snowfall or exclude Florida because its ratio is unavailable.
    extent = np.concatenate(all_state_points)
    ax.set_xlim(extent[:, 0].min()-.006, extent[:, 0].max()+.006)
    ax.set_ylim(extent[:, 1].min()-.006, extent[:, 1].max()+.006)
    ax.set_aspect('equal')
    ax.set_xticks([]); ax.set_yticks([])
    cax = fig.add_axes([.038, .100, .924, .034])
    cb = fig.colorbar(field, cax=cax, orientation='horizontal', ticks=ticks, spacing='uniform', drawedges=True,
                      extendrect=True, extendfrac=0)
    cb.ax.tick_params(labelsize=9, length=3)
    cb.outline.set_linewidth(.5)
    title = 'CIPS CWA Mean Snow-to-Liquid Ratios' if ratio else 'CFSv2 Estimated Snowfall Accumulation (in)'
    fig.text(.038, .955, title, fontsize=15.5, weight='bold', color='#172735')
    fig.text(.962, .955, '1971–2000' if ratio else LABELS[period], fontsize=13, weight='bold', ha='right', color='#172735')
    fig.text(.038, .912, 'EXPERIMENTAL ESTIMATE  •  ' + ('Published CWA means; not monthly or storm-specific ratios' if ratio else
             'Latest init 04 Sep 2026 12Z  •  24 forecast cycles, Aug 29–Sep 4'), fontsize=10, color='#43535d')
    fig.text(.038, .878, 'Native snowfall × CIPS CWA mean ratio  •  Hatched areas: ratio unavailable' if not ratio else
             '97 supported CWAs  •  Hatched areas: ratio unavailable', fontsize=9.5, color='#536875')
    fig.text(.5, .052, 'Snow-to-liquid ratio (snow:water)' if ratio else 'Accumulated snowfall depth (inches)  •  Not standing snowpack',
             ha='center', fontsize=10, color='#43535d')
    fig.text(.5, .028, 'CWA boundaries can create steps. Historical event means are not storm-specific.' if ratio else
             'Unadjusted estimate  •  CWA steps possible  •  Colors saturate at 200 in; higher values retained',
             ha='center', fontsize=8.5, color='#536875')
    source = output / ('ratio-source.png' if ratio else f'{period}-total-source.png')
    fig.savefig(source, dpi=120); plt.close(fig)
    save_branded_image(source, output / ('ratio.png' if ratio else f'{period}-total.png'))
