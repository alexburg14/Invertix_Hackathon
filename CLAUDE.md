# EnerSite — project context

One-day hackathon build (Invertix Track, Energy × AI Hackathon Munich, June 2026).
Goal: a data-center siting decision engine. User enters a DC size in MW → ranked
candidate sites on a map, weight sliders for trade-offs, click-to-explain panel.
enersite.app is *inspiration only* — this is a from-scratch build.

**Current focus: underwater data centers.** The live frontend ranks *offshore
wind farms* only (`web/app/public/windfarms.geojson`, see "Underwater DC mode"
below). The onshore grid pipeline (`build_sites.py` → `sites.geojson`) is kept in
the repo but no longer wired into the UI.

## Architecture

Two stages, deliberately decoupled:

1. **Offline prep** (`scripts/build_sites.py`): collapses every data layer onto one
   row per candidate site (= PyPSA grid bus) and writes `web/sites.geojson`.
   All expensive joins happen here, once.
2. **Frontend** (`web/`): loads that single GeoJSON; composite score
   `Σ wᵢ·sᵢ` is computed client-side so weight sliders re-rank instantly.
   No backend at demo time.

## Data layers & join patterns (all verified working)

| Layer | Source | Join pattern |
|---|---|---|
| Candidate sites + headroom | `data/pypsa/buses.csv` + `lines.csv` | sum s_nom of lines touching bus (ID match) |
| Price (EUR/MWh) | `data/ember/ember_prices_monthly.csv` | merge on country (ISO3→ISO2 needed) |
| Carbon (gCO2/kWh) | `data/ember/ember_yearly.csv` | merge on country, latest year |
| Connectivity | `data/osm_datacenters.json` (1.5k pts) | nearest-neighbor, numpy haversine |
| PPA potential | `data/osm_power_plants.json` (46k wind/solar) | radius sum (~50 km), parse `plant:output:electricity` |

### Underwater DC siting (offshore wind farms) — the live app

Candidate set: offshore wind farms as positions for *underwater* data centers
(Project-Natick style — clean power on-site + free seawater cooling).
`scripts/build_windfarms.py` pulls EMODnet Human Activities' `windfarms` WFS
layer (GeoJSON) and per farm samples two marine-risk layers, then writes
`web/app/public/windfarms.geojson` (~399 farms, 19 countries, ~280 GW):

| Per-farm layer | Source | Sampling |
|---|---|---|
| Water depth | EMODnet Bathymetry `depth_sample` REST | 1 GET per farm, parse `avg` (negative m) |
| Cargo route density | EMODnet HA WMS `routedensity_01` (Cargo) | GetFeatureInfo, `GRAY_INDEX` (WMS 1.3.0 = lat,lon bbox order) |

Both sampled threaded (~16 s total for ~800 calls). Five scored factors
(1 = best): `s_power` (capacity), `s_coast` (proximity), `s_depth` (suitability
sweet-spot ~15-60 m, *not* monotonic), `s_ship` (1−percentile of cargo density),
`s_status` (operational readiness). `power_mw` is the hard filter; all five
sliders feed the composite. Map has a Leaflet **heat layer** (`leaflet.heat`,
intensity = composite score) toggle + click-to-zoom (`flyTo`) on cards/markers.

**Water currents: not wired.** No free per-point service — EMODnet HF-radar is
patchy coastal-only and errored; full coverage needs Copernicus Marine creds.
Add later via Copernicus `sea_water_velocity` (uo/vo) if a key is available.

## Gotchas (hard-won, do not rediscover)

- **PyPSA CSVs quote geometry with single quotes** — always
  `pd.read_csv(..., quotechar="'")`, otherwise rows shift and joins silently fail.
- The Zenodo record (18619025) has **no generators table** — renewables come from
  OSM power plants instead.
- Ember files key on ISO3 (`DEU`); PyPSA buses on ISO2 (`DE`). Map explicitly.
- Only ~11k of 46k OSM plants have a MW tag — use tagged capacity where present,
  plant count as fallback signal.
- No sklearn in the env (numpy 1.26 / pandas 1.5.3 / python 3.11). Use numpy
  broadcasting for distance math; scale (~7k × 46k) is fine.
- Headroom is a **hard filter** (site must fit the requested MW), not a score term.
  Normalize score terms by percentile rank, not min-max (outliers flatten it).
- EMODnet `windfarms` keys on full country names (`Germany`), not ISO2 — map to
  ISO2 so the shared country filter works across both modes. `dist_coast` is in
  metres. Drop `Dismantled` farms and any with no `power_mw`.

## Conventions

- Raw data lives in `data/` (gitignored); refetch via `scripts/fetch_data.sh`.
- Demo scope: Europe-wide data, Germany as the polished demo region.
- Keep raw values *and* normalized `s_*` scores in GeoJSON properties — the
  explain panel shows real units, the sliders use the scores.
