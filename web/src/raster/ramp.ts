/**
 * Colour ramps for the raster overlays.
 *
 * Two kinds, and the distinction is not cosmetic:
 *
 * - **Sequential** for a measured quantity (LST, NDVI, elevation). Low to high, one hue
 *   family, domain taken from the data.
 * - **Diverging** for a *change* (ΔLST). Symmetric about zero, because the sign is the
 *   whole message. Cooling and warming must be equally legible and equally scaled — an
 *   asymmetric domain would make −0.5 °C look like a different magnitude than +0.5 °C.
 *
 * The diverging ramp's most important property is that **zero is transparent**. After a
 * simulation ~39,000 of the tile's 40,602 cells hold *exactly* 0.000: outside the 500 m
 * feature neighbourhood the two predictions are bit-identical, so the delta is provably
 * zero rather than merely small. Giving those cells a colour would paint the whole tile
 * as "changed" and bury the intervention in it.
 */

/** RGBA, each channel 0–255. */
export type Rgba = readonly [number, number, number, number];

export interface Ramp {
  /** Sample the ramp at a normalised position. `t` is clamped to 0–1 by callers. */
  at(t: number): Rgba;
  stops: readonly Rgba[];
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Piecewise-linear interpolation across evenly spaced stops. */
function rampFrom(stops: readonly Rgba[]): Ramp {
  return {
    stops,
    at(t: number): Rgba {
      const clamped = Math.min(1, Math.max(0, t));
      const scaled = clamped * (stops.length - 1);
      const lower = Math.floor(scaled);
      const upper = Math.min(stops.length - 1, lower + 1);
      const frac = scaled - lower;
      const a = stops[lower]!;
      const b = stops[upper]!;
      return [
        Math.round(lerp(a[0], b[0], frac)),
        Math.round(lerp(a[1], b[1], frac)),
        Math.round(lerp(a[2], b[2], frac)),
        Math.round(lerp(a[3], b[3], frac)),
      ];
    },
  };
}

/** Heat: dark blue → cyan → yellow → red. For land surface temperature. */
export const HEAT = rampFrom([
  [12, 44, 92, 255],
  [30, 120, 170, 255],
  [110, 190, 175, 255],
  [240, 210, 110, 255],
  [214, 110, 50, 255],
  [150, 26, 30, 255],
]);

/** Vegetation: brown → pale → green. For NDVI. */
export const VEGETATION = rampFrom([
  [120, 88, 52, 255],
  [206, 190, 150, 255],
  [140, 180, 100, 255],
  [26, 110, 52, 255],
]);

/** Neutral greyscale. For elevation, population, and anything without a natural hue. */
export const NEUTRAL = rampFrom([
  [28, 32, 38, 255],
  [130, 138, 146, 255],
  [244, 246, 248, 255],
]);

/**
 * Cool-to-warm diverging, for ΔLST. Blue = cooling, red = warming, transparent at zero.
 *
 * Note the alpha ramp: fully opaque at the extremes, fully transparent in the middle.
 * That is what lets the basemap show through everywhere nothing changed, so the eye goes
 * straight to the intervention instead of to a wash of pale colour over the whole city.
 */
export const DIVERGING = rampFrom([
  [21, 76, 160, 255],
  [70, 140, 210, 220],
  [150, 200, 235, 90],
  [255, 255, 255, 0],
  [245, 190, 160, 90],
  [214, 100, 70, 220],
  [150, 26, 30, 255],
]);

export const RAMPS = { HEAT, VEGETATION, NEUTRAL, DIVERGING } as const;

/** Which ramp suits a cube variable. */
export function rampForVariable(name: string): Ramp {
  if (name === "lst_c") return HEAT;
  if (name === "ndvi") return VEGETATION;
  return NEUTRAL;
}

/**
 * A symmetric domain for a diverging ramp: `[-m, +m]` where m is the larger magnitude.
 *
 * Taking the domain from the data's own min and max would centre the ramp on the middle
 * of the observed range, not on zero — so a field that only cools would render its
 * *weakest* cooling in the "no change" colour and imply warming that does not exist.
 */
export function symmetricDomain(min: number, max: number): [number, number] {
  const magnitude = Math.max(Math.abs(min), Math.abs(max));
  // Guard the all-zero case: a zero-width domain would divide by zero when normalising.
  const safe = magnitude > 0 ? magnitude : 1;
  return [-safe, safe];
}
