import { describe, expect, it } from "vitest";

import type { Equity, DecileShare } from "../api/client";
import {
  EVEN_SHARE,
  barGeometry,
  densestShare,
  equityVerdict,
  evenMarkerPct,
} from "./equity";

function deciles(shares: number[]): DecileShare[] {
  return shares.map((share, index) => ({
    decile: index + 1,
    people: 626_000,
    mean_delta_c: -share,
    share,
  }));
}

function equity(shares: number[], overrides: Partial<Equity> = {}): Equity {
  const sorted = [...shares].sort((a, b) => b - a);
  const topThree = sorted.slice(0, 3).reduce((a, b) => a + b, 0);
  return {
    deciles: deciles(shares),
    stratified_by: "population density",
    population_covered: 6_259_308,
    top_three_share: topThree,
    concentrated: topThree > 0.5,
    shares_reliable: true,
    net_to_gross: 1,
    uninhabited_fraction: 0,
    ...overrides,
  };
}

const EVEN = Array<number>(10).fill(EVEN_SHARE);
// The measured canal-side planting: deciles 5-7 take two thirds, the densest gets nothing.
const CANAL = [0.003, 0.016, 0.077, 0.127, 0.235, 0.253, 0.17, 0.103, 0.016, 0.0];

describe("equityVerdict", () => {
  it("calls an even spread even", () => {
    const verdict = equityVerdict(equity(EVEN));
    expect(verdict.tone).toBe("even");
    expect(verdict.detail).toContain("30%");
  });

  it("calls the measured canal planting skewed and names the densest decile", () => {
    const verdict = equityVerdict(equity(CANAL));
    expect(verdict.tone).toBe("skewed");
    expect(verdict.headline).toContain("66%");
    // The damning number: the most heat-exposed decile receives nothing.
    expect(verdict.detail).toContain("0.0%");
  });

  it("reports wasted cooling ahead of concentration", () => {
    // Skewed *and* wasteful. Landing on empty ground is the more damaging failure and
    // the one a ΔLST headline hides completely, so it must win the headline.
    const verdict = equityVerdict(equity(CANAL, { uninhabited_fraction: 0.264 }));
    expect(verdict.tone).toBe("wasted");
    expect(verdict.headline).toContain("26%");
  });

  it("does not call a merely-even plan wasteful below the threshold", () => {
    expect(equityVerdict(equity(EVEN, { uninhabited_fraction: 0.08 })).tone).toBe("even");
  });

  it("refuses to rank an unreliable split, ahead of every other verdict", () => {
    // Shares divide by the net benefit. When that vanishes the shares explode, so this
    // has to outrank even the wasted-cooling headline - there is nothing to share out.
    const verdict = equityVerdict(
      equity(CANAL, { shares_reliable: false, net_to_gross: 0.005, uninhabited_fraction: 0.9 }),
    );
    expect(verdict.tone).toBe("no-net-effect");
    expect(verdict.headline).toContain("no net effect");
  });
});

describe("densestShare", () => {
  it("reads the last decile, which is the most densely populated", () => {
    expect(densestShare(equity(CANAL))).toBe(0);
    expect(densestShare(equity(EVEN))).toBeCloseTo(0.1);
  });

  it("survives an empty distribution rather than throwing", () => {
    expect(densestShare(equity([]))).toBe(0);
  });
});

describe("barGeometry", () => {
  it("normalises to the widest bar so an even split is still visible", () => {
    // Against a fixed 100% scale every bar would be a 10% sliver and the chart would
    // show nothing at all.
    const geometry = barGeometry(EVEN_SHARE, EVEN);
    expect(geometry.widthPct).toBeCloseTo(100);
    expect(geometry.warming).toBe(false);
  });

  it("gives the largest share the full width and scales the rest against it", () => {
    expect(barGeometry(0.253, CANAL).widthPct).toBeCloseTo(100);
    expect(barGeometry(0.127, CANAL).widthPct).toBeCloseTo(50.2, 0);
  });

  it("draws a warmed decile rather than clipping it to zero", () => {
    const warmed = [-0.2, 0.4, 0.4];
    const geometry = barGeometry(-0.2, warmed);
    expect(geometry.warming).toBe(true);
    expect(geometry.widthPct).toBeCloseTo(50);
  });

  it("does not divide by zero when nothing changed anywhere", () => {
    expect(barGeometry(0, [0, 0, 0]).widthPct).toBe(0);
  });
});

describe("evenMarkerPct", () => {
  it("sits at full width when the distribution is even", () => {
    expect(evenMarkerPct(EVEN)).toBeCloseTo(100);
  });

  it("sits partway along when one decile dominates", () => {
    expect(evenMarkerPct(CANAL)).toBeCloseTo(39.5, 0);
  });

  it("never runs past the end of the track", () => {
    expect(evenMarkerPct([0.01, 0.01])).toBeLessThanOrEqual(100);
  });
});
