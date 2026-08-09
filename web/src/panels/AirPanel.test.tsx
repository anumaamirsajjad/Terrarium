/**
 * The air panel, and specifically the caveats it is not allowed to drop.
 *
 * Phase 9 shipped a complete `AirResponse` that nothing in the frontend read, so this
 * panel is the whole of what makes the air core visible. The naming rules are the point of
 * the tests rather than a detail: an unqualified "PM2.5" is a claim about what a monitor
 * reads, which this number is not, and an unqualified magnitude is a calibrated figure,
 * which it also is not.
 *
 * Server-rendered to a string, like the other panel tests — no jsdom, no testing-library.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Air } from "../api/client";
import AirPanel from "./AirPanel";
import { compassPoint } from "./air";

/** A worked low-emission zone: 50 % of traffic removed, 2024-winter, on cube_phase9. */
const WINTER: Air = {
  variable: "pm25_delta_ug_m3",
  units: "µg/m³",
  stats: {
    n_cells_changed: 1264,
    mean_delta_inside: -0.4667,
    mean_delta_spillover: -0.2553,
    spillover_cells: 1610,
    min_delta: -1.6053,
    max_delta: 0,
  },
  mixing_height_m: 250,
  wind_speed_ms: 0.79,
  wind_direction_deg: 65.9,
  emission_fraction_removed: 0.5,
  delta: {
    variable: "pm25_delta_ug_m3",
    description: "",
    units: "µg/m³",
    window: "2024-winter",
    grid: {
      crs: "EPSG:32643",
      resolution_m: 100,
      shape: [202, 201],
      bounds: [663_000, 3_480_000, 683_100, 3_500_200],
      bounds_wgs84: [74.2533, 31.4305, 74.4641, 31.6103],
    },
    encoding: "base64:float32:little:row-major",
    // The panel never decodes the raster — that is the map's job, and the decode path is
    // covered in raster/decode.test.ts. An empty payload keeps this fixture readable.
    data: "",
    vmin: -1.6,
    vmax: 0,
    valid_fraction: 1,
  },
};

const SUMMER: Air = { ...WINTER, mixing_height_m: 800, wind_direction_deg: 300 };

describe("AirPanel", () => {
  it("leads with the change inside the polygon", () => {
    const html = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    expect(html).toContain("-0.47");
  });

  it("names the concentration as locally-generated, never as air quality", () => {
    const html = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    expect(html).toContain("Locally-generated");
  });

  it("carries its window, because winter is worth 6-9x", () => {
    const html = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    expect(html).toContain("2024-winter");
    expect(html).toContain("winter");
  });

  it("explains the inversion in winter and the deep mixing in summer", () => {
    const winter = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    const summer = renderToStaticMarkup(
      <AirPanel air={SUMMER} window="2024-summer" season="summer" />,
    );

    expect(winter).toContain("inversion");
    expect(winter).toContain("250 m");
    expect(summer).toContain("well mixed");
    expect(summer).toContain("800 m");
  });

  it("shows the downwind spillover, not just the inside", () => {
    // Unlike cooling, which stops at the edge of a 500 m feature neighbourhood, a plume is
    // still measurable a kilometre downwind. Dropping it would understate the plan.
    const html = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    expect(html).toContain("1,610");
    expect(html).toContain("Downwind");
  });

  it("says removed rather than electrified", () => {
    const html = renderToStaticMarkup(
      <AirPanel air={WINTER} window="2024-winter" season="winter" />,
    );
    expect(html).toContain("brake, tyre and road wear");
  });
});

describe("compassPoint", () => {
  it("reads the meteorological convention: the direction wind comes from", () => {
    expect(compassPoint(0)).toBe("N");
    expect(compassPoint(90)).toBe("E");
    expect(compassPoint(180)).toBe("S");
    expect(compassPoint(270)).toBe("W");
  });

  it("wraps rather than running off the end", () => {
    // 350 deg and 10 deg are both northerlies. An index that overflowed here would print
    // the plume travelling the opposite way, which nothing else would catch.
    expect(compassPoint(359)).toBe("N");
    expect(compassPoint(360)).toBe("N");
    // Negative input folds into the same circle: -30 is 330, which is NNW.
    expect(compassPoint(-30)).toBe("NNW");
  });
});
