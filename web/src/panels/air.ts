/**
 * Pure helpers for the air panel, kept out of the component file.
 *
 * Same split as `equity.ts` beside `EquityPanel.tsx`: a module that exports both a
 * component and a plain function breaks React Fast Refresh, and a bare function is easier
 * to test than a rendered string anyway.
 */

const COMPASS_POINTS = [
  "N", "NNE", "NE", "ENE",
  "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW",
  "W", "WNW", "NW", "NNW",
] as const;

/**
 * A wind direction in degrees as a compass point.
 *
 * **Meteorological convention: the direction the wind comes _from_.** 90° is an easterly
 * and the plume travels west. The air core depends on that sign being right and nothing
 * else in the cube would notice if it were not, so the panel spells it out rather than
 * showing a bare number the reader has to interpret.
 *
 * Folds any input into one circle, so a negative or over-wound angle cannot index off the
 * end of the table and print the plume travelling the opposite way.
 */
export function compassPoint(degrees: number): string {
  const wrapped = (((degrees % 360) + 360) % 360) / 22.5;
  return COMPASS_POINTS[Math.round(wrapped) % 16]!;
}
