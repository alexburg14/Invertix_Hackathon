#!/usr/bin/env python3
"""Holistic seafloor suitability grid for underwater data-center siting.

We grid the whole European shelf (~0.05 deg cells in the 20-120 m depth band) and
score every cell on 10 factors. Each cell is assigned to its nearest wind farm so
the frontend can (a) threshold the composite into a heatmap of good seabed and
(b) rank wind farms by the potential of their assigned cells.

All factors are computed offline and stored as scores s_* in [0,1] (1 = best);
raw values are kept for the explain panel. The composite is recombined client-side
so the weight sliders + threshold react instantly.

Factors:
  1 s_windpark  distance to nearest wind farm        (closer better)
  2 s_depth     depth fit (band suitability curve)    (ideal ~25-55 m)
  3 s_shore     distance-to-shore fit (band curve)    (ideal ~10-60 km)
  4 s_bottemp   bottom-water temperature at seabed    (cooler better)
  5 s_wind      mean surface wind speed (ASCAT)       (windier better)
  6 s_current   surface current speed (OSCAR)         (moderate band best)
  7 s_capacity  capacity of the associated wind farm  (bigger better)
  8 s_slope     seabed slope (from bathymetry)        (flatter better)
  9 s_mpa       marine protected area                 (outside better)
 10 s_negprice  share of negative-price hours         (more surplus = better)

Hard filters (exclude the cell entirely): depth 20-120 m, dist-to-windpark
<= 100 km, dist-to-grid <= 150 km (must be able to land the power).

Input:  data/ (run scripts/fetch_data.sh) + web/app/public/windfarms.geojson
Output: web/app/public/seafloor.geojson
Run:    python3 scripts/build_seafloor.py   (~2-3 min)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FARMS = ROOT / "web" / "app" / "public" / "windfarms.geojson"
OUT = ROOT / "web" / "app" / "public" / "seafloor.geojson"
COAST = DATA / "ne_50m_coastline.geojson"  # 50 m is plenty at ~5 km cell size

EARTH_KM = 6371.0
DEPTH_MIN, DEPTH_MAX = 20.0, 120.0   # m below sea level (Natick-style band)
MAX_GRID_KM = 150.0                  # export-cable practicality (hard filter)
MAX_WINDPARK_KM = 100.0              # keep cells in reach of a wind farm


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine; lat/lon in degrees, broadcasting-friendly."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def nearest(slat, slon, tlat, tlon, chunk=500):
    """Chunked nearest-neighbor: returns (min_dist_km, argmin) per site."""
    dist = np.empty(len(slat))
    idx = np.empty(len(slat), dtype=int)
    for i in range(0, len(slat), chunk):
        sl = slice(i, i + chunk)
        d = haversine_km(slat[sl, None], slon[sl, None], tlat[None], tlon[None])
        dist[sl], idx[sl] = d.min(axis=1), d.argmin(axis=1)
    return dist, idx


def grid_mean(path, ucol, vcol):
    """ERDDAP CSV (units row at line 2) -> per-cell mean speed sqrt(u^2+v^2),
    lon remapped to -180..180. Averages away NaN-heavy time slices."""
    df = pd.read_csv(path, skiprows=[1])
    df["longitude"] = np.where(df.longitude > 180, df.longitude - 360, df.longitude)
    df["speed"] = np.hypot(df[ucol], df[vcol])
    return df.groupby(["latitude", "longitude"]).speed.mean().reset_index()


def coastline_vertices():
    """Coastline vertices as (lat, lon) arrays from the cached Natural Earth file."""
    gj = json.loads(COAST.read_text())
    lats, lons = [], []
    for f in gj["features"]:
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            for lon, lat in line:
                lats.append(lat)
                lons.append(lon)
    return np.asarray(lats), np.asarray(lons)


def seabed_slope(bathy):
    """Seabed slope (degrees) per grid cell from the bathymetry gradient.
    Returns a (lat, lon, slope_deg) DataFrame aligned to the bathymetry grid."""
    grid = bathy.pivot(index="latitude", columns="longitude", values="altitude")
    lats = grid.index.to_numpy(dtype=float)
    lons = grid.columns.to_numpy(dtype=float)
    z = grid.to_numpy(dtype=float)  # altitude; negative = below sea level
    dlat_m = np.gradient(lats) * 111_000.0
    dlon_m = np.gradient(lons) * 111_000.0 * np.cos(np.radians(np.nanmean(lats)))
    dzdy = np.gradient(z, axis=0) / dlat_m[:, None]
    dzdx = np.gradient(z, axis=1) / dlon_m[None, :]
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    out = pd.DataFrame(slope, index=lats, columns=lons)
    out = out.reset_index().melt(id_vars="index", var_name="lon", value_name="slope_deg")
    return out.rename(columns={"index": "lat"})


def bottom_temp(woa_path, slat, slon, depth_m):
    """Warm-season bottom-water temp per cell: nearest WOA column, sampled at the
    depth level closest to the seabed depth (capped at the deepest valid level)."""
    woa = pd.read_csv(woa_path, skiprows=[1]).dropna(subset=["t_an"])
    prof, wlat, wlon = {}, [], []
    for (la, lo), g in woa.groupby(["latitude", "longitude"]):
        gg = g.sort_values("depth")
        prof[(la, lo)] = (gg.depth.to_numpy(), gg.t_an.to_numpy())
        wlat.append(la)
        wlon.append(lo)
    keys = list(prof)
    _, j = nearest(slat, slon, np.array(wlat), np.array(wlon))
    out = np.empty(len(slat))
    for i in range(len(slat)):
        depths, temps = prof[keys[j[i]]]
        dd = min(depth_m[i], depths.max())
        out[i] = temps[np.abs(depths - dd).argmin()]
    return np.round(out, 1)


def _ray_cast(px, py, vx, vy):
    """Even-odd point-in-polygon for one ring, vectorized over points."""
    inside = np.zeros(len(px), dtype=bool)
    n = len(vx)
    j = n - 1
    for i in range(n):
        cond = ((vy[i] > py) != (vy[j] > py)) & (
            px < (vx[j] - vx[i]) * (py - vy[i]) / (vy[j] - vy[i] + 1e-12) + vx[i]
        )
        inside ^= cond
        j = i
    return inside


def mpa_inside(mpa_path, slat, slon):
    """For each cell, whether it lies inside a marine protected area, and that
    area's name. Bbox-prefilters cells per ring so 800+ polygons stay cheap."""
    gj = json.loads(Path(mpa_path).read_text())
    inside = np.zeros(len(slat), dtype=bool)
    names = np.array([None] * len(slat), dtype=object)
    for f in gj["features"]:
        nm = f["properties"].get("name") or f["properties"].get("orig_name") or "Protected area"
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = np.asarray(poly[0], dtype=float)  # exterior ring (lon, lat)
            if len(ring) < 4:
                continue
            if len(ring) > 300:  # EMODnet rings are very dense; decimate for speed
                ring = ring[:: len(ring) // 300]
            rlon, rlat = ring[:, 0], ring[:, 1]
            cand = (
                (slat >= rlat.min()) & (slat <= rlat.max())
                & (slon >= rlon.min()) & (slon <= rlon.max()) & (~inside)
            )
            ci = np.where(cand)[0]
            if ci.size == 0:
                continue
            hit = _ray_cast(slon[ci], slat[ci], rlon, rlat)
            inside[ci[hit]] = True
            names[ci[hit]] = nm
    return inside, names


def depth_fit(d):
    """Depth suitability: ideal ~25-55 m, graded falloff to the band edges."""
    if 25.0 <= d <= 55.0:
        return 1.0
    if d < 25.0:
        return 0.6 + 0.4 * (d - DEPTH_MIN) / 5.0
    return max(0.2, 1.0 - (d - 55.0) / (DEPTH_MAX - 55.0) * 0.8)


def shore_fit(km):
    """Distance-to-shore suitability: ideal ~10-60 km, graded falloff either side."""
    if 10.0 <= km <= 60.0:
        return 1.0
    if km < 10.0:
        return 0.2 + 0.8 * km / 10.0
    return max(0.1, 1.0 - (km - 60.0) / 90.0)


def current_fit(v):
    """Current suitability: a moderate band (~0.1-0.4 m/s) is best."""
    if np.isnan(v):
        return np.nan
    if 0.1 <= v <= 0.4:
        return 1.0
    if v < 0.1:
        return 0.3 + 7.0 * v
    return max(0.1, 1.0 - (v - 0.4) / 0.6)


def slope_fit(s):
    """Slope suitability: flat (<0.5 deg) is ideal, falling off to ~5 deg."""
    if np.isnan(s):
        return np.nan
    if s <= 0.5:
        return 1.0
    return max(0.05, 1.0 - (s - 0.5) / 4.5)


def main():
    # --- 1. Candidate cells: bathymetry in the operating depth band ---
    bathy = pd.read_csv(DATA / "sea" / "bathymetry.csv", skiprows=[1])
    slope_df = seabed_slope(bathy)
    depth = -bathy.altitude
    sites = bathy[(depth >= DEPTH_MIN) & (depth <= DEPTH_MAX)].rename(
        columns={"latitude": "lat", "longitude": "lon"}
    ).copy()
    sites["depth_m"] = -sites.pop("altitude")
    sites = sites.reset_index(drop=True)
    print(f"{len(sites)} cells in {DEPTH_MIN:.0f}-{DEPTH_MAX:.0f} m band")
    slat, slon = sites.lat.to_numpy(), sites.lon.to_numpy()

    # --- 2. Nearest wind farm: grouping + distance + capacity ---
    farms = json.loads(FARMS.read_text())["features"]
    flat = np.array([f["geometry"]["coordinates"][1] for f in farms])
    flon = np.array([f["geometry"]["coordinates"][0] for f in farms])
    fname = [f["properties"].get("name") or "Unnamed farm" for f in farms]
    fmw = np.array([f["properties"].get("power_mw") or np.nan for f in farms], dtype=float)
    d, j = nearest(slat, slon, flat, flon)
    sites["dist_windpark_km"] = d.round(1)
    sites["fi"] = j
    sites["wf_name"] = [fname[k] for k in j]
    sites["wf_power_mw"] = fmw[j]
    sites = sites[sites.dist_windpark_km <= MAX_WINDPARK_KM].reset_index(drop=True)
    slat, slon = sites.lat.to_numpy(), sites.lon.to_numpy()
    print(f"{len(sites)} cells within {MAX_WINDPARK_KM:.0f} km of a wind farm")

    # --- 3. Grid landing: nearest onshore PyPSA bus (hard filter + country) ---
    buses = pd.read_csv(DATA / "pypsa" / "buses.csv", quotechar="'")
    buses = buses[(buses.under_construction == "f") & (buses.dc == "f")]
    d, j = nearest(slat, slon, buses.y.to_numpy(), buses.x.to_numpy())
    sites["dist_grid_km"] = d.round(1)
    sites["landing_country"] = buses.country.to_numpy()[j]
    sites = sites[sites.dist_grid_km <= MAX_GRID_KM].reset_index(drop=True)
    slat, slon = sites.lat.to_numpy(), sites.lon.to_numpy()
    print(f"{len(sites)} cells within {MAX_GRID_KM:.0f} km of an onshore bus")

    # --- 4. Bottom-water temperature at the seabed (WOA, warm season) ---
    sites["bot_temp_c"] = bottom_temp(
        DATA / "sea" / "woa_bottemp.csv", slat, slon, sites.depth_m.to_numpy()
    )

    # --- 5. Wind: mean ASCAT speed (two longitude bands stitched) ---
    wind = pd.concat([
        grid_mean(DATA / "sea" / "wind_east.csv", "x_wind", "y_wind"),
        grid_mean(DATA / "sea" / "wind_west.csv", "x_wind", "y_wind"),
    ], ignore_index=True).dropna(subset=["speed"])
    _, j = nearest(slat, slon, wind.latitude.to_numpy(), wind.longitude.to_numpy())
    sites["wind_ms"] = wind.speed.to_numpy()[j].round(2)

    # --- 6. Current: mean OSCAR surface speed ---
    cur = grid_mean(DATA / "sea" / "current.csv", "u", "v").dropna(subset=["speed"])
    _, j = nearest(slat, slon, cur.latitude.to_numpy(), cur.longitude.to_numpy())
    sites["current_ms"] = cur.speed.to_numpy()[j].round(3)

    # --- 7. Distance to shore + seabed slope ---
    clat, clon = coastline_vertices()
    d, _ = nearest(slat, slon, clat, clon, chunk=200)
    sites["dist_shore_km"] = d.round(1)
    sites = sites.merge(
        slope_df.assign(lat=slope_df.lat.round(4), lon=slope_df.lon.round(4)),
        left_on=[sites.lat.round(4), sites.lon.round(4)], right_on=["lat", "lon"],
        how="left", suffixes=("", "_s"),
    )
    sites["slope_deg"] = sites.slope_deg.round(3)

    # --- 8. Marine protected areas (inside test) ---
    inside, names = mpa_inside(DATA / "sea" / "mpa.json", slat, slon)
    sites["in_mpa"] = inside
    sites["mpa_status"] = np.where(inside, names, "open water")

    # --- 9. Negative-price probability by landing country ---
    neg = json.loads((DATA / "sea" / "negprice.json").read_text())
    sites["negprice_pct"] = sites.landing_country.map(neg)
    sites["negprice_pct"] = sites.negprice_pct.fillna(np.median(list(neg.values())))

    # --- 10. Scores (1 = best). Curves for depth/shore/current/slope; ---
    #          binary for MPA; percentile for the monotonic factors. ---
    sites["s_depth"] = sites.depth_m.map(depth_fit).round(3)
    sites["s_shore"] = sites.dist_shore_km.map(shore_fit).round(3)
    sites["s_current"] = sites.current_ms.map(current_fit).round(3)
    sites["s_slope"] = sites.slope_deg.map(slope_fit).round(3)
    sites["s_mpa"] = np.where(sites.in_mpa, 0.0, 1.0)

    lower_is_better = {"s_windpark": "dist_windpark_km", "s_bottemp": "bot_temp_c"}
    higher_is_better = {"s_wind": "wind_ms", "s_capacity": "wf_power_mw",
                        "s_negprice": "negprice_pct"}
    for s, raw in lower_is_better.items():
        sites[s] = (1 - sites[raw].rank(pct=True)).round(3)
    for s, raw in higher_is_better.items():
        sites[s] = sites[raw].rank(pct=True).round(3)

    score_keys = ["s_windpark", "s_depth", "s_shore", "s_bottemp", "s_wind",
                  "s_current", "s_capacity", "s_slope", "s_mpa", "s_negprice"]
    raw_keys = ["dist_windpark_km", "depth_m", "dist_shore_km", "bot_temp_c",
                "wind_ms", "current_ms", "wf_power_mw", "slope_deg", "negprice_pct"]

    for s in score_keys:  # residual NaN (data gaps) -> median so the cell survives
        sites[s] = sites[s].fillna(sites[s].median())

    # --- 11. Write GeoJSON ---
    feats = []
    for r in sites.itertuples():
        props = {"fi": int(r.fi), "wf_name": r.wf_name, "mpa_status": r.mpa_status}
        for k in score_keys:
            props[k] = float(getattr(r, k))
        for k in raw_keys:
            v = getattr(r, k)
            props[k] = None if pd.isna(v) else float(v)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(r.lon, 3), round(r.lat, 3)]},
            "properties": props,
        })
    OUT.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False
    ))
    composite = sites[score_keys].mean(axis=1)
    print(f"{len(feats)} cells -> {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
    print("raw ranges:",
          sites[["bot_temp_c", "slope_deg", "wind_ms", "current_ms", "negprice_pct"]]
          .describe().loc[["min", "50%", "max"]].round(2).to_dict())
    print(f"cells inside an MPA: {int(sites.in_mpa.sum())} / {len(sites)}")
    print("composite (equal weights):",
          composite.describe()[["min", "50%", "max"]].round(3).to_dict())
    print(f"distinct wind farms with cells: {sites.fi.nunique()}")


if __name__ == "__main__":
    main()
