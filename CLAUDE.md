# EnerSite Subsea — project context

One-day hackathon build (Invertix Track, Energy × AI Hackathon Munich, June 2026).
Goal: a siting decision engine for **underwater data centers** (Natick-style).
User enters a DC size in MW → ranked seafloor sites on a map, weight sliders for
trade-offs, click-to-explain panel with annual impact numbers.
Pivoted from an onshore version (still in `scripts/build_sites.py`, unused).

## Architecture

Two stages, deliberately decoupled:

1. **Offline prep** (`scripts/build_subsea_sites.py`): collapses every data layer
   onto one row per candidate site (= ocean grid cell, ~0.1°, 20–120 m deep) and
   writes `web/sites.geojson` + `web/cables.json`. All expensive joins happen here.
2. **Frontend** (`web/`): loads those two GeoJSONs; composite score `Σ wᵢ·sᵢ`
   is computed client-side so weight sliders re-rank instantly. No backend.

Power is assumed to land at the **nearest onshore PyPSA bus** — that bus's
country sets price/carbon, and its headroom is the feasibility gate.

## Data layers & join patterns (all verified working)

| Layer | Source | Join pattern |
|---|---|---|
| Candidate cells (depth) | `data/sea/bathymetry.csv` (ETOPO via ERDDAP) | hard filter 20–120 m |
| Cooling (SST mean/max) | `data/sea/sst_monthly.csv` (OISST via ERDDAP) | nearest 0.25° sea cell |
| Grid landing + headroom | `data/pypsa/buses.csv` + `lines.csv` | nearest bus; sum s_nom of touching lines |
| Price (EUR/MWh) | `data/ember/ember_prices_monthly.csv` | landing country (ISO3→ISO2 needed) |
| Carbon (gCO2/kWh) | `data/ember/ember_yearly.csv` | landing country, latest year |
| Fiber | `data/sea/cables.json` (TeleGeography) | nearest densified cable vertex |
| Offshore-wind PPA | `data/osm_power_plants.json` filtered to wind | radius sum (~50 km) |

## Gotchas (hard-won, do not rediscover)

- **PyPSA CSVs quote geometry with single quotes** — always
  `pd.read_csv(..., quotechar="'")`, otherwise rows shift and joins silently fail.
- ERDDAP subset URLs use brackets — pass `curl -g` to disable globbing. CSV
  responses have a **units row at line 2**: read with `skiprows=[1]`.
- OISST is the `ncdcOisst21Agg_LonPM180` dataset (lon −180..180, not 0..360);
  time subset by index `[last-360:30:last]` ≈ monthly slices of the last year.
- TeleGeography cable polylines are **sparse (~19 vertices/cable)** — densify
  segments to ~10 km before point-to-line distance, or distances overestimate badly.
- Ember files key on ISO3 (`DEU`); PyPSA buses on ISO2 (`DE`). Map explicitly.
- No sklearn/rasterio/shapely in the env (numpy 1.26 / pandas 1.5.3 / py 3.11;
  PIL + scipy + xarray ARE available). Use chunked numpy haversine for distances.
- Depth band + dist-to-grid ≤150 km + landing-bus headroom are **hard filters**,
  not score terms. Normalize score terms by percentile rank, not min-max.
- PUE 1.07 (subsea, Microsoft Natick measured) vs 1.2 (land) — the delta is the
  headline pitch number (cooling-energy savings in GWh/yr).

## Conventions

- Raw data lives in `data/` (gitignored); refetch via `scripts/fetch_data.sh`.
- Demo scope: European shelf, German Bight (North Sea) as the polished demo region.
- Keep raw values *and* normalized `s_*` scores in GeoJSON properties — the
  explain panel shows real units, the sliders use the scores.
- Dev server: `python3 -m http.server 8123 -d web`.
