import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";

// Each factor: wkey = which weight slider drives it, skey = the precomputed
// score field (1 = best), raw/unit/fmt for the explain panel, good/bad tags.
const ONSHORE_FACTORS = [
  { wkey: "s_price_eur_mwh", skey: "s_price_eur_mwh", label: "Price", raw: "price_eur_mwh", unit: "EUR/MWh", fmt: (v) => v.toFixed(0), good: "cheap power", bad: "expensive power" },
  { wkey: "s_carbon", skey: "s_carbon", label: "Carbon intensity", raw: "gco2_kwh", unit: "g/kWh", fmt: (v) => v.toFixed(0), good: "clean grid", bad: "carbon-heavy grid" },
  { wkey: "s_dist_dc_km", skey: "s_dist_dc_km", label: "Connectivity", raw: "dist_dc_km", unit: "km to nearest DC", fmt: (v) => v.toFixed(0), good: "well connected", bad: "remote / poorly connected" },
  { wkey: "s_ppa_mw_50km", skey: "s_ppa_mw_50km", label: "PPA potential", raw: "ppa_mw_50km", unit: "MW renewables / 50km", fmt: (v) => v.toFixed(0), good: "strong PPA potential", bad: "little renewables nearby" },
];

// Underwater DCs sit beside offshore wind farms: clean power on-site, free
// seawater cooling. The three offshore factors reuse three of the same sliders.
const OFFSHORE_FACTORS = [
  { wkey: "s_ppa_mw_50km", skey: "s_power", label: "Clean power capacity", raw: "power_mw", unit: "MW", fmt: (v) => v.toFixed(0), good: "large clean-power supply", bad: "limited power output" },
  { wkey: "s_dist_dc_km", skey: "s_coast", label: "Proximity to shore", raw: "dist_coast_km", unit: "km to coast", fmt: (v) => v.toFixed(0), good: "easy cable landing", bad: "far offshore" },
  { wkey: "s_carbon", skey: "s_status", label: "Operational readiness", raw: "status", unit: "", fmt: (v) => v, good: "operational / near-term", bad: "early-stage" },
];

const TOP_N = 60;

// Onshore: of a bus's total connected line rating, assume at most this share is
// realistically available to a new load (rest carries existing flows).
const AVAILABLE_SHARE = 0.2;
const PUE = 1.2; // IEA-typical for new builds: grid draw = IT load * PUE
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

export default function App() {
  const [onshore, setOnshore] = useState(null);
  const [offshore, setOffshore] = useState(null);
  const [siteType, setSiteType] = useState("onshore");
  const [view, setView] = useState("landing");
  const [mw, setMw] = useState(50);
  const [weights, setWeights] = useState({
    s_price_eur_mwh: 1,
    s_carbon: 1,
    s_dist_dc_km: 1,
    s_ppa_mw_50km: 1,
  });
  const [selected, setSelected] = useState(null);
  const [country, setCountry] = useState("all");
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    fetch("/sites.geojson")
      .then((r) => r.json())
      .then((d) =>
        setOnshore(
          d.features.map((f) => {
            const p = f.properties;
            return {
              ...p,
              _id: p.bus_id,
              s_carbon: (p.s_gco2_kwh + p.s_clean_share_pct) / 2,
              lat: f.geometry.coordinates[1],
              lon: f.geometry.coordinates[0],
            };
          })
        )
      );
    fetch("/windfarms.geojson")
      .then((r) => r.json())
      .then((d) =>
        setOffshore(
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

  const isOffshore = siteType === "underwater";
  const sites = isOffshore ? offshore : onshore;
  const factors = isOffshore ? OFFSHORE_FACTORS : ONSHORE_FACTORS;

  // Hard filter threshold + accessor (capacity that must fit the requested MW).
  const needPower = isOffshore ? mw * OFFSHORE_PUE : (mw * PUE) / AVAILABLE_SHARE;
  const capacityOf = (s) => (isOffshore ? s.power_mw : s.headroom_mva);

  const countries = useMemo(() => {
    if (!sites) return [];
    return [...new Set(sites.map((s) => s.country))].sort();
  }, [sites]);

  const ranked = useMemo(() => {
    if (!sites) return [];
    const totalW = factors.reduce((s, f) => s + weights[f.wkey], 0) || 1;
    const candidates = sites.filter(
      (s) => capacityOf(s) >= needPower && (country === "all" || s.country === country)
    );
    const scored = candidates.map((s) => {
      const score = factors.reduce((acc, f) => acc + weights[f.wkey] * (s[f.skey] ?? 0), 0) / totalW;
      const pros = factors.filter((f) => (s[f.skey] ?? 0) >= PRO_THRESHOLD).map((f) => f.good);
      const cons = factors.filter((f) => (s[f.skey] ?? 0) <= CON_THRESHOLD).map((f) => f.bad);
      return { ...s, _score: score, _pros: pros, _cons: cons };
    });
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, TOP_N).map((s, i) => ({ ...s, _rank: i + 1 }));
  }, [sites, factors, weights, needPower, country, isOffshore]);

  const setWeight = (key, val) => setWeights((w) => ({ ...w, [key]: val }));
  const changeType = (t) => {
    setSiteType(t);
    setSelected(null);
    setCountry("all");
  };

  if (view === "landing") {
    return (
      <Landing
        mw={mw}
        setMw={setMw}
        weights={weights}
        setWeight={setWeight}
        siteType={siteType}
        changeType={changeType}
        isOffshore={isOffshore}
        factors={factors}
        loading={!onshore || !offshore}
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
          <span>data-center siting</span>
        </div>

        <button className="back-btn" onClick={() => { setLeaving(false); setView("landing"); }}>
          &larr; Edit preferences
        </button>

        <TypeToggle siteType={siteType} changeType={changeType} />

        <div className="stats">
          {isOffshore ? (
            <div><b>{ranked.length}</b> offshore wind farms can power a {mw} MW underwater DC (needs &ge; {Math.round(needPower)} MW capacity, PUE {OFFSHORE_PUE}) of {sites?.length ?? 0} total.</div>
          ) : (
            <div><b>{ranked.length}</b> sites can host {mw} MW (needs &ge; {Math.round(needPower).toLocaleString()} MVA connected @ {AVAILABLE_SHARE * 100}% available, PUE {PUE}) of {sites?.length ?? 0} total.</div>
          )}
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
              factors={factors}
              isOffshore={isOffshore}
              active={selected?._id === s._id}
              onClick={() => setSelected(selected?._id === s._id ? null : s)}
            />
          ))}
          {ranked.length === 0 && (
            <div className="empty-hint">No {isOffshore ? "wind farms" : "sites"} match — lower the MW or change the country filter.</div>
          )}
        </div>
      </aside>

      <div className="map-wrap">
        <MapContainer className="map" center={[54, 6]} zoom={5} preferCanvas>
          <TileLayer
            attribution='&copy; OpenStreetMap &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
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
                color: isOffshore ? "#22d3ee" : "#0b1020",
                fillColor: scoreColor(s._score),
                fillOpacity: 0.9,
                weight: selected?._id === s._id ? 3 : isOffshore ? 2 : 1.5,
              }}
              eventHandlers={{ click: () => setSelected(selected?._id === s._id ? null : s) }}
            >
              <Tooltip>
                #{s._rank} &middot; {isOffshore ? s.name : s.country} &middot; score {s._score.toFixed(2)}
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

