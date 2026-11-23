"""FLOOD-LINE ZA: download the inputs. Run in the QGIS Python console (QGIS 3.44, OSGeo4W).

Everything lands under ROOT/data/raw. The downloads are polite: FABDEM tiles are read out of the
1.75 GB zip with HTTP range requests (only the six Gauteng tiles, ~130 MB), the JRC maps are read as
windows, and CHIRPS is read one day at a time with a pause, backing off when the server says 429.

Buildings come from Overture Maps through DuckDB (pip install --user duckdb). DuckDB is given an
anonymous S3 secret on purpose: without it, it picks up any AWS credentials on the machine.
"""
import datetime, io, os, re, shutil, struct, threading, time, urllib.request, zipfile, zlib
import numpy as np
from osgeo import gdal

ROOT = os.path.expanduser(r'~/Documents/GIS Projects & Data/Profile map/flood-line-za')
RAW = os.path.join(ROOT, 'data', 'raw')
for d in ('fabdem', 'jrc', 'chirps', 'osm', 'buildings'):
    os.makedirs(os.path.join(RAW, d), exist_ok=True)
gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
UA = {'User-Agent': 'flood-line-za (portfolio research)'}

# ------------------------------------------------------------------ FABDEM: six 1-degree tiles out of the 10-degree zip
FABDEM_ZIP = 'https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/S30E020-S20E030_FABDEM_V1-2.zip'
TILES = [f'S{la}E0{lo}_FABDEM_V1-2.tif' for la in (26, 27) for lo in (27, 28, 29)]


class HttpFile(io.RawIOBase):
    """A seekable remote file, so zipfile can read the central directory without the whole zip."""
    def __init__(self, url):
        self.url, self.pos = url, 0
        self.size = int(urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=30).headers['Content-Length'])
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off); return self.pos
    def readinto(self, b):
        if self.pos >= self.size: return 0
        end = min(self.size, self.pos + len(b)) - 1
        d = rng(self.url, self.pos, end); b[:len(d)] = d; self.pos += len(d); return len(d)


def rng(url, a, b):
    for k in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'Range': f'bytes={a}-{b}'}), timeout=120).read()
        except Exception as e:
            err = e; time.sleep(3)
    raise err


def fabdem():
    z = zipfile.ZipFile(io.BufferedReader(HttpFile(FABDEM_ZIP), buffer_size=1 << 20))
    for info in z.infolist():
        if info.filename not in TILES:
            continue
        out = os.path.join(RAW, 'fabdem', info.filename)
        if os.path.exists(out) and os.path.getsize(out) == info.file_size:
            continue
        h = rng(FABDEM_ZIP, info.header_offset, info.header_offset + 29)
        nl, el = struct.unpack('<HH', h[26:30]); start = info.header_offset + 30 + nl + el
        parts, step = [], 8 << 20                                        # 8 MB ranges fetched in parallel
        chunks = [(start + k, min(start + info.compress_size, start + k + step) - 1) for k in range(0, info.compress_size, step)]
        res = [None] * len(chunks)
        ts = [threading.Thread(target=lambda i=i, c=c: res.__setitem__(i, rng(FABDEM_ZIP, *c))) for i, c in enumerate(chunks)]
        [t.start() for t in ts]; [t.join() for t in ts]
        data = b''.join(res)
        data = zlib.decompress(data, -15) if info.compress_type == 8 else data
        assert len(data) == info.file_size
        open(out, 'wb').write(data); print('FABDEM', info.filename)


# ------------------------------------------------------------------ JRC GloFAS river flood hazard v2.1 (tile 141, S20 E20)
def jrc():
    base = 'https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/'
    for rp in ('RP10', 'RP20', 'RP50', 'RP100', 'RP200', 'RP500'):
        gdal.Translate(os.path.join(RAW, 'jrc', f'{rp}_depth_gauteng.tif'), f'/vsicurl/{base}{rp}/ID141_S20_E20_{rp}_depth.tif',
                       projWin=[26.9, -24.9, 29.4, -27.2], creationOptions=['COMPRESS=DEFLATE'])
        print('JRC', rp)


# ------------------------------------------------------------------ OpenStreetMap (Geofabrik) and GHSL building height
def osm():
    urllib.request.urlretrieve('https://download.geofabrik.de/africa/south-africa-latest.osm.pbf', os.path.join(RAW, 'osm', 'south-africa-latest.osm.pbf'))


def ghsl_height():
    u = ('https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100/'
         'V1-0/tiles/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R13_C21.zip')
    z = os.path.join(RAW, 'buildings', 'ghs_built_h.zip'); urllib.request.urlretrieve(u, z)
    zipfile.ZipFile(z).extractall(os.path.join(RAW, 'buildings'))


# ------------------------------------------------------------------ Overture buildings for Johannesburg
def overture(release='2026-11-18.0'):
    import site, sys
    sys.path.append(site.getusersitepackages())
    import duckdb
    out = os.path.join(RAW, 'buildings', 'overture_jhb.parquet').replace('\\', '/')
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("CREATE OR REPLACE SECRET anon (TYPE s3, PROVIDER config, KEY_ID '', SECRET '', REGION 'us-west-2');")
    con.execute(f"""COPY (SELECT id, height, num_floors, class, subtype, sources[1].dataset AS src, ST_AsWKB(geometry) AS wkb
        FROM read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*', hive_partitioning=1)
        WHERE bbox.xmin > 27.69 AND bbox.xmax < 28.27 AND bbox.ymin > -26.56 AND bbox.ymax < -25.88) TO '{out}' (FORMAT PARQUET)""")


# ------------------------------------------------------------------ CHIRPS v3 daily rainfall, Gauteng window
X0, Y0, W, H = 4140, 1699, 46, 44             # columns/rows of the 0.05-degree global grid: lon 27.0-29.3, lat -24.95 to -27.15
START = datetime.date(1998, 1, 1)


def chirps_url(d, final_until=datetime.date(2026, 10, 31)):
    if d <= final_until:
        return f'/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/cogs/{d.year}/chirps-v3.0.sat.{d:%Y.%m.%d}.cog'
    return f'/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/{d.year}/chirps-v3.0.prelim.{d:%Y.%m.%d}.tif'


def chirps(end=datetime.date(2026, 11, 20), pause=5.0, path=None):
    """One day at a time. The CHC server rate-limits (HTTP 429); on a 429 this waits ten minutes."""
    path = path or os.path.join(RAW, 'chirps', 'chirps3_sat_daily_gauteng.npz')
    n = (end - START).days + 1
    cube = np.load(path)['rain'] if os.path.exists(path) else np.full((n, H, W), np.nan, np.float32)
    gdal.SetConfigOption('GDAL_HTTP_MAX_RETRY', '0')
    for i in np.where(np.isnan(cube[:, H // 2, W // 2]))[0]:
        d = START + datetime.timedelta(int(i))
        for _ in range(6):
            try:
                cube[i] = gdal.Open(chirps_url(d)).ReadAsArray(X0, Y0, W, H); break
            except Exception as e:
                time.sleep(600 if '429' in str(e) else 5)
        time.sleep(pause)
        if i % 100 == 0:
            np.savez_compressed(path, rain=cube, start=str(START), x0=27.0, y0=-24.95, res=0.05)
    np.savez_compressed(path, rain=cube, start=str(START), x0=27.0, y0=-24.95, res=0.05)


if __name__ == '__console__' or __name__ == '__main__':
    fabdem(); jrc(); osm(); ghsl_height(); overture(); chirps()
