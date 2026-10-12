// #4471: /legal/privacy discloses the cookieless reach measurement that the landing actually runs
// (tracking client #4467, experiments #4469, decisions in ADR-026).
import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Legal from "../pages/Legal";

const privacyText = () =>
  render(
    <MemoryRouter>
      <Legal kind="privacy" />
    </MemoryRouter>,
  ).container.textContent ?? "";

describe("Privacy policy — cookieless reach measurement (#4471)", () => {
  it("names the tools and that they are self-hosted (ADR-026 §1)", () => {
    const text = privacyText();
    expect(text).toMatch(/Cookieless Reach Measurement/);
    expect(text).toMatch(/Umami/);
    expect(text).toMatch(/GrowthBook/);
    expect(text).toMatch(/self-hosted/i);
  });

  it("names the hosting location in the EU (ADR-026 §2)", () => {
    const text = privacyText();
    expect(text).toMatch(/europe-west3/);
    expect(text).toMatch(/Frankfurt/);
  });

  it("states that nothing is stored on or read from the device (ADR-026 §3)", () => {
    const text = privacyText();
    expect(text).toMatch(/no cookies/i);
    expect(text).toMatch(/localStorage/);
    expect(text).toMatch(/sessionStorage/);
  });

  it("lists the processed data as sent by the tracking client (src/lib/analytics.ts)", () => {
    const text = privacyText();
    for (const field of [/page path/i, /referrer/i, /page title/i, /browser language/i, /hostname/i]) {
      expect(text).toMatch(field);
    }
    expect(text).toMatch(/IP address is not stored/i);
  });

  it("names the retention period and the legal basis (ADR-026 §4, §5)", () => {
    const text = privacyText();
    expect(text).toMatch(/13 months/);
    expect(text).toMatch(/Art\. 6 \(1\) lit\. f GDPR/);
    expect(text).toMatch(/§ 25 TDDDG/);
  });

  it("no longer claims that no analytics is used, and does not name Google Analytics", () => {
    const text = privacyText();
    expect(text).not.toMatch(/commercial analytics frameworks/i);
    expect(text).not.toMatch(/Google Analytics/i);
  });
});
