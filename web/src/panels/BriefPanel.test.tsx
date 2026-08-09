/**
 * The brief is written on the server, so this panel is only allowed to render it.
 *
 * That makes the test narrow and worth having: the panel must not drop the uncertainties,
 * must not hide them behind a toggle, and must not lose the caveat that a reader who takes
 * the headline alone is quoting the wrong number.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Brief } from "../api/client";
import BriefPanel from "./BriefPanel";

const BRIEF: Brief = {
  headline:
    "Street trees over 4.20 km2 cools this tile's mid-morning land surface by 0.51 degC " +
    "on average inside the polygon in 2024-summer — closer to 0.20 degC once the " +
    "hindcast correction is applied.",
  findings: [
    "Canopy actually added: 18% per planted cell, after each cell was capped.",
    "The ceiling on this window is 2.60 degC.",
  ],
  uncertainties: [
    "Modelled, not measured. The emulator over-predicted cooling by about 2.5x.",
    "This is 2024-summer (summer).",
    "Mid-morning *land surface* temperature — not what a thermometer in the shade reads.",
  ],
  plain: {
    verdict: "moderate",
    headline: "Planting across 4.2 km2 would cool the ground about 0.20 degC.",
    points: ["About 6.2 million people live here."],
    caveat: "This is a model, not a measurement.",
    source: "template",
  },
  confidence: "moderate",
  expected_cooling_c: -0.204,
};

describe("BriefPanel", () => {
  it("renders the headline and every finding", () => {
    const html = renderToStaticMarkup(<BriefPanel brief={BRIEF} />);
    expect(html).toContain("2024-summer");
    for (const finding of BRIEF.findings) expect(html).toContain(finding);
  });

  it("renders every uncertainty, in the open", () => {
    // Not behind a <details>: the hindcast correction and the surface-vs-air distinction
    // are what make the headline quotable, and a collapsed caveat is a dropped one.
    const html = renderToStaticMarkup(<BriefPanel brief={BRIEF} />);
    for (const line of BRIEF.uncertainties) expect(html).toContain(line);
    expect(html).not.toContain("<details");
  });

  it("shows the confidence, which is never high", () => {
    const html = renderToStaticMarkup(<BriefPanel brief={BRIEF} />);
    expect(html).toContain("moderate confidence");
    expect(html).not.toContain("high confidence");
  });
});
