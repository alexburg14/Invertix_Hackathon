#!/usr/bin/env python3
"""Offshore wind farms -> underwater data-center candidate scores.

Pulls the EMODnet windfarms WFS layer, then enriches each farm with 10 siting
factors from local data files (pre-fetched into data/sea/). Each factor is
scored 0-1 (1 = best) and stored alongside raw values in the output GeoJSON.

The 10 factors:
  1. s_capacity  - Wind-farm capacity (MW)
  2. s_bottemp   - Bottom-water temperature (colder = better cooling)
  3. s_windpark   - Distance to nearest other wind farm (redundancy/clustering)
  4. s_shore     - Distance to shore (cable landing)
  5. s_depth     - Water depth fit (sweet spot ~15-60 m)
  6. s_negprice  - Negative-price hours (surplus power opportunity)
  7. s_wind      - Wind speed (higher = more production)
  8. s_current   - Sea current (sweet spot for cooling, not too strong)
  9. s_slope     - Seabed slope (flatter = easier deployment)
  10. s_mpa      - Protected areas (outside = better)

Output: web/app/public/windfarms.geojson
Run:    python3 scripts/build_windfarms.py
"""
import json
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "app" / "public" / "windfarms.geojson"
DATA = ROOT / "data" / "sea"

WFS = (
    "https://ows.emodnet-humanactivities.eu/wfs?service=WFS&version=2.0.0"
    "&request=GetFeature&typeName=windfarms&outputFormat=application/json"
)
DEPTH_REST = "https://rest.emodnet-bathymetry.eu/depth_sample?geom=POINT({lon}%20{lat})"

NAME_TO_ISO2 = {
    "Belgium": "BE", "Denmark": "DK", "Estonia": "EE", "Finland": "FI",
    "France": "FR", "Germany": "DE", "Greece": "GR", "Ireland": "IE",
    "Italy": "IT", "Latvia": "LV", "Lithuania": "LT", "Netherlands": "NL",
    "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO",
    "Spain": "ES", "Sweden": "SE", "United Kingdom": "GB",
}

FACTOR_SPECS = [
    ("s_capacity", "power_mw", "higher"),
    ("s_bottemp", "bot_temp_c", "lower"),
    ("s_windpark", "dist_windpark_km", "lower"),
    ("s_shore", "dist_shore_km", "lower"),
    ("s_depth", "depth_m", "curve"),
    ("s_negprice", "negprice_pct", "higher"),
    ("s_wind", "wind_ms", "higher"),
    ("s_current", "current_ms", "curve"),
    ("s_slope", "slope_deg", "lower"),
    ("s_mpa", "mpa_status", "binary"),
]

SCORE_KEYS = [skey for skey, _, _ in FACTOR_SPECS]


def _make_ctx():
    ctx = ssl.create_default_context()
    try:
        urllib.request.urlopen(urllib.request.Request(WFS, method="HEAD"), timeout=10, context=ctx)
    except urllib.error.HTTPError:
        pass
    except (ssl.SSLError, urllib.error.URLError):
        return ssl._create_unverified_context()
    return ctx


_CTX = _make_ctx()


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "EnerSite/1.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def sample_depth(lon, lat):
    try:
        d = json.loads(_get(DEPTH_REST.format(lon=lon, lat=lat)))
        avg = d.get("avg")
        return round(-avg, 1) if avg is not None and avg < 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Grid-lookup helpers: find nearest grid cell for a given lat/lon
# ---------------------------------------------------------------------------

def _build_grid_index(lats, lons):
    """Return sorted unique lat/lon arrays for fast nearest-neighbor lookup."""
    return np.sort(np.unique(lats)), np.sort(np.unique(lons))


def _nearest_idx(arr, val):
    idx = np.searchsorted(arr, val, side="left")
    if idx == 0:
        return 0
    if idx == len(arr):
        return len(arr) - 1
    return idx if abs(arr[idx] - val) < abs(arr[idx - 1] - val) else idx - 1


def _grid_lookup(df, lat_col, lon_col, val_col, query_lats, query_lons):
    """Look up nearest-grid-cell values for arrays of query points."""
    ulats, ulons = _build_grid_index(df[lat_col].values, df[lon_col].values)
    # Build a dict keyed by (lat_idx_val, lon_idx_val) for O(1) lookup
    lookup = {}
    for _, row in df.iterrows():
        key = (round(row[lat_col], 4), round(row[lon_col], 4))
        lookup[key] = row[val_col]

    results = []
    for qlat, qlon in zip(query_lats, query_lons):
        nlat = ulats[_nearest_idx(ulats, qlat)]
        nlon = ulons[_nearest_idx(ulons, qlon)]
        results.append(lookup.get((round(nlat, 4), round(nlon, 4))))
    return results


