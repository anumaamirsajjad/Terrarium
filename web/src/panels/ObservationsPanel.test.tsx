/**
 * The citizen-photo panel.
 *
 * What matters on screen: that a missing vision key reads as a configuration fact rather
 * than a failure, and that a report is never presented as a measurement.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { StoredObservation } from "../api/client";
import ObservationsPanel from "./ObservationsPanel";
import { stripDataUri } from "./photo";

const NO_READER =
  "no vision model configured. Set TERRARIUM_GEMINI_API_KEY (free, no card) to read photos.";

const REPORTS: StoredObservation[] = [
  {
    id: 1,
    observation: {
      category: "air_source",
      description: "waste burning at the kerb",
      severity: 5,
      confidence: 0.91,
    },
    lon: 74.35,
    lat: 31.52,
    row: 80,
    col: 96,
  },
];

function render(overrides: Partial<Parameters<typeof ObservationsPanel>[0]> = {}): string {
  return renderToStaticMarkup(
    <ObservationsPanel
      lon={74.3587}
      lat={31.5204}
      observations={[]}
      reader={NO_READER}
      onSubmitted={() => {}}
      {...overrides}
    />,
  );
}

describe("stripDataUri", () => {
  it("removes the FileReader prefix the API does not want", () => {
    expect(stripDataUri("data:image/jpeg;base64,QUJD")).toBe("QUJD");
  });

  it("leaves raw base64 alone", () => {
    expect(stripDataUri("QUJD")).toBe("QUJD");
  });
});

describe("ObservationsPanel", () => {
  it("says these are reports rather than measurements", () => {
    // They draw on the same grid as the modelled layers, which is exactly why the label
    // has to be on screen.
    const html = render();
    expect(html).toContain("Reports,");
    expect(html).toContain("never inside it");
  });

  it("shows where the photo will be placed", () => {
    expect(render()).toContain("31.5204");
  });

  it("explains a missing key as configuration, not breakage", () => {
    const html = render();
    expect(html).toContain("No vision model is configured");
    expect(html).toContain("no offline fallback");
    expect(html).toContain("disabled");
  });

  it("enables the input when a reader exists", () => {
    const html = render({ reader: "gemini:gemini-2.5-flash", observations: REPORTS });
    expect(html).not.toContain("No vision model is configured");
    expect(html).toContain("gemini:gemini-2.5-flash");
  });

  it("shows the category, severity and the model's own confidence", () => {
    const html = render({ reader: "gemini:gemini-2.5-flash", observations: REPORTS });
    expect(html).toContain("Air source");
    expect(html).toContain("severity 5/5");
    expect(html).toContain("0.91");
    expect(html).toContain("waste burning at the kerb");
  });

  it("says the reports are not persisted", () => {
    const html = render({ reader: "gemini:gemini-2.5-flash", observations: REPORTS });
    expect(html).toContain("gone when it restarts");
  });
});
