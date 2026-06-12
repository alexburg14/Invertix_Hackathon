#!/usr/bin/env python3
"""Collapse all data layers onto one row per subsea candidate site (ocean grid cell).

Candidate sites = ETOPO bathymetry cells in the 20-120 m depth band (Natick
operating range). Power is drawn through the nearest onshore PyPSA bus (the
"landing point"), so price/carbon/headroom come from that bus's country.

Output: web/sites.geojson + web/cables.json (Europe-cropped overlay). ~1 min.
Run after scripts/fetch_data.sh.
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "web" / "sites.geojson"
OUT_CABLES = ROOT / "web" / "cables.json"

EARTH_KM = 6371.0
DEPTH_MIN, DEPTH_MAX = 20.0, 120.0   # m below sea level (Natick-style band)
MAX_GRID_KM = 150.0                  # export-cable practicality (hard filter)
PPA_RADIUS_KM = 50.0
CABLE_DENSIFY_KM = 10.0              # cable vertices are sparse (~19/cable)
BBOX = (35.0, -11.0, 72.0, 32.0)     # lat_min, lon_min, lat_max, lon_max

# Ember keys on ISO3, PyPSA buses on ISO2.
ISO3_TO_ISO2 = {
    "ALB": "AL", "AUT": "AT", "BEL": "BE", "BGR": "BG", "BIH": "BA",
    "CHE": "CH", "CZE": "CZ", "DEU": "DE", "DNK": "DK", "ESP": "ES",
    "EST": "EE", "FIN": "FI", "FRA": "FR", "GBR": "GB", "GRC": "GR",
    "HRV": "HR", "HUN": "HU", "IRL": "IE", "ITA": "IT", "LTU": "LT",
    "LUX": "LU", "LVA": "LV", "MDA": "MD", "MKD": "MK", "MNE": "ME",
    "NLD": "NL", "NOR": "NO", "POL": "PL", "PRT": "PT", "ROU": "RO",
    "SRB": "RS", "SVK": "SK", "SVN": "SI", "SWE": "SE", "UKR": "UA",
    "XKX": "XK",
}


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine; lat/lon in degrees, broadcasting-friendly."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def nearest(slat, slon, tlat, tlon, chunk=1000):
    """Chunked nearest-neighbor: returns (min_dist_km, argmin) per site."""
    dist = np.empty(len(slat))
    idx = np.empty(len(slat), dtype=int)
    for i in range(0, len(slat), chunk):
        sl = slice(i, i + chunk)
        d = haversine_km(slat[sl, None], slon[sl, None], tlat[None], tlon[None])
        dist[sl], idx[sl] = d.min(axis=1), d.argmin(axis=1)
    return dist, idx


def osm_points(path, capacity_tag=None):
    """OSM Overpass JSON -> DataFrame of (lat, lon, name[, mw])."""
    rows = []
    for e in json.loads(path.read_text())["elements"]:
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None:
            continue
        tags = e.get("tags", {})
        row = {"lat": lat, "lon": lon, "name": tags.get("name", "")}
        if capacity_tag:
            m = re.match(r"([\d.]+)\s*MW", tags.get(capacity_tag, ""), re.I)
            row["mw"] = float(m.group(1)) if m else np.nan
            row["source"] = tags.get("plant:source", "")
        rows.append(row)
    return pd.DataFrame(rows)


def cable_points():
    """Cable vertices densified to ~CABLE_DENSIFY_KM spacing, Europe only.

    Also writes the Europe-cropped cable GeoJSON for the map overlay.
    Returns (lat, lon, cable_index, names) arrays.
    """
    g = json.loads((DATA / "sea/cables.json").read_text())
    lat0, lon0, lat1, lon1 = BBOX
    pad = 3.0  # keep cables just outside the bbox: they still serve the region
    lats, lons, cidx, names, keep = [], [], [], [], []
    for f in g["features"]:
        in_bbox = False
        for line in f["geometry"]["coordinates"]:
            arr = np.asarray(line, dtype=float)
            m = ((arr[:, 1] >= lat0 - pad) & (arr[:, 1] <= lat1 + pad)
                 & (arr[:, 0] >= lon0 - pad) & (arr[:, 0] <= lon1 + pad))
            if not m.any():
                continue
            in_bbox = True
            for (xa, ya), (xb, yb) in zip(arr[:-1], arr[1:]):
                if not (lat0 - pad <= ya <= lat1 + pad and lon0 - pad <= xa <= lon1 + pad):
                    continue
                seg_km = haversine_km(ya, xa, yb, xb)
                n = max(int(seg_km // CABLE_DENSIFY_KM), 1)
                t = np.linspace(0, 1, n + 1)[:-1]
                lons.extend(xa + t * (xb - xa))
                lats.extend(ya + t * (yb - ya))
                cidx.extend([len(names)] * len(t))
        if in_bbox:
            names.append(f["properties"]["name"])
            keep.append(f)
    OUT_CABLES.write_text(json.dumps({"type": "FeatureCollection", "features": keep}))
    return np.asarray(lats), np.asarray(lons), np.asarray(cidx), names


def main():
    # --- 1. Candidate cells: bathymetry in the operating depth band ---
    bathy = pd.read_csv(DATA / "sea/bathymetry.csv", skiprows=[1])
    depth = -bathy.altitude
    sites = bathy[(depth >= DEPTH_MIN) & (depth <= DEPTH_MAX)].rename(
        columns={"latitude": "lat", "longitude": "lon"}
    ).copy()
    sites["depth_m"] = -sites.pop("altitude")
    print(f"{len(sites)} cells in {DEPTH_MIN:.0f}-{DEPTH_MAX:.0f} m band")
    slat, slon = sites.lat.to_numpy(), sites.lon.to_numpy()

    # --- 2. Cooling: nearest OISST cell, mean + warmest-month SST ---
    sst = pd.read_csv(DATA / "sea/sst_monthly.csv", skiprows=[1]).dropna(subset=["sst"])
    agg = sst.groupby(["latitude", "longitude"]).sst.agg(["mean", "max"]).reset_index()
    _, j = nearest(slat, slon, agg.latitude.to_numpy(), agg.longitude.to_numpy())
    sites["sst_mean_c"] = agg["mean"].to_numpy()[j].round(1)
    sites["sst_max_c"] = agg["max"].to_numpy()[j].round(1)

    # --- 3. Grid landing: nearest onshore PyPSA bus (+ its headroom, as v1) ---
    buses = pd.read_csv(DATA / "pypsa/buses.csv", quotechar="'")
    buses = buses[(buses.under_construction == "f") & (buses.dc == "f")]
    lines = pd.read_csv(
        DATA / "pypsa/lines.csv", quotechar="'",
        usecols=["bus0", "bus1", "s_nom", "under_construction"],
    )
    lines = lines[lines.under_construction == "f"]
    ends = pd.concat([
        lines[["bus0", "s_nom"]].rename(columns={"bus0": "bus_id"}),
        lines[["bus1", "s_nom"]].rename(columns={"bus1": "bus_id"}),
    ])
    cap = ends.groupby("bus_id").s_nom.sum()
    buses["headroom_mva"] = buses.bus_id.map(cap).fillna(0)
    buses = buses[buses.headroom_mva > 0].reset_index(drop=True)
    d, j = nearest(slat, slon, buses.y.to_numpy(), buses.x.to_numpy())
    sites["dist_grid_km"] = d.round(1)
    sites["landing_country"] = buses.country.to_numpy()[j]
    sites["landing_voltage"] = buses.voltage.to_numpy()[j]
    sites["landing_headroom_mva"] = buses.headroom_mva.to_numpy()[j].round(0)
    sites = sites[sites.dist_grid_km <= MAX_GRID_KM]
    slat, slon = sites.lat.to_numpy(), sites.lon.to_numpy()
    print(f"{len(sites)} cells within {MAX_GRID_KM:.0f} km of an onshore bus")

    # --- 4. Price + carbon: Ember, keyed on the landing country ---
    prices = pd.read_csv(DATA / "ember/ember_prices_monthly.csv")
    prices["iso2"] = prices["ISO3 Code"].map(ISO3_TO_ISO2)
    prices["Date"] = pd.to_datetime(prices.Date)
    recent = prices[prices.Date > prices.Date.max() - pd.DateOffset(months=12)]
    price_by_c = recent.groupby("iso2")["Price (EUR/MWhe)"].mean().round(1)
    sites["price_eur_mwh"] = sites.landing_country.map(price_by_c)

    ember = pd.read_csv(
        DATA / "ember/ember_yearly.csv",
        usecols=["ISO 3 code", "Year", "Category", "Variable", "Unit", "Value"],
    )
    ember["iso2"] = ember["ISO 3 code"].map(ISO3_TO_ISO2)

    def latest(df):
        return df.sort_values("Year").groupby("iso2").Value.last()

    ci = ember[(ember.Variable == "CO2 intensity") & (ember.Unit == "gCO2/kWh")]
    sites["gco2_kwh"] = sites.landing_country.map(latest(ci)).round(0)
    clean = ember[
        (ember.Category == "Electricity generation")
        & (ember.Variable == "Clean") & (ember.Unit == "%")
    ]
    sites["clean_share_pct"] = sites.landing_country.map(latest(clean)).round(1)

    # --- 5. Fiber: distance to nearest submarine cable (densified vertices) ---
    clat, clon, cidx, cnames = cable_points()
    print(f"{len(cnames)} cables in region, {len(clat)} densified vertices")
    d, j = nearest(slat, slon, clat, clon, chunk=500)
    sites["dist_cable_km"] = d.round(1)
    sites["nearest_cable"] = [cnames[cidx[k]] for k in j]

    # --- 6. PPA: wind capacity within PPA_RADIUS_KM (OSM; incl. offshore farms) ---
    plants = osm_points(DATA / "osm_power_plants.json", "plant:output:electricity")
    wind = plants[plants.source.str.contains("wind", case=False, na=False)].copy()
    wind["mw_est"] = wind.mw.fillna(wind.mw.median())
    plat, plon, pmw = (wind[c].to_numpy() for c in ("lat", "lon", "mw_est"))
    ppa, nplants = np.zeros(len(sites)), np.zeros(len(sites), dtype=int)
    for i in range(0, len(sites), 500):
        sl = slice(i, i + 500)
        d = haversine_km(slat[sl, None], slon[sl, None], plat[None], plon[None])
        near = d <= PPA_RADIUS_KM
        ppa[sl] = (near * pmw[None]).sum(axis=1)
        nplants[sl] = near.sum(axis=1)
    sites["ppa_mw_50km"] = ppa.round(0)
    sites["ppa_plants_50km"] = nplants

    # --- 7. Percentile scores (1 = best). Depth + grid distance: hard filters. ---
    lower_is_better = ["sst_max_c", "dist_grid_km", "dist_cable_km",
                       "price_eur_mwh", "gco2_kwh"]
    higher_is_better = ["ppa_mw_50km", "clean_share_pct"]
    for c in lower_is_better:
        sites["s_" + c] = (1 - sites[c].rank(pct=True)).round(3)
    for c in higher_is_better:
        sites["s_" + c] = sites[c].rank(pct=True).round(3)

    sites = sites.dropna(subset=["price_eur_mwh", "gco2_kwh"])

    # --- 8. Write GeoJSON ---
    OUT.parent.mkdir(exist_ok=True)
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(r.lon, 3), round(r.lat, 3)]},
            "properties": {
                k: (None if pd.isna(v) else v)
                for k, v in r._asdict().items() if k not in ("Index", "lat", "lon")
            },
        }
        for r in sites.itertuples()
    ]
    OUT.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False
    ))
    print(f"{len(feats)} sites -> {OUT}")
    print(sites.groupby("landing_country").size().sort_values(ascending=False).head(8).to_dict())
    print(sites[["depth_m", "sst_max_c", "dist_grid_km", "dist_cable_km",
                 "ppa_mw_50km", "landing_headroom_mva"]].describe().round(1).to_string())


if __name__ == "__main__":
    main()
