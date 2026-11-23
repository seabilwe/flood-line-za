"""Build the web tile archives for FLOOD-LINE ZA (runs anywhere with rasterio + pmtiles).

terrain.pmtiles   Terrarium-encoded elevation from FABDEM (bare earth), for MapLibre 3D terrain
flood.pmtiles     Pre-styled raster tiles: low-lying land along streams (2 classes) and the
                  JRC river-flood model (1-in-10, 1-in-100, 1-in-500 years), one tile set each

Usage: python web_tiles.py <work_dir> <out_dir>
"""
import io, sys, math, os
import numpy as np
import mercantile
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.transform import from_origin
from PIL import Image
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

WORK, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
BBOX = (27.05, -27.05, 29.25, -24.95)          # Gauteng plus a margin (lon/lat)
TS = 256


def tiles_for(zmin, zmax):
    for z in range(zmin, zmax + 1):
        for t in mercantile.tiles(*BBOX, zooms=z):
            yield t


class Mem:
    """A raster held in memory, so each tile warps only the cells it needs."""
    def __init__(self, path):
        with rasterio.open(path) as s:
            self.a = s.read(1); self.transform = s.transform; self.crs = s.crs; self.nodata = s.nodata if s.nodata is not None else 0
            self.dtype = self.a.dtype


def read_tile(src, t, resampling):
    """Reproject the source straight into one 256 px web-mercator tile."""
    b = mercantile.xy_bounds(t)
    dst = np.full((TS, TS), src.nodata, dtype=src.dtype)
    # crop the source to the tile's footprint first (plus a margin) so the warp stays small
    from rasterio.warp import transform_bounds
    l, bt, r, tp = transform_bounds('EPSG:3857', src.crs, b.left, b.bottom, b.right, b.top)
    inv = ~src.transform
    c0, r0 = inv * (l, tp); c1, r1 = inv * (r, bt)
    c0, r0 = max(int(c0) - 4, 0), max(int(r0) - 4, 0); c1, r1 = min(int(c1) + 5, src.a.shape[1]), min(int(r1) + 5, src.a.shape[0])
    if c1 <= c0 or r1 <= r0:
        return dst
    sub = src.a[r0:r1, c0:c1]
    st = src.transform * rasterio.Affine.translation(c0, r0)
    reproject(sub, dst, src_transform=st, src_crs=src.crs, src_nodata=src.nodata,
              dst_transform=from_origin(b.left, b.top, (b.right - b.left) / TS, (b.top - b.bottom) / TS),
              dst_crs='EPSG:3857', dst_nodata=src.nodata, resampling=resampling)
    return dst


def write_archive(path, entries, zmin, zmax, ttype, meta):
    entries.sort(key=lambda e: e[0])
    with open(path, 'wb') as f:
        w = Writer(f)
        for tid, data in entries:
            w.write_tile(tid, data)
        w.finalize({
            'tile_type': ttype, 'tile_compression': Compression.NONE,
            'min_zoom': zmin, 'max_zoom': zmax,
            'min_lon_e7': int(BBOX[0] * 1e7), 'min_lat_e7': int(BBOX[1] * 1e7),
            'max_lon_e7': int(BBOX[2] * 1e7), 'max_lat_e7': int(BBOX[3] * 1e7),
            'center_zoom': 9, 'center_lon_e7': int(28.0 * 1e7), 'center_lat_e7': int(-26.1 * 1e7),
        }, meta)
    return os.path.getsize(path)


def png(arr_rgba):
    bio = io.BytesIO(); Image.fromarray(arr_rgba, 'RGBA').save(bio, 'PNG', optimize=True); return bio.getvalue()


def webp_lossless(arr_rgb):
    bio = io.BytesIO(); Image.fromarray(arr_rgb, 'RGB').save(bio, 'WEBP', lossless=True, quality=100, method=4); return bio.getvalue()


def terrain(zmin=5, zmax=12):
    out = []
    src = Mem(os.path.join(WORK, 'dem30_utm35s.tif'))
    if True:
        for t in tiles_for(zmin, zmax):
            e = read_tile(src, t, Resampling.bilinear).astype(np.float64)
            e[e < -1000] = 1500.0                       # outside the DEM: a neutral Highveld height, keeps edges flat
            e = np.round(e * 4) / 4 + 32768.0           # quarter-metre steps keep the tiles small
            r = np.floor(e / 256); g = np.floor(e - r * 256); b = np.round((e - np.floor(e)) * 256)
            rgb = np.dstack([r, g, b]).clip(0, 255).astype(np.uint8)
            out.append((zxy_to_tileid(t.z, t.x, t.y), webp_lossless(rgb)))
    n = write_archive(os.path.join(OUT, 'terrain.pmtiles'), out, zmin, zmax, TileType.WEBP,
                      {'name': 'FABDEM terrain (terrarium)', 'attribution': 'FABDEM v1-2 (Hawker et al. 2022)', 'encoding': 'terrarium'})
    print('terrain tiles', len(out), round(n / 1e6, 1), 'MB')


# low-lying classes and JRC return periods, in the site's water blues
STYLE = {
    'low2': (0x2F, 0x6F, 0x9A, 190),   # higher: low-lying land, within h of the stream
    'low1': (0x8F, 0xB8, 0xD2, 165),   # lower: within 2h
    'rp10': (0x1F, 0x4E, 0x70, 210),
    'rp100': (0x3E, 0x7F, 0xAB, 185),
    'rp500': (0xA7, 0xC8, 0xDD, 160),
}


def flood(zmin=7, zmax=13):
    out = []
    srcs = {k: Mem(os.path.join(WORK, f)) for k, f in
            [('low', 'lowlying_class.tif'), ('rp10', 'jrc_RP10_30.tif'), ('rp100', 'jrc_RP100_30.tif'), ('rp500', 'jrc_RP500_30.tif')]}
    vrts = srcs
    for t in tiles_for(zmin, zmax):
        rs = Resampling.mode if t.z < 11 else Resampling.nearest
        low = read_tile(vrts['low'], t, rs)
        rp = {k: read_tile(vrts[k], t, Resampling.nearest if t.z >= 11 else Resampling.max) for k in ('rp10', 'rp100', 'rp500')}
        for layer, img in (('low', low), ('jrc', None)):
            rgba = np.zeros((TS, TS, 4), np.uint8)
            if layer == 'low':
                for v, key in ((1, 'low1'), (2, 'low2')):
                    rgba[img == v] = STYLE[key]
            else:
                for key in ('rp500', 'rp100', 'rp10'):
                    rgba[rp[key] > 0] = STYLE[key]
            if rgba[..., 3].any():
                out.append((layer, zxy_to_tileid(t.z, t.x, t.y), png(rgba)))
    for layer in ('low', 'jrc'):
        ents = [(tid, d) for l, tid, d in out if l == layer]
        n = write_archive(os.path.join(OUT, f'flood_{layer}.pmtiles'), ents, zmin, zmax, TileType.PNG,
                          {'name': f'FLOOD-LINE ZA {layer}'})
        print(layer, len(ents), round(n / 1e6, 1), 'MB')


if __name__ == '__main__':
    what = sys.argv[3] if len(sys.argv) > 3 else 'all'
    if what in ('all', 'terrain'):
        terrain()
    if what in ('all', 'flood'):
        flood()
