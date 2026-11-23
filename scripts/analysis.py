"""FLOOD-LINE ZA: growth, exposure and the check against reported floods. Run in the QGIS Python console after hazard.py.

Growth uses the first-built year from the companion project urban-edge-za (GHSL R2023A, 1975-2020,
a hectare counts once a fifth of it is under buildings); pop_2020.tif is GHS-POP 2020 from the same run.
Both are 100 m on GHSL's Mollweide grid and are put on the 30 m grid by nearest neighbour.

Writes data/work/growth_lowlying.json, _exposure.json, _facilities.json and data/export/* for web_*.py.
"""
import json, os
import numpy as np
from osgeo import gdal, ogr, osr

ROOT = os.path.expanduser(r'~/Documents/GIS Projects & Data/Profile map/flood-line-za')
UE = os.path.expanduser(r'~/Documents/GIS Projects & Data/Profile map/urban-edge-za/data/work')
RAW, W, E = (os.path.join(ROOT, 'data', d) for d in ('raw', 'work', 'export'))
os.makedirs(E, exist_ok=True)
NAMES = ['Johannesburg', 'Tshwane', 'Ekurhuleni', 'Emfuleni', 'Lesedi', 'Merafong City', 'Midvaal', 'Mogale City', 'Rand West City']
EPOCHS = [1975, 1980, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020]
ref = gdal.Open(os.path.join(W, 'dem30_utm35s.tif')); GT = ref.GetGeoTransform(); NR, NC = ref.RasterYSize, ref.RasterXSize
read = lambda n: gdal.Open(os.path.join(W, n)).ReadAsArray()


def to_grid(src, name):
    gdal.Warp(os.path.join(W, name), src, dstSRS='EPSG:32735', outputBounds=[GT[0], GT[3] + GT[5] * NR, GT[0] + GT[1] * NC, GT[3]],
              xRes=30, yRes=30, resampleAlg='near', creationOptions=['COMPRESS=DEFLATE'])


def growth():
    to_grid(os.path.join(UE, 'first_built.tif'), 'first_built_30.tif')
    fb, cls, muni = read('first_built_30.tif'), read('lowlying_class.tif'), read('muni30.tif')
    out = {}
    for i, nm in enumerate(['Gauteng'] + NAMES):
        mm = (muni > 0) if i == 0 else (muni == i)
        row = {'land_km2': round(mm.sum() * 9e-4, 1), 'lowlying_km2': round((mm & (cls >= 1)).sum() * 9e-4, 1),
               'lowlying_hi_km2': round((mm & (cls == 2)).sum() * 9e-4, 1), 'built': {}, 'built_low': {}, 'built_low_hi': {}}
        for e in EPOCHS:
            b = mm & (fb > 0) & (fb <= e)
            row['built'][e] = round(b.sum() * 9e-4, 1)
            row['built_low'][e] = round((b & (cls >= 1)).sum() * 9e-4, 2)
            row['built_low_hi'][e] = round((b & (cls == 2)).sum() * 9e-4, 2)
        out[nm] = row
    json.dump(out, open(os.path.join(W, 'growth_lowlying.json'), 'w'), indent=1)
    # polygons of built-up land on low-lying ground, valued epoch*10 + class
    ep = np.zeros(fb.shape, np.uint8)
    for i, y in enumerate(EPOCHS): ep[fb == y] = i + 1
    ep[(cls == 0) | (muni == 0)] = 0
    val = ep * 10 + np.where(ep > 0, cls, 0).astype(np.uint8)
    mem = gdal.GetDriverByName('MEM').Create('', NC, NR, 1, gdal.GDT_Byte); mem.SetGeoTransform(GT); mem.SetProjection(ref.GetProjection())
    mem.GetRasterBand(1).WriteArray(val); mem.GetRasterBand(1).SetNoDataValue(0)
    srs = osr.SpatialReference(); srs.ImportFromEPSG(32735)
    vd = ogr.GetDriverByName('Memory').CreateDataSource('v'); vl = vd.CreateLayer('g', srs, ogr.wkbPolygon); vl.CreateField(ogr.FieldDefn('v', ogr.OFTInteger))
    gdal.Polygonize(mem.GetRasterBand(1), mem.GetRasterBand(1), vl, 0, ['8CONNECTED=8'])
    gdal.VectorTranslate(os.path.join(E, 'growth_lowlying.geojson'), vd, format='GeoJSON', dstSRS='EPSG:4326', reproject=True, simplifyTolerance=10,
                         layerCreationOptions=['COORDINATE_PRECISION=5', 'RFC7946=YES'],
                         SQLStatement='SELECT CAST(v/10 AS INTEGER) AS e, v - CAST(v/10 AS INTEGER)*10 AS c, * FROM g', SQLDialect='SQLITE')


def px(x, y):
    c, r = int((x - GT[0]) / GT[1]), int((y - GT[3]) / GT[5])
    return (r, c) if 0 <= r < NR and 0 <= c < NC else None


