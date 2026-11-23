"""FLOOD-LINE ZA: low-lying land along Gauteng's streams. Run in the QGIS Python console after running acquire_data.py.

1. FABDEM tiles -> one 30 m DEM in WGS 84 / UTM 35S (EPSG:32735)
2. GRASS r.watershed (single flow direction) -> flow accumulation and drainage direction
3. Height above the nearest stream (HAND): every cell follows its drainage path to the first cell that
   drains at least 1 km^2. Done with pointer jumping in NumPy: each pass replaces a cell's target with
   its target's target, so a path of n cells resolves in log2(n) passes.
4. Classes, on streams draining at least 5 km^2, with a depth that grows with the catchment
   (hydraulic geometry: depth ~ Q^0.4 and Q ~ A^0.75, so depth ~ A^0.3):
       h(A) = A^0.3 metres (A in km^2)    lowest class: HAND <= h      low class: HAND <= 2h
5. The municipality raster, the JRC maps on the same grid, and the ponding-hollow test that was dropped.

The GRASS provider ships with QGIS but may be switched off: qgis.utils.loadPlugin('grassprovider').
"""
import glob, os
import numpy as np
from osgeo import gdal, ogr
import processing

ROOT = os.path.expanduser(r'~/Documents/GIS Projects & Data/Profile map/flood-line-za')
RAW, W = os.path.join(ROOT, 'data', 'raw'), os.path.join(ROOT, 'data', 'work')
os.makedirs(W, exist_ok=True)
STREAM_KM2, CLASS_MIN_KM2, EXPONENT = 1.0, 5.0, 0.3
D8 = {1: (-1, 1), 2: (-1, 0), 3: (-1, -1), 4: (0, -1), 5: (1, -1), 6: (1, 0), 7: (1, 1), 8: (0, 1)}   # GRASS: counter-clockwise from NE


def save(name, arr, ref, nodata, dtype):
    opts = ['COMPRESS=DEFLATE', 'TILED=YES', 'PREDICTOR=3' if dtype == gdal.GDT_Float32 else 'PREDICTOR=2']
    ds = gdal.GetDriverByName('GTiff').Create(os.path.join(W, name), ref.RasterXSize, ref.RasterYSize, 1, dtype, opts)
    ds.SetGeoTransform(ref.GetGeoTransform()); ds.SetProjection(ref.GetProjection())
    b = ds.GetRasterBand(1)
    if nodata is not None: b.SetNoDataValue(nodata)
    b.WriteArray(arr); ds = None


def read(name):
    ds = gdal.Open(os.path.join(W, name)); return ds.GetRasterBand(1).ReadAsArray()


def dem():
    tiles = glob.glob(os.path.join(RAW, 'fabdem', '*.tif'))
    gdal.Warp(os.path.join(W, 'dem30_utm35s.tif'), tiles, dstSRS='EPSG:32735', xRes=30, yRes=30,
              outputBounds=[510000, 7010000, 730000, 7240000], targetAlignedPixels=True, resampleAlg='bilinear', dstNodata=-9999,
              creationOptions=['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES', 'BIGTIFF=IF_SAFER'], multithread=True)


def watershed():
    processing.run('grass:r.watershed', {
        'elevation': os.path.join(W, 'dem30_utm35s.tif'), 'threshold': int(STREAM_KM2 * 1e6 / 900), '-s': True, 'memory': 4000,
        'accumulation': os.path.join(W, 'acc.tif'), 'drainage': os.path.join(W, 'drain.tif'),
        'GRASS_REGION_CELLSIZE_PARAMETER': 30, 'GRASS_RASTER_FORMAT_OPT': 'COMPRESS=DEFLATE'})


def hand():
    ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif'))
    z = ref.GetRasterBand(1).ReadAsArray().astype(np.float32); nr, nc = z.shape
    dr = read('drain.tif').astype(np.int16)
    acc = np.abs(read('acc.tif').astype(np.float32))
    valid = z > -9000
    stream = (acc * 900 / 1e6 >= STREAM_KM2) & valid
    idx = np.arange(nr * nc, dtype=np.int32).reshape(nr, nc); ptr = idx.copy()
    rr, cc = np.indices((nr, nc), dtype=np.int32)
    for code, (a, b) in D8.items():
        m = (np.abs(dr) == code) & valid & ~stream
        r2, c2 = rr[m] + a, cc[m] + b
        ok = (r2 >= 0) & (r2 < nr) & (c2 >= 0) & (c2 < nc)
        ptr[m] = np.where(ok, r2 * nc + c2, idx[m])
    del rr, cc
    ptr = ptr.ravel()
    for _ in range(40):                                    # pointer jumping
        nxt = ptr[ptr]
        if np.array_equal(nxt, ptr): break
        ptr = nxt
    zf = z.ravel()
    reached = stream.ravel()[ptr] & valid.ravel()
    h = np.where(reached, zf - zf[ptr], np.nan).astype(np.float32).reshape(nr, nc)
    a = np.where(reached, acc.ravel()[ptr] * 900 / 1e6, np.nan).astype(np.float32).reshape(nr, nc)
    save('hand.tif', np.nan_to_num(h, nan=-9999), ref, -9999, gdal.GDT_Float32)
    save('anear_km2.tif', np.nan_to_num(a, nan=-9999), ref, -9999, gdal.GDT_Float32)
    print('cells not reaching a stream: %.2f%%' % (100 * np.mean(~reached[valid.ravel()])))


