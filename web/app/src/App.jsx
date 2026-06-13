import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";
import {
  FACTORS,
  createDefaultWeights,
  factorCons,
  factorPros,
  formatFactorValue,
  rawFactorValue,
  weightedSuitabilityScore,
} from "./siteFactors";

const TOP_N = 60;
const OFFSHORE_PUE = 1.1;
const WEIGHT_LABELS = ["Off", "Low", "Med", "High"];

function Logo({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M17 5 L8 18 H15 L14 27 L24 13 H17 L17 5 Z" fill="white" />
    </svg>
  );
}

function scoreColor(s) {
  const hue = 14 + s * 120;
  return `hsl(${hue}, 75%, 50%)`;
}

function FlyTo({ center, zoom }) {
  const map = useMap();
  useEffect(() => {
    if (center) map.flyTo(center, zoom ?? 9, { duration: 0.8 });
  }, [center, zoom, map]);
  return null;
}

export default function App() {
  const [farms, setFarms] = useState(null);
  const [view, setView] = useState("landing");
  const [mw, setMw] = useState(50);
  const [weights, setWeights] = useState(createDefaultWeights);
  const [selected, setSelected] = useState(null);
  const [country, setCountry] = useState("all");
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    fetch("/windfarms.geojson")
      .then((r) => r.json())
      .then((d) =>
        setFarms(
          d.features.map((f, i) => ({
            ...f.properties,
            _idx: i,
            lat: f.geometry.coordinates[1],
            lon: f.geometry.coordinates[0],
          }))
        )
      );
  }, []);

  const needPower = mw * OFFSHORE_PUE;

  const countries = useMemo(() => {
    if (!farms) return [];
    return [...new Set(farms.map((s) => s.country))].sort();
  }, [farms]);

  const ranked = useMemo(() => {
    if (!farms) return [];
    const results = [];
    for (const farm of farms) {
      if (farm.power_mw < needPower) continue;
      if (country !== "all" && farm.country !== country) continue;
      const score = weightedSuitabilityScore(farm, weights);
      results.push({
        ...farm,
        _score: score,
        _pros: factorPros(farm),
        _cons: factorCons(farm),
      });
    }
    results.sort((a, b) => b._score - a._score);
    return results.slice(0, TOP_N).map((p, i) => ({ ...p, _rank: i + 1 }));
  }, [farms, weights, needPower, country]);

  const flyTarget = useMemo(() => {
    if (!selected) return null;
    return [selected.lat, selected.lon];
  }, [selected]);

  const setWeight = (key, val) => setWeights((w) => ({ ...w, [key]: val }));
  const loading = !farms;

  if (view === "landing") {
    return (
      <Landing
        mw={mw}
        setMw={setMw}
        weights={weights}
        setWeight={setWeight}
        loading={loading}
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
          <div><b>{ranked.length}</b> offshore wind farms can host a {mw} MW underwater data center (needs &ge; {Math.round(needPower)} MW farm, PUE {OFFSHORE_PUE}).</div>
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
              key={s._idx}
              site={s}
              active={selected?._idx === s._idx}
              onClick={() => setSelected(selected?._idx === s._idx ? null : s)}
            />
          ))}
          {ranked.length === 0 && (
            <div className="empty-hint">No farms match — lower the MW requirement or change the country filter.</div>
          )}
        </div>
      </aside>

      <div className="map-wrap">
        <MapContainer className="map" center={[54, 6]} zoom={5} preferCanvas>
          <TileLayer
            attribution='&copy; OpenStreetMap &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <FlyTo center={flyTarget} />
          {ranked.map((s) => (
            <CircleMarker
              key={s._idx}
              center={[s.lat, s.lon]}
              radius={s._rank === 1 ? 9 : 5}
              pathOptions={{
                color: "#22d3ee",
                fillColor: scoreColor(s._score),
                fillOpacity: 0.9,
                weight: selected?._idx === s._idx ? 3 : 1.5,
              }}
              eventHandlers={{ click: () => setSelected(selected?._idx === s._idx ? null : s) }}
            >
              <Tooltip>
                #{s._rank} &middot; {s.name} &middot; score {s._score.toFixed(2)}
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>

        <div className="legend">
          <div className="legend-title">Wind farm suitability</div>
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
          Find the best offshore wind farm to co-locate an underwater data center —
          clean power on-site, free seawater cooling. Tell us your size and what
          matters most; we'll rank every farm in Europe.
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
                  className={"weight-btn" + (weights[f.skey] === i ? " active" : "")}
                  onClick={() => setWeight(f.skey, i)}
                  type="button"
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
        ))}

        <button className="submit-btn" onClick={onSubmit} disabled={loading}>
          {loading ? "Loading wind farm data..." : "Find best sites →"}
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
          <div className="site-card-sub">{site.country} &middot; {site.power_mw?.toFixed(0)} MW &middot; {site.status}</div>
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
                <span className="raw">
                  {formatFactorValue(rawFactorValue(site, f), f.format)} {f.unit}
                </span>
              </div>
              <div className="bar-bg">
                <div className="bar-fg" style={{ width: `${(site[f.skey] ?? 0) * 100}%`, background: scoreColor(site[f.skey] ?? 0) }} />
              </div>
            </div>
          ))}
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