def exposure():
    cls, muni = read('lowlying_class.tif'), read('muni30.tif')
    # people (GHS-POP 2020, 100 m cells -> 30 m cells carry 9% of a cell each)
    to_grid(os.path.join(UE, 'pop_2020.tif'), 'pop_2020_30.tif')
    pop = np.clip(read('pop_2020_30.tif').astype(np.float64), 0, None) * 0.09
    res = {'Gauteng': [pop[muni > 0].sum(), pop[(muni > 0) & (cls == 2)].sum(), pop[(muni > 0) & (cls >= 1)].sum()]}
    for i, nm in enumerate(NAMES):
        res[nm] = [pop[muni == i + 1].sum(), pop[(muni == i + 1) & (cls == 2)].sum(), pop[(muni == i + 1) & (cls >= 1)].sum()]
    # roads in Johannesburg, by class, measured along 15 m segments
    pbf = os.path.join(RAW, 'osm', 'south-africa-latest.osm.pbf'); gpkg = os.path.join(RAW, 'osm', 'jhb_osm.gpkg')
    bb = [27.69, -26.56, 28.27, -25.88]
    gdal.VectorTranslate(gpkg, pbf, format='GPKG', layers=['lines'], layerName='roads', spatFilter=bb, where='highway IS NOT NULL',
                         accessMode='overwrite', geometryType='LINESTRING', dstSRS='EPSG:32735', reproject=True)
    tag = lambda k, v: f'other_tags LIKE \'%"{k}"=>"{v}"%\''
    fac_where = ' OR '.join([tag('amenity', 'school'), tag('amenity', 'hospital'), tag('amenity', 'clinic'), tag('healthcare', 'clinic'), tag('healthcare', 'hospital')])
    gdal.VectorTranslate(gpkg, pbf, format='GPKG', layers=['points'], layerName='fac_pts', spatFilter=bb, where=fac_where, accessMode='append', dstSRS='EPSG:32735', reproject=True)
    gdal.VectorTranslate(gpkg, pbf, format='GPKG', layers=['multipolygons'], layerName='fac_polys', spatFilter=bb,
                         where="amenity IN ('school','hospital','clinic') OR " + tag('healthcare', 'clinic') + ' OR ' + tag('healthcare', 'hospital'),
                         accessMode='append', dstSRS='EPSG:32735', reproject=True)
    ds = ogr.Open(gpkg); km = {0: 0.0, 1: 0.0, 2: 0.0}
    skip = {'footway', 'path', 'steps', 'cycleway', 'pedestrian', 'bridleway', 'corridor', 'proposed', 'construction', 'platform', 'elevator'}
    for f in ds.GetLayerByName('roads'):
        if f.GetField('highway') in skip: continue
        g = f.GetGeometryRef().Clone(); g.Segmentize(15); pts = g.GetPoints()
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            p = px((x0 + x1) / 2, (y0 + y1) / 2)
            if p and muni[p] == 1: km[int(cls[p])] += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / 1000
    fac = []
    for lname in ('fac_pts', 'fac_polys'):
        lyr = ds.GetLayerByName(lname); defn = lyr.GetLayerDefn()
        for f in lyr:
            tags = f.GetField('other_tags') or '' if defn.GetFieldIndex('other_tags') >= 0 else ''
            am = f.GetField('amenity') if defn.GetFieldIndex('amenity') >= 0 else None
            kind = next((k for k in ('school', 'hospital', 'clinic') if am == k or f'"amenity"=>"{k}"' in tags or f'"healthcare"=>"{k}"' in tags), None)
            if not kind: continue
            c = f.GetGeometryRef().Centroid(); p = px(c.GetX(), c.GetY())
            if not p or muni[p] != 1: continue
            r, cc = p
            fac.append((kind, f.GetField('name'), c.GetX(), c.GetY(), int(cls[max(r - 1, 0):r + 2, max(cc - 1, 0):cc + 2].max())))
    json.dump({'roads_km': {'all': km}, 'pop': res}, open(os.path.join(W, '_exposure.json'), 'w'))
    s = osr.SpatialReference(); s.ImportFromEPSG(32735); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    s2 = osr.SpatialReference(); s2.ImportFromEPSG(4326); s2.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, s2)
    feats = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [round(v, 6) for v in tr.TransformPoint(x, y)[:2]]},
              'properties': {'k': k, 'n': n, 'c': c}} for k, n, x, y, c in fac]
    json.dump({'type': 'FeatureCollection', 'features': feats}, open(os.path.join(E, 'facilities_jhb.geojson'), 'w'))


