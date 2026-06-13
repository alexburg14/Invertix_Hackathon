#!/usr/bin/env python3
"""Wind-farm capacity factor and zero-production hours from Renewables.ninja.

For each offshore wind farm, queries the free Renewables.ninja API with the
farm's lat/lon for a full year of hourly simulated output (MERRA-2 reanalysis).
Computes:
  - capacity_factor: mean hourly output / rated capacity (0-1)
  - zero_hours_pct:  share of hours with zero production (%)

Output: data/sea/uptime.json  -> {"0": {"cf": 0.45, "zero_pct": 2.1}, ...}
        keyed by farm index in windfarms.geojson.
Run:    python3 scripts/fetch_uptime.py   (~15-20 min for ~400 farms)
"""
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

_CTX = ssl._create_unverified_context()

ROOT = Path(__file__).resolve().parent.parent
WF_PATH = ROOT / "web" / "app" / "public" / "windfarms.geojson"
OUT = ROOT / "data" / "sea" / "uptime.json"

API = (
    "https://www.renewables.ninja/api/data/wind?"
    "lat={lat}&lon={lon}&date_from=2019-01-01&date_to=2019-12-31"
    "&capacity=1&height=100&turbine=Vestas+V164+8000&format=json"
)


def fetch_cf(lat, lon):
    url = API.format(lat=round(lat, 4), lon=round(lon, 4))
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EnerSite/1.0"})
            raw = urllib.request.urlopen(req, timeout=60, context=_CTX).read()
            data = json.loads(raw)
            vals = [v["electricity"] for v in data["data"].values()]
            if not vals:
                return None
            cf = sum(vals) / len(vals)
            zero_pct = 100.0 * sum(1 for v in vals if v == 0) / len(vals)
            return round(cf, 4), round(zero_pct, 2)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10.0 * (attempt + 1)
                print(f"    rate-limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
            else:
                time.sleep(3.0 * (attempt + 1))
        except Exception:
            time.sleep(3.0 * (attempt + 1))
    return None


def main():
    wf = json.loads(WF_PATH.read_text())
    farms = wf["features"]

    # Resume from partial results if they exist
    if OUT.exists():
        result = json.loads(OUT.read_text())
        print(f"Resuming: {len(result)} farms already done")
    else:
        result = {}

    print(f"Fetching capacity factors for {len(farms)} farms...", flush=True)

    for i, f in enumerate(farms):
        if str(i) in result:
            continue
        lon, lat = f["geometry"]["coordinates"]
        name = f["properties"].get("name", "?")
        out = fetch_cf(lat, lon)
        if out:
            cf, zero_pct = out
            result[str(i)] = {"cf": cf, "zero_pct": zero_pct}
            print(f"  [{i+1}/{len(farms)}] {name}: CF={cf:.3f}, zero={zero_pct:.1f}%", flush=True)
        else:
            print(f"  [{i+1}/{len(farms)}] {name}: FAILED", flush=True)

        # Save incrementally every 10 farms
        if len(result) % 10 == 0:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(result, indent=0))

        time.sleep(2.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=0))
    cfs = [v["cf"] for v in result.values()]
    print(f"\n{len(result)}/{len(farms)} farms -> {OUT}")
    if cfs:
        print(f"CF: min={min(cfs):.3f} median={sorted(cfs)[len(cfs)//2]:.3f} max={max(cfs):.3f}")


if __name__ == "__main__":
    main()
