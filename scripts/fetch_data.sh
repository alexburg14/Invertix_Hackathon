#!/usr/bin/env bash
# Fetch all raw data layers into data/. Idempotent — skips files that exist.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/pypsa data/ember

# 1. PyPSA-Eur prebuilt OSM network (Zenodo 18619025) — buses, lines, links
for f in buses.csv lines.csv links.csv transformers.csv converters.csv; do
  [ -f "data/pypsa/$f" ] || curl -sL -o "data/pypsa/$f" "https://zenodo.org/records/18619025/files/$f?download=1"
done

# 2. Ember yearly data (carbon intensity, clean share) + monthly wholesale prices
[ -f data/ember/ember_yearly.csv ] || curl -sL -o data/ember/ember_yearly.csv \
  "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/yearly_full_release_long_format.csv"
[ -f data/ember/ember_prices_monthly.csv ] || curl -sL -o data/ember/ember_prices_monthly.csv \
  "https://files.ember-energy.org/public-downloads/price/outputs/european_wholesale_electricity_price_data_monthly.csv"

# 3. Existing data centers from OSM Overpass (Europe bbox)
[ -f data/osm_datacenters.json ] || curl -s -m 300 -A "Mozilla/5.0 (EnerSite data fetch script)" -X POST "https://overpass.kumi.systems/api/interpreter" \
  --data-urlencode 'data=[out:json][timeout:120];(node["telecom"="data_center"](35,-11,72,32);way["telecom"="data_center"](35,-11,72,32);relation["telecom"="data_center"](35,-11,72,32););out center tags;' \
  -o data/osm_datacenters.json

# 4. Wind & solar power plants from OSM (PPA-potential layer)
[ -f data/osm_power_plants.json ] || curl -s -m 300 -A "Mozilla/5.0 (EnerSite data fetch script)" -X POST "https://overpass.kumi.systems/api/interpreter" \
  --data-urlencode 'data=[out:json][timeout:180];(way["power"="plant"]["plant:source"~"wind|solar"](35,-11,72,32);relation["power"="plant"]["plant:source"~"wind|solar"](35,-11,72,32););out center tags;' \
  -o data/osm_power_plants.json

echo "All data layers present:"
ls -lh data/pypsa data/ember data/osm_datacenters.json
