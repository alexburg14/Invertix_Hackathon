#!/usr/bin/env python3
"""Probability of negative day-ahead electricity prices, per country.

Underwater data centers co-located with offshore wind can soak up surplus power
exactly when prices go negative (wind oversupply), so the *frequency* of
negative-price hours is a real siting signal. We pull ~1 year of hourly
day-ahead prices per bidding zone from the free Fraunhofer Energy-Charts API
(no auth), compute the share of hours below 0 EUR/MWh, and aggregate to ISO2
country codes (averaging multi-zone countries).

Output: data/sea/negprice.json  -> {"DE": 6.2, "DK": 8.1, ...}  (percent)
Run:    python3 scripts/fetch_negprice.py
"""
import json
import ssl
import time
import urllib.request
from pathlib import Path

# Sandbox CA bundles are often incomplete; this API is public read-only data.
_CTX = ssl._create_unverified_context()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sea" / "negprice.json"
API = "https://api.energy-charts.info/price?bzn={zone}&start={start}&end={end}"
START, END = "2025-06-01", "2026-06-01"

# Energy-Charts bidding zone -> ISO2 country (multi-zone countries averaged).
# GB / IE are not served by this free API; those cells fall back to the median.
ZONE_TO_ISO2 = {
    "AT": "AT", "BE": "BE", "CH": "CH", "CZ": "CZ", "DE-LU": "DE",
    "DK1": "DK", "DK2": "DK", "EE": "EE", "ES": "ES", "FI": "FI",
    "FR": "FR", "GR": "GR", "HR": "HR", "HU": "HU", "LT": "LT",
    "LV": "LV", "NL": "NL", "NO2": "NO", "PL": "PL", "PT": "PT",
    "RO": "RO", "SE4": "SE", "SI": "SI", "SK": "SK",
}


def neg_pct(zone):
    """Percent of hours with price < 0 over the window, or None on failure.
    Retries with backoff: the API rate-limits rapid sequential requests."""
    url = API.format(zone=zone, start=START, end=END)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EnerSite/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=60, context=_CTX).read())
            prices = [p for p in d.get("price", []) if p is not None]
            if not prices:
                return None
            return round(100.0 * sum(1 for p in prices if p < 0) / len(prices), 2)
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return None


def main():
    by_country = {}
    for zone, iso2 in ZONE_TO_ISO2.items():
        pct = neg_pct(zone)
        print(f"  {zone:7s} -> {iso2}: {pct}")
        if pct is not None:
            by_country.setdefault(iso2, []).append(pct)
        time.sleep(1.5)  # stay under the API rate limit
    result = {c: round(sum(v) / len(v), 2) for c, v in by_country.items()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=0))
    print(f"{len(result)} countries -> {OUT}: {result}")


if __name__ == "__main__":
    main()
