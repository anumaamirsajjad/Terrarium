/**
 * Marching ants for the active intervention boundary.
 *
 * `PathStyleExtension({ dash: true })` is the obvious route and it is a dead end: it gives
 * dashes through `getDashArray` but has **no dash offset**, so there is nothing to animate.
 * Cycling `getDashArray` changes dash *length*, which reads as the border breathing rather
 * than as anything marching.
 *
 * The ring is a fixed, short polyline, so the dashes are generated as geometry and the
 * phase advanced instead. Thirty lines, no extension, and genuinely marching.
 */

import type { Position } from "./useDrawnPolygon";

/** Dash and gap in degrees. About 130 m and 100 m at this latitude — a visible crawl. */
export const DASH = 0.0012;
export const GAP = 0.0009;
export const PERIOD = DASH + GAP;

/**
 * Cut a closed ring into alternating dash segments, offset by `phase`.
 *
 * Planar arithmetic on lon/lat. Over a 20 km tile the distortion is a fraction of a dash,
 * and this decides where a dash *starts*, never where a cell is — the polygon the API
 * receives is untouched by any of it.
 */
export function antSegments(
  ring: readonly Position[],
  dash: number = DASH,
  gap: number = GAP,
  phase = 0,
): Position[][] {
  const period = dash + gap;
  const out: Position[][] = [];
  if (period <= 0 || ring.length < 2) return out;

  // How far into the current dash cycle the next edge begins. Carried across edges so the
  // pattern is continuous around a corner instead of restarting at every vertex.
  let travelled = ((phase % period) + period) % period;

  for (let i = 0; i < ring.length - 1; i++) {
    const [ax, ay] = ring[i]!;
    const [bx, by] = ring[i + 1]!;
    const length = Math.hypot(bx - ax, by - ay);
    if (length === 0) continue;

    for (let d = -travelled; d < length; d += period) {
      const start = Math.max(0, d);
      const end = Math.min(length, d + dash);
      if (end <= start) continue;
      const t0 = start / length;
      const t1 = end / length;
      out.push([
        [ax + (bx - ax) * t0, ay + (by - ay) * t0],
        [ax + (bx - ax) * t1, ay + (by - ay) * t1],
      ]);
    }
    travelled = (travelled + length) % period;
  }

  return out;
}
