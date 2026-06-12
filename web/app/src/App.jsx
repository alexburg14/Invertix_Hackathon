import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";

const WEIGHTS = [
  { key: "s_price_eur_mwh", label: "Price", raw: "price_eur_mwh", unit: "EUR/MWh", fmt: (v) => v.toFixed(0), good: "cheap power", bad: "expensive power" },
  { key: "s_carbon", label: "Carbon intensity", raw: "gco2_kwh", unit: "g/kWh", fmt: (v) => v.toFixed(0), good: "clean grid", bad: "carbon-heavy grid" },
  { key: "s_dist_dc_km", label: "Connectivity", raw: "dist_dc_km", unit: "km to nearest DC", fmt: (v) => v.toFixed(0), good: "well connected", bad: "remote / poorly connected" },
  { key: "s_ppa_mw_50km", label: "PPA potential", raw: "ppa_mw_50km", unit: "MW renewables / 50km", fmt: (v) => v.toFixed(0), good: "strong PPA potential", bad: "little renewables nearby" },
];

const TOP_N = 60;

// Of a bus's total connected line rating, assume at most this share is
// realistically available to a new load (rest carries existing flows).
const AVAILABLE_SHARE = 0.2;
const PUE = 1.2; // IEA-typical for new builds: grid draw = IT load * PUE

const PRO_THRESHOLD = 0.7;
const CON_THRESHOLD = 0.3;

const WEIGHT_LABELS = ["Low", "Medium", "High"];

function Logo({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M17 5 L8 18 H15 L14 27 L24 13 H17 L17 5 Z" fill="white" />
    </svg>
  );
}

function scoreColor(s) {
  // s in [0,1] -> red -> amber -> green
  const hue = 14 + s * 120; // 14 (red) to 134 (green)
  return `hsl(${hue}, 75%, 50%)`;
}

export default function App() {
  const [sites, setSites] = useState(null);
  const [view, setView] = useState("landing");
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
      const pros = WEIGHTS.filter((w) => (s[w.key] ?? 0) >= PRO_THRESHOLD).map((w) => w.good);
      const cons = WEIGHTS.filter((w) => (s[w.key] ?? 0) <= CON_THRESHOLD).map((w) => w.bad);
      return { ...s, _score: score, _pros: pros, _cons: cons };
    });
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, TOP_N).map((s, i) => ({ ...s, _rank: i + 1 }));
  }, [sites, mw, weights, need]);

  const setWeight = (key, val) => setWeights((w) => ({ ...w, [key]: val }));

  if (view === "landing") {
    return (
      <Landing
        mw={mw}
        setMw={setMw}
        weights={weights}
        setWeight={setWeight}
        loading={!sites}
        onSubmit={() => setView("results")}
      />
    );
  }

  return (
    <div className="app">
      <aside className="sidebar results-sidebar">
        <div className="brand">
          <Logo />
          <h1>EnerSite</h1>
          <span>data-center siting</span>
        </div>

        <button className="back-btn" onClick={() => setView("landing")}>
          &larr; Edit preferences
        </button>

        <div className="stats">
          <div><b>{ranked.length}</b> sites can host {mw} MW (needs &ge; {Math.round(need).toLocaleString()} MVA connected @ {AVAILABLE_SHARE * 100}% available, PUE {PUE}) of {sites?.length ?? 0} total.</div>
        </div>

        <div className="ranked-list">
          {ranked.map((s) => (
            <SiteCard
              key={s.bus_id}
              site={s}
              active={selected?.bus_id === s.bus_id}
              onClick={() => setSelected(selected?.bus_id === s.bus_id ? null : s)}
            />
          ))}
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
              key={"halo-" + s.bus_id}
              center={[s.lat, s.lon]}
              radius={s._rank === 1 ? 18 : 10}
              pathOptions={{
                color: scoreColor(s._score),
                fillColor: scoreColor(s._score),
                fillOpacity: 0.12,
                weight: 0,
              }}
              interactive={false}
            />
          ))}
          {ranked.map((s) => (
            <CircleMarker
              key={s.bus_id}
              center={[s.lat, s.lon]}
              radius={s._rank === 1 ? 10 : 6}
              pathOptions={{
                color: "#0b1020",
                fillColor: scoreColor(s._score),
                fillOpacity: 0.9,
                weight: selected?.bus_id === s.bus_id ? 3 : 1.5,
              }}
              eventHandlers={{ click: () => setSelected(selected?.bus_id === s.bus_id ? null : s) }}
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
      </div>
    </div>
  );
}

function Landing({ mw, setMw, weights, setWeight, loading, onSubmit }) {
  return (
    <div className="landing">
      <div className="landing-card">
        <div className="brand">
          <Logo />
          <h1>EnerSite</h1>
          <span>data-center siting</span>
        </div>
        <p className="landing-sub">
          Tell us how big your data center is and what matters most to you —
          we'll rank candidate grid sites across Europe and explain the
          trade-offs of each.
        </p>

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

        <div className="section-title">What matters most to you?</div>
        {WEIGHTS.map((w) => (
          <div className="slider-row" key={w.key}>
            <div className="top">
              <span>{w.label}</span>
              <span className="val">{WEIGHT_LABELS[weights[w.key]]}</span>
            </div>
            <input
              type="range"
              min="0"
              max="2"
              step="1"
              value={weights[w.key]}
              onChange={(e) => setWeight(w.key, Number(e.target.value))}
            />
            <div className="slider-ticks">
              {WEIGHT_LABELS.map((l) => (
                <span key={l}>{l}</span>
              ))}
            </div>
          </div>
        ))}

        <button className="submit-btn" onClick={onSubmit} disabled={loading}>
          {loading ? "Loading site data..." : "Find sites →"}
        </button>
      </div>
    </div>
  );
}

function SiteCard({ site, active, onClick }) {
  return (
    <div className={"site-card" + (active ? " active" : "")} onClick={onClick}>
      <div className="site-card-head">
        <div className="rank-badge">#{site._rank}</div>
        <div className="site-card-title">
          <div className="site-card-name">{site.country} &middot; {site.voltage} kV</div>
          <div className="site-card-sub">{site.nearest_dc ? `near ${site.nearest_dc}` : site.bus_id}</div>
        </div>
        <div className="site-card-score" style={{ color: scoreColor(site._score) }}>
          {Math.round(site._score * 100)}
        </div>
      </div>

      {active && (
        <div className="site-card-details">
          {WEIGHTS.map((w) => (
            <div className="metric-row" key={w.key}>
              <div className="metric-top">
                <span className="name">{w.label}</span>
                <span className="raw">{w.fmt(site[w.raw])} {w.unit}</span>
              </div>
              <div className="bar-bg">
                <div className="bar-fg" style={{ width: `${(site[w.key] ?? 0) * 100}%`, background: scoreColor(site[w.key] ?? 0) }} />
              </div>
            </div>
          ))}
          <div className="metric-row">
            <div className="metric-top">
              <span className="name">Grid headroom</span>
              <span className="raw">{site.headroom_mva.toFixed(0)} MVA</span>
            </div>
          </div>
        </div>
      )}

      <div className="pros-cons">
        {site._pros.map((p) => (
          <span className="tag tag-pro" key={p}>+ {p}</span>
        ))}
        {site._cons.map((c) => (
          <span className="tag tag-con" key={c}>- {c}</span>
        ))}
      </div>
    </div>
  );
}