def _read_erddap_csv(path):
    """ERDDAP CSVs include a units row after the header; skip it."""
    return pd.read_csv(path, skiprows=[1])


def _haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance in km."""
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Enrichment functions (each reads from data/sea/ files)
# ---------------------------------------------------------------------------

def enrich_bottemp(df):
    """Bottom-water temperature from WOA climatology."""
    path = DATA / "woa_bottemp.csv"
    if not path.exists():
        print("  SKIP bottemp: file not found")
        df["bot_temp_c"] = np.nan
        return
    bt = _read_erddap_csv(path).rename(columns={"latitude": "lat", "longitude": "lon"})
    # Use deepest available depth per grid cell as proxy for bottom temp
    bt = bt.dropna(subset=["t_an"])
    bt_deep = bt.sort_values("depth", ascending=False).drop_duplicates(subset=["lat", "lon"], keep="first")
    df["bot_temp_c"] = _grid_lookup(bt_deep, "lat", "lon", "t_an", df.lat.values, df.lon.values)
    df["bot_temp_c"] = pd.to_numeric(df["bot_temp_c"], errors="coerce").round(1)
    print(f"  bottemp: {df.bot_temp_c.notna().sum()}/{len(df)} matched")


def enrich_wind(df):
    """Mean wind speed from ERA5 / ECMWF grid data."""
    results = []
    for name in ["wind_east.csv", "wind_west.csv"]:
        path = DATA / name
        if path.exists():
            w = _read_erddap_csv(path).rename(
                columns={"latitude": "lat", "longitude": "lon", "x_wind": "u", "y_wind": "v"}
            )
            results.append(w)
    if not results:
        print("  SKIP wind: files not found")
        df["wind_ms"] = np.nan
        return
    w = pd.concat(results)
    w["ws"] = np.sqrt(w.u ** 2 + w.v ** 2)
    w = w.dropna(subset=["ws"])
    # Wrap longitudes > 180 to negative
    w.loc[w.lon > 180, "lon"] = w.loc[w.lon > 180, "lon"] - 360
    wmean = w.groupby(["lat", "lon"]).ws.mean().reset_index()
    df["wind_ms"] = _grid_lookup(wmean, "lat", "lon", "ws", df.lat.values, df.lon.values)
    df["wind_ms"] = pd.to_numeric(df["wind_ms"], errors="coerce").round(1)
    print(f"  wind: {df.wind_ms.notna().sum()}/{len(df)} matched")


def enrich_current(df):
    """Sea current speed from ocean model grid."""
    path = DATA / "current.csv"
    if not path.exists():
        print("  SKIP current: file not found")
        df["current_ms"] = np.nan
        return
    cr = _read_erddap_csv(path).rename(columns={"latitude": "lat", "longitude": "lon"})
    # Wrap longitudes > 180
    cr.loc[cr.lon > 180, "lon"] = cr.loc[cr.lon > 180, "lon"] - 360
    cr["speed"] = np.sqrt(cr.u ** 2 + cr.v ** 2)
    cr = cr.dropna(subset=["speed"])
    cmean = cr.groupby(["lat", "lon"]).speed.mean().reset_index()
    df["current_ms"] = _grid_lookup(cmean, "lat", "lon", "speed", df.lat.values, df.lon.values)
    df["current_ms"] = pd.to_numeric(df["current_ms"], errors="coerce").round(3)
    print(f"  current: {df.current_ms.notna().sum()}/{len(df)} matched")


def _ray_cast(px, py, vx, vy):
    """Even-odd point-in-polygon for one ring, vectorized over points."""
    inside = np.zeros(len(px), dtype=bool)
    j = len(vx) - 1
    for i in range(len(vx)):
        crosses = ((vy[i] > py) != (vy[j] > py)) & (
            px < (vx[j] - vx[i]) * (py - vy[i]) / (vy[j] - vy[i] + 1e-12) + vx[i]
        )
        inside ^= crosses
        j = i
    return inside


def enrich_mpa(df):
    """Check whether each farm lies inside a marine protected area."""
    path = DATA / "mpa.json"
    if not path.exists():
        print("  SKIP mpa: file not found")
        df["mpa_status"] = "unknown"
        return
    mpa = json.loads(path.read_text())
    slat, slon = df.lat.to_numpy(), df.lon.to_numpy()
    inside = np.zeros(len(df), dtype=bool)
    names = np.array(["open water"] * len(df), dtype=object)
    polygons = 0
    for f in mpa.get("features", []):
        name = f["properties"].get("name") or f["properties"].get("orig_name") or "protected area"
        geom = f.get("geometry") or {}
        polys = geom.get("coordinates", []) if geom.get("type") == "MultiPolygon" else [geom.get("coordinates", [])]
        for poly in polys:
            if not poly:
                continue
            ring = np.asarray(poly[0], dtype=float)
            if len(ring) < 4:
                continue
            polygons += 1
            if len(ring) > 500:
                ring = ring[:: max(1, len(ring) // 500)]
            rlon, rlat = ring[:, 0], ring[:, 1]
            candidates = (
                (slon >= rlon.min()) & (slon <= rlon.max())
                & (slat >= rlat.min()) & (slat <= rlat.max())
                & (~inside)
            )
            idx = np.where(candidates)[0]
            if idx.size == 0:
                continue
            hit = _ray_cast(slon[idx], slat[idx], rlon, rlat)
            inside[idx[hit]] = True
            names[idx[hit]] = name
    df["mpa_status"] = names
    print(f"  mpa: {int(inside.sum())} inside, {len(df) - int(inside.sum())} outside ({polygons} polygons)")


def enrich_slope(df):
    """Estimate seabed slope from bathymetry grid (gradient of depth)."""
    path = DATA / "bathymetry.csv"
    if not path.exists():
        print("  SKIP slope: bathymetry.csv not found")
        df["slope_deg"] = np.nan
        return
    bath = _read_erddap_csv(path).rename(
        columns={"latitude": "lat", "longitude": "lon", "altitude": "depth"}
    )
    bath["depth"] = pd.to_numeric(bath.depth, errors="coerce")
    bath = bath.dropna(subset=["depth"])
    # Wrap longitudes
    bath.loc[bath.lon > 180, "lon"] = bath.loc[bath.lon > 180, "lon"] - 360

    # Compute gradient: for each grid cell, slope = max depth difference to neighbors
    ulats = np.sort(bath.lat.unique())
    ulons = np.sort(bath.lon.unique())
    dlat = np.median(np.diff(ulats)) if len(ulats) > 1 else 0.25
    dlon = np.median(np.diff(ulons)) if len(ulons) > 1 else 0.25

    depth_map = {}
    for _, r in bath.iterrows():
        depth_map[(round(r.lat, 4), round(r.lon, 4))] = r.depth

    def cell_slope(lat, lon):
        d0 = depth_map.get((round(lat, 4), round(lon, 4)))
        if d0 is None:
            return None
        neighbors = [
            depth_map.get((round(lat + dlat, 4), round(lon, 4))),
            depth_map.get((round(lat - dlat, 4), round(lon, 4))),
            depth_map.get((round(lat, 4), round(lon + dlon, 4))),
            depth_map.get((round(lat, 4), round(lon - dlon, 4))),
        ]
        diffs = [abs(d0 - n) for n in neighbors if n is not None]
        if not diffs:
            return 0.0
        dist_m = dlat * 111_000  # rough m per degree lat
        return np.degrees(np.arctan(max(diffs) / dist_m))

    slopes = []
    lat_grid, lon_grid = _build_grid_index(bath.lat.values, bath.lon.values)
    for _, row in df.iterrows():
        nlat = lat_grid[_nearest_idx(lat_grid, row.lat)]
        nlon = lon_grid[_nearest_idx(lon_grid, row.lon)]
        slopes.append(cell_slope(nlat, nlon))
    df["slope_deg"] = [round(s, 2) if s is not None else None for s in slopes]
    print(f"  slope: {df.slope_deg.notna().sum()}/{len(df)} computed")


def enrich_negprice(df):
    """Negative-price hours per country from Fraunhofer Energy-Charts."""
    path = DATA / "negprice.json"
    if not path.exists():
        print("  SKIP negprice: file not found")
        df["negprice_pct"] = np.nan
        return
    negprice = json.loads(path.read_text())
    df["negprice_pct"] = df.country.map(negprice)
    print(f"  negprice: {df.negprice_pct.notna().sum()}/{len(df)} matched")


def enrich_nearest_farm(df):
    """Distance to nearest other wind farm (clustering / redundancy)."""
    lats = df.lat.values
    lons = df.lon.values
    n = len(df)
    dists = np.full(n, np.inf)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = _haversine_km(lats[i], lons[i], lats[j], lons[j])
            if d < dists[i]:
                dists[i] = d
    df["dist_windpark_km"] = np.round(dists, 1)
    print(f"  nearest farm: min={dists.min():.1f} km, max={dists.max():.1f} km")


# ---------------------------------------------------------------------------
# Scoring functions (each maps raw -> 0-1, 1 = best)
# ---------------------------------------------------------------------------

def depth_suitability(d):
    if d is None or (isinstance(d, float) and np.isnan(d)):
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


def current_suitability(c):
    """Sweet spot 0.05-0.3 m/s: enough flow for cooling, not too strong for structures."""
    if c is None or (isinstance(c, float) and np.isnan(c)):
        return np.nan
    if c < 0.02:
        return 0.3
    if c <= 0.3:
        return 1.0
    if c <= 0.6:
        return 0.6
    return 0.3


def score(df):
    """Compute all s_* scores from raw values."""
    df["s_capacity"] = df.power_mw.rank(pct=True).round(3)
    df["s_shore"] = (1 - df.dist_shore_km.rank(pct=True)).round(3)  # closer = better
    df["s_depth"] = df.depth_m.map(depth_suitability).round(3)

    # Bottom temp: colder = better cooling (rank inverted)
    # Store filled value for output so UI never shows "?"
    bt_median = df.bot_temp_c.median()
    bt = df.bot_temp_c.fillna(bt_median)
    df["bot_temp_c_filled"] = bt.round(1)
    df["s_bottemp"] = (1 - bt.rank(pct=True)).round(3)

    # Wind speed: higher = more production
    ws = df.wind_ms.fillna(df.wind_ms.median())
    df["s_wind"] = ws.rank(pct=True).round(3)

    # Current: sweet-spot scoring
    df["s_current"] = df.current_ms.map(current_suitability).round(3)

    # Slope: flatter = better
    sl = df.slope_deg.fillna(df.slope_deg.median())
    df["s_slope"] = (1 - sl.rank(pct=True)).round(3)

    # MPA: binary (outside = 1, inside = 0)
    df["s_mpa"] = df.mpa_status.map(lambda x: 1.0 if x == "open water" else 0.0)

    # Negative price: more negative hours = better
    neg = df.negprice_pct.fillna(df.negprice_pct.median())
    df["s_negprice"] = neg.rank(pct=True).round(3)

    # Nearest wind farm: closer = better (clustering benefit)
    dwp = df.dist_windpark_km.fillna(df.dist_windpark_km.median())
    df["s_windpark"] = (1 - dwp.rank(pct=True)).round(3)

    for skey in SCORE_KEYS:
        df[skey] = df[skey].fillna(df[skey].median()).round(3)

    df["score_equal_weight"] = df[SCORE_KEYS].mean(axis=1).round(3)
    df["rank_equal_weight"] = df["score_equal_weight"].rank(
        method="first", ascending=False
    ).astype(int)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching EMODnet wind farms...")
    raw = json.loads(_get(WFS, timeout=120))
    df = pd.json_normalize([f["properties"] | {
        "lon": f["geometry"]["coordinates"][0],
        "lat": f["geometry"]["coordinates"][1],
    } for f in raw["features"]])

    df = df[df.status != "Dismantled"]
    df = df[df.power_mw.notna() & (df.power_mw > 0)].reset_index(drop=True)
    df["country"] = df.country.map(NAME_TO_ISO2).fillna(df.country)
    df["dist_shore_km"] = (df.dist_coast / 1000).round(1)
    df["dist_coast_km"] = df["dist_shore_km"]
    df["n_turbines"] = df.n_turbines.fillna(0).astype(int)
    df["power_mw"] = df.power_mw.round(0)

    # --- Sample depth per farm (threaded) ---
    pts = list(zip(df.lon, df.lat))
    print(f"Sampling depth for {len(pts)} farms...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        df["depth_m"] = list(ex.map(lambda p: sample_depth(*p), pts))

    # --- Enrich from local data files ---
    print("Enriching from local data...")
    enrich_bottemp(df)
    enrich_wind(df)
    enrich_current(df)
    enrich_slope(df)
    enrich_negprice(df)
    enrich_nearest_farm(df)

    enrich_mpa(df)

    # --- Score all factors ---
    print("Scoring...")
    score(df)
    df = df.sort_values("rank_equal_weight").reset_index(drop=True)

    # --- Write output ---
    keep = [
        "name", "country", "status", "year", "power_mw", "n_turbines",
        "dist_shore_km", "dist_coast_km", "depth_m", "bot_temp_c_filled", "wind_ms", "current_ms",
        "slope_deg", "mpa_status", "negprice_pct", "dist_windpark_km",
        "s_capacity", "s_shore", "s_depth", "s_bottemp", "s_wind",
        "s_current", "s_slope", "s_mpa", "s_negprice", "s_windpark",
        "score_equal_weight", "rank_equal_weight",
    ]
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
            "properties": {k: (None if pd.isna(getattr(r, k, None)) else getattr(r, k))
                           for k in keep if hasattr(r, k)},
        }
        for r in df.itertuples()
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False
    ))
    print(f"\n{len(feats)} offshore wind farms -> {OUT}")
    coverage = {
        raw: int(df[raw].notna().sum())
        for _, raw, mode in FACTOR_SPECS
        if mode not in {"curve", "binary"} or raw in df
    }
    coverage.update({"depth_m": int(df.depth_m.notna().sum()), "current_ms": int(df.current_ms.notna().sum())})
    print("factor raw coverage:", coverage)
    print("status:", df.status.value_counts().to_dict())
    print("total capacity:", int(df.power_mw.sum()), "MW")


if __name__ == "__main__":
    main()
