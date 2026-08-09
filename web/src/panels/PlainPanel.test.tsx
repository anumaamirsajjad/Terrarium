/**
 * The panel most people will read, so the tests are about what it may not do.
 *
 * It renders server-written prose, which makes the interesting failures omissions rather
 * than mistakes: dropping the caveat, or quietly showing the raw modelled figure where the
 * hindcast-corrected one belongs. Both would leave a panel that looks perfectly fine.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Brief } from "../api/client";
import PlainPanel from "./PlainPanel";

const BRIEF: Brief = {
  headline: "Street trees over 16.71 km2 cools this tile's mid-morning land surface by 0.40 degC.",
  findings: ["Canopy actually added: 27% per planted cell."],
  uncertainties: ["Modelled, not measured."],
  confidence: "moderate",
  // -0.40 modelled, so -0.16 after the 2.5x hindcast correction. The two differ by enough
  // that a panel showing the wrong one is visible in a test rather than a rounding away.
  expected_cooling_c: -0.16140140295028688,
  plain: {
    verdict: "small",
    headline:
      "Planting across 16.7 km2 would make the ground about 0.16 degC cooler on a summer " +
      "morning - roughly 7% of the way from a bare street to a leafy one in this city.",
    points: [
      "That means planting about 180,905 trees.",
      "6.3 million people live across this tile.",
    ],
    caveat: "This is a model, not a measurement.",
    source: "template",
  },
};

describe("PlainPanel", () => {
  it("shows the hindcast-corrected figure, not the modelled one", () => {
    const html = renderToStaticMarkup(<PlainPanel brief={BRIEF} />);

    expect(html).toContain("0.16 °C");
    // The upper bound belongs beside its correction in the technical panels, never alone
    // and never at this size.
    expect(html).not.toContain("0.40 °C");
  });

  it("renders every plain point the server sent", () => {
    const html = renderToStaticMarkup(<PlainPanel brief={BRIEF} />);

    for (const point of BRIEF.plain.points) {
      expect(html).toContain(point);
    }
  });

  it("never drops the caveat", () => {
    const html = renderToStaticMarkup(<PlainPanel brief={BRIEF} />);

    expect(html).toContain(BRIEF.plain.caveat);
  });

  it("says when the prose came from a model rather than the template", () => {
    const offline = renderToStaticMarkup(<PlainPanel brief={BRIEF} />);
    expect(offline).toContain("written offline");

    const reworded = renderToStaticMarkup(
      <PlainPanel
        brief={{ ...BRIEF, plain: { ...BRIEF.plain, source: "langchain:llama-3.3-70b" } }}
      />,
    );
    expect(reworded).toContain("reworded by langchain:llama-3.3-70b");
  });

  it("hides the big number when the plan changes nothing measurable", () => {
    // 0.00 degC rendered large reads as a broken panel rather than a small result. The
    // sentence still explains what happened.
    const html = renderToStaticMarkup(
      <PlainPanel
        brief={{
          ...BRIEF,
          expected_cooling_c: -0.0001,
          plain: { ...BRIEF.plain, headline: "This plan changes nothing measurable." },
        }}
      />,
    );

    expect(html).not.toContain("0.00 °C");
    expect(html).toContain("This plan changes nothing measurable.");
  });

  it("shows how big the change is, and stays quiet when there was none", () => {
    // The verdict is the server's, not the narrator's — it is the one thing on this panel
    // a model cannot talk up, so it has to actually reach the screen.
    expect(renderToStaticMarkup(<PlainPanel brief={BRIEF} />)).toContain("small change");

    const nothing = renderToStaticMarkup(
      <PlainPanel brief={{ ...BRIEF, plain: { ...BRIEF.plain, verdict: "none" } }} />,
    );
    // The headline already says nothing happened; a badge repeating it is noise.
    expect(nothing).not.toContain("change</span>");
  });
});
