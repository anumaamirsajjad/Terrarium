import { describe, expect, it } from "vitest";

import type { GridInfo, LayerResponse } from "../api/client";
import { EXPECTED_ENCODING, decodeLayer } from "./decode";

const GRID: GridInfo = {
  crs: "EPSG:32643",
  resolution_m: 100,
  shape: [2, 3],
  bounds: [0, 0, 300, 200],
  bounds_wgs84: [74.25, 31.43, 74.46, 31.61],
};

function encode(values: number[]): string {
  const bytes = new Uint8Array(new Float32Array(values).buffer);
  return btoa(String.fromCharCode(...bytes));
}

function layer(values: number[], overrides: Partial<LayerResponse> = {}): LayerResponse {
  return {
    variable: "lst_c",
    description: "test",
    units: "degC",
    window: "2024-summer",
    grid: GRID,
    encoding: EXPECTED_ENCODING,
    data: encode(values),
    vmin: null,
    vmax: null,
    valid_fraction: 1,
    ...overrides,
  };
}

describe("decodeLayer", () => {
  it("round-trips values in row-major order", () => {
    const raster = decodeLayer(layer([1, 2, 3, 4, 5, 6]));

    expect(raster.height).toBe(2);
    expect(raster.width).toBe(3);
    expect(Array.from(raster.values)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("preserves NaN rather than turning it into zero", () => {
    // Zero is a real temperature and a real ΔLST. Collapsing no-data onto it would
    // render missing pixels as "no change", which is a different claim entirely.
    const raster = decodeLayer(layer([NaN, 1, NaN, 2, 3, 4]));

    expect(Number.isNaN(raster.values[0])).toBe(true);
    expect(Number.isNaN(raster.values[2])).toBe(true);
    expect(raster.values[1]).toBe(1);
  });

  it("keeps negative values, which a delta field is full of", () => {
    const raster = decodeLayer(layer([-0.5, -1.25, 0, 0.25, -2, 3]));

    expect(raster.values[0]).toBeCloseTo(-0.5);
    expect(raster.values[4]).toBeCloseTo(-2);
  });

  it("rejects an encoding it does not understand", () => {
    // The contract is named in the payload precisely so this can be checked. A silently
    // misread float array renders as a plausible map with the wrong colours.
    expect(() => decodeLayer(layer([1, 2, 3, 4, 5, 6], { encoding: "base64:float64" }))).toThrow(
      /only reads/,
    );
  });

  it("rejects a payload whose length disagrees with the declared shape", () => {
    expect(() => decodeLayer(layer([1, 2, 3]))).toThrow(/needs 24/);
  });
});
