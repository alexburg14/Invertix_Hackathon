# EnerSite Subsea — Underwater Data-Center Siting & Power

Invertix Track, Energy × AI Hackathon Munich. A siting decision engine for
**subsea data centers** (Microsoft-Natick-style): enter a DC size in MW, get
ranked seafloor sites across the European shelf with transparent,
slider-weighted trade-off scores — and a per-site explanation.

Why subsea? Passive seawater cooling (PUE ~1.07 measured by Natick, vs ~1.2 for
a good land build), co-location with offshore wind, and an escape hatch from the
congested onshore grid.

## How it works

Candidate sites = ocean grid cells (~0.1°) in the 20–120 m depth band. Every
data layer is collapsed onto those cells offline by `scripts/build_subsea_sites.py`,
which writes a single `sites.geojson` the frontend consumes. Scoring weights are
applied client-side, so re-ranking is instant. Power is assumed to land at the
nearest onshore grid bus, whose country sets price and carbon intensity.

**Per-site metrics**

| Metric | Source | Join |
|---|---|---|
| Depth (hard filter, 20–120 m) | ETOPO bathymetry via NOAA ERDDAP | grid cell value |
| Cooling (warmest-month SST) | NOAA OISST v2.1 via ERDDAP | nearest 0.25° cell |
| Grid landing (km, kV, headroom) | PyPSA-Eur buses + lines (Zenodo 18619025) | nearest onshore bus |
| Wholesale price (EUR/MWh) | Ember monthly prices | landing-bus country |
| Carbon intensity (gCO2/kWh) | Ember yearly data | landing-bus country |
| Fiber (km to subsea cable) | TeleGeography submarine cable map | nearest densified cable vertex |
| Offshore-wind PPA (MW in 50 km) | OpenStreetMap `power=plant` wind | radius aggregation |

Hard filters: depth band, ≤150 km to an onshore bus, and the landing bus must
have grid headroom for the requested MW (at PUE 1.07, 20 % of connected rating
assumed available).

## Setup

```bash
./scripts/fetch_data.sh           # downloads all raw layers (~90 MB, public sources)
python3 scripts/build_subsea_sites.py   # -> web/sites.geojson + web/cables.json
python3 -m http.server 8123 -d web      # open http://localhost:8123
```
