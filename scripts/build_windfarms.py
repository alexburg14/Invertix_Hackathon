#!/usr/bin/env python3
"""Offshore wind farms -> underwater data-center candidate positions.

Source: EMODnet Human Activities "windfarms" layer (WFS, GeoJSON). Each
offshore wind farm is a candidate position for an underwater data center
(Project-Natick style): clean power generated on-site + free seawater cooling.

Output: web/app/public/windfarms.geojson — raw values + percentile scores.
The WFS endpoint *is* the raw source, so this script fetches and collapses in
one step (no data/ staging file needed). ~2 s.
"""
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "app" / "public" / "windfarms.geojson"

WFS = (
    "https://ows.emodnet-humanactivities.eu/wfs?service=WFS&version=2.0.0"
    "&request=GetFeature&typeName=windfarms&outputFormat=application/json"
)

# EMODnet keys on full country names; the frontend country filter uses ISO2.
NAME_TO_ISO2 = {
    "Belgium": "BE", "Denmark": "DK", "Estonia": "EE", "Finland": "FI",
    "France": "FR", "Germany": "DE", "Greece": "GR", "Ireland": "IE",
    "Italy": "IT", "Latvia": "LV", "Lithuania": "LT", "Netherlands": "NL",
    "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO",
    "Spain": "ES", "Sweden": "SE", "United Kingdom": "GB",
}

# Operational readiness: how soon a farm can host a DC (1 = ready now).
STATUS_SCORE = {
    "Production": 1.0, "Construction": 0.75, "Approved": 0.5, "Planned": 0.25,
}


def main():
    raw = json.loads(urllib.request.urlopen(WFS, timeout=120).read())
    df = pd.json_normalize([f["properties"] | {
        "lon": f["geometry"]["coordinates"][0],
        "lat": f["geometry"]["coordinates"][1],
    } for f in raw["features"]])

    # Dismantled farms no longer exist; keep only real/planned positions with a
    # known capacity (the hard filter needs MW).
    df = df[df.status != "Dismantled"]
    df = df[df.power_mw.notna() & (df.power_mw > 0)]

    df["country"] = df.country.map(NAME_TO_ISO2).fillna(df.country)
    df["dist_coast_km"] = (df.dist_coast / 1000).round(1)
    df["n_turbines"] = df.n_turbines.fillna(0).astype(int)
    df["power_mw"] = df.power_mw.round(0)
    df["s_status"] = df.status.map(STATUS_SCORE).fillna(0.25)

    # Percentile scores (1 = best). Capacity higher-is-better, coast closer-is-better.
    df["s_power"] = df.power_mw.rank(pct=True).round(3)
    df["s_coast"] = (1 - df.dist_coast_km.rank(pct=True)).round(3)

    keep = ["name", "country", "status", "year", "power_mw", "n_turbines",
            "dist_coast_km", "s_power", "s_coast", "s_status"]
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
            "properties": {k: (None if pd.isna(getattr(r, k)) else getattr(r, k))
                           for k in keep},
        }
        for r in df.itertuples()
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False
    ))
    print(f"{len(feats)} offshore wind farms -> {OUT}")
    print("status:", df.status.value_counts().to_dict())
    print("total capacity:", int(df.power_mw.sum()), "MW")


if __name__ == "__main__":
    main()
