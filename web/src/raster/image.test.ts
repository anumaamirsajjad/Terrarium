import { describe, expect, it } from "vitest";

import type { Raster } from "./decode";
import {
  addRasters,
  colourise,
  columnToLongitude,
  finiteExtent,
  longitudeToColumn,
  splitRasters,
} from "./image";
import { DIVERGING, HEAT, symmetricDomain } from "./ramp";

function raster(values: number[], width: number): Raster {
  return { values: new Float32Array(values), width, height: values.length / width };
}

function alphaAt(rgba: Uint8ClampedArray, index: number): number {
  return rgba[index * 4 + 3]!;
}

describe("colourise", () => {
  it("renders no-data fully transparent", () => {
    const rgba = colourise(raster([NaN, 10], 2), { ramp: HEAT, domain: [0, 20] });

    expect(alphaAt(rgba, 0)).toBe(0);
    expect(alphaAt(rgba, 1)).toBeGreaterThan(0);
  });

  it("keeps exact zeros invisible in a delta field", () => {
    // The load-bearing one. After a simulation ~39,000 of 40,602 cells are *exactly*
    // 0.000 — outside the feature neighbourhood both predictions are bit-identical. If
    // those took a colour the map would imply change across the whole city.
    const rgba = colourise(raster([0, -0.5, 0, 0.4], 2), {
      ramp: DIVERGING,
      domain: [-1, 1],
      transparentAtZero: true,
    });

    expect(alphaAt(rgba, 0)).toBe(0);
    expect(alphaAt(rgba, 2)).toBe(0);
    expect(alphaAt(rgba, 1)).toBeGreaterThan(0);
    expect(alphaAt(rgba, 3)).toBeGreaterThan(0);
  });

  it("does not hide a real zero in a temperature field", () => {
    // 0 °C is a measurement, not an absence. Only a *delta* field opts into hiding it.
    const rgba = colourise(raster([0, 10], 2), { ramp: HEAT, domain: [-10, 10] });

    expect(alphaAt(rgba, 0)).toBeGreaterThan(0);
  });

  it("gives cooling and warming opposite ends of the diverging ramp", () => {
    const rgba = colourise(raster([-1, 1], 2), {
      ramp: DIVERGING,
      domain: [-1, 1],
      transparentAtZero: true,
    });

    const cool = [rgba[0]!, rgba[1]!, rgba[2]!];
    const warm = [rgba[4]!, rgba[5]!, rgba[6]!];

    expect(cool[2]).toBeGreaterThan(cool[0]); // cooling reads blue
    expect(warm[0]).toBeGreaterThan(warm[2]); // warming reads red
  });

  it("clamps out-of-domain values instead of wrapping them", () => {
    const inside = colourise(raster([1], 1), { ramp: HEAT, domain: [0, 1] });
    const beyond = colourise(raster([99], 1), { ramp: HEAT, domain: [0, 1] });

    expect(Array.from(beyond)).toEqual(Array.from(inside));
  });

  it("scales alpha by the opacity multiplier", () => {
    const full = colourise(raster([1], 1), { ramp: HEAT, domain: [0, 1], opacity: 1 });
    const half = colourise(raster([1], 1), { ramp: HEAT, domain: [0, 1], opacity: 0.5 });

    // Within a byte of half: Uint8ClampedArray rounds, so 127.5 lands on 128.
    expect(Math.abs(alphaAt(half, 0) - alphaAt(full, 0) / 2)).toBeLessThanOrEqual(1);
  });
});

describe("symmetricDomain", () => {
  it("centres on zero even when the field only cools", () => {
    // A domain taken from the data would put the weakest cooling at the ramp's midpoint
    // — the "no change" colour — and imply warming that is not in the field at all.
    expect(symmetricDomain(-1.2, -0.1)).toEqual([-1.2, 1.2]);
  });

  it("uses the larger magnitude so both signs share a scale", () => {
    expect(symmetricDomain(-0.3, 2)).toEqual([-2, 2]);
  });

  it("survives an all-zero field without dividing by zero", () => {
    expect(symmetricDomain(0, 0)).toEqual([-1, 1]);
  });
});

describe("splitRasters", () => {
  const left = raster([1, 1, 1, 1, 1, 1], 3);
  const right = raster([2, 2, 2, 2, 2, 2], 3);

  it("takes columns left of the cut from the first raster", () => {
    const split = splitRasters(left, right, 1);

    expect(Array.from(split.values)).toEqual([1, 2, 2, 1, 2, 2]);
  });

  it("degenerates cleanly at both ends", () => {
    expect(Array.from(splitRasters(left, right, 0).values)).toEqual([2, 2, 2, 2, 2, 2]);
    expect(Array.from(splitRasters(left, right, 3).values)).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it("clamps a cut beyond the raster rather than overflowing", () => {
    expect(Array.from(splitRasters(left, right, 99).values)).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it("refuses mismatched shapes", () => {
    expect(() => splitRasters(left, raster([1, 2], 2), 1)).toThrow(/different shapes/);
  });
});

describe("addRasters", () => {
  it("adds a delta onto a baseline", () => {
    const sum = addRasters(raster([10, 20], 2), raster([-1, 2], 2));

    expect(Array.from(sum.values)).toEqual([9, 22]);
  });

  it("propagates a NaN delta rather than passing the baseline through", () => {
    // A NaN delta means the core had no usable inputs there. Showing the baseline would
    // claim the scenario is known at a pixel where it is not.
    const sum = addRasters(raster([10, 20], 2), raster([NaN, 2], 2));

    expect(Number.isNaN(sum.values[0])).toBe(true);
    expect(sum.values[1]).toBe(22);
  });
});

describe("finiteExtent", () => {
  it("ignores NaN", () => {
    expect(finiteExtent(raster([NaN, 3, -2, NaN], 2))).toEqual([-2, 3]);
  });

  it("returns null when there is nothing finite", () => {
    expect(finiteExtent(raster([NaN, NaN], 2))).toBeNull();
  });
});

describe("column/longitude mapping", () => {
  const bounds: [number, number, number, number] = [74.0, 31.0, 75.0, 32.0];

  it("round-trips a cut column through its divider longitude", () => {
    expect(longitudeToColumn(columnToLongitude(40, 100, bounds), 100, bounds)).toBe(40);
  });

  it("puts the divider on a cell edge, not a cell centre", () => {
    // Cut 0 is the western edge of the tile; cut `width` is the eastern edge. A
    // half-cell offset here would draw the line where the data is not actually split.
    expect(columnToLongitude(0, 100, bounds)).toBe(74.0);
    expect(columnToLongitude(100, 100, bounds)).toBe(75.0);
  });

  it("clamps longitudes outside the tile", () => {
    expect(longitudeToColumn(0, 100, bounds)).toBe(0);
    expect(longitudeToColumn(180, 100, bounds)).toBe(100);
  });
});
