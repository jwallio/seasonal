"""Publication gate for the reviewed native-snowfall snapshot; no network or writes."""
import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from PIL import Image

RUN = '20260904T1200Z-style-a'
MONTHS = ('202701', '202702', '202703')


def validate(site_root):
    page = site_root / 'cfsv2/native-snowfall'
    root = page / RUN
    provenance = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
    expected = {f'{p}-{suffix}' for p in (*MONTHS, 'JFM')
                for suffix in ('total.png', 'total.csv.gz', 'lwe.csv.gz')}
    if set(provenance['files_sha256']) != expected:
        raise ValueError('Incomplete or unexpected asset set')
    for name, digest in provenance['files_sha256'].items():
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Asset hash mismatch: {name}')
        if name.endswith('.png'):
            with Image.open(path) as image:
                if image.size != (1080, 914):
                    raise ValueError(f'Incorrect graphic dimensions: {name}')
                image.verify()
    if provenance['automatic_refresh'] or provenance['departure_status'] != 'unavailable':
        raise ValueError('Snapshot must retain its frozen-run and unavailable-departure status')
    grids = {}
    coords = None
    for kind in ('total', 'lwe'):
        for period in (*MONTHS, 'JFM'):
            with gzip.open(root / f'{period}-{kind}.csv.gz', 'rt') as stream:
                rows = list(csv.DictReader(stream))
            points = [(r['lon'], r['lat']) for r in rows]
            if len(points) != 384 * 190 or (coords is not None and points != coords):
                raise ValueError('Incompatible grid geometry')
            coords = points
            values = [float(r['value']) for r in rows]
            if any(math.isinf(v) or v < 0 for v in values):
                raise ValueError('Negative/infinite accumulation')
            if kind == 'lwe' and not all(math.isfinite(v) for v in values):
                raise ValueError('Incomplete native LWE input')
            grids[period, kind] = values
        for a, b, c, total in zip(*(grids[p, kind] for p in (*MONTHS, 'JFM'))):
            if math.isnan(total):
                if not all(math.isnan(v) for v in (a, b, c)):
                    raise ValueError('Seasonal missingness mismatch')
            elif not math.isclose(a + b + c, total, rel_tol=1e-8, abs_tol=1e-6):
                raise ValueError('JFM does not equal monthly sum')
    if len(provenance['supported_cwas']) != 97 or len(provenance['unsupported_cwas']) != 19:
        raise ValueError('Coverage differs from reviewed product')
    if not any(math.isnan(v) for v in grids['JFM', 'total']):
        raise ValueError('Missing ratios were filled')
    html = (page / 'index.html').read_text(encoding='utf-8')
    if RUN not in html or 'does not refresh automatically' not in html:
        raise ValueError('Viewer references wrong run or omits snapshot status')
    print('NATIVE SNAPSHOT OK: 12 hashes, four PNGs, eight grids, monthly/seasonal identity, missingness and status')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-root', type=Path, required=True)
    validate(parser.parse_args().site_root)
