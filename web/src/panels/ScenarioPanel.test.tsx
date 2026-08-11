/**
 * The DSL panel, rendered to a string.
 *
 * What is worth asserting here is what the panel refuses to hide: the validator's notes
 * and the refusal message with its arithmetic. Everything else is layout.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PlanResponse, Preset } from "../api/client";
import ScenarioPanel from "./ScenarioPanel";

const PRESETS: Preset[] = [
  {
    slug: "street-trees",
    label: "Street trees",
    summary: "Add 15 % canopy.",
    caveat: "Most of a built-up cell is roof and carriageway.",
    plan: { name: "Street trees", actions: [{ kind: "plant_trees", canopy_fraction_added: 0.15 }] },
  },
  {
    slug: "low-emission-zone",
    label: "Low-emission zone",
    summary: "Remove all vehicle PM2.5.",
    caveat: "Traffic gone, not electrified.",
    plan: {
      name: "Low-emission zone",
      actions: [{ kind: "restrict_vehicles", emission_fraction_removed: 1 }],
    },
  },
];

const PLAN: PlanResponse = {
  plan: PRESETS[0].plan,
  source: "preset",
  window: "2024-summer",
  season: "summer",
  cells: 1254,
  area_km2: 12.54,
  canopy_fraction_added: 0.15,
  emission_fraction_removed: 0,
  tree_count: 75_240,
  max_trees: 210_000,
  canopy_utilisation: 0.36,
  notes: ["Planting moves air quality by ~0.0003 µg/m3 at this scale."],
  warnings: [],
  basis: "One tree = 25 m2 of crown at maturity.",
  simulate_request: {
    geometry: { type: "Polygon", coordinates: [] },
    canopy_fraction_added: 0.15,
    emission_fraction_removed: 0,
    window: "2024-summer",
  },
};

const noop = () => {};

function render(overrides: Partial<Parameters<typeof ScenarioPanel>[0]> = {}) {
  return renderToStaticMarkup(
    <ScenarioPanel
      presets={PRESETS}
      hasPolygon
      plan={null}
      error={null}
      busy={false}
      onPreset={noop}
      onText={noop}
      onClear={noop}
      {...overrides}
    />,
  );
}

describe("ScenarioPanel", () => {
  it("offers every preset with its summary", () => {
    const html = render();
    expect(html).toContain("Street trees");
    expect(html).toContain("Low-emission zone");
    expect(html).toContain("Remove all vehicle PM2.5.");
  });

  it("disables the buttons until a polygon exists", () => {
    // Whether 5,000 trees fit is a question about a place.
    expect(render({ hasPolygon: false })).toContain("disabled");
  });

  it("shows the refusal verbatim, arithmetic and all", () => {
    const detail =
      "5,000 trees need 0.125 km2 of crown at 25 m2 each, but this 0.300 km2 polygon " +
      "has only 0.031 km2 still plantable";
    expect(render({ error: detail })).toContain(detail);
  });

  it("shows what fits alongside what was asked for", () => {
    const html = render({ plan: PLAN });
    expect(html).toContain("75,240");
    expect(html).toContain("210,000");
    expect(html).toContain("12.54 km²");
  });

  it("keeps the validator's notes", () => {
    expect(render({ plan: PLAN })).toContain("0.0003");
  });
});
