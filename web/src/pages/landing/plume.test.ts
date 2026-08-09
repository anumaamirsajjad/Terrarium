/**
 * The landing page quotes 6.3x-8.9x, measured across 2023, 2024 and 2025 in `cores/air.py`.
 * The visualisation derives its own factor from the two seasons' lid heights and winds, and
 * prints it. If a constant is nudged for looks and the derived factor leaves the measured
 * band, the page starts contradicting the core — so this is the one thing worth asserting.
 */

import { describe, expect, it } from "vitest";

import { concentration, groundLevel, reflect, SUMMER, WINTER } from "./plume";

describe("plume", () => {
  it("puts the winter/summer factor inside the measured 6.3x-8.9x band", () => {
    const factor = groundLevel(WINTER) / groundLevel(SUMMER);
    expect(factor).toBeGreaterThan(6.3);
    expect(factor).toBeLessThan(8.9);
  });

  it("converges to the well-mixed slab once the plume fills the layer", () => {
    // Far downwind, sigma_z is well past the lid and the image series should have collapsed
    // to Q / (u * lid). Within 5 %, which is as close as seven images get.
    const mixed = concentration(60_000, 0, WINTER);
    const slab = 1 / (WINTER.windMs * WINTER.lidM);
    expect(Math.abs(mixed - slab) / slab).toBeLessThan(0.05);
  });

  it("keeps nothing above the lid", () => {
    expect(concentration(5_000, WINTER.lidM + 1, WINTER)).toBe(0);
    expect(reflect(WINTER.lidM * 1.4, WINTER.lidM)).toBeLessThanOrEqual(WINTER.lidM);
    expect(reflect(-30, WINTER.lidM)).toBe(30);
  });
});
