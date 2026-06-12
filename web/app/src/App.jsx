import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";

// Underwater DCs sit beside offshore wind farms: clean power on-site, free
// seawater cooling. Each factor: wkey = which weight slider drives it,
// skey = precomputed score field (1 = best), raw/unit/fmt for the explain panel.
const FACTORS = [
  { wkey: "s_power", skey: "s_power", label: "Clean power capacity", raw: "power_mw", unit: "MW", fmt: (v) => v.toFixed(0), good: "large clean-power supply", bad: "limited power output" },
  { wkey: "s_coast", skey: "s_coast", label: "Proximity to shore", raw: "dist_coast_km", unit: "km to coast", fmt: (v) => v.toFixed(0), good: "easy cable landing", bad: "far offshore" },
  { wkey: "s_status", skey: "s_status", label: "Operational readiness", raw: "status", unit: "", fmt: (v) => v, good: "operational / near-term", bad: "early-stage" },
];

const TOP_N = 60;

const OFFSHORE_PUE = 1.1; // underwater DCs cool with seawater (Natick ~1.07)

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

// Pans/zooms the map to the selected wind farm when it changes.
function FlyTo({ selected }) {
  const map = useMap();
  useEffect(() => {
    if (selected) {
      map.flyTo([selected.lat, selected.lon], 9, { duration: 0.8 });
    }
  }, [selected, map]);
  return null;
}

