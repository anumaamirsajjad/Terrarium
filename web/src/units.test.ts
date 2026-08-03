import { describe, expect, it } from "vitest";

import { formatUnits, labelWithUnits, withUnits } from "./units";

describe("units display", () => {
  it("hides the CF dimensionless marker", () => {
    // NDVI's units are literally "1" in the cube, which is correct metadata and reads
    // as a typo on screen: the legend showed "-0.45 1 … 0.78 1".
    expect(formatUnits("1")).toBe("");
    expect(labelWithUnits("ndvi", "1")).toBe("ndvi");
    expect(withUnits("0.78", "1")).toBe("0.78");
  });

  it("keeps real units", () => {
    expect(labelWithUnits("lst_c", "degC")).toBe("lst_c (degC)");
    expect(withUnits("-0.54", "degC")).toBe("-0.54 degC");
    expect(withUnits("120", "people")).toBe("120 people");
  });

  it("does not mistake a unit that merely contains a 1", () => {
    expect(formatUnits("m s-1")).toBe("m s-1");
    expect(labelWithUnits("wind_speed_ms", "m s-1")).toBe("wind_speed_ms (m s-1)");
  });
});
