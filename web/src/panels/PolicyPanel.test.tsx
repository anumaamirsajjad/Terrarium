/**
 * Does the panel render, and does the one piece of arithmetic it does — a fraction to a
 * rounded percentage — come out right, in both directions the DSL has (canopy, emissions)?
 *
 * Server-rendered to a string, same as EquityPanel's test: this panel has no interaction of
 * its own beyond a click handler, so a DOM and testing-library buy nothing a render-and-grep
 * does not already catch.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { MappedPlan, PolicyMeasureItem } from "../api/client";
import PolicyPanel from "./PolicyPanel";

function item(overrides: Partial<PolicyMeasureItem>): PolicyMeasureItem {
  return {
    measure: {
      title: "Establish a low emission zone",
      sector: "transport",
      target: "36 percent of vehicle emissions",
      target_year: 2030,
      source_page: 12,
      quote: "Establish a low emission zone removing 36 percent of vehicle emissions.",
      document: "punjab-clean-air-action-plan",
      document_sha256: "0".repeat(64),
    },
    mapped: null,
    ...overrides,
  };
}

const EXPRESSIBLE: MappedPlan = {
  measure: item({}).measure,
  plan: { name: "x", actions: [{ kind: "restrict_vehicles", emission_fraction_removed: 0.36 }] },
  basis: "'36 percent' read as removing 36% of this polygon's road PM2.5.",
  assumed: false,
};

describe("PolicyPanel", () => {
  it("explains the empty state rather than rendering nothing", () => {
    const markup = renderToStaticMarkup(
      <PolicyPanel measures={[]} loading={false} error={null} onApply={() => {}} hasPolygon />,
    );
    expect(markup).toContain("scripts/extract_policy.py");
  });

  it("rounds a fraction to a percentage and offers to apply an expressible measure", () => {
    const markup = renderToStaticMarkup(
      <PolicyPanel
        measures={[item({ mapped: EXPRESSIBLE })]}
        loading={false}
        error={null}
        onApply={() => {}}
        hasPolygon
      />,
    );
    expect(markup).toContain("emissions -36%");
    expect(markup).toContain("Apply this measure");
  });

  it("says so, rather than hiding, when neither lever expresses a measure", () => {
    const markup = renderToStaticMarkup(
      <PolicyPanel
        measures={[item({ mapped: null })]}
        loading={false}
        error={null}
        onApply={() => {}}
        hasPolygon
      />,
    );
    expect(markup).toContain("no lever for this");
    expect(markup).not.toContain("Apply this measure");
  });

  it("disables applying before a polygon is drawn, rather than doing nothing silently", () => {
    const markup = renderToStaticMarkup(
      <PolicyPanel
        measures={[item({ mapped: EXPRESSIBLE })]}
        loading={false}
        error={null}
        onApply={() => {}}
        hasPolygon={false}
      />,
    );
    expect(markup).toContain("disabled=\"\"");
  });
});
