/**
 * The ants are decoration, but two of their properties are not.
 *
 * A dash that leaves the ring puts a line across the map where the user drew nothing, and
 * a pattern that restarts at every vertex reads as four separate borders rather than one.
 * Both are invisible in a screenshot and obvious in motion, which is exactly the kind of
 * thing worth asserting instead of watching for.
 */

import { describe, expect, it } from "vitest";

import { antSegments, DASH, GAP, PERIOD } from "./ants";
import type { Position } from "./useDrawnPolygon";

/** A unit square, closed. */
const RING: Position[] = [
  [0, 0],
  [0.01, 0],
  [0.01, 0.01],
  [0, 0.01],
  [0, 0],
];

/** Is `point` on the segment a→b, within floating-point slop? */
function onEdge(point: Position, a: Position, b: Position): boolean {
  const cross =
    (point[0] - a[0]) * (b[1] - a[1]) - (point[1] - a[1]) * (b[0] - a[0]);
  if (Math.abs(cross) > 1e-12) return false;
  const dot = (point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1]);
  const len2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2;
  return dot >= -1e-12 && dot <= len2 + 1e-12;
}

describe("antSegments", () => {
  it("keeps every dash on the ring", () => {
    for (const [start, end] of antSegments(RING, DASH, GAP, 0.0007)) {
      const fits = [0, 1, 2, 3].some((i) => {
        const a = RING[i]!;
        const b = RING[i + 1]!;
        return onEdge(start!, a, b) && onEdge(end!, a, b);
      });
      expect(fits).toBe(true);
    }
  });

  it("never draws a dash longer than one dash", () => {
    for (const [start, end] of antSegments(RING, DASH, GAP, 0.0004)) {
      expect(Math.hypot(end![0] - start![0], end![1] - start![1])).toBeLessThanOrEqual(
        DASH + 1e-12,
      );
    }
  });

  it("carries the phase across corners rather than restarting at each vertex", () => {
    // The invariant is *duty cycle*, not where individual dashes fall: whatever the phase,
    // the inked fraction of the ring is dash/period. Restarting the cycle at every vertex
    // would begin a fresh full dash at each corner and ink more than that.
    //
    // A dash straddling a corner is clipped and re-emitted on the next edge, so it counts
    // as two segments starting at the vertex — which is the continuity, not a defect.
    const perimeter = 0.04;
    for (const phase of [0, PERIOD * 0.25, PERIOD * 0.6, PERIOD * 0.9]) {
      const inked = antSegments(RING, DASH, GAP, phase).reduce(
        (total, [a, b]) => total + Math.hypot(b![0] - a![0], b![1] - a![1]),
        0,
      );
      // Within one dash: the ring's own start and end clip against the pattern.
      expect(Math.abs(inked - (perimeter * DASH) / PERIOD)).toBeLessThan(DASH);
    }
  });

  it("advances with the phase instead of resizing the dashes", () => {
    // Not the first segment: it is clipped to the ring's start at every phase, so it is
    // the one dash that cannot move.
    const a = antSegments(RING, DASH, GAP, 0);
    const b = antSegments(RING, DASH, GAP, PERIOD * 0.4);
    expect(a[1]![0]).not.toEqual(b[1]![0]);
    // The count may differ by one — a dash at either end of the ring can be clipped in or
    // out by the phase — but it must not change by more than that, which is what resizing
    // the dashes rather than sliding them would do.
    expect(Math.abs(a.length - b.length)).toBeLessThanOrEqual(1);
  });

  it("returns nothing for a degenerate ring", () => {
    expect(antSegments([[0, 0]])).toEqual([]);
  });
});