function TypeToggle({ siteType, changeType }) {
  return (
    <div className="type-toggle">
      <button
        className={"type-btn" + (siteType === "onshore" ? " active" : "")}
        onClick={() => changeType("onshore")}
        type="button"
      >
        ⚡ Onshore grid
      </button>
      <button
        className={"type-btn" + (siteType === "underwater" ? " active" : "")}
        onClick={() => changeType("underwater")}
        type="button"
      >
        🌊 Underwater
      </button>
    </div>
  );
}

function Landing({ mw, setMw, weights, setWeight, siteType, changeType, isOffshore, factors, loading, leaving, onSubmit }) {
  return (
    <div className={"landing" + (leaving ? " leaving" : "")}>
      <div className="landing-card">
        <div className="brand">
          <Logo />
          <h1>EnerSite</h1>
          <span>data-center siting</span>
        </div>
        <p className="landing-sub">
          {isOffshore
            ? "Place an underwater data center beside Europe's offshore wind farms — clean power on-site, free seawater cooling. We'll rank the best wind farms for your size."
            : "Tell us how big your data center is and what matters most to you — we'll rank candidate grid sites across Europe and explain the trade-offs of each."}
        </p>

        <div className="field">
          <label>Site type</label>
          <TypeToggle siteType={siteType} changeType={changeType} />
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

        <div className="section-title">What matters most to you?</div>
        {factors.map((f) => (
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
          {loading ? "Loading site data..." : "Find sites →"}
        </button>
      </div>
    </div>
  );
}

function SiteCard({ site, factors, isOffshore, active, onClick }) {
  return (
    <div className={"site-card" + (active ? " active" : "")} onClick={onClick}>
      <div className="site-card-head">
        <div className="rank-badge">#{site._rank}</div>
        <div className="site-card-title">
          {isOffshore ? (
            <>
              <div className="site-card-name">{site.name}</div>
              <div className="site-card-sub">{site.country} &middot; {site.status} &middot; {site.n_turbines || "?"} turbines</div>
            </>
          ) : (
            <>
              <div className="site-card-name">{site.country} &middot; {site.voltage} kV</div>
              <div className="site-card-sub">{site.nearest_dc ? `near ${site.nearest_dc}` : site.bus_id}</div>
            </>
          )}
        </div>
        <div className="site-card-score" style={{ color: scoreColor(site._score) }}>
          {Math.round(site._score * 100)}
        </div>
      </div>

      {active && (
        <div className="site-card-details">
          {factors.map((f) => (
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
              {isOffshore ? (
                <>
                  <span className="name">Farm capacity</span>
                  <span className="raw">{site.power_mw.toFixed(0)} MW &middot; {site.year || "—"}</span>
                </>
              ) : (
                <>
                  <span className="name">Grid headroom</span>
                  <span className="raw">{site.headroom_mva.toFixed(0)} MVA</span>
                </>
              )}
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
