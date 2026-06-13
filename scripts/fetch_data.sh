#!/usr/bin/env bash
# Fetch all raw data layers into data/. Idempotent — skips files that exist.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/sea

# 1. Bathymetry: ETOPO 1-arcmin via NOAA ERDDAP, Europe bbox, stride 3 (~0.05 deg)
#    (-g: brackets are ERDDAP subset syntax, not curl globs)
[ -f data/sea/bathymetry.csv ] || curl -sg -o data/sea/bathymetry.csv \
  "https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.csv?altitude[(35):3:(72)][(-11):3:(32)]"

# 2. Surface wind: Metop ASCAT 0.25 deg, ~monthly slices over the last year. The
#    grid is 0-360 and Europe straddles 0, so we pull two longitude bands
#    (east 0..32, west 349..359.75) and stitch them in the build step.
[ -f data/sea/wind_east.csv ] || curl -sg -o data/sea/wind_east.csv \
  "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdQMwind1day.csv?x_wind[last-330:30:last][(10.0)][(35):(72)][(0):(32)],y_wind[last-330:30:last][(10.0)][(35):(72)][(0):(32)]"
[ -f data/sea/wind_west.csv ] || curl -sg -o data/sea/wind_west.csv \
  "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdQMwind1day.csv?x_wind[last-330:30:last][(10.0)][(35):(72)][(349):(359.75)],y_wind[last-330:30:last][(10.0)][(35):(72)][(349):(359.75)]"

# 3. Surface currents: OSCAR 1/3 deg, 5-day composites. Grid is 20..420; Europe is
#    contiguous as lon 349..392 (remapped to -11..32 in the build step).
[ -f data/sea/current.csv ] || curl -sg -o data/sea/current.csv \
  "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplOscar.csv?u[last-60:5:last][(15.0)][(35):(72)][(349):(392)],v[last-60:5:last][(15.0)][(35):(72)][(349):(392)]"

# 4. Bottom-water temperature: World Ocean Atlas 2018, August (warm-season)
#     climatology, depths 20-125 m. lon is -180..180 so no wrap for Europe.
[ -f data/sea/woa_bottemp.csv ] || curl -sg -o data/sea/woa_bottemp.csv \
  "https://cwcgom.aoml.noaa.gov/erddap/griddap/WOA_TEMP_d48d_916b_3478.csv?t_an[7][(20.0):(125.0)][(35):(72)][(-11):(32)]"

# 5. Marine protected areas (EMODnet Human Activities, GeoJSON polygons)
[ -f data/sea/mpa.json ] || curl -s -o data/sea/mpa.json \
  "https://ows.emodnet-humanactivities.eu/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=emodnet:marineprotectedareas&outputFormat=application/json"

# 6. Negative-price probability per country (Fraunhofer Energy-Charts, no auth)
[ -f data/sea/negprice.json ] || python3 scripts/fetch_negprice.py

echo "All data layers present:"
ls -lh data/sea
