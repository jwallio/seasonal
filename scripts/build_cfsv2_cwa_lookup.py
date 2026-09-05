"""Offline lookup rebuild with verified NWS geometry; no network or runner dependency changes.

Uses the existing local shapely/pyshp map environment. Normal generation loads
the resulting arrays using NumPy alone. Input geometry must be the reviewed
April 16, 2026 boundary release; future boundary changes need separate review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import cfsv2_seasonal as cf
import render_cfsv2_cwa_snapshot as cwa


def build(reference_grid, cwa_shapefile, boundary_zip, state_borders, output_dir):
    if hashlib.md5(boundary_zip.read_bytes()).hexdigest() != 'b8614c30e80d68b7ccb74ff599c57c39':
        raise ValueError('Boundary archive differs from the verified NOAA release')
    # Require extracted geometry bytes to match the archive, not just its name.
    import zipfile
    with zipfile.ZipFile(boundary_zip) as archive:
        for suffix in ('.shp','.shx','.dbf'):
            file=cwa_shapefile.with_suffix(suffix)
            if archive.read(file.name) != file.read_bytes():
                raise ValueError('Extracted CWA geometry differs from archive')
    grid=cf.read_grid_state(reference_grid)
    geometry=cwa.read_cwas(cwa_shapefile)
    registry=json.loads(cwa.REGISTRY.read_text(encoding='utf-8'));ratios=registry['mean_ratios']
    if len(geometry)!=116 or len(ratios)!=97 or set(ratios)-set(geometry):
        raise ValueError('Unexpected CWA support')
    lon,lat=np.meshgrid(grid.lons,grid.lats);native,_=cwa.ratio_grid(lon,lat,geometry,ratios)
    dlons=np.linspace(-127,-65,1241);dlats=np.linspace(23,51,561)
    dx,dy=np.meshgrid(dlons,dlats);display,_=cwa.ratio_grid(dx,dy,geometry,ratios)
    missing=[]
    for code,geo in geometry.items():
        if code in ratios:continue
        for poly in (list(geo.geoms) if geo.geom_type=='MultiPolygon' else [geo]):
            missing.append(np.asarray(poly.exterior.coords))
    states=[]
    for feature in json.loads(state_borders.read_text(encoding='utf-8'))['features']:
        if feature['properties']['name'] not in cf.CONUS_STATE_NAMES:continue
        polys=feature['geometry']['coordinates']
        if feature['geometry']['type']=='Polygon':polys=[polys]
        states.extend(np.asarray(p[0]) for p in polys)
    data=dict(lons=grid.lons,lats=grid.lats,native_ratios=native,display_lons=dlons,display_lats=dlats,display_ratios=display)
    for name,rings in [('missing',missing),('states',states)]:
        data[name+'_points']=np.concatenate(rings);data[name+'_offsets']=np.cumsum([0]+[len(p) for p in rings])
    if output_dir.exists():raise ValueError('Rebuild into a fresh directory for comparison')
    output_dir.mkdir(parents=True)
    np.savez_compressed(output_dir/'cfsv2_cwa_slr_v1.npz',**data)
    print('Compare every array with the checked lookup before replacing it or its recorded hash.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['reference-grid','cwa-shapefile','boundary-zip','state-borders','output-dir']:
        parser.add_argument('--'+name,type=Path,required=True)
    build(**vars(parser.parse_args()))
