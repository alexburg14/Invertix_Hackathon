#!/usr/bin/env python3
"""Per-farm suitability *surfaces* for underwater data-center siting.

`build_windfarms.py` treats each offshore wind farm as a single candidate
*point*. This script zooms in one level: for every farm it lays a grid of
cells over the seabed around it (a ~12 km disc = realistic cable reach from
the farm's export infrastructure) and scores each cell on its own, so the
frontend can render a heat *surface* showing where, within reach of a park,
the seabed is actually a good place to drop a data center.

Two things vary cell-to-cell at this scale:
  - water depth      -> sampled per cell from EMODnet Bathymetry (depth_sample)
  - distance to coast -> computed per cell against a coastline (Natural Earth)
The farm-level factors (capacity, shipping, readiness) don't change within a
disc, so they stay in windfarms.geojson and are recombined client-side.

Input:  web/app/public/windfarms.geojson  (farm points, produced upstream)
Output: web/app/public/site_fields.geojson  (one Point per grid cell)
Run:    python3 scripts/build_site_fields.py   (~20k light calls, a few min)
"""
import json
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FARMS = ROOT / "web" / "app" / "public" / "windfarms.geojson"
OUT = ROOT / "web" / "app" / "public" / "site_fields.geojson"
COAST_CACHE = ROOT / "data" / "ne_10m_coastline.geojson"

DEPTH_REST = "https://rest.emodnet-bathymetry.eu/depth_sample?geom=POINT({lon}%20{lat})"
COAST_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_10m_coastline.geojson"
)

# Disc of seabed we consider "reachable" from a farm, and how finely we grid it.
RADIUS_KM = 12.0
STEP_KM = 3.0

# Sandbox CAs are often incomplete; verify if we can, fall back only on a real
# TLS failure (an HTTP error status means the handshake already succeeded).
def _make_ctx():
    ctx = ssl.create_default_context()
    try:
        urllib.request.urlopen(
            urllib.request.Request(COAST_URL, method="HEAD"), timeout=10, context=ctx
        )
    except urllib.error.HTTPError:
        pass  # TLS fine, server just dislikes the request
    except (ssl.SSLError, urllib.error.URLError):
        return ssl._create_unverified_context()
    return ctx


_CTX = _make_ctx()


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "EnerSite/1.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def depth_suitability(d):
    """Underwater-DC depth fit (Natick sat ~36 m). Sweet spot ~15-60 m: deep
    enough to clear shipping draft & wave action, shallow enough to deploy and
    service. Identical curve to build_windfarms.py so farm & cell scores agree."""
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


def sample_depth(lon, lat):
    """Mean seabed depth in positive metres below sea level (None on miss)."""
    try:
        d = json.loads(_get(DEPTH_REST.format(lon=lon, lat=lat)))
        avg = d.get("avg")
        return round(-avg, 1) if avg is not None and avg < 0 else None
    except Exception:
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance (km). Args broadcast against each other."""
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return r * 2 * np.arcsin(np.sqrt(a))


def load_coastline():
    """Coastline vertices as (lat, lon) arrays, cached locally after first pull."""
    if COAST_CACHE.exists():
        gj = json.loads(COAST_CACHE.read_text())
    else:
        print("fetching Natural Earth coastline ...")
        raw = _get(COAST_URL, timeout=120)
        COAST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        COAST_CACHE.write_bytes(raw)
        gj = json.loads(raw)
    lats, lons = [], []
    for f in gj["features"]:
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            for lon, lat in line:
                lats.append(lat)
                lons.append(lon)
    return np.array(lats), np.array(lons)


def disc_cells(lat0, lon0):
    """Grid of (lat, lon) cells within RADIUS_KM of a farm centre."""
    dlat = STEP_KM / 111.0
    dlon = STEP_KM / (111.0 * np.cos(np.radians(lat0)))
    n = int(np.ceil(RADIUS_KM / STEP_KM))
    cells = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            lat, lon = lat0 + i * dlat, lon0 + j * dlon
            if haversine_km(lat0, lon0, lat, lon) <= RADIUS_KM:
                cells.append((round(lat, 4), round(lon, 4)))
    return cells


def main():
    farms = json.loads(FARMS.read_text())["features"]
    centres = [(f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) for f in farms]
    print(f"{len(centres)} farms, radius {RADIUS_KM} km @ {STEP_KM} km step")

    coast_lat, coast_lon = load_coastline()
    print(f"coastline vertices: {len(coast_lat)}")

    # Reference distribution: farm coast distances drive a comparable cell
    # s_coast (same 1 - percentile scale the farm scores use).
    farm_dists = np.sort([f["properties"]["dist_coast_km"] for f in farms
                          if f["properties"].get("dist_coast_km") is not None])

    # Build every cell up front, tagged with its farm index, then sample depth
    # for all of them in one threaded pass.
    cells = []  # (farm_idx, lat, lon)
    for fi, (lat0, lon0) in enumerate(centres):
        for lat, lon in disc_cells(lat0, lon0):
            cells.append((fi, lat, lon))
    print(f"sampling depth for {len(cells)} cells ...")

    with ThreadPoolExecutor(max_workers=16) as ex:
        depths = list(ex.map(lambda c: sample_depth(c[2], c[1]), cells))

    # Per-cell distance to coast: clip coastline to each farm's neighbourhood
    # once, then vectorise over that farm's cells.
    by_farm = {}
    for idx, (fi, lat, lon) in enumerate(cells):
        by_farm.setdefault(fi, []).append(idx)

    dist_coast = [None] * len(cells)
    for fi, idxs in by_farm.items():
        lat0, lon0 = centres[fi]
        # Coastline within ~1.5 deg of the farm is plenty for a 12 km disc.
        m = (np.abs(coast_lat - lat0) < 1.5) & (np.abs(coast_lon - lon0) < 1.5)
        clat, clon = coast_lat[m], coast_lon[m]
        for idx in idxs:
            _, lat, lon = cells[idx]
            if clat.size:
                dist_coast[idx] = round(float(haversine_km(lat, lon, clat, clon).min()), 1)
            else:
                # No coastline nearby (open ocean) -> fall back to farm's value.
                dist_coast[idx] = farms[fi]["properties"].get("dist_coast_km")

    feats = []
    for idx, (fi, lat, lon) in enumerate(cells):
        d = depths[idx]
        dc = dist_coast[idx]
        s_depth = depth_suitability(d)
        if dc is None:
            s_coast = None
        else:
            # 1 - empirical percentile of this distance among farms.
            s_coast = round(1.0 - np.searchsorted(farm_dists, dc) / max(len(farm_dists), 1), 3)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
            "properties": {
                "fi": fi,
                "depth_m": d,
                "dist_coast_km": dc,
                "s_depth": None if (s_depth is None or np.isnan(s_depth)) else round(float(s_depth), 3),
                "s_coast": s_coast,
            },
        })

    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                              ensure_ascii=False))
    n_depth = sum(1 for f in feats if f["properties"]["depth_m"] is not None)
    print(f"{len(feats)} cells -> {OUT}")
    print(f"depth coverage: {n_depth}/{len(feats)} "
          f"({100 * n_depth / max(len(feats), 1):.0f}%)")
    print(f"output size: {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
