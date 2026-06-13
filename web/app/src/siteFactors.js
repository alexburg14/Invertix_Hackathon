export const FACTORS = [
  {
    skey: "s_capacity",
    label: "Wind-farm capacity",
    rawKeys: ["power_mw", "wf_power_mw"],
    unit: "MW",
    format: "integer",
    good: "large clean-power supply",
    bad: "small wind farm",
  },
  {
    skey: "s_bottemp",
    label: "Bottom-water temp",
    rawKeys: ["bot_temp_c"],
    unit: "deg C",
    format: "oneDecimal",
    good: "cold seabed, free cooling",
    bad: "warm seabed, cooling load",
  },
  {
    skey: "s_windpark",
    label: "Distance to wind farm",
    rawKeys: ["dist_windpark_km"],
    unit: "km",
    format: "integer",
    good: "clustered, redundant power",
    bad: "isolated farm",
  },
  {
    skey: "s_shore",
    label: "Distance to shore",
    rawKeys: ["dist_shore_km", "dist_coast_km"],
    unit: "km",
    format: "integer",
    good: "close to cable landing",
    bad: "far from shore",
  },
  {
    skey: "s_depth",
    label: "Depth",
    rawKeys: ["depth_m"],
    unit: "m deep",
    format: "integer",
    good: "ideal deployment depth",
    bad: "too shallow / too deep",
  },
  {
    skey: "s_negprice",
    label: "Negative-price hours",
    rawKeys: ["negprice_pct"],
    unit: "% hrs",
    format: "oneDecimal",
    good: "frequent free surplus power",
    bad: "rarely negative prices",
  },
  {
    skey: "s_wind",
    label: "Wind speed",
    rawKeys: ["wind_ms"],
    unit: "m/s",
    format: "oneDecimal",
    good: "strong, steady wind",
    bad: "weak wind resource",
  },
  {
    skey: "s_current",
    label: "Sea current",
    rawKeys: ["current_ms"],
    unit: "m/s",
    format: "twoDecimal",
    good: "ideal flow for cooling",
    bad: "too calm or too strong",
  },
  {
    skey: "s_slope",
    label: "Seabed slope",
    rawKeys: ["slope_deg"],
    unit: "deg",
    format: "oneDecimal",
    good: "flat, stable seabed",
    bad: "steep seabed",
  },
  {
    skey: "s_mpa",
    label: "Protected area",
    rawKeys: ["mpa_status"],
    unit: "",
    format: "text",
    good: "outside protected area",
    bad: "inside protected area",
  },
];

export const DEFAULT_FACTOR_WEIGHT = 2;
export const PRO_THRESHOLD = 0.7;
export const CON_THRESHOLD = 0.3;

export function createDefaultWeights() {
  return Object.fromEntries(FACTORS.map((factor) => [factor.skey, DEFAULT_FACTOR_WEIGHT]));
}

export function weightedSuitabilityScore(site, weights) {
  const totalWeight = FACTORS.reduce((sum, factor) => sum + (weights[factor.skey] ?? 0), 0) || 1;
  const weightedScore = FACTORS.reduce(
    (sum, factor) => sum + (weights[factor.skey] ?? 0) * (site[factor.skey] ?? 0),
    0
  );
  return weightedScore / totalWeight;
}

export function factorPros(site) {
  return FACTORS.filter((factor) => (site[factor.skey] ?? 0) >= PRO_THRESHOLD).map(
    (factor) => factor.good
  );
}

export function factorCons(site) {
  return FACTORS.filter((factor) => (site[factor.skey] ?? 0) <= CON_THRESHOLD).map(
    (factor) => factor.bad
  );
}

export function rawFactorValue(site, factor) {
  for (const key of factor.rawKeys) {
    if (site[key] !== undefined && site[key] !== null) return site[key];
  }
  return undefined;
}

export function formatFactorValue(value, format) {
  if (value === undefined || value === null) return "?";
  if (typeof value !== "number") return value;
  if (format === "integer") return value.toFixed(0);
  if (format === "oneDecimal") return value.toFixed(1);
  if (format === "twoDecimal") return value.toFixed(2);
  return String(value);
}
