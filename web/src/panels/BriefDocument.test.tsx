/**
 * The printed sheet.
 *
 * A printout outlives the session that made it, and nobody can click through to the API
 * that produced it. So the two things asserted here are that it carries its provenance —
 * window, tile, the fact that these are model outputs — and that the caveats reach the
 * page rather than being trimmed to make it fit.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PlanResponse, SimulateResponse, TileInfo } from "../api/client";
import BriefDocument from "./BriefDocument";

const TILE: TileInfo = {
  name: "Lahore",
  country: "PK",
  bbox: [74.2533, 31.4305, 74.4641, 31.6103],
  centroid: [74.3587, 31.5204],
  crs: "EPSG:32643",
  target_resolution_m: 100,
};

const RESULT = {
  variable: "lst_c",
  units: "degC",
  window: "2024-summer",
  season: "summer",
  stats: {
    n_cells_changed: 638,
    mean_delta_inside: -0.265,
    mean_delta_spillover: -0.061,
    spillover_cells: 251,
    min_delta: -0.94,
    max_delta: 0,
  },
  context: {
    tree_built_contrast_c: 2.6,
    mean_canopy_added: 0.15,
    linear_expectation_c: -0.39,
    ratio_to_linear: 0.7,
  },
  equity: {
    deciles: Array.from({ length: 10 }, (_, index) => ({
      decile: index + 1,
      people: 626_000,
      mean_delta_c: -0.02,
      share: 0.1,
    })),
    stratified_by: "population density",
    population_covered: 6_259_308,
    top_three_share: 0.3,
    concentrated: false,
    shares_reliable: true,
    net_to_gross: 1,
    uninhabited_fraction: 0.05,
  },
  delta: null as never,
  air: null,
  brief: {
    headline: "Street trees over 6.38 km2 cools the surface by 0.27 degC in 2024-summer.",
    findings: ["Canopy actually added: 15% per planted cell."],
    uncertainties: [
      "Modelled, not measured. The emulator over-predicted cooling by about 2.5x.",
      "Mid-morning land surface temperature, not air temperature.",
    ],
    confidence: "moderate",
    expected_cooling_c: -0.106,
    // The plain-language block the printed brief now carries as its own section, so a
    // resident gets the half they can read. `source` is what decides `dir="rtl"`.
    plain: {
      verdict: "small",
      headline: "Street trees here would make the ground about 0.11 degC cooler.",
      points: ["About 626,000 people live across this tile."],
      caveat: "This is a model, not a measurement.",
      source: "template",
    },
  },
} as unknown as SimulateResponse;

const PLAN = {
  plan: { name: "Street trees", actions: [] },
  tree_count: 38_280,
  max_trees: 137_305,
  cost: { planting_usd: 574_200, restriction_usd: 0, total_usd: 574_200, basis: "", calibrated: false },
} as unknown as PlanResponse;

function render(plan: PlanResponse | null = PLAN): string {
  return renderToStaticMarkup(
    <BriefDocument
      result={RESULT}
      plan={plan}
      tile={TILE}
      producedAt={new Date("2026-08-06T10:30:00Z")}
    />,
  );
}

describe("BriefDocument", () => {
  it("names the plan, the tile and the window in the header", () => {
    const html = render();
    expect(html).toContain("Street trees");
    expect(html).toContain("Lahore");
    expect(html).toContain("2024-summer");
  });

  it("shows the corrected figure next to the raw one", () => {
    // Either alone is a different claim: -0.27 is the model, -0.11 is the expectation.
    const html = render();
    expect(html).toContain("-0.27");
    expect(html).toContain("-0.11");
  });

  it("prints the ceiling", () => {
    expect(render()).toContain("2.60");
  });

  it("prints every uncertainty and the confidence", () => {
    const html = render();
    for (const line of RESULT.brief.uncertainties) expect(html).toContain(line);
    expect(html).toContain("moderate");
    expect(html).toContain("never reports high");
  });

  it("dates the sheet and says the figures are modelled", () => {
    const html = render();
    expect(html).toContain("2026-08-06");
    expect(html).toContain("not a measurement of what happened");
  });

  it("says costs are not calibrated", () => {
    expect(render()).toContain("not calibrated");
  });

  it("renders without a plan — /simulate can be driven by the sliders alone", () => {
    const html = render(null);
    expect(html).toContain("Modelled intervention");
    expect(html).not.toContain("Indicative cost");
  });
});
