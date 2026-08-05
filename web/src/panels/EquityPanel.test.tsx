/**
 * Does the panel render, and does it show the numbers that make it worth having?
 *
 * Server-rendered to a string rather than mounted in a DOM: `react-dom` is already a
 * dependency, so this needs no testing-library and no jsdom. It cannot test interaction,
 * which is fine — this panel has none. What it does catch is the panel crashing, and the
 * unflattering figures being quietly dropped from the markup.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Equity } from "../api/client";
import EquityPanel from "./EquityPanel";

/** The measured canal-side planting on cube_phase4.zarr, 2024-summer. */
const CANAL: Equity = {
  deciles: [0.003, 0.016, 0.077, 0.127, 0.235, 0.253, 0.17, 0.103, 0.016, 0.0].map(
    (share, index) => ({
      decile: index + 1,
      people: 626_000,
      mean_delta_c: -share / 10,
      share,
    }),
  ),
  stratified_by: "population density",
  population_covered: 6_259_308,
  top_three_share: 0.659,
  concentrated: true,
  shares_reliable: true,
  net_to_gross: 1,
  uninhabited_fraction: 0,
};

describe("EquityPanel", () => {
  it("renders nothing when the cube carried no population", () => {
    // Not a row of zeroes: "nobody counted" and "nobody benefits" are opposite findings.
    expect(renderToStaticMarkup(<EquityPanel equity={null} />)).toBe("");
  });

  it("shows the densest decile getting nothing", () => {
    const html = renderToStaticMarkup(<EquityPanel equity={CANAL} />);
    expect(html).toContain("0.0%");
    expect(html).toContain("66%");
    expect(html).toContain("Most crowded decile");
  });

  it("renders one row per decile", () => {
    const html = renderToStaticMarkup(<EquityPanel equity={CANAL} />);
    expect(html.match(/equity__row/g)).toHaveLength(10);
  });

  it("names its stratifier so nobody reads it as deprivation", () => {
    const html = renderToStaticMarkup(<EquityPanel equity={CANAL} />);
    expect(html).toContain("population density");
    expect(html).toContain("not by deprivation");
  });

  it("marks a warmed decile as warming rather than dropping it", () => {
    const warmed: Equity = {
      ...CANAL,
      deciles: CANAL.deciles.map((d, i) => (i === 0 ? { ...d, share: -0.05 } : d)),
    };
    expect(renderToStaticMarkup(<EquityPanel equity={warmed} />)).toContain(
      "equity__bar--warming",
    );
  });

  it("draws no bars at all when the shares are not reportable", () => {
    // The bars would be computed from a total near zero. Showing the verdict without the
    // chart is the honest rendering; showing ten confident bars is not.
    const unreliable: Equity = { ...CANAL, shares_reliable: false, net_to_gross: 0.005 };
    const html = renderToStaticMarkup(<EquityPanel equity={unreliable} />);
    expect(html).not.toContain("equity__row");
    expect(html).toContain("no net effect");
  });

  it("leads with wasted cooling when a plan mostly misses people", () => {
    const wasteful: Equity = { ...CANAL, uninhabited_fraction: 0.264 };
    expect(renderToStaticMarkup(<EquityPanel equity={wasteful} />)).toContain(
      "reaches nobody",
    );
  });
});
