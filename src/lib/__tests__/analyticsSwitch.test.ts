// #4870 (#4581 S2): the privacy policy shows the Weg-D section 3 (c) on the same switch that turns
// reporting on at the `edge` function: the deploy passes one repository variable to the function
// as ANALYTICS_ENABLED and to the build as VITE_ANALYTICS_ENABLED (.github/workflows/deploy-demo.yml).
import { describe, expect, it } from "vitest";

import { measurementEnabled } from "../analyticsSwitch";

describe("measurementEnabled", () => {
  it("is true only for the exact value 'true'", () => {
    expect(measurementEnabled({})).toBe(false);
    expect(measurementEnabled({ VITE_ANALYTICS_ENABLED: "" })).toBe(false);
    expect(measurementEnabled({ VITE_ANALYTICS_ENABLED: "false" })).toBe(false);
    expect(measurementEnabled({ VITE_ANALYTICS_ENABLED: "1" })).toBe(false);
    expect(measurementEnabled({ VITE_ANALYTICS_ENABLED: "true" })).toBe(true);
  });

  it("ignores the old client variables of the removed browser script", () => {
    expect(
      measurementEnabled({ VITE_ANALYTICS_HOST: "https://analytics.example.test", VITE_ANALYTICS_WEBSITE_ID: "x" } as never),
    ).toBe(false);
  });
});
