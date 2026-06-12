import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";

const WEIGHTS = [
  { key: "s_price_eur_mwh", label: "Price", raw: "price_eur_mwh", unit: "EUR/MWh", fmt: (v) => v.toFixed(0) },
  { key: "s_carbon", label: "Carbon intensity", raw: "gco2_kwh", unit: "g/kWh", fmt: (v) => v.toFixed(0) },
  { key: "s_dist_dc_km", label: "Connectivity", raw: "dist_dc_km", unit: "km to nearest DC", fmt: (v) => v.toFixed(0) },
  { key: "s_ppa_mw_50km", label: "PPA potential", raw: "ppa_mw_50km", unit: "MW renewables / 50km", fmt: (v) => v.toFixed(0) },
];

const TOP_N = 60;

// Of a bus's total connected line rating, assume at most this share is
// realistically available to a new load (rest carries existing flows).
const AVAILABLE_SHARE = 0.2;
const PUE = 1.2; // IEA-typical for new builds: grid draw = IT load * PUE

function scoreColor(s) {
  // s in [0,1] -> red -> amber -> green
  const hue = 14 + s * 120; // 14 (red) to 134 (green)
  return `hsl(${hue}, 75%, 50%)`;
}

export default function App() {
  const [sites, setSites] = useState(null);
  const [mw, setMw] = useState(50);
  const [weights, setWeights] = useState({
    s_price_eur_mwh: 1,
    s_carbon: 1,
    s_dist_dc_km: 1,
    s_ppa_mw_50km: 1,
  });
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    fetch("/sites.geojson")
      .then((r) => r.json())
      .then((d) =>
        setSites(
          d.features.map((f) => {
            const p = f.properties;
            return {
              ...p,
              s_carbon: (p.s_gco2_kwh + p.s_clean_share_pct) / 2,
              lat: f.geometry.coordinates[1],
              lon: f.geometry.coordinates[0],
            };
          })
        )
      );
  }, []);

  const need = (mw * PUE) / AVAILABLE_SHARE; // MVA of connected rating required

  const ranked = useMemo(() => {
    if (!sites) return [];
    const totalW = WEIGHTS.reduce((s, w) => s + weights[w.key], 0) || 1;
    const candidates = sites.filter((s) => s.headroom_mva >= need);
    const scored = candidates.map((s) => {
      const score = WEIGHTS.reduce((acc, w) => acc + weights[w.key] * (s[w.key] ?? 0), 0) / totalW;
      return { ...s, _score: score };
    });
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, TOP_N).map((s, i) => ({ ...s, _rank: i + 1 }));
  }, [sites, mw, weights, need]);

  const setWeight = (key, val) => setWeights((w) => ({ ...w, [key]: val }));

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>EnerSite</h1>
          <span>data-center siting</span>
        </div>

        <div className="field">
          <label>Data-center size</label>
          <div className="mw-input">
            <input
              type="number"
              min="0"
              value={mw}
              onChange={(e) => setMw(Number(e.target.value) || 0)}
            />
            <span className="unit">MW</span>
          </div>
        </div>

        <div className="section-title">Trade-off weights</div>
        {WEIGHTS.map((w) => (
          <div className="slider-row" key={w.key}>
            <div className="top">
              <span>{w.label}</span>
              <span className="val">{weights[w.key].toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              value={weights[w.key]}
              onChange={(e) => setWeight(w.key, Number(e.target.value))}
            />
          </div>
        ))}

        <div className="stats">
          {sites ? (
            <>
              <div><b>{ranked.length}</b> sites can host {mw} MW (needs &ge; {Math.round(need).toLocaleString()} MVA connected @ {AVAILABLE_SHARE * 100}% available, PUE {PUE}) of {sites.length} total.</div>
              <div style={{ marginTop: 6 }}>Showing top {Math.min(TOP_N, ranked.length)}, ranked by composite score.</div>
            </>
          ) : (
            "Loading site data..."
          )}
        </div>
      </aside>

      <div className="map-wrap">
        <MapContainer className="map" center={[50, 12]} zoom={5} preferCanvas>
          <TileLayer
            attribution='&copy; OpenStreetMap &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          {ranked.map((s) => (
            <CircleMarker
              key={s.bus_id}
              center={[s.lat, s.lon]}
              radius={s._rank === 1 ? 10 : 6}
              pathOptions={{
                color: scoreColor(s._score),
                fillColor: scoreColor(s._score),
                fillOpacity: 0.85,
                weight: selected?.bus_id === s.bus_id ? 3 : 1,
              }}
              eventHandlers={{ click: () => setSelected(s) }}
            >
              <Tooltip>
                #{s._rank} &middot; {s.country} &middot; score {s._score.toFixed(2)}
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>

        <div className="legend">
          <div className="bar" />
          <div className="labels">
            <span>worse</span>
            <span>better</span>
          </div>
        </div>

        {selected ? (
          <ExplainPanel site={selected} onClose={() => setSelected(null)} />
        ) : (
          <div className="empty-hint">
            Click a site on the map to see why it ranks where it does — raw
            values and how each factor contributes to its score.
          </div>
        )}
      </div>
    </div>
  );
}

function ExplainPanel({ site, onClose }) {
  return (
    <div className="explain">
      <button className="close-btn" onClick={onClose}>✕</button>
      <div className="rank-pill">Rank #{site._rank}</div>
      <h2>{site.country} &middot; {site.voltage} kV bus</h2>
      <div className="sub">{site.bus_id}</div>
      <div className="score-big">
        {site._score.toFixed(2)} <small>composite score</small>
      </div>
      {WEIGHTS.map((w) => (
        <div className="metric-row" key={w.key}>
          <span className="name">{w.label}</span>
          <span className="raw">{w.fmt(site[w.raw])} {w.unit}</span>
          <div className="bar-bg">
            <div className="bar-fg" style={{ width: `${(site[w.key] ?? 0) * 100}%`, background: scoreColor(site[w.key] ?? 0) }} />
          </div>
        </div>
      ))}
      <div className="metric-row">
        <span className="name">Nearest data center</span>
        <span className="raw">{site.nearest_dc}</span>
        <div />
      </div>
      <div className="metric-row">
        <span className="name">Headroom (hard filter)</span>
        <span className="raw">{site.headroom_mva.toFixed(0)} MVA</span>
        <div />
      </div>
    </div>
  );
}
