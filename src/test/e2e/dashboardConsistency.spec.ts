import { test, expect } from "@playwright/test";

/**
 * Dashboard consistency — RENDER layer (#3911 / dashboard-consistency).
 *
 * The API/data-level invariants live in
 * `ai_trading_bot/tests/consistency/test_dashboard_consistency.py` and run in CI
 * without the app. This spec asserts the SAME invariants on what the Overview
 * actually RENDERS — catching client-side transform / rounding / basis / anchor
 * bugs that the backend numbers alone do not reveal.
 *
 * Status: scaffold. The Overview must expose stable data-testids before this can
 * run (see TODO). Marked `fixme` so it neither fake-passes nor breaks CI until the
 * testids are wired; flip to `test(...)` in the same PR that adds them.
 */
test.describe("Overview consistency (render)", () => {
  // TODO(#3911): add data-testids on Overview.tsx:
  //   hero-change (abs + pct), equity-chart (expose first/last point values),
  //   since-inception-value, since-inception-label (the window it claims).
  test.fixme("hero change equals chart(end - start) in the active basis", async ({ page }) => {
    await page.goto("/"); // console baseURL :8082 (see playwright.config.ts)
    const hero = Number(await page.getByTestId("hero-change-abs").getAttribute("data-value"));
    const first = Number(await page.getByTestId("equity-chart").getAttribute("data-first"));
    const last = Number(await page.getByTestId("equity-chart").getAttribute("data-last"));
    expect(Math.abs(hero - (last - first))).toBeLessThanOrEqual(0.01);
  });

  test.fixme("since-inception label window matches the data anchor (#3911)", async ({ page }) => {
    await page.goto("/");
    // The label must name the SAME date the number is actually computed from
    // (start_date == timestamp(initial_capital)). Today it claims the account
    // inception while computing from the local install-date equity.
    const labelDate = await page.getByTestId("since-inception-label").getAttribute("data-anchor");
    const baseDate = await page.getByTestId("since-inception-value").getAttribute("data-base");
    expect(labelDate).toBe(baseDate);
  });
});
