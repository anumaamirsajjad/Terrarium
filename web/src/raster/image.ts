/**
 * Raster + ramp → RGBA image for deck.gl's BitmapLayer.
 *
 * Colouring happens here, in JS, rather than in a shader. At 40,602 cells the cost is
 * negligible and the payoff is control: NaN becomes genuinely transparent, exact zeros
 * in a delta field stay invisible, and the compare split is an array operation rather
 * than a GPU trick.
 *
 * Row order is the one thing to get right. The cube's row 0 is the NORTH edge (y
 * descends, raster convention) and BitmapLayer with `bounds = [w, s, e, n]` draws its
 * image row 0 at the top, i.e. north. So the array maps straight through — no flip. A
 * flip here would mirror the city about its own centre line, which is subtle enough to
 * ship unnoticed and wrong enough to invalidate every claim on the map.
 */

import type { Raster } from "./decode";
import type { Ramp } from "./ramp";

/** Deltas smaller than this render as nothing. Well under any meaningful ΔLST. */
const ZERO_EPSILON = 1e-6;

export interface ColourOptions {
  ramp: Ramp;
  /** Value range the ramp spans. Values outside are clamped, not discarded. */
  domain: [number, number];
  /**
   * Treat near-zero as fully transparent. For a delta field this is what keeps the
   * ~39,000 provably-unchanged cells from taking a colour.
   */
  transparentAtZero?: boolean;
  /** Global multiplier on alpha, 0–1. Drives the layer opacity slider. */
  opacity?: number;
}

/**
 * RGBA bytes, row-major, ready for `new ImageData(...)`.
 *
 * Pinned to a plain `ArrayBuffer` rather than `ArrayBufferLike`: `ImageData` will not
 * accept a view that might be backed by a `SharedArrayBuffer`.
 */
export type RgbaBytes = Uint8ClampedArray<ArrayBuffer>;

export function colourise(raster: Raster, options: ColourOptions): RgbaBytes {
  const { ramp, domain, transparentAtZero = false, opacity = 1 } = options;
  const [low, high] = domain;
  const span = high - low || 1;

  const { values } = raster;
  const rgba: RgbaBytes = new Uint8ClampedArray(new ArrayBuffer(values.length * 4));

  for (let i = 0; i < values.length; i += 1) {
    const value = values[i]!;

    // NaN is no-data and must stay invisible. It is not zero: zero is a real
    // temperature and a real ΔLST, so collapsing them would paint gaps as "no change".
    if (!Number.isFinite(value)) continue;
    if (transparentAtZero && Math.abs(value) < ZERO_EPSILON) continue;

    const [r, g, b, a] = ramp.at((value - low) / span);
    const offset = i * 4;
    rgba[offset] = r;
    rgba[offset + 1] = g;
    rgba[offset + 2] = b;
    rgba[offset + 3] = a * opacity;
  }

  return rgba;
}

/**
 * Split two rasters at a column, for before/after comparison.
 *
 * The divider is a **column index**, which means a fixed line of longitude on the
 * ground rather than a position on screen. That is the honest choice for a single fixed
 * tile: the boundary stays put when the user pans or zooms, so the two halves always
 * describe the same places, and the comparison cannot drift under the cursor.
 *
 * Columns strictly left of `column` come from `left`; the rest from `right`.
 */
export function splitRasters(left: Raster, right: Raster, column: number): Raster {
  if (left.width !== right.width || left.height !== right.height) {
    throw new Error(
      `cannot split rasters of different shapes: ` +
        `${left.height}x${left.width} vs ${right.height}x${right.width}`,
    );
  }

  const { width, height } = left;
  const cut = Math.min(width, Math.max(0, Math.round(column)));
  const values = new Float32Array(width * height);

  for (let row = 0; row < height; row += 1) {
    const start = row * width;
    // Two contiguous copies per row rather than a per-pixel branch.
    values.set(left.values.subarray(start, start + cut), start);
    values.set(right.values.subarray(start + cut, start + width), start + cut);
  }

  return { values, height, width };
}

/** Element-wise sum. Used to turn a baseline plus a delta into the scenario field. */
export function addRasters(base: Raster, delta: Raster): Raster {
  if (base.width !== delta.width || base.height !== delta.height) {
    throw new Error("cannot add rasters of different shapes");
  }

  const values = new Float32Array(base.values.length);
  for (let i = 0; i < values.length; i += 1) {
    const d = delta.values[i]!;
    // A NaN in the delta means the core had no usable inputs there. Carrying the
    // baseline through unchanged would claim knowledge the model did not have.
    values[i] = base.values[i]! + (Number.isFinite(d) ? d : Number.NaN);
  }
  return { values, height: base.height, width: base.width };
}

/** Min and max over finite values only. Returns null when everything is NaN. */
export function finiteExtent(raster: Raster): [number, number] | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  let seen = false;

  for (const value of raster.values) {
    if (!Number.isFinite(value)) continue;
    seen = true;
    if (value < min) min = value;
    if (value > max) max = value;
  }

  return seen ? [min, max] : null;
}

/**
 * Longitude of the divider when the split is cut at `column`.
 *
 * This is a cell *edge*, not a cell centre: the cut at column N sits on the boundary
 * between columns N-1 and N. Mixing the two conventions puts the drawn divider half a
 * cell away from where the data is actually split, which at 100 m is invisible on screen
 * and still wrong.
 */
export function columnToLongitude(
  column: number,
  width: number,
  bounds: [number, number, number, number],
): number {
  const [west, , east] = bounds;
  return west + (column / width) * (east - west);
}

/** Inverse of `columnToLongitude`, clamped into the raster. */
export function longitudeToColumn(
  longitude: number,
  width: number,
  bounds: [number, number, number, number],
): number {
  const [west, , east] = bounds;
  const fraction = (longitude - west) / (east - west);
  return Math.min(width, Math.max(0, Math.round(fraction * width)));
}
