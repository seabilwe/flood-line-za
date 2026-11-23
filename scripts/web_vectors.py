"""Vector tiles for FLOOD-LINE ZA (mapbox-vector-tile + pmtiles).

growth.pmtiles:     built-up land on low-lying ground, by the five-year epoch it was first built (e) and class (c)
buildings.pmtiles  Johannesburg buildings on low-lying ground: class (c) and estimated height (h, GHSL ANBH)

Usage: python web_vectors.py <export_dir> <out_dir>
"""
import json, math, os, sys
import mercantile
import mapbox_vector_tile as mvt
from shapely.geometry import shape, box, mapping
from shapely.ops import transform
from shapely.strtree import STRtree
from shapely import make_valid
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression
import gzip

EXP, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
R = 6378137.0


def merc(x, y):
    return R * math.radians(x), R * math.log(math.tan(math.pi / 4 + math.radians(y) / 2))


def _tm(xs, ys):
    import numpy as np
    xs = np.asarray(xs); ys = np.asarray(ys)
    return R * np.radians(xs), R * np.log(np.tan(np.pi / 4 + np.radians(ys) / 2))


def build(feats, layer, zmin, zmax, path, simplify_px=0.6, min_area_px=0.0):
    """feats: list of (shapely geometry in EPSG:3857, props)."""
    geoms = [g for g, _ in feats]
    tree = STRtree(geoms)
    lon0 = min(g.bounds[0] for g in geoms); lat0 = min(g.bounds[1] for g in geoms)
    lon1 = max(g.bounds[2] for g in geoms); lat1 = max(g.bounds[3] for g in geoms)
    inv = lambda x, y: (math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2))
    ll0 = inv(lon0, lat0); ll1 = inv(lon1, lat1)
    entries = []
    for z in range(zmin, zmax + 1):
        for t in mercantile.tiles(ll0[0], ll0[1], ll1[0], ll1[1], zooms=z):
            b = mercantile.xy_bounds(t); w = b.right - b.left
            px = w / 4096
            buf = 64 * px
            tb = box(b.left - buf, b.bottom - buf, b.right + buf, b.top + buf)
            idx = tree.query(tb)
            if len(idx) == 0:
                continue
            out = []
            for i in idx:
                g = geoms[i]
                if not g.intersects(tb):
                    continue
                c = g.intersection(tb)
                if c.is_empty:
                    continue
                c = c.simplify(simplify_px * px, preserve_topology=True)
                if c.is_empty or c.area < min_area_px * px * px:
                    continue
                # to tile coordinates, y down
                c = transform(lambda xs, ys, z=None: ((xs - b.left) / px, (b.top - ys) / px), c)
                out.append({'geometry': c, 'properties': feats[i][1]})
            if not out:
                continue
            data = mvt.encode([{'name': layer, 'features': out}], default_options={'y_coord_down': True, 'extents': 4096})
            entries.append((zxy_to_tileid(z, t.x, t.y), gzip.compress(data)))
    entries.sort()
    with open(path, 'wb') as f:
        wr = Writer(f)
        for tid, d in entries:
            wr.write_tile(tid, d)
        wr.finalize({'tile_type': TileType.MVT, 'tile_compression': Compression.GZIP, 'min_zoom': zmin, 'max_zoom': zmax,
                     'min_lon_e7': int(ll0[0] * 1e7), 'min_lat_e7': int(ll0[1] * 1e7), 'max_lon_e7': int(ll1[0] * 1e7), 'max_lat_e7': int(ll1[1] * 1e7),
                     'center_zoom': zmin, 'center_lon_e7': int((ll0[0] + ll1[0]) / 2 * 1e7), 'center_lat_e7': int((ll0[1] + ll1[1]) / 2 * 1e7)},
                    {'vector_layers': [{'id': layer, 'fields': {k: 'Number' for k in feats[0][1]}}]})
    print(path, len(entries), 'tiles', round(os.path.getsize(path) / 1e6, 2), 'MB')


def growth():
    d = json.load(open(os.path.join(EXP, 'growth_lowlying.geojson')))
    feats = []
    for f in d['features']:
        p = f['properties']
        g = make_valid(shape(f['geometry']))
        feats.append((transform(_tm, g), {'e': int(p['e']), 'c': int(p['c'])}))
    build(feats, 'growth', 7, 13, os.path.join(OUT, 'growth.pmtiles'), simplify_px=0.8)


def buildings():
    feats = []
    for line in open(os.path.join(EXP, 'buildings_exposed.geojsonseq')):
        line = line.strip().lstrip('\x1e')
        if not line:
            continue
        f = json.loads(line); p = f['properties']
        h = p.get('oh') or (p.get('nf') or 0) * 3 or p.get('h') or 0
        h = max(3.0, min(float(h), 120.0))
        feats.append((transform(_tm, shape(f['geometry'])), {'c': int(p['c']), 'h': round(h, 1)}))
    build(feats, 'bld', 13, 15, os.path.join(OUT, 'buildings.pmtiles'), simplify_px=0.3)


if __name__ == '__main__':
    what = sys.argv[3] if len(sys.argv) > 3 else 'all'
    if what in ('all', 'growth'):
        growth()
    if what in ('all', 'buildings'):
        buildings()