def buildings():
    """Johannesburg buildings on low-lying land, with GHSL average building height (ANBH, 100 m) at each centroid."""
    import site, sys
    sys.path.append(site.getusersitepackages())
    import duckdb, pandas as pd
    cls, muni = read('lowlying_class.tif'), read('muni30.tif')
    con = duckdb.connect(); con.execute('LOAD spatial;')
    p = os.path.join(RAW, 'buildings', 'overture_jhb.parquet').replace('\\', '/')
    d = con.execute(f"""SELECT id,
          ST_X(ST_Transform(ST_Centroid(ST_GeomFromWKB(wkb)),'EPSG:4326','EPSG:32735',always_xy:=true)) AS ux,
          ST_Y(ST_Transform(ST_Centroid(ST_GeomFromWKB(wkb)),'EPSG:4326','EPSG:32735',always_xy:=true)) AS uy,
          ST_Area(ST_Transform(ST_GeomFromWKB(wkb),'EPSG:4326','EPSG:32735',always_xy:=true)) AS area FROM read_parquet('{p}')""").fetchnumpy()
    col = np.clip(((d['ux'] - GT[0]) / GT[1]).astype(int), 0, NC - 1); row = np.clip(((d['uy'] - GT[3]) / GT[5]).astype(int), 0, NR - 1)
    m, c = muni[row, col], cls[row, col]
    jb = m == 1
    print('Johannesburg buildings', jb.sum(), 'lowest', (jb & (c == 2)).sum(), 'low', (jb & (c == 1)).sum(),
          'median m2 all %.0f lowest %.0f' % (np.median(d['area'][jb]), np.median(d['area'][jb & (c == 2)])))
    sel = jb & (c >= 1)
    hds = gdal.Open(os.path.join(RAW, 'buildings', 'GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R13_C21.tif'))
    hg = hds.GetGeoTransform(); ha = hds.GetRasterBand(1).ReadAsArray()
    s = osr.SpatialReference(); s.ImportFromEPSG(32735); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    mo = osr.SpatialReference(); mo.ImportFromProj4('+proj=moll +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'); mo.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    pts = osr.CoordinateTransformation(s, mo).TransformPoints(list(zip(d['ux'][sel].tolist(), d['uy'][sel].tolist())))
    hh = [float(ha[int((y - hg[3]) / hg[5]), int((x - hg[0]) / hg[1])]) for x, y, _ in pts]
    con.register('sel', pd.DataFrame({'id': d['id'][sel], 'c': c[sel].astype(int), 'h': np.round(hh, 1)}))
    o = os.path.join(E, 'buildings_exposed.geojsonseq').replace('\\', '/')
    con.execute(f"""COPY (SELECT s.c AS c, s.h AS h, b.height AS oh, b.num_floors AS nf, ST_ReducePrecision(ST_GeomFromWKB(b.wkb), 0.000001) AS geom
                    FROM read_parquet('{p}') b JOIN sel s USING (id)) TO '{o}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')""")


def check():
    """Reported flood places against built-up Johannesburg: how often is the lowest class within ~500 m,
    and a stream draining 50 km^2 or more within 1 km?"""
    cls, muni, fb = read('lowlying_class.tif'), read('muni30.tif'), read('first_built_30.tif')
    acc = np.abs(read('acc.tif').astype(np.float32)) * 9e-4
    s = osr.SpatialReference(); s.ImportFromEPSG(4326); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    u = osr.SpatialReference(); u.ImportFromEPSG(32735); tr = osr.CoordinateTransformation(s, u)
    inc = json.load(open(os.path.join(ROOT, 'data', 'incidents.geojson'), encoding='utf8'))['features']
    P = np.array([px(*tr.TransformPoint(*f['geometry']['coordinates'])[:2]) for f in inc])
    precise = np.array([f['properties']['precision'] in ('road', 'river', 'facility', 'settlement') for f in inc])
    rng = np.random.default_rng(1); jb = np.argwhere((muni == 1) & (fb > 0)); base = jb[rng.choice(len(jb), 4000, replace=False)]
    near = lambda a, pts, r: np.array([a[max(y - r, 0):y + r + 1, max(x - r, 0):x + r + 1].max() for y, x in pts])
    hi = (cls == 2).astype(np.uint8)
    print('lowest class within 510 m: precise %.0f%%, all %.0f%%, built-up Johannesburg %.0f%%' % (
        100 * near(hi, P[precise], 17).mean(), 100 * near(hi, P, 17).mean(), 100 * near(hi, base, 17).mean()))
    drv = gdal.GetDriverByName('MEM')
    sd = drv.Create('', NC, NR, 1, gdal.GDT_Byte); sd.SetGeoTransform(GT); sd.GetRasterBand(1).WriteArray((acc >= 50).astype(np.uint8))
    pd_ = drv.Create('', NC, NR, 1, gdal.GDT_Float32); pd_.SetGeoTransform(GT)
    gdal.ComputeProximity(sd.GetRasterBand(1), pd_.GetRasterBand(1), ['VALUES=1', 'DISTUNITS=GEO'])
    dist = pd_.GetRasterBand(1).ReadAsArray()
    f = lambda pts: 100 * np.mean(dist[pts[:, 0], pts[:, 1]] <= 1000)
    print('stream >= 50 km2 within 1 km: precise %.0f%%, all %.0f%%, built-up Johannesburg %.0f%%' % (f(P[precise]), f(P), f(base)))
    jrc = read('jrc_RP100_30.tif') > 0
    print('places within ~500 m of JRC 1-in-100:', int(near(jrc.astype(np.uint8), P, 17).sum()), 'of', len(P))
    hol = read('hollows.tif') > 0
    print('places within ~500 m of a ponding hollow:', int(near(hol.astype(np.uint8), P, 17).sum()))


if __name__ == '__console__' or __name__ == '__main__':
    growth(); exposure(); buildings(); check()