export default function App() {
  const [farms, setFarms] = useState(null);
  const [view, setView] = useState("landing");
  const [mw, setMw] = useState(50);
  const [weights, setWeights] = useState({
    s_power: 1,
    s_coast: 1,
    s_status: 1,
  });
  const [selected, setSelected] = useState(null);
  const [country, setCountry] = useState("all");
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    fetch("/windfarms.geojson")
      .then((r) => r.json())
      .then((d) =>
        setFarms(
          d.features.map((f) => {
            const p = f.properties;
            return {
              ...p,
              _id: `wf_${f.geometry.coordinates[0]}_${f.geometry.coordinates[1]}`,
              lat: f.geometry.coordinates[1],
              lon: f.geometry.coordinates[0],
            };
          })
        )
      );
  }, []);

  const needPower = mw * OFFSHORE_PUE; // farm capacity (MW) the DC must fit within

  const countries = useMemo(() => {
    if (!farms) return [];
    return [...new Set(farms.map((s) => s.country))].sort();
  }, [farms]);

  const ranked = useMemo(() => {
    if (!farms) return [];
    const totalW = FACTORS.reduce((s, f) => s + weights[f.wkey], 0) || 1;
    const candidates = farms.filter(
      (s) => s.power_mw >= needPower && (country === "all" || s.country === country)
    );
    const scored = candidates.map((s) => {
      const score = FACTORS.reduce((acc, f) => acc + weights[f.wkey] * (s[f.skey] ?? 0), 0) / totalW;
      const pros = FACTORS.filter((f) => (s[f.skey] ?? 0) >= PRO_THRESHOLD).map((f) => f.good);
      const cons = FACTORS.filter((f) => (s[f.skey] ?? 0) <= CON_THRESHOLD).map((f) => f.bad);
      return { ...s, _score: score, _pros: pros, _cons: cons };
    });
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, TOP_N).map((s, i) => ({ ...s, _rank: i + 1 }));
  }, [farms, weights, needPower, country]);

  const setWeight = (key, val) => setWeights((w) => ({ ...w, [key]: val }));

  if (view === "landing") {
    return (
      <Landing
        mw={mw}
        setMw={setMw}
        weights={weights}
        setWeight={setWeight}
        loading={!farms}
        leaving={leaving}
        onSubmit={() => {
          setLeaving(true);
          setTimeout(() => setView("results"), 320);
        }}
      />
    );
  }

  return (
    <div className="app">
      <aside className="sidebar results-sidebar">
        <div className="brand">
          <Logo />
          <h1>EnerSite</h1>
          <span>underwater DC siting</span>
        </div>

        <button className="back-btn" onClick={() => { setLeaving(false); setView("landing"); }}>
          &larr; Edit preferences
        </button>

        <div className="stats">
          <div><b>{ranked.length}</b> offshore wind farms can power a {mw} MW underwater data center (needs &ge; {Math.round(needPower)} MW capacity, PUE {OFFSHORE_PUE}) of {farms?.length ?? 0} total.</div>
        </div>

        <div className="field">
          <label>Country</label>
          <select className="country-select" value={country} onChange={(e) => setCountry(e.target.value)}>
            <option value="all">All countries</option>
            {countries.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>

        <div className="ranked-list">
          {ranked.map((s) => (
            <SiteCard
              key={s._id}
              site={s}
              active={selected?._id === s._id}
              onClick={() => setSelected(selected?._id === s._id ? null : s)}
            />
          ))}
          {ranked.length === 0 && (
            <div className="empty-hint">No wind farms match — lower the MW or change the country filter.</div>
          )}
        </div>
      </aside>

      <div className="map-wrap">
        <MapContainer className="map" center={[54, 6]} zoom={5} preferCanvas>
          <TileLayer
            attribution='&copy; OpenStreetMap &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <FlyTo selected={selected} />
          {ranked.map((s) => (
            <CircleMarker
              key={"halo-" + s._id}
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
              key={s._id}
              center={[s.lat, s.lon]}
              radius={s._rank === 1 ? 10 : 6}
              pathOptions={{
                color: "#22d3ee",
                fillColor: scoreColor(s._score),
                fillOpacity: 0.9,
                weight: selected?._id === s._id ? 3 : 2,
              }}
              eventHandlers={{ click: () => setSelected(selected?._id === s._id ? null : s) }}
            >
              <Tooltip>
                #{s._rank} &middot; {s.name} &middot; score {s._score.toFixed(2)}
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

function Landing({ mw, setMw, weights, setWeight, loading, leaving, onSubmit }) {
  return (
    <div className={"landing" + (leaving ? " leaving" : "")}>
      <div className="landing-card">
        <div className="brand">
          <Logo />
          <h1>EnerSite</h1>
          <span>underwater DC siting</span>
        </div>
        <p className="landing-sub">
          Place an underwater data center beside Europe's offshore wind farms —
          clean power on-site, free seawater cooling. Tell us your size and what
          matters most, and we'll rank the best wind farms with their trade-offs.
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
        {FACTORS.map((f) => (
          <div className="slider-row" key={f.skey}>
            <div className="top">
              <span>{f.label}</span>
            </div>
            <div className="weight-btns">
              {WEIGHT_LABELS.map((l, i) => (
                <button
                  key={l}
                  className={"weight-btn" + (weights[f.wkey] === i ? " active" : "")}
                  onClick={() => setWeight(f.wkey, i)}
                  type="button"
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
        ))}

        <button className="submit-btn" onClick={onSubmit} disabled={loading}>
          {loading ? "Loading wind farm data..." : "Find sites →"}
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
          <div className="site-card-name">{site.name}</div>
          <div className="site-card-sub">{site.country} &middot; {site.status} &middot; {site.n_turbines || "?"} turbines</div>
        </div>
        <div className="site-card-score" style={{ color: scoreColor(site._score) }}>
          {Math.round(site._score * 100)}
        </div>
      </div>

      {active && (
        <div className="site-card-details">
          {FACTORS.map((f) => (
            <div className="metric-row" key={f.skey}>
              <div className="metric-top">
                <span className="name">{f.label}</span>
                <span className="raw">{f.fmt(site[f.raw])} {f.unit}</span>
              </div>
              <div className="bar-bg">
                <div className="bar-fg" style={{ width: `${(site[f.skey] ?? 0) * 100}%`, background: scoreColor(site[f.skey] ?? 0) }} />
              </div>
            </div>
          ))}
          <div className="metric-row">
            <div className="metric-top">
              <span className="name">Farm capacity</span>
              <span className="raw">{site.power_mw.toFixed(0)} MW &middot; {site.year || "—"}</span>
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
