#!/usr/bin/env python3
"""Offshore wind farms -> underwater data-center candidate positions.

Source: EMODnet Human Activities "windfarms" layer (WFS, GeoJSON). Each
offshore wind farm is a candidate position for an underwater data center
(Project-Natick style): clean power generated on-site + free seawater cooling.

Per farm we additionally sample two marine-risk layers (both per-point web
services, so no big rasters to stage):
  - water depth  -> EMODnet Bathymetry depth_sample REST
  - cargo-ship route density -> EMODnet Human Activities WMS (routedensity_01)

Output: web/app/public/windfarms.geojson — raw values + scores.
Run: python3 scripts/build_windfarms.py  (~1-2 min, mostly the sampling calls).
"""
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "app" / "public" / "windfarms.geojson"

WFS = (
    "https://ows.emodnet-humanactivities.eu/wfs?service=WFS&version=2.0.0"
    "&request=GetFeature&typeName=windfarms&outputFormat=application/json"
)
DEPTH_REST = "https://rest.emodnet-bathymetry.eu/depth_sample?geom=POINT({lon}%20{lat})"
# routedensity_01 = Cargo (EMSA route density, monthly totals).
CARGO_WMS = "https://ows.emodnet-humanactivities.eu/wms"

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


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "EnerSite/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def sample_depth(lon, lat):
    """Mean seabed depth in positive metres below sea level (None on miss)."""
    try:
        d = json.loads(_get(DEPTH_REST.format(lon=lon, lat=lat)))
        avg = d.get("avg")
        return round(-avg, 1) if avg is not None and avg < 0 else None
    except Exception:
        return None


def sample_cargo(lon, lat):
    """Cargo-ship route-density index at the point (0 = no traffic)."""
    eps = 0.01
    q = {
        "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
        "layers": "routedensity_01", "query_layers": "routedensity_01",
        "crs": "EPSG:4326",  # 1.3.0 EPSG:4326 axis order is lat,lon
        "bbox": f"{lat - eps},{lon - eps},{lat + eps},{lon + eps}",
        "width": "3", "height": "3", "i": "1", "j": "1",
        "info_format": "application/json",
    }
    try:
        d = json.loads(_get(CARGO_WMS + "?" + urllib.parse.urlencode(q)))
        feats = d.get("features") or []
        v = feats[0]["properties"].get("GRAY_INDEX") if feats else None
        return float(v) if v is not None else None
    except Exception:
        return None


def depth_suitability(d):
    """Underwater-DC depth fit (Natick sat ~36 m). Sweet spot ~15-60 m: deep
    enough to clear shipping draft & wave action, shallow enough to deploy and
    service. Very shallow or very deep both penalised."""
    if d is None or np.isnan(d):
        return np.nan
    if d < 10:
        return 0.25
    if d <= 60:
        return 1.0
    if d <= 120:
        return 0.7
    if d <= 200:
        return 0.4
    return 0.15


def main():
    raw = json.loads(_get(WFS, timeout=120))
    df = pd.json_normalize([f["properties"] | {
        "lon": f["geometry"]["coordinates"][0],
        "lat": f["geometry"]["coordinates"][1],
    } for f in raw["features"]])

    # Dismantled farms no longer exist; keep only real/planned positions with a
    # known capacity (the hard filter needs MW).
    df = df[df.status != "Dismantled"]
    df = df[df.power_mw.notna() & (df.power_mw > 0)].reset_index(drop=True)

    df["country"] = df.country.map(NAME_TO_ISO2).fillna(df.country)
    df["dist_coast_km"] = (df.dist_coast / 1000).round(1)
    df["n_turbines"] = df.n_turbines.fillna(0).astype(int)
    df["power_mw"] = df.power_mw.round(0)
    df["s_status"] = df.status.map(STATUS_SCORE).fillna(0.25)

    # --- Sample marine-risk layers per farm (threaded; ~800 light calls) ---
    pts = list(zip(df.lon, df.lat))
    print(f"sampling depth + cargo for {len(pts)} farms ...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        df["depth_m"] = list(ex.map(lambda p: sample_depth(*p), pts))
    with ThreadPoolExecutor(max_workers=8) as ex:
        df["cargo_density"] = list(ex.map(lambda p: sample_cargo(*p), pts))
    df["cargo_density"] = df.cargo_density.round(0)

    # --- Scores (1 = best) ---
    df["s_power"] = df.power_mw.rank(pct=True).round(3)          # bigger supply
    df["s_coast"] = (1 - df.dist_coast_km.rank(pct=True)).round(3)  # closer shore
    df["s_depth"] = df.depth_m.map(depth_suitability).round(3)   # depth sweet spot
    # Shipping safety: less cargo traffic = safer (anchor/collision). Missing
    # data treated as median traffic so it neither helps nor hurts.
    cargo = df.cargo_density.fillna(df.cargo_density.median())
    df["s_ship"] = (1 - cargo.rank(pct=True)).round(3)

    keep = ["name", "country", "status", "year", "power_mw", "n_turbines",
            "dist_coast_km", "depth_m", "cargo_density",
            "s_power", "s_coast", "s_status", "s_depth", "s_ship"]
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
    print("depth m:", df.depth_m.describe()[["min", "50%", "max"]].round(1).to_dict())
    print("cargo coverage:", int(df.cargo_density.notna().sum()), "/", len(df))


if __name__ == "__main__":
    main()
