/**
 * Display helpers for cube units.
 *
 * The cube follows the CF convention where a dimensionless quantity has units `"1"`.
 * That is correct in the metadata and nonsense on screen: NDVI's legend read
 * "-0.45 1 … 0.78 1", which looks like a rendering bug rather than a unit. Suppress it
 * at the display boundary rather than lying about it in `state/cube.py`.
 */

/** CF-convention marker for a dimensionless quantity. */
const DIMENSIONLESS = "1";

/** The unit as it should appear beside a number — empty when there is nothing to say. */
export function formatUnits(units: string): string {
  return units === DIMENSIONLESS ? "" : units;
}

/** "lst_c (degC)", but "ndvi" rather than "ndvi (1)". */
export function labelWithUnits(name: string, units: string): string {
  const suffix = formatUnits(units);
  return suffix ? `${name} (${suffix})` : name;
}

/** Join a value and its unit, without a trailing space when dimensionless. */
export function withUnits(value: string, units: string): string {
  const suffix = formatUnits(units);
  return suffix ? `${value} ${suffix}` : value;
}

/**
 * Plain-English label for a cube variable, for the base-layer picker.
 *
 * The cube names things by their CF/remote-sensing convention (`lst_c`, `ndbi`) because
 * that is what a physics core and a scientist need. A dashboard reader needs the opposite:
 * `lst_c` keeps "ground" rather than "surface" and "mid-morning" rather than nothing,
 * which is the same plain-language trade `dsl/explain.py` makes server-side (D9) - this is
 * that same call, made for a five-word menu label instead of a sentence.
 *
 * `pm25_emission_g_s` deliberately does not say "air quality": it is an emission rate,
 * not a concentration, so calling it pollution *levels* would overclaim what a monitor
 * would read (the same distinction `AirPanel` draws for the delta this field feeds).
 */
const PLAIN_VARIABLE_LABEL: Record<string, string> = {
  lst_c: "Ground temperature",
  ndvi: "Greenery",
  ndbi: "Buildings & pavement",
  albedo: "Sunlight reflected",
  elevation_m: "Ground height",
  population: "People living here",
  pm25_emission_g_s: "Traffic pollution source",
};

/** One plain sentence for the caption under the picker. Falls back to nothing rather
 * than the server's `description`, which is written for someone checking provenance
 * (band math, DEM source) and not for the dashboard. */
const PLAIN_VARIABLE_BLURB: Record<string, string> = {
  lst_c:
    "How hot the ground surface is around 10:30 in the morning - not the air " +
    "temperature you would feel.",
  ndvi: "How much healthy plant cover is on the ground, from satellite imagery.",
  ndbi: "How much of the ground is built over with concrete, roads and buildings.",
  albedo: "How much sunlight the ground bounces back rather than absorbs.",
  elevation_m: "Height of the ground, including rooftops and tree canopy.",
  population: "Roughly how many people live in each small area.",
  pm25_emission_g_s: "Where this area's own traffic gives off fine particle pollution.",
};

export function plainVariableLabel(name: string): string {
  return PLAIN_VARIABLE_LABEL[name] ?? name;
}

export function plainVariableBlurb(name: string): string {
  return PLAIN_VARIABLE_BLURB[name] ?? "";
}
