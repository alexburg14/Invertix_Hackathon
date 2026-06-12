#!/usr/bin/env python3
"""Collapse all data layers onto one row per candidate site (PyPSA grid bus).

Output: web/sites.geojson — raw values + percentile scores per site.
Run after scripts/fetch_data.sh. ~1 min.
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "web" / "sites.geojson"

EARTH_KM = 6371.0
PPA_RADIUS_KM = 50.0

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


def main():
    # --- 1. Candidate sites: PyPSA buses (NB single-quoted geometry column) ---
    buses = pd.read_csv(DATA / "pypsa/buses.csv", quotechar="'")
    buses = buses[(buses.under_construction == "f") & (buses.dc == "f")]
    sites = buses[["bus_id", "x", "y", "voltage", "country"]].rename(
        columns={"x": "lon", "y": "lat"}
    )

    # --- 2. Grid headroom: sum of ratings of lines touching each bus ---
    lines = pd.read_csv(
        DATA / "pypsa/lines.csv", quotechar="'",
        usecols=["bus0", "bus1", "s_nom", "under_construction"],
    )
    lines = lines[lines.under_construction == "f"]
    ends = pd.concat([
        lines[["bus0", "s_nom"]].rename(columns={"bus0": "bus_id"}),
        lines[["bus1", "s_nom"]].rename(columns={"bus1": "bus_id"}),
    ])
    cap = ends.groupby("bus_id").s_nom.agg(["sum", "count"])
    sites["headroom_mva"] = sites.bus_id.map(cap["sum"]).fillna(0).round(0)
    sites["n_lines"] = sites.bus_id.map(cap["count"]).fillna(0).astype(int)
    sites = sites[sites.headroom_mva > 0]

    # --- 3. Price: Ember monthly, mean of latest 12 months per country ---
    prices = pd.read_csv(DATA / "ember/ember_prices_monthly.csv")
    prices["iso2"] = prices["ISO3 Code"].map(ISO3_TO_ISO2)
    prices["Date"] = pd.to_datetime(prices.Date)
    recent = prices[prices.Date > prices.Date.max() - pd.DateOffset(months=12)]
    price_by_c = recent.groupby("iso2")["Price (EUR/MWhe)"].mean().round(1)
    sites["price_eur_mwh"] = sites.country.map(price_by_c)

    # --- 4. Carbon intensity + clean share: Ember yearly, latest year ---
    ember = pd.read_csv(
        DATA / "ember/ember_yearly.csv",
        usecols=["ISO 3 code", "Year", "Category", "Variable", "Unit", "Value"],
    )
    ember["iso2"] = ember["ISO 3 code"].map(ISO3_TO_ISO2)

    def latest(df):
        return df.sort_values("Year").groupby("iso2").Value.last()

    ci = ember[(ember.Variable == "CO2 intensity") & (ember.Unit == "gCO2/kWh")]
    sites["gco2_kwh"] = sites.country.map(latest(ci)).round(0)
    clean = ember[
        (ember.Category == "Electricity generation")
        & (ember.Variable == "Clean") & (ember.Unit == "%")
    ]
    sites["clean_share_pct"] = sites.country.map(latest(clean)).round(1)

    # --- 5. Connectivity: distance to nearest existing data center (OSM) ---
    dcs = osm_points(DATA / "osm_datacenters.json")
    slat, slon = sites.lat.to_numpy()[:, None], sites.lon.to_numpy()[:, None]
    d = haversine_km(slat, slon, dcs.lat.to_numpy()[None], dcs.lon.to_numpy()[None])
    sites["dist_dc_km"] = d.min(axis=1).round(1)
    sites["nearest_dc"] = dcs.name.to_numpy()[d.argmin(axis=1)]

    # --- 6. PPA potential: wind/solar MW within PPA_RADIUS_KM (OSM plants) ---
    plants = osm_points(DATA / "osm_power_plants.json", "plant:output:electricity")
    # untagged plants count at the median tagged capacity of their source type
    fill = plants.groupby(plants.source.str.contains("wind")).mw.median()
    plants["mw_est"] = plants.mw.fillna(
        plants.source.str.contains("wind").map(fill)
    ).fillna(plants.mw.median())
    plat, plon, pmw = (plants[c].to_numpy() for c in ("lat", "lon", "mw_est"))
    ppa, nplants = np.zeros(len(sites)), np.zeros(len(sites), dtype=int)
    for i in range(0, len(sites), 500):  # chunk: full matrix would be ~2.5 GB
        sl = slice(i, i + 500)
        d = haversine_km(slat[sl], slon[sl], plat[None], plon[None])
        near = d <= PPA_RADIUS_KM
        ppa[sl] = (near * pmw[None]).sum(axis=1)
        nplants[sl] = near.sum(axis=1)
    sites["ppa_mw_50km"] = ppa.round(0)
    sites["ppa_plants_50km"] = nplants

    # --- 7. Percentile scores (1 = best). Headroom stays a hard filter. ---
    lower_is_better = ["price_eur_mwh", "gco2_kwh", "dist_dc_km"]
    higher_is_better = ["ppa_mw_50km", "clean_share_pct", "headroom_mva"]
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
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
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
    print(sites.groupby("country").size().sort_values(ascending=False).head(8).to_dict())
    print(sites[["headroom_mva", "price_eur_mwh", "gco2_kwh",
                 "dist_dc_km", "ppa_mw_50km"]].describe().round(1).to_string())


if __name__ == "__main__":
    main()