def municipalities():
    ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif'))
    gdal.VectorTranslate(os.path.join(W, 'muni_utm.gpkg'), os.path.join(ROOT, 'data', 'municipalities.geojson'),
                         dstSRS='EPSG:32735', reproject=True, format='GPKG', layerName='muni')
    src = ogr.Open(os.path.join(W, 'muni_utm.gpkg')); sl = src.GetLayer()
    mem = ogr.GetDriverByName('Memory').CreateDataSource('m'); ml = mem.CreateLayer('m', sl.GetSpatialRef(), ogr.wkbMultiPolygon)
    ml.CreateField(ogr.FieldDefn('mid', ogr.OFTInteger))
    for i, f in enumerate(sl):                              # 1 Johannesburg ... 9 Rand West City, in the GeoJSON's order
        g = ogr.Feature(ml.GetLayerDefn()); g.SetField('mid', i + 1); g.SetGeometry(f.GetGeometryRef().Clone()); ml.CreateFeature(g)
    ds = gdal.GetDriverByName('GTiff').Create(os.path.join(W, 'muni30.tif'), ref.RasterXSize, ref.RasterYSize, 1, gdal.GDT_Byte, ['COMPRESS=DEFLATE'])
    ds.SetGeoTransform(ref.GetGeoTransform()); ds.SetProjection(ref.GetProjection()); ds.GetRasterBand(1).SetNoDataValue(0)
    gdal.RasterizeLayer(ds, [1], ml, options=['ATTRIBUTE=mid']); ds = None


def classes():
    ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif'))
    h, a, muni = read('hand.tif'), read('anear_km2.tif'), read('muni30.tif')
    ok = (h > -9000) & (muni > 0) & (a >= CLASS_MIN_KM2)
    depth = np.power(np.maximum(a, 1), EXPONENT)
    c = np.zeros(h.shape, np.uint8); c[ok & (h <= 2 * depth)] = 1; c[ok & (h <= depth)] = 2
    save('lowlying_class.tif', c, ref, None, gdal.GDT_Byte)


def jrc_on_grid():
    ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif')); gt = ref.GetGeoTransform()
    b = [gt[0], gt[3] + gt[5] * ref.RasterYSize, gt[0] + gt[1] * ref.RasterXSize, gt[3]]
    for rp in ('RP10', 'RP100', 'RP500'):
        gdal.Warp(os.path.join(W, f'jrc_{rp}_30.tif'), os.path.join(RAW, 'jrc', f'{rp}_depth_gauteng.tif'), dstSRS='EPSG:32735',
                  outputBounds=b, xRes=30, yRes=30, resampleAlg='near', creationOptions=['COMPRESS=DEFLATE'])


def hollows():
    """Tested and dropped: hollows at least 0.5 m deep and 1 ha, from a Wang & Liu fill. None lies near a reported flood."""
    processing.run('native:fillsinkswangliu', {'INPUT': os.path.join(W, 'dem30_utm35s.tif'), 'BAND': 1, 'MIN_SLOPE': 0,
                                               'OUTPUT_FILLED_DEM': os.path.join(W, 'dem_filled.tif')})
    ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif'))
    z, f = read('dem30_utm35s.tif'), read('dem_filled.tif')
    m = ((np.where(z > -9000, f - z, 0)) >= 0.5).astype(np.uint8)
    save('hollows.tif', m, ref, 0, gdal.GDT_Byte)
    ds = gdal.Open(os.path.join(W, 'hollows.tif'), gdal.GA_Update); b = ds.GetRasterBand(1)
    gdal.SieveFilter(b, None, b, 11, 8); ds = None


if __name__ == '__console__' or __name__ == '__main__':
    dem(); watershed(); hand(); municipalities(); classes(); jrc_on_grid(); hollows()
