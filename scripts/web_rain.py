"""Rainfall behind each reported flood, from the CHIRPS v3 daily cube (Gauteng window, 0.05 degrees).

For each event: the Johannesburg-average daily rain from 21 days before to 7 days after, the wettest day
within a day of the report, the wettest three days ending within a day of it, and how that wettest day
ranks among all rainy-season days (October to April) in the record.

Usage: python web_rain.py <chirps.npz> <municipalities.geojson> <data.json> <out rain.json> [gee.csv]

gee.csv (optional) is the same Johannesburg average from Earth Engine (UCSB-CHC/CHIRPS/V3/DAILY_SAT,
gee/chirps3_jhb_daily.js); where it has a day, it is used, and the downloaded cube fills the days after it.
"""
import datetime, json, sys
import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape

NPZ, MUN, DATA, OUT = sys.argv[1:5]
GEE = sys.argv[5] if len(sys.argv) > 5 else None
z = np.load(NPZ)
rain = z['rain']; x0 = float(z['x0']); y0 = float(z['y0']); res = float(z['res'])
start = datetime.date.fromisoformat(str(z['start']))
n, H, W = rain.shape
mun = json.load(open(MUN))
jhb = [shape(f['geometry']) for f in mun['features'] if f['properties']['name'] == 'Johannesburg'][0]
mask = rasterize([(jhb, 1)], out_shape=(H, W), transform=from_origin(x0, y0, res, res), all_touched=False).astype(bool)
rain = np.where(rain < 0, np.nan, rain)
jmean = np.nanmean(rain[:, mask], axis=1)          # Johannesburg average, mm a day (NaN where the day is missing)
have = ~np.isnan(rain[:, mask]).all(axis=1)
jmean[~have] = np.nan
dates = [start + datetime.timedelta(i) for i in range(n)]
if GEE:
    import csv
    g = {r['date']: float(r['mm']) for r in csv.DictReader(open(GEE)) if r['mm']}
    for i, d in enumerate(dates):
        if d.isoformat() in g:
            jmean[i] = g[d.isoformat()]; have[i] = True
    print('Earth Engine days used:', sum(1 for d in dates if d.isoformat() in g), 'last', max(g))
season = np.array([d.month in (10, 11, 12, 1, 2, 3, 4) for d in dates])
base = jmean[season & have]

events = json.load(open(DATA))['events']
out = []
for e in events:
    d = datetime.date.fromisoformat(e['date']); i = (d - start).days
    lo, hi = i - 21, min(i + 7, n - 1)
    s = [None if np.isnan(v) else round(float(v), 1) for v in jmean[lo:hi + 1]]
    near = [jmean[k] for k in (i - 1, i, i + 1) if 0 <= k < n and not np.isnan(jmean[k])]
    peak = max(near) if near else None
    three = []
    for k in (i - 1, i, i + 1):
        w = jmean[k - 2:k + 1]
        if len(w) == 3 and not np.isnan(w).any():
            three.append(float(w.sum()))
    rec = {'days': [(dates[k]).isoformat() for k in range(lo, hi + 1)], 'series': s, 'offset': i - lo,
           'day': round(float(peak), 1) if peak is not None else None,
           'd3': round(max(three), 1) if three else None,
           'pct': round(float(100 * np.mean(base < peak)), 1) if peak is not None and len(base) else None,
           'complete': all(v is not None for v in s)}
    out.append(rec)
    print(e['date'], rec['day'], rec['d3'], rec['pct'], 'complete' if rec['complete'] else 'gaps')
json.dump({'events': out, 'baseline_days': int(len(base)), 'record': [str(dates[int(np.argmax(have))]), str(dates[n - 1 - int(np.argmax(have[::-1]))])]},
          open(OUT, 'w'), separators=(',', ':'))
