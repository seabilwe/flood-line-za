"""Assemble the small JSON/GeoJSON files that the FLOOD-LINE ZA page reads.

Usage: python web_data.py <work_dir> <export_dir> <incidents.geojson> <municipalities.geojson> <out_dir> [rain.json]
"""
import json, os, sys

WORK, EXP, INC, MUN, OUT = sys.argv[1:6]
RAIN = sys.argv[6] if len(sys.argv) > 6 else None
os.makedirs(OUT, exist_ok=True)

EVENTS = [
    ("2016-11-09", "9 Nov 2016", "Flash floods across northern Johannesburg; the Jukskei floods Setswetla in Alexandra and cars are trapped on the N3."),
    ("2018-03-22", "22–23 Mar 2018", "Two days of heavy rain; shacks washed away in Diepsloot, evacuations in Soweto and Tshwane."),
    ("2020-02-08", "8 Feb 2020", "The Jukskei and Klipspruit burst their banks; 200 households in Setswetla moved to shelters; two hospitals flooded."),
    ("2020-02-28", "28 Feb 2020", "A school bus is caught by flash floods on Vundla Drive, Soweto."),
    ("2021-12-27", "27 Dec 2021", "Flash floods next to the Jukskei in Alexandra; about 300 households flooded in Majaneng, Hammanskraal."),
    ("2022-12-03", "3 Dec 2022", "A flash flood on the Jukskei sweeps away people at a baptism near Bramley Park."),
    ("2022-12-09", "8–9 Dec 2022", "The Klipspruit floods Kliptown; flooding across Soweto, Lenasia and Orange Farm. A provincial disaster is declared in January."),
    ("2023-02-14", "14 Feb 2023", "Flash flooding leaves about 60 people homeless in Diepsloot."),
    ("2024-01-27", "27 Jan 2024", "Homes flooded in Kliptown's Sidikidikini settlement, Dlamini, Molapo and Eldorado Park."),
    ("2025-01-04", "4–5 Jan 2025", "Homes flooded and roads damaged in Orange Farm."),
    ("2025-04-06", "Early Apr 2025", "After weeks of rain, homes on the Jukskei embankment in Setswetla crack and collapse."),
    ("2026-09-06", "6 Sep 2026", "Vehicles trapped by floodwater in Doornfontein."),
    ("2026-11-17", "17 Nov 2026", "Storm floods roads and homes across Soweto, Alexandra and Slovo Park; a man dies and a child is reported missing in Dobsonville."),
]
ALIAS = {"2018-03-23": "2018-03-22"}

inc = json.load(open(INC, encoding="utf-8"))
ev_ids = {d: i for i, (d, _, _) in enumerate(EVENTS)}
for f in inc["features"]:
    p = f["properties"]
    d = ALIAS.get(p["event_date"], p["event_date"])
    p["ev"] = ev_ids[d]
    f["geometry"]["coordinates"] = [round(c, 5) for c in f["geometry"]["coordinates"]]
json.dump(inc, open(os.path.join(OUT, "incidents.geojson"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

growth = json.load(open(os.path.join(WORK, "growth_lowlying.json")))
expo = json.load(open(os.path.join(WORK, "_exposure.json")))
fac = json.load(open(os.path.join(EXP, "facilities_jhb.geojson")))
for f in fac["features"]:
    f["properties"].pop("n", None) if not f["properties"].get("n") else None
json.dump(fac, open(os.path.join(OUT, "facilities.geojson"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

mun = json.load(open(MUN))
for f in mun["features"]:
    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 4), round(c[1], 4)]
    f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
json.dump(mun, open(os.path.join(OUT, "municipalities.geojson"), "w"), separators=(",", ":"))

fc = {}
for f in fac["features"]:
    k = (f["properties"]["k"], f["properties"]["c"]); fc[k] = fc.get(k, 0) + 1
facs = {k: {"total": sum(v for (kk, c), v in fc.items() if kk == k), "hi": fc.get((k, 2), 0), "lo": fc.get((k, 1), 0)} for k in ("school", "clinic", "hospital")}

areas = {}
for name, g in growth.items():
    pop = expo["pop"].get(name)
    areas[name] = {
        "land_km2": g["land_km2"], "low_km2": g["lowlying_km2"], "low_hi_km2": g["lowlying_hi_km2"],
        "built": {k: v for k, v in g["built"].items()}, "built_low": g["built_low"], "built_low_hi": g["built_low_hi"],
        "pop": round(pop[0]) if pop else None, "pop_low": round(pop[2]) if pop else None, "pop_low_hi": round(pop[1]) if pop else None,
    }

data = {
    "years": [1975, 1980, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020],
    "events": [{"date": d, "label": l, "text": t} for d, l, t in EVENTS],
    "areas": areas,
    "jhb": {
        "buildings": 1375088, "bld_hi": 30198, "bld_lo": 37894, "bld_med_m2": 45, "bld_hi_med_m2": 36,
        "roads_km": round(sum(expo["roads_km"]["all"].values())),
        "roads_hi_km": round(expo["roads_km"]["all"]["2"]), "roads_lo_km": round(expo["roads_km"]["all"]["1"]),
        "facilities": facs,
    },
    "check": {
        "incidents": len(inc["features"]), "precise": sum(1 for f in inc["features"] if f["properties"]["precision"] in ("road", "river", "facility", "settlement")),
        "near500_precise_pct": 70, "near500_all_pct": 52, "near500_base_pct": 35,
        "near_jrc100_500m": 6, "big_stream_1km_all_pct": 35, "big_stream_1km_base_pct": 16, "big_stream_1km_precise_pct": 57,
        "hollows_near_pct": 0,
    },
}
# wards (MDB 2020): residents on low-lying land, GHS-POP 2020
WARDS = os.path.join(EXP, "wards2020_gp.geojson")
if os.path.exists(WARDS):
    st = json.load(open(os.path.join(WORK, "_wards.json")))
    short = lambda m: next((n for n in ["Johannesburg", "Tshwane", "Ekurhuleni", "Emfuleni", "Lesedi", "Merafong City", "Midvaal", "Mogale City", "Rand West City"] if n in m), m)
    wd = json.load(open(WARDS))
    for f in wd["features"]:
        w = st[f["properties"]["WardID"]]
        f["properties"] = {"id": f["properties"]["WardID"], "no": w["no"], "mun": short(w["mun"]), "pop": w["pop"], "low": w["pop_low"], "hi": w["pop_hi"],
                           "s": round(100 * w["pop_low"] / w["pop"], 1) if w["pop"] >= 500 else None}
    json.dump(wd, open(os.path.join(OUT, "wards.geojson"), "w"), separators=(",", ":"))
    shares = [f["properties"]["s"] for f in wd["features"] if f["properties"]["s"] is not None]
    data_wards = {"n": len(wd["features"]), "over20": sum(1 for v in shares if v > 20), "over10": sum(1 for v in shares if v > 10)}
    print("wards", data_wards)
else:
    data_wards = None
data["wards"] = data_wards
if RAIN and os.path.exists(RAIN):
    data["rain"] = json.load(open(RAIN))
json.dump(data, open(os.path.join(OUT, "data.json"), "w"), separators=(",", ":"))
print("events", len(EVENTS), "incidents", len(inc["features"]), "facilities", facs)
