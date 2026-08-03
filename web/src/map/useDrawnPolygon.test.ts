import { describe, expect, it } from "vitest";

import { MIN_VERTICES, type Position, toGeoJson } from "./useDrawnPolygon";

const SQUARE: Position[] = [
  [74.34, 31.49],
  [74.38, 31.49],
  [74.38, 31.52],
  [74.34, 31.52],
];

describe("toGeoJson", () => {
  it("closes the ring by repeating the first position", () => {
    // Shapely closes rings implicitly, so an unclosed ring would not error — it would
    // just rasterise a slightly different polygon than the one drawn on screen.
    const polygon = toGeoJson(SQUARE)!;
    const ring = polygon.coordinates[0]!;

    expect(ring).toHaveLength(SQUARE.length + 1);
    expect(ring[ring.length - 1]).toEqual(ring[0]);
  });

  it("emits lon/lat order, which is what GeoJSON and the API expect", () => {
    const ring = toGeoJson(SQUARE)!.coordinates[0]!;

    // Longitude first. Swapped, the polygon lands off the coast of Somalia and the API
    // rejects it with "selects no grid cells" — a confusing way to learn about axis order.
    expect(ring[0]![0]).toBeCloseTo(74.34);
    expect(ring[0]![1]).toBeCloseTo(31.49);
  });

  it("refuses anything without enough corners to enclose an area", () => {
    expect(toGeoJson([])).toBeNull();
    expect(toGeoJson(SQUARE.slice(0, MIN_VERTICES - 1))).toBeNull();
  });

  it("accepts exactly the minimum number of corners", () => {
    expect(toGeoJson(SQUARE.slice(0, MIN_VERTICES))).not.toBeNull();
  });
});
