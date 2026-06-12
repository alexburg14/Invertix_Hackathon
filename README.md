# Data-Center Siting & Power

Invertix Track, Energy × AI Hackathon Munich. Turns the "overlay map" idea into a
decision engine: enter a data-center size in MW, get ranked candidate sites with
transparent, slider-weighted trade-off scores — and a per-site explanation.

## How it works

Candidate sites = transmission-grid buses from the PyPSA-Eur prebuilt OSM network.
Every data layer is collapsed onto those points offline by `scripts/build_sites.py`,
which writes a single `sites.geojson` the frontend consumes. Scoring weights are
applied client-side, so re-ranking is instant.

**Per-site metrics**

| Metric | Source | Join |
|---|---|---|
| Grid headroom (MVA, hard filter) | PyPSA-Eur lines (Zenodo 18619025) | sum of connected line ratings |
| Congestion proxy | PyPSA-Eur topology | line capacity vs. regional demand |
| Wholesale price (EUR/MWh) | Ember monthly prices | by country |
| Carbon intensity (gCO2/kWh) | Ember yearly data | by country |
| Connectivity (km to nearest DC) | OpenStreetMap `telecom=data_center` | nearest-neighbor |
| PPA potential (renewables nearby) | PyPSA-Eur generators | radius aggregation |

## Setup

```bash
./scripts/fetch_data.sh      # downloads all raw layers (~70 MB, public sources)
python3 scripts/build_sites.py   # -> web/sites.geojson
```
