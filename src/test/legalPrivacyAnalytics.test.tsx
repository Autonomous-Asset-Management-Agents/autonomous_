// #4471: /legal/privacy discloses the cookieless reach measurement that the landing actually runs.
// #4581: section 3 (c) follows the build — it names the measurement only when it is switched on,
// and says that nothing is measured otherwise.
// #4870 (#4581 S2, ADR-026 "Bestätigung der Auflage (27.10.2026)"): the measurement is Weg D — the
// `edge` function counts on the server from the request the browser sends anyway. The switch is
// VITE_ANALYTICS_ENABLED, set from the same repository variable as the function's ANALYTICS_ENABLED.
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Legal from "../pages/Legal";

const privacyText = () =>
  render(
    <MemoryRouter>
      <Legal kind="privacy" />
    </MemoryRouter>,
  ).container.textContent ?? "";

const measuringBuild = () => {
  vi.stubEnv("VITE_ANALYTICS_ENABLED", "true");
};

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Privacy policy — build without measurement (#4581)", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_ANALYTICS_ENABLED", "");
  });

  it("says that no reach measurement and no A/B test runs", () => {
    const text = privacyText();
    expect(text).toMatch(/currently runs no reach measurement and no A\/B tests/);
    expect(text).toMatch(/Our website currently runs no reach\s+measurement \(see \(c\)\)/);
  });

  it("does not claim a measurement it does not run", () => {
    const text = privacyText();
    for (const claim of [/Umami/, /GrowthBook/, /are counted/i, /90 days/, /described under \(c\)/, /reach measurement\s+data/]) {
      expect(text).not.toMatch(claim);
    }
  });

  it("leaves no template placeholder in the text", () => {
    expect(privacyText()).not.toMatch(/\{\{/);
  });

  it("the old client variables no longer switch anything on", () => {
    vi.stubEnv("VITE_ANALYTICS_HOST", "https://analytics.example.test");
    vi.stubEnv("VITE_ANALYTICS_WEBSITE_ID", "site-id");
    expect(privacyText()).not.toMatch(/Umami/);
  });
});

describe("Privacy policy — server-side reach measurement, Weg D (#4870)", () => {
  beforeEach(measuringBuild);

  // #4907 (owner 28.10.2026, ADR-026): the counts are log entries in Cloud Logging; Umami and
  // GrowthBook are not built.
  it("names Cloud Logging as the store and no Umami or GrowthBook", () => {
    const text = privacyText();
    expect(text).toMatch(/Cookieless Reach Measurement/);
    expect(text).toMatch(/Google Cloud Logging/);
    expect(text).toMatch(/no third-party analytics provider/i);
    expect(text).not.toMatch(/Umami/);
    expect(text).not.toMatch(/GrowthBook/);
  });

  it("says the count happens on our server and no measurement code runs in the browser", () => {
    const text = privacyText();
    expect(text).toMatch(/on our server/i);
    expect(text).toMatch(/no measurement code/i);
    expect(text).toMatch(/only the first load/i);
  });

  it("extends the data subject rights to the measurement data", () => {
    expect(privacyText()).toMatch(/server data and the reach measurement\s+data described in Section 3 \(c\)/);
  });

  it("names the hosting: Firebase Hosting, Cloud Storage and europe-west3", () => {
    const text = privacyText();
    expect(text).toMatch(/Firebase Hosting/);
    expect(text).toMatch(/Cloud Storage/);
    expect(text).toMatch(/europe-west3/);
    expect(text).toMatch(/Frankfurt/);
  });

  it("states that nothing is stored on or read from the device", () => {
    const text = privacyText();
    expect(text).toMatch(/no cookies/i);
    expect(text).toMatch(/localStorage/);
    expect(text).toMatch(/sessionStorage/);
  });

  it("#4907: lists exactly what a count entry stores — no identifier, no location", () => {
    const text = privacyText();
    for (const field of [/page path/i, /utm_/, /operating system/i, /which test and which variant/i]) {
      expect(text).toMatch(field);
    }
    expect(text).toMatch(/no IP address, no user agent, no referrer and no identifier/);
    expect(text).not.toMatch(/country/i);
    expect(text).not.toMatch(/changes every day/i);
    expect(text).not.toMatch(/page title/i);
    expect(text).not.toMatch(/browser language/i);
  });

  it("#4907: IP address and user agent are read only while the request is processed", () => {
    const text = privacyText();
    expect(text).toMatch(/IP address and your browser's user agent while\s+it processes your request/);
    expect(text).toMatch(/automated requests \(bots\)/);
  });

  it("#4870: no IP address is stored, also not in the server logs of the counting step", () => {
    // Backed by infra/terraform/edge_logging.tf (exclusion of the edge request logs, owner 28.10.).
    expect(privacyText()).toMatch(/server logs of the counting step do not store your IP address/);
  });

  it("honours Global Privacy Control and Do Not Track", () => {
    const text = privacyText();
    expect(text).toMatch(/Global Privacy Control/);
    expect(text).toMatch(/Do Not Track/);
  });

  it("names the retention period and the legal basis", () => {
    const text = privacyText();
    expect(text).toMatch(/90 days/);
    expect(text).not.toMatch(/13 months/);
    expect(text).toMatch(/Art\. 6 \(1\) lit\. f GDPR/);
    expect(text).toMatch(/§ 25 TDDDG/);
    expect(text).toMatch(/Art\. 21 GDPR/);
  });

  it("no longer describes the removed browser-side variant choice", () => {
    expect(privacyText()).not.toMatch(/random identifier that exists only in memory/i);
  });

  it("no longer claims that no analytics is used, and does not name Google Analytics", () => {
    const text = privacyText();
    expect(text).not.toMatch(/commercial analytics frameworks/i);
    expect(text).not.toMatch(/Google Analytics/i);
    expect(text).not.toMatch(/\[to be confirmed\]/i);
    expect(text).not.toMatch(/Draft pending counsel review/i);
  });

  it("renders Terms of Service with Execution-Only governance, EULA scope, and Rome I consumer clause", () => {
    const text =
      render(
        <MemoryRouter>
          <Legal kind="terms" />
        </MemoryRouter>,
      ).container.textContent ?? "";
    expect(text).toMatch(/User-Defined Automation/);
    expect(text).toMatch(/User Eligibility, License Scope, and Third-Party Terms/);
    expect(text).toMatch(/Art\. 6 \(2\) Rome I Regulation/);
    expect(text).toMatch(/Last updated 2026-10-28/);
    expect(text).not.toMatch(/Draft pending counsel review/i);
  });

  it("renders Imprint without Pending Allocation placeholder", () => {
    const text =
      render(
        <MemoryRouter>
          <Legal kind="imprint" />
        </MemoryRouter>,
      ).container.textContent ?? "";
    expect(text).not.toMatch(/\[Pending Allocation\]/i);
  });
});
